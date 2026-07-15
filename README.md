# Project Selene

A take-home engineering exercise that evaluates systems thinking. You'll build an autonomous agent to discover, map, and analyze a simulated lunar colony — twelve interconnected habitat pods, each running as a Docker container with its own REST API.

This is intentionally open-ended. There is no spec for "done." We're interested in the choices you make, not just the code you write.

## Submission — Anuraag Polisetty

| Artifact | Link |
|---|---|
| Writeup (architecture & design decisions) | [WRITEUP.md](WRITEUP.md) |
| Agent code | [rover/agent/](rover/agent/) |
| Generated map | [rover/output/map.json](rover/output/map.json) |
| Final report | [rover/output/report.md](rover/output/report.md) |

Run the mapping and reporting phases with [rover/run_mapping.sh](rover/run_mapping.sh) and [rover/run_reporting.sh](rover/run_reporting.sh).

## Prerequisites

- **Docker** (with `docker compose`) — the entire colony runs as containers
- **An LLM API key** — Anthropic or OpenAI. Your agent will need it for reasoning over the colony data.

## Getting Started

```bash
docker compose up --build -d
```

Then open **http://localhost:3000** in your browser.

## Full Instructions

See [candidate/README.md](candidate/README.md) for the complete mission briefing, API reference, and deliverables.
