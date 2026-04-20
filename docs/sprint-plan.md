# Project Selene — Sprint Plan

Concrete, ordered technical TODOs to build the system described in
`approach.md` and debated in `deliberation.md`. Organized as milestones
(M0–M6), each with ticket-level scope so work can be picked up one ticket
at a time and reviewed independently.

## Milestone overview

| ID | Milestone | Focus |
|----|-----------|-------|
| M0 | Scaffold | Dockerfile, shell stubs, checkpoint module, smoke test |
| M1 | Crawl mechanics | HTTP client with receipts, port-probe resolver, BFS with per-pod checkpoint |
| M2 | Derived data | Entity extraction, unified timeline, facet indexes |
| M3 | Graph + reconciliation | Edges as evidence, graph metrics |
| M4 | Map assembly | Assemble + write `map.json` from checkpoint; cleanup on success |
| M5 | Reporter | LLM client, per-section checkpointed synthesis, markdown render |
| M6 | Tests + polish | Unit tests (incl. resume), smoke, writeup |
| M7 | Report quality | Prompt grounding + few-shot examples + citation audit |

Cut-line order if scope needs to tighten: M6.3 writeup → M6.1-timeline
tests → M5.2-observations section → M3.3-transitive → M2.3-facets. See
"Cut lines" at the bottom.

## Target module layout (reference)

```
rover/
  agent/
    __init__.py
    http.py           # httpx client + query-receipt wrapping
    discovery.py      # gateway parse + port-probe pod_id resolver
    crawl.py          # BFS traversal, per-pod endpoint sweep
    checkpoint.py     # atomic per-unit state, append-only query/error logs
    entities.py       # extract directive IDs, resources, pod mentions
    timeline.py       # merge logs + comms into unified timeline
    facets.py         # by_directive / by_resource / by_pod_mentioned
    graph.py          # edges, reconciliation, metrics (degree, APs)
    llm.py            # anthropic client with prompt caching
    reporter.py       # per-section synthesis (LLM + deterministic)
    render.py         # markdown assembly
    main.py           # `python -m agent map` / `python -m agent report`
  run_mapping.sh      # exec python -m agent map
  run_reporting.sh    # exec python -m agent report
  Dockerfile
  tests/
    test_entities.py
    test_graph.py
    test_timeline.py
    test_checkpoint.py
```

## M0 — Scaffold

### M0.1 — Extend `rover/Dockerfile` with Python deps
- **Files:** `rover/Dockerfile`
- **Scope:** Add `RUN pip install httpx==0.27.* anthropic==0.40.* networkx==3.*`. Keep `python:3.12-slim` base; keep existing server.py untouched.
- **Acceptance:** `docker compose build rover` succeeds; `docker compose exec rover python -c "import httpx, anthropic, networkx"` exits 0.
- **Deps:** —

### M0.2 — Wire `run_mapping.sh` / `run_reporting.sh` to Python entrypoints
- **Files:** `rover/run_mapping.sh`, `rover/run_reporting.sh`
- **Scope:** Each script `exec`s `python -m agent map` / `python -m agent report` from `/rover`. Pass through `GATEWAY_URL` and `LLM_API_KEY`. Exit non-zero on Python failure.
- **Acceptance:** Scripts fail cleanly (exit 1 with message) until `agent/main.py` exists; no Python import errors once it does.
- **Deps:** M0.1

### M0.3 — Stack smoke test
- **Scope:** `LLM_API_KEY=... docker compose up --build -d`. Verify `curl localhost:3000`, `curl localhost:3002/info`, `curl localhost:8080/get-map` (expect 404 not-started). Confirm rover container is on `selene-net`.
- **Acceptance:** All four curls return expected responses.
- **Deps:** M0.1, M0.2

