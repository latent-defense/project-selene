# Selene Colony — Infrastructure Resilience Report
**Date:** 2094-08-15T00:00:00Z · **Scope:** all 12 pods · **Prepared by:** Independent Resilience Audit

---

## Executive Summary

**BLUF:** <u>A single undirected dependency cycle — Aquifer ↔ Helios ↔ Terminus — has zero redundancy and zero buffer separation; any one of its three nodes failing alone takes 9 of 12 pods and 109 of 130 residents offline instantly (fragmentation 0.77 → 1.00), and deliberate infrastructure consolidation directives between 2093 and 2094 systematically destroyed every backstop that once existed.</u>

A simple "most-depended-upon" count (degree-centrality) would flag Aquifer and Helios, both at 8 dependents, as the obvious targets — but would miss why Terminus is equally lethal despite having only 3 dependents. The KP-Neg / blast-radius lens reveals the mechanism: Aquifer, Helios, and Terminus form a tightly coupled mutual-dependency cycle (Aquifer needs Helios power and Terminus pump components; Helios needs Terminus silicon and Aquifer coolant; Terminus needs Aquifer slurry water and Helios electricity). This cycle makes all three nodes equivalent single points of failure — pull any vertex and the remaining two immediately lose a critical input, collapsing the full cascade. Terminus's degree-count of 3 gives no warning of this equivalence; only enumerating its individual blast radius (9 pods, 109 residents, fragmentation → 1.0) reveals that it is as dangerous as Helios. That is the core insight KP-Neg delivers that degree-counting cannot.

---

## 1. Inventory

| Pod | Role | Population | Key Buffer / Backup |
|---|---|---|---|
| **Aquifer** | Primary water recycling & distribution | 8 | Reservoir 120,000 L; throughput 42,000 L/day (~2.9 days); **0 backup systems** |
| **Helios** | Primary power generation (solar) | 12 | Battery reserve 78%; Vault coolant loop **decommissioned** Feb 2094 |
| **Terminus** | Regolith mining & raw material extraction | 9 | Regolith stockpile 145 t; slurry dual-feed **decommissioned** May 2093 |
| **Zephyr** | Atmospheric processing & O₂ generation | 10 | Backup power 4 h; humidity reclaim loop **retired** Jun 2093; **0% internal moisture buffer** |
| **Hydroponics** | Food production & agriculture | 14 | 24 grow bays; no independent water store |
| **Medica** | Medical services & healthcare | 16 | Pharmacy stock 12 days; O₂ reserve 6 h only |
| **Nexus** | Communications relay & data routing | 6 | Independent battery array: **30 days**; onboard micro-recycler |
| **Prometheus** | Research & pharmaceutical synthesis | 11 | Synthesis water re-routed through Hydroponics circuit (pipe consolidation 2093-P4) |
| **Artemis** | Colony command & administration | 18 | None declared |
| **Forge** | Manufacturing & fabrication | 11 | None declared |
| **Sentinel** | External monitoring & defense | 8 | Independent solar array (180 kW), grid-independent |
| **Vault** | Emergency reserves & backup systems | 7 | Secondary water system → maintenance reserve Mar 2093; coolant equipment transferred Jan 2094 |

---

## 2. Critical Pods & Blast Radius

**The three key players are Aquifer, Helios, and Terminus, each tied at rank 1 with an individual blast radius of 9 pods and 109 residents offline (fragmentation 0.77 → 1.00).** This is confirmed by `failure_impact_ranking` and validated by `simulate_failure` on each pod individually. They are equivalent single points of failure because the cycle `aquifer → helios → terminus → aquifer` (identified by `graph_metrics`) means each node is simultaneously a supplier to and a dependent of the others — there is no arc in the cycle that can be severed without collapsing the whole ring. Aquifer supplies coolant to Helios and slurry water to Terminus; Helios supplies electrical power to both; Terminus supplies silicon feedstock back to Helios and pump components back to Aquifer. All three mutual dependencies are classified `material` by `reconciliation`, meaning they are hard operational links, not administrative ones. The only surviving pods after any one failure are Nexus (independent 30-day battery), Sentinel (independent solar array), and Vault (no power or water dependency), which together hold only 21 residents — 16% of the colony.

