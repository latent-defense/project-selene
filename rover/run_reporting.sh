#!/bin/bash
# Reporting agent entrypoint.
# Available environment variables:
#   GATEWAY_URL  - Colony gateway (http://gateway:3000)
#   LLM_API_KEY  - Your LLM provider API key (Anthropic)
#
# Input:  /rover/output/map.json (produced by run_mapping.sh)
# Output: /rover/output/report.md

set -euo pipefail
cd /rover
exec python -m agent.reporting
