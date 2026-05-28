import json
import os
import sys
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse, urlunparse

import httpx
from anthropic import Anthropic, NotFoundError

SYSTEM_PROMPT_PATH = Path("/rover/agent/mapping_system_prompt.md")
OUTPUT_PATH = Path("/rover/output/map.json")
DEFAULT_MODEL = "claude-haiku-4-5-20251001"
MODEL_FALLBACKS = (
    "claude-haiku-4-5-20251001",
    "claude-sonnet-4-6",
    "claude-opus-4-7",
)
DEFAULT_MAX_ITERATIONS = 20
DEFAULT_FETCH_TIMEOUT_SECONDS = 10.0
MAX_OBSERVATION_CHARS = 8000
MAX_CONTEXT_BODY_PREVIEW_CHARS = 1200
DEFAULT_CONTEXT_WINDOW_TURNS = 4
DISCOVERY_PORT_START = 3001
DISCOVERY_PORT_END = 3012
REQUIRED_POD_ENDPOINTS = ("/info", "/status", "/dependencies", "/supplies", "/logs", "/comms")
LEARNED_PORT_BY_POD: dict[str, int] = {}


@dataclass
class RunConfig:
    model: str
    max_iterations: int
    gateway_url: str
    fetch_timeout_seconds: float
    context_window_turns: int


def utc_now_iso() -> str:
    return datetime.now(UTC).isoformat()


def build_run_config() -> RunConfig:
    """Build run configuration from environment variables."""
    model = os.getenv("ANTHROPIC_MODEL", DEFAULT_MODEL)
    max_iterations = int(os.getenv("REACT_MAX_ITERATIONS", str(DEFAULT_MAX_ITERATIONS)))
    gateway_url = os.getenv("GATEWAY_URL", "http://gateway:3000")
    fetch_timeout_seconds = float(
        os.getenv("FETCH_TIMEOUT_SECONDS", str(DEFAULT_FETCH_TIMEOUT_SECONDS))
    )
    context_window_turns = int(
        os.getenv("CONTEXT_WINDOW_TURNS", str(DEFAULT_CONTEXT_WINDOW_TURNS))
    )
    return RunConfig(
        model=model,
        max_iterations=max_iterations,
        gateway_url=gateway_url,
        fetch_timeout_seconds=fetch_timeout_seconds,
        context_window_turns=context_window_turns,
    )


def load_system_prompt() -> str:
    if not SYSTEM_PROMPT_PATH.exists():
        raise FileNotFoundError(f"System prompt file not found: {SYSTEM_PROMPT_PATH}")
    return SYSTEM_PROMPT_PATH.read_text(encoding="utf-8")


def get_client() -> Anthropic:
    api_key = os.getenv("LLM_API_KEY")
    if not api_key:
        raise RuntimeError(
            "Missing LLM_API_KEY. Set it before running the mapping agent."
        )
    return Anthropic(api_key=api_key)


def truncate_text(value: str, limit: int = MAX_OBSERVATION_CHARS) -> str:
    if len(value) <= limit:
        return value
    return f"{value[:limit]}\n...[truncated {len(value) - limit} chars]"


def maybe_parse_json(value: str) -> dict[str, Any] | list[Any] | None:
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return None


def register_pod_port_from_url(url: str) -> None:
    """Persist host->port mappings from successful in-network requests."""
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    port = parsed.port
    if not host or port is None:
        return
    if host in {"localhost", "127.0.0.1", "gateway"}:
        return
    LEARNED_PORT_BY_POD[host] = port


