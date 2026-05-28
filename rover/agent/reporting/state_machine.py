from typing import Callable

from .models import ReportingContext, ReportingState
from .workflow import (
    handle_analyze,
    handle_load_and_normalize,
    handle_synthesize_narrative,
    handle_validate,
    handle_write,
)


class ReportingStateMachine:
    """Explicit executable state machine for report generation."""

    def __init__(
        self,
        context: ReportingContext | None = None,
        handlers: dict[ReportingState, Callable[[ReportingContext], ReportingState]]
        | None = None,
    ) -> None:
        self.context = context or ReportingContext()
        self._handlers = handlers or {
            ReportingState.LOAD_AND_NORMALIZE: handle_load_and_normalize,
            ReportingState.ANALYZE: handle_analyze,
            ReportingState.SYNTHESIZE_NARRATIVE: handle_synthesize_narrative,
            ReportingState.VALIDATE: handle_validate,
            ReportingState.WRITE: handle_write,
        }

    def _transition(self, next_state: ReportingState) -> None:
        self.context.state = next_state
        self.context.transitions.append(next_state.value)

    def run(self) -> str:
        try:
            self.context.transitions.append(self.context.state.value)
            while self.context.state not in {
                ReportingState.COMPLETE,
                ReportingState.FAILED,
            }:
                handler = self._handlers[self.context.state]
                next_state = handler(self.context)
                self._transition(next_state)

            if self.context.state == ReportingState.COMPLETE:
                return self.context.report_content
            raise RuntimeError(self.context.error or "Reporting state machine failed.")
        except Exception as exc:
            self.context.error = f"{type(exc).__name__}: {exc}"
            if self.context.state != ReportingState.FAILED:
                self._transition(ReportingState.FAILED)
            raise
