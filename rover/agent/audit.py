"""Post-hoc citation validator for the generated report.

Every claim in `report.md` carries a citation in one of three shapes:
`[t:N]` (timeline entry), `[d:YYYY-NNN]` (directive), or `[e:s→c:r]` (edge).
The reporter is instructed (via `llm.py:CITATION_RULES`) to only cite
entities that appear in the substrate, but LLM outputs can still drift —
especially on the `[e:...]` form where a plausible-sounding triple is easy
to fabricate. This module parses the rendered report and checks every
citation against the authoritative `map.json`, reporting what's valid and
what isn't.

The audit is informational: it prints a summary but does not mutate the
report. The operator decides whether an invalid citation warrants a
regeneration.
"""
from __future__ import annotations

import re
from typing import Any

TIMELINE_RE = re.compile(r"\[t:(\d+)\]")
DIRECTIVE_RE = re.compile(r"\[d:(\d{4}-\d{3})\]")
EDGE_RE = re.compile(r"\[e:([^\]→]+)→([^\]:]+):([^\]]+)\]")


def audit_citations(map_dict: dict[str, Any], report_md: str) -> dict[str, Any]:
    """Validate every citation in `report_md` against `map_dict`.

    Returns a dict with one entry per citation kind:
      ``{ 'timeline': {'valid': N, 'invalid': [refs]},
          'directive': {...}, 'edge': {...} }``.
    Invalid lists are deduplicated and sorted for stable reporting.
    """
    timeline_ids: set[int] = {entry["id"] for entry in map_dict.get("timeline", [])}
    known_directives: set[str] = set(
        map_dict.get("facets", {}).get("by_directive", {}).keys()
    )
    # Also accept directives that appear in any timeline entry's entities —
    # single-pod directives aren't in the by_directive facet if we key strictly,
    # so this broadens the accept set.
    for entry in map_dict.get("timeline", []):
        for d in entry.get("entities", {}).get("directive_ids", []):
            known_directives.add(d)

    edge_triples: set[tuple[str, str, str]] = {
        (e["supplier"], e["consumer"], e["resource"])
        for e in map_dict.get("edges", [])
    }

    t_valid, t_bad = 0, set()
    for m in TIMELINE_RE.finditer(report_md):
        tid = int(m.group(1))
        if tid in timeline_ids:
            t_valid += 1
        else:
            t_bad.add(f"[t:{tid}]")

    d_valid, d_bad = 0, set()
    for m in DIRECTIVE_RE.finditer(report_md):
        did = m.group(1)
        if did in known_directives:
            d_valid += 1
        else:
            d_bad.add(f"[d:{did}]")

    e_valid, e_bad = 0, set()
    for m in EDGE_RE.finditer(report_md):
        triple = (m.group(1).strip(), m.group(2).strip(), m.group(3).strip())
        if triple in edge_triples:
            e_valid += 1
        else:
            e_bad.add(f"[e:{triple[0]}→{triple[1]}:{triple[2]}]")

    return {
        "timeline": {"valid": t_valid, "invalid": sorted(t_bad)},
        "directive": {"valid": d_valid, "invalid": sorted(d_bad)},
        "edge": {"valid": e_valid, "invalid": sorted(e_bad)},
    }


def format_summary(result: dict[str, Any]) -> str:
    """One-line summary suitable for stderr logging."""
    parts = []
    for kind in ("timeline", "directive", "edge"):
        v = result[kind]["valid"]
        n_bad = len(result[kind]["invalid"])
        parts.append(f"{kind}={v} valid / {n_bad} invalid")
    return "citations: " + ", ".join(parts)


def format_details(result: dict[str, Any]) -> str:
    """Multi-line breakdown listing every invalid citation."""
    lines = []
    for kind in ("timeline", "directive", "edge"):
        v = result[kind]["valid"]
        bad = result[kind]["invalid"]
        lines.append(f"{kind}: {v} valid, {len(bad)} invalid")
        for ref in bad:
            lines.append(f"  - {ref}")
    return "\n".join(lines)
