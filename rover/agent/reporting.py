"""Reporting pipeline entrypoint.

Reads /rover/output/map.json, builds a structured prompt with all the raw and
derived data, and asks Claude to synthesize a Markdown infrastructure assessment.

Single LLM call with adaptive thinking. Streamed because the response can be
large enough to risk a non-streaming HTTP timeout.

Model: claude-opus-4-7. The report is the deliverable and benefits most from
the strongest model. The lighter per-pod LLM-tag extraction (if enabled) runs
on sonnet 4.6 — see agent/llm_tag.py.
"""

from __future__ import annotations

import json
import logging
import os
import sys
from pathlib import Path

from anthropic import Anthropic

MODEL = "claude-opus-4-7"
MAX_TOKENS = 32000
INPUT_PATH = Path("/rover/output/map.json")
OUTPUT_PATH = Path("/rover/output/report.md")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(name)s %(levelname)s %(message)s",
)
log = logging.getLogger("reporting")


SYSTEM_PROMPT = """You are an independent infrastructure assessor commissioned by the Selene Lunar Colony Administration to review the colony's habitat pod infrastructure.

You receive a structured mapping artifact (compiled from the colony's REST APIs) that contains:
- Raw responses from each pod's /info, /status, /dependencies, /supplies, /logs, /comms endpoints
- A derived dependency graph with mismatches, SPOF candidates, articulation points, orphans, and dangling references
- A timeline of log entries matching structural-change patterns (decommissioning, reroutes, directive references)
- Optional LLM-extracted semantic tags per pod

Every pod's /status endpoint returns "nominal" — yet the logs, comms, and graph mismatches frequently reveal a different reality. Your job is to surface that reality.

REQUIRED REPORT STRUCTURE (Markdown):

# Project Selene — Infrastructure Assessment

## Executive Summary
Exactly 5 bullets, each calling out a specific risk. Name pods, name resources, cite evidence.

## Colony Topology
Describe the dependency graph in prose. Identify the most-depended-upon pods, the structural backbone, and any orphan pods or dangling references. Include an ASCII or mermaid diagram if it adds clarity.

## Critical Findings
Numbered list. For each finding:
- A clear title
- Specific evidence (quote log entries, comms, dependency mismatches verbatim)
- The risk implication

Cover at minimum:
- Single points of failure (high in-degree pods without backups)
- Declared-vs-actual dependency drift (mismatches)
- Decommissioned redundancy (directives and consequent log entries)
- Capacity utilization (pods at or near rated capacity)
- Recurring failures invisible to /status (comms flagging unresolved issues)

## Storyline
A chronological narrative reconstructing how the infrastructure evolved. Use the directive_index entries and surrounding log lines as anchors. Surface the strategic trades — efficiency over redundancy, consolidation, decommissioning — and the consequences they have set up.

## Recommendations
A prioritized list of concrete actions for colony leadership.

DISCIPLINE:
- Every claim must trace back to data in the mapping artifact. Do not invent details.
- Quote log entries, comms, and directive numbers verbatim where they support a finding.
- Where the data is silent, say so explicitly rather than speculate.
- Use exact pod names (proper case) in prose, but pod_ids when referring to graph fields."""