**Terminus is the hidden single point of failure that degree-counting conceals.** Its `dependent_count` is just 3 (only Aquifer, Forge, and Helios depend on it), making it appear far less critical than Aquifer or Helios on a raw in-degree ranking. Yet its blast radius is identical: 9 pods, 109 residents. The path from Terminus failure to medical collapse runs: Terminus fails → Helios loses silicon feedstock (high criticality) and Aquifer loses pump components (medium, but uniquely sourced) → Helios and Aquifer collapse → Zephyr loses both electrical power and 100% of its humidity feedstock (internal reclaim loop retired Jun 2093; confirmed in Zephyr comms 2094-03-18: *"our humidity feedstock draw from Aquifer is now 100% of our atmospheric moisture budget"*) → Hydroponics loses power and irrigation → Prometheus loses nutrient compounds from Hydroponics → Medica loses pharmaceuticals from Prometheus and medical-grade oxygen from Zephyr, with only a 6-hour O₂ reserve and 12-day pharmacy stock before clinical operations degrade.

---

## 3. Iterative Dynamics: Buffers and the Time Dimension

The graph topology tells you *who* falls; buffer metadata tells you *how fast*. These two dimensions are not equivalent, and the difference has direct operational meaning. Not all edges in the dependency graph carry the same latency to failure: a node with deep reserves will degrade slowly even after its supplier fails, buying time for intervention, whereas a node with no buffer fails in lockstep with its upstream. The blast-radius metric captures the topological cascade correctly, but the timeline of that cascade is shaped entirely by the buffer layer — and that layer has been systematically stripped.

Zephyr is the starkest example of deliberate buffer elimination. In June 2093, its internal humidity reclamation loop — which provided a degree of moisture self-sufficiency — was retired under an infrastructure simplification directive, bringing `humidity_reclaim_pct` to **0**. Simultaneously, `backup_power_hours` sits at just **4 hours**, confirmed in Zephyr's own comms to Helios ops (2094-05-02): *"we've got about 4 hours of reserve on our end before the processors would need to start cycling down."* This means a power interruption from Helios begins degrading O₂ output within 4 hours — and since Medica holds only a **6-hour O₂ reserve**, the window from grid failure to clinical oxygen loss is approximately 2 hours of net margin, colony-wide. Contrast this with Nexus, which carries a **30-day independent power reserve** and an onboard micro-water recycler: this is precisely why Nexus survives every simulated cascade and remains a functioning communications island even when 9 pods go dark. At Aquifer, the `reservoir_capacity_l` of 120,000 L divided by `throughput_l_day` of 42,000 L yields roughly **2.9 days** of water autonomy — meaning Helios failure triggers Aquifer pump failure near-immediately (pumps are electrically driven), but even if pumps held, the reservoir is exhausted in under 3 days. The Helios battery bank, previously cooled by a redundant Vault coolant loop, lost that backup in February 2094 under Directive 2094-011, making Aquifer the sole thermal regulator for Helios batteries — another cross-dependency tightening that was logged but not risk-assessed. Terminus's slurry processing, formerly on a **dual-feed** configuration with a redundant plumbing path, was simplified to a **single Aquifer loop** in May 2093 (Terminus log, 2093-05-11): redundant plumbing decommissioned. The pattern across 2093–2094 is consistent: each consolidation directive (2093-089, 2093-P4, 2094-011) eliminated a buffer or redundant path in exchange for maintenance savings, and the cumulative effect is that the cycle now has near-zero latency to total collapse.

---

## 4. Recommendations

