#!/bin/bash
# Mapping agent entrypoint.
# Env (from harness): GATEWAY_URL, LLM_API_KEY (latter unused in mapping).
# Output: /rover/output/map.json
set -euo pipefail
cd /rover
exec python -m agent map
