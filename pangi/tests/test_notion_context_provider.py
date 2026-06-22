import asyncio
from pathlib import Path

import pytest

from pangi.config import Settings
from pangi.infra.notion.context_provider import NotionMcpContextProvider
from pangi.infra.notion.mcp_client import NotionMcpTool
from pangi.infra.notion.token_store import (
    JsonNotionTokenStore,
    NotionOAuthClientRegistration,
    NotionOAuthConnection,
    NotionOAuthMetadata,
    NotionOAuthTokenSet,
)
from pangi.usecase.notion_context import NotionContextAccessDeniedError, NotionContextDisabledError


PAGE_ID = "265db9e736cf80018f00e19a0fb1185d"
DATABASE_ID = "37bdb9e736cf80028251c8d070cd4110"


class FakeMcpClient:
    def __init__(self):
        self.calls = []

    async def list_tools(self):
        return (
            NotionMcpTool(name="notion-fetch", description="Fetch a Notion page or database by id"),
            NotionMcpTool(name="notion-query-database", description="Query a Notion database"),
        )

    async def call_tool(self, name, arguments):
        self.calls.append((name, arguments))
        if name == "notion-query-database" and arguments.get("database_id") == DATABASE_ID:
            return "업무진행 rows\n- 관리자 페이지 UI 개선: 진행 중"
        if name == "notion-fetch" and arguments.get("id") == PAGE_ID:
            return "팝팡 페이지 본문\n- 그라운드 룰"
        return ""


class FakeOAuthClient:
    async def refresh_tokens(self, connection):
        return connection.tokens


def valid_env(**overrides: str) -> dict[str, str]:
    values = {
        "SLACK_SIGNING_SECRET": "placeholder-signing-secret",
        "SLACK_BOT_TOKEN": "placeholder-bot-token",
        "SLACK_ALLOWED_USER_IDS": "U123",
        "SLACK_ALLOWED_CHANNEL_IDS": "C123",
        "PANGI_WORKTREE_ROOT": "/tmp/pangi/worktrees",
        "PANGI_SOURCE_REPO_ROOT": "/tmp/pangi/sources",
        "PANGI_NOTION_ENABLED": "1",
        "PANGI_NOTION_ALLOWED_PAGE_IDS": PAGE_ID,
        "PANGI_NOTION_ALLOWED_DATABASE_IDS": DATABASE_ID,
    }
    values.update(overrides)
    source_root = Path(values["PANGI_SOURCE_REPO_ROOT"])
    source_root.mkdir(parents=True, exist_ok=True)
    (source_root / "PopPang-iOS").mkdir(parents=True, exist_ok=True)
    return values


def save_connected_token(store: JsonNotionTokenStore):
    store.save(
        NotionOAuthConnection(
            metadata=NotionOAuthMetadata(
                authorization_endpoint="https://auth.example.com/authorize",
                token_endpoint="https://auth.example.com/token",
            ),
            client=NotionOAuthClientRegistration(client_id="client-123"),
            tokens=NotionOAuthTokenSet(access_token="access-token"),
        )
    )


def test_provider_fetches_allowed_page_and_database_context(tmp_path):
    settings = Settings.from_env(valid_env(PANGI_NOTION_CONTEXT_MAX_CHARS="1000"))
    store = JsonNotionTokenStore(tmp_path / "oauth.json")
    save_connected_token(store)
    fake_client = FakeMcpClient()
    provider = NotionMcpContextProvider(
        settings=settings,
        token_store=store,
        oauth_client=FakeOAuthClient(),
        mcp_client_factory=lambda *, access_token: fake_client,
    )

    context = asyncio.run(provider.fetch_context(text="노션 내용 알려줘", user_id="U", channel_id="C", thread_ts="1"))

    assert "팝팡 페이지 본문" in context.markdown
    assert "업무진행 rows" in context.markdown
    assert len(context.sources) == 2
    assert ("notion-fetch", {"id": PAGE_ID}) in fake_client.calls
    assert ("notion-query-database", {"database_id": DATABASE_ID, "page_size": 10}) in fake_client.calls


def test_provider_denies_explicit_unallowed_notion_id(tmp_path):
    settings = Settings.from_env(valid_env())
    store = JsonNotionTokenStore(tmp_path / "oauth.json")
    save_connected_token(store)
    provider = NotionMcpContextProvider(
        settings=settings,
        token_store=store,
        oauth_client=FakeOAuthClient(),
        mcp_client_factory=lambda *, access_token: FakeMcpClient(),
    )

    with pytest.raises(NotionContextAccessDeniedError):
        asyncio.run(
            provider.fetch_context(
                text="https://app.notion.com/p/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
                user_id="U",
                channel_id="C",
                thread_ts="1",
            )
        )


def test_provider_requires_oauth_tokens(tmp_path):
    settings = Settings.from_env(valid_env())
    provider = NotionMcpContextProvider(
        settings=settings,
        token_store=JsonNotionTokenStore(tmp_path / "missing.json"),
        oauth_client=FakeOAuthClient(),
        mcp_client_factory=lambda *, access_token: FakeMcpClient(),
    )

    with pytest.raises(NotionContextDisabledError):
        asyncio.run(provider.fetch_context(text="노션 알려줘", user_id="U", channel_id="C", thread_ts="1"))


def test_provider_truncates_long_context(tmp_path):
    settings = Settings.from_env(valid_env(PANGI_NOTION_CONTEXT_MAX_CHARS="50", PANGI_NOTION_ALLOWED_DATABASE_IDS=""))
    store = JsonNotionTokenStore(tmp_path / "oauth.json")
    save_connected_token(store)
    provider = NotionMcpContextProvider(
        settings=settings,
        token_store=store,
        oauth_client=FakeOAuthClient(),
        mcp_client_factory=lambda *, access_token: FakeMcpClient(),
    )

    context = asyncio.run(provider.fetch_context(text="노션 알려줘", user_id="U", channel_id="C", thread_ts="1"))

    assert len(context.markdown) <= 50
    assert "생략했습니다" in context.markdown
