"""Unified timeline: merge every pod's `/logs` and `/comms` into one sorted
stream of entries, each tagged with extracted entities.

The timeline is the substrate the LLM reporter sits on top of (see
`deliberation.md` §4, §8). Entries have stable sequential ids so the report
can cite `timeline[47]` unambiguously.
"""
from __future__ import annotations

import re
from datetime import datetime
from typing import Any

from .entities import extract


def _parse_timestamp_ms(ts: str) -> int:
    """Parse an ISO-8601 timestamp to epoch ms. Returns 0 on malformed input.

    The pod configs all look like `'2094-03-15T10:30:00Z'`. Accept the `Z`
    suffix and fall back to 0 if we can't parse — the caller can detect
    sentinel 0 values by comparing to `timestamp_iso`.
    """
    if not ts:
        return 0
    try:
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        return int(dt.timestamp() * 1000)
    except (ValueError, TypeError):
        return 0


def _log_entries(pod_id: str, pod: dict[str, Any], pod_re: re.Pattern[str], resource_re: re.Pattern[str]) -> list[dict[str, Any]]:
    logs = (pod["endpoints"].get("/logs") or {}).get("logs", [])
    out: list[dict[str, Any]] = []
    for idx, entry in enumerate(logs):
        event = entry.get("event", "") or ""
        detail = entry.get("detail", "") or ""
        text = f"{event}: {detail}".strip(": ").strip()
        ts_iso = entry.get("timestamp", "") or ""
        out.append(
            {
                "source_pod": pod_id,
                "kind": "log",
                "timestamp_iso": ts_iso,
                "timestamp": _parse_timestamp_ms(ts_iso),
                "text": text,
                "raw_pointer": {"endpoint": "/logs", "index": idx},
                "entities": extract(text, pod_re=pod_re, resource_re=resource_re),
            }
        )
    return out


def _comm_entries(
    pod_id: str,
    pod: dict[str, Any],
    pod_re: re.Pattern[str],
    resource_re: re.Pattern[str],
    known_pods: set[str],
) -> list[dict[str, Any]]:
    body = pod["endpoints"].get("/comms")
    if not isinstance(body, dict):
        return []
    out: list[dict[str, Any]] = []
    for idx, msg in enumerate(body.get("messages", [])):
        content = msg.get("content", "") or ""
        ts_iso = msg.get("timestamp", "") or ""
        sender = msg.get("from")
        recipient = msg.get("to")
        # Entity extraction over content text; union in from/to only if they
        # name a known pod (guards against team names like "zephyr_ops").
        entities = extract(content, pod_re=pod_re, resource_re=resource_re)
        pod_refs = set(entities["pod_refs"])
        for p in (sender, recipient):
            if isinstance(p, str) and p.lower() in known_pods:
                pod_refs.add(p.lower())
        entities["pod_refs"] = sorted(pod_refs)
        out.append(
            {
                "source_pod": pod_id,
                "kind": "comm",
                "timestamp_iso": ts_iso,
                "timestamp": _parse_timestamp_ms(ts_iso),
                "text": content,
                "from": sender,
                "to": recipient,
                "raw_pointer": {"endpoint": "/comms", "index": idx},
                "entities": entities,
            }
        )
    return out


def build(
    pods: dict[str, dict[str, Any]],
    pod_re: re.Pattern[str],
    resource_re: re.Pattern[str],
) -> list[dict[str, Any]]:
    """Return a chronological, stably-ordered timeline with sequential `id`s."""
    entries: list[dict[str, Any]] = []
    known_pods = {p.lower() for p in pods}
    for pod_id in sorted(pods):  # deterministic pre-sort seed
        pod = pods[pod_id]
        entries.extend(_log_entries(pod_id, pod, pod_re, resource_re))
        entries.extend(_comm_entries(pod_id, pod, pod_re, resource_re, known_pods))

    # Stable sort — ties broken by pod then kind then original position
    entries.sort(key=lambda e: (e["timestamp"], e["source_pod"], e["kind"]))
    for i, entry in enumerate(entries):
        entry["id"] = i
    return entries
