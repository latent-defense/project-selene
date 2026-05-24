# Project Selene — Infrastructure Assessment

**Kojin Glick.** Terse by intent. Two decisions carry the work; everything else serves them.

## TL;DR

- **Mapping** is fully deterministic: discover (gateway BFS + port-probe), crawl all six endpoints, write a raw `map.json`. No LLM.
- **Reporting** is a deterministic graph **engine** + an LLM that *narrates* over it via tools. The engine measures; the model explains.
- **Headline finding:** Aquifer, Helios, and Terminus form a high-criticality dependency **cycle**. Any one failing alone takes **9 of 12 pods and 109 of 130 residents** offline. Every pod reports `nominal`.
- A separate read-only **dashboard** ([moonstripe/selene-colony-monitor](https://github.com/moonstripe/selene-colony-monitor)) visualizes the graph, runs what-if failure injection, and streams the agent's tool-use loop. It touches none of this repo.

---

## Decision 1 — Borgatti's key players, because in-degree lies

The obvious criticality metric is **in-degree** ("most depended-upon"). Here it is wrong, and provably so.

Borgatti reframes the question (*Identifying sets of key players in a social network*, Comput. Math. Organ. Theory, 2006 — https://doi.org/10.1007/s10588-006-7084-x): a key player is not the most *connected* node, it is the node whose **removal most disrupts the network** (KPP-Neg = maximize fragmentation on removal). Connectedness and criticality are different questions.

**The Terminus case study makes this concrete:**

| Pod | In-degree (dependents) | Blast radius (pods offline if it fails) |
|---|---|---|
| Aquifer | 8 | **9** |
| Helios | 8 | **9** |
| **Terminus** | **3** | **9** |

By in-degree, Terminus looks minor — only 3 pods depend on it. But **Helios depends *critically* on Terminus** for silicon feedstock; remove Terminus → Helios fails → the colony cascades. Terminus's blast radius equals Aquifer's and Helios's. In-degree hides it; removal-impact reveals it. **This is the hidden single point of failure the assignment asks for, and only the Borgatti lens finds it.**

**Operationalization.** We compute each pod's blast radius by cascade simulation — failure propagates through high-criticality *material* dependencies to a fixpoint — a directed, criticality-weighted adaptation of KPP-Neg's "fragment-on-removal." Key players = the pods tied at the top: **{aquifer, helios, terminus}**.

*Honest note:* we also built the exhaustive joint-set KPP-Neg search. Under cascade semantics it degenerates — once Aquifer is removed it already downs its cycle-partners, so a "maximize total offline" set search pads itself with *surviving independents* (sentinel, vault) that aren't critical at all. Per-node blast radius is the faithful operationalization for a cascading graph. The engine still exposes `simulate_failure(set)` for arbitrary what-ifs.

---

## Decision 2 — The agent sits on *our* side of the analyst / world-model divide

An LLM has no world model. It cannot be trusted to *measure* a graph — it miscounts cascades and invents numbers. So we drew a hard line through the system:

- The **deterministic engine** owns every measurement: topology, fragmentation, blast radius, supply/dependency reconciliation, cascade. These are **verifiable and valid by construction** — exact, reproducible, exhaustive at n=12.
- The **agent** owns *selection and narration only*. Through progressive-disclosure tools it pulls the measures it needs and explains them. **It never computes them.**

Put plainly: the **world model** — the validated representation of the colony — lives in code, on the analyst's side of the divide. The agent stands there *with us*, querying that model and ferrying its measures to the reader; it does not *become* the model. We treat the **agent as fallible** and the **measures as sound**. Because every number the agent emits came from a tool call, its fallibility is bounded to *what to look at and how to phrase it* — never to the facts themselves.

This is why **mapping uses no LLM at all**, and why reporting keeps the model strictly downstream of the engine. The same tools power the dashboard's chat, so you can watch the loop: the agent calls `list_pods`, `simulate_failure`, `reconciliation` — and answers from what they return.

---

## Architecture (brief)

`rover/agent/`: `discovery` (gateway BFS + port-probe, cross-checked) → `mapping` (raw capture) → `engine` (all deterministic metrics) → `tools` (progressive disclosure) → `llm` (provider-agnostic tool loop) → `reporting`. `map.json` is a faithful raw capture; all analysis happens at report time. Provider-agnostic via `LLM_API_KEY` (Anthropic or OpenAI).

## What the agent found

- **`nominal` is a red herring** — `/status` is uniformly nominal by design. The signal is in the graph, logs, and comms.
- **Zero-redundancy core:** helios/aquifer/terminus cycle; each individually catastrophic (9/12 pods, 109/130 residents).
- **Buffers were systematically decommissioned 2093–2094** (directives 2093-089, 2093-P4, 2094-011): Vault water backup, Helios backup coolant, Zephyr humidity reclaim (now 0%). *The graph says who falls; buffers say how fast* — Zephyr holds 4h on backup power, Medica 6h of O₂ → a ~2h colony-wide O₂ margin after a power loss.
- **Reconciliation** surfaced material mismatches (e.g. `prometheus → aquifer` synthesis water rerouted through hydroponics) — coupled failure domains invisible from any single pod's data.

## What I'd do with more time

- Time-resolved cascade (buffer-weighted) to rank failures by *time-to-impact*, not just reach.
- Provenance/confidence tags on each measure handed to the agent.
- Persist and evaluate agent tool-selection traces.

---

*Artifacts:* agent code in [`rover/`](rover/); outputs in [`outputs/map.json`](outputs/map.json) and [`outputs/report.md`](outputs/report.md); dashboard at [moonstripe/selene-colony-monitor](https://github.com/moonstripe/selene-colony-monitor).
