"""Mapping agent: discover the colony, crawl every endpoint, write a raw map.json.

map.json is a faithful capture (DDL-002): raw endpoint bodies plus per-endpoint
fetch_meta. All analysis is deferred to the reporting step.
"""
import json
import logging
import sys
import time

from . import config
from .discovery import discover
from .http_client import get_json

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
log = logging.getLogger("mapping")


def crawl_pod(pod_id: str, base_url: str):
    """Crawl all six endpoints for one pod. Returns the per-pod map entry."""
    entry = {"endpoint": base_url, "fetch_meta": {}}
    for ep in config.ENDPOINTS:
        data, meta = get_json(f"{base_url}/{ep}")
        entry["fetch_meta"][ep] = meta
        # Normalize: unwrap the list-bearing endpoints; comms 404 -> None (expected).
        if ep == "comms" and meta.get("http_status") == 404:
            entry["comms"] = None
        elif ep in ("dependencies", "supplies", "logs"):
            entry[ep] = (data or {}).get(ep, []) if data else []
        elif ep == "comms":
            entry["comms"] = (data or {}).get("messages", []) if data else None
        else:  # info, status
            entry[ep] = data
    return entry


def build_map():
    started = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    registry, disc_errors = discover()
    log.info("discovery complete: %d pods", len(registry))

    pods = {}
    errors = list(disc_errors)
    for pod_id, info in sorted(registry.items()):
        pods[pod_id] = crawl_pod(pod_id, info["url"])
        # Surface hard failures (a missing /info means we can't trust this pod's data).
        fm = pods[pod_id]["fetch_meta"]
        for required in ("info", "dependencies", "supplies", "status", "logs"):
            if fm[required].get("http_status") != 200:
                errors.append(f"{pod_id}/{required} returned {fm[required].get('http_status')} ({fm[required].get('error')})")

    ports = sorted(info["port"] for info in registry.values())
    comms_pods = sorted(pid for pid, e in pods.items() if e.get("comms"))

    doc = {
        "meta": {
            "generated_at": started,
            "frozen_now": config.COLONY_NOW,
            "discovery_method": "gateway BFS + port-probe verify",
            "gateway_url": config.GATEWAY_URL,
            "pods_found": len(pods),
            "ports_resolved": ports,
            "comms_pods": comms_pods,
            "errors": errors,
        },
        "pods": pods,
    }
    return doc


def main():
    try:
        doc = build_map()
    except Exception as e:
        log.error("mapping failed: %s", e)
        return 1

    if doc["meta"]["pods_found"] == 0:
        log.error("no pods discovered — aborting")
        return 1

    with open(config.MAP_PATH, "w") as f:
        json.dump(doc, f, indent=2)

    log.info("wrote %s (%d pods, %d comms channels)",
             config.MAP_PATH, doc["meta"]["pods_found"], len(doc["meta"]["comms_pods"]))
    if doc["meta"]["pods_found"] != 12:
        log.warning("expected 12 pods, found %d", doc["meta"]["pods_found"])
    if doc["meta"]["errors"]:
        log.warning("%d crawl warnings recorded in map.meta.errors", len(doc["meta"]["errors"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
