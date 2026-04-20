"""BFS crawl from the gateway seed, with per-pod checkpoint atomicity.

The crawl is the ground-truth production step of stage 1: it should favor
accuracy and explainability (see `deliberation.md` §11). Every HTTP call is
recorded; per-pod success or failure is explicit; the BFS frontier is
derivable from the completed pods' `/supplies` and `/dependencies`, so we
don't need a separate frontier file to resume.

Per-pod atomicity: a pod is either fully crawled (all required endpoints
returned 200; `/comms` 404 is fine) or not saved at all. On resume, an
unsaved pod is retried fresh.
"""
from __future__ import annotations

from typing import Any

from .checkpoint import MappingCheckpoint
from .discovery import PodUnreachable, Resolver, parse_gateway
from .http import Client

POD_ENDPOINTS = ("/info", "/status", "/dependencies", "/supplies", "/logs", "/comms")
REQUIRED_ENDPOINTS = {"/info", "/status", "/dependencies", "/supplies", "/logs"}
# /comms: 404 is first-class "absent"; 200 is data. Any other status counts as a failure.


class PodIncomplete(RuntimeError):
    """Raised inside `fetch_pod` when a required endpoint didn't return 200."""

    def __init__(self, pod_id: str, failures: list[dict[str, Any]]) -> None:
        super().__init__(
            f"pod {pod_id!r} incomplete; failures={failures}"
        )
        self.pod_id = pod_id
        self.failures = failures


def fetch_pod(
    client: Client,
    pod_id: str,
    resolved_url: str,
    learned_from: str,
    resolution_query_ids: list[int],
) -> dict[str, Any]:
    """Fetch all six endpoints for a pod. Returns the pod record or raises."""
    endpoints: dict[str, Any] = {}
    endpoint_query_ids: dict[str, int] = {}
    endpoint_status: dict[str, int | None] = {}
    failures: list[dict[str, Any]] = []

    for ep in POD_ENDPOINTS:
        data, receipt = client.get(
            f"{resolved_url}{ep}",
            target_service=pod_id,
            endpoint=ep,
            discovery_context=learned_from,
        )
        status = receipt.get("status_code")
        endpoints[ep] = data  # None for 404 / error
        endpoint_query_ids[ep] = receipt["query_id"]
        endpoint_status[ep] = status

        ok = (status == 200) or (ep == "/comms" and status == 404)
        if not ok:
            failures.append(
                {
                    "endpoint": ep,
                    "status_code": status,
                    "query_id": receipt["query_id"],
                    "exception_type": receipt.get("exception_type"),
                }
            )

    if failures:
        raise PodIncomplete(pod_id, failures)

    return {
        "pod_id": pod_id,
        "resolved_url": resolved_url,
        "learned_from": learned_from,
        "resolution_query_ids": resolution_query_ids,
        "endpoints": endpoints,
        "endpoint_query_ids": endpoint_query_ids,
        "endpoint_status": endpoint_status,
    }


def _extract_neighbors(pod_id: str, pod_data: dict[str, Any]) -> list[tuple[str, str]]:
    """Return `[(neighbor_pod_id, learned_from_label), ...]` from supplies+deps."""
    neighbors: list[tuple[str, str]] = []
    eps = pod_data["endpoints"]
    for endpoint_key, source_label in (
        ("/supplies", f"{pod_id}.supplies"),
        ("/dependencies", f"{pod_id}.dependencies"),
    ):
        body = eps.get(endpoint_key) or {}
        entries = body.get(endpoint_key.lstrip("/"), [])
        for entry in entries:
            nid = entry.get("pod_id")
            if isinstance(nid, str) and nid:
                neighbors.append((nid, source_label))
    return neighbors


def crawl(gateway_url: str, checkpoint: MappingCheckpoint) -> dict[str, Any]:
    """BFS-crawl the colony, returning the assembled crawl artifact.

    On entry, any pods already in the checkpoint are reused as-is; their
    URLs preload the resolver so we don't re-probe. The crawl then extends
    the BFS from their supplies/deps until the frontier drains.
    """
    completed: dict[str, dict[str, Any]] = checkpoint.load_all_pods()
    resolver_seed_log: list[dict[str, Any]] = []  # records for resumed pods

    resume_notes: list[str] = []
    if completed:
        resume_notes.append(f"resuming with {len(completed)} pods already checkpointed")

    with Client(checkpoint) as client:
        resolver = Resolver(client)

        # Preload resolver with URLs known from the checkpoint
        for pod_id, data in completed.items():
            url = data.get("resolved_url")
            if isinstance(url, str):
                resolver.preload(pod_id, url)

        # Gateway parse — always run (cheap; produces colony_metadata)
        gw = parse_gateway(gateway_url, client)
        seed_pod_id = gw["seed_pod_id"]

        # BFS
        queue: list[tuple[str, str]] = [(seed_pod_id, "gateway")]
        visited: set[str] = set(completed.keys())

        while queue:
            pod_id, learned_from = queue.pop(0)
            if pod_id in visited:
                continue
            visited.add(pod_id)

            try:
                resolution = resolver.resolve(pod_id, discovery_context=learned_from)
            except PodUnreachable as exc:
                checkpoint.append_error(
                    {
                        "stage": "resolve",
                        "pod_id": pod_id,
                        "learned_from": learned_from,
                        "exception_type": "PodUnreachable",
                        "message": str(exc),
                        "resumable": True,
                    }
                )
                continue

            try:
                pod_record = fetch_pod(
                    client,
                    pod_id=pod_id,
                    resolved_url=resolution["resolved_url"],
                    learned_from=learned_from,
                    resolution_query_ids=resolution["resolution_query_ids"],
                )
            except PodIncomplete as exc:
                checkpoint.append_error(
                    {
                        "stage": "fetch_pod",
                        "pod_id": pod_id,
                        "learned_from": learned_from,
                        "resolved_url": resolution["resolved_url"],
                        "exception_type": "PodIncomplete",
                        "message": str(exc),
                        "failures": exc.failures,
                        "resumable": True,
                    }
                )
                continue
            except Exception as exc:  # defensive; unexpected
                checkpoint.append_error(
                    {
                        "stage": "fetch_pod",
                        "pod_id": pod_id,
                        "learned_from": learned_from,
                        "exception_type": type(exc).__name__,
                        "message": str(exc),
                        "resumable": True,
                    }
                )
                continue

            checkpoint.save_pod(pod_id, pod_record)
            completed[pod_id] = pod_record

            for neighbor_id, source_label in _extract_neighbors(pod_id, pod_record):
                if neighbor_id not in visited:
                    queue.append((neighbor_id, source_label))

    return {
        "colony_metadata": gw["colony_metadata"],
        "seed_pod_id": seed_pod_id,
        "gateway_hint_url": gw["gateway_hint_url"],
        "pods": completed,
        "queries": checkpoint.load_queries(),
        "errors": checkpoint.load_errors(),
        "resume_notes": resume_notes,
    }
