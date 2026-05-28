import json
import os
import re
from collections import defaultdict, deque
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from anthropic import Anthropic, NotFoundError

MAP_PATH = Path("/rover/output/map.json")
REPORT_PATH = Path("/rover/output/report.md")
SYSTEM_PROMPT_PATH = Path("/rover/agent/reporting_system_prompt.md")
DEFAULT_MODEL = "claude-haiku-4-5-20251001"
MODEL_FALLBACKS = (
    "claude-haiku-4-5-20251001",
    "claude-sonnet-4-6",
    "claude-opus-4-7",
)
REQUIRED_POD_ENDPOINTS = ("/info", "/status", "/dependencies", "/supplies", "/logs", "/comms")
CRITICALITY_WEIGHT = {"high": 3, "medium": 2, "low": 1}


@dataclass
class ReportConfig:
    model: str
    max_tokens: int


def utc_now_iso() -> str:
    return datetime.now(UTC).isoformat()


def load_system_prompt() -> str:
    if not SYSTEM_PROMPT_PATH.exists():
        raise FileNotFoundError(f"Reporting system prompt file not found: {SYSTEM_PROMPT_PATH}")
    return SYSTEM_PROMPT_PATH.read_text(encoding="utf-8")


def load_map_artifact() -> dict[str, Any]:
    if not MAP_PATH.exists():
        raise FileNotFoundError(f"map.json not found at {MAP_PATH}")
    data = json.loads(MAP_PATH.read_text(encoding="utf-8"))
    required_keys = ("crawl_summary", "observations", "placeholders")
    missing = [key for key in required_keys if key not in data]
    if missing:
        raise ValueError(f"map.json missing required keys: {missing}")
    return data


def build_report_config() -> ReportConfig:
    model = os.getenv("ANTHROPIC_REPORT_MODEL", os.getenv("ANTHROPIC_MODEL", DEFAULT_MODEL))
    max_tokens = int(os.getenv("REPORT_MAX_TOKENS", "2000"))
    return ReportConfig(model=model, max_tokens=max_tokens)


def get_client() -> Anthropic | None:
    api_key = os.getenv("LLM_API_KEY")
    if not api_key:
        return None
    return Anthropic(api_key=api_key)


def build_model_candidates(configured_model: str) -> list[str]:
    candidates = [configured_model]
    for fallback in MODEL_FALLBACKS:
        if fallback not in candidates:
            candidates.append(fallback)
    return candidates


def list_available_models(client: Anthropic) -> list[str]:
    try:
        models = client.models.list(limit=50)
        return [model.id for model in models.data]
    except Exception:
        return []


def create_message_with_fallback(
    client: Anthropic,
    model_candidates: list[str],
    system_prompt: str,
    user_content: str,
    max_tokens: int,
) -> tuple[str, str]:
    last_not_found_error: NotFoundError | None = None
    for model in model_candidates:
        try:
            response = client.messages.create(
                model=model,
                max_tokens=max_tokens,
                system=system_prompt,
                messages=[{"role": "user", "content": user_content}],
            )
            text = "\n".join(
                block.text for block in response.content if getattr(block, "type", None) == "text"
            ).strip()
            return text, model
        except NotFoundError as exc:
            last_not_found_error = exc
    if last_not_found_error is not None:
        raise last_not_found_error
    raise RuntimeError("No available model for reporting synthesis.")


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


def compute_endpoint_coverage(observations: list[dict[str, Any]]) -> dict[str, set[str]]:
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
                    "supplier_resources": sorted(str(r) for r in reciprocal_resources if r is not None),
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
            unknown_pod_references.append({"pod_id": edge["from_pod"], "referenced_by": "from_pod"})
        if edge["to_pod"] not in discovered_pods:
            unknown_pod_references.append({"pod_id": edge["to_pod"], "referenced_by": "to_pod"})

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


def extract_timeline(observations: list[dict[str, Any]]) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for observation in observations:
        payload = observation.get("json")
        if not isinstance(payload, dict):
            continue
        pod = payload.get("id")
        if not isinstance(pod, str):
            continue
        url = observation.get("resolved_url") or observation.get("url")
        endpoint = ""
        if isinstance(url, str):
            endpoint = urlparse(url).path or "/"
        for field, detail_key in (("logs", "detail"), ("messages", "content")):
            rows = payload.get(field)
            if not isinstance(rows, list):
                continue
            for row in rows:
                if not isinstance(row, dict):
                    continue
                timestamp = row.get("timestamp")
                detail = row.get(detail_key)
                if not isinstance(timestamp, str) or not isinstance(detail, str):
                    continue
                events.append(
                    {
                        "timestamp": timestamp,
                        "pod_id": pod.lower(),
                        "endpoint": endpoint,
                        "event": row.get("event") or field,
                        "detail": detail,
                    }
                )
    return sorted(events, key=lambda item: item["timestamp"])


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


