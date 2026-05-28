"""Timeline extraction helpers."""

from typing import Any
from urllib.parse import urlparse


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


__all__ = ["extract_timeline"]