def learn_pod_port_mapping_from_json(payload: Any, source_url: str) -> None:
    """Learn pod_id to port mappings from discovered response payloads."""
    parsed_source = urlparse(source_url)
    source_port = parsed_source.port
    source_host = (parsed_source.hostname or "").lower()

    def visit(node: Any) -> None:
        if isinstance(node, dict):
            pod = node.get("pod")
            localhost_url = node.get("url")
            if isinstance(pod, str) and isinstance(localhost_url, str):
                parsed_localhost = urlparse(localhost_url)
                localhost_host = (parsed_localhost.hostname or "").lower()
                localhost_port = parsed_localhost.port
                if localhost_host in {"localhost", "127.0.0.1"} and localhost_port is not None:
                    LEARNED_PORT_BY_POD[pod.lower()] = localhost_port

            pod_id = node.get("id")
            if (
                isinstance(pod_id, str)
                and source_port is not None
                and source_host
                and source_host not in {"localhost", "127.0.0.1"}
                and source_host == pod_id.lower()
            ):
                LEARNED_PORT_BY_POD[pod_id.lower()] = source_port

            for value in node.values():
                visit(value)
        elif isinstance(node, list):
            for item in node:
                visit(item)

    visit(payload)


def normalize_fetch_url(args: dict[str, str]) -> dict[str, Any]:
    """Rewrite requested URLs to canonical in-network service host:port pairs."""
    requested_url = args["requested_url"]
    parsed = urlparse(requested_url)
    host = parsed.hostname
    port = parsed.port
    if host is None:
        return {"requested_url": requested_url, "resolved_url": requested_url}
    host = host.lower()
    learned_host_by_port = {port: pod for pod, port in LEARNED_PORT_BY_POD.items()}

    resolved_host = host
    resolved_port = port
    normalization_reasons: list[str] = []

    if host in {"localhost", "127.0.0.1"} and port is not None:
        learned_host = learned_host_by_port.get(port)
        if learned_host:
            resolved_host = learned_host
            normalization_reasons.append(
                f"rewrote localhost:{port} to learned host {resolved_host}:{port}"
            )

    if resolved_host in LEARNED_PORT_BY_POD:
        canonical_port = LEARNED_PORT_BY_POD[resolved_host]
        if resolved_port is None:
            resolved_port = canonical_port
            normalization_reasons.append(
                f"filled missing learned port for {resolved_host} to {canonical_port}"
            )
        elif resolved_port != canonical_port:
            normalization_reasons.append(
                f"corrected {resolved_host} port {resolved_port} to learned port {canonical_port}"
            )
            resolved_port = canonical_port

    if resolved_host == host and resolved_port == port:
        return {"requested_url": requested_url, "resolved_url": requested_url}

    resolved_netloc = (
        f"{resolved_host}:{resolved_port}"
        if resolved_port is not None
        else resolved_host
    )
    netloc = resolved_netloc
    if parsed.username or parsed.password:
        auth = parsed.username or ""
        if parsed.password:
            auth = f"{auth}:{parsed.password}"
        netloc = f"{auth}@{resolved_netloc}"
    resolved_url = urlunparse(
        (parsed.scheme, netloc, parsed.path, parsed.params, parsed.query, parsed.fragment)
    )
    return {
        "requested_url": requested_url,
        "resolved_url": resolved_url,
        "normalization_note": "; ".join(normalization_reasons),
    }


def normalize_fetch_observation(args: dict[str, Any], response: httpx.Response) -> dict[str, Any]:
    text_body = response.text
    register_pod_port_from_url(args["resolved_url"])
    parsed_json = maybe_parse_json(text_body)
    if parsed_json is not None:
        learn_pod_port_mapping_from_json(parsed_json, args["resolved_url"])
    return {
        "tool": "fetch_url",
        "url": args["resolved_url"],
        "requested_url": args["requested_url"],
        "resolved_url": args["resolved_url"],
        "normalization_note": args.get("normalization_note"),
        "status_code": response.status_code,
        "ok": response.is_success,
        "content_type": response.headers.get("content-type"),
        "body_preview": truncate_text(text_body),
        "json": parsed_json,
    }


def discover_port_for_pod(host: str, client: httpx.Client) -> int | None:
    """Probe known colony port range to learn a pod host's active port."""
    for candidate_port in range(DISCOVERY_PORT_START, DISCOVERY_PORT_END + 1):
        try:
            probe_response = client.get(f"http://{host}:{candidate_port}/info")
            if probe_response.status_code < 500:
                LEARNED_PORT_BY_POD[host] = candidate_port
                return candidate_port
        except Exception:
            continue
    return None