### M0.4 — `agent/checkpoint.py`: point-in-time recovery primitive
- **Files:** `rover/agent/checkpoint.py`
- **Scope:** Two classes, `MappingCheckpoint` and `ReportingCheckpoint`, over `/rover/output/.checkpoint/{mapping,reporting}/`. Shared primitives:
  - `save_unit(name, data)` / `load_unit(name)` — atomic write (temp + rename) and read of per-pod JSON (mapping) or per-section markdown (reporting).
  - `load_all_units() -> dict` — returns everything already on disk so the caller can skip already-done work.
  - `append_query(receipt)` / `append_error(err)` — append JSONL, flush, fsync.
  - `load_queries()` / `load_errors()` — tolerant JSONL reader (skip torn last line, log and continue).
  - `is_fresh_start() -> bool` — true if checkpoint dir is empty or missing.
  - `cleanup()` — remove checkpoint dir on successful phase completion.
  - Env `RESUME=false` → call `cleanup()` before start of phase (opt-in fresh run).
  - Error record schema matches `deliberation.md §10`: `{timestamp, phase, stage, pod_id?, endpoint?, query_id?, exception_type, message, resumable}`.
- **Acceptance:** Unit-testable — can create a checkpoint, save units, simulate crash (process exit), reopen, see completed units + queries + errors intact.
- **Deps:** M0.1

## M1 — Crawl mechanics

### M1.1 — `agent/http.py`: HTTP client with query receipts
- **Files:** `rover/agent/http.py`
- **Scope:** `Client` class wrapping `httpx.Client` (timeout=5s, max_retries=2 on transient errors). `get(url, context)` returns `(parsed_json_or_none, receipt)` where `receipt` has:
  - `query_id` (int, monotonic), `host_service="rover"`, `target_service`, `endpoint`, `target_url`
  - `start_time`, `end_time` (epoch ms), `latency_ms` (computed and stored)
  - `status_code`, `response_size_bytes`, `has_response` (bool)
  - `discovery_context` (caller-provided, e.g. `"artemis.supplies"`)
  - 404 returns `(None, receipt_with_404)` without raising.
- **Acceptance:** Unit-testable — can drive it against a local httpx mock and verify receipt fields. Numeric `start_time` / `end_time` / `latency_ms`.
- **Deps:** M0

### M1.2 — `agent/discovery.py`: gateway parse + port-probe resolver
- **Files:** `rover/agent/discovery.py`
- **Scope:**
  - `parse_gateway(gateway_url, client)` → hit gateway `/`, extract `entrypoint.pod` and `entrypoint.url`. Return `{seed_pod_id, seed_url}`.
  - `resolve_pod(pod_id, client)` → probe `http://{pod_id}:{port}/info` for `port in 3001..3012` until 200 with matching `id`. Return `{pod_id, resolved_url, resolution_query_ids}`. Raise `PodUnreachable` if no port responds.
  - In-process cache: don't re-resolve a pod we've already resolved.
- **Acceptance:** `resolve_pod("artemis")` returns `http://artemis:3002` inside the container; `resolve_pod("bogus")` raises; every probe attempt produces a query receipt.
- **Deps:** M1.1

### M1.3 — `agent/crawl.py`: BFS per-pod sweep with checkpoint
- **Files:** `rover/agent/crawl.py`
- **Scope:**
  - On entry, open `MappingCheckpoint`; `completed = ckpt.load_all_units()`. If `completed` is non-empty, log "resuming from N pods" and skip their HTTP work.
  - Queue seeded with `(seed_pod_id, seed_url)` from gateway parse.
  - For each pod:
    - If `pod_id in completed`, reuse cached data; continue.
    - Otherwise, fetch `/info`, `/status`, `/dependencies`, `/supplies`, `/logs`, `/comms` (404 on comms = absent, first-class). `ckpt.append_query(receipt)` per HTTP call.
    - On success: `ckpt.save_unit(pod_id, pod_data)` atomically.
    - On per-pod exception: `ckpt.append_error({..., resumable: True})`, record failure in discovery trace, continue to next pod. Fatal errors (gateway unreachable, etc.) → `resumable: False` and re-raise.
  - Extract new `pod_id`s from `/supplies.*.pod_id` and `/dependencies.*.pod_id`; resolve via `discovery.resolve_pod`; enqueue unseen ones.
  - Output on return: dict `pod_id -> {info, status, dependencies, supplies, logs, comms?}`; plus `queries` list (loaded from checkpoint); plus `errors` list (loaded from checkpoint); plus `discovery_trace`. `map.json` assembly happens in M4.1, not here.
- **Acceptance:**
  - First run against the live stack: `crawl()` returns all 12 pods; `discovery_trace` has 12 entries; every HTTP call shows up in `queries.jsonl`.
  - **Resume test:** after partial crawl (simulate by stopping process mid-BFS), re-run and confirm no pod already in `completed` triggers a new HTTP call — verify via query count deltas.
