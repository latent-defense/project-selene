"""Anthropic-SDK wrapper tuned for the reporting phase.

Design goals (see `deliberation.md` §3, §11):
- Reproducibility: pinned model id + deterministic substrate. (Opus 4.7
  deprecates the `temperature` parameter; the model handles its own
  sampling, so we don't pass it.)
- Cheap re-use: substrate is a cached system prefix, so every section call
  after the first hits the prompt cache.
- Graceful degradation: API failures return `None`; callers fall back to
  deterministic templates (see `reporter.py`).

The substrate is a compact, structured rendering of the map — not the raw
JSON — focused on what the reporter actually needs: pods, edges, metrics,
facets, timeline, and explicit citation rules.
"""
from __future__ import annotations

import json
import os
import sys
from typing import Any

from anthropic import Anthropic, APIError, AsyncAnthropic

DEFAULT_MODEL = "claude-opus-4-7"
DEFAULT_MAX_TOKENS = 1200
# Note: Opus 4.7 deprecates the `temperature` parameter entirely; the model
# manages its own sampling. Reproducibility comes from a pinned model id
# + a fixed substrate, not from `temperature=0` (which the API rejects).

CITATION_RULES = """\
## Citation rules (REQUIRED)

Ground every substantive claim in one of these three citation forms:

- Timeline entry: `[t:N]` where N is the entry id.
- Directive: `[d:YYYY-NNN]` (e.g. `[d:2093-089]`).
- Edge: `[e:supplier→consumer:resource]` (e.g. `[e:helios→medica:electrical_power]`).

**Hard rules:**

1. Write citations as bare brackets. Never wrap them in backticks, quotes, or
   any other delimiter. Correct: `[t:47]`. Incorrect: `` `[t:47]` ``.
2. Every `[e:supplier→consumer:resource]` citation MUST match verbatim a row
   in the substrate's Edges section above. If the relationship you want to
   describe is not in the Edges list, you may not cite it as an edge. Instead,
   either describe the relationship in prose without citation, or cite a
   `[t:N]` timeline entry that references it. **Do not invent edge triples.**
3. A claim without a citation is worse than no claim. Prefer concision: two
   well-cited sentences beat a paragraph of unsupported prose.

## Few-shot examples

**Good — real edge + timeline:**

    Helios is the sole high-criticality supplier of electrical_power to zephyr
    [e:helios→zephyr:electrical_power]. Zephyr has only ~4 hours of local
    reserve before processors cycle down [t:88], so a helios outage is
    time-critical rather than merely inconvenient.

**Bad — invented edge (DO NOT WRITE THIS):**

    ❌ Helios also supplements power to nexus [e:helios→nexus:electrical_power].

(If no `helios→nexus:electrical_power` row exists in the Edges section above,
this citation is hallucinated. Write the ✅ version below instead.)

**✅ Fix — the same claim, grounded in evidence that does exist:**

    Nexus operates a 30-day independent battery reserve and its own water
    micro-recycler [t:42], so its grid-power dependency is soft rather than
    critical.

**Graceful no-edge — narrate with `[t:N]` only:**

    Hydroponics flagged the Aquifer-dependency consolidation risk to Artemis
    for Q3 capacity planning [t:86], but no follow-up directive appears in the
    timeline.

(No edge citation here because no single edge captures the flag-and-ignore
pattern — the evidence is a log event, so we cite it directly.)
"""


def _build_substrate(m: dict[str, Any]) -> str:
    """Render the map into a compact prose substrate for the LLM."""
    parts: list[str] = []
    parts.append("# Project Selene — Colony Analyst Substrate")
    parts.append("")
    parts.append(
        "This substrate is authoritative ground truth. Do not speculate beyond it."
    )
    parts.append("")

    cm = m["colony_metadata"]
    parts.append("## Colony")
    parts.append(
        f"{cm.get('colony', 'Project Selene')}; established "
        f"{cm.get('established', '?')}; population {cm.get('population', '?')}; "
        f"status {cm.get('status', '?')}."
    )
    parts.append("")

    metrics = m["graph_metrics"]

    parts.append(f"## Pods ({len(m['pods'])})")
    for pod_id in sorted(m["pods"]):
        pod = m["pods"][pod_id]
        info = pod["endpoints"].get("/info") or {}
        in_deg = metrics["in_degree"].get(pod_id, 0)
        out_deg = metrics["out_degree"].get(pod_id, 0)
        trans_dep = len(metrics["transitive_dependents"].get(pod_id, []))
        role = info.get("role", "?")
        parts.append(
            f"- **{pod_id}** — {role}. in-degree={in_deg}, out-degree={out_deg}, "
            f"transitive-dependents={trans_dep}."
        )
        meta = info.get("metadata") or {}
        if meta:
            parts.append(f"  metadata: {json.dumps(meta, sort_keys=True)}")
    parts.append("")

    parts.append(f"## Edges ({len(m['edges'])})")
    parts.append(
        "Format: `supplier → consumer : resource [status][criticality][log_evidence_count]`"
    )
    for e in m["edges"]:
        crit = e.get("criticality") or "—"
        parts.append(
            f"- `[e:{e['supplier']}→{e['consumer']}:{e['resource']}]` "
            f"[{e['status']}][crit={crit}][log_ev={len(e['log_evidence'])}]"
        )
        if e.get("notes"):
            parts.append(f"  notes: {e['notes']}")
    parts.append("")

    parts.append("## Graph metrics")
    parts.append(f"- Articulation points (topology SPOFs): {metrics['articulation_points']}")
    parts.append(
        f"- SPOF pods (sole source of high-criticality resource): {metrics['spof_pods']}"
    )
    parts.append("- Critical-unique edges (sole source + high criticality):")
    for e in metrics["critical_unique_edges"]:
        parts.append(
            f"  - `[e:{e['supplier']}→{e['consumer']}:{e['resource']}]` "
            f"(notes: {e.get('notes') or '—'})"
        )
    parts.append("")

    parts.append("## Facets")
    directives = m["facets"]["by_directive"]
    if directives:
        parts.append("- Directives referenced in timeline:")
        for d, ids in sorted(directives.items()):
            src_pods = sorted({m["timeline"][i]["source_pod"] for i in ids})
            parts.append(f"  - `[d:{d}]`: {len(ids)} entries, pods {src_pods}")
    top_resources = sorted(
        m["facets"]["by_resource"].items(), key=lambda kv: -len(kv[1])
    )[:15]
    parts.append("- Top resources by mention count:")
    for res, ids in top_resources:
        parts.append(f"  - {res}: {len(ids)} entries")
    parts.append("")

    parts.append(f"## Timeline ({len(m['timeline'])} entries)")
    for entry in m["timeline"]:
        kind = entry["kind"]
        prefix = f"[t:{entry['id']}] {entry['timestamp_iso']} {entry['source_pod']} ({kind})"
        if kind == "comm":
            prefix += f" {entry.get('from')}→{entry.get('to')}"
        parts.append(f"{prefix}: {entry['text']}")
    parts.append("")

    parts.append(CITATION_RULES)
    return "\n".join(parts)


