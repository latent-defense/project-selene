"""Pod discovery via two complementary passes.

(a) Declarative (gateway-walk): GET gateway → seed pod (artemis) → BFS via
    /dependencies and /supplies, enqueueing every new pod_id encountered.
    Builds the connected component the colony *says* exists.

(b) Observational (network sweep): socket.getaddrinfo against each of the 12
    known pod IDs. Anything that resolves is on the wire.

The two passes converge on a set of reachable pods. The diff between them is
itself a finding: pods seen only by network-sweep are "orphans" (referenced by
no other pod's dependencies/supplies); pods referenced by some other pod but
absent from DNS are "dangling references" (computed later in analysis.py,
which has the full edge list).
"""

from __future__ import annotations

import asyncio
import logging
import socket
from dataclasses import dataclass, field

import httpx

from agent.client import PodEndpoint, fetch_endpoint, resolve_pod_port

KNOWN_POD_IDS = (
    "helios", "artemis", "hydroponics", "aquifer", "zephyr", "prometheus",
    "medica", "terminus", "nexus", "forge", "vault", "sentinel",
)

log = logging.getLogger(__name__)


@dataclass
class DiscoveryResult:
    pods: dict[str, PodEndpoint] = field(default_factory=dict)
    gateway_walk_set: set[str] = field(default_factory=set)
    network_sweep_set: set[str] = field(default_factory=set)

    def discovered_via(self, pod_id: str) -> str:
        in_walk = pod_id in self.gateway_walk_set
        in_sweep = pod_id in self.network_sweep_set
        if in_walk:
            return "gateway_walk"
        if in_sweep:
            return "network_sweep_orphan"
        return "unknown"


async def _resolve_in_dns(loop: asyncio.AbstractEventLoop, pod_id: str) -> bool:
    try:
        await loop.getaddrinfo(pod_id, None, family=socket.AF_INET)
        return True
    except (socket.gaierror, OSError):
        return False


async def network_sweep(loop: asyncio.AbstractEventLoop) -> set[str]:
    """DNS-resolve each known pod ID; return the set that resolves."""
    results = await asyncio.gather(*(_resolve_in_dns(loop, p) for p in KNOWN_POD_IDS))
    return {pid for pid, ok in zip(KNOWN_POD_IDS, results) if ok}


async def _fetch_neighbor_ids(client: httpx.AsyncClient, pod: PodEndpoint) -> list[str]:
    deps, sup = await asyncio.gather(
        fetch_endpoint(client, pod, "dependencies"),
        fetch_endpoint(client, pod, "supplies"),
    )
    ids: list[str] = []
    if isinstance(deps, dict):
        ids.extend(d["pod_id"] for d in deps.get("dependencies", []) if d.get("pod_id"))
    if isinstance(sup, dict):
        ids.extend(s["pod_id"] for s in sup.get("supplies", []) if s.get("pod_id"))
    return ids


async def _gateway_walk_bfs(
    client: httpx.AsyncClient, seed: str
) -> tuple[set[str], dict[str, PodEndpoint]]:
    """BFS in waves. Each wave resolves ports + fetches neighbors in parallel."""
    reached: dict[str, PodEndpoint] = {}
    visited: set[str] = set()
    frontier: list[str] = [seed]

    while frontier:
        batch = sorted({pid for pid in frontier if pid not in visited})
        frontier = []
        if not batch:
            break
        visited.update(batch)

        ports = await asyncio.gather(*(resolve_pod_port(client, pid) for pid in batch))
        endpoints = [
            PodEndpoint(name=pid, port=port)
            for pid, port in zip(batch, ports)
            if port is not None
        ]
        for ep in endpoints:
            reached[ep.name] = ep

        neighbor_lists = await asyncio.gather(*(_fetch_neighbor_ids(client, ep) for ep in endpoints))
        for neighbors in neighbor_lists:
            frontier.extend(n for n in neighbors if n not in visited)

    return set(reached.keys()), reached


async def discover(client: httpx.AsyncClient, gateway_url: str) -> DiscoveryResult:
    """Run both discovery passes, merge into a single result."""
    loop = asyncio.get_running_loop()

    try:
        r = await client.get(gateway_url, timeout=10.0)
        r.raise_for_status()
        seed = r.json()["entrypoint"]["pod"]
        log.info("Gateway responded; seed pod = %s", seed)
    except Exception as e:
        log.error("Gateway fetch failed (%s); falling back to artemis as seed", e)
        seed = "artemis"

    walk_task = _gateway_walk_bfs(client, seed)
    sweep_task = network_sweep(loop)
    (walk_set, reached), sweep_set = await asyncio.gather(walk_task, sweep_task)

    missed = sweep_set - walk_set
    if missed:
        log.info("Network sweep found %d orphan pod(s) not reached by gateway walk: %s",
                 len(missed), sorted(missed))
        missed_list = sorted(missed)
        ports = await asyncio.gather(*(resolve_pod_port(client, pid) for pid in missed_list))
        for pid, port in zip(missed_list, ports):
            if port is not None:
                reached[pid] = PodEndpoint(name=pid, port=port)

    return DiscoveryResult(
        pods=reached,
        gateway_walk_set=walk_set,
        network_sweep_set=sweep_set,
    )
