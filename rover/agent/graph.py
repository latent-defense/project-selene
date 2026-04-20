"""Resource-edge graph, evidence-based reconciliation, and graph metrics.

The edge model treats `(supplier, consumer, resource)` as a first-class triple
(not just an unweighted pod→pod arc). Each edge carries `claimed_by` — the
set of sides that asserted it (`upstream` if the supplier's `/supplies`
declared it, `downstream` if the consumer's `/dependencies` declared it).
Reconciliation annotates edges with `log_evidence` (timeline entry ids that
corroborate the relationship) and a categorical `status`.

See `deliberation.md` §7 for the design rationale.
"""
from __future__ import annotations

from typing import Any

import networkx as nx


# --- edge construction -----------------------------------------------------


def build_edges(pods: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    """Walk every pod's /supplies + /dependencies; merge into a unified edge list.

    Edges are keyed by `(supplier, consumer, resource)` — so "pod A supplies
    water to pod B" and "pod A supplies coolant to pod B" are two distinct
    edges. Each edge's `claimed_by` records which side(s) asserted it.

    Criticality is only available from the downstream side (`/dependencies`
    entries have a `criticality` field; `/supplies` does not). When the
    downstream claims the edge, we propagate criticality + notes onto the
    edge record; otherwise those fields are `None`.
    """
    key_to_edge: dict[tuple[str, str, str], dict[str, Any]] = {}

    def _ensure(supplier: str, consumer: str, resource: str) -> dict[str, Any]:
        key = (supplier, consumer, resource)
        return key_to_edge.setdefault(
            key,
            {
                "supplier": supplier,
                "consumer": consumer,
                "resource": resource,
                "claimed_by": set(),
                "criticality": None,
                "notes": None,
            },
        )

    for pod_id, pod in pods.items():
        supplies_body = pod["endpoints"].get("/supplies") or {}
        for entry in supplies_body.get("supplies", []):
            consumer_id = entry.get("pod_id")
            resource = entry.get("resource")
            if isinstance(consumer_id, str) and isinstance(resource, str):
                edge = _ensure(pod_id, consumer_id, resource)
                edge["claimed_by"].add("upstream")

        deps_body = pod["endpoints"].get("/dependencies") or {}
        for entry in deps_body.get("dependencies", []):
            supplier_id = entry.get("pod_id")
            resource = entry.get("resource")
            if isinstance(supplier_id, str) and isinstance(resource, str):
                edge = _ensure(supplier_id, pod_id, resource)
                edge["claimed_by"].add("downstream")
                # Downstream side carries criticality + notes
                crit = entry.get("criticality")
                if isinstance(crit, str):
                    edge["criticality"] = crit
                notes = entry.get("notes")
                if isinstance(notes, str):
                    edge["notes"] = notes

    edges: list[dict[str, Any]] = []
    for key in sorted(key_to_edge):
        edge = key_to_edge[key]
        edge["claimed_by"] = sorted(edge["claimed_by"])
        edges.append(edge)
    return edges


# --- reconciliation --------------------------------------------------------


def _resource_tokens(resource: str) -> set[str]:
    """Canonical form + underscore-split tokens, lowercased."""
    lower = resource.lower()
    parts = {p for p in lower.split("_") if p}
    return {lower} | parts


def reconcile(
    edges: list[dict[str, Any]],
    timeline: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Annotate edges with `log_evidence` and categorical `status`.

    For each edge, a timeline entry is log evidence if it (a) is sourced from
    either the supplier or consumer, (b) mentions the other endpoint in its
    `entities.pod_refs`, and (c) mentions the resource (canonical form or any
    underscore-split token) in its `entities.resources`.

    Status values:
      - `reciprocated`           — both sides claim the edge.
      - `reciprocated_with_logs` — both sides claim + log evidence corroborates.
      - `single_sided`           — only one side claims (log evidence may
                                    still be populated, which is useful signal).

    Not implemented here (see `deliberation.md §10`): `log_only` (neither side
    claims but logs reference the relationship) and `disputed` (conflicting
    claims across pods). Both require heavier text mining or cross-edge
    pattern detection; deferred to the LLM reporter as inference.
    """
    # Index timeline by source_pod for cheap per-pod lookup
    by_source: dict[str, list[dict[str, Any]]] = {}
    for entry in timeline:
        by_source.setdefault(entry["source_pod"], []).append(entry)

    annotated: list[dict[str, Any]] = []
    for edge in edges:
        supplier = edge["supplier"]
        consumer = edge["consumer"]
        tokens = _resource_tokens(edge["resource"])

        log_evidence: set[int] = set()
        for endpoint in (supplier, consumer):
            other = consumer if endpoint == supplier else supplier
            for entry in by_source.get(endpoint, []):
                ents = entry.get("entities", {})
                if other.lower() not in ents.get("pod_refs", []):
                    continue
                if not tokens & set(ents.get("resources", [])):
                    continue
                log_evidence.add(entry["id"])

        claimed = set(edge["claimed_by"])
        if claimed == {"upstream", "downstream"}:
            status = "reciprocated_with_logs" if log_evidence else "reciprocated"
        else:
            status = "single_sided"

        annotated.append(
            {
                **edge,
                "log_evidence": sorted(log_evidence),
                "status": status,
            }
        )
    return annotated


# --- graph metrics ---------------------------------------------------------


def metrics(
    pods: dict[str, dict[str, Any]],
    edges: list[dict[str, Any]],
) -> dict[str, Any]:
    """Graph-theoretic + supply-level summaries used by the reporter.

    The directed graph has an arc `supplier → consumer` for every unique
    `(supplier, consumer)` pair across edges (resource multiplicity is
    collapsed — a graph arc exists iff there's any supply relationship).

    Topology metrics (in/out-degree, articulation points, transitive
    dependents) alone undersell the SPOF story, because a colony can be
    graph-connected and still have pods whose loss is catastrophic at the
    *supply* level. We also compute:

    - `unique_supplier_edges`: edges where the consumer has exactly one
      supplier for the given resource. No redundancy — if that supplier
      fails, the consumer loses that resource.
    - `critical_unique_edges`: subset of `unique_supplier_edges` where the
      downstream side marked `criticality=high`. These are the strongest
      SPOFs — sole source of a critical dependency.
    - `spof_pods`: pods that appear as supplier in at least one
      `critical_unique_edge`.
    """
    directed = nx.DiGraph()
    for pod_id in pods:
        directed.add_node(pod_id)
    for edge in edges:
        directed.add_edge(edge["supplier"], edge["consumer"])

    in_degree = {n: directed.in_degree(n) for n in sorted(directed.nodes())}
    out_degree = {n: directed.out_degree(n) for n in sorted(directed.nodes())}

    undirected = directed.to_undirected()
    articulation_points = sorted(nx.articulation_points(undirected))

    transitive_dependents = {
        n: sorted(nx.descendants(directed, n)) for n in sorted(directed.nodes())
    }

    # ---- supply-level SPOF metrics ----
    # How many suppliers does each (consumer, resource) pair have?
    suppliers_by_need: dict[tuple[str, str], set[str]] = {}
    for edge in edges:
        key = (edge["consumer"], edge["resource"])
        suppliers_by_need.setdefault(key, set()).add(edge["supplier"])

    unique_supplier_edges: list[dict[str, Any]] = []
    critical_unique_edges: list[dict[str, Any]] = []
    for edge in edges:
        key = (edge["consumer"], edge["resource"])
        if len(suppliers_by_need[key]) == 1:
            unique_supplier_edges.append(edge)
            if edge.get("criticality") == "high":
                critical_unique_edges.append(edge)

    spof_pods = sorted({e["supplier"] for e in critical_unique_edges})

    return {
        "in_degree": in_degree,
        "out_degree": out_degree,
        "articulation_points": articulation_points,
        "transitive_dependents": transitive_dependents,
        "unique_supplier_edges": unique_supplier_edges,
        "critical_unique_edges": critical_unique_edges,
        "spof_pods": spof_pods,
    }
