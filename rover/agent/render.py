"""Compose section markdown fragments into a single `report.md`.

This module is purely string assembly — no LLM, no checkpoint. The ordered
section list lives in `reporter.SECTIONS`; we concatenate, prepend a header
with map metadata, and (if any sections used the deterministic fallback)
prepend a banner calling that out.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from .reporter import SECTIONS


def _iso(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%SZ")


def _header(m: dict[str, Any], reporter_stats: dict[str, int]) -> str:
    meta = m["meta"]
    cov = meta["coverage"]
    cm = m["colony_metadata"]
    lines = [
        f"# Project Selene — Infrastructure Assessment",
        "",
        f"> _{cm.get('colony', 'Project Selene')} — est. {cm.get('established', '?')}, "
        f"pop {cm.get('population', '?')}, status `{cm.get('status', '?')}`_",
        "",
        "**Map metadata**",
        "",
        f"- Map generated: `{_iso(meta['generated_at'])}`",
        f"- Map status: `{meta['status']}`",
        f"- Pods crawled: **{cov['pods_crawled']}** / 12",
        f"- Edges: **{cov['edges']}** "
        f"({', '.join(f'{k}={v}' for k, v in sorted(cov['edge_status_distribution'].items()))})",
        f"- Timeline entries: **{cov['timeline_entries']}**",
        f"- Total HTTP queries: **{cov['total_queries']}**",
    ]
    if cov.get("pods_with_errors"):
        lines.append(f"- Pods with errors: {cov['pods_with_errors']}")
    if reporter_stats.get("call_count"):
        lines.append(
            f"- LLM calls: {reporter_stats['call_count']} "
            f"(input={reporter_stats['input_tokens']}, "
            f"output={reporter_stats['output_tokens']}, "
            f"cache_read={reporter_stats['cache_read_tokens']}, "
            f"cache_create={reporter_stats['cache_create_tokens']})"
        )
    lines.append("")
    return "\n".join(lines)


def _fallback_banner(errors: list[dict[str, Any]]) -> str:
    section_fallbacks = sorted(
        {e.get("section") for e in errors if e.get("stage") == "section_llm"} - {None}
    )
    if not section_fallbacks:
        return ""
    return (
        "> ⚠️ **Partial report** — the following sections rendered from "
        "deterministic templates because the LLM call was unavailable or "
        f"errored: **{', '.join(section_fallbacks)}**. The tables and "
        "metrics in those sections are authoritative; the narrative prose is "
        "missing.\n\n"
    )


def compose(
    m: dict[str, Any],
    sections: dict[str, str],
    errors: list[dict[str, Any]],
    reporter_stats: dict[str, int] | None = None,
) -> str:
    """Assemble the full report markdown."""
    parts = [_header(m, reporter_stats or {})]
    banner = _fallback_banner(errors)
    if banner:
        parts.append(banner)
    for name, _heading in SECTIONS:
        body = sections.get(name, "").strip()
        if body:
            parts.append(body)
    return "\n\n".join(p for p in parts if p) + "\n"
