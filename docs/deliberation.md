# Project Selene — Design Deliberation

A running record of the design conversation between Claude and I before
touching code. The point is to show how the shape of the system was argued
into place, not to present a polished spec. Each section captures one
decision, the competing options, and why we landed where we did.

This document is **living**. As decisions get revisited, reversed, or
refined, sections get updated in place — but with enough of the prior shape
preserved that the narrative of *why it changed* stays readable. If you're
new to the project, reading top-to-bottom walks you through the thinking in
roughly the order it happened.

## 1. First principle — split the work at `map.json`

Starting observation: the harness gives us two independent jobs
(`run_mapping.sh`, `run_reporting.sh`) with `map.json` as the only artifact
between them. That's not an arbitrary scaffold choice — it's the design.

**Decision:** treat `map.json` as a *real* interface, not a dump. Mapping
produces it; reporting is a pure function of it. Nothing in reporting touches
HTTP.

**Why:** the two phases are independently retryable, independently debuggable,
and independently scorable. It also forces us to be explicit about what data
the report will need — schema design pressure is a feature.

## 2. Where does the LLM belong?

This was the first real fork. LLM access is offered at both phases
(`LLM_API_KEY` is passed to both scripts), so we had to decide deliberately
rather than by default.

**Options we weighed:**

- **LLM in mapping too** — agent reasons about which endpoints to hit, handles
  partial data, forms hypotheses. Maximalist "autonomous agent" read of the
  brief.
- **LLM only in reporting** — mapping is deterministic crawl; LLM earns its
  keep doing narrative synthesis over the map.
- **No LLM anywhere** — template-driven report from deterministic metrics.
  Fully reproducible, but thin on narrative.

**Decision:** LLM only in reporting.

**Why:** the mapping surface is 12 pods, 6 known-shape endpoints each, reached
from a single gateway pointer. That's not a discovery problem that needs
reasoning — it's a graph traversal. Putting an LLM in the crawl path trades
reproducibility for no gain. Conversely, the report has to synthesize ~110
log/comms entries into a cross-pod narrative about infrastructure evolution —
that's exactly the job LLMs are good at.

## 3. LLM stack — raw SDK or something heavier?

Given "LLM only in reporting," the stack choice is narrower than it looks.

**Options we weighed:**

- **Raw `anthropic` SDK + prompt caching** *(picked)* — small surface, total
  control.
- **Claude Agent SDK** — built-in tool orchestration. Useful if we wanted the
  LLM to drive HTTP probes. We don't.
- **langgraph / MCP** — state-machine orchestration. Overkill for a single
  pipeline with a bounded input.
- **No LLM** — falls out of §2.

**Decision:** raw SDK with prompt caching.

**Why:** once the LLM's job is "read this map, write these paragraphs," the
orchestration frameworks aren't solving a problem we have. Caching the map as
the long prefix across several focused calls (criticality narrative, SPOF
narrative, infrastructure-evolution narrative) keeps cost and latency
predictable.

## 4. Sampling + merging the logs — the corpus size changed the answer

This was the section with the most back-and-forth. Starting question: how do
we extract signal from `/logs` and `/comms` across 12 pods without losing
cross-pod patterns?

**The proposal on the table (me):** vector-DB-backed clustering of similar
log entries, then LLM-summarize each cluster.

**The pushback (Claude), after counting entries:**

- Actual corpus is ~**94 logs + 16 comms = 110 items total**.
- That fits in a single Claude call with thousands of tokens to spare.
- Vector clustering on 110 items adds infrastructure (embeddings, HDBSCAN /
  k-means), dependency weight, and — critically — *nondeterminism* across
  re-runs, for marginal lift over what an LLM does natively in-context.

**Counter-proposal:** deterministic entity indexing instead of semantic
clustering.

- The "clusters" that actually matter aren't semantic neighbors — they're
  **shared entities**: directive IDs (e.g. `2093-089`) that appear verbatim
  across pods, named resources (water, coolant, oxygen), explicit pod
  references.
- Regex + substring matching builds exact clusters with explainable
  membership.
- Ship the unified timeline *and* the facet indexes (`by_directive`,
  `by_resource`, `by_pod_mentioned`) in `map.json`. Let the LLM narrate the
  clusters, not discover them.

