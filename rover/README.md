# Rover — Agent Scaffold

> **Submission — Kojin Glick (`kojin-glick-report` branch).** This branch implements the
> mapping + reporting agents and adds an analysis write-up and a companion dashboard.
>
> - **Write-up:** [`../KOJIN_WRITE_UP.md`](../KOJIN_WRITE_UP.md) — start here. The current
>   argument is that Selene is doubly centralized: destructive criticality is concentrated
>   in the `aquifer-helios-terminus` material core, while constructive response is routed
>   through Artemis. Buffers are treated as countdown timers, not redundancy.
> - **Agent code:** [`agent/`](agent/) — `discovery` → `mapping` → `engine` (deterministic
>   topology, cascade, reconciliation, buffer, and coordination analysis) → `tools`
>   (progressive disclosure) → `llm` → `reporting`. Entrypoints:
>   [`run_mapping.sh`](run_mapping.sh), [`run_reporting.sh`](run_reporting.sh).
> - **Produced outputs:** [`../outputs/map.json`](../outputs/map.json),
>   [`../outputs/report.md`](../outputs/report.md).
> - **Dashboard (separate, read-only, does not touch this stack):**
>   https://github.com/moonstripe/selene-colony-monitor — live rover monitor, material /
>   cascade / coordination graph views, deterministic topology brief, failure injection,
>   rendered report, and an agentic chat that streams the rover tool loop.
>
> **Run:**
> ```bash
> LLM_API_KEY=sk-ant-... docker compose up --build -d
> curl -X POST localhost:8080/map     && curl localhost:8080/get-map      # poll to 200
> curl -X POST localhost:8080/report  && curl localhost:8080/get-report   # poll to 200
> ```
> The agent is provider-agnostic — an `sk-...` OpenAI key works too.

---

The rover is a Docker container that runs on the colony network alongside the habitat pods. It provides an HTTP interface for triggering your agent and retrieving results.

## Quick Start

```bash
LLM_API_KEY=your-key-here docker compose up --build
```

The rover is available at **http://localhost:8080**. The colony gateway is at **http://localhost:3000**.

## Architecture

```
rover/
├── Dockerfile          # Build your agent's container — install deps here
├── base/
│   └── server.py       # FastAPI job runner (DO NOT EDIT — handles /map, /report, etc.)
├── run_mapping.sh      # YOUR CODE: discovery + mapping entrypoint
├── run_reporting.sh    # YOUR CODE: analysis + report entrypoint
└── README.md           # This file
```

The job runner (`base/server.py`) handles the HTTP API. When you `POST /map`, it runs `run_mapping.sh` as a subprocess. When you `POST /report`, it runs `run_reporting.sh`. Your job is to implement those two scripts.

## What to Implement

### `run_mapping.sh` — Discovery & Mapping

This script should:
1. Discover the colony network (all 12 pods + gateway)
2. Crawl each pod's API endpoints (`/info`, `/dependencies`, `/supplies`, `/status`, `/logs`, `/comms`)
3. Write structured findings to `/rover/output/map.json`

The JSON schema is entirely up to you — design it for whatever analysis you plan to do in the reporting step.

```bash
# Trigger
curl -X POST localhost:8080/map

# Poll (202 = running, 200 = done with JSON body, 500 = error)
curl localhost:8080/get-map
```

### `run_reporting.sh` — Analysis & Report

This script should:
1. Read `/rover/output/map.json`
2. Analyze the data — build a dependency graph, compute metrics, cross-reference logs
3. Write a Markdown report to `/rover/output/report.md`

```bash
# Trigger (after mapping is done)
curl -X POST localhost:8080/report

# Poll (202 = running, 200 = done with Markdown body, 500 = error)
curl localhost:8080/get-report
```

### The shell scripts are just entrypoints

They can call any language or tool. Examples:

```bash
# Python
cd /rover && python -m my_agent.mapping

# Node
cd /rover && node src/mapper.js

# Direct curl scripting
for port in $(seq 3001 3012); do
  curl -s http://192.168.x.x:$port/info >> /rover/output/map.json
done
```

