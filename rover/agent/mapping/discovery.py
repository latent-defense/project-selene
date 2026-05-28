"""Pod discovery and coverage helpers."""

from typing import Any
from urllib.parse import urlparse

REQUIRED_POD_ENDPOINTS = (
    "/info",
    "/status",
    "/dependencies",
    "/supplies",
    "/logs",
    "/comms",
)
LEARNED_PORT_BY_POD: dict[str, int] = {}


def register_pod_port_from_url(url: str) -> None:
    """Persist host->port mappings from successful in-network requests."""
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    port = parsed.port
    if not host or port is None:
        return
    if host in {"localhost", "127.0.0.1", "gateway"}:
        return
    LEARNED_PORT_BY_POD[host] = port


def learn_pod_port_mapping_from_json(payload: Any, source_url: str) -> None:
    """Learn pod_id to port mappings from discovered response payloads."""
    parsed_source = urlparse(source_url)
    source_port = parsed_source.port
    source_host = (parsed_source.hostname or "").lower()

    def visit(node: Any) -> None:
        if isinstance(node, dict):
            pod = node.get("pod")
            localhost_url = node.get("url")
            if isinstance(pod, str) and isinstance(localhost_url, str):
                parsed_localhost = urlparse(localhost_url)
                localhost_host = (parsed_localhost.hostname or "").lower()
                localhost_port = parsed_localhost.port
                if (
                    localhost_host in {"localhost", "127.0.0.1"}
                    and localhost_port is not None
                ):
                    LEARNED_PORT_BY_POD[pod.lower()] = localhost_port

            pod_id = node.get("id")
            if (
                isinstance(pod_id, str)
                and source_port is not None
                and source_host
                and source_host not in {"localhost", "127.0.0.1"}
                and source_host == pod_id.lower()
            ):
                LEARNED_PORT_BY_POD[pod_id.lower()] = source_port

            for value in node.values():
                visit(value)
        elif isinstance(node, list):
            for item in node:
                visit(item)

    visit(payload)


def extract_pod_ids_from_observation(observation: dict[str, Any]) -> set[str]:
    """Extract discovered pod IDs from a single tool observation."""
    pod_ids: set[str] = set()
    payload = observation.get("json")
    if not isinstance(payload, dict):
        return pod_ids

    pod_id = payload.get("id")
    if isinstance(pod_id, str):
        pod_ids.add(pod_id.lower())

    entrypoint = payload.get("entrypoint")
    if isinstance(entrypoint, dict):
        entrypoint_pod = entrypoint.get("pod")
        if isinstance(entrypoint_pod, str):
            pod_ids.add(entrypoint_pod.lower())

    for field in ("dependencies", "supplies"):
        items = payload.get(field)
        if isinstance(items, list):
            for item in items:
                if isinstance(item, dict):
                    item_pod_id = item.get("pod_id")
                    if isinstance(item_pod_id, str):
                        pod_ids.add(item_pod_id.lower())

    return pod_ids


def record_covered_endpoint_from_observation(
    observation: dict[str, Any], fetched_endpoints_by_pod: dict[str, set[str]]
) -> None:
    """Track endpoint coverage for each pod host."""
    url = observation.get("resolved_url") or observation.get("url")
    if not isinstance(url, str):
        return
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    if not host or host in {"localhost", "127.0.0.1", "gateway"}:
        return
    path = parsed.path or "/"
    if path in REQUIRED_POD_ENDPOINTS:
        fetched_endpoints_by_pod.setdefault(host, set()).add(path)


def is_crawl_complete(
    discovered_pods: set[str], fetched_endpoints_by_pod: dict[str, set[str]]
) -> bool:
    """Return True when all discovered pods have all required endpoint coverage."""
    if len(discovered_pods) < 12:
        return False
    required = set(REQUIRED_POD_ENDPOINTS)
    for pod in discovered_pods:
        if not required.issubset(fetched_endpoints_by_pod.get(pod, set())):
            return False
    return True


__all__ = [
    "LEARNED_PORT_BY_POD",
    "REQUIRED_POD_ENDPOINTS",
    "extract_pod_ids_from_observation",
    "is_crawl_complete",
    "learn_pod_port_mapping_from_json",
    "record_covered_endpoint_from_observation",
    "register_pod_port_from_url",
]