**Decision:** sample = don't (include everything); merge = unified timeline
with per-item entity tags; cluster = deterministic facets over those tags.

**Caveat we agreed on:** if log text turned out to paraphrase the same events
without shared vocabulary, embeddings would earn their keep. At this corpus
size with shared entity vocabulary, they don't.

## 5. Reproducibility as a tiebreaker

A theme that kept reappearing across §2–§4: **graders re-run containers**.
That's a design constraint, not a nice-to-have. Every place we had a choice
between "deterministic" and "clever," the tie broke toward deterministic
unless the LLM added a kind of value deterministic code structurally can't
(narrative synthesis, cross-item theming in prose).

The rule of thumb that emerged: **LLM produces prose; deterministic code
produces numbers, structure, and cluster boundaries.** Anywhere the boundary
between those is fuzzy, we chose the deterministic side.

## 6. Iterative discovery — URL resolution and query receipts

The critical framing for mapping: **we start with one URL, and everything
else has to be discovered.** The gateway hands us Artemis. From Artemis we
get `pod_id`s via `/supplies` and `/dependencies`. But the pod responses only
carry `id`, not addresses — so `pod_id` → URL is a real lookup step, not a
join on data we already have.

### Port range: documented, but probed

`candidate/README.md` tells us pods live on `3001–3012` and the rover is on
`8080`. The range is documented, but we don't use it to *assign* ports to
pods — we use it to *bound* a probe. For each newly-discovered `pod_id`,
the agent resolves by trying `http://{pod_id}:{port}/info` for `port in
3001..3012` until one returns 200 with a matching `id`. We trade a handful of
extra queries per unresolved pod for:

- robustness against port mis-ordering (or, in a real system, non-sequential
  assignments),
- a truthful "we discovered this" claim rather than "we guessed 3010 because
  it's the tenth in the compose file,"
- explicit evidence of the resolution process in the query log.

Candidate alternatives we considered and rejected:

- **nmap subnet sweep** — finds all 12 in one shot, but it's scan-first /
  crawl-second, which is the inverse of "let the graph reveal itself." It
  also loses the causal trace of who-told-us-about-whom.
- **Hardcoding pod_id → port** — even with the range documented, wiring the
  specific assignment defeats the exercise's autonomous-discovery framing.

### Query receipts — one numerical row per HTTP call

Every HTTP request the mapper makes is recorded as a row in a top-level
`queries` array in `map.json`. The schema is numerical where possible, so
the reporter (and any downstream querying) can filter/sort/aggregate cheaply:

```jsonc
{
  "query_id": 17,
  "host_service": "rover",
  "target_service": "forge",
  "endpoint": "/info",
  "target_url": "http://forge:3010/info",
  "start_time": 1734567890123,   // epoch ms
  "end_time":   1734567890156,   // epoch ms
  "latency_ms": 33,              // computed; stored for query-time convenience
  "status_code": 200,
  "response_size_bytes": 512,
  "has_response": true,
  "discovery_context": "artemis.supplies"
}
```

Design notes:

- **Epoch ms, not ISO strings.** Numeric fields index and filter better;
  human-readable is a rendering concern, not storage.
- **Latency as a calculated-and-stored field.** Derivable from
  `end_time - start_time`, but storing it avoids re-computing at every query
  and keeps downstream filters simple (`latency_ms > 1000` is immediate).
- **`has_response`** distinguishes "200 with empty body" from "non-2xx," and
  separates "network failure" from "explicit 404" (both valid signals about
  the pod surface).
- **`discovery_context`** names the source of the query — the edge that
  caused us to reach here. Makes the discovery trace reconstructible from
  the query log alone.

### Discovery trace as a first-class map output

Alongside the flat query log, `map.json` carries a `discovery_trace`: one
record per pod, capturing how we learned about it, which query resolved its
URL, and which failed probe attempts preceded the successful one. This is
the artifact that backs up "the agent truly discovered" during grading — it's
directly inspectable, and it's derived purely from `queries` + the crawl
order, so nothing in it can drift from what actually happened on the wire.

## 7. Edges as evidence — reconciliation as a first-class map output

