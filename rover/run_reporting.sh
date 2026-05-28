#!/bin/bash
# Your reporting agent entrypoint.
# Available environment variables:
#   GATEWAY_URL  - Colony gateway (http://gateway:3000)
#   LLM_API_KEY  - Your LLM provider API key
#
# Input: /rover/output/map.json (produced by run_mapping.sh)
# Output: /rover/output/report.md
#
# Analyze the map and produce a Markdown report on your findings.

set -euo pipefail

python /rover/agent/reporting_agent.py
