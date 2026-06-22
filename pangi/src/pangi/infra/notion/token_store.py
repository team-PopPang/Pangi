from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


class NotionTokenStoreError(RuntimeError):
    """Raised when Notion OAuth state cannot be loaded or saved."""


@dataclass(frozen=True)
class NotionOAuthMetadata:
    authorization_endpoint: str
    token_endpoint: str
    registration_endpoint: str | None = None


@dataclass(frozen=True)
class NotionOAuthClientRegistration:
    client_id: str
    client_secret: str | None = None


@dataclass(frozen=True)
class NotionOAuthTokenSet:
    access_token: str
    token_type: str = "Bearer"
    refresh_token: str | None = None
    expires_at: int | None = None
    scope: str | None = None


@dataclass(frozen=True)
class NotionOAuthPendingState:
    state: str
    code_verifier: str
    redirect_uri: str
    created_at: int


@dataclass(frozen=True)
class NotionOAuthConnection:
    metadata: NotionOAuthMetadata
    client: NotionOAuthClientRegistration
    tokens: NotionOAuthTokenSet | None
    pending: NotionOAuthPendingState | None = None


class JsonNotionTokenStore:
    def __init__(self, path: Path) -> None:
        self._path = path

    @property
    def path(self) -> Path:
        return self._path

    def load(self) -> NotionOAuthConnection | None:
        if not self._path.is_file():
            return None
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise NotionTokenStoreError("Failed to read Notion token store") from error
        return _connection_from_dict(data)

    def save(self, connection: NotionOAuthConnection) -> None:
        data = _connection_to_dict(connection)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = self._path.with_suffix(self._path.suffix + ".tmp")
        try:
            temp_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
            os.chmod(temp_path, 0o600)
            temp_path.replace(self._path)
        except OSError as error:
            raise NotionTokenStoreError("Failed to write Notion token store") from error

    def clear(self) -> None:
        try:
            self._path.unlink(missing_ok=True)
        except OSError as error:
            raise NotionTokenStoreError("Failed to remove Notion token store") from error


def _connection_to_dict(connection: NotionOAuthConnection) -> dict[str, Any]:
    return {
        "metadata": asdict(connection.metadata),
        "client": asdict(connection.client),
        "tokens": asdict(connection.tokens) if connection.tokens else None,
        "pending": asdict(connection.pending) if connection.pending else None,
    }


def _connection_from_dict(data: dict[str, Any]) -> NotionOAuthConnection:
    metadata = NotionOAuthMetadata(**data["metadata"])
    client = NotionOAuthClientRegistration(**data["client"])
    tokens_data = data.get("tokens")
    pending_data = data.get("pending")
    return NotionOAuthConnection(
        metadata=metadata,
        client=client,
        tokens=NotionOAuthTokenSet(**tokens_data) if tokens_data else None,
        pending=NotionOAuthPendingState(**pending_data) if pending_data else None,
    )
