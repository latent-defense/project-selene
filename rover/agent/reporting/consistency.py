"""Consistency check helpers."""

from collections import defaultdict
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


def compute_endpoint_coverage(
    observations: list[dict[str, Any]]
) -> dict[str, set[str]]:
    coverage: dict[str, set[str]] = defaultdict(set)
    for observation in observations:
        url = observation.get("resolved_url") or observation.get("url")
        if not isinstance(url, str):
            continue
        parsed = urlparse(url)
        host = (parsed.hostname or "").lower()
        path = parsed.path or "/"
        if host and path in REQUIRED_POD_ENDPOINTS:
            coverage[host].add(path)
    return coverage


def build_consistency_checks(
    dependency_graph: list[dict[str, Any]],
    supply_graph: list[dict[str, Any]],
    discovered_pods: set[str],
    endpoint_coverage: dict[str, set[str]],
) -> dict[str, Any]:
    dependency_by_pair: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    supply_by_pair: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for edge in dependency_graph:
        dependency_by_pair[(edge["from_pod"], edge["to_pod"])].append(edge)
    for edge in supply_graph:
        supply_by_pair[(edge["from_pod"], edge["to_pod"])].append(edge)

    missing_supply_links: list[dict[str, Any]] = []
    resource_mismatch_links: list[dict[str, Any]] = []
    for dep in dependency_graph:
        reciprocal = supply_by_pair.get((dep["to_pod"], dep["from_pod"]), [])
        if not reciprocal:
            missing_supply_links.append(
                {
                    "dependent_pod": dep["from_pod"],
                    "supplier_pod": dep["to_pod"],
                    "resource": dep.get("resource"),
                    "criticality": dep.get("criticality"),
                }
            )
            continue
        dep_resource = dep.get("resource")
        reciprocal_resources = {item.get("resource") for item in reciprocal}
        if dep_resource and dep_resource not in reciprocal_resources:
            resource_mismatch_links.append(
                {
                    "dependent_pod": dep["from_pod"],
                    "supplier_pod": dep["to_pod"],
                    "dependency_resource": dep_resource,
                    "supplier_resources": sorted(
                        str(r) for r in reciprocal_resources if r is not None
                    ),
                }
            )

    undocumented_dependency_links: list[dict[str, Any]] = []
    for supply in supply_graph:
        reciprocal = dependency_by_pair.get((supply["to_pod"], supply["from_pod"]), [])
        if not reciprocal:
            undocumented_dependency_links.append(
                {
                    "supplier_pod": supply["from_pod"],
                    "consumer_pod": supply["to_pod"],
                    "resource": supply.get("resource"),
                }
            )

    unknown_pod_references: list[dict[str, Any]] = []
    for edge in dependency_graph + supply_graph:
        if edge["from_pod"] not in discovered_pods:
            unknown_pod_references.append(
                {"pod_id": edge["from_pod"], "referenced_by": "from_pod"}
            )
        if edge["to_pod"] not in discovered_pods:
            unknown_pod_references.append(
                {"pod_id": edge["to_pod"], "referenced_by": "to_pod"}
            )

    endpoint_coverage_gaps: list[dict[str, Any]] = []
    required = set(REQUIRED_POD_ENDPOINTS)
    for pod in sorted(discovered_pods):
        missing = sorted(required - endpoint_coverage.get(pod, set()))
        if missing:
            endpoint_coverage_gaps.append({"pod_id": pod, "missing_endpoints": missing})

    return {
        "summary": {
            "dependency_edge_count": len(dependency_graph),
            "supply_edge_count": len(supply_graph),
            "missing_supply_link_count": len(missing_supply_links),
            "undocumented_dependency_link_count": len(undocumented_dependency_links),
            "resource_mismatch_count": len(resource_mismatch_links),
            "unknown_pod_reference_count": len(unknown_pod_references),
            "endpoint_coverage_gap_count": len(endpoint_coverage_gaps),
        },
        "missing_supply_links": missing_supply_links,
        "undocumented_dependency_links": undocumented_dependency_links,
        "resource_mismatch_links": resource_mismatch_links,
        "unknown_pod_references": unknown_pod_references,
        "endpoint_coverage_gaps": endpoint_coverage_gaps,
    }


__all__ = [
    "build_consistency_checks",
    "compute_endpoint_coverage",
    "REQUIRED_POD_ENDPOINTS",
]
