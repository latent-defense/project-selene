"""I/O helpers for map artifact."""

import json
from pathlib import Path
from typing import Any

OUTPUT_PATH = Path("/rover/output/map.json")
SYSTEM_PROMPT_PATH = Path("/rover/agent/mapping_system_prompt.md")


def load_system_prompt() -> str:
    if not SYSTEM_PROMPT_PATH.exists():
        raise FileNotFoundError(f"System prompt file not found: {SYSTEM_PROMPT_PATH}")
    return SYSTEM_PROMPT_PATH.read_text(encoding="utf-8")


def write_map_artifact(artifact: dict[str, Any]) -> None:
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(json.dumps(artifact, indent=2), encoding="utf-8")


__all__ = ["OUTPUT_PATH", "load_system_prompt", "write_map_artifact"]
