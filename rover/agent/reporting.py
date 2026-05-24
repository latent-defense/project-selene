"""Reporting agent: load map.json, run the analyst agent, write report.md.

The deterministic engine computes all metrics; the LLM investigates via tools and
writes the narrative. A computed-metrics appendix is always appended so numbers
are auditable, and a deterministic-only report is emitted if the LLM is
unavailable — the job always produces a useful report.
"""
import json
import logging
import os
import re
import signal
import sys

from . import config
from .engine import ColonyEngine

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
log = logging.getLogger("reporting")
REPORT_DEADLINE_S = int(os.environ.get("REPORT_DEADLINE_S", "50"))

SYSTEM = """You are an independent infrastructure resilience analyst auditing the Selene lunar colony \
ahead of a Phase 3 expansion. A deterministic engine has already computed the dependency graph and exact \
metrics; you investigate through tools and explain what the numbers mean. Every pod reports status \
"nominal" — ignore that, it is not evidence of resilience.

Investigate first with the tools: list_pods, graph_metrics, failure_impact_ranking, reconciliation, then \
get_pod / get_logs / get_comms / simulate_failure to confirm specifics and pull buffer metadata. Ground \
every claim in tool output — never invent numbers.

When citing time, use exact colony dates from tool output and logs. Do not convert 209x colony timestamps into 202x real-world dates.

Then write a TIGHT, executive-grade Markdown report with EXACTLY the structure below and nothing else. \
Aim for ~1000 words. No extra sections, no per-pod walkthroughs, no padding.

# Selene Colony — Infrastructure Resilience Report
A single metadata line: **Date:** <frozen colony date> · **Scope:** all 12 pods · **Prepared by:** Independent Resilience Audit

## Executive Summary
- First line begins literally "**BLUF:** " followed by ONE sentence wrapped in <u>...</u> stating the single most important finding.
- Then ONE short paragraph on the dynamics the key-player analysis surfaces — why Borgatti's Key-Player (KP-Neg) framing (remove nodes to maximize network fragmentation / blast radius) is more insightful here than a simple "most-depended-upon" count: the critical pods form a tightly coupled dependency CYCLE so a single removal collapses most of the colony, and at least one key player is invisible to degree-counting (low in-degree, high blast radius through the cycle).

## 1. Inventory
A compact table of all 12 pods: Pod | Role | Population | Key buffer / backup (pull buffer facts from metadata).

## 2. Critical Pods & Blast Radius
EXACTLY two paragraphs. (1) The key players (tied at the top of failure_impact_ranking), their blast radius (pods + residents offline), and the cycle that couples them into one failure domain. (2) The hidden single point of failure that degree-counting misses (cite its low dependent-count vs its large blast radius), and the downstream chain that reaches medical care.

## 3. Iterative Dynamics: Buffers and the Time Dimension
Explain why KP-Neg / blast-radius is the right lens when dependencies are NOT uniform: edges differ in criticality AND in buffer-mediated latency, so the same topological cascade plays out over very different timescales — the graph tells you WHO falls, buffers tell you HOW FAST. Use concrete metadata: Zephyr (humidity_reclaim_pct 0 → no internal buffer, 100% reliant on Aquifer; backup_power_hours 4 → O2 processors cycle down ~4h after power loss) vs a buffered survivor (Nexus independent_power_days 30, which is exactly why it survives the cascade) and Aquifer's thin margin (reservoir_capacity_l ÷ throughput_l_day ≈ days of water). Note buffers that were deliberately removed where relevant.

## 4. Recommendations
A short prioritized list, most urgent first, each ONE sentence tied to a specific finding.

Your FINAL message must be ONLY this report, beginning with the "# " title — no preamble or commentary."""

USER = """Produce the infrastructure resilience report for the Selene colony ({n} pods, frozen date {now}). \
Investigate with the tools first, then write the report in the exact structure specified."""


class _DeadlineExceeded(TimeoutError):
    pass


def _deadline_handler(signum, frame):
    raise _DeadlineExceeded("report generation exceeded deadline")


