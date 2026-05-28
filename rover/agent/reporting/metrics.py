"""Deterministic metrics helpers."""

from collections import defaultdict, deque
from typing import Any

CRITICALITY_WEIGHT = {"high": 3, "medium": 2, "low": 1}


def compute_dependency_metrics(
    dependency_graph: list[dict[str, Any]], discovered_pods: set[str]
) -> dict[str, Any]:
    in_degree: dict[str, int] = {pod: 0 for pod in discovered_pods}
    weighted_in_degree: dict[str, int] = {pod: 0 for pod in discovered_pods}
    reverse_adj: dict[str, set[str]] = defaultdict(set)

    for edge in dependency_graph:
        supplier = edge["to_pod"]
        dependent = edge["from_pod"]
        in_degree[supplier] = in_degree.get(supplier, 0) + 1
        weight = CRITICALITY_WEIGHT.get(str(edge.get("criticality", "")).lower(), 1)
        weighted_in_degree[supplier] = weighted_in_degree.get(supplier, 0) + weight
        reverse_adj[supplier].add(dependent)

    top_depended = sorted(
        (
            {
                "pod_id": pod,
                "in_degree": in_degree.get(pod, 0),
                "weighted_in_degree": weighted_in_degree.get(pod, 0),
            }
            for pod in discovered_pods
        ),
        key=lambda item: (item["weighted_in_degree"], item["in_degree"]),
        reverse=True,
    )

    failure_impact: list[dict[str, Any]] = []
    for item in top_depended[:5]:
        root = item["pod_id"]
        visited: set[str] = set()
        queue = deque(reverse_adj.get(root, set()))
        while queue:
            current = queue.popleft()
            if current in visited:
                continue
            visited.add(current)
            for nxt in reverse_adj.get(current, set()):
                if nxt not in visited:
                    queue.append(nxt)
        failure_impact.append(
            {
                "pod_id": root,
                "immediate_dependents": sorted(reverse_adj.get(root, set())),
                "immediate_count": len(reverse_adj.get(root, set())),
                "transitive_dependents": sorted(visited),
                "transitive_count": len(visited),
            }
        )

    hidden_spofs = [
        impact
        for impact in failure_impact
        if impact["transitive_count"] >= 4 or impact["immediate_count"] >= 3
    ]
    return {
        "top_depended_pods": top_depended,
        "failure_impact": failure_impact,
        "hidden_spofs": hidden_spofs,
    }


__all__ = ["compute_dependency_metrics", "CRITICALITY_WEIGHT"]
