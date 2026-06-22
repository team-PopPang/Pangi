"""Notion infrastructure adapter registry."""

from __future__ import annotations

from pangi.config import get_settings
from pangi.infra.notion.context_provider import NotionMcpContextProvider
from pangi.infra.notion.oauth import NotionOAuthClient
from pangi.infra.notion.token_store import JsonNotionTokenStore
from pangi.usecase.ports import NotionContextProvider


_notion_context_provider: NotionContextProvider | None = None


def get_notion_context_provider() -> NotionContextProvider | None:
    global _notion_context_provider
    if _notion_context_provider is not None:
        return _notion_context_provider
    settings = get_settings()
    if not settings.notion_enabled:
        return None
    if settings.notion_token_store_path is None:
        return None
    token_store = JsonNotionTokenStore(settings.notion_token_store_path)
    oauth_client = NotionOAuthClient(mcp_url=settings.notion_mcp_url, token_store=token_store)
    _notion_context_provider = NotionMcpContextProvider(
        settings=settings,
        token_store=token_store,
        oauth_client=oauth_client,
    )
    return _notion_context_provider


def set_notion_context_provider(provider: NotionContextProvider | None) -> None:
    global _notion_context_provider
    _notion_context_provider = provider


__all__ = [
    "JsonNotionTokenStore",
    "NotionMcpContextProvider",
    "NotionOAuthClient",
    "get_notion_context_provider",
    "set_notion_context_provider",
]