class Reporter:
    """One instance per reporting run; holds the cached substrate."""

    def __init__(
        self,
        map_dict: dict[str, Any],
        *,
        model: str = DEFAULT_MODEL,
        api_key: str | None = None,
    ) -> None:
        self._map = map_dict
        self._substrate = _build_substrate(map_dict)
        key = api_key or os.environ.get("LLM_API_KEY")
        self._client = Anthropic(api_key=key) if key else None
        self._async_client = AsyncAnthropic(api_key=key) if key else None
        self._model = model
        self._call_count = 0
        self._cache_read_tokens = 0
        self._cache_create_tokens = 0
        self._input_tokens = 0
        self._output_tokens = 0

    @property
    def substrate(self) -> str:
        return self._substrate

    @property
    def available(self) -> bool:
        """True iff we have an API key. False → callers should use templates."""
        return self._client is not None

    async def aclose(self) -> None:
        """Close the async client; safe to call multiple times."""
        if self._async_client is not None:
            await self._async_client.close()

    def _messages_kwargs(self, prompt: str, max_tokens: int) -> dict[str, Any]:
        return {
            "model": self._model,
            "max_tokens": max_tokens,
            "system": [
                {
                    "type": "text",
                    "text": self._substrate,
                    "cache_control": {"type": "ephemeral"},
                }
            ],
            "messages": [{"role": "user", "content": prompt}],
        }

    def _record_usage(self, resp: Any) -> None:
        self._call_count += 1
        usage = getattr(resp, "usage", None)
        if usage is not None:
            self._cache_read_tokens += getattr(usage, "cache_read_input_tokens", 0) or 0
            self._cache_create_tokens += getattr(usage, "cache_creation_input_tokens", 0) or 0
            self._input_tokens += getattr(usage, "input_tokens", 0) or 0
            self._output_tokens += getattr(usage, "output_tokens", 0) or 0

    @staticmethod
    def _extract_text(resp: Any) -> str:
        chunks: list[str] = []
        for block in resp.content:
            if getattr(block, "type", None) == "text":
                chunks.append(block.text)
        return "".join(chunks).strip()

    def synthesize(
        self,
        prompt: str,
        *,
        max_tokens: int = DEFAULT_MAX_TOKENS,
    ) -> str | None:
        """Run one LLM call; returns text or None on error (caller templates)."""
        if self._client is None:
            return None
        try:
            resp = self._client.messages.create(
                **self._messages_kwargs(prompt, max_tokens)
            )
        except APIError as exc:
            print(f"LLM API error: {type(exc).__name__}: {exc}", file=sys.stderr)
            return None
        except Exception as exc:
            print(f"LLM unexpected error: {type(exc).__name__}: {exc}", file=sys.stderr)
            return None
        self._record_usage(resp)
        return self._extract_text(resp)

    async def synthesize_async(
        self,
        prompt: str,
        *,
        max_tokens: int = DEFAULT_MAX_TOKENS,
    ) -> str | None:
        """Async twin of `synthesize` — same contract, same failure mode."""
        if self._async_client is None:
            return None
        try:
            resp = await self._async_client.messages.create(
                **self._messages_kwargs(prompt, max_tokens)
            )
        except APIError as exc:
            print(f"LLM API error: {type(exc).__name__}: {exc}", file=sys.stderr)
            return None
        except Exception as exc:
            print(f"LLM unexpected error: {type(exc).__name__}: {exc}", file=sys.stderr)
            return None
        self._record_usage(resp)
        return self._extract_text(resp)

    @property
    def stats(self) -> dict[str, int]:
        return {
            "call_count": self._call_count,
            "input_tokens": self._input_tokens,
            "output_tokens": self._output_tokens,
            "cache_read_tokens": self._cache_read_tokens,
            "cache_create_tokens": self._cache_create_tokens,
        }
