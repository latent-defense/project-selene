"""Colony discovery (DDL-003).

Strategy: BFS from the gateway's single entrypoint (Artemis), following pod_ids
referenced in each pod's /dependencies and /supplies. A pod_id maps to a Docker
hostname (== pod_id); its port is resolved by probing the known service range.
Then we cross-check: every referenced pod_id must resolve, and the resolved port
set is reported so callers can assert full, non-overlapping coverage.
"""
import logging
from urllib.parse import urlparse

from . import config
from .http_client import get_json

log = logging.getLogger("discovery")


def _probe_port(pod_id: str, port: int):
    """Return /info JSON if pod_id answers on this port with a matching id, else None."""
    url = f"http://{pod_id}:{port}/info"
    data, meta = get_json(url, timeout=2.0, retries=0)
    if data and data.get("id") == pod_id:
        return port
    return None


def resolve_endpoint(pod_id: str, hint_port: int | None = None):
    """Resolve pod_id -> port by probing. Tries the hint first, then the full range."""
    candidates = []
    if hint_port:
        candidates.append(hint_port)
    candidates.extend(p for p in config.POD_PORT_RANGE if p != hint_port)
    for port in candidates:
        if _probe_port(pod_id, port):
            return port
    return None


def _referenced_pod_ids(pod_id: str, port: int):
    """Collect pod_ids referenced in this pod's dependencies and supplies."""
    refs = set()
    for ep, key in (("dependencies", "dependencies"), ("supplies", "supplies")):
        data, _ = get_json(f"http://{pod_id}:{port}/{ep}")
        for item in (data or {}).get(key, []):
            if item.get("pod_id"):
                refs.add(item["pod_id"])
    return refs


def discover():
    """Discover the colony. Returns (registry, errors).

    registry: {pod_id: {"host": pod_id, "port": port, "url": "http://host:port"}}
    """
    errors = []

    gw, gw_meta = get_json(config.GATEWAY_URL)
    if not gw:
        raise RuntimeError(f"gateway unreachable at {config.GATEWAY_URL}: {gw_meta}")

    entry = gw.get("entrypoint", {})
    entry_id = entry.get("pod", "artemis")
    hint_port = None
    if entry.get("url"):
        parsed = urlparse(entry["url"])
        hint_port = parsed.port
    log.info("gateway entrypoint: pod=%s url=%s", entry_id, entry.get("url"))

    registry = {}
    queue = [(entry_id, hint_port)]
    seen = set()

    while queue:
        pod_id, hint = queue.pop(0)
        if pod_id in seen:
            continue
        seen.add(pod_id)
        port = resolve_endpoint(pod_id, hint)
        if port is None:
            errors.append(f"could not resolve endpoint for pod '{pod_id}'")
            log.warning("unresolved pod: %s", pod_id)
            continue
        registry[pod_id] = {"host": pod_id, "port": port, "url": f"http://{pod_id}:{port}"}
        log.info("discovered %s -> %s", pod_id, registry[pod_id]["url"])
        for ref in sorted(_referenced_pod_ids(pod_id, port)):
            if ref not in seen:
                queue.append((ref, None))

    return registry, errors