## Installing Dependencies

Edit the [`Dockerfile`](./Dockerfile) to add whatever you need:

```dockerfile
FROM base

# System tools
RUN apt-get update && apt-get install -y nmap iproute2

# Python packages
RUN pip install anthropic httpx networkx mcp langgraph

# Node packages
# RUN apt-get install -y nodejs npm && npm install openai

COPY . /rover/
RUN chmod +x /rover/run_mapping.sh /rover/run_reporting.sh
```

The base image is `python:3.12-slim` with `fastapi` and `uvicorn` pre-installed.

## Environment Variables

Your scripts inherit these from the container:

| Variable | Value | Description |
|----------|-------|-------------|
| `GATEWAY_URL` | `http://gateway:3000` | Colony gateway address (use this, not localhost) |
| `LLM_API_KEY` | *(your key)* | Your LLM provider API key |

Set `LLM_API_KEY` when starting the stack:

```bash
LLM_API_KEY=sk-ant-... docker compose up --build
```

## Output Files

| File | Format | Produced by | Retrieved via |
|------|--------|-------------|---------------|
| `/rover/output/map.json` | JSON (your schema) | `run_mapping.sh` | `GET /get-map` |
| `/rover/output/report.md` | Markdown | `run_reporting.sh` | `GET /get-report` |

Output persists across container rebuilds via a Docker volume.

## Rover API Reference

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/health` | Health check — returns `{"status": "ok"}` |
| `POST` | `/map` | Start the mapping job |
| `GET` | `/get-map` | Get mapping status or results |
| `POST` | `/report` | Start the reporting job |
| `GET` | `/get-report` | Get reporting status or results |

### Response Codes

| Code | Meaning |
|------|---------|
| **200** | Job complete — response body is the output file |
| **202** | Job started or still running — poll again |
| **404** | No job started yet |
| **409** | Job already running |
| **500** | Job failed — response body contains stderr/logs |

## Colony Pod API Reference

Each habitat pod exposes these REST endpoints:

| Endpoint | Description | Example |
|----------|-------------|---------|
| `GET /` | Plain-text welcome with name, role, status | `curl http://artemis:3002/` |
| `GET /info` | JSON metadata: id, name, role, population, uptime, specs | `curl http://artemis:3002/info` |
| `GET /status` | JSON status: always `"nominal"`, alerts array, last_incident | `curl http://artemis:3002/status` |
| `GET /dependencies` | JSON list of pods this one depends on (with resource + criticality) | `curl http://artemis:3002/dependencies` |
| `GET /supplies` | JSON list of pods this one provides resources to | `curl http://artemis:3002/supplies` |
| `GET /logs` | JSON array of timestamped operational log entries | `curl http://artemis:3002/logs` |
| `GET /comms` | JSON array of inter-pod messages (404 if pod has no comms) | `curl http://artemis:3002/comms` |

### Pod Hostnames and Ports

From inside the rover container, pods are reachable by service name:

| Pod | Hostname | Port |
|-----|----------|------|
| Helios Station | `helios` | 3001 |
| Artemis Core | `artemis` | 3002 |
| Hydroponics Bay | `hydroponics` | 3003 |
| Aquifer Module | `aquifer` | 3004 |
| Zephyr Hub | `zephyr` | 3005 |
| Prometheus Lab | `prometheus` | 3006 |
| Medica Ward | `medica` | 3007 |
| Terminus Mine | `terminus` | 3008 |
| Nexus Relay | `nexus` | 3009 |
| Forge Works | `forge` | 3010 |
| Vault Reserve | `vault` | 3011 |
| Sentinel Array | `sentinel` | 3012 |

You can also discover these dynamically via nmap or DNS from inside the container — the rover is on the same `selene-net` Docker network as all pods.

## Debugging

Check job logs if something fails:

```bash
# View mapping logs
docker compose exec rover cat /rover/output/.map.log

# View reporting logs
docker compose exec rover cat /rover/output/.report.log

# Shell into the rover
docker compose exec rover bash
```