def rebuild_url_with_port(url: str, port: int) -> str:
    parsed = urlparse(url)
    host = parsed.hostname
    if host is None:
        return url
    netloc = f"{host}:{port}"
    if parsed.username or parsed.password:
        auth = parsed.username or ""
        if parsed.password:
            auth = f"{auth}:{parsed.password}"
        netloc = f"{auth}@{netloc}"
    return urlunparse(
        (parsed.scheme, netloc, parsed.path, parsed.params, parsed.query, parsed.fragment)
    )


def run_fetch_tool(args: dict[str, Any], timeout_seconds: float) -> dict[str, Any]:
    """Run the fetch_url tool and normalize output for the model and map artifact."""
    requested_url = str(args.get("url", "")).strip()
    if not requested_url:
        return {"tool": "fetch_url", "error": "Missing required 'url' parameter", "args": args}

    url_details = normalize_fetch_url({"requested_url": requested_url})
    resolved_url = str(url_details["resolved_url"])

    try:
        with httpx.Client(timeout=timeout_seconds) as client:
            try:
                response = client.get(resolved_url)
            except httpx.ConnectError as exc:
                parsed_resolved = urlparse(resolved_url)
                resolved_host = (parsed_resolved.hostname or "").lower()
                discovered_port: int | None = None
                if resolved_host and resolved_host not in {"localhost", "127.0.0.1", "gateway"}:
                    discovered_port = discover_port_for_pod(resolved_host, client)
                if discovered_port is None:
                    raise exc

                resolved_url = rebuild_url_with_port(resolved_url, discovered_port)
                existing_note = url_details.get("normalization_note")
                discovery_note = (
                    f"auto-discovered {resolved_host} port {discovered_port} after connection error"
                )
                url_details["resolved_url"] = resolved_url
                url_details["normalization_note"] = (
                    f"{existing_note}; {discovery_note}" if existing_note else discovery_note
                )
                response = client.get(resolved_url)
        return normalize_fetch_observation(url_details, response)
    except Exception as exc:
        return {
            "tool": "fetch_url",
            "url": resolved_url,
            "requested_url": requested_url,
            "resolved_url": resolved_url,
            "normalization_note": url_details.get("normalization_note"),
            "error": f"{type(exc).__name__}: {exc}",
        }


def extract_text_blocks(content: list[Any]) -> str:
    text_parts: list[str] = []
    for block in content:
        if getattr(block, "type", None) == "text":
            text_parts.append(block.text)
    return "\n".join(text_parts).strip()


def extract_endpoint_path(url: str) -> str:
    parsed = urlparse(url)
    return parsed.path or "/"


def compact_observation_for_context(observation: dict[str, Any]) -> dict[str, Any]:
    """Trim tool output before feeding it back into model context."""
    compact = {
        "tool": observation.get("tool"),
        "url": observation.get("url"),
        "requested_url": observation.get("requested_url"),
        "resolved_url": observation.get("resolved_url"),
        "normalization_note": observation.get("normalization_note"),
        "status_code": observation.get("status_code"),
        "ok": observation.get("ok"),
        "error": observation.get("error"),
    }
    body_preview = observation.get("body_preview")
    if isinstance(body_preview, str):
        compact["body_preview"] = truncate_text(
            body_preview, limit=MAX_CONTEXT_BODY_PREVIEW_CHARS
        )
    return compact


def extract_pod_ids_from_observation(observation: dict[str, Any]) -> set[str]:
    """Extract discovered pod IDs from a single tool observation."""
    pod_ids: set[str] = set()
    payload = observation.get("json")
    if not isinstance(payload, dict):
        return pod_ids

    pod_id = payload.get("id")
    if isinstance(pod_id, str):
        pod_ids.add(pod_id.lower())

    entrypoint = payload.get("entrypoint")
    if isinstance(entrypoint, dict):
        entrypoint_pod = entrypoint.get("pod")
        if isinstance(entrypoint_pod, str):
            pod_ids.add(entrypoint_pod.lower())

    for field in ("dependencies", "supplies"):
        items = payload.get(field)
        if isinstance(items, list):
            for item in items:
                if isinstance(item, dict):
                    item_pod_id = item.get("pod_id")
                    if isinstance(item_pod_id, str):
                        pod_ids.add(item_pod_id.lower())

    return pod_ids


