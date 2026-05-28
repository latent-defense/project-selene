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

# Kick off reporting via rover API so /get-report shows status.
if [ -f /rover/output/map.json ]; then
  python - <<'PY'
import urllib.error
import urllib.request

url = "http://127.0.0.1:8080/report"
request = urllib.request.Request(url, method="POST")

try:
    with urllib.request.urlopen(request) as response:
        body = response.read().decode("utf-8")
        print(f"[mapping] reporting auto-kickoff response={response.getcode()} body={body}")
except urllib.error.HTTPError as error:
    body = error.read().decode("utf-8")
    if error.code == 409:
        print(f"[mapping] reporting already running body={body}")
    else:
        print(
            f"[mapping] warning: reporting auto-kickoff failed status={error.code} body={body}"
        )
except Exception as error:
    print(f"[mapping] warning: reporting auto-kickoff failed error={error}")
PY
fi