- **Deps:** M1.1, M1.2, M0.4

## M2 — Derived data

### M2.1 — `agent/entities.py`: entity extraction
- **Files:** `rover/agent/entities.py`
- **Scope:** Given a text string + a set of known `pod_id`s (populated after M1.3), return `{pod_refs: [...], directive_ids: [...], resources: [...]}`. Extraction rules:
  - `pod_refs`: case-insensitive substring match against known pod list.
  - `directive_ids`: regex `\b\d{4}-\d{3}\b`.
  - `resources`: dictionary match against the union of `resource` strings seen across all `/supplies` and `/dependencies` responses.
- **Acceptance:** Unit tests: given sample log entries, extraction finds the right tokens; no false positives on normal prose.
- **Deps:** M1.3 (needs pod list + resource vocab from crawl)

### M2.2 — `agent/timeline.py`: unified timeline
- **Files:** `rover/agent/timeline.py`
- **Scope:** Merge all pods' `/logs` + `/comms` into a single sorted list. Each entry:
  ```
  {
    id: int,            # sequential, stable for cross-references
    source_pod: str,
    kind: "log" | "comm",
    timestamp: int,     # epoch ms
    timestamp_iso: str, # convenience, not primary
    text: str,          # log.event + log.detail, or comm.content
    from?: str,         # comms only
    to?: str,           # comms only
    raw: {...},         # original object
    entities: {pod_refs, directive_ids, resources}
  }
  ```
  Sort by `timestamp` ascending; assign sequential `id`s after sort.
- **Acceptance:** Timeline contains 94 log + 16 comms entries (110 total given current fixture); sorted monotonically; every entry has entity tags.
- **Deps:** M2.1

### M2.3 — `agent/facets.py`: facet indexes
- **Files:** `rover/agent/facets.py`
- **Scope:** Three inverted indexes built from the timeline's entity tags:
  - `by_directive: {directive_id: [entry_ids]}`
  - `by_resource: {resource: [entry_ids]}`
  - `by_pod_mentioned: {pod_id: [entry_ids]}`
- **Acceptance:** For at least one known directive ID, `by_directive` lists entries from ≥2 different pods (corroboration of the shared-entity hypothesis).
- **Deps:** M2.2

## M3 — Graph + reconciliation

### M3.1 — `agent/graph.py`: raw edge construction
- **Files:** `rover/agent/graph.py`
- **Scope:** For each pod, walk `/supplies` (yields `(pod, consumer, resource)`) and `/dependencies` (yields `(pod, supplier, resource)`). Collapse into a single directed edge set keyed by `(supplier, consumer, resource)`. Track `claimed_by` per edge: `"upstream"`, `"downstream"`, or both.
- **Acceptance:** Every unique (supplier, consumer, resource) triple from fixture data appears exactly once, with correct `claimed_by`.
- **Deps:** M1.3

### M3.2 — Reconciliation pass (deterministic)
- **Files:** `rover/agent/graph.py` (same module)
- **Scope:** For each edge:
  - If both sides claim: `status = reciprocated`.
  - If one side claims, scan both endpoints' timeline entries for mentions of the counterparty + resource. Any hits → `log_evidence: [entry_ids]` and upgrade:
    - both-claim + log hits → `reciprocated_with_logs`
    - one-claim + log hits → (still single-sided on claims, but has evidence — mark `single_sided` with populated `log_evidence`)
    - no-claim + log hits → `log_only` (new edge inferred purely from logs)
  - Conflicting claims → `disputed`.
- **Acceptance:** Unit test with synthetic edges + logs exercises every status class. On live data, report reveals at least one non-reciprocated edge (expected per fixture).
- **Deps:** M3.1, M2.2

### M3.3 — Graph metrics
- **Files:** `rover/agent/graph.py` (same module)
- **Scope:** Using `networkx`:
  - in-degree / out-degree per pod
  - articulation points (SPOF candidates) over the undirected projection
  - transitive collapse: for each pod, the set of pods that become unreachable from a designated root (e.g. artemis) if it's removed
- **Acceptance:** Metrics populate for all 12 pods; articulation points list is non-empty on fixture data (colony has at least one SPOF).
- **Deps:** M3.1

## M4 — Map assembly

