# Project Selene — Architecture

The system is two phases joined by a single artifact (`map.json`).
Stage 1 is deterministic; stage 2 is LLM-augmented but operates on
the substrate, not on the live HTTP surface.

## Component diagram

```mermaid
flowchart TB
  subgraph COLONY[Colony - fixture]
    GW["gateway:3000<br/>entrypoint = artemis"]
    PODS["12 pods (helios:3001 … sentinel:3012)<br/>endpoints: /info /status /dependencies<br/>/supplies /logs /comms"]
    GW -.->|hint| PODS
  end

  subgraph STAGE1[Stage 1 - mapping &lpar;deterministic&rpar;]
    HTTP["http.py<br/>httpx + receipts + retries"]
    DISC["discovery.py<br/>port-probe resolver (3001–3012)"]
    CRAWL["crawl.py<br/>BFS, per-pod 6-endpoint sweep"]
    GRAPH["graph.py<br/>edges + reconciliation + metrics"]
    ENTITIES["entities.py<br/>regex over pod IDs / directives / resources"]
    TIMELINE["timeline.py<br/>merge logs+comms, sort, tag entities,<br/>raw_pointer to per-pod source"]
    FACETS["facets.py<br/>by_directive / by_resource / by_pod_mentioned"]
    MAIN1["main.py — assemble<br/>atomic write, cleanup checkpoint"]
  end

  subgraph CKPT_M[".checkpoint/mapping/"]
    PODSDIR["pods/&lt;id&gt;.json<br/>(atomic temp+rename)"]
    QJSONL["queries.jsonl<br/>(append-only receipts)"]
    EJSONL["errors.jsonl<br/>(append-only)"]
  end

  MAP["/rover/output/map.json<br/>the interface"]

  subgraph STAGE2[Stage 2 - reporting &lpar;LLM-augmented&rpar;]
    LLM["llm.py — Reporter<br/>builds substrate + citation rules + few-shots<br/>AsyncAnthropic, prompt cache (ephemeral)"]
    RENDER_ALL["reporter.py — render_all<br/>cache-warm: section 0 sync<br/>then asyncio.gather over sections 1..5"]
    SECTIONS["per-section synthesis<br/>(executive_summary, critical_pods, spofs,<br/>infra_evolution, reconciliation, notable_observations)"]
    COMPOSE["render.py — compose<br/>header + banner + ordered sections"]
    AUDIT["audit.py<br/>regex citations, validate against map<br/>print summary"]
    MAIN2["main.py — atomic write report.md<br/>cleanup checkpoint"]
  end

  subgraph CKPT_R[".checkpoint/reporting/"]
    SECTIONSDIR["sections/&lt;name&gt;.md<br/>(atomic temp+rename)"]
    EJSONL2["errors.jsonl"]
  end

  REPORT["/rover/output/report.md<br/>the deliverable"]

  HARNESS["rover/base/server.py — FastAPI harness<br/>POST /map, /report — spawn subprocess<br/>GET /get-map, /get-report — poll"]

  PODS -->|HTTP GET| HTTP
  GW -->|seed pod_id| HTTP
  HTTP --> DISC
  DISC --> CRAWL
  CRAWL --> GRAPH
  CRAWL --> ENTITIES
  ENTITIES --> TIMELINE
  TIMELINE --> FACETS
  CRAWL -.->|per-pod write| PODSDIR
  CRAWL -.->|per-call append| QJSONL
  CRAWL -.->|per-failure append| EJSONL
  PODSDIR -->|reload on resume| CRAWL
  GRAPH --> MAIN1
  FACETS --> MAIN1
  MAIN1 -->|atomic write| MAP

  MAP --> LLM
  LLM --> RENDER_ALL
  RENDER_ALL --> SECTIONS
  SECTIONS -.->|per-section write| SECTIONSDIR
  SECTIONS -.->|per-failure append| EJSONL2
  SECTIONSDIR -->|reload on resume| SECTIONS
  SECTIONS --> COMPOSE
  COMPOSE --> AUDIT
  AUDIT --> MAIN2
  MAP --> AUDIT
  MAIN2 -->|atomic write| REPORT

  HARNESS -.->|exec| MAIN1
  HARNESS -.->|exec| MAIN2

  classDef colony fill:#e8f0fe,stroke:#4285f4
  classDef stage1 fill:#fef7e0,stroke:#fbbc04
  classDef stage2 fill:#e6f4ea,stroke:#34a853
  classDef ckpt fill:#fce8e6,stroke:#ea4335
  classDef artifact fill:#f1f3f4,stroke:#5f6368,stroke-width:2px
  class COLONY,GW,PODS colony
  class STAGE1,HTTP,DISC,CRAWL,GRAPH,ENTITIES,TIMELINE,FACETS,MAIN1 stage1
  class STAGE2,LLM,RENDER_ALL,SECTIONS,COMPOSE,AUDIT,MAIN2 stage2
  class CKPT_M,PODSDIR,QJSONL,EJSONL,CKPT_R,SECTIONSDIR,EJSONL2 ckpt
  class MAP,REPORT artifact
```

## Data flow at a glance

1. **Mapping** crawls the colony from one URL, port-probes new pod IDs,
   and persists per-pod JSON + every HTTP receipt + every error to a
   checkpoint as it goes. After the BFS frontier drains, derived data
   (entity tags → unified timeline → facet indexes → graph metrics with
   reconciliation) is computed in-memory and `map.json` is written
   atomically. Successful write triggers checkpoint cleanup.
2. **Reporting** opens `map.json`, builds a structured substrate (the
   long cached prefix), and runs six section calls — first synchronously
   to warm the prompt cache, then five in parallel via `asyncio.gather`.
   Each section is checkpointed independently; degraded sections fall
   back to deterministic templates with an error logged. After the
   final markdown is composed, the citation audit validates every
   `[t:N]` / `[d:…]` / `[e:…]` reference against the map and emits a
   summary. Successful write triggers checkpoint cleanup.

## Resume

Both phases can be killed mid-flight and restarted. On rerun, each
phase loads its checkpoint:

- Mapping skips any pod already in `.checkpoint/mapping/pods/`.
- Reporting reuses any section already in
  `.checkpoint/reporting/sections/`.
- HTTP receipts and error logs are append-only, so they survive across
  runs and end up complete in the final `map.json`.

## Storage layout in `map.json`

- `pods["<id>"]` — verbatim per-pod responses (the source of truth for
  raw payloads).
- `timeline[N]` — derived chronological view; each entry carries
  `text`, `entities`, and a `raw_pointer: {endpoint, index}` that
  dereferences back to `pods[source_pod].endpoints[endpoint]` to
  recover the original record without storing it twice.
- `edges[]` — `(supplier, consumer, resource)` triples with
  `claimed_by`, `criticality`, `log_evidence`, `status`.
- `graph_metrics` — degree, articulation points, transitive
  dependents, plus `unique_supplier_edges`, `critical_unique_edges`,
  `spof_pods` (the supply-level SPOF view).
- `facets` — inverted indexes over the timeline's entity tags.
- `queries[]`, `errors[]` — full HTTP receipt log + error log loaded
  from the checkpoint.
- `discovery_trace` — implicit in `pods[<id>].learned_from` +
  `resolution_query_ids`.
