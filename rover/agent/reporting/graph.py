"""Graph normalization and rendering helpers."""

from typing import Any
from urllib.parse import urlparse


def normalize_dependency_graph(raw_edges: list[dict[str, Any]]) -> list[dict[str, Any]]:
    edges: list[dict[str, Any]] = []
    for edge in raw_edges:
        from_pod = edge.get("from_pod")
        to_pod = edge.get("to_pod")
        if not isinstance(from_pod, str) or not isinstance(to_pod, str):
            continue
        edges.append(
            {
                "from_pod": from_pod.lower(),
                "to_pod": to_pod.lower(),
                "resource": edge.get("resource"),
                "criticality": edge.get("criticality"),
                "notes": edge.get("notes"),
            }
        )
    return edges


def normalize_supply_graph(raw_edges: list[dict[str, Any]]) -> list[dict[str, Any]]:
    edges: list[dict[str, Any]] = []
    for edge in raw_edges:
        from_pod = edge.get("from_pod")
        to_pod = edge.get("to_pod")
        if not isinstance(from_pod, str) or not isinstance(to_pod, str):
            continue
        edges.append(
            {
                "from_pod": from_pod.lower(),
                "to_pod": to_pod.lower(),
                "resource": edge.get("resource"),
            }
        )
    return edges


def extract_discovered_pods(args: dict[str, Any]) -> set[str]:
    crawl_summary = args["crawl_summary"]
    placeholders = args["placeholders"]
    pods: set[str] = set()
    for pod in crawl_summary.get("discovered_pods", []):
        if isinstance(pod, str):
            pods.add(pod.lower())
    for pod in placeholders.get("pods", []):
        if isinstance(pod, str):
            pods.add(pod.lower())
    return pods


def extract_gateway_entrypoint(observations: list[dict[str, Any]]) -> str | None:
    """Extract gateway->entrypoint pod mapping from gateway root response."""
    for observation in observations:
        url = observation.get("resolved_url") or observation.get("url")
        payload = observation.get("json")
        if not isinstance(url, str) or not isinstance(payload, dict):
            continue
        parsed = urlparse(url)
        host = (parsed.hostname or "").lower()
        path = parsed.path or "/"
        if host == "gateway" and path == "/":
            entrypoint = payload.get("entrypoint")
            if isinstance(entrypoint, dict):
                pod = entrypoint.get("pod")
                if isinstance(pod, str):
                    return pod.lower()
    return None


def build_mermaid_graph(
    dependency_graph: list[dict[str, Any]],
    discovered_pods: set[str],
    gateway_entrypoint: str | None,
) -> str:
    lines = ["flowchart TD"]
    lines.append('    gateway["gateway"]')
    for pod in sorted(discovered_pods):
        lines.append(f'    {pod}["{pod}"]')
    if gateway_entrypoint:
        lines.append(f'    gateway -->|"entrypoint"| {gateway_entrypoint}')
    for edge in dependency_graph:
        label = edge.get("resource") or "dependency"
        lines.append(f'    {edge["from_pod"]} -->|"{label}"| {edge["to_pod"]}')
    return "\n".join(lines)


def build_text_graph(
    dependency_graph: list[dict[str, Any]],
    discovered_pods: set[str],
    gateway_entrypoint: str | None,
) -> str:
    adj: dict[str, list[str]] = {pod: [] for pod in discovered_pods}
    adj["gateway"] = []
    if gateway_entrypoint:
        adj["gateway"].append(f"{gateway_entrypoint} (entrypoint)")
    for edge in dependency_graph:
        resource = edge.get("resource") or "dependency"
        adj.setdefault(edge["from_pod"], []).append(f'{edge["to_pod"]} ({resource})')
    lines = []
    for pod in sorted(adj):
        targets = ", ".join(sorted(adj[pod])) if adj[pod] else "(none)"
        lines.append(f"- {pod} -> {targets}")
    return "\n".join(lines)


__all__ = [
    "build_mermaid_graph",
    "build_text_graph",
    "extract_discovered_pods",
    "extract_gateway_entrypoint",
    "normalize_dependency_graph",
    "normalize_supply_graph",
]