The `candidate/README.md` asks one question explicitly: *"Does what pods say
they supply match what other pods say they depend on?"* That's not a report
question. It's a **map question**. The map shouldn't render the dependency
graph as if every claim were ground truth — it should render each edge with
the evidence behind it and flag the ones that don't reconcile.

### Edge shape

Every edge in `map.json` carries:

- `claimed_by`: which side(s) asserted this edge — `["upstream"]`,
  `["downstream"]`, or `["upstream", "downstream"]` when reciprocated.
- `log_evidence`: references (by timeline entry id) to logs/comms from either
  endpoint that corroborate the relationship — e.g. Prometheus logs
  "transferred coolant feed to Forge", Forge logs "received coolant supply",
  comms messages between the two.
- `status`: a categorical summary derived from the above.
  - `reciprocated` — both sides claim it.
  - `reciprocated_with_logs` — both sides claim it *and* logs corroborate.
  - `single_sided` — only one side claims it, no log evidence.
  - `log_only` — neither side currently claims it, but historical logs/comms
    reference the relationship (likely a decommissioned edge).
  - `disputed` — conflicting claims (different resource, different direction,
    or mutually exclusive statements).

No floats, no made-up weights. Just evidence count and a categorical
reconciliation status. Any consumer can see exactly why an edge is flagged.

### Implementation refinement — supply-level SPOFs on top of topology

During implementation, the topology-level `articulation_points` metric came
back **empty** on the live colony — every pod has alternate connectivity
paths. That's a real signal, but it leaves the "where are the SPOFs?"
question unanswered. We added two supply-level metrics in `graph.py` to
fill the gap:

- `unique_supplier_edges`: edges where the consumer has exactly one
  supplier for that resource (no redundancy).
- `critical_unique_edges`: subset where the downstream side marks
  `criticality=high` (the strongest SPOFs — sole source of a critical
  resource).
- `spof_pods`: pods that supply at least one critical-unique edge.

The criticality field comes from `/dependencies` entries (the consumer
declares how critical the dependency is). Both metrics are deterministic,
explainable, and complement the topology view rather than replacing it.
The report surfaces both side-by-side: "no topological SPOFs *but* 6
supply-level SPOFs."

### Two passes — deterministic first, inference only on residuals

1. **Deterministic pass.** After BFS, walk every `(supplier, resource,
   consumer)` claim from `/supplies` and every `(consumer, resource,
   supplier)` claim from `/dependencies`. Match them; any unmatched claim is
   `single_sided`. Then scan log/comms text for each unmatched edge — if the
   other endpoint's logs mention supplying or receiving that resource, add
   the evidence reference and upgrade the status. This is regex-and-substring
   work: cheap, explainable, reproducible.

2. **Inference pass — only on residuals.** Any edge still in `single_sided`,
   `log_only`, or `disputed` after step 1 gets handed to the LLM at *report
   time*, not map time. The LLM's job is narrative: *"Vault claimed to
   supply water backup but no other pod acknowledges it; logs reference a
   2093 directive decommissioning those reserves. Plausible explanation:
   the edge is legacy."* The map records the uncertainty; the report
   explains it. Inference doesn't get to mutate the edge.

This keeps the map reproducible on re-run, and every reported finding can
be traced back to specific evidence that lives in the map.

### HTTP-level pivot — the safety-net case

One case where mapping really does need a pivot to a new HTTP call: a log
or comms entry names a `pod_id` that our BFS didn't reach. Realistically
this never fires for this colony (gateway → Artemis → BFS covers all 12),
but including it is the one place "autonomous discovery" has concrete
meaning beyond "follow the given pointer." Budget: one sweep at the end of
the deterministic pass, nothing recursive, and every resulting query shows
up in the `queries` log like any other.

## 8. Phase 2 input — structured buckets and unstructured inference are complements

The open question had been: do we want an LLM "notable observations"
section, or keep the report disciplined to the four questions in the brief?

We landed on **both, as complements**:

- **Deterministic buckets** (directive IDs, resources, pod-mentions,
  reconciliation residuals, graph metrics, query receipts) give the LLM a
  grounded substrate it can point at. *"As seen in `directive:2093-089`,
  which appears in 4 pods' logs..."* is a citation, not a guess.
