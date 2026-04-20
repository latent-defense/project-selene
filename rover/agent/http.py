"""HTTP client wrapping `httpx.Client` with per-request query receipts.

Every `get()` call produces a receipt record and appends it to the
`MappingCheckpoint`'s queries log, regardless of outcome. The client never
raises for HTTP-level failures — connect errors, timeouts, and non-2xx
responses all come back as `(None, receipt)` with the receipt's
`status_code` / `exception_*` fields telling the caller what happened.

This keeps the contract uniform: every HTTP attempt is one line of audit
trail, and callers decide whether a given outcome is an error worth
escalating (via `checkpoint.append_error(...)`).
"""
from __future__ import annotations

import time
from typing import Any

import httpx

from .checkpoint import MappingCheckpoint


def _now_ms() -> int:
    return int(time.time() * 1000)


class Client:
    """Single-shot sync HTTP client. One instance per phase (mapping)."""

    def __init__(
        self,
        checkpoint: MappingCheckpoint,
        *,
        timeout_s: float = 5.0,
        connect_timeout_s: float = 0.5,
    ) -> None:
        self._checkpoint = checkpoint
        self._client = httpx.Client(
            timeout=httpx.Timeout(timeout_s, connect=connect_timeout_s),
        )

    def get(
        self,
        url: str,
        *,
        target_service: str,
        endpoint: str,
        discovery_context: str | None = None,
        retries: int = 2,
        retry_backoff_s: float = 0.25,
    ) -> tuple[Any, dict]:
        """GET `url`. Returns `(parsed_json_or_none, receipt)` and never raises.

        - 2xx with JSON body → `(parsed, receipt)`, `has_response=True`.
        - 404 → `(None, receipt)`, recorded but not treated as exception.
        - Other non-2xx → `(None, receipt)` with `status_code` populated.
        - Connect/read errors → `(None, receipt)` with `status_code=None` and
          `exception_type` / `exception_message` populated.

        Retries are attempted only on transient network errors
        (`httpx.ConnectError`, `httpx.ReadTimeout`), not on HTTP status
        codes. Each attempt produces its own receipt line so the audit
        trail shows every wire call.
        """
        last_result: tuple[Any, dict] | None = None
        for attempt in range(retries + 1):
            parsed, receipt = self._one_attempt(
                url,
                target_service=target_service,
                endpoint=endpoint,
                discovery_context=discovery_context,
                attempt=attempt,
            )
            last_result = (parsed, receipt)
            # Retry only transient network failures
            if receipt.get("exception_type") in {"ConnectError", "ReadTimeout"} and attempt < retries:
                time.sleep(retry_backoff_s * (attempt + 1))
                continue
            break
        assert last_result is not None
        return last_result

    def _one_attempt(
        self,
        url: str,
        *,
        target_service: str,
        endpoint: str,
        discovery_context: str | None,
        attempt: int,
    ) -> tuple[Any, dict]:
        query_id = self._checkpoint.next_query_id()
        start = _now_ms()
        receipt: dict[str, Any] = {
            "query_id": query_id,
            "host_service": "rover",
            "target_service": target_service,
            "endpoint": endpoint,
            "target_url": url,
            "start_time": start,
            "end_time": None,
            "latency_ms": None,
            "status_code": None,
            "response_size_bytes": 0,
            "has_response": False,
            "discovery_context": discovery_context,
            "attempt": attempt,
        }
        parsed: Any = None
        try:
            resp = self._client.get(url)
            receipt["status_code"] = resp.status_code
            receipt["response_size_bytes"] = len(resp.content)
            if resp.status_code == 200:
                try:
                    parsed = resp.json()
                    receipt["has_response"] = True
                except ValueError:
                    # 200 with a non-JSON body — record the status but no body.
                    pass
            # 404 and other non-200: parsed stays None.
        except httpx.HTTPError as exc:
            receipt["exception_type"] = type(exc).__name__
            receipt["exception_message"] = str(exc)
        finally:
            end = _now_ms()
            receipt["end_time"] = end
            receipt["latency_ms"] = end - start
            self._checkpoint.append_query(receipt)
        return parsed, receipt

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "Client":
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()
