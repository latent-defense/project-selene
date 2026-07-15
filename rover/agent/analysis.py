"""Deterministic analysis of crawled pod data.

Takes the raw pod responses + the network-sweep set and computes:

- Declared dependency and supply edges
- Mismatches between declared dep and declared supply (Prometheus↔Aquifer-style drift)
- Per-pod graph metrics (degree, betweenness, articulation membership)
- SPOF candidates (high in-degree + no declared backups)
- Capacity utilization parsed from metadata
- Log pattern flags (structural change events: decommissioning, reroutes, etc.)
- Dangling references (declared targets not in DNS) + orphans (DNS but unreferenced)

All deterministic. The LLM does not run here.
"""

from __future__ import annotations

import re
from typing import Any

import networkx as nx

LOG_PATTERN = re.compile(
    r"\b(decommissioned|rerouted|consolidated|transferred|reassigned|retired|directive)\b",
    re.IGNORECASE,
)
DIRECTIVE_PATTERN = re.compile(r"\bdirective\s+(\d{4}-\d{3})\b", re.IGNORECASE)

HIGH_UTILIZATION_THRESHOLD = 85.0  # percent
SPOF_IN_DEGREE_THRESHOLD = 3


def _safe_dict(maybe: Any) -> dict:
    return maybe if isinstance(maybe, dict) else {}


def _safe_list(maybe: Any) -> list:
    return maybe if isinstance(maybe, list) else []


def extract_edges(pods_raw: dict[str, dict]) -> tuple[list[dict], list[dict]]:
    """Pull every declared dep + supply edge from the raw pod data."""
    depends_edges: list[dict] = []
    supplies_edges: list[dict] = []
    for src, data in pods_raw.items():
        deps = _safe_list(_safe_dict(data.get("dependencies")).get("dependencies"))
        for d in deps:
            if not isinstance(d, dict):
                continue
            depends_edges.append({
                "from": src,
                "to": d.get("pod_id"),
                "resource": d.get("resource"),
                "criticality": d.get("criticality"),
                "notes": d.get("notes"),
            })
        sups = _safe_list(_safe_dict(data.get("supplies")).get("supplies"))
        for s in sups:
            if not isinstance(s, dict):
                continue
            supplies_edges.append({
                "from": src,
                "to": s.get("pod_id"),
                "resource": s.get("resource"),
            })
    return depends_edges, supplies_edges


def detect_mismatches(
    depends_edges: list[dict],
    supplies_edges: list[dict],
) -> list[dict]:
    """Cross-check declared dep ↔ declared supply.

    Convention:
      depend edge (A→B for R) "A depends on B for R" should match
      supply edge (B→A for R) "B supplies R to A".

    Any unmatched edge is a finding (something declared on one side that
    the other side doesn't acknowledge).
    """
    supply_keys = {(e["from"], e["to"], e["resource"]) for e in supplies_edges}
    depend_keys = {(e["from"], e["to"], e["resource"]) for e in depends_edges}

    mismatches: list[dict] = []
    for e in depends_edges:
        expected = (e["to"], e["from"], e["resource"])  # supplier=to, recipient=from
        if expected not in supply_keys:
            mismatches.append({
                "type": "depends_without_supplies",
                "from": e["from"],
                "to": e["to"],
                "resource": e["resource"],
                "evidence": (
                    f"{e['from']} declares dependency on {e['to']} for {e['resource']}, "
                    f"but {e['to']} does not list {e['from']} in /supplies for {e['resource']}."
                ),
            })
    for e in supplies_edges:
        expected = (e["to"], e["from"], e["resource"])  # depender=to, dependee=from
        if expected not in depend_keys:
            mismatches.append({
                "type": "supplies_without_depends",
                "from": e["from"],
                "to": e["to"],
                "resource": e["resource"],
                "evidence": (
                    f"{e['from']} declares supplying {e['resource']} to {e['to']}, "
                    f"but {e['to']} does not list {e['from']} in /dependencies for {e['resource']}."
                ),
            })
    return mismatches


def build_dependency_graph(depends_edges: list[dict]) -> nx.DiGraph:
    """Directed graph: A→B means A depends on B (B supplies A)."""
    G = nx.DiGraph()
    for e in depends_edges:
        if e.get("from") and e.get("to"):
            G.add_edge(e["from"], e["to"], resource=e.get("resource"), criticality=e.get("criticality"))
    return G


def per_pod_graph_metrics(G: nx.DiGraph, pod_ids: list[str]) -> dict[str, dict]:
    """in/out degree, betweenness, articulation-point membership for each pod."""
    UG = G.to_undirected()
    cut_vertices = set(nx.articulation_points(UG)) if UG.number_of_edges() > 0 else set()
    betweenness = nx.betweenness_centrality(G) if G.number_of_edges() > 0 else {}

    metrics: dict[str, dict] = {}
    for pid in pod_ids:
        metrics[pid] = {
            "in_degree": G.in_degree(pid) if pid in G else 0,
            "out_degree": G.out_degree(pid) if pid in G else 0,
            "betweenness": round(betweenness.get(pid, 0.0), 4),
            "is_articulation_point": pid in cut_vertices,
        }
    return metrics


def capacity_utilization_pct(metadata: dict) -> float | None:
    """Return the highest utilization signal in metadata, as a percentage."""
    if not isinstance(metadata, dict):
        return None
    signals: list[float] = []

    # Explicit load/usage/utilization percentage keys
    for k, v in metadata.items():
        if not isinstance(v, (int, float)):
            continue
        kl = k.lower()
        if any(token in kl for token in ("utilization_pct", "load_pct", "usage_pct")):
            signals.append(float(v))

    # throughput_<unit> / rated_capacity_<unit> pairs
    for k, v in metadata.items():
        if k.startswith("throughput_") and isinstance(v, (int, float)):
            suffix = k[len("throughput_"):]
            rated = metadata.get(f"rated_capacity_{suffix}")
            if isinstance(rated, (int, float)) and rated > 0:
                signals.append(round(v / rated * 100, 1))

    return max(signals) if signals else None