def _summarize_map(map_data: dict) -> str:
    """Render the map.json into a single text block for the LLM."""
    meta = map_data.get("metadata", {})
    pods = map_data.get("pods", {})
    graph = map_data.get("graph", {})
    log_patterns = map_data.get("log_patterns", [])

    parts: list[str] = []

    parts.append("# Mapping artifact metadata")
    parts.append(f"Generated at: {meta.get('discovered_at')}")
    parts.append(f"Agent version: {meta.get('agent_version')}")
    parts.append(f"Pod count: {meta.get('pod_count')}")
    parts.append(f"LLM-tagged: {meta.get('llm_tagged')}")
    parts.append(f"Known pod IDs (authoritative list of 12): {meta.get('known_pod_ids')}")
    parts.append(f"Reached via gateway-walk: {meta.get('gateway_walk_set')}")
    parts.append(f"Reached via network-sweep: {meta.get('network_sweep_set')}")
    parts.append("")

    parts.append("# Per-pod data")
    for pid in sorted(pods.keys()):
        pod = pods[pid]
        raw = pod.get("raw", {})
        info = raw.get("info") or {}
        status = raw.get("status") or {}
        deps_obj = raw.get("dependencies") or {}
        sups_obj = raw.get("supplies") or {}
        logs_obj = raw.get("logs") or {}
        comms_obj = raw.get("comms")
        derived = pod.get("derived", {})
        llm_tags = pod.get("llm_tags")

        parts.append(f"## {pid} ({info.get('name', '(unknown)')})")
        parts.append(f"Role: {info.get('role', '(unknown)')}")
        parts.append(f"Population: {info.get('population')}")
        parts.append(f"Uptime: {info.get('uptime_days')} days")
        parts.append(f"Discovered via: {pod.get('discovered_via')}")
        parts.append(f"Metadata: {json.dumps(info.get('metadata', {}), indent=2)}")
        parts.append("")
        parts.append(
            f"Derived: in_degree={derived.get('in_degree')}, "
            f"out_degree={derived.get('out_degree')}, "
            f"betweenness={derived.get('betweenness')}, "
            f"articulation_point={derived.get('is_articulation_point')}, "
            f"capacity_utilization_pct={derived.get('capacity_utilization_pct')}, "
            f"flags={derived.get('flags')}"
        )
        parts.append("")
        parts.append(f"Status: {json.dumps(status)}")
        parts.append("")

        deps = deps_obj.get("dependencies", []) if isinstance(deps_obj, dict) else []
        parts.append("Dependencies:")
        if deps:
            for d in deps:
                parts.append(
                    f"  - on {d.get('pod_id')} for {d.get('resource')} "
                    f"(criticality={d.get('criticality')}): {d.get('notes')}"
                )
        else:
            parts.append("  (none declared)")
        parts.append("")

        sups = sups_obj.get("supplies", []) if isinstance(sups_obj, dict) else []
        parts.append("Supplies:")
        if sups:
            for s in sups:
                parts.append(f"  - {s.get('resource')} -> {s.get('pod_id')}")
        else:
            parts.append("  (none declared)")
        parts.append("")

        logs = logs_obj.get("logs", []) if isinstance(logs_obj, dict) else []
        parts.append(f"Logs ({len(logs)} entries):")
        for entry in logs:
            parts.append(
                f"  [{entry.get('timestamp')}] {entry.get('event')}: {entry.get('detail')}"
            )
        parts.append("")

        if comms_obj is None:
            parts.append("Comms: (no comms channel for this pod)")
        elif isinstance(comms_obj, dict):
            msgs = comms_obj.get("messages", [])
            parts.append(f"Comms ({len(msgs)} messages):")
            for entry in msgs:
                parts.append(
                    f"  [{entry.get('timestamp')}] {entry.get('from')} -> "
                    f"{entry.get('to')}: {entry.get('content')}"
                )
        parts.append("")

        if llm_tags:
            parts.append(f"LLM tags: {json.dumps(llm_tags, indent=2)}")
            parts.append("")

        parts.append("---")
        parts.append("")

    parts.append("# Graph analysis")
    parts.append(f"Declared dependency edges: {len(graph.get('edges_declared_depends', []))}")
    parts.append(f"Declared supply edges: {len(graph.get('edges_declared_supplies', []))}")
    parts.append("")

    mismatches = graph.get("mismatches", [])
    parts.append(f"## Mismatches ({len(mismatches)})")
    for m in mismatches:
        parts.append(
            f"- [{m.get('type')}] {m.get('from')} <-> {m.get('to')} re: {m.get('resource')}"
        )
        parts.append(f"    {m.get('evidence')}")
    parts.append("")

    spofs = graph.get("spof_candidates", [])
    parts.append(f"## SPOF candidates ({len(spofs)})")
    for s in spofs:
        parts.append(
            f"- {s.get('pod')}: in_degree={s.get('in_degree')}, "
            f"dependents={s.get('dependents')}, "
            f"backup_systems={s.get('backup_systems')}, "
            f"articulation_point={s.get('is_articulation_point')}"
        )
    parts.append("")

    parts.append(f"## Articulation points: {graph.get('articulation_points', [])}")
    parts.append(f"## Orphans (discovered via DNS, never referenced): {graph.get('orphans', [])}")
    parts.append(f"## Dangling references (referenced but unresolvable): {graph.get('dangling_references', [])}")
    parts.append("")

    parts.append(f"# Log pattern hits ({len(log_patterns)})")
    for p in log_patterns:
        suffix = f" [directive {p.get('directive')}]" if p.get("directive") else ""
        parts.append(
            f"- [{p.get('timestamp')}] {p.get('pod')} ({p.get('pattern')}){suffix}: "
            f"{p.get('event')}: {p.get('detail')}"
        )

    return "\n".join(parts)


def main() -> int:
    api_key = os.environ.get("LLM_API_KEY")
    if not api_key:
        log.error("LLM_API_KEY not set; cannot run reporting")
        return 1
    if not INPUT_PATH.exists():
        log.error("%s not found; run mapping first", INPUT_PATH)
        return 1

    log.info("Loading %s", INPUT_PATH)
    map_data = json.loads(INPUT_PATH.read_text())

    log.info("Building prompt")
    map_summary = _summarize_map(map_data)
    log.info("Map summary size: %d chars", len(map_summary))

    log.info("Calling Claude (%s) for report synthesis", MODEL)
    client = Anthropic(api_key=api_key)

    with client.messages.stream(
        model=MODEL,
        max_tokens=MAX_TOKENS,
        thinking={"type": "adaptive"},
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": map_summary}],
    ) as stream:
        final_message = stream.get_final_message()

    text = next(
        (b.text for b in final_message.content if b.type == "text"),
        None,
    )
    if not text:
        log.error("Claude returned no text content")
        return 1

    usage = final_message.usage
    log.info(
        "Report length: %d chars | input_tokens=%d output_tokens=%d "
        "cache_read=%s cache_create=%s",
        len(text),
        usage.input_tokens,
        usage.output_tokens,
        usage.cache_read_input_tokens or 0,
        usage.cache_creation_input_tokens or 0,
    )

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(text)
    log.info("Wrote %s", OUTPUT_PATH)
    return 0


if __name__ == "__main__":
    sys.exit(main())
