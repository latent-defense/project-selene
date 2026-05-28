from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class MappingState(str, Enum):
    INIT = "INIT"
    DISCOVER = "DISCOVER"
    COLLECT = "COLLECT"
    FINALIZE_ARTIFACT = "FINALIZE_ARTIFACT"
    COMPLETE = "COMPLETE"
    FAILED = "FAILED"


@dataclass
class MappingContext:
    state: MappingState = MappingState.INIT
    transitions: list[str] = field(default_factory=list)
    config: dict[str, Any] = field(default_factory=dict)
    system_prompt: str = ""
    client: Any | None = None
    model_candidates: list[str] = field(default_factory=list)
    selected_model: str = ""
    tools: list[dict[str, Any]] = field(default_factory=list)
    seed_user_content: str = ""
    turn_history: list[dict[str, Any]] = field(default_factory=list)
    visited_urls: set[str] = field(default_factory=set)
    observations: list[dict[str, Any]] = field(default_factory=list)
    assistant_trace: list[str] = field(default_factory=list)
    discovered_pods: set[str] = field(default_factory=set)
    fetched_endpoints_by_pod: dict[str, set[str]] = field(default_factory=dict)
    completion_reason: str = "max_iterations_reached"
    iterations_run: int = 0
    started_at: str = ""
    finished_at: str = ""
    run_started_monotonic: float = 0.0
    last_assistant_text: str = ""
    pending_tool_use_blocks: list[Any] = field(default_factory=list)
    artifact: dict[str, Any] | None = None
    error: str | None = None
