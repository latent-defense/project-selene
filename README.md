# Project Selene

A take-home engineering exercise that evaluates systems thinking. You'll build an autonomous agent to discover, map, and analyze a simulated lunar colony — twelve interconnected habitat pods, each running as a Docker container with its own REST API.

This is intentionally open-ended. There is no spec for "done." We're interested in the choices you make, not just the code you write.

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

## Work-product

A reviewer's map of the submission, in the order worth reading:

- **[`WRITEUP.md`](WRITEUP.md)** — 1-page design decisions, findings, and what we'd do with more time.
- **[`docs/deliberation.md`](docs/deliberation.md)** — the design dialog: how the shape of the system was argued into place, what alternatives were weighed, where we reversed course. This is the most informative artifact for understanding the *thinking* behind the code.
- **[`docs/approach.md`](docs/approach.md)** — 10,000-ft architecture summary (polished companion to the deliberation).
- **[`docs/sprint-plan.md`](docs/sprint-plan.md)** — milestone-level execution plan with acceptance criteria.
- **[`rover/agent/`](rover/agent/)** — Python source (one module per responsibility: `http`, `discovery`, `crawl`, `checkpoint`, `entities`, `timeline`, `facets`, `graph`, `llm`, `reporter`, `render`, `audit`, `main`).
- **[`rover/tests/`](rover/tests/)** — pytest suite over the deterministic pieces.
- **`/rover/output/map.json`** and **`/rover/output/report.md`** — produced by `make map` and `make report`; see `Makefile` for the full command surface.

### Run it

```bash
cp .env.example .env   # then set LLM_API_KEY
make demo              # up + map + report (one shot)
make get-report        # read the findings
```

Use `make help` to see every target.
