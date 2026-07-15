"""Flag-gated LLM extraction pass over pod logs+comms.

Runs only when mapping is invoked with --llm-tag. For each pod with non-empty
logs+comms, makes a single Claude call (sonnet 4.6) to extract semantic tags:

- references_reroute: pod mentions a resource being rerouted
- flags_failure: pod mentions a recurring problem or risk that contradicts /status
- contradicts_status: logs/comms describe stress that /status (always "nominal") hides
- mentions_pods: pod IDs referenced in freetext
- summary: 1-2 sentence narrative of what this pod's freetext reveals

Failures (network, rate limit, parse errors) don't fail the run — they're logged
and the pod is skipped (no llm_tags field in the output for that pod).

Sonnet 4.6 is the right choice for this task: structured extraction across many
small calls. The heavier analytical synthesis runs in reporting with opus 4.7.
"""

from __future__ import annotations

import asyncio
import logging
import os
from typing import Any

from anthropic import AsyncAnthropic
from pydantic import BaseModel, Field

MODEL = "claude-sonnet-4-6"
MAX_TOKENS = 1024
MAX_CONCURRENT = 4  # gentle on rate limits; pods take ~2s each in parallel

log = logging.getLogger(__name__)


SYSTEM_PROMPT = """You are an infrastructure analyst extracting structured tags from one habitat pod's operational data on a lunar colony.

You receive, per pod: identity, role, log entries (timestamp/event/detail), and inter-pod comms (timestamp/from/to/content). Comms may be empty.

The colony's /status endpoints all report "nominal" — your job is to detect when logs or comms tell a different story.

Extract:
- references_reroute (bool): logs/comms mention a resource path change, consolidation, or supply reroute
- flags_failure (bool): logs/comms mention a recurring problem, broken system, or risk that contradicts "nominal"
- contradicts_status (bool): logs/comms describe an unresolved incident or capacity stress that /status (always "nominal") would not reveal
- mentions_pods (list of strings): pod IDs explicitly referenced (lowercase, e.g. "helios", "aquifer"). Only from the actual pod ID list, not arbitrary names.
- summary (1-2 sentences): what this pod's freetext reveals about its real operational state beyond surface status

Be precise. Never invent details. If a field has no supporting evidence, set the bool false / list empty / summary minimal."""


class PodTags(BaseModel):
    references_reroute: bool
    flags_failure: bool
    contradicts_status: bool
    mentions_pods: list[str] = Field(default_factory=list)
    summary: str


def _build_user_message(pod_id: str, raw: dict) -> str | None:
    """Render the pod's data as a single string. Returns None if pod has no freetext."""
    info = raw.get("info") or {}
    logs_obj = raw.get("logs") or {}
    comms_obj = raw.get("comms")
    logs = logs_obj.get("logs", []) if isinstance(logs_obj, dict) else []
    comms = (
        comms_obj.get("messages", [])
        if isinstance(comms_obj, dict)
        else []
    )

    if not logs and not comms:
        return None

    lines = [
        f"Pod ID: {pod_id}",
        f"Name: {info.get('name', '(unknown)')}",
        f"Role: {info.get('role', '(unknown)')}",
        "",
        "Logs:",
    ]
    if logs:
        for entry in logs:
            ts = entry.get("timestamp", "")
            ev = entry.get("event", "")
            detail = entry.get("detail", "")
            lines.append(f"  [{ts}] {ev}: {detail}")
    else:
        lines.append("  (none)")

    lines.extend(["", "Comms:"])
    if comms:
        for entry in comms:
            ts = entry.get("timestamp", "")
            frm = entry.get("from", "")
            to = entry.get("to", "")
            content = entry.get("content", "")
            lines.append(f"  [{ts}] {frm} -> {to}: {content}")
    else:
        lines.append("  (none)")

    return "\n".join(lines)


async def _tag_pod(
    client: AsyncAnthropic, sem: asyncio.Semaphore, pod_id: str, raw: dict
) -> tuple[str, dict | None]:
    msg = _build_user_message(pod_id, raw)
    if msg is None:
        log.info("Skipping %s — no logs or comms", pod_id)
        return pod_id, None

    async with sem:
        try:
            response = await client.messages.parse(
                model=MODEL,
                max_tokens=MAX_TOKENS,
                system=SYSTEM_PROMPT,
                messages=[{"role": "user", "content": msg}],
                output_format=PodTags,
            )
            tags = response.parsed_output
            if tags is None:
                log.warning("LLM tag for %s returned no parsed output", pod_id)
                return pod_id, None
            return pod_id, tags.model_dump()
        except Exception as e:
            log.warning("LLM tag for %s failed: %s", pod_id, e)
            return pod_id, None


async def tag_all(pods_raw: dict[str, dict]) -> dict[str, dict]:
    """Run LLM extraction on all pods in parallel. Returns {pod_id → tag dict}."""
    api_key = os.environ.get("LLM_API_KEY")
    if not api_key:
        log.error("LLM_API_KEY not set; skipping llm-tag pass")
        return {}

    client = AsyncAnthropic(api_key=api_key)
    sem = asyncio.Semaphore(MAX_CONCURRENT)
    tasks = [_tag_pod(client, sem, pid, raw) for pid, raw in pods_raw.items()]
    results = await asyncio.gather(*tasks)
    await client.close()
    return {pid: tags for pid, tags in results if tags is not None}
