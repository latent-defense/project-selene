"""Deterministic endpoint completion engine."""

import httpx

from .discovery import (
    LEARNED_PORT_BY_POD,
    REQUIRED_POD_ENDPOINTS,
    extract_pod_ids_from_observation,
    record_covered_endpoint_from_observation,
)
from .tool_executor import discover_port_for_pod, run_fetch_tool


def auto_fetch_required_endpoints(
    discovered_pods: set[str],
    fetched_endpoints_by_pod: dict[str, set[str]],
    timeout_seconds: float,
) -> list[dict[str, object]]:
    """Deterministically crawl missing required endpoints for discovered pods."""
    new_observations: list[dict[str, object]] = []
    required = set(REQUIRED_POD_ENDPOINTS)

    while True:
        scheduled = False
        for pod in sorted(discovered_pods):
            if pod not in LEARNED_PORT_BY_POD:
                with httpx.Client(timeout=timeout_seconds) as client:
                    discover_port_for_pod(pod, client)
            port = LEARNED_PORT_BY_POD.get(pod)
            if port is None:
                continue

            covered = fetched_endpoints_by_pod.get(pod, set())
            missing = sorted(required - covered)
            for endpoint in missing:
                scheduled = True
                requested_url = f"http://{pod}:{port}{endpoint}"
                observation = run_fetch_tool({"url": requested_url}, timeout_seconds)
                new_observations.append(observation)
                record_covered_endpoint_from_observation(
                    observation, fetched_endpoints_by_pod
                )
                discovered_pods.update(extract_pod_ids_from_observation(observation))

        if not scheduled:
            break

    return new_observations


__all__ = ["auto_fetch_required_endpoints"]
