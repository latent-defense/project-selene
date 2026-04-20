"""Inverted indexes over the unified timeline's entity tags.

Three facets:
- `by_directive`: directive_id → list of timeline entry ids
- `by_resource`: resource (lowercased) → list of timeline entry ids
- `by_pod_mentioned`: pod_id (lowercased) → list of timeline entry ids

These are the "deterministic clusters" (see `deliberation.md` §4) that the
reporter cites when narrating themes. Membership is explainable: an id is
in a bucket iff the timeline entry's `entities` contains that tag.
"""
from __future__ import annotations

from typing import Any


def build(timeline: list[dict[str, Any]]) -> dict[str, dict[str, list[int]]]:
    by_directive: dict[str, list[int]] = {}
    by_resource: dict[str, list[int]] = {}
    by_pod_mentioned: dict[str, list[int]] = {}

    for entry in timeline:
        eid = entry["id"]
        ents = entry.get("entities", {})
        for d in ents.get("directive_ids", []):
            by_directive.setdefault(d, []).append(eid)
        for r in ents.get("resources", []):
            by_resource.setdefault(r, []).append(eid)
        for p in ents.get("pod_refs", []):
            by_pod_mentioned.setdefault(p, []).append(eid)

    # Sorted dicts so the output is deterministic across runs.
    return {
        "by_directive": {k: by_directive[k] for k in sorted(by_directive)},
        "by_resource": {k: by_resource[k] for k in sorted(by_resource)},
        "by_pod_mentioned": {k: by_pod_mentioned[k] for k in sorted(by_pod_mentioned)},
    }