- **LLM unstructured takeaways** catch the things deterministic bucketing
  can't: recurring *themes* across paraphrased events, tone shifts in
  comms, implicit narratives like *"engineers kept routing around Vault for
  months before the decommissioning was formalized."* This is prose-native
  work.

Neither output fails silently. If deterministic buckets are thin, the report
says "the facet-level findings were sparse; the LLM observations below are
the main signal." If the LLM hallucinates, the facet citations are right
there to check against. They keep each other honest.

The phase-2 input packet is therefore:

```
{
  graph_metrics,
  reconciliation_residuals,
  facet_indexes,
  unified_timeline,
  query_receipts     // for "the agent's own report" — crawl self-assessment
}
```

Not raw logs. Not raw HTTP responses. A structured map whose schema was
designed for exactly the analysis the report has to produce.

## 9. Out of scope — system design questions for later

This is a living list of questions that **don't need an answer for the
take-home** but would be the next things to work through if this system
graduated to production or had to handle a real colony's worth of data.
They're captured here so the interview conversation can go wide without
them cluttering the implementation conversation.

A recurring theme across every item below: **the deterministic map is the
durable ground truth; LLM insights are derived views on top of it.** That
layering is what makes most of these answers tractable — you scale the
deterministic pipeline, and the LLM layer inherits the result.

### Scale — what breaks first as data grows?

- **Log volume.** Current design reads every pod's full log into memory. At
  ~100 entries/pod, trivial. At terabytes: we need streaming ingest,
  log-segment manifests, per-segment entity extraction, and cursor-based
  pod APIs instead of "GET /logs returns everything."
- **RAM ceiling in phase 2.** Today the whole map fits in a single LLM
  context with room to spare. At scale: the LLM call itself becomes the
  binding constraint long before Python's memory does, which pushes the
  design toward per-facet narration (one call per directive / per resource)
  with only the relevant slice of the unified timeline in context.
- **Query receipts table size.** 12 pods × ~6 endpoints × small probe
  overhead = dozens of rows today. At 10K pods and hourly re-crawls, query
  receipts want Parquet / columnar storage, not JSON.

### Processing pipelines — when do we stop running it as one script?

- **Map/reduce structure.** Per-pod entity extraction is embarrassingly
  parallel: it's a *map*. Facet-index assembly is a *reduce*. The current
  sequential implementation is a small-data collapse of that structure;
  formalizing it is the first move when per-pod data stops fitting in RAM.
- **Stream vs. batch.** If pods emit logs continuously rather than serving
  a static corpus, mapping becomes an incremental indexer: new entries
  flow in, entity extraction runs on them only, facet indexes get appended.
  This is where the deterministic-pipeline investment pays off — the delta
  is small and local.
- **Orchestration.** A single shell script calling Python is fine at this
  scale. At scale you want a DAG (airflow / dagster / prefect / temporal)
  so map/reduce stages can be retried, sharded, and observed
  independently.

### Incremental updates — what has to be recomputed when data changes?

This is where the deterministic pipeline earns its keep. The question
isn't "how do we rebuild from scratch faster" — it's "what *doesn't* have
to be rebuilt at all."

- **New logs on an existing pod.** Only that pod's entity extraction runs;
  the affected facet indexes get new entries appended; graph edges are
  untouched *unless* a new log references a previously-unknown pod or
  upgrades a `single_sided` edge to `log_only`. The reconciliation pass
  runs only on edges whose evidence set changed.
- **New pod discovered.** Its `/info`, `/supplies`, `/dependencies` are
  resolved; reconciliation runs only on edges touching the new pod and on
  pre-existing single-sided edges whose counterparty just appeared. No
  global re-index.
