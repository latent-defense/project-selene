"""Point-in-time recovery primitive for both mapping and reporting phases.

See `docs/deliberation.md` §10 for the full design discussion. Short form:

- Atomic per-unit storage: each pod (mapping) or section (reporting) is written
  as its own file via write-temp + `os.replace`, so a partial file never
  appears as a valid checkpoint.
- Append-only JSONL logs for query receipts (mapping only) and errors (both).
  Readers tolerate a torn final line, which is the typical failure mode after
  a mid-write crash.
- `cleanup()` removes all state on successful phase completion so checkpoints
  don't accumulate across runs.
- `RESUME=false` in the env forces a fresh start by calling `cleanup()` on
  construction.
"""
from __future__ import annotations

import json
import logging
import os
import shutil
import time
from pathlib import Path
from typing import Any, Iterator

CHECKPOINT_ROOT = Path("/rover/output/.checkpoint")
_RESUME_ENV = "RESUME"

logger = logging.getLogger(__name__)


def _now_ms() -> int:
    return int(time.time() * 1000)


class Checkpoint:
    """Base checkpoint: atomic per-unit storage + append-only error log.

    Subclasses set `PHASE` and `UNITS_DIRNAME` class attributes and add
    serialization helpers on top of the raw byte primitives here.
    """

    PHASE: str = ""
    UNITS_DIRNAME: str = ""

    def __init__(self, root: Path = CHECKPOINT_ROOT) -> None:
        if not self.PHASE or not self.UNITS_DIRNAME:
            raise RuntimeError(
                "Checkpoint subclass must set PHASE and UNITS_DIRNAME"
            )
        self._root = root / self.PHASE
        self._units_dir = self._root / self.UNITS_DIRNAME
        self._errors_path = self._root / "errors.jsonl"

        if os.environ.get(_RESUME_ENV, "").lower() == "false":
            self.cleanup()

        self._units_dir.mkdir(parents=True, exist_ok=True)

    # ---- unit primitives ---------------------------------------------------

    def _unit_path(self, name: str) -> Path:
        if "/" in name or ".." in name:
            raise ValueError(f"invalid unit name: {name!r}")
        return self._units_dir / name

    def _save_raw(self, name: str, content: bytes) -> None:
        final = self._unit_path(name)
        tmp = final.with_name(final.name + ".tmp")
        with open(tmp, "wb") as f:
            f.write(content)
            f.flush()
        os.replace(tmp, final)

    def _load_raw(self, name: str) -> bytes | None:
        path = self._unit_path(name)
        if not path.exists():
            return None
        return path.read_bytes()

    def unit_names(self) -> list[str]:
        """Filenames of successfully-saved units. Excludes in-flight `.tmp`."""
        if not self._units_dir.exists():
            return []
        return sorted(
            p.name
            for p in self._units_dir.iterdir()
            if p.is_file() and not p.name.endswith(".tmp")
        )

    # ---- error log ---------------------------------------------------------

    def append_error(self, err: dict[str, Any]) -> None:
        record = {
            "timestamp": _now_ms(),
            "phase": self.PHASE,
            **err,
        }
        self._append_jsonl(self._errors_path, record)

    def load_errors(self) -> list[dict[str, Any]]:
        return list(self._read_jsonl(self._errors_path))

    # ---- lifecycle ---------------------------------------------------------

    def is_fresh(self) -> bool:
        """True if no completed units are persisted (resume is a no-op)."""
        return not self.unit_names()

    def cleanup(self) -> None:
        """Remove all checkpoint state. Call on phase success or fresh-start."""
        if self._root.exists():
            shutil.rmtree(self._root)

    # ---- jsonl helpers -----------------------------------------------------

    @staticmethod
    def _append_jsonl(path: Path, record: dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, sort_keys=True) + "\n")
            f.flush()

    @staticmethod
    def _read_jsonl(path: Path) -> Iterator[dict[str, Any]]:
        if not path.exists():
            return
        with open(path, encoding="utf-8") as f:
            lines = f.readlines()
        last_idx = len(lines) - 1
        for idx, line in enumerate(lines):
            stripped = line.strip()
            if not stripped:
                continue
            try:
                yield json.loads(stripped)
            except json.JSONDecodeError as exc:
                if idx == last_idx:
                    logger.warning(
                        "ignoring torn last JSONL line in %s: %s", path, exc
                    )
                else:
                    logger.warning(
                        "skipping corrupt JSONL line %d in %s: %s",
                        idx,
                        path,
                        exc,
                    )


class MappingCheckpoint(Checkpoint):
    """Per-pod JSON storage + append-only query/error logs.

    Query IDs are monotonic across resume: `next_query_id()` derives its
    starting value from the highest ID already persisted in `queries.jsonl`.
    """

    PHASE = "mapping"
    UNITS_DIRNAME = "pods"

    def __init__(self, root: Path = CHECKPOINT_ROOT) -> None:
        super().__init__(root)
        self._queries_path = self._root / "queries.jsonl"
        self._next_query_id: int | None = None

    def save_pod(self, pod_id: str, data: dict[str, Any]) -> None:
        content = json.dumps(data, sort_keys=True, indent=2).encode("utf-8")
        self._save_raw(f"{pod_id}.json", content)

    def load_pod(self, pod_id: str) -> dict[str, Any] | None:
        content = self._load_raw(f"{pod_id}.json")
        if content is None:
            return None
        return json.loads(content)

    def load_all_pods(self) -> dict[str, dict[str, Any]]:
        result: dict[str, dict[str, Any]] = {}
        for name in self.unit_names():
            if not name.endswith(".json"):
                continue
            pod_id = name[: -len(".json")]
            data = self.load_pod(pod_id)
            if data is not None:
                result[pod_id] = data
        return result

    def next_query_id(self) -> int:
        if self._next_query_id is None:
            existing = self.load_queries()
            self._next_query_id = max(
                (q.get("query_id", -1) for q in existing), default=-1
            ) + 1
        nid = self._next_query_id
        self._next_query_id += 1
        return nid

    def append_query(self, receipt: dict[str, Any]) -> None:
        self._append_jsonl(self._queries_path, receipt)

    def load_queries(self) -> list[dict[str, Any]]:
        return list(self._read_jsonl(self._queries_path))


class ReportingCheckpoint(Checkpoint):
    """Per-section markdown storage + append-only error log."""

    PHASE = "reporting"
    UNITS_DIRNAME = "sections"

    def save_section(self, name: str, markdown: str) -> None:
        self._save_raw(f"{name}.md", markdown.encode("utf-8"))

    def load_section(self, name: str) -> str | None:
        content = self._load_raw(f"{name}.md")
        if content is None:
            return None
        return content.decode("utf-8")

    def load_all_sections(self) -> dict[str, str]:
        result: dict[str, str] = {}
        for filename in self.unit_names():
            if not filename.endswith(".md"):
                continue
            section = filename[: -len(".md")]
            md = self.load_section(section)
            if md is not None:
                result[section] = md
        return result
