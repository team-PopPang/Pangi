from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import secrets
import time
from dataclasses import replace
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urljoin, urlparse
from urllib.request import Request, urlopen

from pangi.infra.notion.token_store import (
    JsonNotionTokenStore,
    NotionOAuthClientRegistration,
    NotionOAuthConnection,
    NotionOAuthMetadata,
    NotionOAuthPendingState,
    NotionOAuthTokenSet,
)


OAUTH_USER_AGENT = "Pangi-Notion-MCP-Client/0.1"
OAUTH_STATE_TTL_SECONDS = 60 * 10


class NotionOAuthError(RuntimeError):
    """Raised when Notion OAuth cannot complete safely."""


class NotionOAuthClient:
    def __init__(self, *, mcp_url: str, token_store: JsonNotionTokenStore) -> None:
        self._mcp_url = mcp_url.rstrip("/")
        self._token_store = token_store

    async def begin_authorization(self, *, redirect_uri: str) -> str:
        connection = self._token_store.load()
        metadata = connection.metadata if connection else await self.discover_metadata()
        client = connection.client if connection else await self.register_client(
            metadata=metadata,
            redirect_uri=redirect_uri,
        )
        state = secrets.token_urlsafe(32)
        code_verifier = secrets.token_urlsafe(64)
        pending = NotionOAuthPendingState(
            state=state,
            code_verifier=code_verifier,
            redirect_uri=redirect_uri,
            created_at=int(time.time()),
        )
        self._token_store.save(
            NotionOAuthConnection(
                metadata=metadata,
                client=client,
                tokens=connection.tokens if connection else None,
                pending=pending,
            )
        )
        return _authorization_url(
            metadata=metadata,
            client=client,
            pending=pending,
        )

    async def complete_authorization(self, *, code: str, state: str) -> None:
        connection = self._token_store.load()
        if connection is None or connection.pending is None:
            raise NotionOAuthError("No pending Notion OAuth state")
        pending = connection.pending
        if not secrets.compare_digest(pending.state, state):
            raise NotionOAuthError("Invalid Notion OAuth state")
        if time.time() - pending.created_at > OAUTH_STATE_TTL_SECONDS:
            raise NotionOAuthError("Notion OAuth state expired")

        tokens = await self.exchange_code_for_tokens(
            metadata=connection.metadata,
            client=connection.client,
            code=code,
            code_verifier=pending.code_verifier,
            redirect_uri=pending.redirect_uri,
        )
        self._token_store.save(replace(connection, tokens=tokens, pending=None))

    async def refresh_tokens(self, connection: NotionOAuthConnection) -> NotionOAuthTokenSet:
        if connection.tokens is None or not connection.tokens.refresh_token:
            raise NotionOAuthError("Notion refresh token is not available")
        body = {
            "grant_type": "refresh_token",
            "refresh_token": connection.tokens.refresh_token,
            "client_id": connection.client.client_id,
        }
        if connection.client.client_secret:
            body["client_secret"] = connection.client.client_secret
        data = await _post_form(connection.metadata.token_endpoint, body)
        tokens = _token_set_from_response(data)
        if tokens.refresh_token is None:
            tokens = replace(tokens, refresh_token=connection.tokens.refresh_token)
        self._token_store.save(replace(connection, tokens=tokens))
        return tokens

    async def discover_metadata(self) -> NotionOAuthMetadata:
        resource_metadata = await _get_json_from_first_successful_url(
            _protected_resource_metadata_urls(self._mcp_url)
        )
        authorization_servers = resource_metadata.get("authorization_servers") or []
        if not authorization_servers:
            raise NotionOAuthError("Notion OAuth authorization server was not advertised")
        authorization_server = str(authorization_servers[0]).rstrip("/")
        metadata = await _get_json(urljoin(authorization_server + "/", ".well-known/oauth-authorization-server"))
        try:
            return NotionOAuthMetadata(
                authorization_endpoint=metadata["authorization_endpoint"],
                token_endpoint=metadata["token_endpoint"],
                registration_endpoint=metadata.get("registration_endpoint"),
            )
        except KeyError as error:
            raise NotionOAuthError("Notion OAuth metadata is missing required endpoints") from error

    async def register_client(
        self,
        *,
        metadata: NotionOAuthMetadata,
        redirect_uri: str,
    ) -> NotionOAuthClientRegistration:
        if not metadata.registration_endpoint:
            raise NotionOAuthError("Notion OAuth dynamic registration endpoint is missing")
        payload = {
            "client_name": "Pangi",
            "redirect_uris": [redirect_uri],
            "grant_types": ["authorization_code", "refresh_token"],
            "response_types": ["code"],
            "token_endpoint_auth_method": "none",
        }
        data = await _post_json(metadata.registration_endpoint, payload)
        client_id = data.get("client_id")
        if not client_id:
            raise NotionOAuthError("Notion OAuth registration did not return client_id")
        return NotionOAuthClientRegistration(
            client_id=client_id,
            client_secret=data.get("client_secret"),
        )

    async def exchange_code_for_tokens(
        self,
        *,
        metadata: NotionOAuthMetadata,
        client: NotionOAuthClientRegistration,
        code: str,
        code_verifier: str,
        redirect_uri: str,
    ) -> NotionOAuthTokenSet:
        body = {
            "grant_type": "authorization_code",
            "code": code,
            "client_id": client.client_id,
            "redirect_uri": redirect_uri,
            "code_verifier": code_verifier,
        }
        if client.client_secret:
            body["client_secret"] = client.client_secret
        data = await _post_form(metadata.token_endpoint, body)
        return _token_set_from_response(data)


