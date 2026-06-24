"""Deterministic colony analysis engine (DDL-001).

Owns every graph fact. Computes topology, degree centrality, articulation points,
cycles, classified supply/dependency reconciliation, directed fragmentation,
cascade failure simulation, and the optimal Key-Player sets. No LLM involved —
results are exact and reproducible. The agent layer queries this via tools.py.
"""
import heapq
from collections import Counter

import networkx as nx

from .config import classify_resource

# Criticality levels that propagate a hard failure downstream. A pod that loses a
# HIGH-criticality material input is modeled as going offline; that then cascades.
HARD_CASCADE_CRITICALITIES = {"high"}

# Metadata fields that state, explicitly, how long a pod keeps running after it
# loses its critical input. Value -> hours via the multiplier. We only assign a
# hold-time where the colony itself declares one; every other pod is treated as
# zero-hold (fails the instant its input does). No invented durations.
BUFFER_HOLD_FIELDS = {
    "backup_power_hours": 1.0,
    "oxygen_reserve_hours": 1.0,
    "independent_power_days": 24.0,
}
COORDINATION_RISK_KEYWORDS = (
    "contingency", "standard channels", "no action required", "allocation",
    "backup", "reserve", "downtime", "planning", "records", "flag"
)


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

    def metadata(self, pid):
        return (self.pods.get(pid, {}).get("info") or {}).get("metadata") or {}

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

    def bridges(self):
        return [sorted(e) for e in nx.bridges(self.G.to_undirected())]

    def _hard_cascade_graph(self):
        h = nx.DiGraph()
        h.add_nodes_from(self.ids)
        for pid in self.ids:
            for dep in self.dependencies(pid):
                if classify_resource(dep.get("resource", "")) != "material":
                    continue
                if (dep.get("criticality") or "").lower() in HARD_CASCADE_CRITICALITIES and dep.get("pod_id"):
                    h.add_edge(pid, dep["pod_id"], resource=dep.get("resource"))
        return h

    def topology_summary(self):
        """Structural summary of the colony graph and hard-failure backbone."""
        sccs = sorted((sorted(c) for c in nx.strongly_connected_components(self.G)),
                      key=lambda c: (-len(c), c))
        core = next((c for c in sccs if len(c) > 1), [])
        h = self._hard_cascade_graph()
        hard_components = sorted((sorted(c) for c in nx.weakly_connected_components(h)),
                                 key=lambda c: (-len(c), c))
        condensation = nx.condensation(self.G)
        members = {}
        for node, cid in condensation.graph["mapping"].items():
            members.setdefault(cid, []).append(node)
        source_components = [sorted(members[c]) for c in condensation.nodes() if condensation.in_degree(c) == 0]
        sink_components = [sorted(members[c]) for c in condensation.nodes() if condensation.out_degree(c) == 0]
        return {
            "core_cycle": core,
            "strongly_connected_components": sccs,
            "articulation_points": self.articulation_points(),
            "bridges": self.bridges(),
            "hard_cascade_components": hard_components,
            "source_components": sorted(source_components),
            "sink_components": sorted(sink_components),
            "betweenness": [
                {"pod": pod, "score": round(score, 4)}
                for pod, score in sorted(nx.betweenness_centrality(self.G).items(),
                                         key=lambda x: (-x[1], x[0]))
            ],
        }

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

    # ---- authority / time-resolved cascade ------------------------------
    def _hold_hours(self, pid):
        """Hours this pod keeps running after losing its critical input, taken
        only from explicit metadata (BUFFER_HOLD_FIELDS). Zero if undeclared."""
        md = self.metadata(pid)
        vals = [md[f] * mult for f, mult in BUFFER_HOLD_FIELDS.items() if isinstance(md.get(f), (int, float))]
        return max(vals) if vals else 0.0

    def _authority_pods(self, min_consumers=2):
        """Pods that issue administrative authority (approvals, authorization,
        oversight, reserve management) to multiple others. These are the only
        nodes that can sanction a failover; the colony has no election to
        replace them. Derived from declared administrative supply edges."""
        issued = Counter()
        for s in self.ids:
            consumers = {sup.get("pod_id") for sup in self.supplies(s)
                         if classify_resource(sup.get("resource", "")) == "administrative" and sup.get("pod_id")}
            issued[s] = len(consumers)
        return sorted([p for p, c in issued.items() if c >= min_consumers])

    def cascade_timeline(self, removed):
        """Time-resolved cascade: order the failures by accumulated buffer hold-time
        and mark when administrative authority is lost.

        A removed pod fails at hour 0. A pod that loses a hard material input fails
        at the input's failure time plus its own declared hold-time (Dijkstra over
        the hard-cascade graph, edge weight = the downstream pod's hold hours). The
        sole authority (see _authority_pods) can only sanction a failover while it
        is itself online — so any buffer that expires after the authority is gone is
        burning with no one able to approve a response. That is the leader-election
        failure made quantitative.
        """
        removed = [r for r in removed if r in self.G]
        h = self._hard_cascade_graph()  # edge p->q means p depends (hard, material) on q
        fail_at = {r: 0.0 for r in removed}
        trigger = {r: None for r in removed}
        pq = [(0.0, r) for r in removed]
        while pq:
            t, q = heapq.heappop(pq)
            if t > fail_at.get(q, float("inf")):
                continue
            for p in h.predecessors(q):  # pods that depend on q
                cand = t + self._hold_hours(p)
                if cand < fail_at.get(p, float("inf")):
                    fail_at[p], trigger[p] = cand, q
                    heapq.heappush(pq, (cand, p))

        authority = self._authority_pods()
        auth_times = [fail_at[a] for a in authority if a in fail_at]
        auth_offline_at = min(auth_times) if auth_times else None

        events = []
        for pid in sorted(fail_at, key=lambda p: (fail_at[p], p)):
            t = fail_at[pid]
            hold = self._hold_hours(pid)
            events.append({
                "pod": pid, "role": self.role(pid), "population": self.population(pid),
                "fails_at_h": round(t, 2), "hold_hours": round(hold, 2),
                "triggered_by": trigger[pid],
                "removed": pid in removed,
                "authority_online": auth_offline_at is None or t < auth_offline_at,
            })

        burned = [e["pod"] for e in events
                  if e["hold_hours"] > 0 and auth_offline_at is not None and e["fails_at_h"] > auth_offline_at]
        last = max((e["fails_at_h"] for e in events), default=0.0)
        return {
            "removed": list(removed),
            "authority_pods": authority,
            "mode": "decapitation" if auth_offline_at is not None else "authority-survives",
            "authority_offline_at_h": round(auth_offline_at, 2) if auth_offline_at is not None else None,
            "window_without_authority_h": round(last - auth_offline_at, 2) if auth_offline_at is not None else None,
            "events": events,
            "buffers_burned_without_authority": burned,
        }

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

    def coordination_summary(self):
        """Summarize the comms / permission layer visible in informal messages."""
        msgs = self.all_comms()
        senders = Counter()
        receivers = Counter()
        flagged = []
        artemis_inbound = []
        artemis_broadcasts = []
        for m in msgs:
            sender = m.get("from") or "unknown"
            receiver = m.get("to") or "unknown"
            senders[sender] += 1
            receivers[receiver] += 1
            text = (m.get("content") or "").lower()
            rec = {
                "pod": m.get("pod"),
                "timestamp": m.get("timestamp"),
                "from": sender,
                "to": receiver,
                "content": m.get("content"),
            }
            if receiver == "artemis_admin":
                artemis_inbound.append(rec)
            if sender.startswith("artemis_") and receiver == "all_pods":
                artemis_broadcasts.append(rec)
            if any(k in text for k in COORDINATION_RISK_KEYWORDS):
                flagged.append(rec)
        return {
            "message_count": len(msgs),
            "senders": [{"node": n, "count": c} for n, c in senders.most_common()],
            "receivers": [{"node": n, "count": c} for n, c in receivers.most_common()],
            "artemis_admin_inbound": artemis_inbound,
            "artemis_broadcasts": artemis_broadcasts,
            "flagged_messages": flagged,
        }

    def buffer_summary(self):
        """Extract explicit hold-time / reserve / backup facts from pod metadata."""
        rows = []
        for pid in self.ids:
            md = self.metadata(pid)
            row = {"pod": pid, "role": self.role(pid), "population": self.population(pid), "buffer_facts": {}}
            for key in (
                "backup_power_hours", "independent_power_days", "oxygen_reserve_hours",
                "pharmacy_stock_days", "emergency_ration_days", "humidity_reclaim_pct",
                "reservoir_capacity_l", "throughput_l_day", "backup_systems",
                "decommissioned_reserves", "prometheus_water_share_pct",
            ):
                if key in md:
                    row["buffer_facts"][key] = md[key]
            if pid == "aquifer" and md.get("reservoir_capacity_l") and md.get("throughput_l_day"):
                row["buffer_facts"]["water_cover_days_estimate"] = round(
                    md["reservoir_capacity_l"] / md["throughput_l_day"], 2
                )
            if row["buffer_facts"]:
                rows.append(row)
        return rows

    # ---- consolidated facts ---------------------------------------------
    def facts(self):
        """One structured bundle of all deterministic findings."""
        return {
            "pod_count": len(self.ids),
            "pods": [{"id": p, "role": self.role(p), "population": self.population(p)} for p in self.ids],
            "baseline_fragmentation": self.baseline_fragmentation(),
            "depended_upon": self.depended_upon(),
            "articulation_points": self.articulation_points(),
            "bridges": self.bridges(),
            "cycles": self.cycles(),
            "topology_summary": self.topology_summary(),
            "reconciliation": self.reconciliation(),
            "failure_impact_ranking": self.failure_impact_ranking(),
            "core_failure_timelines": {p: self.cascade_timeline([p]) for p in self.topology_summary()["core_cycle"]},
            "buffer_summary": self.buffer_summary(),
            "coordination_summary": self.coordination_summary(),
            "notable_events": [e for e in self.timeline() if e["notable"]],
        }