def record_covered_endpoint_from_observation(
    observation: dict[str, Any], fetched_endpoints_by_pod: dict[str, set[str]]
) -> None:
    """Track endpoint coverage for each pod host."""
    url = observation.get("resolved_url") or observation.get("url")
    if not isinstance(url, str):
        return
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    if not host or host in {"localhost", "127.0.0.1", "gateway"}:
        return
    path = parsed.path or "/"
    if path in REQUIRED_POD_ENDPOINTS:
        fetched_endpoints_by_pod.setdefault(host, set()).add(path)


def is_crawl_complete(
    discovered_pods: set[str], fetched_endpoints_by_pod: dict[str, set[str]]
) -> bool:
    """Return True when all discovered pods have all required endpoint coverage."""
    if len(discovered_pods) < 12:
        return False
    required = set(REQUIRED_POD_ENDPOINTS)
    for pod in discovered_pods:
        if not required.issubset(fetched_endpoints_by_pod.get(pod, set())):
            return False
    return True


def auto_fetch_required_endpoints(
    discovered_pods: set[str],
    fetched_endpoints_by_pod: dict[str, set[str]],
    timeout_seconds: float,
) -> list[dict[str, Any]]:
    """Deterministically crawl missing required endpoints for discovered pods."""
    new_observations: list[dict[str, Any]] = []
    required = set(REQUIRED_POD_ENDPOINTS)

    # Loop until no additional endpoint fetches are scheduled.
    while True:
        scheduled = False
        for pod in sorted(discovered_pods):
            if pod not in LEARNED_PORT_BY_POD:
                with httpx.Client(timeout=timeout_seconds) as client:
                    discover_port_for_pod(pod, client)
            port = LEARNED_PORT_BY_POD.get(pod)
            if port is None:
                continue

            covered = fetched_endpoints_by_pod.get(pod, set())
            missing = sorted(required - covered)
            for endpoint in missing:
                scheduled = True
                requested_url = f"http://{pod}:{port}{endpoint}"
                observation = run_fetch_tool({"url": requested_url}, timeout_seconds)
                new_observations.append(observation)
                record_covered_endpoint_from_observation(
                    observation, fetched_endpoints_by_pod
                )
                discovered_pods.update(extract_pod_ids_from_observation(observation))

        if not scheduled:
            break

    return new_observations


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


def build_memory_summary(
    visited_urls: set[str], observations: list[dict[str, Any]]
) -> dict[str, Any]:
    """Build compact state memory for the next model turn."""
    endpoint_coverage: dict[str, dict[str, int]] = {}
    error_count = 0
    success_count = 0
    not_found_count = 0

    for observation in observations:
        url = observation.get("url")
        if not isinstance(url, str):
            continue
        endpoint_path = extract_endpoint_path(url)
        endpoint_stats = endpoint_coverage.setdefault(
            endpoint_path, {"success": 0, "not_found": 0, "other_error": 0}
        )
        status_code = observation.get("status_code")
        if isinstance(status_code, int):
            if 200 <= status_code < 300:
                endpoint_stats["success"] += 1
                success_count += 1
            elif status_code == 404:
                endpoint_stats["not_found"] += 1
                not_found_count += 1
            else:
                endpoint_stats["other_error"] += 1
                error_count += 1
        elif observation.get("error"):
            endpoint_stats["other_error"] += 1
            error_count += 1

    return {
        "visited_url_count": len(visited_urls),
        "visited_urls_sample": sorted(visited_urls)[:30],
        "observation_count": len(observations),
        "success_count": success_count,
        "not_found_count": not_found_count,
        "error_count": error_count,
        "endpoint_coverage": endpoint_coverage,
    }


