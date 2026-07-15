"""HTTP client for the colony pod API.

Each pod is reachable by Docker service name (`helios`, `artemis`, ...) on a port
in 3001-3012. The pod APIs don't reveal their own port, so we probe the range
in parallel and pick the first port that answers /info with 200.

Per the plan: each pod exposes /, /info, /status, /dependencies, /supplies,
/logs, and /comms. /comms is missing on ~5 pods and returns 404 there.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass

import httpx

POD_PORT_RANGE = range(3001, 3013)
PROBE_TIMEOUT = 1.5
FETCH_TIMEOUT = 10.0
ENDPOINTS = ("info", "status", "dependencies", "supplies", "logs", "comms")

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class PodEndpoint:
    """An addressable pod: docker service name + the port it's listening on."""

    name: str
    port: int

    @property
    def base_url(self) -> str:
        return f"http://{self.name}:{self.port}"


async def _probe(client: httpx.AsyncClient, name: str, port: int) -> int | None:
    try:
        r = await client.get(f"http://{name}:{port}/info", timeout=PROBE_TIMEOUT)
        return port if r.status_code == 200 else None
    except (httpx.HTTPError, httpx.TimeoutException):
        return None


async def resolve_pod_port(client: httpx.AsyncClient, name: str) -> int | None:
    """Find the port a pod is listening on by probing 3001-3012 in parallel.

    Returns the first port that answers /info with 200, or None if no port
    in the range responds (pod is unreachable on the network).
    """
    results = await asyncio.gather(*(_probe(client, name, p) for p in POD_PORT_RANGE))
    return next((p for p in results if p is not None), None)


async def fetch_endpoint(
    client: httpx.AsyncClient, pod: PodEndpoint, endpoint: str
) -> dict | list | None:
    """Fetch a single endpoint. Returns the JSON body on 200, None otherwise.

    None covers both 404 (expected for /comms on some pods) and transport errors.
    Non-404 errors are logged for visibility but don't fail the crawl.
    """
    url = f"{pod.base_url}/{endpoint}"
    try:
        r = await client.get(url, timeout=FETCH_TIMEOUT)
        if r.status_code == 200:
            return r.json()
        if r.status_code != 404:
            log.warning("%s returned %s", url, r.status_code)
        return None
    except Exception as e:
        log.warning("%s fetch failed: %s", url, e)
        return None


async def fetch_pod(client: httpx.AsyncClient, pod: PodEndpoint) -> dict:
    """Fetch all six standard endpoints in parallel. Keys match ENDPOINTS."""
    results = await asyncio.gather(*(fetch_endpoint(client, pod, ep) for ep in ENDPOINTS))
    return dict(zip(ENDPOINTS, results))