### M4.1 — `agent/main.py map`: assemble from checkpoint + cleanup
- **Files:** `rover/agent/main.py`
- **Scope:** `python -m agent map`:
  1. Run `crawl()` (M1.3) — which reads/writes the checkpoint itself.
  2. After crawl returns: run entity extraction (M2.1), timeline merge (M2.2), facet indexing (M2.3), graph build + reconciliation (M3.1–M3.3). All pure functions of the crawl result.
  3. Assemble `map.json` with keys:
     ```
     {
       "meta": { "generated_at": epoch_ms, "map_version": "1", "status": "complete"|"partial", "coverage": {...} },
       "gateway_response": {...},
       "pods": { pod_id: { info, status, dependencies, supplies, logs, comms? } },
       "edges": [ { supplier, consumer, resource, claimed_by, log_evidence, status } ],
       "graph_metrics": { degree: {...}, articulation_points: [...], transitive_collapse: {...} },
       "timeline": [ ... ],
       "facets": { by_directive, by_resource, by_pod_mentioned },
       "discovery_trace": [ ... ],
       "queries": [ ... ],
       "errors": [ ... ]     // loaded from checkpoint; empty list on clean runs
     }
     ```
  4. Write atomically to `/rover/output/map.json` (temp + rename).
  5. On success (all 12 pods present OR no unrecoverable errors): `ckpt.cleanup()` — removes `.checkpoint/mapping/`.
  6. `meta.status = "partial"` if any pod failed but we still produced a map.
  7. Exit 0 if a valid `map.json` was written (even partial); exit 1 only if assembly itself failed (which leaves the checkpoint intact for the next run).
- **Acceptance:**
  - `curl -X POST localhost:8080/map` → poll `/get-map` until 200; JSON body validates against the shape above; all 12 pods present; `queries` non-empty; `edges` non-empty; `.checkpoint/mapping/` does **not** exist post-success.
  - **Resume test:** after crashing crawl mid-way and re-running, assembly produces the same `map.json` as a clean run — verify structural equivalence (sorted deep diff).
- **Deps:** M3.3, M2.3, M0.4

## M5 — Reporter

### M5.1 — `agent/llm.py`: Anthropic client with prompt caching
- **Files:** `rover/agent/llm.py`
- **Scope:** Thin wrapper around `anthropic.Anthropic`:
  - `Reporter.__init__(map_dict)` — stores the map, pre-computes a "substrate" string to cache (graph metrics + edges + facet indexes + timeline summary).
  - `Reporter.synthesize(prompt, max_tokens=1024)` — calls `messages.create` with the substrate as cached system prefix (`cache_control: {type: "ephemeral"}`), the section prompt as the user message. Model: `claude-opus-4-7` (the latest).
  - Graceful degradation: on API error, return a sentinel like `{"error": "...", "fallback": True}`; caller renders a template instead.
- **Acceptance:** A smoke call returns text; second call to same instance shows `cache_read_input_tokens > 0` in response metadata.
- **Deps:** M4.1

### M5.2 — `agent/reporter.py`: per-section checkpointed synthesis
- **Files:** `rover/agent/reporter.py`
- **Scope:** One function per report section, each returning `{heading, body_markdown, citations}`. Sections:
  - `critical_pods(map)` — deterministic: top-N by in-degree + transitive collapse size. LLM: one paragraph explaining *why* these are critical, citing specific supply edges.
  - `spofs(map)` — deterministic: articulation points. LLM: one paragraph per AP, describing downstream impact, citing edges.
  - `infra_evolution(map)` — LLM: per-directive narrative, one short paragraph per directive_id that has ≥2 log references, citing timeline entry IDs.
  - `reconciliation(map)` — deterministic table of non-reciprocated edges. LLM: one sentence per flagged edge hypothesizing the cause, grounded in logs.
  - `notable_observations(map)` — LLM: open-ended, 3–5 bullets, each with a citation to a facet or timeline entry. Capped.
- **Checkpoint integration:**
  - Open `ReportingCheckpoint`; `completed = ckpt.load_all_units()`.
  - For each section: if `section_name in completed`, reuse cached markdown (skip the LLM call entirely — **this is where the resource discipline pays off**); otherwise run the section function, then `ckpt.save_unit(section_name, rendered_markdown)` atomically.
  - On per-section LLM failure: `ckpt.append_error({..., resumable: True})`, render a template fallback for that section, checkpoint the fallback so we don't retry it this run but leave the error visible.
