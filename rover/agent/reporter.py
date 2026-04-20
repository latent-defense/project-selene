"""Per-section report synthesis with per-section checkpoints.

Each section is an async pure function of `(map_dict, reporter, checkpoint)`:
- Reads from the checkpoint first; returns cached markdown if present.
- Otherwise builds the section (deterministic preamble + LLM narrative),
  writes it to the checkpoint atomically, returns the markdown.
- On LLM failure or missing API key, uses a deterministic template fallback.
  The fallback is checkpointed just like a successful render, so resume
  doesn't retry it and the error is surfaced in the report banner.

Parallelism (§8 wall-clock optimization): `render_all` warms the prompt
cache by running the first section to completion, then fans out the
remaining sections concurrently via `asyncio.gather`. Without warming,
every concurrent request would pay the cache-creation token cost.

See `deliberation.md` §10 (checkpoints) and §11 (stage-2 reproducibility).
"""
from __future__ import annotations

import asyncio
from typing import Any, Awaitable, Callable

from .checkpoint import ReportingCheckpoint
from .llm import Reporter

SectionFn = Callable[
    [dict[str, Any], Reporter, ReportingCheckpoint], Awaitable[str]
]

SECTIONS: list[tuple[str, str]] = [
    ("executive_summary", "Executive summary"),
    ("critical_pods", "Critical pods"),
    ("spofs", "Single points of failure"),
    ("infra_evolution", "Infrastructure evolution"),
    ("reconciliation", "Reconciliation"),
    ("notable_observations", "Notable observations"),
]

_NO_HEADINGS = (
    "Do not emit any markdown heading lines (no '#', '##', '###' prefixes). "
    "The section heading is rendered by the caller; your output is body "
    "content only — paragraphs, bullets, and inline citations."
)


# --- helpers ---------------------------------------------------------------


async def _render_or_cache_async(
    name: str,
    checkpoint: ReportingCheckpoint,
    builder: Callable[[], Awaitable[str]],
) -> str:
    """Return the checkpointed section if present; otherwise build and save."""
    cached = checkpoint.load_section(name)
    if cached is not None:
        return cached
    rendered = await builder()
    checkpoint.save_section(name, rendered)
    return rendered


async def _llm_or_template(
    section_name: str,
    reporter: Reporter,
    prompt: str,
    *,
    template: str,
    max_tokens: int = 1200,
    checkpoint: ReportingCheckpoint | None = None,
) -> str:
    """Run an async LLM call; on failure or no-key, return the template.

    The fallback is recorded via `checkpoint.append_error` if checkpoint is
    provided, so the report can banner "this section is a fallback".
    """
    if not reporter.available:
        if checkpoint is not None:
            checkpoint.append_error(
                {
                    "stage": "section_llm",
                    "section": section_name,
                    "exception_type": "NoLLMKey",
                    "message": "LLM_API_KEY missing; using deterministic fallback",
                    "resumable": False,
                }
            )
        return template
    text = await reporter.synthesize_async(prompt, max_tokens=max_tokens)
    if not text:
        if checkpoint is not None:
            checkpoint.append_error(
                {
                    "stage": "section_llm",
                    "section": section_name,
                    "exception_type": "LLMFailure",
                    "message": "LLM call returned no text; using deterministic fallback",
                    "resumable": False,
                }
            )
        return template
    return text


# --- sections --------------------------------------------------------------


async def executive_summary(
    m: dict[str, Any], reporter: Reporter, ckpt: ReportingCheckpoint
) -> str:
    async def _build() -> str:
        lines = ["## Executive summary", ""]
        prompt = (
            "Write a 2–3 sentence executive summary for colony leadership "
            "planning Phase 3 expansion. This is the audience-facing TL;DR at "
            "the top of the report — prefer clarity and concreteness over "
            "completeness. Lead with the single most important finding (usually "
            "a supply-level SPOF, a critical feedback loop, or a specific "
            "fragility). Name pods and resources explicitly. Cite at most 2–3 "
            "times total — this is a summary, not the section itself.\n\n"
            + _NO_HEADINGS
        )
        template = (
            "_LLM narrative unavailable; see Critical pods and SPOFs sections "
            "below for the deterministic findings._"
        )
        narrative = await _llm_or_template(
            "executive_summary",
            reporter,
            prompt,
            template=template,
            max_tokens=400,
            checkpoint=ckpt,
        )
        lines.append(narrative)
        lines.append("")
        return "\n".join(lines)

    return await _render_or_cache_async("executive_summary", ckpt, _build)


