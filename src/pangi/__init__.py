"""Stable public API for Pangi."""

from pangi._version import __version__
from pangi.application.contracts.orchestration import AgentResult, Evidence
from pangi.config import PangiConfig
from pangi.domain.runs import AttachmentRef, Principal, RunEvent, RunRequest
from pangi.runtime import PangiRuntime

__all__ = (
    "AgentResult",
    "AttachmentRef",
    "Evidence",
    "PangiConfig",
    "PangiRuntime",
    "Principal",
    "RunEvent",
    "RunRequest",
    "__version__",
)
