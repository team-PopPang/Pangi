"""Bootstrap Admin URL issuance boundary for the later Web/Auth package."""

from typing import Protocol


class BootstrapAdminPort(Protocol):
    def issue_url(self) -> str:
        """Issue a one-time URL without persisting the raw token in config."""

        ...

