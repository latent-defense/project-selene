import json
import sys
import time
from typing import Any

from .artifact import build_dependency_graph, build_supply_graph
from .auto_crawl import auto_fetch_required_endpoints
from .discovery import LEARNED_PORT_BY_POD
from .discovery import (
    extract_pod_ids_from_observation,
    is_crawl_complete,
    record_covered_endpoint_from_observation,
)
from .io import load_system_prompt
from .llm_planner import (
    build_memory_summary,
    build_messages_for_model,
    build_model_candidates,
    build_run_config,
    compact_observation_for_context,
    create_message_with_fallback,
    extract_text_blocks,
    get_client,
    list_available_models,
    utc_now_iso,
)
from .tool_executor import run_fetch_tool, truncate_text
from .models import MappingContext, MappingState


def handle_init(context: MappingContext) -> MappingState:
    """Prepare runtime dependencies and immutable run configuration."""
    LEARNED_PORT_BY_POD.clear()
    context.config = build_run_config()
    context.system_prompt = load_system_prompt()
    context.client = get_client()
    context.started_at = utc_now_iso()
    available_models = list_available_models(context.client)
    context.model_candidates = build_model_candidates(context.config["model"])
    for available_model in available_models:
        if available_model not in context.model_candidates:
            context.model_candidates.append(available_model)
    context.selected_model = context.config["model"]
    context.tools = [
        {
            "name": "fetch_url",
            "description": "Make an HTTP GET request and return status + response body.",
            "input_schema": {
                "type": "object",
                "properties": {"url": {"type": "string"}},
                "required": ["url"],
                "additionalProperties": False,
            },
        }
    ]
    context.seed_user_content = (
        "Begin your mapping run now.\n"
        f"Gateway URL: {context.config['gateway_url']}\n"
        "Remember to use the fetch_url tool for network exploration."
    )
    context.run_started_monotonic = time.monotonic()
    context.completion_reason = "max_iterations_reached"
    return MappingState.DISCOVER


def handle_discover(context: MappingContext) -> MappingState:
    """Run one planner turn and decide whether tool execution is needed."""
    if context.iterations_run >= context.config["max_iterations"]:
        context.completion_reason = "max_iterations_reached"
        return MappingState.FINALIZE_ARTIFACT

    context.iterations_run += 1
    iteration = context.iterations_run
    memory_summary = build_memory_summary(context.visited_urls, context.observations)
    elapsed_seconds = time.monotonic() - context.run_started_monotonic
    print(
        (
            f"[mapping] Iteration {iteration}/{context.config['max_iterations']} "
            f"elapsed={elapsed_seconds:.1f}s "
            f"visited={memory_summary['visited_url_count']} "
            f"observations={memory_summary['observation_count']} "
            f"errors={memory_summary['error_count']} "
            f"history_messages={len(context.turn_history)}"
        ),
        file=sys.stderr,
    )
    messages_for_model = build_messages_for_model(
        seed_user_content=context.seed_user_content,
        memory_summary=memory_summary,
        turn_history=context.turn_history,
        context_window_turns=context.config["context_window_turns"],
    )
    response, context.selected_model = create_message_with_fallback(
        client=context.client,
        model_candidates=context.model_candidates,
        system_prompt=context.system_prompt,
        messages=messages_for_model,
        tools=context.tools,
    )
    assistant_message: dict[str, Any] = {
        "role": "assistant",
        "content": [block.model_dump() for block in response.content],
    }
    context.turn_history.append(assistant_message)
    print(
        (
            f"[mapping] Model response received "
            f"model={context.selected_model} "
            f"stop_reason={getattr(response, 'stop_reason', 'unknown')}"
        ),
        file=sys.stderr,
    )

    assistant_text = extract_text_blocks(response.content)
    context.last_assistant_text = assistant_text
    if assistant_text:
        context.assistant_trace.append(assistant_text)

    context.pending_tool_use_blocks = [
        block for block in response.content if block.type == "tool_use"
    ]
    if not context.pending_tool_use_blocks:
        if "action: complete" in assistant_text.lower():
            context.completion_reason = "model_declared_complete"
        else:
            context.completion_reason = "no_tool_request"
        print(
            (
                f"[mapping] No tool call requested; completion_reason={context.completion_reason}. "
                f"assistant_preview={truncate_text(assistant_text, 220)}"
            ),
            file=sys.stderr,
        )
        return MappingState.FINALIZE_ARTIFACT
    return MappingState.COLLECT


