"""LLM planner helpers for mapping state machine."""

import json
import os
import sys
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlparse

from anthropic import Anthropic, NotFoundError

from .tool_executor import truncate_text

DEFAULT_MODEL = "claude-haiku-4-5-20251001"
MODEL_FALLBACKS = ("claude-haiku-4-5-20251001", "claude-sonnet-4-6", "claude-opus-4-7")
DEFAULT_MAX_ITERATIONS = 20
DEFAULT_FETCH_TIMEOUT_SECONDS = 10.0
MAX_CONTEXT_BODY_PREVIEW_CHARS = 1200
DEFAULT_CONTEXT_WINDOW_TURNS = 4


def utc_now_iso() -> str:
    return datetime.now(UTC).isoformat()


def build_run_config() -> dict[str, Any]:
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
    return {
        "model": model,
        "max_iterations": max_iterations,
        "gateway_url": gateway_url,
        "fetch_timeout_seconds": fetch_timeout_seconds,
        "context_window_turns": context_window_turns,
    }


def get_client() -> Anthropic:
    api_key = os.getenv("LLM_API_KEY")
    if not api_key:
        raise RuntimeError(
            "Missing LLM_API_KEY. Set it before running the mapping agent."
        )
    return Anthropic(api_key=api_key)


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
    bounded_history = turn_history[-(context_window_turns * 2) :]
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


__all__ = [
    "build_memory_summary",
    "build_messages_for_model",
    "build_model_candidates",
    "build_run_config",
    "compact_observation_for_context",
    "create_message_with_fallback",
    "extract_text_blocks",
    "get_client",
    "list_available_models",
    "utc_now_iso",
]