def find_spof_candidates(
    pods_raw: dict[str, dict],
    metrics: dict[str, dict],
    threshold: int = SPOF_IN_DEGREE_THRESHOLD,
) -> list[dict]:
    """Pods that many others depend on AND have no declared backups."""
    spofs: list[dict] = []
    for pid, m in metrics.items():
        if m["in_degree"] < threshold:
            continue
        info = _safe_dict(pods_raw.get(pid, {}).get("info"))
        metadata = _safe_dict(info.get("metadata"))
        backups = metadata.get("backup_systems")
        # "0", 0, None, or "none" → no backup. Anything else → has backup.
        if backups not in (0, "0", None, "none", False):
            continue
        dependents = sorted({
            other for other, data in pods_raw.items()
            if other != pid and any(
                isinstance(d, dict) and d.get("pod_id") == pid
                for d in _safe_list(_safe_dict(data.get("dependencies")).get("dependencies"))
            )
        })
        spofs.append({
            "pod": pid,
            "in_degree": m["in_degree"],
            "dependents": dependents,
            "backup_systems": backups,
            "is_articulation_point": m["is_articulation_point"],
        })
    return spofs


def find_log_patterns(pods_raw: dict[str, dict]) -> list[dict]:
    """Scan /logs for structural-change events (regex match)."""
    matches: list[dict] = []
    for pid, data in pods_raw.items():
        logs = _safe_list(_safe_dict(data.get("logs")).get("logs"))
        for entry in logs:
            if not isinstance(entry, dict):
                continue
            text = f"{entry.get('event', '')} {entry.get('detail', '')}"
            m = LOG_PATTERN.search(text)
            if not m:
                continue
            directive = DIRECTIVE_PATTERN.search(text)
            matches.append({
                "pod": pid,
                "timestamp": entry.get("timestamp"),
                "pattern": m.group(1).lower(),
                "directive": directive.group(1) if directive else None,
                "event": entry.get("event"),
                "detail": entry.get("detail"),
            })
    return matches


def find_orphans(network_sweep_set: set[str], edges: list[dict]) -> list[str]:
    """Pods in DNS but never named as a target by any other pod."""
    referenced = {e["to"] for e in edges if e.get("to")}
    return sorted(network_sweep_set - referenced)


def find_dangling_references(
    edges_depends: list[dict],
    edges_supplies: list[dict],
    network_sweep_set: set[str],
) -> list[dict]:
    """Edges naming a pod_id that DNS doesn't resolve."""
    danglings: list[dict] = []
    for e in edges_depends:
        if e.get("to") and e["to"] not in network_sweep_set:
            danglings.append({
                "type": "depends",
                "from": e["from"], "to_missing_pod": e["to"], "resource": e.get("resource"),
            })
    for e in edges_supplies:
        if e.get("to") and e["to"] not in network_sweep_set:
            danglings.append({
                "type": "supplies",
                "from": e["from"], "to_missing_pod": e["to"], "resource": e.get("resource"),
            })
    return danglings


def per_pod_derived(
    pods_raw: dict[str, dict],
    graph_metrics: dict[str, dict],
    spof_pod_ids: set[str],
) -> dict[str, dict]:
    """Assemble per-pod derived block: metrics + capacity + flags."""
    derived: dict[str, dict] = {}
    for pid, raw in pods_raw.items():
        info = _safe_dict(raw.get("info"))
        metadata = _safe_dict(info.get("metadata"))
        util = capacity_utilization_pct(metadata)
        flags: list[str] = []
        if util is not None and util >= HIGH_UTILIZATION_THRESHOLD:
            flags.append("high_utilization")
        if pid in spof_pod_ids:
            flags.append("spof_candidate")
        if metadata.get("backup_systems") in (0, "0", "none", None):
            flags.append("no_declared_backups")
        derived[pid] = {
            **graph_metrics.get(pid, {}),
            "capacity_utilization_pct": util,
            "flags": flags,
        }
    return derived


def analyze(pods_raw: dict[str, dict], network_sweep_set: set[str]) -> dict:
    """Top-level: run all deterministic analyses, return a single dict for map.json."""
    depends_edges, supplies_edges = extract_edges(pods_raw)
    mismatches = detect_mismatches(depends_edges, supplies_edges)

    G = build_dependency_graph(depends_edges)
    pod_ids = sorted(pods_raw.keys())
    graph_metrics = per_pod_graph_metrics(G, pod_ids)
    spofs = find_spof_candidates(pods_raw, graph_metrics)
    spof_pod_ids = {s["pod"] for s in spofs}

    return {
        "edges_declared_depends": depends_edges,
        "edges_declared_supplies": supplies_edges,
        "mismatches": mismatches,
        "spof_candidates": spofs,
        "articulation_points": sorted(
            pid for pid, m in graph_metrics.items() if m["is_articulation_point"]
        ),
        "orphans": find_orphans(network_sweep_set, depends_edges + supplies_edges),
        "dangling_references": find_dangling_references(
            depends_edges, supplies_edges, network_sweep_set
        ),
        "log_patterns": find_log_patterns(pods_raw),
        "per_pod": per_pod_derived(pods_raw, graph_metrics, spof_pod_ids),
    }