def _authorization_url(
    *,
    metadata: NotionOAuthMetadata,
    client: NotionOAuthClientRegistration,
    pending: NotionOAuthPendingState,
) -> str:
    params = {
        "response_type": "code",
        "client_id": client.client_id,
        "redirect_uri": pending.redirect_uri,
        "state": pending.state,
        "code_challenge": _code_challenge(pending.code_verifier),
        "code_challenge_method": "S256",
    }
    return f"{metadata.authorization_endpoint}?{urlencode(params)}"


def _code_challenge(code_verifier: str) -> str:
    digest = hashlib.sha256(code_verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")


def _token_set_from_response(data: dict[str, Any]) -> NotionOAuthTokenSet:
    access_token = data.get("access_token")
    if not access_token:
        raise NotionOAuthError("Notion OAuth token response did not include access_token")
    expires_at = None
    expires_in = data.get("expires_in")
    if isinstance(expires_in, int):
        expires_at = int(time.time()) + expires_in
    return NotionOAuthTokenSet(
        access_token=access_token,
        token_type=data.get("token_type") or "Bearer",
        refresh_token=data.get("refresh_token"),
        expires_at=expires_at,
        scope=data.get("scope"),
    )


def _protected_resource_metadata_urls(mcp_url: str) -> tuple[str, ...]:
    parsed = urlparse(mcp_url)
    base = f"{parsed.scheme}://{parsed.netloc}"
    path = parsed.path.strip("/")
    urls = [mcp_url.rstrip("/") + "/.well-known/oauth-protected-resource", base + "/.well-known/oauth-protected-resource"]
    if path:
        urls.append(base + "/.well-known/oauth-protected-resource/" + path)
    return tuple(dict.fromkeys(urls))


async def _get_json_from_first_successful_url(urls: tuple[str, ...]) -> dict[str, Any]:
    last_error: Exception | None = None
    for url in urls:
        try:
            return await _get_json(url)
        except NotionOAuthError as error:
            last_error = error
    raise NotionOAuthError("Failed to discover Notion OAuth metadata") from last_error


async def _get_json(url: str) -> dict[str, Any]:
    return await asyncio.to_thread(_request_json, "GET", url, None, None)


async def _post_json(url: str, payload: dict[str, Any]) -> dict[str, Any]:
    body = json.dumps(payload).encode("utf-8")
    headers = {"Content-Type": "application/json", "Accept": "application/json"}
    return await asyncio.to_thread(_request_json, "POST", url, body, headers)


async def _post_form(url: str, payload: dict[str, str]) -> dict[str, Any]:
    body = urlencode(payload).encode("utf-8")
    headers = {"Content-Type": "application/x-www-form-urlencoded", "Accept": "application/json"}
    return await asyncio.to_thread(_request_json, "POST", url, body, headers)


def _request_json(
    method: str,
    url: str,
    body: bytes | None,
    headers: dict[str, str] | None,
) -> dict[str, Any]:
    request_headers = {"User-Agent": OAUTH_USER_AGENT, **(headers or {})}
    request = Request(url, data=body, method=method, headers=request_headers)
    try:
        with urlopen(request, timeout=20) as response:
            raw = response.read()
    except (HTTPError, URLError, TimeoutError) as error:
        raise NotionOAuthError("Notion OAuth HTTP request failed") from error
    try:
        data = json.loads(raw.decode("utf-8"))
    except json.JSONDecodeError as error:
        raise NotionOAuthError("Notion OAuth endpoint returned non-JSON response") from error
    if not isinstance(data, dict):
        raise NotionOAuthError("Notion OAuth endpoint returned an invalid response")
    return data
