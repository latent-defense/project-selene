"""Unit tests for graph build, reconciliation, and metrics (M3)."""
from __future__ import annotations

from agent.graph import build_edges, metrics, reconcile


def _pod(
    pod_id,
    supplies=None,
    dependencies=None,
):
    return {
        "pod_id": pod_id,
        "endpoints": {
            "/supplies": {"supplies": supplies or []},
            "/dependencies": {"dependencies": dependencies or []},
        },
    }


def test_reciprocated_edge() -> None:
    pods = {
        "a": _pod("a", supplies=[{"pod_id": "b", "resource": "water"}]),
        "b": _pod("b", dependencies=[{"pod_id": "a", "resource": "water", "criticality": "high"}]),
    }
    edges = reconcile(build_edges(pods), timeline=[])
    assert len(edges) == 1
    e = edges[0]
    assert e["claimed_by"] == ["downstream", "upstream"]
    assert e["status"] == "reciprocated"
    assert e["criticality"] == "high"


def test_single_sided_upstream() -> None:
    pods = {
        "a": _pod("a", supplies=[{"pod_id": "b", "resource": "water"}]),
        "b": _pod("b"),
    }
    edges = reconcile(build_edges(pods), timeline=[])
    assert edges[0]["status"] == "single_sided"
    assert edges[0]["claimed_by"] == ["upstream"]
    assert edges[0]["criticality"] is None


def test_single_sided_downstream_carries_criticality() -> None:
    pods = {
        "a": _pod("a"),
        "b": _pod("b", dependencies=[{"pod_id": "a", "resource": "water", "criticality": "medium", "notes": "n"}]),
    }
    edges = reconcile(build_edges(pods), timeline=[])
    assert edges[0]["status"] == "single_sided"
    assert edges[0]["claimed_by"] == ["downstream"]
    assert edges[0]["criticality"] == "medium"
    assert edges[0]["notes"] == "n"


def test_reciprocated_with_logs_when_evidence_present() -> None:
    pods = {
        "a": _pod("a", supplies=[{"pod_id": "b", "resource": "water"}]),
        "b": _pod("b", dependencies=[{"pod_id": "a", "resource": "water"}]),
    }
    timeline = [
        {
            "id": 0,
            "source_pod": "a",
            "kind": "log",
            "text": "shipped water to b",
            "entities": {"pod_refs": ["b"], "resources": ["water"], "directive_ids": []},
        }
    ]
    edges = reconcile(build_edges(pods), timeline)
    assert edges[0]["status"] == "reciprocated_with_logs"
    assert edges[0]["log_evidence"] == [0]


def test_reconcile_log_evidence_requires_resource_token_match() -> None:
    """A log mentioning the counterparty without the resource is NOT evidence."""
    pods = {
        "a": _pod("a", supplies=[{"pod_id": "b", "resource": "water"}]),
        "b": _pod("b"),
    }
    timeline = [
        {
            "id": 0,
            "source_pod": "a",
            "kind": "log",
            "text": "talked to b",
            "entities": {"pod_refs": ["b"], "resources": [], "directive_ids": []},
        }
    ]
    edges = reconcile(build_edges(pods), timeline)
    assert edges[0]["log_evidence"] == []
    assert edges[0]["status"] == "single_sided"


def test_reconcile_resource_token_split() -> None:
    """Log mentioning 'water' is evidence for an edge over 'potable_water'."""
    pods = {
        "a": _pod("a", supplies=[{"pod_id": "b", "resource": "potable_water"}]),
        "b": _pod("b", dependencies=[{"pod_id": "a", "resource": "potable_water"}]),
    }
    timeline = [
        {
            "id": 0,
            "source_pod": "a",
            "kind": "log",
            "text": "water to b",
            "entities": {"pod_refs": ["b"], "resources": ["water"], "directive_ids": []},
        }
    ]
    edges = reconcile(build_edges(pods), timeline)
    assert edges[0]["status"] == "reciprocated_with_logs"


def test_metrics_unique_supplier_and_critical() -> None:
    pods = {
        "a": _pod("a", supplies=[{"pod_id": "c", "resource": "water"}]),
        "b": _pod("b", supplies=[{"pod_id": "c", "resource": "power"}]),
        "c": _pod(
            "c",
            dependencies=[
                {"pod_id": "a", "resource": "water", "criticality": "high"},
                {"pod_id": "b", "resource": "power", "criticality": "high"},
            ],
        ),
    }
    edges = reconcile(build_edges(pods), timeline=[])
    m = metrics(pods, edges)
    # Both edges are unique-supplier and high-crit → both pods are SPOFs
    assert sorted(m["spof_pods"]) == ["a", "b"]
    assert m["in_degree"]["c"] == 2
    assert m["out_degree"]["a"] == 1
    assert m["out_degree"]["b"] == 1


def test_metrics_redundant_supplier_not_unique() -> None:
    """If two pods supply the same resource to a third, neither is unique."""
    pods = {
        "a": _pod("a", supplies=[{"pod_id": "c", "resource": "water"}]),
        "b": _pod("b", supplies=[{"pod_id": "c", "resource": "water"}]),
        "c": _pod(
            "c",
            dependencies=[{"pod_id": "a", "resource": "water", "criticality": "high"}],
        ),
    }
    edges = reconcile(build_edges(pods), timeline=[])
    m = metrics(pods, edges)
    # No unique-supplier; no SPOFs
    assert m["spof_pods"] == []
