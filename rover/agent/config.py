"""Shared constants and small helpers.

The colony clock is frozen: pod-service computes uptime against 2094-08-15, so all
temporal analysis uses the same reference instead of the real wall clock.
"""
import os

GATEWAY_URL = os.environ.get("GATEWAY_URL", "http://gateway:3000")

# Pods are exposed on a fixed, contiguous range (see docker-compose.yml). Discovery
# resolves a pod_id -> port by probing this range; it is not hardcoded per pod.
POD_PORT_RANGE = range(3001, 3013)  # 3001..3012

ENDPOINTS = ["info", "status", "dependencies", "supplies", "logs", "comms"]

OUTPUT_DIR = "/rover/output"
MAP_PATH = os.path.join(OUTPUT_DIR, "map.json")
REPORT_PATH = os.path.join(OUTPUT_DIR, "report.md")

# Frozen "now" — matches pod-service/index.js uptimeDays().
COLONY_NOW = "2094-08-15T00:00:00Z"
COLONY_ESTABLISHED = "2092-01-15"

# Resource classification for supply/dependency reconciliation (DDL-006).
# Administrative/oversight flows are expected to be one-directional and must NOT be
# treated as integrity errors. Everything else is treated as a material resource.
ADMIN_RESOURCE_KEYWORDS = (
    "oversight", "approval", "directive", "administrative", "admin",
    "planning", "threat", "assessment", "report", "coordination",
    "monitoring", "surveillance", "authorization", "policy", "scheduling",
    "management", "logistics",
)


def classify_resource(resource: str) -> str:
    """Return 'administrative' or 'material' for a resource label."""
    r = (resource or "").lower()
    return "administrative" if any(k in r for k in ADMIN_RESOURCE_KEYWORDS) else "material"
