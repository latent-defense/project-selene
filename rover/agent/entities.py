"""Entity extraction over log/comms text — the deterministic substrate for the
facet indexes and for log-evidence reconciliation (see `deliberation.md` §7).

Accuracy-first: we use word-boundary regexes so that substrings inside larger
words don't produce false positives (e.g. `forge` won't match `reforge`).
The vocabularies (pod_ids, resources) are built from crawl-time ground truth,
so extraction is explainable: every hit maps back to a known entity.
"""
from __future__ import annotations

import re
from typing import Any, Iterable

DIRECTIVE_RE = re.compile(r"\b(\d{4}-\d{3})\b")
"""Matches directive IDs like `2093-089`. Year is 4 digits, suffix is 3."""


def build_pod_ref_re(pod_ids: Iterable[str]) -> re.Pattern[str]:
    """Compile a case-insensitive whole-word regex that matches any pod_id.

    Alternation is sorted longest-first so that `foo-bar` wins over `foo` on
    prefix collisions; pod_ids are currently single-word but future-proofing.
    """
    pods = sorted({p for p in pod_ids if p}, key=len, reverse=True)
    if not pods:
        return re.compile(r"(?!)")  # unmatchable
    alts = "|".join(re.escape(p) for p in pods)
    return re.compile(rf"\b({alts})\b", re.IGNORECASE)


def expand_resource_tokens(resources: Iterable[str]) -> set[str]:
    """Split resource names on underscores; return both originals and parts.

    Vocab entries look like `coolant_water` / `electrical_power`, but log
    prose says `coolant` or `power` without the modifier. Matching requires
    both forms; callers pass this expanded set to `build_resource_re`.
    """
    expanded: set[str] = set()
    for r in resources:
        if not r:
            continue
        expanded.add(r.lower())
        for part in r.lower().split("_"):
            if part:
                expanded.add(part)
    return expanded


def build_resource_re(resources: Iterable[str]) -> re.Pattern[str]:
    """Compile a case-insensitive whole-word regex over resource tokens.

    Pass the output of `expand_resource_tokens(vocab)` as input so both the
    canonical form (`coolant_water`) and the prose forms (`coolant`,
    `water`) are matched. Longer alternates come first so `coolant_water`
    wins over `coolant` when both could match.
    """
    vocab = sorted({r for r in resources if r}, key=len, reverse=True)
    if not vocab:
        return re.compile(r"(?!)")
    alts = "|".join(re.escape(r) for r in vocab)
    return re.compile(rf"\b({alts})\b", re.IGNORECASE)


def collect_resource_vocab(pods: dict[str, dict[str, Any]]) -> set[str]:
    """Gather every `resource` string seen in any pod's supplies/dependencies."""
    vocab: set[str] = set()
    for pod in pods.values():
        for endpoint_key in ("/supplies", "/dependencies"):
            body = pod["endpoints"].get(endpoint_key) or {}
            entries = body.get(endpoint_key.lstrip("/"), [])
            for entry in entries:
                r = entry.get("resource")
                if isinstance(r, str) and r:
                    vocab.add(r)
    return vocab


def extract(
    text: str,
    *,
    pod_re: re.Pattern[str],
    resource_re: re.Pattern[str],
) -> dict[str, list[str]]:
    """Extract entity mentions from `text`. Returns deduplicated, sorted lists.

    Self-references are NOT filtered here — callers that want to drop the
    source pod from `pod_refs` should do it after extraction. Keeping the raw
    output preserves audit fidelity.
    """
    if not text:
        return {"pod_refs": [], "directive_ids": [], "resources": []}

    pod_refs = {m.group(1).lower() for m in pod_re.finditer(text)}
    directive_ids = {m.group(1) for m in DIRECTIVE_RE.finditer(text)}
    resources = {m.group(1).lower() for m in resource_re.finditer(text)}

    return {
        "pod_refs": sorted(pod_refs),
        "directive_ids": sorted(directive_ids),
        "resources": sorted(resources),
    }