def build_messages_for_model(
    seed_user_content: str,
    memory_summary: dict[str, Any],
    turn_history: list[dict[str, Any]],
    context_window_turns: int,
) -> list[dict[str, Any]]:
    """Compose bounded conversation context with rolling history."""
    bounded_history = turn_history[-(context_window_turns * 2):]
    memory_message = {
        "role": "user",
        "content": (
            "Current crawl memory summary (use this to avoid redundant calls):\n"
            f"{json.dumps(memory_summary)}"
        ),
    }
    return [
        {"role": "user", "content": seed_user_content},
        memory_message,
        *bounded_history,
    ]


def build_model_candidates(configured_model: str) -> list[str]:
    """Build ordered model candidates with env-configured model first."""
    candidates = [configured_model]
    for fallback in MODEL_FALLBACKS:
        if fallback not in candidates:
            candidates.append(fallback)
    return candidates


def list_available_models(client: Anthropic) -> list[str]:
    """Return available model IDs for the current API key."""
    try:
        models = client.models.list(limit=50)
        return [model.id for model in models.data]
    except Exception as exc:
        print(
            f"[mapping] Could not list available models: {type(exc).__name__}: {exc}",
            file=sys.stderr,
        )
        return []


def create_message_with_fallback(
    client: Anthropic,
    model_candidates: list[str],
    system_prompt: str,
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]],
) -> tuple[Any, str]:
    """Try Anthropic message creation across configured model fallbacks."""
    last_not_found_error: NotFoundError | None = None
    for model in model_candidates:
        try:
            response = client.messages.create(
                model=model,
                max_tokens=1200,
                system=system_prompt,
                messages=messages,
                tools=tools,
            )
            return response, model
        except NotFoundError as exc:
            last_not_found_error = exc
            print(
                f"[mapping] Model unavailable, trying fallback: {model}",
                file=sys.stderr,
            )

    if last_not_found_error is not None:
        raise last_not_found_error

    raise RuntimeError("No model candidates available for Anthropic request.")