- **Acceptance:**
  - Each section function returns non-empty output on the live map; citations resolve to valid IDs.
  - **Resume test:** after completing 3 of 5 sections and crashing, re-run; verify via LLM call count that the 3 completed sections are not re-called.
- **Deps:** M5.1, M0.4

### M5.3 — `agent/render.py` + `main.py report`: compose from checkpoint + cleanup
- **Files:** `rover/agent/render.py`, `rover/agent/main.py` (extend)
- **Scope:**
  - `render.py` concatenates section markdown in order (Executive summary → Critical pods → SPOFs → Infrastructure evolution → Reconciliation → Notable observations → Coverage/appendix) with consistent heading style. Prepends a header with map metadata and (if present) a "partial-run" banner citing any errors loaded from the checkpoint.
  - `python -m agent report`:
    1. Reads `/rover/output/map.json`.
    2. Runs reporter (M5.2) — which reads/writes the reporting checkpoint itself.
    3. Reads all section markdown from the checkpoint, composes `/rover/output/report.md` via atomic write.
    4. On success: `ckpt.cleanup()` — removes `.checkpoint/reporting/`.
    5. Exit 0 if a valid report was written; exit 1 on assembly failure (leaves checkpoint intact).
- **Acceptance:**
  - `curl -X POST localhost:8080/report` → poll `/get-report` until 200; `report.md` is valid markdown, renders in a previewer, has all six sections, every LLM claim has an adjacent citation; `.checkpoint/reporting/` does **not** exist post-success.
  - **Resume test:** crash mid-report, re-run; final `report.md` is structurally identical to a clean run.
- **Deps:** M5.2

## M6 — Tests + polish

### M6.1 — Unit tests for pure functions + checkpoint
- **Files:** `rover/tests/test_entities.py`, `rover/tests/test_graph.py`, `rover/tests/test_timeline.py`, `rover/tests/test_checkpoint.py`
- **Scope:** Test-drive the deterministic pieces against tiny synthetic fixtures:
  - `test_entities`: directive regex, pod-ref substring matcher, resource dictionary match.
  - `test_graph`: edge classification for each of the five reconciliation statuses.
  - `test_timeline`: sort stability, entity-tag attachment, comms vs log `kind` handling.
  - `test_checkpoint`: atomic save/load round-trip, simulated mid-write crash (truncate file, verify load tolerates it), JSONL torn-line skipping, `cleanup()` idempotency, `RESUME=false` reset.
- **Acceptance:** `pytest rover/tests/` passes inside the container.
- **Deps:** M2, M3, M0.4

### M6.2 — End-to-end smoke + resume + failure-mode check
- **Scope:**
  - Clean run: `docker compose up --build -d`; trigger `/map`; trigger `/report`; validate both outputs.
  - **Mid-crawl kill:** trigger `/map`; mid-run, `docker compose exec rover pkill -KILL -f 'agent map'`; re-trigger `/map`; verify via `queries.jsonl` that no pod present in the pre-kill checkpoint is re-fetched.
  - **Mid-report kill:** analogous for `/report`; verify via LLM call count that completed sections are not re-called.
  - **Pod-down degradation:** `docker compose stop medica`; re-trigger `/map`; confirm `map.json` is `partial` with medica's failures in `errors`, not a harness 500.
- **Acceptance:** All three scenarios produce valid output files; resume paths short-circuit the expensive work they're supposed to.
- **Deps:** M5.3

### M6.3 — Candidate writeup
- **Files:** `WRITEUP.md` (new, root)
- **Scope:** One-page writeup per `candidate/README.md:140` — design decisions, what the agent found, what we'd do with more time (link into `deliberation.md §9`).
- **Acceptance:** ~1 page; references concrete findings from the live `report.md`.
- **Deps:** M6.2

## Risks

