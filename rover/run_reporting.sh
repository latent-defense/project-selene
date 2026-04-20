#!/bin/bash
# Reporting agent entrypoint.
# Env (from harness): GATEWAY_URL (unused here), LLM_API_KEY (required).
# Input:  /rover/output/map.json
# Output: /rover/output/report.md
set -euo pipefail
cd /rover
exec python -m agent report