def evidence_line(metric: str, pod: str, endpoint: str, source: str) -> str:
    return f"Evidence: metric:{metric}; pod:{pod}; endpoint:{endpoint}; source:{source}"


def build_deterministic_markdown(
    metrics: dict[str, Any],
    consistency_checks: dict[str, Any],
    timeline: list[dict[str, Any]],
) -> str:
    top = metrics["top_depended_pods"][:3]
    impacts = metrics["failure_impact"][:3]
    spofs = metrics["hidden_spofs"][:3]

    lines: list[str] = []
    lines.append("### Deterministic Findings")
    lines.append("")
    lines.append("#### Most Depended-Upon Pods")
    for item in top:
        lines.append(
            f"- `{item['pod_id']}` has in-degree `{item['in_degree']}` and weighted in-degree `{item['weighted_in_degree']}`."
        )
        lines.append(evidence_line("weighted_in_degree", item["pod_id"], "/dependencies", "dependency_graph"))
    lines.append("")
    lines.append("#### Failure Impact")
    for impact in impacts:
        lines.append(
            f"- If `{impact['pod_id']}` fails: immediate dependents `{impact['immediate_count']}`, transitive impact `{impact['transitive_count']}`."
        )
        lines.append(evidence_line("failure_impact_transitive_count", impact["pod_id"], "/dependencies", "dependency_graph"))
    lines.append("")
    lines.append("#### Hidden Single Points of Failure")
    if spofs:
        for spof in spofs:
            lines.append(
                f"- `{spof['pod_id']}` behaves as a potential SPOF with transitive impact `{spof['transitive_count']}`."
            )
            lines.append(evidence_line("hidden_spof_transitive_threshold", spof["pod_id"], "/dependencies", "risk_metrics"))
    else:
        lines.append("- No strong SPOF threshold exceeded in deterministic checks.")
        lines.append(evidence_line("hidden_spof_transitive_threshold", "none", "n/a", "risk_metrics"))
    lines.append("")
    lines.append("#### Supply vs Dependency Consistency")
    summary = consistency_checks["summary"]
    lines.append(
        f"- Missing reciprocal supply links: `{summary['missing_supply_link_count']}`; undocumented dependency links: `{summary['undocumented_dependency_link_count']}`; resource mismatches: `{summary['resource_mismatch_count']}`."
    )
    lines.append(evidence_line("consistency_summary", "multi", "n/a", "consistency_checks"))
    lines.append("")
    lines.append("#### Infrastructure Evolution (Logs + Comms)")
    if timeline:
        sample = timeline[:2] + timeline[-2:]
        for event in sample:
            lines.append(
                f"- `{event['timestamp']}` `{event['pod_id']}` `{event['event']}`: {event['detail']}"
            )
            lines.append(evidence_line("timeline_event", event["pod_id"], event["endpoint"] or "n/a", "timeline"))
    else:
        lines.append("- Insufficient data to build timeline events.")
        lines.append(evidence_line("timeline_event_count", "none", "n/a", "timeline"))
    return "\n".join(lines)


def synthesize_llm_section(
    client: Anthropic | None,
    config: ReportConfig,
    deterministic_payload: dict[str, Any],
) -> tuple[str, str]:
    if client is None:
        return "LLM synthesis skipped (LLM_API_KEY not set).", "none"
    prompt = load_system_prompt()
    candidates = build_model_candidates(config.model)
    for model in list_available_models(client):
        if model not in candidates:
            candidates.append(model)
    user_content = (
        "Use only the JSON evidence below to write a concise analysis section.\n"
        "Do not add facts not present in the payload.\n"
        "Do not output any 'Evidence:' lines; deterministic citations are appended by code.\n"
        "If uncertain, write 'insufficient data'.\n\n"
        f"{json.dumps(deterministic_payload)}"
    )
    try:
        text, model_used = create_message_with_fallback(
            client=client,
            model_candidates=candidates,
            system_prompt=prompt,
            user_content=user_content,
            max_tokens=config.max_tokens,
        )
        if not text:
            return "LLM synthesis produced no text.", model_used
        # Deterministic citation pipeline: strip any accidental citation lines.
        sanitized_lines = [
            line for line in text.splitlines() if not line.strip().startswith("Evidence:")
        ]
        sanitized_text = "\n".join(sanitized_lines).strip() or "LLM synthesis produced no narrative."
        return sanitized_text, model_used
    except Exception as exc:
        return f"LLM synthesis unavailable: {type(exc).__name__}: {exc}", "error"


