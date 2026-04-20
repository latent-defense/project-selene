# Project Selene — 10,000 ft Approach

## Context

Project Selene is a take-home exercise. A FastAPI harness in `rover/` runs two
scripts — `run_mapping.sh` and `run_reporting.sh` — as subprocesses, and scores
the candidate on the two files those scripts produce:

- `/rover/output/map.json` — a model of the 12-pod lunar colony.
- `/rover/output/report.md` — a markdown analysis of that colony.

The candidate implements the scripts and the `Dockerfile`. The gateway, pods,
configs, and harness are fixture. Language: Python.

## What the exercise actually tests

Four judgment calls sit under the coding:

1. **Where the LLM belongs.** The crawl is 12 known-shape REST endpoints on a
   bounded graph — LLMs add noise, not value. The *report* is synthesis across
   narrative text (logs + comms) — that's where the LLM earns its keep. Graph
   metrics (degree, SPOFs) stay deterministic: exact, auditable, cheap.
2. **Schema design for `map.json`.** It's the interface between the two
   phases. Rich map → easy report. Thin map → Phase 2 re-parses everything.
3. **Actual discovery, not a hardcoded pod list.** Gateway reveals only Artemis
   by design. BFS via `/dependencies` and `/supplies` edges reaches the rest.
4. **Reading the real signal.** `/status` is a stub (always "nominal"). Colony
   history lives in `/logs` and `/comms`. `/comms` 404s on most pods — expected.

## Recommended approach

Split cleanly into two phases with `map.json` as the only interface:

**Phase 1 — deterministic crawler.**
Start from the gateway pointer, BFS the dependency/supply graph, pull every
pod's full payload (`/info`, `/status`, `/dependencies`, `/supplies`, `/logs`,
`/comms`). Handle 404 from `/comms` as "absent", not error. Record fetch
metadata (status code, timing, errors) alongside data. Write a rich
`map.json`: per-pod payloads, edges both directions, merged event timeline
across all logs/comms, colony-level metadata.

**Phase 2 — hybrid reporter.**
Compute graph metrics deterministically: in/out-degree, articulation points
(SPOFs), transitive dependents (what breaks if pod X fails). Use the LLM only
for narrative synthesis — reading the merged timeline and pod metadata to
identify cross-pod themes (decommissioning events, infrastructure transfers,
directive impact). LLM sees the map, never raw HTTP. Compose a markdown report
that grounds every claim in either a metric or a citable event.

> See [`architecture.md`](architecture.md) for a Mermaid component
> diagram of the same shape.

## Proposed module layout

```
rover/
  agent/
    http.py         # httpx client; timeout, retry, 404-as-absent
    crawl.py        # BFS from gateway pointer
    graph.py        # edges, degree, articulation points, transitive collapse
    timeline.py     # merge + sort logs/comms across pods
    llm.py          # Claude client; grounded prompts with map as context
    render.py       # markdown report assembly
    main.py         # `python -m agent map` / `python -m agent report`
  run_mapping.sh    # exec python -m agent map
  run_reporting.sh  # exec python -m agent report
  Dockerfile        # + httpx, anthropic; pinned versions
```

## Critical files / references

- `gateway/index.js:5-25` — single entry pointer (Artemis).
- `pod-service/index.js:22-72` — endpoint contracts; `/comms` 404 quirk at :64-71.
- `pod-service/index.js:12` — hard-coded "now" = 2094-08-15 (uptime anchor).
- `rover/base/server.py:29-67` — job lifecycle; exit 0 + output file required.
- `candidate/README.md` — mission brief; deliverable schemas are candidate's choice.
- `rover/Dockerfile` — candidate extends to install dependencies.
- `configs/*.json` — per-pod fixture data; 12 pods; some `comms: []`.

## LLM stack (decided)

Raw `anthropic` SDK with prompt caching. Rationale: the LLM only does
narrative synthesis on a pre-built `map.json` — no tool-calling, no
orchestration — so the SDK surface area we need is small, and caching the map
as a long system/user prefix across several focused calls (criticality
narrative, infra-evolution narrative, SPOF narrative) keeps cost and latency
predictable.

## Non-blocking choices (will be picked during implementation)

- **Report depth.** Default: cover the three explicit questions from
  `candidate/README.md` (critical pods, SPOFs, infrastructure evolution), plus
  a short "notable observations" section for themes the agent surfaces that
  don't fit those buckets.
- **Testing posture.** Default: unit tests for `graph.py` and `timeline.py`
  against fixture JSON (pure functions, cheap to test); a smoke script that
  exercises the live stack end-to-end. Skip unit tests for `llm.py` — assert
  the shape of its output in the smoke script instead.

## Verification (how we'll know it works end-to-end)

1. `LLM_API_KEY=... docker compose up --build -d` — stack starts.
2. `curl localhost:3000` — gateway sanity check.
3. `curl -X POST localhost:8080/map` → poll `/get-map` until 200. Validate
   `map.json` has all 12 pods, edges in both directions, timeline merged.
4. `curl -X POST localhost:8080/report` → poll `/get-report` until 200.
   Validate `report.md` answers the three questions from `candidate/README.md`
   with concrete pod names and evidence.
5. Kill one pod; re-run; confirm the agent degrades gracefully (partial map
   with explicit errors, not a 500).
