from __future__ import annotations

import re
import time
from dataclasses import replace
from typing import Protocol

from pangi.config import Settings, SettingsError, normalize_notion_id
from pangi.infra.notion.mcp_client import NotionMcpAuthError, NotionMcpError, NotionMcpHttpClient, NotionMcpTool
from pangi.infra.notion.oauth import NotionOAuthClient
from pangi.infra.notion.token_store import JsonNotionTokenStore, NotionOAuthConnection
from pangi.usecase.notion_context import (
    NotionContext,
    NotionContextAccessDeniedError,
    NotionContextDisabledError,
    NotionContextSource,
)


NOTION_ID_IN_TEXT_PATTERN = re.compile(r"(?i)[0-9a-f]{8}-?[0-9a-f]{4}-?[0-9a-f]{4}-?[0-9a-f]{4}-?[0-9a-f]{12}")
DEFAULT_DATABASE_ROW_LIMIT = 10


class NotionMcpClientFactory(Protocol):
    def __call__(self, *, access_token: str) -> NotionMcpHttpClient:
        ...


class NotionMcpContextProvider:
    def __init__(
        self,
        *,
        settings: Settings,
        token_store: JsonNotionTokenStore,
        oauth_client: NotionOAuthClient,
        mcp_client_factory: NotionMcpClientFactory | None = None,
    ) -> None:
        self._settings = settings
        self._token_store = token_store
        self._oauth_client = oauth_client
        self._mcp_client_factory = mcp_client_factory or self._default_mcp_client

    async def fetch_context(self, *, text: str, user_id: str, channel_id: str, thread_ts: str) -> NotionContext:
        if not self._settings.notion_enabled:
            raise NotionContextDisabledError("Notion context is disabled")
        connection = self._token_store.load()
        if connection is None or connection.tokens is None:
            raise NotionContextDisabledError("Notion OAuth connection is missing")
        if connection.tokens.expires_at is not None and connection.tokens.expires_at <= int(time.time()) + 60:
            connection = await self._refresh_connection(connection)

        client = self._mcp_client_factory(access_token=connection.tokens.access_token)
        try:
            return await self._fetch_with_client(client=client, text=text)
        except NotionMcpAuthError:
            refreshed = await self._refresh_connection(connection)
            retry_client = self._mcp_client_factory(access_token=refreshed.tokens.access_token)
            return await self._fetch_with_client(client=retry_client, text=text)

    async def _fetch_with_client(self, *, client: NotionMcpHttpClient, text: str) -> NotionContext:
        selected = _select_allowed_targets(text, self._settings)
        if not selected.page_ids and not selected.database_ids:
            raise NotionContextAccessDeniedError("No allowed Notion target matched the request")

        tools = await client.list_tools()
        sections: list[str] = []
        sources: list[NotionContextSource] = []

        for page_id in selected.page_ids:
            content = await _fetch_page_text(client=client, tools=tools, page_id=page_id)
            if content:
                sections.append(f"## Page {page_id}\n\n{content}")
                sources.append(NotionContextSource(notion_id=page_id, title=f"Notion page {page_id}"))

        for database_id in selected.database_ids:
            content = await _fetch_database_text(client=client, tools=tools, database_id=database_id)
            if content:
                sections.append(f"## Database {database_id}\n\n{content}")
                sources.append(NotionContextSource(notion_id=database_id, title=f"Notion database {database_id}"))

        if not sections:
            raise NotionMcpError("Notion MCP returned empty context")
        return NotionContext(
            markdown=_truncate_context("\n\n---\n\n".join(sections), self._settings.notion_context_max_chars),
            sources=tuple(sources),
        )

    async def _refresh_connection(self, connection: NotionOAuthConnection) -> NotionOAuthConnection:
        tokens = await self._oauth_client.refresh_tokens(connection)
        refreshed = replace(connection, tokens=tokens)
        if refreshed.tokens is None:
            raise NotionContextDisabledError("Notion OAuth refresh failed")
        return refreshed

    def _default_mcp_client(self, *, access_token: str) -> NotionMcpHttpClient:
        return NotionMcpHttpClient(
            mcp_url=self._settings.notion_mcp_url,
            access_token=access_token,
            timeout_seconds=self._settings.notion_timeout_seconds,
        )


