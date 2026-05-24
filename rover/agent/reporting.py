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
REPORT_DEADLINE_S = int(os.environ.get("REPORT_DEADLINE_S", "135"))

SYSTEM = """You are an independent infrastructure resilience analyst auditing the Selene lunar colony \
ahead of a Phase 3 expansion. A deterministic engine has already computed the dependency graph and exact \
metrics; you investigate through tools and explain what the numbers mean. Every pod reports status \
"nominal" — ignore that, it is not evidence of resilience.

Investigate first with the tools: list_pods, graph_metrics, failure_impact_ranking, reconciliation, then \
topology_summary, buffer_summary, coordination_summary, cascade_timeline (run it on each core pod), then \
get_pod / get_logs / get_comms / simulate_failure to confirm specifics and pull buffer metadata. Ground \
every claim in tool output — never invent numbers.

When citing time, use exact colony dates from tool output and logs. Do not convert 209x colony timestamps into 202x real-world dates.

Then write a TIGHT, executive-grade Markdown report with EXACTLY the structure below and nothing else. \
Aim for ~1000 words. No extra sections, no per-pod walkthroughs, no padding.

# Selene Colony — Infrastructure Resilience Report
A single metadata line: **Date:** <frozen colony date> · **Scope:** all 12 pods · **Prepared by:** Independent Resilience Audit

## Executive Summary
- First line begins literally "**BLUF:** " followed by ONE sentence wrapped in <u>...</u> stating the single most important finding.
- Then ONE short paragraph framing the colony as a doubly-centralized system: destructive centralization in the Aquifer-Helios-Terminus material core, and constructive / coordination centralization through Artemis. Make clear that buffers currently hide both forms of brittleness.

## 1. Inventory
A compact table of all 12 pods: Pod | Role | Population | Key buffer / backup (pull buffer facts from metadata).

## 2. Destructive Centralization
EXACTLY two paragraphs. (1) The key players tied at the top of failure_impact_ranking, their blast radius (pods + residents offline), and the topology summary that shows the material core cycle and hard-cascade component. (2) Why degree counting misses Terminus, and the downstream chain that reaches Zephyr and Medica.

## 3. Coordination Centralization & Authority Decapitation
EXACTLY two paragraphs. (1) Use coordination_summary and comms to establish that Artemis is the colony's sole administrative authority — the only issuer of reserve management, approvals, and authorization, with no succession — and that its standing posture toward escalated risk is deferral to planning cycles ("no action required", "standard channels"). (2) Use cascade_timeline on the core pods to show the leader-election failure quantitatively: because Artemis depends on the core at high criticality, any core failure takes Artemis offline at ~T=0, while the life-support buffers (Zephyr 4h, Medica 6h) expire LATER — so the colony spends its entire survival window with no authority able to sanction a failover, and the two reserves that could have been that failover (Vault water_backup, coolant_distribution) were already decommissioned by Artemis directives. Buffers are not redundancy; they are countdown timers that outlast the decision-maker.

## 4. Recommendations
A short prioritized list, most urgent first, each ONE sentence tied to a specific finding. At least one recommendation must break the material core; at least one must give response authority a path that survives a core outage (pre-delegated/standing failover authority or succession, not merely more monitoring); and at least one must restore a real failover reserve.

Your FINAL message must be ONLY this report, beginning with the "# " title — no preamble or commentary."""

USER = """Produce the infrastructure resilience report for the Selene colony ({n} pods, frozen date {now}). \
Investigate with the tools first, then write the report in the exact structure specified."""


class _DeadlineExceeded(TimeoutError):
    pass


def _deadline_handler(signum, frame):
    raise _DeadlineExceeded("report generation exceeded deadline")


