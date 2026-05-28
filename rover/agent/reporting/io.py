"""I/O helpers for report artifact."""

import json
from pathlib import Path
from typing import Any

MAP_PATH = Path("/rover/output/map.json")
REPORT_PATH = Path("/rover/output/report.md")


def load_map_artifact() -> dict[str, Any]:
    """Load and minimally validate map artifact input."""
    if not MAP_PATH.exists():
        raise FileNotFoundError(f"map.json not found at {MAP_PATH}")
    data = json.loads(MAP_PATH.read_text(encoding="utf-8"))
    required_keys = ("crawl_summary", "observations", "placeholders")
    missing = [key for key in required_keys if key not in data]
    if missing:
        raise ValueError(f"map.json missing required keys: {missing}")
    return data


def write_report(content: str) -> None:
    """Write report markdown to output path."""
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(content, encoding="utf-8")


__all__ = ["MAP_PATH", "REPORT_PATH", "load_map_artifact", "write_report"]