def deterministic_appendix(engine: ColonyEngine) -> str:
    f = engine.facts()
    lines = ["# Appendix: Computed Metrics (deterministic)\n",
             f"- Pods analyzed: **{f['pod_count']}**",
             f"- Baseline directed fragmentation: **{f['baseline_fragmentation']}**",
             f"- Articulation points (cut vertices): **{', '.join(f['articulation_points']) or 'none'}**",
             f"- Dependency cycles: **{f['cycles'] if f['cycles'] else 'none'}**\n",
             "## Most depended-upon pods\n",
             "| Pod | Role | Dependents | High-criticality dependents |",
             "|---|---|---|---|"]
    for r in f["depended_upon"]:
        if r["dependent_count"]:
            lines.append(f"| {r['pod']} | {r['role']} | {r['dependent_count']} | "
                         f"{', '.join(r['high_criticality_dependents']) or '—'} |")

    lines.append("\n## Single-pod failure impact (blast radius)\n")
    lines.append("_Each row: the colony-wide impact of that one pod failing, via cascade. "
                 "The top pods (tied) are the true key players / single points of failure._\n")
    lines.append("| Pod | Role | Pods offline | Pop. offline | Cascades to |")
    lines.append("|---|---|---|---|---|")
    for r in f["failure_impact_ranking"]:
        casc = ", ".join(r["cascaded_offline"]) or "—"
        lines.append(f"| {r['pod']} | {r['role']} | {r['total_offline']} | "
                     f"{r['population_offline']} | {casc} |")

    rec = f["reconciliation"]
    lines.append("\n## Material supply/dependency mismatches\n")
    lines.append(f"- Matched material edges: **{len(rec['matched'])}**")
    lines.append(f"- Supply claimed but consumer doesn't declare dependency: **{len(rec['material_supply_without_dependency'])}**")
    for m in rec["material_supply_without_dependency"]:
        lines.append(f"  - `{m['supplier']}` → `{m['consumer']}` ({m['resource']})")
    lines.append(f"- Dependency declared but supplier doesn't acknowledge: **{len(rec['material_dependency_without_supply'])}**")
    for m in rec["material_dependency_without_supply"]:
        lines.append(f"  - `{m['consumer']}` depends on `{m['supplier']}` ({m['resource']})")
    return "\n".join(lines)


def deterministic_report(engine: ColonyEngine) -> str:
    """Fallback narrative built purely from computed facts (no LLM)."""
    f = engine.facts()
    top = f["depended_upon"][0] if f["depended_upon"] else None
    ranking = f["failure_impact_ranking"]
    worst = ranking[0] if ranking else None
    key_players = [r["pod"] for r in ranking if worst and r["total_offline"] == worst["total_offline"] and worst["total_offline"] > 1]
    head = ["# Selene Colony — Infrastructure Resilience Assessment",
            "_Generated without LLM narration (deterministic fallback)._\n",
            "## Executive Summary\n"]
    if top:
        head.append(f"- The most depended-upon pod is **{top['pod']}** ({top['role']}), "
                    f"with {top['dependent_count']} dependents.")
    if worst:
        head.append(f"- The key players (each individually catastrophic) are **{', '.join(key_players)}** — "
                    f"any one failing takes {worst['total_offline']} pods and {worst['population_offline']} residents offline.")
    head.append(f"- Articulation points: {', '.join(f['articulation_points']) or 'none'}.")
    return "\n".join(head) + "\n\n" + deterministic_appendix(engine)


def main():
    try:
        with open(config.MAP_PATH) as fp:
            map_doc = json.load(fp)
    except Exception as e:
        log.error("could not read map.json: %s", e)
        return 1

    engine = ColonyEngine(map_doc)
    log.info("engine built: %d pods, baseline fragmentation %s",
             len(engine.ids), engine.baseline_fragmentation())

    appendix = deterministic_appendix(engine)

    try:
        from .llm import run_agent
        previous = signal.signal(signal.SIGALRM, _deadline_handler)
        signal.alarm(REPORT_DEADLINE_S)
        try:
            narrative = run_agent(SYSTEM, USER.format(n=len(engine.ids), now=config.COLONY_NOW), engine)
        finally:
            signal.alarm(0)
            signal.signal(signal.SIGALRM, previous)
        # Strip any pre-title preamble the model emits before the first Markdown heading.
        m = re.search(r"^# ", narrative, re.M)
        if m:
            narrative = narrative[m.start():]
        report = narrative.strip() + "\n\n---\n\n" + appendix
        log.info("LLM narrative generated (%d chars)", len(narrative))
    except Exception as e:
        log.warning("LLM reporting failed (%s); writing deterministic report", e)
        report = deterministic_report(engine)

    with open(config.REPORT_PATH, "w") as fp:
        fp.write(report)
    log.info("wrote %s (%d chars)", config.REPORT_PATH, len(report))
    return 0


if __name__ == "__main__":
    sys.exit(main())
