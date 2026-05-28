from typing import Callable

from .models import MappingContext, MappingState
from .workflow import (
    handle_collect,
    handle_discover,
    handle_finalize_artifact,
    handle_init,
)


class MappingStateMachine:
    """Explicit executable state machine for mapping flow."""

    def __init__(
        self,
        write_artifact_fn: Callable[[dict[str, object]], None],
        context: MappingContext | None = None,
        handlers: dict[MappingState, Callable[[MappingContext], MappingState]]
        | None = None,
    ) -> None:
        self._write_artifact = write_artifact_fn
        self.context = context or MappingContext()
        self._handlers = handlers or {
            MappingState.INIT: handle_init,
            MappingState.DISCOVER: handle_discover,
            MappingState.COLLECT: handle_collect,
            MappingState.FINALIZE_ARTIFACT: handle_finalize_artifact,
        }

    def _transition(self, next_state: MappingState) -> None:
        self.context.state = next_state
        self.context.transitions.append(next_state.value)

    def run(self) -> dict[str, object]:
        try:
            self.context.transitions.append(self.context.state.value)
            while self.context.state not in {MappingState.COMPLETE, MappingState.FAILED}:
                handler = self._handlers[self.context.state]
                next_state = handler(self.context)
                self._transition(next_state)

            if self.context.state == MappingState.COMPLETE:
                if self.context.artifact is None:
                    raise RuntimeError("Mapping completed without artifact.")
                self._write_artifact(self.context.artifact)
                return self.context.artifact
            raise RuntimeError(self.context.error or "Mapping state machine failed.")
        except Exception as exc:
            self.context.error = f"{type(exc).__name__}: {exc}"
            if self.context.state != MappingState.FAILED:
                self._transition(MappingState.FAILED)
            raise
