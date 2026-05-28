from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class ReportingState(str, Enum):
    LOAD_AND_NORMALIZE = "LOAD_AND_NORMALIZE"
    ANALYZE = "ANALYZE"
    SYNTHESIZE_NARRATIVE = "SYNTHESIZE_NARRATIVE"
    VALIDATE = "VALIDATE"
    WRITE = "WRITE"
    COMPLETE = "COMPLETE"
    FAILED = "FAILED"


@dataclass
class ReportConfig:
    model: str
    max_tokens: int


@dataclass
class ReportingContext:
    state: ReportingState = ReportingState.LOAD_AND_NORMALIZE
    transitions: list[str] = field(default_factory=list)
    config: ReportConfig | None = None
    map_artifact: dict[str, Any] = field(default_factory=dict)
    placeholders: dict[str, Any] = field(default_factory=dict)
    observations: list[dict[str, Any]] = field(default_factory=list)
    crawl_summary: dict[str, Any] = field(default_factory=dict)
    dependency_graph: list[dict[str, Any]] = field(default_factory=list)
    supply_graph: list[dict[str, Any]] = field(default_factory=list)
    discovered_pods: set[str] = field(default_factory=set)
    endpoint_coverage: dict[str, set[str]] = field(default_factory=dict)
    consistency_checks: dict[str, Any] = field(default_factory=dict)
    metrics: dict[str, Any] = field(default_factory=dict)
    timeline: list[dict[str, Any]] = field(default_factory=list)
    gateway_entrypoint: str | None = None
    mermaid_graph: str = ""
    text_graph: str = ""
    deterministic_markdown: str = ""
    deterministic_payload: dict[str, Any] = field(default_factory=dict)
    llm_text: str = ""
    llm_model: str = "none"
    report_content: str = ""
    report_length: int = 0
    error: str | None = None
