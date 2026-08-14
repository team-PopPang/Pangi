"""Runtime status exposed to CLI adapters."""

from dataclasses import dataclass
from enum import StrEnum


class RuntimeState(StrEnum):
    STOPPED = "stopped"
    RUNNING = "running"
    UNAVAILABLE = "unavailable"


@dataclass(frozen=True, slots=True)
class RuntimeStatus:
    state: RuntimeState
    detail: str

    def as_dict(self) -> dict[str, str]:
        return {"state": self.state.value, "detail": self.detail}