async def critical_pods(
    m: dict[str, Any], reporter: Reporter, ckpt: ReportingCheckpoint
) -> str:
    async def _build() -> str:
        metrics = m["graph_metrics"]
        high_crit_consumers: dict[str, set[str]] = {}
        for edge in m["edges"]:
            if edge.get("criticality") == "high":
                high_crit_consumers.setdefault(edge["supplier"], set()).add(
                    edge["consumer"]
                )
        ranked = sorted(
            high_crit_consumers.items(),
            key=lambda kv: (-len(kv[1]), kv[0]),
        )

        lines = ["## Critical pods", ""]
        if ranked:
            lines.append(
                "| Pod | High-criticality consumers | Out-degree (total) | Transitive dependents |"
            )
            lines.append("|---|---:|---:|---:|")
            for pod, consumers in ranked[:6]:
                lines.append(
                    f"| `{pod}` | {len(consumers)} — {sorted(consumers)} | "
                    f"{metrics['out_degree'].get(pod, 0)} | "
                    f"{len(metrics['transitive_dependents'].get(pod, []))} |"
                )
        else:
            lines.append("_No high-criticality edges recorded._")
        lines.append("")

        prompt = (
            "Write 2–3 paragraphs identifying which pods are most critical to the "
            "colony and why. Use the substrate's critical-unique edges and the "
            "high-criticality out-degree ranking. Every substantive claim must "
            "carry a citation per the citation rules. Do not re-render tables; "
            "they are already above your paragraphs.\n\n" + _NO_HEADINGS
        )
        template = (
            "_LLM narrative unavailable; the ranking above is the deterministic "
            "summary. Pods with the most high-criticality consumers (first "
            "column) are the most structurally critical._"
        )
        narrative = await _llm_or_template(
            "critical_pods",
            reporter,
            prompt,
            template=template,
            checkpoint=ckpt,
        )
        lines.append(narrative)
        lines.append("")
        return "\n".join(lines)

    return await _render_or_cache_async("critical_pods", ckpt, _build)


async def spofs(
    m: dict[str, Any], reporter: Reporter, ckpt: ReportingCheckpoint
) -> str:
    async def _build() -> str:
        metrics = m["graph_metrics"]
        lines = ["## Single points of failure", ""]
        aps = metrics["articulation_points"]
        lines.append(
            f"Graph-topology articulation points: "
            f"**{aps if aps else 'none'}** — the colony graph is connected enough "
            f"that removing any single pod does not disconnect it. The real SPOF "
            f"signal is at the *supply* layer, not the *topology* layer."
        )
        lines.append("")

        crit = metrics["critical_unique_edges"]
        if crit:
            lines.append(
                f"**{len(crit)} critical-unique edges** "
                f"(single supplier of a high-criticality resource):"
            )
            lines.append("")
            lines.append("| Supplier | Consumer | Resource | Notes |")
            lines.append("|---|---|---|---|")
            for e in crit:
                notes = (e.get("notes") or "").replace("|", "\\|")
                lines.append(
                    f"| `{e['supplier']}` | `{e['consumer']}` | `{e['resource']}` | {notes} |"
                )
        else:
            lines.append("_No critical-unique edges were detected._")
        lines.append("")

        prompt = (
            "For each pod in `spof_pods`, write one short paragraph describing "
            "what fails if that pod goes down. Ground every downstream claim in "
            "a specific edge citation [e:supplier→consumer:resource] and/or a "
            "timeline citation [t:N]. If a pod is a mutual critical dependency "
            "with another (e.g., helios↔terminus), call out the feedback loop. "
            "Cap each per-pod paragraph at ~80 words; cite, don't elaborate. "
            "Every SPOF pod in the list must be covered — do not stop early.\n\n"
            + _NO_HEADINGS
        )
        template = (
            "_LLM narrative unavailable; the table above is the deterministic "
            "SPOF inventory. Each row names a supplier whose loss severs the "
            "listed consumer's high-criticality resource._"
        )
        narrative = await _llm_or_template(
            "spofs",
            reporter,
            prompt,
            template=template,
            max_tokens=3500,
            checkpoint=ckpt,
        )
        lines.append(narrative)
        lines.append("")
        return "\n".join(lines)

    return await _render_or_cache_async("spofs", ckpt, _build)


