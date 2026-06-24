#!/bin/bash
# Mapping agent entrypoint — discovers the colony and writes /rover/output/map.json.
# Deterministic: no LLM involved in mapping.
set -euo pipefail
cd /rover
exec python -m agent.mapping