def run_mapping() -> dict[str, Any]:
    config = build_run_config()
    system_prompt = load_system_prompt()
    client = get_client()
    started_at = utc_now_iso()
    available_models = list_available_models(client)
    model_candidates = build_model_candidates(config.model)
    for available_model in available_models:
        if available_model not in model_candidates:
            model_candidates.append(available_model)
    selected_model = config.model

    tools = [
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

    seed_user_content = (
        "Begin your mapping run now.\n"
        f"Gateway URL: {config.gateway_url}\n"
        "Remember to use the fetch_url tool for network exploration."
    )
    turn_history: list[dict[str, Any]] = []

    visited_urls: set[str] = set()
    observations: list[dict[str, Any]] = []
    assistant_trace: list[str] = []
    discovered_pods: set[str] = set()
    fetched_endpoints_by_pod: dict[str, set[str]] = {}
    completion_reason = "max_iterations_reached"
    iterations_run = 0
    run_started_monotonic = time.monotonic()

    for iteration in range(1, config.max_iterations + 1):
        iterations_run = iteration
        memory_summary = build_memory_summary(visited_urls, observations)
        elapsed_seconds = time.monotonic() - run_started_monotonic
        print(
            (
                f"[mapping] Iteration {iteration}/{config.max_iterations} "
                f"elapsed={elapsed_seconds:.1f}s "
                f"visited={memory_summary['visited_url_count']} "
                f"observations={memory_summary['observation_count']} "
                f"errors={memory_summary['error_count']} "
                f"history_messages={len(turn_history)}"
            ),
            file=sys.stderr,
        )
        messages_for_model = build_messages_for_model(
            seed_user_content=seed_user_content,
            memory_summary=memory_summary,
            turn_history=turn_history,
            context_window_turns=config.context_window_turns,
        )
        response, selected_model = create_message_with_fallback(
            client=client,
            model_candidates=model_candidates,
            system_prompt=system_prompt,
            messages=messages_for_model,
            tools=tools,
        )

        assistant_message: dict[str, Any] = {
            "role": "assistant",
            "content": [
                block.model_dump()
                for block in response.content
            ],
        }
        turn_history.append(assistant_message)
        print(
            (
                f"[mapping] Model response received "
                f"model={selected_model} "
                f"stop_reason={getattr(response, 'stop_reason', 'unknown')}"
            ),
            file=sys.stderr,
        )

        assistant_text = extract_text_blocks(response.content)
        if assistant_text:
            assistant_trace.append(assistant_text)

        tool_use_blocks = [block for block in response.content if block.type == "tool_use"]
        if not tool_use_blocks:
            if "action: complete" in assistant_text.lower():
                completion_reason = "model_declared_complete"
            else:
                completion_reason = "no_tool_request"
            print(
                (
                    f"[mapping] No tool call requested; completion_reason={completion_reason}. "
                    f"assistant_preview={truncate_text(assistant_text, 220)}"
                ),
                file=sys.stderr,
            )
            break

        tool_results_for_model: list[dict[str, Any]] = []
        for block in tool_use_blocks:
            if block.name != "fetch_url":
                result_payload = {
                    "tool": block.name,
                    "error": f"Unsupported tool '{block.name}'",
                }
            else:
                result_payload = run_fetch_tool(block.input, config.fetch_timeout_seconds)
                url = result_payload.get("url")
                if isinstance(url, str) and url:
                    visited_urls.add(url)
                observations.append(result_payload)
                discovered_pods.update(extract_pod_ids_from_observation(result_payload))
                record_covered_endpoint_from_observation(
                    result_payload, fetched_endpoints_by_pod
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
            discovered_pods=discovered_pods,
            fetched_endpoints_by_pod=fetched_endpoints_by_pod,
            timeout_seconds=config.fetch_timeout_seconds,
        )
        for observation in auto_observations:
            url = observation.get("url")
            if isinstance(url, str) and url:
                visited_urls.add(url)
            observations.append(observation)
            discovered_pods.update(extract_pod_ids_from_observation(observation))
            record_covered_endpoint_from_observation(
                observation, fetched_endpoints_by_pod
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

        turn_history.append({"role": "user", "content": tool_results_for_model})
        print(
            (
                f"[mapping] Iteration {iteration} complete "
                f"tool_calls={len(tool_use_blocks)} "
                f"visited_total={len(visited_urls)} "
                f"observations_total={len(observations)} "
                f"discovered_pods={len(discovered_pods)}"
            ),
            file=sys.stderr,
        )

        if is_crawl_complete(discovered_pods, fetched_endpoints_by_pod):
            completion_reason = "auto_crawl_complete"
            print("[mapping] Auto crawl reached full discovered coverage.", file=sys.stderr)
            break

        if "action: complete" in assistant_text.lower():
            completion_reason = "model_declared_complete"
            print("[mapping] Model declared complete in assistant text.", file=sys.stderr)
            break

    finished_at = utc_now_iso()
    dependency_graph = build_dependency_graph(observations)
    supply_graph = build_supply_graph(observations)
    artifact = {
        "metadata": {
            "started_at": started_at,
            "finished_at": finished_at,
            "model": selected_model,
            "gateway_url": config.gateway_url,
            "iterations_run": iterations_run,
            "max_iterations": config.max_iterations,
            "context_window_turns": config.context_window_turns,
            "completion_reason": completion_reason,
        },
        "crawl_summary": {
            "visited_url_count": len(visited_urls),
            "visited_urls": sorted(visited_urls),
            "observation_count": len(observations),
            "discovered_pod_count": len(discovered_pods),
            "discovered_pods": sorted(discovered_pods),
        },
        "observations": observations,
        "assistant_trace": assistant_trace,
        "placeholders": {
            "pods": sorted(discovered_pods),
            "dependency_graph": dependency_graph,
            "supply_graph": supply_graph,
            "risk_signals": [],
        },
    }
    return artifact


def write_map_artifact(artifact: dict[str, Any]) -> None:
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(json.dumps(artifact, indent=2), encoding="utf-8")


def main() -> None:
    artifact = run_mapping()
    write_map_artifact(artifact)
    print(
        f"[mapping] Wrote scaffold map artifact to {OUTPUT_PATH}",
        file=sys.stderr,
    )


if __name__ == "__main__":
    main()
