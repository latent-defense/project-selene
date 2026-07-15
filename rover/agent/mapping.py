"""Mapping pipeline entrypoint.

Orchestrates: discovery → crawl → deterministic analysis → (optional) LLM tag
→ write /rover/output/map.json.

Invoked from run_mapping.sh as `python -m agent.mapping [--llm-tag]`.
The --llm-tag flag is gated via MAPPING_FLAGS env var per the plan, so the
entry-point script stays static while still allowing the variant test.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import httpx

from agent.analysis import analyze
from agent.client import fetch_pod
from agent.discovery import KNOWN_POD_IDS, discover

OUTPUT_PATH = Path("/rover/output/map.json")
GATEWAY_URL = os.environ.get("GATEWAY_URL", "http://gateway:3000")
AGENT_VERSION = "1.0"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(name)s %(levelname)s %(message)s",
)
log = logging.getLogger("mapping")


async def amain(args: argparse.Namespace) -> int:
    log.info("Mapping pipeline start. gateway=%s llm_tag=%s", GATEWAY_URL, args.llm_tag)

    async with httpx.AsyncClient(limits=httpx.Limits(max_connections=64)) as client:
        log.info("Discovery phase")
        discovery = await discover(client, GATEWAY_URL)
        log.info(
            "Discovery complete: gateway_walk=%d network_sweep=%d total_reachable=%d",
            len(discovery.gateway_walk_set),
            len(discovery.network_sweep_set),
            len(discovery.pods),
        )

        pod_ids = sorted(discovery.pods.keys())
        log.info("Crawl phase (%d pods, 6 endpoints each)", len(pod_ids))
        crawled = await asyncio.gather(
            *(fetch_pod(client, discovery.pods[pid]) for pid in pod_ids)
        )
        pods_raw = dict(zip(pod_ids, crawled))

    log.info("Analysis phase (deterministic)")
    analysis = analyze(pods_raw, discovery.network_sweep_set)

    llm_tags: dict[str, dict] = {}
    if args.llm_tag:
        log.info("LLM tag phase (--llm-tag enabled)")
        from agent.llm_tag import tag_all
        llm_tags = await tag_all(pods_raw)
        log.info("LLM tag complete: %d/%d pods tagged", len(llm_tags), len(pods_raw))

    now = datetime.now(timezone.utc).isoformat()
    map_out = {
        "metadata": {
            "discovered_at": now,
            "pod_count": len(pods_raw),
            "agent_version": AGENT_VERSION,
            "llm_tagged": args.llm_tag,
            "known_pod_ids": list(KNOWN_POD_IDS),
            "gateway_walk_set": sorted(discovery.gateway_walk_set),
            "network_sweep_set": sorted(discovery.network_sweep_set),
        },
        "pods": {
            pid: {
                "raw": pods_raw[pid],
                "discovered_via": discovery.discovered_via(pid),
                "derived": analysis["per_pod"].get(pid, {}),
                **({"llm_tags": llm_tags[pid]} if pid in llm_tags else {}),
            }
            for pid in pod_ids
        },
        "graph": {
            "edges_declared_depends": analysis["edges_declared_depends"],
            "edges_declared_supplies": analysis["edges_declared_supplies"],
            "mismatches": analysis["mismatches"],
            "spof_candidates": analysis["spof_candidates"],
            "articulation_points": analysis["articulation_points"],
            "orphans": analysis["orphans"],
            "dangling_references": analysis["dangling_references"],
        },
        "log_patterns": analysis["log_patterns"],
    }

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(json.dumps(map_out, indent=2))

    log.info(
        "Wrote %s — pods=%d edges_dep=%d edges_sup=%d mismatches=%d spofs=%d orphans=%d dangling=%d log_patterns=%d",
        OUTPUT_PATH,
        len(pods_raw),
        len(analysis["edges_declared_depends"]),
        len(analysis["edges_declared_supplies"]),
        len(analysis["mismatches"]),
        len(analysis["spof_candidates"]),
        len(analysis["orphans"]),
        len(analysis["dangling_references"]),
        len(analysis["log_patterns"]),
    )
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Crawl + analyze the Selene colony.")
    parser.add_argument(
        "--llm-tag",
        action="store_true",
        help="Run optional Claude extraction pass per pod over logs+comms.",
    )
    args = parser.parse_args()
    return asyncio.run(amain(args))


if __name__ == "__main__":
    sys.exit(main())
