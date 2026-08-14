"""Bootstrap Admin request and result contracts."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum


class BootstrapIssueStatus(StrEnum):
    ISSUED = "issued"
    ALREADY_ISSUED = "already_issued"
    ADMIN_EXISTS = "admin_exists"


@dataclass(frozen=True, slots=True)
class BootstrapIssueResult:
    status: BootstrapIssueStatus
    bootstrap_url: str | None = None
    expires_at: datetime | None = None

    def as_dict(self) -> dict[str, object]:
        value: dict[str, object] = {"status": self.status.value}
        if self.bootstrap_url is not None:
            value["url"] = self.bootstrap_url
        if self.expires_at is not None:
            value["expires_at"] = self.expires_at.isoformat()
        return value


@dataclass(frozen=True, slots=True)
class BootstrapAdminResult:
    user_id: str
    local_id: str
    display_name: str
    role: str = "admin"
    status: str = "active"

    def as_dict(self) -> dict[str, str]:
        return {
            "user_id": self.user_id,
            "local_id": self.local_id,
            "display_name": self.display_name,
            "role": self.role,
            "status": self.status,
        }