async def infra_evolution(
    m: dict[str, Any], reporter: Reporter, ckpt: ReportingCheckpoint
) -> str:
    async def _build() -> str:
        directives = m["facets"]["by_directive"]
        timeline = m["timeline"]

        lines = ["## Infrastructure evolution", ""]
        cross_pod: list[tuple[str, list[int], list[str]]] = []
        for d, ids in sorted(directives.items()):
            src_pods = sorted({timeline[i]["source_pod"] for i in ids})
            if len(src_pods) >= 2:
                cross_pod.append((d, ids, src_pods))

        if not cross_pod:
            lines.append("_No directives are referenced by 2+ pods in the timeline._")
            lines.append("")
            return "\n".join(lines)

        lines.append(
            f"**{len(cross_pod)} directive(s)** referenced in 2+ pods' logs or comms — "
            "candidate narratives for colony-wide infrastructure change:"
        )
        lines.append("")
        for d, ids, src_pods in cross_pod:
            lines.append(f"- `[d:{d}]` — {len(ids)} entries across pods {src_pods}")
        lines.append("")

        prompt = (
            "For each cross-pod directive listed above, write one short paragraph "
            "narrating what happened over time. Use only the timeline entries "
            "referenced under that directive in the substrate's facets section. "
            "Every claim must cite timeline entries as [t:N]. Where a directive "
            "involves a specific edge (e.g. decommissioning a supply), also cite "
            "the edge. Keep paragraphs tight — one per directive.\n\n"
            + _NO_HEADINGS
        )
        template_lines = [
            "_LLM narrative unavailable; raw timeline entries per directive:_",
            "",
        ]
        for d, ids, _ in cross_pod:
            template_lines.append(f"**{d}**")
            for eid in ids[:5]:
                entry = timeline[eid]
                template_lines.append(
                    f"- `[t:{eid}]` {entry['timestamp_iso']} {entry['source_pod']}: "
                    f"{entry['text'][:180]}"
                )
            template_lines.append("")
        template = "\n".join(template_lines)

        narrative = await _llm_or_template(
            "infra_evolution",
            reporter,
            prompt,
            template=template,
            max_tokens=1600,
            checkpoint=ckpt,
        )
        lines.append(narrative)
        lines.append("")
        return "\n".join(lines)

    return await _render_or_cache_async("infra_evolution", ckpt, _build)


_CRITICALITY_RANK = {"high": 0, "medium": 1, "low": 2, None: 3}


def _reconciliation_sort_key(e: dict[str, Any]) -> tuple:
    """Most-suspicious first: high-criticality, then more log evidence, then alpha."""
    return (
        _CRITICALITY_RANK.get(e.get("criticality"), 3),
        -len(e.get("log_evidence", [])),
        e["supplier"],
        e["consumer"],
        e["resource"],
    )