1. **Break the Aquifer–Helios–Terminus cycle immediately by installing a secondary power source (battery bank or micro-reactor) at Aquifer, independent of Helios** — this single intervention degrades the cycle's mutual lock and is the fastest risk reduction available given Aquifer's role as the universal water supplier to 7 pods.
2. **Restore a redundant water feed to Terminus slurry processing** (reinstate the dual-feed configuration decommissioned May 2093), eliminating Terminus's brittle single-point dependency on Aquifer and reducing Terminus's blast radius from 109 to a containable subset.
3. **Reinstate Zephyr's internal humidity reclamation loop** (retired Jun 2093) and extend its backup power reserve beyond 4 hours, given that Medica's 6-hour O₂ reserve creates a 2-hour intervention window from any grid failure — insufficient for a lunar colony with no external emergency services.
4. **Restore or replace the Vault secondary water backup system** decommissioned under Directive 2093-089 to provide Aquifer with an emergency bypass; at 91.6% utilisation and 0 declared backup systems, any Aquifer maintenance event or failure leaves the entire colony waterless with no fallback.
5. **Commission a supply declaration from Forge to formally register its `replacement_pumps` flow to Aquifer** — `reconciliation` flags this as a material supply without a matching dependency declaration, meaning it is invisible to cascade modelling and could be silently discontinued without triggering any dependency alert.
6. **Mandate a buffer-impact assessment as a prerequisite for all future infrastructure consolidation directives**, given that Directives 2093-089, 2093-P4, and 2094-011 each individually appeared operationally benign but collectively removed every redundant path in the colony's most critical failure domain ahead of Phase 3 expansion.

---

# Appendix: Computed Metrics (deterministic)

- Pods analyzed: **12**
- Baseline directed fragmentation: **0.7689**
- Articulation points (cut vertices): **helios**
- Dependency cycles: **[['aquifer', 'helios', 'terminus'], ['aquifer', 'helios'], ['aquifer', 'terminus'], ['aquifer', 'terminus', 'helios'], ['terminus', 'helios']]**

## Most depended-upon pods

| Pod | Role | Dependents | High-criticality dependents |
|---|---|---|---|
| aquifer | Primary water recycling and distribution | 8 | hydroponics, terminus |
| helios | Primary power generation (solar array) | 8 | aquifer, artemis, forge, hydroponics, terminus, zephyr |
| terminus | Regolith mining and raw material extraction | 3 | forge, helios |
| zephyr | Atmospheric processing and oxygen generation | 2 | medica |
| hydroponics | Food production and agricultural systems | 1 | prometheus |
| nexus | Communications relay and data routing | 1 | — |
| prometheus | Research and pharmaceutical synthesis | 1 | medica |

## Single-pod failure impact (blast radius)

_Each row: the colony-wide impact of that one pod failing, via cascade. The top pods (tied) are the true key players / single points of failure._

| Pod | Role | Pods offline | Pop. offline | Cascades to |
|---|---|---|---|---|
| aquifer | Primary water recycling and distribution | 9 | 109 | artemis, forge, helios, hydroponics, medica, prometheus, terminus, zephyr |
| helios | Primary power generation (solar array) | 9 | 109 | aquifer, artemis, forge, hydroponics, medica, prometheus, terminus, zephyr |
| terminus | Regolith mining and raw material extraction | 9 | 109 | aquifer, artemis, forge, helios, hydroponics, medica, prometheus, zephyr |
| hydroponics | Food production and agricultural systems | 3 | 41 | medica, prometheus |
| prometheus | Research and pharmaceutical synthesis | 2 | 27 | medica |
| zephyr | Atmospheric processing and oxygen generation | 2 | 26 | medica |
| artemis | Colony command and administration | 1 | 18 | — |
| medica | Medical services and healthcare | 1 | 16 | — |
| forge | Manufacturing and fabrication | 1 | 11 | — |
| sentinel | External monitoring and defense systems | 1 | 8 | — |
| vault | Emergency reserves and backup systems | 1 | 7 | — |
| nexus | Communications relay and data routing | 1 | 6 | — |

## Material supply/dependency mismatches

- Matched material edges: **23**
- Supply claimed but consumer doesn't declare dependency: **10**
  - `forge` → `aquifer` (replacement_pumps)
  - `forge` → `terminus` (cutting_tools)
  - `forge` → `artemis` (fabricated_components)
  - `helios` → `medica` (electrical_power)
  - `hydroponics` → `artemis` (fresh_produce)
  - `hydroponics` → `medica` (dietary_supplements)
  - `nexus` → `sentinel` (comms_relay)
  - `sentinel` → `nexus` (sensor_feeds)
  - `vault` → `artemis` (emergency_rations)
  - `zephyr` → `artemis` (atmospheric_regulation)
- Dependency declared but supplier doesn't acknowledge: **1**
  - `prometheus` depends on `aquifer` (synthesis_water)