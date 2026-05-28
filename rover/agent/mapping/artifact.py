"""Map artifact builders."""

from typing import Any


def build_dependency_graph(observations: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Build deterministic dependency graph edges from crawled observations."""
    edges: list[dict[str, Any]] = []
    for observation in observations:
        payload = observation.get("json")
        if not isinstance(payload, dict):
            continue
        pod_id = payload.get("id")
        dependencies = payload.get("dependencies")
        if not isinstance(pod_id, str) or not isinstance(dependencies, list):
            continue
        for dependency in dependencies:
            if not isinstance(dependency, dict):
                continue
            target = dependency.get("pod_id")
            if not isinstance(target, str):
                continue
            edges.append(
                {
                    "from_pod": pod_id.lower(),
                    "to_pod": target.lower(),
                    "resource": dependency.get("resource"),
                    "criticality": dependency.get("criticality"),
                    "notes": dependency.get("notes"),
                }
            )
    return edges


def build_supply_graph(observations: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Build deterministic supply graph edges from crawled observations."""
    edges: list[dict[str, Any]] = []
    for observation in observations:
        payload = observation.get("json")
        if not isinstance(payload, dict):
            continue
        pod_id = payload.get("id")
        supplies = payload.get("supplies")
        if not isinstance(pod_id, str) or not isinstance(supplies, list):
            continue
        for supply in supplies:
            if not isinstance(supply, dict):
                continue
            target = supply.get("pod_id")
            if not isinstance(target, str):
                continue
            edges.append(
                {
                    "from_pod": pod_id.lower(),
                    "to_pod": target.lower(),
                    "resource": supply.get("resource"),
                }
            )
    return edges


__all__ = ["build_dependency_graph", "build_supply_graph"]
