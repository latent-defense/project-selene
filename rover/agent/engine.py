"""Deterministic colony analysis engine (DDL-001).

Owns every graph fact. Computes topology, degree centrality, articulation points,
cycles, classified supply/dependency reconciliation, directed fragmentation,
cascade failure simulation, and the optimal Key-Player sets. No LLM involved —
results are exact and reproducible. The agent layer queries this via tools.py.
"""
import networkx as nx

from .config import classify_resource

# Criticality levels that propagate a hard failure downstream. A pod that loses a
# HIGH-criticality material input is modeled as going offline; that then cascades.
HARD_CASCADE_CRITICALITIES = {"high"}


class ColonyEngine:
    def __init__(self, map_doc: dict):
        self.map = map_doc
        self.pods = map_doc.get("pods", {})
        self.ids = sorted(self.pods.keys())
        # Directed dependency graph: edge A -> B means "A depends on B".
        self.G = nx.DiGraph()
        for pid in self.ids:
            info = self.pods[pid].get("info") or {}
            self.G.add_node(pid, role=info.get("role"), population=info.get("population", 0),
                            status=info.get("status"))
        for pid in self.ids:
            for dep in self.pods[pid].get("dependencies", []):
                tgt = dep.get("pod_id")
                if tgt:
                    self.G.add_edge(pid, tgt, resource=dep.get("resource"),
                                    criticality=(dep.get("criticality") or "").lower(),
                                    kind=classify_resource(dep.get("resource", "")))

    # ---- basic accessors -------------------------------------------------
    def role(self, pid):
        return (self.pods.get(pid, {}).get("info") or {}).get("role")

    def population(self, pid):
        return (self.pods.get(pid, {}).get("info") or {}).get("population", 0)

    def dependencies(self, pid):
        return self.pods.get(pid, {}).get("dependencies", [])

    def supplies(self, pid):
        return self.pods.get(pid, {}).get("supplies", [])

    # ---- centrality / topology ------------------------------------------
    def depended_upon(self):
        """How many pods depend on each pod (in-degree of A->B). Most-critical first."""
        rows = []
        for pid in self.ids:
            dependents = sorted(self.G.predecessors(pid))
            hi = [a for a in dependents
                  if (self.G[a][pid].get("criticality") in HARD_CASCADE_CRITICALITIES)]
            rows.append({"pod": pid, "role": self.role(pid),
                         "dependent_count": len(dependents), "dependents": dependents,
                         "high_criticality_dependents": hi})
        return sorted(rows, key=lambda r: (-r["dependent_count"], r["pod"]))

    def articulation_points(self):
        """Cut vertices on the undirected projection — removal disconnects the graph."""
        return sorted(nx.articulation_points(self.G.to_undirected()))

    def cycles(self):
        return [c for c in nx.simple_cycles(self.G)]

    # ---- reconciliation (DDL-006) ---------------------------------------
    def reconciliation(self):
        """Compare declared dependencies vs declared supplies, classified by resource.

        Only *material* asymmetries are integrity findings; administrative one-way
        flows are expected and reported separately.
        """
        # supplies index: supplier -> set(consumer), and (supplier, consumer) -> resource
        supply_pairs = {}
        for s in self.ids:
            for sup in self.supplies(s):
                t = sup.get("pod_id")
                if t:
                    supply_pairs[(s, t)] = sup.get("resource")
        # dependency index: consumer depends on supplier
        dep_pairs = {}
        for c in self.ids:
            for dep in self.dependencies(c):
                s = dep.get("pod_id")
                if s:
                    dep_pairs[(c, s)] = dep.get("resource")

        matched, supply_without_dep, dep_without_supply, administrative = [], [], [], []

        for (s, t), res in supply_pairs.items():
            kind = classify_resource(res or "")
            consumer_declares = (t, s) in dep_pairs
            rec = {"supplier": s, "consumer": t, "resource": res, "kind": kind}
            if kind == "administrative":
                administrative.append(rec)
            elif consumer_declares:
                matched.append(rec)
            else:
                supply_without_dep.append(rec)  # producer claims supply, consumer silent

        for (c, s), res in dep_pairs.items():
            kind = classify_resource(res or "")
            supplier_declares = (s, c) in supply_pairs
            if kind == "administrative" or supplier_declares:
                continue
            dep_without_supply.append({"consumer": c, "supplier": s, "resource": res, "kind": kind})

        return {
            "matched": matched,
            "material_supply_without_dependency": supply_without_dep,
            "material_dependency_without_supply": dep_without_supply,
            "administrative_flows": administrative,
        }

    # ---- fragmentation (DDL-005) ----------------------------------------
    @staticmethod
    def _fragmentation(g: nx.DiGraph) -> float:
        """Directed distance-based fragmentation: F = 1 - (sum 1/d)/(n(n-1)).

        Reachable, short-path graph -> low F. Disconnected (infinite distances
        dropped) -> F approaches 1. Generalizes the 2023 undirected measure to
        ordered pairs.
        """
        n = g.number_of_nodes()
        if n < 2:
            return None  # fragmentation undefined for <2 nodes
        total = 0.0
        lengths = dict(nx.all_pairs_shortest_path_length(g))
        for src, dmap in lengths.items():
            for dst, d in dmap.items():
                if dst != src and d > 0:
                    total += 1.0 / d
        return 1.0 - (total / (n * (n - 1)))

    @staticmethod
    def _round_frag(value):
        return round(value, 4) if value is not None else None

    def baseline_fragmentation(self):
        return self._round_frag(self._fragmentation(self.G))

    # ---- cascade failure simulation -------------------------------------
    def simulate_failure(self, removed, cascade_criticalities=HARD_CASCADE_CRITICALITIES):
        """Remove pods and propagate failure through hard material dependencies.

        A pod goes offline if it is removed, or it has a material dependency of
        cascade-criticality on an already-offline pod. Iterates to a fixpoint.
        """
        removed = [r for r in removed if r in self.G]
        offline = set(removed)
        changed = True
        while changed:
            changed = False
            for pid in self.ids:
                if pid in offline:
                    continue
                for dep in self.dependencies(pid):
                    tgt = dep.get("pod_id")
                    crit = (dep.get("criticality") or "").lower()
                    kind = classify_resource(dep.get("resource", ""))
                    if tgt in offline and kind == "material" and crit in cascade_criticalities:
                        offline.add(pid)
                        changed = True
                        break

        cascaded = sorted(offline - set(removed))
        surviving = [p for p in self.ids if p not in offline]
        remaining = self.G.subgraph(surviving).copy()
        return {
            "removed": list(removed),
            "cascaded_offline": cascaded,
            "total_offline": sorted(offline),
            "total_offline_count": len(offline),
            "surviving": surviving,
            "population_offline": sum(self.population(p) for p in offline),
            "fragmentation_after": self._round_frag(self._fragmentation(remaining)),
            "components_after": nx.number_weakly_connected_components(remaining) if remaining.number_of_nodes() else 0,
        }

    def failure_impact_ranking(self):
        """Each pod's individual blast radius (DDL-005 / DDL-008).

        Exhaustively simulates the removal of every single pod and ranks by how
        many pods (then residents) its failure takes offline via cascade. This is
        the meaningful "key player" metric here: it surfaces the pods whose loss is
        individually catastrophic. (A joint max-offline set search is misleading
        under cascades — once a hub fails, its cycle-partners are already down, so
        such a search pads the set with surviving independents like sentinel/vault.)
        """
        rows = []
        for p in self.ids:
            sim = self.simulate_failure([p])
            rows.append({
                "pod": p, "role": self.role(p),
                "total_offline": sim["total_offline_count"],
                "population_offline": sim["population_offline"],
                "cascaded_offline": sim["cascaded_offline"],
                "fragmentation_after": sim["fragmentation_after"],
            })
        return sorted(rows, key=lambda r: (-r["total_offline"], -r["population_offline"], r["pod"]))

    # ---- temporal layer --------------------------------------------------
    TIMELINE_KEYWORDS = ("decommission", "retired", "reroute", "rerouted", "consolidat",
                         "directive", "capacity", "backup", "redundan", "transferred",
                         "absorb", "single", "project ")

    def timeline(self):
        """All log entries sorted by time, with notable redundancy/capacity events flagged."""
        events = []
        for pid in self.ids:
            for entry in self.pods[pid].get("logs", []):
                text = f"{entry.get('event','')} {entry.get('detail','')}".lower()
                events.append({
                    "pod": pid, "timestamp": entry.get("timestamp"),
                    "event": entry.get("event"), "detail": entry.get("detail"),
                    "notable": any(k in text for k in self.TIMELINE_KEYWORDS),
                })
        events.sort(key=lambda e: e.get("timestamp") or "")
        return events

    def all_comms(self):
        msgs = []
        for pid in self.ids:
            for m in (self.pods[pid].get("comms") or []):
                msgs.append({"pod": pid, **m})
        msgs.sort(key=lambda m: m.get("timestamp") or "")
        return msgs

    # ---- consolidated facts ---------------------------------------------
    def facts(self):
        """One structured bundle of all deterministic findings."""
        return {
            "pod_count": len(self.ids),
            "pods": [{"id": p, "role": self.role(p), "population": self.population(p)} for p in self.ids],
            "baseline_fragmentation": self.baseline_fragmentation(),
            "depended_upon": self.depended_upon(),
            "articulation_points": self.articulation_points(),
            "cycles": self.cycles(),
            "reconciliation": self.reconciliation(),
            "failure_impact_ranking": self.failure_impact_ranking(),
            "notable_events": [e for e in self.timeline() if e["notable"]],
        }