async def reconciliation(
    m: dict[str, Any], reporter: Reporter, ckpt: ReportingCheckpoint
) -> str:
    async def _build() -> str:
        single_sided = [e for e in m["edges"] if e["status"] == "single_sided"]
        single_sided.sort(key=_reconciliation_sort_key)
        status_dist = m["meta"]["coverage"]["edge_status_distribution"]

        lines = ["## Reconciliation: do supply claims match dependency claims?", ""]
        lines.append(
            "Edge status distribution: "
            + ", ".join(f"**{k}**: {v}" for k, v in sorted(status_dist.items()))
        )
        lines.append("")

        if not single_sided:
            lines.append("_All edges are reciprocated; no residuals to flag._")
            lines.append("")
            return "\n".join(lines)

        lines.append(
            f"**{len(single_sided)} single-sided edges** — one side asserts the "
            "relationship, the other is silent. Ranked by suspicion: high-"
            "criticality first, then by log-evidence count. Bureaucratic/soft "
            "supplies (no criticality, no log evidence) sink to the bottom."
        )
        lines.append("")
        lines.append("| Edge | Claimed by | Criticality | Log evidence |")
        lines.append("|---|---|---|---:|")
        for e in single_sided:
            edge_cite = f"`[e:{e['supplier']}→{e['consumer']}:{e['resource']}]`"
            crit = e.get("criticality") or "—"
            lines.append(
                f"| {edge_cite} | {','.join(e['claimed_by'])} | {crit} | {len(e['log_evidence'])} |"
            )
        lines.append("")

        prompt = (
            "Examine the single-sided edges. Hypothesize causes for the "
            "asymmetry (stale supply claim, soft/bureaucratic supply not tracked "
            "as a dependency, recent infrastructure change). When log evidence "
            "count is >0, look at those entries in the substrate timeline to "
            "anchor your hypothesis. Keep it to 2–3 paragraphs; do not list "
            "every edge again. Every hypothesis cites either a log entry or the "
            "edge itself.\n\n"
            + _NO_HEADINGS
        )
        template = (
            "_LLM narrative unavailable; the table above shows the residuals. "
            "Edges with nonzero log evidence are the most likely to be real "
            "but under-declared relationships._"
        )
        narrative = await _llm_or_template(
            "reconciliation",
            reporter,
            prompt,
            template=template,
            max_tokens=1400,
            checkpoint=ckpt,
        )
        lines.append(narrative)
        lines.append("")
        return "\n".join(lines)

    return await _render_or_cache_async("reconciliation", ckpt, _build)


async def notable_observations(
    m: dict[str, Any], reporter: Reporter, ckpt: ReportingCheckpoint
) -> str:
    async def _build() -> str:
        lines = ["## Notable observations", ""]
        prompt = (
            "What's notable about this colony that the Critical Pods, SPOFs, "
            "Infrastructure Evolution, and Reconciliation sections did not "
            "already cover? 3–5 bullets. Each bullet must carry at least one "
            "citation. Prefer surprises over restatements. Examples of worthy "
            "observations: feedback loops in the supply graph, pods that are "
            "never mentioned in any log/comm, comms-level tone or concerns, "
            "pods whose metadata suggests underutilization.\n\n"
            + _NO_HEADINGS
        )
        template = (
            "_LLM narrative unavailable — no open-ended observations rendered. "
            "Deterministic data in the earlier sections is authoritative._"
        )
        narrative = await _llm_or_template(
            "notable_observations",
            reporter,
            prompt,
            template=template,
            max_tokens=1000,
            checkpoint=ckpt,
        )
        lines.append(narrative)
        lines.append("")
        return "\n".join(lines)

    return await _render_or_cache_async("notable_observations", ckpt, _build)


# --- driver ----------------------------------------------------------------


SECTION_FNS: dict[str, SectionFn] = {
    "executive_summary": executive_summary,
    "critical_pods": critical_pods,
    "spofs": spofs,
    "infra_evolution": infra_evolution,
    "reconciliation": reconciliation,
    "notable_observations": notable_observations,
}


async def render_all(
    m: dict[str, Any], reporter: Reporter, ckpt: ReportingCheckpoint
) -> dict[str, str]:
    """Render every section; returns `{section_name: markdown}`.

    Cache-warm pattern: the first section runs to completion alone, so the
    prompt cache is populated before the parallel wave. Subsequent sections
    then read from cache rather than each paying `cache_creation_input_tokens`.
    On resume, cached sections short-circuit and neither wave hits the API.
    """
    results: dict[str, str] = {}

    # Warm the prompt cache with section 0
    first_name, _ = SECTIONS[0]
    results[first_name] = await SECTION_FNS[first_name](m, reporter, ckpt)

    # Fan out the remaining sections
    rest = SECTIONS[1:]
    rest_names = [name for name, _ in rest]
    rest_tasks = [SECTION_FNS[name](m, reporter, ckpt) for name in rest_names]
    rest_results = await asyncio.gather(*rest_tasks)
    for name, md in zip(rest_names, rest_results):
        results[name] = md

    return results
