"""Progressive-disclosure tools over the deterministic engine (DDL-001 / DDL-003).

The agent pulls detail on demand instead of receiving the whole map up front:
broad accessors (list_pods, graph_metrics) orient it; targeted ones (get_logs,
simulate_failure) let it chase specific leads. Every tool result comes straight
from ColonyEngine, so the numbers the model reports are the verified numbers.
"""

# Provider-neutral tool specs. llm.py translates these per SDK.
TOOL_SPECS = [
    {
        "name": "list_pods",
        "description": "List all colony pods with id, role, population, and reported status.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "get_pod",
        "description": "Get one pod's info, status, declared dependencies, and declared supplies.",
        "input_schema": {"type": "object", "properties": {"pod_id": {"type": "string"}}, "required": ["pod_id"]},
    },
    {
        "name": "get_logs",
        "description": "Get the full operational log history for one pod (use to trace how infrastructure changed over time).",
        "input_schema": {"type": "object", "properties": {"pod_id": {"type": "string"}}, "required": ["pod_id"]},
    },
    {
        "name": "get_comms",
        "description": "Get informal inter-pod engineer messages for one pod (only ~5 pods have these; returns null otherwise).",
        "input_schema": {"type": "object", "properties": {"pod_id": {"type": "string"}}, "required": ["pod_id"]},
    },
    {
        "name": "graph_metrics",
        "description": "Deterministic graph metrics: most-depended-upon pods, articulation points (cut vertices), dependency cycles, and baseline fragmentation.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "reconciliation",
        "description": "Compare what pods say they SUPPLY vs what others say they DEPEND on, classified into material vs administrative. Material asymmetries are integrity findings.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "simulate_failure",
        "description": "Remove one or more pods and propagate the failure through hard material dependencies. Returns cascaded outages, population offline, and post-failure fragmentation.",
        "input_schema": {"type": "object", "properties": {"pod_ids": {"type": "array", "items": {"type": "string"}}}, "required": ["pod_ids"]},
    },
    {
        "name": "failure_impact_ranking",
        "description": "Rank every pod by its INDIVIDUAL blast radius: simulate each pod failing alone and count how many pods/residents go offline via cascade. The strongest single-point-of-failure detector — the top pods are the true key players.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "timeline",
        "description": "Chronological log events across all pods. Set notable_only=true for redundancy/capacity/decommissioning events.",
        "input_schema": {"type": "object", "properties": {"notable_only": {"type": "boolean"}}},
    },
]


def dispatch(engine, name: str, args: dict):
    args = args or {}
    if name == "list_pods":
        return [{"id": p, "role": engine.role(p), "population": engine.population(p),
                 "status": (engine.pods[p].get("status") or {}).get("status")} for p in engine.ids]
    if name == "get_pod":
        p = args["pod_id"]
        e = engine.pods.get(p)
        if not e:
            return {"error": f"unknown pod '{p}'"}
        return {"info": e.get("info"), "status": e.get("status"),
                "dependencies": e.get("dependencies"), "supplies": e.get("supplies")}
    if name == "get_logs":
        return engine.pods.get(args["pod_id"], {}).get("logs", [])
    if name == "get_comms":
        return engine.pods.get(args["pod_id"], {}).get("comms")
    if name == "graph_metrics":
        return {"depended_upon": engine.depended_upon(),
                "articulation_points": engine.articulation_points(),
                "cycles": engine.cycles(),
                "baseline_fragmentation": engine.baseline_fragmentation()}
    if name == "reconciliation":
        return engine.reconciliation()
    if name == "simulate_failure":
        return engine.simulate_failure(args.get("pod_ids", []))
    if name == "failure_impact_ranking":
        return engine.failure_impact_ranking()
    if name == "timeline":
        events = engine.timeline()
        if args.get("notable_only"):
            events = [e for e in events if e["notable"]]
        return events
    return {"error": f"unknown tool '{name}'"}
