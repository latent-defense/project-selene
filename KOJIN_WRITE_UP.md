# Project Selene — Infrastructure Assessment

## Executive Summary

- **The colony is doubly centralized.** Material failure is concentrated in the `aquifer-helios-terminus` core. Operational response is concentrated in `Artemis`. Any one core failure takes **9 of 12 pods and 109 of 130 residents** offline. The remaining safety margin is mostly just local buffers: Zephyr `4h`, Medica `6h`, Aquifer `~2.9d`.
- **Mapping is deterministic.** The rover discovers the colony from the gateway, resolves all pods, crawls every endpoint, and writes a raw `map.json`. No LLM is involved.
- **Reporting is deterministic-first.** The engine computes topology, cascade, reconciliation, buffers, and comms summaries; the LLM only investigates through tools and explains those results.

---

## Decision 1 — Analyze Selene as a coupled failure-and-response system

The first pass through the data gave the obvious graph result: the colony contains a hidden catastrophic core. The second pass made the first result more urgent, not less.

On the material side, Borgatti's key-player framing is the right lens. The wrong question is "which pod has the most dependents?" The right question is "which removal most damages the colony?" On that metric, Aquifer, Helios, and Terminus are tied. They are not three separate risks; they are one tightly coupled industrial failure domain.

| Pod | Dependents | Pods offline if it fails |
|---|---|---|
| Aquifer | 8 | **9** |
| Helios | 8 | **9** |
| Terminus | 3 | **9** |

That table is the assignment in miniature. Terminus looks secondary under in-degree and is still collapse-equivalent under removal impact. The colony is therefore not organized around a broad distributed substrate; it is organized around one industrial core whose members share the same colony-scale blast radius.

The comms force a second lens. Selene is also a **response system**, and that system is centralized too. Artemis is the *sole* administrative authority — the only issuer of reserve management, approvals, and authorization, with no succession in the graph — and its standing posture toward escalated risk is deferral ("no action required", "standard channels", "next planning cycle"). The colony has centralized both **failure** and **response**.

The fatal part is that these two centralizations are not independent: **the response authority is contained inside the failure domain.** Artemis depends on Helios at high criticality, so a time-resolved cascade (`cascade_timeline`) shows Artemis going offline at **T+0h** in *every* core-failure scenario — before the cascade even reaches the buffered pods. The life-support buffers then expire inside the resulting authority vacuum: Zephyr's `backup_power_hours = 4` and Medica's `oxygen_reserve_hours = 6` both run out during a **6-hour window in which no node can sanction a failover.** This is a leader-election failure made quantitative — and the two reserves that could have *been* the failover, Vault's `water_backup` and `coolant_distribution`, were already decommissioned by Artemis directives 2093-089 and 2094-011. Buffers are not redundancy; they are countdown timers that outlast the decision-maker.

That is the main judgment of the project: **Selene is not merely fragile. The same failure that breaks the colony also removes the only authority that could coordinate a response.**

---

## Decision 2 — Keep the world model in code, not in the prompt

Once the problem became "material core + coordination relay + finite buffers," the epistemic boundary had to stay hard.

The LLM is useful here as an analyst interface, not as an analyst substrate. It cannot be trusted to derive topology, blast radius, reserve windows, or authority structure from raw logs and scattered endpoint payloads. So the system is split cleanly:

- The **engine** owns every measurement: SCCs, articulation points, hard-cascade components, blast radius, supply/dependency mismatches, buffer facts, and comms / coordination summaries.
- The **agent** owns selection and narration only. It calls tools like `failure_impact_ranking`, `topology_summary`, `buffer_summary`, `coordination_summary`, `reconciliation`, and targeted pod/log/comms accessors. It does not compute the facts it reports.

That split matters for reliability too. Mapping is deterministic by design. Reporting is deadline-bounded: if the model stalls, the rover emits a deterministic fallback report rather than hanging in `202 Accepted`. The fallback is blunter than the narrated report, but it preserves the actual analytical spine of the submission.

In other words: the validated colony model lives in code. The model can phrase the case; it does not get to invent the case.

---

## Architecture

`rover/agent/` is a straight pipeline:

- `discovery`: gateway BFS plus port-probe verification
- `mapping`: raw capture of `info`, `status`, `dependencies`, `supplies`, `logs`, `comms`
- `engine`: deterministic topology, cascade, reconciliation, buffer, and coordination analysis
- `tools`: progressive-disclosure accessors over that engine
- `llm`: provider-agnostic tool-use loop
- `reporting`: report generation with deterministic fallback

`map.json` is the durable artifact. Everything interesting happens at report time from deterministic measurements over that capture.

---

## What the agent found

- **The colony's reported state is misleading.** Every pod says `nominal`. The real signal is in graph structure, change logs, buffers, and informal comms.
- **The colony has one true industrial core.** `aquifer`, `helios`, and `terminus` form the only materially important cycle. Any one failing alone takes `9/12` pods and `109/130` residents offline.
- **The hard-failure backbone is narrow.** Most of the colony hangs off the main cascade component; `nexus`, `sentinel`, and `vault` survive mainly as disconnected islands.
- **The reserve story runs in one direction.** Across 2093–2094, Selene removed slack: Vault water backup, Vault coolant distribution, and Zephyr humidity reclaim. The result is not lean resilience; it is reduced recovery time.
- **Artemis is the coordination choke point — and a casualty of the failure it should coordinate.** It is the sole administrative authority with no succession, and it depends on the core at high criticality. The time-resolved cascade puts it offline at `T+0h` in every core scenario, opening a `6h` window in which Zephyr's `4h` and Medica's `6h` buffers expire with no one able to authorize a failover.
- **The failover reserves were already spent.** Vault's `water_backup` and `coolant_distribution` — the exact paths that would cover an Aquifer or Helios loss — were decommissioned by Artemis directives 2093-089 and 2094-011. The buffers that remain are countdown timers, not redundancy.
- **Phase 3 is the wrong next move unless the structure changes first.** Expansion increases load on exactly the parts of the colony that are already over-coupled.

---

## What I'd do with more time

- Refine the time-resolved cascade beyond declared hold-times: partial degradation, throughput-dependent drain rates, and a continuous wavefront rather than discrete buffer expiries.
- Formalize the coordination layer into a first-class relay/authority graph rather than deriving it from a comms summary.
- Clean up appendix presentation further: normalize cycle output, format topology more readably, and make the deterministic fallback read like a final report rather than a safe fallback.

---

*Artifacts:* agent code in [`rover/`](rover/); outputs in [`outputs/map.json`](outputs/map.json) and [`outputs/report.md`](outputs/report.md); companion dashboard at [moonstripe/selene-colony-monitor](https://github.com/moonstripe/selene-colony-monitor).
