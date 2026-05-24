#!/bin/bash
# Reporting agent entrypoint — analyzes /rover/output/map.json, writes report.md.
# Deterministic engine computes metrics; LLM narrates via tools.
set -euo pipefail
cd /rover
exec python -m agent.reporting
