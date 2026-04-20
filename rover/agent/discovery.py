"""Gateway parsing + port-probe URL resolution.

The colony is discovered iteratively from a single entry URL. The gateway
hands us `{entrypoint: {pod, url}}` (fully-qualified for Artemis). Every
other pod comes to us as a bare `pod_id` embedded in a `/supplies` or
`/dependencies` response, and we have to resolve `pod_id → URL` ourselves.

`candidate/README.md` documents the port range (3001–3012) and that pods
are reachable by Docker service name. We probe that range — not to save
guessing, but so the resolution is observable in the query log: every
unsuccessful probe is a receipt, and the agent's discovery path is
reconstructable from the audit trail.
"""
from __future__ import annotations

from typing import Any

from .http import Client

PORT_RANGE_MIN = 3001
PORT_RANGE_MAX = 3012  # documented range from candidate/README.md


class PodUnreachable(RuntimeError):
    """Raised when no port in the documented range answers /info with a matching id."""


def parse_gateway(gateway_url: str, client: Client) -> dict[str, Any]:
    """Hit the gateway and extract the colony metadata + seed pod_id.

    The gateway's `entrypoint.url` uses `localhost`, which is only valid from
    the host machine (see `candidate/README.md`). We therefore return only the
    `seed_pod_id` and let the `Resolver` port-probe it like any other pod —
    one code path for all resolution, and self-correcting if the gateway's
    hint were ever wrong.
    """
    data, receipt = client.get(
        gateway_url,
        target_service="gateway",
        endpoint="/",
        discovery_context="gateway",
    )
    if not isinstance(data, dict) or "entrypoint" not in data:
        raise RuntimeError(
            f"gateway response malformed (query_id={receipt.get('query_id')}): {data!r}"
        )
    entry = data["entrypoint"]
    if not isinstance(entry, dict) or "pod" not in entry:
        raise RuntimeError(f"gateway entrypoint malformed: {entry!r}")
    return {
        "colony_metadata": {k: v for k, v in data.items() if k != "entrypoint"},
        "seed_pod_id": entry["pod"],
        "gateway_hint_url": entry.get("url"),  # recorded for audit; not used
    }


class Resolver:
    """Port-probe resolver with in-process cache.

    Resolution is not re-probed within a single run; on resume, pods that
    were fully crawled are `preload`ed from the checkpoint so their URL
    is known without re-probing.
    """

    def __init__(self, client: Client) -> None:
        self._client = client
        self._cache: dict[str, dict[str, Any]] = {}

    def preload(self, pod_id: str, resolved_url: str) -> None:
        self._cache[pod_id] = {
            "pod_id": pod_id,
            "resolved_url": resolved_url,
            "resolution_query_ids": [],
        }

    def resolve(self, pod_id: str, discovery_context: str) -> dict[str, Any]:
        """Return `{pod_id, resolved_url, resolution_query_ids}`.

        Raises `PodUnreachable` if no port in the documented range responds
        with a matching id.
        """
        if pod_id in self._cache:
            return self._cache[pod_id]

        query_ids: list[int] = []
        for port in range(PORT_RANGE_MIN, PORT_RANGE_MAX + 1):
            base_url = f"http://{pod_id}:{port}"
            data, receipt = self._client.get(
                f"{base_url}/info",
                target_service=pod_id,
                endpoint="/info",
                discovery_context=f"probe:{discovery_context}",
                retries=0,  # probes fail-fast; the whole point is "keep moving"
            )
            query_ids.append(receipt["query_id"])
            if isinstance(data, dict) and data.get("id") == pod_id:
                result = {
                    "pod_id": pod_id,
                    "resolved_url": base_url,
                    "resolution_query_ids": query_ids,
                }
                self._cache[pod_id] = result
                return result

        raise PodUnreachable(
            f"no port in {PORT_RANGE_MIN}..{PORT_RANGE_MAX} on {pod_id!r} "
            f"returned a matching /info (probed {len(query_ids)} ports)"
        )
