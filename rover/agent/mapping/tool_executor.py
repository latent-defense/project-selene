"""Tool execution wrappers for mapping state machine."""

import json
from typing import Any
from urllib.parse import urlparse, urlunparse

import httpx

from .discovery import (
    LEARNED_PORT_BY_POD,
    learn_pod_port_mapping_from_json,
    register_pod_port_from_url,
)

DISCOVERY_PORT_START = 3001
DISCOVERY_PORT_END = 3012
MAX_OBSERVATION_CHARS = 8000


def truncate_text(value: str, limit: int = MAX_OBSERVATION_CHARS) -> str:
    if len(value) <= limit:
        return value
    return f"{value[:limit]}\n...[truncated {len(value) - limit} chars]"


def maybe_parse_json(value: str) -> dict[str, Any] | list[Any] | None:
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return None


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
        (
            parsed.scheme,
            netloc,
            parsed.path,
            parsed.params,
            parsed.query,
            parsed.fragment,
        )
    )
    return {
        "requested_url": requested_url,
        "resolved_url": resolved_url,
        "normalization_note": "; ".join(normalization_reasons),
    }


def normalize_fetch_observation(
    args: dict[str, Any], response: httpx.Response
) -> dict[str, Any]:
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
        (
            parsed.scheme,
            netloc,
            parsed.path,
            parsed.params,
            parsed.query,
            parsed.fragment,
        )
    )


def run_fetch_tool(args: dict[str, Any], timeout_seconds: float) -> dict[str, Any]:
    """Run the fetch_url tool and normalize output for the model and map artifact."""
    requested_url = str(args.get("url", "")).strip()
    if not requested_url:
        return {
            "tool": "fetch_url",
            "error": "Missing required 'url' parameter",
            "args": args,
        }

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
                if resolved_host and resolved_host not in {
                    "localhost",
                    "127.0.0.1",
                    "gateway",
                }:
                    discovered_port = discover_port_for_pod(resolved_host, client)
                if discovered_port is None:
                    raise exc

                resolved_url = rebuild_url_with_port(resolved_url, discovered_port)
                existing_note = url_details.get("normalization_note")
                discovery_note = f"auto-discovered {resolved_host} port {discovered_port} after connection error"
                url_details["resolved_url"] = resolved_url
                url_details["normalization_note"] = (
                    f"{existing_note}; {discovery_note}"
                    if existing_note
                    else discovery_note
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


__all__ = [
    "discover_port_for_pod",
    "normalize_fetch_observation",
    "normalize_fetch_url",
    "rebuild_url_with_port",
    "run_fetch_tool",
    "truncate_text",
]
