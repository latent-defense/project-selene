"""Unit tests for timeline merge (M2.2)."""
from __future__ import annotations

from agent.entities import build_pod_ref_re, build_resource_re
from agent.timeline import build


def _pod(pod_id, logs=None, comms=None):
    """Synthetic pod with /logs and /comms endpoints."""
    return {
        "pod_id": pod_id,
        "endpoints": {
            "/info": {"id": pod_id, "name": pod_id},
            "/status": {"id": pod_id, "status": "nominal"},
            "/dependencies": {"dependencies": []},
            "/supplies": {"supplies": []},
            "/logs": {"id": pod_id, "logs": logs or []},
            "/comms": {"id": pod_id, "messages": comms} if comms is not None else None,
        },
    }


def test_timeline_sorted_chronologically_with_sequential_ids() -> None:
    pods = {
        "a": _pod(
            "a",
            logs=[
                {"timestamp": "2094-03-15T10:00:00Z", "event": "ev2", "detail": ""},
                {"timestamp": "2094-01-01T00:00:00Z", "event": "ev1", "detail": ""},
            ],
        ),
    }
    pod_re = build_pod_ref_re(["a"])
    res_re = build_resource_re(set())
    tl = build(pods, pod_re, res_re)
    assert [e["text"] for e in tl] == ["ev1", "ev2"]
    assert [e["id"] for e in tl] == [0, 1]


def test_timeline_includes_logs_and_comms() -> None:
    pods = {
        "a": _pod(
            "a",
            logs=[{"timestamp": "2094-01-01T00:00:00Z", "event": "log_a", "detail": ""}],
            comms=[
                {
                    "timestamp": "2094-01-02T00:00:00Z",
                    "from": "a",
                    "to": "b",
                    "content": "hi",
                }
            ],
        ),
    }
    pod_re = build_pod_ref_re(["a", "b"])
    res_re = build_resource_re(set())
    tl = build(pods, pod_re, res_re)
    kinds = [e["kind"] for e in tl]
    assert kinds == ["log", "comm"]
    # Comm entry preserves from/to
    assert tl[1]["from"] == "a"
    assert tl[1]["to"] == "b"


def test_timeline_filters_unknown_pod_in_comms_from_to() -> None:
    """Team names like 'zephyr_ops' must not be tagged as pod_refs."""
    pods = {
        "a": _pod(
            "a",
            comms=[
                {
                    "timestamp": "2094-01-01T00:00:00Z",
                    "from": "zephyr_ops",
                    "to": "a",
                    "content": "ack",
                }
            ],
        ),
    }
    pod_re = build_pod_ref_re(["a"])
    res_re = build_resource_re(set())
    tl = build(pods, pod_re, res_re)
    assert tl[0]["entities"]["pod_refs"] == ["a"]  # 'a' is a known pod, 'zephyr_ops' isn't


def test_timeline_handles_missing_comms() -> None:
    pods = {"a": _pod("a", logs=[], comms=None)}
    pod_re = build_pod_ref_re(["a"])
    res_re = build_resource_re(set())
    assert build(pods, pod_re, res_re) == []


def test_timeline_handles_malformed_timestamp() -> None:
    pods = {
        "a": _pod(
            "a",
            logs=[{"timestamp": "garbage", "event": "ev", "detail": ""}],
        ),
    }
    pod_re = build_pod_ref_re(["a"])
    res_re = build_resource_re(set())
    tl = build(pods, pod_re, res_re)
    # Sentinel 0 timestamp; iso preserved verbatim for debugging
    assert tl[0]["timestamp"] == 0
    assert tl[0]["timestamp_iso"] == "garbage"
