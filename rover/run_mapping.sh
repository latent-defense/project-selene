#!/bin/bash
# Your mapping agent entrypoint.
# Available environment variables:
#   GATEWAY_URL  - Colony gateway (http://gateway:3000)
#   LLM_API_KEY  - Your LLM provider API key
#
# Your task: discover the colony network and write your map to:
#   /rover/output/map.json
#
# The JSON structure is up to you — design it for downstream analysis.

set -euo pipefail

python /rover/agent/mapping_agent.py
