"""Unit tests for checkpoint atomicity, JSONL tolerance, and resume (M0.4)."""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from agent.checkpoint import (
    Checkpoint,
    MappingCheckpoint,
    ReportingCheckpoint,
)


@pytest.fixture
def tmp_root(tmp_path: Path) -> Path:
    """Per-test checkpoint root (pytest tmp_path is auto-cleaned)."""
    return tmp_path / "ckpt"


def test_pod_save_load_roundtrip(tmp_root: Path) -> None:
    ckpt = MappingCheckpoint(root=tmp_root)
    ckpt.save_pod("artemis", {"info": {"id": "artemis"}, "logs": [1, 2]})
    ckpt2 = MappingCheckpoint(root=tmp_root)  # simulate fresh process
    pods = ckpt2.load_all_pods()
    assert pods == {"artemis": {"info": {"id": "artemis"}, "logs": [1, 2]}}


def test_query_id_continues_across_resume(tmp_root: Path) -> None:
    ckpt = MappingCheckpoint(root=tmp_root)
    for i in range(5):
        ckpt.append_query({"query_id": ckpt.next_query_id(), "endpoint": f"/e{i}"})
    # New instance — should resume IDs from 5
    ckpt2 = MappingCheckpoint(root=tmp_root)
    assert ckpt2.next_query_id() == 5
    assert ckpt2.next_query_id() == 6


def test_torn_last_jsonl_line_tolerated(tmp_root: Path) -> None:
    ckpt = MappingCheckpoint(root=tmp_root)
    ckpt.append_query({"query_id": 0, "ok": True})
    ckpt.append_query({"query_id": 1, "ok": True})
    # Simulate a torn write by appending a half-line
    qpath = tmp_root / "mapping" / "queries.jsonl"
    with open(qpath, "a") as f:
        f.write('{"query_id": 2, "ok"')  # no newline, malformed
    ckpt2 = MappingCheckpoint(root=tmp_root)
    queries = ckpt2.load_queries()
    assert len(queries) == 2  # 2 valid, torn line skipped


def test_atomic_write_temp_files_ignored_in_unit_names(tmp_root: Path) -> None:
    ckpt = MappingCheckpoint(root=tmp_root)
    ckpt.save_pod("a", {"x": 1})
    # Simulate a stale .tmp left from a prior crash
    tmp = tmp_root / "mapping" / "pods" / "b.json.tmp"
    tmp.parent.mkdir(parents=True, exist_ok=True)
    tmp.write_text('{"x": 2}')
    # unit_names should ignore .tmp
    assert ckpt.unit_names() == ["a.json"]


def test_cleanup_removes_phase_dir(tmp_root: Path) -> None:
    ckpt = MappingCheckpoint(root=tmp_root)
    ckpt.save_pod("a", {"x": 1})
    ckpt.append_query({"query_id": 0})
    ckpt.cleanup()
    assert not (tmp_root / "mapping").exists()


def test_path_traversal_guard(tmp_root: Path) -> None:
    ckpt = MappingCheckpoint(root=tmp_root)
    with pytest.raises(ValueError, match="invalid unit name"):
        ckpt.save_pod("../etc/passwd", {"x": 1})


def test_resume_env_false_resets_on_construction(tmp_root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    ckpt = MappingCheckpoint(root=tmp_root)
    ckpt.save_pod("a", {"x": 1})
    monkeypatch.setenv("RESUME", "false")
    ckpt2 = MappingCheckpoint(root=tmp_root)
    assert ckpt2.is_fresh()


def test_reporting_section_save_load(tmp_root: Path) -> None:
    ckpt = ReportingCheckpoint(root=tmp_root)
    ckpt.save_section("critical_pods", "## Critical Pods\nHelios is critical.")
    ckpt2 = ReportingCheckpoint(root=tmp_root)
    sections = ckpt2.load_all_sections()
    assert sections == {"critical_pods": "## Critical Pods\nHelios is critical."}


def test_checkpoint_subclass_must_set_class_attrs() -> None:
    with pytest.raises(RuntimeError, match="must set"):
        Checkpoint()


def test_error_log_appends_phase_and_timestamp(tmp_root: Path) -> None:
    ckpt = MappingCheckpoint(root=tmp_root)
    ckpt.append_error({"stage": "x", "exception_type": "Boom", "message": "kaboom", "resumable": True})
    errs = ckpt.load_errors()
    assert len(errs) == 1
    assert errs[0]["phase"] == "mapping"
    assert errs[0]["stage"] == "x"
    assert isinstance(errs[0]["timestamp"], int)
