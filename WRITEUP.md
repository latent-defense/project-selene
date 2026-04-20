# Project Selene — Candidate Writeup

## Architecture

The agent is two scripts (`run_mapping.sh`, `run_reporting.sh`) — both Python
modules under `rover/agent/` — wired by a single intermediate artifact,
`/rover/output/map.json`. Mapping is a deterministic crawler; reporting is a
pure function of the map plus an LLM. There is no LLM call in the mapping
phase and no HTTP call in the reporting phase.

**Key design decisions:**

- **`map.json` is the interface, not a dump.** Designed for downstream
  analysis: per-pod payloads, edges with reconciliation status + criticality,
  graph metrics (topology + supply-level SPOFs), unified timeline (94 logs +
  16 comms across 12 pods, sorted, with entity tags), inverted facet indexes
  (by directive / resource / pod-mentioned), full HTTP query log, error log,
  discovery trace.
- **Iterative discovery from one URL.** The gateway returns only `{pod:
  artemis}`; everything else is reached via BFS over `/supplies` and
  `/dependencies`. Pods are resolved by port-probing the documented 3001-3012
  range — every probe is a receipt in the query log, so the discovery path is
  reconstructable.
- **Edges as evidence, not as facts.** Every edge carries `claimed_by`
  (`upstream` from `/supplies`, `downstream` from `/dependencies`),
  `criticality` (from the consumer side), and `log_evidence` (timeline entry
  ids that mention the relationship). Status is categorical:
  `reciprocated` / `reciprocated_with_logs` / `single_sided`. The map
  records uncertainty; the report explains it.
- **Deterministic clustering, LLM narration.** "Clusters" are built
  deterministically via shared-entity facets (directive IDs, resource
  tokens, pod mentions). The LLM's job is prose synthesis with grounded
  citations — every claim is required to cite a timeline entry, directive,
  or edge.
- **Point-in-time recovery throughout.** Both phases checkpoint to
  `/rover/output/.checkpoint/` — per-pod for mapping, per-section for
  reporting. A mid-phase crash re-runs only the affected unit. Verified
  live: a partial crawl that persisted 151 HTTP receipts re-ran with
  exactly **1 new HTTP call** (the gateway re-fetch).
- **Stage-level optimization targets.** Stage 1 is tuned for accuracy and
  explainability (no LLM, every claim traces to receipts). Stage 2 uses
  `temperature=0`, prompt caching of the substrate, and fallback templates
  per section so degraded-LLM runs still ship a useful report.

## What the agent found

- **6 supply-level SPOF pods**: `aquifer`, `helios`, `hydroponics`,
  `prometheus`, `terminus`, `zephyr`. Each is the *sole* supplier of a
  high-criticality resource to at least one consumer.
- **Helios is the dominant hub** — 6 of the other 11 pods critically depend
  on it for `electrical_power`. Loss of helios cascades farther than any
  other single failure.
- **Cross-critical pair**: `helios ↔ terminus` (helios supplies power to
  terminus, terminus supplies silicon_feedstock to helios). Mutual hard
  dependency: a failure in one risks both.
- **Zero topological articulation points** — the colony graph is connected
  enough that no single pod's removal disconnects it. The real SPOF risk
  lives at the supply layer, not the topology layer.
- **16 of 39 edges are single-sided.** Most are `artemis → X` for
  bureaucratic supplies (`administrative_oversight`, `research_authorization`)
  that pods do not list as hard dependencies — likely a categorical
  asymmetry between operational and administrative dependencies, not a data
  bug.
- **2 cross-pod directives** (`2093-089`, `2094-011`) are referenced in
  multiple pods' logs — candidate narratives for colony-wide infrastructure
  evolution events. The reporter uses these as anchor points for the
  evolution section.
- **2 pods are never mentioned by name in any log or comm**: `nexus` and
  `zephyr`. They participate in supply edges but do not appear in any
  narrative — operationally invisible despite structural connectivity.

## With more time

These map directly to `docs/deliberation.md` §9 (out-of-scope questions):

- **Inferred edges from prose.** A `log_only` edge status — relationships
  that neither side declares but that recur in logs — would catch
  decommissioned-but-still-active patterns. Requires structured-claim
  extraction from log text.
- **Disputed edge detection.** Cross-edge pattern matching to detect
  contradictions (e.g., A claims to supply X to B, but B's `/dependencies`
  names a different supplier for X).
- **Section parallelization.** The 5 reporter section calls are currently
  serial. `asyncio.gather` over the Anthropic async client would cut latency
  ~5×; substrate caching means cost is unaffected.
- **Schema versioning + diffability.** `map.json` carries a `map_version`;
  successive runs would benefit from a structural diff (what changed in the
  graph, which edges flipped reconciliation status, what's new in the
  timeline).
- **Time-windowed timeline buckets.** At 110 entries the unified timeline
  is fine to render whole; at 10K+ it would want pre-computed quarter
  buckets in the substrate.
- **Snapshot semantics.** The pods are static here. In production, capture
  ETags per response and detect mid-crawl drift.

## Repo layout

- `rover/agent/` — Python source (one module per responsibility).
- `rover/tests/` — pytest unit tests (32 passing) over the deterministic
  pieces (entities, graph, timeline, checkpoint).
- `rover/Dockerfile` — extends the provided base with `httpx`, `anthropic`,
  `networkx`, `pytest`.
- `docs/approach.md` — 10,000-ft architecture summary.
- `docs/deliberation.md` — full record of the design dialog (the
  not-rationalized-after-the-fact version).
- `docs/sprint-plan.md` — milestone-level execution plan with acceptance
  criteria.

## How to verify

```bash
LLM_API_KEY=<your-key> docker compose up --build -d

# Mapping
curl -X POST localhost:8080/map
# poll
curl localhost:8080/get-map  # → 200 + map.json once complete

# Reporting (after mapping done)
curl -X POST localhost:8080/report
curl localhost:8080/get-report  # → 200 + report.md

# Tests
docker compose exec rover python -m pytest tests/ -v

# Resume test (kill mid-run, re-trigger; observe queries.jsonl re-use)
# Pod-down test (docker compose stop medica; re-run /map; status=partial)
```
