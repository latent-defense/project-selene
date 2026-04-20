"""CLI entry for the rover agent.

Invoked via the shell scripts as `python -m agent map` / `python -m agent report`.
"""
from __future__ import annotations

import argparse
import sys

from .main import run_audit, run_map, run_report


def main() -> int:
    parser = argparse.ArgumentParser(prog="agent")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("map", help="Crawl the colony and write /rover/output/map.json")
    sub.add_parser("report", help="Synthesize /rover/output/report.md from the map")
    sub.add_parser("audit", help="Validate citations in report.md against map.json")
    args = parser.parse_args()

    if args.cmd == "map":
        return run_map()
    if args.cmd == "report":
        return run_report()
    if args.cmd == "audit":
        return run_audit()
    return 1


if __name__ == "__main__":
    sys.exit(main())