class _SelectedTargets:
    def __init__(self, *, page_ids: tuple[str, ...], database_ids: tuple[str, ...]) -> None:
        self.page_ids = page_ids
        self.database_ids = database_ids


def _select_allowed_targets(text: str, settings: Settings) -> _SelectedTargets:
    explicit_ids = _extract_notion_ids(text)
    if explicit_ids:
        page_ids = tuple(notion_id for notion_id in explicit_ids if notion_id in settings.notion_allowed_page_ids)
        database_ids = tuple(notion_id for notion_id in explicit_ids if notion_id in settings.notion_allowed_database_ids)
        if len(page_ids) + len(database_ids) != len(explicit_ids):
            raise NotionContextAccessDeniedError("Explicit Notion id is outside allowlist")
        return _SelectedTargets(page_ids=page_ids, database_ids=database_ids)

    return _SelectedTargets(
        page_ids=tuple(sorted(settings.notion_allowed_page_ids)),
        database_ids=tuple(sorted(settings.notion_allowed_database_ids)),
    )


def _extract_notion_ids(text: str) -> tuple[str, ...]:
    notion_ids: list[str] = []
    for raw_id in NOTION_ID_IN_TEXT_PATTERN.findall(text):
        try:
            notion_ids.append(normalize_notion_id(raw_id))
        except SettingsError:
            continue
    return tuple(dict.fromkeys(notion_ids))


async def _fetch_page_text(*, client: NotionMcpHttpClient, tools: tuple[NotionMcpTool, ...], page_id: str) -> str:
    content = await _try_tool_calls(
        client=client,
        tools=_candidate_tools(tools, include_any=("fetch", "retrieve", "page")),
        argument_sets=(
            {"id": page_id},
            {"page_id": page_id},
            {"url": f"https://www.notion.so/{page_id}"},
        ),
    )
    return content


async def _fetch_database_text(
    *,
    client: NotionMcpHttpClient,
    tools: tuple[NotionMcpTool, ...],
    database_id: str,
) -> str:
    query_content = await _try_tool_calls(
        client=client,
        tools=_candidate_tools(tools, include_any=("query", "database")),
        argument_sets=(
            {"database_id": database_id, "page_size": DEFAULT_DATABASE_ROW_LIMIT},
            {"database_id": database_id},
            {"id": database_id},
        ),
    )
    if query_content:
        return query_content
    return await _try_tool_calls(
        client=client,
        tools=_candidate_tools(tools, include_any=("fetch", "retrieve", "database")),
        argument_sets=(
            {"id": database_id},
            {"database_id": database_id},
            {"url": f"https://www.notion.so/{database_id}"},
        ),
    )


async def _try_tool_calls(
    *,
    client: NotionMcpHttpClient,
    tools: tuple[NotionMcpTool, ...],
    argument_sets: tuple[dict[str, object], ...],
) -> str:
    last_error: Exception | None = None
    for tool in tools:
        for arguments in argument_sets:
            try:
                content = await client.call_tool(tool.name, arguments)
            except NotionMcpAuthError:
                raise
            except NotionMcpError as error:
                last_error = error
                continue
            if content.strip():
                return content.strip()
    if last_error is not None:
        return ""
    return ""


def _candidate_tools(
    tools: tuple[NotionMcpTool, ...],
    *,
    include_any: tuple[str, ...],
) -> tuple[NotionMcpTool, ...]:
    ranked: list[tuple[int, NotionMcpTool]] = []
    for tool in tools:
        haystack = f"{tool.name} {tool.description}".lower()
        score = sum(1 for keyword in include_any if keyword in haystack)
        if score:
            ranked.append((score, tool))
    return tuple(tool for _score, tool in sorted(ranked, key=lambda item: (-item[0], item[1].name)))


def _truncate_context(markdown: str, max_chars: int) -> str:
    if len(markdown) <= max_chars:
        return markdown
    suffix = "\n\n... Notion context가 길어 일부를 생략했습니다."
    return markdown[: max(0, max_chars - len(suffix))].rstrip() + suffix
