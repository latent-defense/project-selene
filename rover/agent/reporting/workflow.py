from .consistency import build_consistency_checks, compute_endpoint_coverage
from .graph import (
    build_mermaid_graph,
    build_text_graph,
    extract_discovered_pods,
    extract_gateway_entrypoint,
    normalize_dependency_graph,
    normalize_supply_graph,
)
from .io import load_map_artifact, write_report
from .metrics import compute_dependency_metrics
from .models import ReportingContext, ReportingState
from .render import assemble_report, build_deterministic_markdown, utc_now_iso
from .synthesis import build_report_config, get_client, synthesize_llm_section
from .timeline import extract_timeline
from .validation import validate_citation_schema


def handle_load_and_normalize(context: ReportingContext) -> ReportingState:
    """Load map artifact and normalize baseline graph inputs."""
    context.config = build_report_config()
    context.map_artifact = load_map_artifact()
    context.placeholders = context.map_artifact.get("placeholders", {})
    context.observations = context.map_artifact.get("observations", [])
    context.crawl_summary = context.map_artifact.get("crawl_summary", {})
    context.dependency_graph = normalize_dependency_graph(
        context.placeholders.get("dependency_graph", [])
    )
    context.supply_graph = normalize_supply_graph(
        context.placeholders.get("supply_graph", [])
    )
    context.discovered_pods = extract_discovered_pods(
        {
            "crawl_summary": context.crawl_summary,
            "placeholders": context.placeholders,
        }
    )
    context.endpoint_coverage = compute_endpoint_coverage(context.observations)
    return ReportingState.ANALYZE


def handle_analyze(context: ReportingContext) -> ReportingState:
    """Compute deterministic analysis artifacts and render graph views."""
    context.consistency_checks = build_consistency_checks(
        dependency_graph=context.dependency_graph,
        supply_graph=context.supply_graph,
        discovered_pods=context.discovered_pods,
        endpoint_coverage=context.endpoint_coverage,
    )
    context.metrics = compute_dependency_metrics(
        dependency_graph=context.dependency_graph,
        discovered_pods=context.discovered_pods,
    )
    context.timeline = extract_timeline(context.observations)
    context.gateway_entrypoint = extract_gateway_entrypoint(context.observations)
    context.mermaid_graph = build_mermaid_graph(
        dependency_graph=context.dependency_graph,
        discovered_pods=context.discovered_pods,
        gateway_entrypoint=context.gateway_entrypoint,
    )
    context.text_graph = build_text_graph(
        dependency_graph=context.dependency_graph,
        discovered_pods=context.discovered_pods,
        gateway_entrypoint=context.gateway_entrypoint,
    )
    context.deterministic_markdown = build_deterministic_markdown(
        metrics=context.metrics,
        consistency_checks=context.consistency_checks,
        timeline=context.timeline,
    )
    context.deterministic_payload = {
        "generated_at": utc_now_iso(),
        "metrics": context.metrics,
        "consistency_checks": context.consistency_checks,
        "timeline_sample": context.timeline[:20],
    }
    return ReportingState.SYNTHESIZE_NARRATIVE


def handle_synthesize_narrative(context: ReportingContext) -> ReportingState:
    """Generate narrative section and assemble full report content."""
    context.llm_text, context.llm_model = synthesize_llm_section(
        client=get_client(),
        config=context.config,
        deterministic_payload=context.deterministic_payload,
    )
    context.report_content = assemble_report(
        {
            "discovered_pod_count": len(context.discovered_pods),
            "dependency_edge_count": len(context.dependency_graph),
            "supply_edge_count": len(context.supply_graph),
            "llm_model": context.llm_model,
            "mermaid_graph": context.mermaid_graph,
            "text_graph": context.text_graph,
            "deterministic_markdown": context.deterministic_markdown,
            "llm_text": context.llm_text,
            "consistency_checks": context.consistency_checks,
        }
    )
    return ReportingState.VALIDATE


def handle_validate(context: ReportingContext) -> ReportingState:
    """Apply deterministic report validation gates before write."""
    validate_citation_schema(context.report_content)
    return ReportingState.WRITE


def handle_write(context: ReportingContext) -> ReportingState:
    """Persist report output artifact to disk."""
    write_report(context.report_content)
    context.report_length = len(context.report_content)
    return ReportingState.COMPLETE