def handle_collect(context: MappingContext) -> MappingState:
    """Execute tool calls, deterministic auto-crawl, and completion checks."""
    tool_results_for_model: list[dict[str, Any]] = []
    for block in context.pending_tool_use_blocks:
        if block.name != "fetch_url":
            result_payload = {
                "tool": block.name,
                "error": f"Unsupported tool '{block.name}'",
            }
        else:
            result_payload = run_fetch_tool(
                block.input, context.config["fetch_timeout_seconds"]
            )
            url = result_payload.get("url")
            if isinstance(url, str) and url:
                context.visited_urls.add(url)
            context.observations.append(result_payload)
            context.discovered_pods.update(extract_pod_ids_from_observation(result_payload))
            record_covered_endpoint_from_observation(
                result_payload, context.fetched_endpoints_by_pod
            )
            requested_url = result_payload.get("requested_url")
            resolved_url = result_payload.get("resolved_url", url)
            normalization_note = result_payload.get("normalization_note")
            print(
                (
                    f"[mapping] Tool fetch_url "
                    f"requested_url={requested_url or 'missing'} "
                    f"resolved_url={resolved_url or 'missing'} "
                    f"status={result_payload.get('status_code', 'n/a')} "
                    f"error={result_payload.get('error', 'none')} "
                    f"normalization={normalization_note or 'none'}"
                ),
                file=sys.stderr,
            )

        tool_results_for_model.append(
            {
                "type": "tool_result",
                "tool_use_id": block.id,
                "content": json.dumps(compact_observation_for_context(result_payload)),
            }
        )

    auto_observations = auto_fetch_required_endpoints(
        discovered_pods=context.discovered_pods,
        fetched_endpoints_by_pod=context.fetched_endpoints_by_pod,
        timeout_seconds=context.config["fetch_timeout_seconds"],
    )
    for observation in auto_observations:
        url = observation.get("url")
        if isinstance(url, str) and url:
            context.visited_urls.add(url)
        context.observations.append(observation)
        context.discovered_pods.update(extract_pod_ids_from_observation(observation))
        record_covered_endpoint_from_observation(
            observation, context.fetched_endpoints_by_pod
        )
        print(
            (
                "[mapping] Auto fetch "
                f"requested_url={observation.get('requested_url', 'missing')} "
                f"resolved_url={observation.get('resolved_url', 'missing')} "
                f"status={observation.get('status_code', 'n/a')} "
                f"error={observation.get('error', 'none')}"
            ),
            file=sys.stderr,
        )

    context.turn_history.append({"role": "user", "content": tool_results_for_model})
    print(
        (
            f"[mapping] Iteration {context.iterations_run} complete "
            f"tool_calls={len(context.pending_tool_use_blocks)} "
            f"visited_total={len(context.visited_urls)} "
            f"observations_total={len(context.observations)} "
            f"discovered_pods={len(context.discovered_pods)}"
        ),
        file=sys.stderr,
    )
    context.pending_tool_use_blocks = []

    if is_crawl_complete(context.discovered_pods, context.fetched_endpoints_by_pod):
        context.completion_reason = "auto_crawl_complete"
        print(
            "[mapping] Auto crawl reached full discovered coverage.",
            file=sys.stderr,
        )
        return MappingState.FINALIZE_ARTIFACT

    if "action: complete" in context.last_assistant_text.lower():
        context.completion_reason = "model_declared_complete"
        print("[mapping] Model declared complete in assistant text.", file=sys.stderr)
        return MappingState.FINALIZE_ARTIFACT

    return MappingState.DISCOVER


def handle_finalize_artifact(context: MappingContext) -> MappingState:
    """Finalize deterministic crawl artifact from accumulated context."""
    context.finished_at = utc_now_iso()
    dependency_graph = build_dependency_graph(context.observations)
    supply_graph = build_supply_graph(context.observations)
    context.artifact = {
        "metadata": {
            "started_at": context.started_at,
            "finished_at": context.finished_at,
            "model": context.selected_model,
            "gateway_url": context.config["gateway_url"],
            "iterations_run": context.iterations_run,
            "max_iterations": context.config["max_iterations"],
            "context_window_turns": context.config["context_window_turns"],
            "completion_reason": context.completion_reason,
        },
        "crawl_summary": {
            "visited_url_count": len(context.visited_urls),
            "visited_urls": sorted(context.visited_urls),
            "observation_count": len(context.observations),
            "discovered_pod_count": len(context.discovered_pods),
            "discovered_pods": sorted(context.discovered_pods),
        },
        "observations": context.observations,
        "assistant_trace": context.assistant_trace,
        "placeholders": {
            "pods": sorted(context.discovered_pods),
            "dependency_graph": dependency_graph,
            "supply_graph": supply_graph,
            "risk_signals": [],
        },
    }
    return MappingState.COMPLETE