- **New edge appears (supplies claim added mid-life).** Local: affects
  reconciliation of that one edge; may change in/out-degree for two pods;
  graph metrics recompute cheaply (they're local).
- **Topology change (pod decommissioned).** Reconciliation residuals
  shift; cached LLM insights that referenced the removed pod need
  re-synthesis. Cache invalidation here is keyed by the hash of the
  deterministic substrate the insight was derived from — if the
  substrate's hash changes, the insight is stale.

### Hot paths and pre-computation

- **The facet indexes already *are* pre-computed clusters.** `by_directive`,
  `by_resource`, `by_pod_mentioned` exist so the reporter (or any other
  consumer) never has to rescan the timeline for common slices.
- **Likely hot paths at production scale.** "What does pod X supply?"
  (already O(1) from the graph), "what logs mention resource Y?" (O(1)
  from facets), "show me every edge in reconciliation state Z" (O(1) from
  the edges collection). The schema already serves these.
- **Time-windowed buckets.** At 2.5 years of logs, the unified timeline is
  fine. At 20 years or continuous ingestion, we'd pre-compute
  month/quarter buckets so the reporter can zoom without re-scanning.
- **LLM insight cache.** Keyed by `hash(deterministic inputs)`. If the
  substrate hasn't changed, the prose hasn't either — skip the call.

### Discovery at scale

- **Probe load.** 12 pods × a 12-port range ≈ low hundreds of queries in
  the worst case today, all on a local Docker network. At 10K pods across
  a real subnet, probe-per-pod stops being free; we'd introduce a proper
  service registry (Consul / etcd / DNS SRV) and fall back to probing
  only for discoveries not yet in the registry.
- **Parallel BFS.** Current BFS is implicitly serial. At scale, a worker
  pool with bounded concurrency and per-host rate limits is the shape.
- **Unknown endpoint kinds.** Today the six endpoints are hard-coded into
  the crawler. At scale, pods advertise their own schema (OpenAPI on
  `/openapi.json`, or a `GET /` that enumerates routes), and the crawler
  reads that descriptor rather than assuming the surface.

### Resilience and consistency

- **Snapshot semantics.** For this exercise, the pod data is static. In
  production, a pod's `/supplies` might change between when we read it
  and when we read the counterparty's `/dependencies`. We'd capture an
  `ETag` / version per response, detect intra-crawl drift, and decide
  per-call whether to re-query or tag the edge as mid-flight.
- **Partial-failure SLA.** One pod 500-ing shouldn't invalidate the whole
  map. Current behavior: record the failure in `queries`, proceed. At
  scale: define an explicit coverage threshold below which the map is
  flagged incomplete, and an alerting path for systematic failure modes
  (auth drift, network partition).

### Extensibility

- **New pod types / roles.** Role-specific analysis (e.g. "power pods get
  a redundancy check, medical pods get a capacity check") becomes a
  plugin surface on the reporter side. The map stays generic.
- **Schema evolution of `map.json`.** Versioned schema with explicit
  `map_version`; reporters are backward-compatible across a few versions;
  old maps are re-readable indefinitely for historical comparison.
- **Alternative reporters.** Because phase 2 is a pure function of the
  map, we can run N reporters (executive summary, engineering detail,
  compliance audit) against the same artifact. The LLM calls get
  parameterized by audience, not by the data pipeline.

### Why the deterministic / LLM split scales

Every scaling question above gets easier when the map is deterministic
ground truth and insights are a derived layer:

- **Cacheable.** Deterministic outputs hash-key cleanly; LLM outputs
  inherit that key.
- **Diffable.** Two crawls produce comparable artifacts. "What changed
  since yesterday's map?" is a structural diff, not a re-narration.
- **Incrementally updatable.** Local data changes trigger local
  recompute. LLM insights are regenerated only for the affected slices.
- **Failure-containable.** If the LLM layer is degraded or absent, the
  map still ships and a template-driven report still reads as useful,
  just less colorful. If the deterministic pipeline fails, no amount of
  LLM cleverness compensates — which is the right way around.
- **Auditable.** Every LLM claim traces back to facets, which trace back
  to raw responses in `queries`. That's a compliance property at scale,
  not just a nice-to-have.

### Living list

Append to this section as new out-of-scope questions surface during
design and implementation. Keep entries one or two paragraphs; if a
question gets answered, move it up into the main deliberation or fold it
into `approach.md`.

## 10. Graceful failure + resume — point-in-time recovery as a resource discipline

The question: if the crawl crashes on pod 8 of 12, or the reporter errors
on section 3 of 5, do we lose everything and start over? Until now, yes
— `map.json` and `report.md` were written only at end-of-phase.

**Decision:** add a checkpoint layer under `/rover/output/.checkpoint/`
so both phases are resumable from the last successful atomic unit. The
metadata trace we already capture (query receipts) makes this natural to
retrofit — the checkpoint extends the same audit-trail philosophy to
work products, not just HTTP calls.

### Motivation — it's about resources, not just convenience

The framing matters: this isn't graceful failure as a UX nicety, it's
**point-in-time recovery as a resource discipline**. Both phases cost
real resources that we should not spend twice on the same work:

- **Crawl costs:** HTTP round-trips against the colony. Cheap here at 12
  pods, less cheap against real production endpoints, and either way the
  pod-service has been hit by every prior request we're about to throw
  away on a restart.
- **Reporter costs:** LLM tokens. Each section call is input-cached but
  the output tokens are paid fresh. A restart from scratch is a direct
  re-bill of everything already synthesized.

Retrying only the failed unit (one pod, one section) keeps the spend
bounded on retries, and makes iteration during development cheap — fix
a bug in the reconciliation pass, re-run, pay only for what needs
recomputing.

### Atomic unit — per pod (mapping) and per section (reporting)

The granularity question is the main trade. Options considered:

- **Per HTTP endpoint** (finest) — every `/logs`, `/comms`, etc. is its
  own checkpoint. Recovers the most work on crash, but multiplies file
  count and complicates pod-level assembly.
- **Per pod** (picked, mapping) — write the pod's file once all six
  endpoints have been fetched. A crash mid-pod loses at most six HTTP
  calls' worth of work; recovery reruns the whole pod cleanly.
- **Per section** (picked, reporting) — write each rendered section
  markdown once the LLM call succeeds. A failed section is retried
  independently; completed sections are immutable.

Per-pod and per-section are the natural *product* boundaries — the
intermediate HTTP responses are building blocks, but a "pod" or a
"section" is a thing we'd be willing to commit to disk as final.

### Checkpoint layout

```
/rover/output/.checkpoint/
  mapping/
    pods/
      {pod_id}.json       # full per-pod crawl result
    queries.jsonl         # append-only HTTP receipt log
    errors.jsonl          # append-only error log
  reporting/
    sections/
      {section}.md        # rendered markdown per section
    errors.jsonl
```

- **Atomic writes.** All per-unit files use write-temp + rename. A
  partial file never appears as a valid checkpoint.
- **Append-only logs.** `queries.jsonl` and `errors.jsonl` survive
  crashes trivially — the last line may be torn, but JSONL tolerates
  that (skip any line that fails to parse, log, continue).
- **No frontier file.** The BFS frontier is derivable from completed
  pods' `/supplies` and `/dependencies` minus already-completed pods,
  so we don't need an extra piece of state to resume. Simpler, fewer
  places for state to drift.

### Resume semantics

On start, each phase checks its checkpoint directory:

- **Mapping.** Load all `pods/*.json` into `completed`. Start BFS from
  the gateway as usual, but skip any `pod_id` already in `completed` —
  use its cached data instead of re-fetching. Checkpoint each pod as
  it finishes. When the queue drains, assemble `map.json` from the
  union of completed pods + appended queries + appended errors, then
  clean up the checkpoint directory.
- **Reporting.** For each section function in the report, check if
  `sections/{section}.md` exists. If yes, read it; if no, compute and
  checkpoint it. When all sections are present, compose `report.md` and
  clean up.

### Error log format

`errors.jsonl` — one structured record per exception:

```jsonc
{
  "timestamp": 1734567890123,
  "phase": "mapping",
  "stage": "crawl_pod",
  "pod_id": "medica",
  "endpoint": "/logs",
  "query_id": 23,
  "exception_type": "httpx.ReadTimeout",
  "message": "Read timeout after 5s",
  "resumable": true
}
```

`resumable` is the key field for resume policy: `true` → the phase can
continue past this error (e.g., one pod failed, keep crawling the
others); `false` → the phase is fatal and won't be retried on resume
(e.g., gateway unreachable, malformed seed).

### Fresh run vs. resume

Default behavior: if a checkpoint directory exists, resume. If env
`RESUME=false` is set, blow away the checkpoint before starting. The
harness re-spawns the script on each `POST /map`, so the script is
responsible for its own resume decision — the harness doesn't need to
know.

### What this costs

A `checkpoint.py` module, maybe ~100 lines. Per-pod and per-section
integration points in `crawl.py` and `reporter.py` are small wrappers.
Landed in M0.4, M1.3, M4.1, M5.2, M5.3 (see `sprint-plan.md`).

### What this doesn't address

- **In-pod recovery.** If we crash after fetching 3 of 6 endpoints, the
  whole pod reruns on resume. Acceptable — pods are small, endpoints
  are cheap, and saving half-pods would double the file count.
- **Non-idempotent side effects.** None in this system (all GETs), so
  this isn't a concern here. For a future version that wrote anywhere,
  we'd need an idempotency key per unit.
- **Partial LLM responses.** Not handled — a section call that returns
  half a response counts as a failure and is retried whole. The LLM is
  already the least deterministic piece; no point trying to patch
  partial outputs.

## 11. Optimization targets per stage

Surfaced while starting implementation: the two stages are optimized for
different things, and that should shape concrete knob choices (retries,
temperatures, cache keys, validation depth).

- **Stage 1 (mapping) — accuracy and explainability.**
  Every claim in `map.json` should be traceable to a specific HTTP
  response (via `queries[query_id]`) or a specific log/comms entry (via
  `timeline[entry_id]`). No inference at this stage. When in doubt:
  add another citation field, not another heuristic. Retries favor
  correctness over speed (transient errors get retried; ambiguous
  responses are recorded rather than normalized).

- **Stage 2 (reporting) — reproducibility and interpretation.**
  - **Reproducibility:** LLM calls use a pinned model ID, pinned
    `max_tokens`, and a fixed substrate. (Update during implementation:
    Opus 4.7 deprecated the `temperature` parameter — the API rejects it
    with a 400. The model now manages its own sampling, so we don't pass
    a temperature value at all. This trades the explicit knob for trust
    in the model's defaults; reproducibility comes from inputs, not
    sampling configuration.)
  - **Interpretation:** this is where the LLM is asked to do work that
    deterministic code can't — cross-pod thematic synthesis, narrative
    over the timeline, plausible-cause hypotheses for reconciliation
    residuals. Section prompts are written to *require* citations into
    the substrate, so interpretation is bounded by evidence.

The two targets aren't in tension — they're the natural priorities of
their respective stages. Stage 1's job is to produce ground truth;
stage 2's job is to produce a useful reading of it. The knobs fall out
from that.

## 12. Citation grounding — prompt hard rule + runtime audit

The first end-to-end LLM-narrated report was substantively strong but
surfaced three defects worth fixing before declaring stage 2 done:

1. SPOFs section truncated mid-sentence — `max_tokens=1600` was too
   tight for six per-pod paragraphs.
2. The LLM emitted its own markdown headings inside section bodies,
   breaking the caller's h1/h2 hierarchy.
3. At least one `[e:...]` citation (`helios→nexus:electrical_power`)
   could not be matched to any row in the authoritative edges list —
   almost certainly a hallucinated triple.

(1) and (2) are tuning. (3) is the interesting one, because it's the
failure mode where LLM output *looks* grounded — the citation syntax is
correct, the entities are plausible — but the underlying triple is
invented. Two complementary defenses:

**Prompt-side hard rule (preventive).** `CITATION_RULES` in `llm.py`
now states explicitly that `[e:s→c:r]` triples must match a row in the
substrate's Edges section verbatim. Three few-shot examples accompany
it: a good citation, a ❌ hallucinated-edge example with the ✅ rewrite
next to it, and a graceful no-edge case where prose + `[t:N]` is the
only valid form. Few-shots outperform abstract rules on this failure
mode: they show the model *what not to do* in the shape it actually
drifts into.

**Runtime audit (corrective).** `agent/audit.py` parses the rendered
report for all three citation kinds and validates each against the
authoritative `map.json`: timeline ids, directive set, and edge
triples. It runs automatically after `run_report` and prints a
summary; `make audit` re-runs it on the existing artifact. The audit
is informational — it doesn't mutate the report — but it makes
hallucinations visible immediately rather than waiting for a reader
to spot them.

**Interaction with opus-4.7 temperature deprecation.** Since we lost
`temperature=0` as an explicit reproducibility lever (§11), *grounding
is now the primary remaining control on output reliability*. The audit
also doubles as a quality signal across runs — two reports with the
same "0 invalid" audit are empirically equivalent for downstream use
even if the prose phrasing drifts slightly.
