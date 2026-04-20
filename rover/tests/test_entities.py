"""Unit tests for entity extraction (M2.1)."""
from __future__ import annotations

from agent.entities import (
    DIRECTIVE_RE,
    build_pod_ref_re,
    build_resource_re,
    collect_resource_vocab,
    expand_resource_tokens,
    extract,
)


def test_directive_regex_matches_yyyy_nnn() -> None:
    assert DIRECTIVE_RE.findall("issued 2093-089 today") == ["2093-089"]
    assert DIRECTIVE_RE.findall("see 2092-042 and 2094-011") == ["2092-042", "2094-011"]


def test_directive_regex_rejects_bad_shapes() -> None:
    # 3-digit year, 2-digit suffix, no hyphen — none should match
    assert DIRECTIVE_RE.findall("issue 209-089 or 2093-89 or 2093089") == []


def test_pod_ref_regex_word_boundary() -> None:
    pod_re = build_pod_ref_re(["helios", "artemis", "forge"])
    # Whole-word matches
    assert pod_re.findall("Helios sent power to Artemis") == ["Helios", "Artemis"]
    # No false positives in larger words
    assert pod_re.findall("reforge the artemiscore") == []


def test_pod_ref_regex_case_insensitive_lowered_in_extract() -> None:
    pod_re = build_pod_ref_re(["helios"])
    res_re = build_resource_re(set())
    out = extract("HELIOS provides power.", pod_re=pod_re, resource_re=res_re)
    assert out["pod_refs"] == ["helios"]


def test_expand_resource_tokens_splits_on_underscore() -> None:
    assert expand_resource_tokens(["coolant_water", "raw_materials"]) == {
        "coolant_water",
        "coolant",
        "water",
        "raw_materials",
        "raw",
        "materials",
    }


def test_resource_re_matches_token_or_canonical() -> None:
    expanded = expand_resource_tokens(["coolant_water", "electrical_power"])
    res_re = build_resource_re(expanded)
    pod_re = build_pod_ref_re([])
    # Matches token forms ('coolant', 'water') and canonical
    out = extract(
        "transferred coolant feed; restored electrical_power.",
        pod_re=pod_re,
        resource_re=res_re,
    )
    assert "coolant" in out["resources"]
    assert "electrical_power" in out["resources"]


def test_extract_handles_empty_text() -> None:
    pod_re = build_pod_ref_re(["helios"])
    res_re = build_resource_re({"water"})
    out = extract("", pod_re=pod_re, resource_re=res_re)
    assert out == {"pod_refs": [], "directive_ids": [], "resources": []}


def test_extract_dedupes_and_sorts() -> None:
    pod_re = build_pod_ref_re(["helios", "artemis"])
    res_re = build_resource_re({"water"})
    out = extract(
        "Helios told Artemis Helios about water and water.",
        pod_re=pod_re,
        resource_re=res_re,
    )
    assert out["pod_refs"] == ["artemis", "helios"]
    assert out["resources"] == ["water"]


def test_collect_resource_vocab() -> None:
    pods = {
        "a": {
            "endpoints": {
                "/supplies": {"supplies": [{"pod_id": "b", "resource": "potable_water"}]},
                "/dependencies": {"dependencies": []},
            }
        },
        "b": {
            "endpoints": {
                "/supplies": {"supplies": []},
                "/dependencies": {
                    "dependencies": [{"pod_id": "a", "resource": "electrical_power"}]
                },
            }
        },
    }
    assert collect_resource_vocab(pods) == {"potable_water", "electrical_power"}