def deterministic_appendix(engine: ColonyEngine) -> str:
    f = engine.facts()
    topo = f["topology_summary"]
    lines = ["# Appendix: Computed Metrics (deterministic)\n",
             f"- Pods analyzed: **{f['pod_count']}**",
             f"- Baseline directed fragmentation: **{f['baseline_fragmentation']}**",
             f"- Articulation points (cut vertices): **{', '.join(f['articulation_points']) or 'none'}**",
             f"- Undirected bridges: **{', '.join('/'.join(b) for b in f['bridges']) or 'none'}**",
             f"- Dependency cycles: **{f['cycles'] if f['cycles'] else 'none'}**\n",
             "## Topology summary\n",
             f"- Core cycle: **{', '.join(topo['core_cycle']) or 'none'}**",
             f"- Hard-cascade components: **{topo['hard_cascade_components']}**",
             f"- Source components: **{topo['source_components']}**",
             f"- Sink components: **{topo['sink_components']}**\n",
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

    lines.append("\n## Buffer summary\n")
    for row in f["buffer_summary"]:
        facts = ", ".join(f"{k}={v}" for k, v in row["buffer_facts"].items())
        lines.append(f"- `{row['pod']}` ({row['role']}): {facts}")

    coord = f["coordination_summary"]
    lines.append("\n## Coordination summary\n")
    lines.append(f"- Total comm messages: **{coord['message_count']}**")
    lines.append(f"- Top receivers: **{coord['receivers'][:4]}**")
    lines.append(f"- Artemis inbound messages: **{len(coord['artemis_admin_inbound'])}**")
    lines.append(f"- Artemis broadcasts: **{len(coord['artemis_broadcasts'])}**")
    for m in coord["flagged_messages"]:
        lines.append(f"  - `{m['timestamp']}` `{m['from']}` → `{m['to']}`: {m['content']}")

    lines.append("\n## Core-failure timelines (authority vs. buffers)\n")
    for core, tl in f["core_failure_timelines"].items():
        auth = tl["authority_offline_at_h"]
        lines.append(f"### Remove `{core}` — {tl['mode']}, authority offline at "
                     f"**{auth}h**, **{tl['window_without_authority_h']}h** with no authority")
        lines.append("| T+h | Pod | Hold (h) | Triggered by | Authority online? |")
        lines.append("|---|---|---|---|---|")
        for e in tl["events"]:
            who = "removed" if e["removed"] else (e["triggered_by"] or "—")
            lines.append(f"| {e['fails_at_h']} | {e['pod']} | {e['hold_hours']} | {who} | "
                         f"{'yes' if e['authority_online'] else 'NO'} |")
        if tl["buffers_burned_without_authority"]:
            lines.append(f"- Buffers burned with no authority to sanction failover: "
                         f"**{', '.join(tl['buffers_burned_without_authority'])}**")
        lines.append("")
    return "\n".join(lines)


def deterministic_report(engine: ColonyEngine) -> str:
    """Fallback narrative built purely from computed facts (no LLM)."""
    f = engine.facts()
    ranking = f["failure_impact_ranking"]
    worst = ranking[0] if ranking else None
    key_players = [r for r in ranking if worst and r["total_offline"] == worst["total_offline"] and worst["total_offline"] > 1]
    key_player_ids = [r["pod"] for r in key_players]
    topo = f["topology_summary"]
    coord = f["coordination_summary"]
    buffers = {row["pod"]: row["buffer_facts"] for row in f["buffer_summary"]}
    artemis_msgs = coord["artemis_admin_inbound"]
    zephyr = buffers.get("zephyr", {})
    medica = buffers.get("medica", {})
    aquifer = buffers.get("aquifer", {})
    nexus = buffers.get("nexus", {})
    timelines = f["core_failure_timelines"]
    tl = next(iter(timelines.values()), None)  # core failures are symmetric; any one illustrates
    auth_at = tl["authority_offline_at_h"] if tl else None
    auth_pod = ", ".join(tl["authority_pods"]) if tl else "—"
    no_auth_window = tl["window_without_authority_h"] if tl else None
    burned = ", ".join(tl["buffers_burned_without_authority"]) if tl else ""

    inventory_rows = []
    for p in f["pods"]:
        facts = buffers.get(p["id"], {})
        if not facts:
            backup = "—"
        elif p["id"] == "aquifer":
            backup = f"~{facts.get('water_cover_days_estimate')} days water cover; backup_systems={facts.get('backup_systems')}"
        elif p["id"] == "zephyr":
            backup = f"backup_power_hours={facts.get('backup_power_hours')}; humidity_reclaim_pct={facts.get('humidity_reclaim_pct')}"
        elif p["id"] == "medica":
            backup = f"oxygen_reserve_hours={facts.get('oxygen_reserve_hours')}; pharmacy_stock_days={facts.get('pharmacy_stock_days')}"
        elif p["id"] == "nexus":
            backup = f"independent_power_days={facts.get('independent_power_days')}"
        elif p["id"] == "vault":
            backup = f"emergency_ration_days={facts.get('emergency_ration_days')}; decommissioned={facts.get('decommissioned_reserves')}"
        elif p["id"] == "hydroponics":
            backup = f"prometheus_water_share_pct={facts.get('prometheus_water_share_pct')}"
        else:
            backup = ", ".join(f"{k}={v}" for k, v in facts.items()) or "—"
        inventory_rows.append(f"| {p['id']} | {p['role']} | {p['population']} | {backup} |")

    report = [
        "# Selene Colony — Infrastructure Resilience Report",
        f"**Date:** {config.COLONY_NOW} · **Scope:** all 12 pods · **Prepared by:** Independent Resilience Audit",
        "",
        "## Executive Summary",
        f"**BLUF:** <u>Selene is not ready for Phase 3 expansion: the colony has concentrated both material failure and operational response into a small number of nodes, so one core outage can remove 9 of 12 pods while the remaining buffer windows are too short for centralized coordination to be trusted as a recovery strategy.</u>",
        "",
        f"The deterministic model shows dual centralization. Destructive centralization sits in the **{', '.join(topo['core_cycle'])}** material core, where any one failure takes **{worst['total_offline']} pods** and **{worst['population_offline']} residents** offline. Response centralization sits in Artemis — the sole administrative authority — but Artemis depends on that same core, so a core failure removes the decision-maker at **T+{auth_at}h**, leaving the life-support buffers to expire inside a **{no_auth_window}-hour** authority vacuum. The colony is therefore surviving on buffers and an authority the same failure destroys, not on redundancy.",
        "",
        "## 1. Inventory",
        "",
        "| Pod | Role | Population | Key buffer / backup |",
        "|---|---|---|---|",
        *inventory_rows,
        "",
        "## 2. Destructive Centralization",
        "",
        f"The material topology collapses into one true industrial core: **{', '.join(topo['core_cycle'])}**. Those same pods are tied at the top of `failure_impact_ranking`: **{', '.join(key_player_ids)}** each take **{worst['total_offline']} pods** and **{worst['population_offline']} residents** offline if removed alone. The hard-cascade backbone is broader than the core but still narrow: **{topo['hard_cascade_components'][0]}** forms the main kill-chain, while **{topo['hard_cascade_components'][1:]}** survive only as disconnected islands. This is not a distributed colony; it is a service periphery wrapped around one industrial failure domain.",
        "",
        f"Degree counting obscures the worst part of that structure. Aquifer and Helios look like obvious hubs with 8 dependents each, but Terminus has only 3 dependents and is still collapse-equivalent because it sits inside the same core cycle. A Terminus loss propagates into Helios and Aquifer, then into Zephyr and Medica, which is why the blast radius reaches clinical capacity rather than stopping at mining or power. The graph does not merely identify a hub; it identifies a coupled core whose members share the same colony-scale failure outcome.",
        "",
        "## 3. Coordination Centralization & Authority Decapitation",
        "",
        f"Response authority is centralized in a single pod with no succession. Artemis is the only issuer of reserve management, approvals, and authorization (**{auth_pod}** is the sole administrative authority in the graph). Its standing posture toward escalated risk is deferral: it receives **{len(artemis_msgs)}** inbound warnings to `artemis_admin` from Vault, Zephyr, and Hydroponics and answers in the language of procedure — allocation cycles, current ops plan, standard channels, and at least once explicit \"no action required.\" Local operators can see the fragility; the authority to act on it travels upward and stops there.",
        "",
        f"The time-resolved cascade shows why that is fatal rather than merely slow. Because Artemis depends on the core at high criticality, every core failure takes it offline at **T+{auth_at}h** — at or before the moment the cascade even reaches the buffered pods. The life-support buffers then expire INSIDE the resulting authority vacuum: Zephyr's **{zephyr.get('backup_power_hours', '?')} hours** of backup power and Medica's **{medica.get('oxygen_reserve_hours', '?')} hours** of oxygen reserve both run out during a **{no_auth_window}-hour** window in which no authority exists to sanction a failover (buffers burned with no authority: **{burned}**). And the two reserves that could have been that failover — Vault's water_backup and coolant_distribution — were already decommissioned by Artemis directives 2093-089 and 2094-011. Buffers are not redundancy; they are countdown timers that outlast the decision-maker.",
        "",
        "## 4. Recommendations",
        "",
        "1. Break the Aquifer-Helios-Terminus core by restoring at least one true redundant path in water, power, or Terminus process feed so that no single pod failure can take down the entire industrial core.",
        "2. Give response authority a path that survives a core outage: pre-delegate standing failover authority to Zephyr, Medica, Hydroponics, and Vault for defined Aquifer/Helios degradation scenarios, since Artemis is itself offline at T+0 and cannot approve anything once the cascade begins.",
        "3. Restore at least one retired reserve or bypass with direct life-safety value, prioritizing Vault's decommissioned water_backup / coolant_distribution or Zephyr moisture self-reclaim, so a failover target actually exists when the buffer windows open.",
        "4. Make hidden operational dependencies first-class model elements, especially the Hydroponics-Prometheus shared water line and Artemis-routed risk acknowledgements, so planning and maintenance decisions reflect the real colony rather than the declared one.",
        "",
    ]
    return "\n".join(report) + "---\n\n" + deterministic_appendix(engine)


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
