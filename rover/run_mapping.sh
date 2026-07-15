#!/bin/bash
# Mapping agent entrypoint.
# Available environment variables:
#   GATEWAY_URL    - Colony gateway (http://gateway:3000)
#   LLM_API_KEY    - Your LLM provider API key (Anthropic)
#   LLM_TAG        - Optional. Set to "true" / "1" / "yes" to enable the
#                    flag-gated Claude extraction pass over each pod's
#                    logs+comms (see agent/llm_tag.py). Adds ~5-10s and 12
#                    sonnet calls per /map invocation. Default: off.
#   MAPPING_FLAGS  - Optional. Raw CLI flags forwarded to `python -m agent.mapping`.
#                    Power-user override; takes precedence over LLM_TAG.
#
# Output: /rover/output/map.json

set -euo pipefail
cd /rover

FLAGS="${MAPPING_FLAGS:-}"
case "${LLM_TAG:-}" in
  1|true|TRUE|True|yes|Yes|YES)
    case " $FLAGS " in
      *" --llm-tag "*) ;;
      *) FLAGS="$FLAGS --llm-tag" ;;
    esac
    ;;
esac

exec python -m agent.mapping $FLAGS
