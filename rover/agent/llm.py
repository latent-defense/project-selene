"""Provider-agnostic LLM tool-use loop (DDL-004).

Detects the provider from the LLM_API_KEY prefix and runs the same agentic loop
on Anthropic or OpenAI. The agent calls tools (tools.py) to investigate the
colony; tool results come from the deterministic engine.
"""
import json
import logging
import os
import time

from . import tools

log = logging.getLogger("llm")

MAX_TURNS = 16
MAX_TOKENS = 16000  # final report is long; 4096 truncated it mid-section
REQUEST_TIMEOUT_S = float(os.environ.get("LLM_REQUEST_TIMEOUT_S", "60"))  # final narration runs long
AGENT_DEADLINE_S = float(os.environ.get("LLM_DEADLINE_S", "120"))


def detect_provider(api_key: str) -> str:
    return "anthropic" if (api_key or "").startswith("sk-ant-") else "openai"


def default_model(provider: str) -> str:
    if os.environ.get("LLM_MODEL"):
        return os.environ["LLM_MODEL"]
    return "claude-sonnet-4-6" if provider == "anthropic" else "gpt-4o"


def run_agent(system: str, user_prompt: str, engine) -> str:
    """Run the tool-use loop and return the model's final text."""
    api_key = os.environ.get("LLM_API_KEY")
    if not api_key:
        raise RuntimeError("LLM_API_KEY is not set")
    provider = detect_provider(api_key)
    model = default_model(provider)
    log.info("LLM provider=%s model=%s", provider, model)

    def call_tool(name, args):
        log.info("tool call: %s(%s)", name, json.dumps(args)[:200])
        result = tools.dispatch(engine, name, args)
        return json.dumps(result, default=str)

    deadline = time.monotonic() + AGENT_DEADLINE_S
    if provider == "anthropic":
        return _run_anthropic(api_key, model, system, user_prompt, call_tool, deadline)
    return _run_openai(api_key, model, system, user_prompt, call_tool, deadline)


def _remaining_timeout(deadline: float) -> float:
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise TimeoutError("LLM agent exceeded overall deadline")
    return min(REQUEST_TIMEOUT_S, remaining)


def _run_anthropic(api_key, model, system, user_prompt, call_tool, deadline):
    from anthropic import Anthropic
    client = Anthropic(api_key=api_key, timeout=REQUEST_TIMEOUT_S, max_retries=0)
    spec = [{"name": t["name"], "description": t["description"], "input_schema": t["input_schema"]}
            for t in tools.TOOL_SPECS]
    messages = [{"role": "user", "content": user_prompt}]

    for _ in range(MAX_TURNS):
        timeout = _remaining_timeout(deadline)
        resp = client.messages.create(model=model, max_tokens=MAX_TOKENS, system=system,
                                      tools=spec, messages=messages, timeout=timeout)
        messages.append({"role": "assistant", "content": resp.content})
        if resp.stop_reason != "tool_use":
            return "".join(b.text for b in resp.content if b.type == "text")
        tool_results = []
        for block in resp.content:
            if block.type == "tool_use":
                out = call_tool(block.name, block.input)
                tool_results.append({"type": "tool_result", "tool_use_id": block.id, "content": out})
        messages.append({"role": "user", "content": tool_results})
    raise RuntimeError("anthropic agent exceeded MAX_TURNS without finishing")


def _run_openai(api_key, model, system, user_prompt, call_tool, deadline):
    from openai import OpenAI
    client = OpenAI(api_key=api_key, timeout=REQUEST_TIMEOUT_S, max_retries=0)
    spec = [{"type": "function", "function": {"name": t["name"], "description": t["description"],
                                              "parameters": t["input_schema"]}}
            for t in tools.TOOL_SPECS]
    messages = [{"role": "system", "content": system}, {"role": "user", "content": user_prompt}]

    for _ in range(MAX_TURNS):
        timeout = _remaining_timeout(deadline)
        resp = client.chat.completions.create(model=model, messages=messages, tools=spec, timeout=timeout)
        msg = resp.choices[0].message
        if not msg.tool_calls:
            return msg.content or ""
        messages.append({"role": "assistant", "content": msg.content, "tool_calls": msg.tool_calls})
        for tc in msg.tool_calls:
            args = json.loads(tc.function.arguments or "{}")
            out = call_tool(tc.function.name, args)
            messages.append({"role": "tool", "tool_call_id": tc.id, "content": out})
    raise RuntimeError("openai agent exceeded MAX_TURNS without finishing")