| Risk | Likelihood | Mitigation |
|------|------------|------------|
| Port-probe resolver is slow (12 × 12 probes) | Low | First-match exit; connect timeout 500ms; in-process cache. |
| LLM call latency × N sections drags report wall-clock | Med | Prompt caching (M5.1); parallelize section calls with `asyncio.gather`. |
| Entity extraction false positives / misses | Med | Unit-test early; start with strict rules, loosen only if recall is thin. |
| Reconciliation logic misclassifies edges | Med | Unit test all five status transitions before live runs. |
| `map.json` schema drift between mapper and reporter | Low | Single Pydantic model (optional stretch) or typed dict constants in `agent/schema.py`. |
| Corrupt checkpoint blocks resume | Low | Atomic writes (temp+rename); JSONL torn-line tolerance; `RESUME=false` escape hatch; `cleanup()` on successful completion so checkpoints don't accumulate across runs. |

## Cut lines (if scope needs to tighten)

In priority order of what gets cut first:

1. **M6.3 writeup** — can be produced after shipping; not blocking.
2. **M6.1 unit tests for timeline** — keep tests for entities + graph, where classification bugs are hardest to eyeball.
3. **M5.2 `notable_observations` section** — the four-question floor is the real deliverable; observations are nice-to-have.
4. **M3.3 transitive collapse** — in-degree alone is a defensible criticality metric; transitive collapse is cherry on top.
5. **M2.3 facet indexes** — the timeline alone is enough for the LLM to work against at this corpus size; facets are for cleaner citations and future scale.

## M7 — Report quality (post-first-run fixes)

Added after the first live LLM-narrated report surfaced three defects:
SPOFs section truncated, LLM-emitted headings inside section bodies, and a
hallucinated `[e:...]` edge citation (`helios→nexus:electrical_power`). See
`deliberation.md` §12 for the framing.

### M7.1 — Tighten prompts + bump spofs budget
- **Files:** `rover/agent/llm.py` (`CITATION_RULES`), `rover/agent/reporter.py`
- **Scope:** Add no-backticks + edge-must-exist rules + 3 few-shot examples (good / ❌ hallucinated-edge / graceful-no-edge) to `CITATION_RULES`. Add a shared `_NO_HEADINGS` constant and append it to every section prompt. Bump `spofs` `max_tokens` from 1600 → 3500 and cap per-pod paragraphs at ~80 words in the prompt.

### M7.2 — `agent/audit.py` citation validator
- **Files:** `rover/agent/audit.py` (new)
- **Scope:** Parse `report.md` for `[t:N]`, `[d:YYYY-NNN]`, and `[e:s→c:r]`; validate each against `map.json`'s timeline ids, directive set, and edge triples. Return `{timeline, directive, edge}` each with `{valid: int, invalid: [sorted refs]}`. Informational — doesn't mutate the report.

### M7.3 — Wire audit into harness + Makefile
- **Files:** `rover/agent/main.py`, `rover/agent/__main__.py`, `Makefile`
- **Scope:** `run_report` runs the audit after writing the report and prints a one-line summary (plus invalid details if any). New `run_audit()` + `python -m agent audit` subcommand. `make report` appends an audit step; new `make audit` target re-runs it standalone.

### M7 verification
- `make rebuild && make clean && make map && make report` — expect the audit line `citations: timeline=X valid / 0 invalid, directive=Y valid / 0 invalid, edge=Z valid / 0 invalid`.
- `grep -nE '^#+ ' /tmp/report.md` returns only the five caller-rendered `## Section` headings.
- SPOFs section reaches the end of the Zephyr paragraph cleanly.
- `make audit` reproduces the same clean result on the written artifact.

## Definition of done (sprint-level)

- [ ] `docker compose up --build -d` brings up the full stack with the rover container built.
- [ ] `curl -X POST localhost:8080/map` → 200 response at `/get-map` with a `map.json` containing all 12 pods, an `edges` array with reconciliation statuses, a `timeline` with 110 entries, `facets`, `graph_metrics`, `discovery_trace`, `queries`, and `errors`.
- [ ] `curl -X POST localhost:8080/report` → 200 response at `/get-report` with a `report.md` answering the four questions from `candidate/README.md` plus notable observations, every LLM claim carrying a citation.
- [ ] `pytest` passes (incl. `test_checkpoint`).
- [ ] **Resume verification:** after killing either phase mid-run, re-triggering completes the phase without re-doing already-checkpointed work (verified by query/LLM-call deltas).
- [ ] **Cleanup verification:** on successful completion, `/rover/output/.checkpoint/` does not exist for the completed phase.
- [ ] `WRITEUP.md` produced.
- [ ] `deliberation.md` updated with any decisions that changed during implementation.