def validate_citation_schema(report_content: str) -> None:
    """Fail fast if any Evidence line violates schema."""
    evidence_lines = [
        line.strip()
        for line in report_content.splitlines()
        if line.strip().startswith("Evidence:")
    ]
    pattern = re.compile(
        r"^Evidence:\s*metric:[^;]+;\s*pod:[^;]+;\s*endpoint:[^;]+;\s*source:[^;]+$"
    )
    invalid = [line for line in evidence_lines if not pattern.match(line)]
    if invalid:
        raise ValueError(f"Invalid citation schema lines found: {invalid[:3]}")


def write_report(content: str) -> None:
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(content, encoding="utf-8")


def main() -> None:
    config = build_report_config()
    map_artifact = load_map_artifact()
    placeholders = map_artifact.get("placeholders", {})
    observations = map_artifact.get("observations", [])
    crawl_summary = map_artifact.get("crawl_summary", {})

    dependency_graph = normalize_dependency_graph(placeholders.get("dependency_graph", []))
    supply_graph = normalize_supply_graph(placeholders.get("supply_graph", []))
    discovered_pods = extract_discovered_pods(
        {"crawl_summary": crawl_summary, "placeholders": placeholders}
    )
    endpoint_coverage = compute_endpoint_coverage(observations)
    consistency_checks = build_consistency_checks(
        dependency_graph=dependency_graph,
        supply_graph=supply_graph,
        discovered_pods=discovered_pods,
        endpoint_coverage=endpoint_coverage,
    )
    metrics = compute_dependency_metrics(
        dependency_graph=dependency_graph,
        discovered_pods=discovered_pods,
    )
    timeline = extract_timeline(observations)
    gateway_entrypoint = extract_gateway_entrypoint(observations)
    mermaid_graph = build_mermaid_graph(
        dependency_graph=dependency_graph,
        discovered_pods=discovered_pods,
        gateway_entrypoint=gateway_entrypoint,
    )
    text_graph = build_text_graph(
        dependency_graph=dependency_graph,
        discovered_pods=discovered_pods,
        gateway_entrypoint=gateway_entrypoint,
    )

    deterministic_markdown = build_deterministic_markdown(
        metrics=metrics,
        consistency_checks=consistency_checks,
        timeline=timeline,
    )
    deterministic_payload = {
        "generated_at": utc_now_iso(),
        "metrics": metrics,
        "consistency_checks": consistency_checks,
        "timeline_sample": timeline[:20],
    }
    llm_text, llm_model = synthesize_llm_section(
        client=get_client(),
        config=config,
        deterministic_payload=deterministic_payload,
    )

    report_content = (
        "# Project Selene Infrastructure Report\n\n"
        "## Executive Summary\n"
        "- Report generated from deterministic graph and consistency analysis.\n"
        f"- Discovered pods: `{len(discovered_pods)}`.\n"
        f"- Dependency edges: `{len(dependency_graph)}`; supply edges: `{len(supply_graph)}`.\n"
        f"- LLM synthesis model: `{llm_model}`.\n\n"
        "## Dependency Graph (Mermaid)\n"
        "```mermaid\n"
        f"{mermaid_graph}\n"
        "```\n\n"
        "## Dependency Graph (Text View)\n"
        f"{text_graph}\n\n"
        "## Deterministic Risk and Consistency Analysis\n"
        f"{deterministic_markdown}\n\n"
        "## LLM Synthesis (Grounded)\n"
        "_Narrative only. Evidence citations are deterministic and generated by code._\n\n"
        f"{llm_text}\n\n"
        "## Data Completeness and Verification\n"
        f"- Endpoint coverage gaps: `{consistency_checks['summary']['endpoint_coverage_gap_count']}`.\n"
        f"- Unknown pod references: `{consistency_checks['summary']['unknown_pod_reference_count']}`.\n"
        "- Findings without deterministic evidence are treated as insufficient data.\n"
    )
    validate_citation_schema(report_content)
    write_report(report_content)


if __name__ == "__main__":
    main()
