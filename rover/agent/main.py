"""End-to-end pipelines for both phases, invoked via `python -m agent <cmd>`.

`run_map()` runs the full mapping pipeline (crawl → derived data → assemble
→ atomic write → cleanup). `run_report()` (wired up in M5) reads the map and
runs the reporter. Both are designed to be resumable: they delegate atomicity
to the checkpoint modules, so a crash halfway through rerun continues from
the last successful unit.
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

from .audit import audit_citations, format_details, format_summary
from .checkpoint import MappingCheckpoint, ReportingCheckpoint
from .crawl import crawl
from .entities import (
    build_pod_ref_re,
    build_resource_re,
    collect_resource_vocab,
    expand_resource_tokens,
)
from .facets import build as build_facets
from .graph import build_edges, metrics as compute_metrics, reconcile
from .llm import Reporter
from .render import compose as compose_report
from .reporter import render_all
from .timeline import build as build_timeline

OUTPUT_DIR = Path("/rover/output")
MAP_PATH = OUTPUT_DIR / "map.json"
REPORT_PATH = OUTPUT_DIR / "report.md"
MAP_VERSION = "1"


def _now_ms() -> int:
    return int(time.time() * 1000)


def _write_atomic(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "wb") as f:
        f.write(content)
        f.flush()
    os.replace(tmp, path)


def run_map() -> int:
    """Run the full mapping pipeline; return a shell exit code."""
    gateway_url = os.environ.get("GATEWAY_URL")
    if not gateway_url:
        print("agent map: GATEWAY_URL is required", file=sys.stderr)
        return 1

    checkpoint = MappingCheckpoint()
    started_ms = _now_ms()

    # ---- 1. Crawl ----------------------------------------------------------
    crawl_out = crawl(gateway_url, checkpoint)
    pods = crawl_out["pods"]
    errors = crawl_out["errors"]
    queries = crawl_out["queries"]

    if not pods:
        print(
            "agent map: crawl produced zero pods; not writing map.json",
            file=sys.stderr,
        )
        return 1

    # ---- 2. Derived data --------------------------------------------------
    canonical_resources = sorted(collect_resource_vocab(pods))
    pod_re = build_pod_ref_re(pods.keys())
    resource_re = build_resource_re(expand_resource_tokens(canonical_resources))
    timeline = build_timeline(pods, pod_re, resource_re)
    facets = build_facets(timeline)

    raw_edges = build_edges(pods)
    edges = reconcile(raw_edges, timeline)
    graph_metrics = compute_metrics(pods, edges)

    # ---- 3. Meta / coverage ----------------------------------------------
    pod_errors = [e for e in errors if e.get("pod_id")]
    pods_with_errors = sorted({e["pod_id"] for e in pod_errors})
    status = "partial" if pod_errors else "complete"

    status_distribution: dict[str, int] = {}
    for edge in edges:
        status_distribution[edge["status"]] = status_distribution.get(edge["status"], 0) + 1

    meta: dict[str, Any] = {
        "map_version": MAP_VERSION,
        "generated_at": _now_ms(),
        "crawl_started_at": started_ms,
        "status": status,
        "coverage": {
            "pods_crawled": len(pods),
            "pods_with_errors": pods_with_errors,
            "timeline_entries": len(timeline),
            "edges": len(edges),
            "edge_status_distribution": status_distribution,
            "total_queries": len(queries),
            "resume_notes": crawl_out.get("resume_notes", []),
        },
    }

    map_out: dict[str, Any] = {
        "meta": meta,
        "colony_metadata": crawl_out["colony_metadata"],
        "seed_pod_id": crawl_out["seed_pod_id"],
        "gateway_hint_url": crawl_out.get("gateway_hint_url"),
        "pods": pods,
        "edges": edges,
        "graph_metrics": graph_metrics,
        "timeline": timeline,
        "facets": facets,
        "resource_vocab": canonical_resources,
        "queries": queries,
        "errors": errors,
    }

    # ---- 4. Atomic write + checkpoint cleanup -----------------------------
    content = json.dumps(map_out, sort_keys=True, indent=2).encode("utf-8")
    try:
        _write_atomic(MAP_PATH, content)
    except OSError as exc:
        print(f"agent map: failed to write {MAP_PATH}: {exc}", file=sys.stderr)
        return 1

    # Only clean up the checkpoint if the write succeeded. Failed writes
    # should leave the checkpoint intact so a retry can recover.
    checkpoint.cleanup()

    print(
        f"agent map: wrote {MAP_PATH} "
        f"({len(pods)} pods, {len(edges)} edges, {len(timeline)} timeline entries, "
        f"{len(queries)} queries, status={status})",
        file=sys.stderr,
    )
    return 0


def run_report() -> int:
    """Read `map.json`, synthesize sections with checkpoint, write `report.md`."""
    if not MAP_PATH.exists():
        print(
            f"agent report: {MAP_PATH} not found — run `agent map` first",
            file=sys.stderr,
        )
        return 1

    try:
        with open(MAP_PATH, "r", encoding="utf-8") as f:
            map_dict = json.load(f)
    except (OSError, ValueError) as exc:
        print(f"agent report: failed to read {MAP_PATH}: {exc}", file=sys.stderr)
        return 1

    reporting_ckpt = ReportingCheckpoint()
    reporter = Reporter(map_dict)

    if not reporter.available:
        print(
            "agent report: LLM_API_KEY missing — falling back to deterministic "
            "section templates. Report will still be written.",
            file=sys.stderr,
        )

    async def _render_and_close() -> dict[str, str]:
        try:
            return await render_all(map_dict, reporter, reporting_ckpt)
        finally:
            await reporter.aclose()

    sections = asyncio.run(_render_and_close())
    errors = reporting_ckpt.load_errors()
    report_md = compose_report(map_dict, sections, errors, reporter.stats)

    try:
        _write_atomic(REPORT_PATH, report_md.encode("utf-8"))
    except OSError as exc:
        print(f"agent report: failed to write {REPORT_PATH}: {exc}", file=sys.stderr)
        return 1

    reporting_ckpt.cleanup()

    degraded_sections = sorted(
        {e.get("section") for e in errors if e.get("stage") == "section_llm"}
        - {None}
    )
    summary = f"agent report: wrote {REPORT_PATH} ({len(sections)} sections"
    if degraded_sections:
        summary += f", degraded={degraded_sections}"
    if reporter.stats.get("call_count"):
        summary += f", llm_calls={reporter.stats['call_count']}"
    summary += ")"
    print(summary, file=sys.stderr)

    # Citation audit — informational, doesn't fail the write.
    audit = audit_citations(map_dict, report_md)
    print("agent report: " + format_summary(audit), file=sys.stderr)
    total_invalid = sum(len(audit[k]["invalid"]) for k in audit)
    if total_invalid:
        print(format_details(audit), file=sys.stderr)
    return 0


def run_audit() -> int:
    """Standalone citation audit — re-runs against the existing artifacts."""
    if not MAP_PATH.exists():
        print(f"agent audit: {MAP_PATH} not found", file=sys.stderr)
        return 1
    if not REPORT_PATH.exists():
        print(f"agent audit: {REPORT_PATH} not found", file=sys.stderr)
        return 1
    with open(MAP_PATH, "r", encoding="utf-8") as f:
        map_dict = json.load(f)
    with open(REPORT_PATH, "r", encoding="utf-8") as f:
        report_md = f.read()
    result = audit_citations(map_dict, report_md)
    print(format_summary(result))
    total_invalid = sum(len(result[k]["invalid"]) for k in result)
    if total_invalid:
        print(format_details(result))
    return 0 if total_invalid == 0 else 1
