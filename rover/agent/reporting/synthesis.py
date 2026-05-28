"""LLM narrative synthesis helpers."""

import json
import os
from pathlib import Path
from typing import Any

from anthropic import Anthropic, NotFoundError

from .models import ReportConfig

SYSTEM_PROMPT_PATH = Path("/rover/agent/reporting_system_prompt.md")
DEFAULT_MODEL = "claude-haiku-4-5-20251001"
MODEL_FALLBACKS = ("claude-haiku-4-5-20251001", "claude-sonnet-4-6", "claude-opus-4-7")


def load_system_prompt() -> str:
    if not SYSTEM_PROMPT_PATH.exists():
        raise FileNotFoundError(
            f"Reporting system prompt file not found: {SYSTEM_PROMPT_PATH}"
        )
    return SYSTEM_PROMPT_PATH.read_text(encoding="utf-8")


def build_report_config() -> ReportConfig:
    model = os.getenv(
        "ANTHROPIC_REPORT_MODEL", os.getenv("ANTHROPIC_MODEL", DEFAULT_MODEL)
    )
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
                block.text
                for block in response.content
                if getattr(block, "type", None) == "text"
            ).strip()
            return text, model
        except NotFoundError as exc:
            last_not_found_error = exc
    if last_not_found_error is not None:
        raise last_not_found_error
    raise RuntimeError("No available model for reporting synthesis.")


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
        sanitized_lines = [
            line
            for line in text.splitlines()
            if not line.strip().startswith("Evidence:")
        ]
        sanitized_text = (
            "\n".join(sanitized_lines).strip() or "LLM synthesis produced no narrative."
        )
        return sanitized_text, model_used
    except Exception as exc:
        return f"LLM synthesis unavailable: {type(exc).__name__}: {exc}", "error"


__all__ = ["build_report_config", "get_client", "synthesize_llm_section"]
