from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from pangi.evaluations.models import EvalTraceEvent


@dataclass
class TraceRecorder:
    _events: list[EvalTraceEvent] = field(default_factory=list)

    def emit(self, name: str, **attributes: Any) -> None:
        self._events.append(EvalTraceEvent(name=name, attributes=dict(attributes)))

    @property
    def events(self) -> tuple[EvalTraceEvent, ...]:
        return tuple(self._events)

    @property
    def names(self) -> tuple[str, ...]:
        return tuple(event.name for event in self._events)
