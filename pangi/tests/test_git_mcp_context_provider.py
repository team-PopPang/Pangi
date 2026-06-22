import asyncio
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from pangi.config import Settings  # noqa: E402
from pangi.infra.git_mcp.context_provider import GitMcpContextProvider  # noqa: E402
from pangi.infra.git_mcp.mcp_client import GitMcpAuthError, GitMcpTool  # noqa: E402
from pangi.usecase.git_context import GitContextAccessDeniedError, GitContextDisabledError  # noqa: E402


def valid_env(tmp_path: Path, **overrides: str) -> dict[str, str]:
    source_root = tmp_path / "sources"
    worktree_root = tmp_path / "worktrees"
    (source_root / "PopPang-iOS").mkdir(parents=True)
    values = {
        "SLACK_SIGNING_SECRET": "placeholder-signing-secret",
        "SLACK_BOT_TOKEN": "placeholder-bot-token",
        "SLACK_ALLOWED_USER_IDS": "U123",
        "SLACK_ALLOWED_CHANNEL_IDS": "C123",
        "PANGI_WORKTREE_ROOT": str(worktree_root),
        "PANGI_SOURCE_REPO_ROOT": str(source_root),
        "PANGI_GIT_MCP_ENABLED": "1",
        "PANGI_GIT_MCP_ORG": "team-PopPang",
    }
    values.update(overrides)
    return values


class FakeGitMcpClient:
    def __init__(self, *, auth_error: bool = False):
        self.auth_error = auth_error
        self.calls = []

    async def list_tools(self):
        if self.auth_error:
            raise GitMcpAuthError("auth")
        return (
            GitMcpTool(name="search_pull_requests", description="Search pull requests"),
            GitMcpTool(name="list_organization_repositories", description="List organization repositories"),
        )

    async def call_tool(self, name, arguments):
        self.calls.append({"name": name, "arguments": arguments})
        if name == "list_organization_repositories":
            return '[{"name": "PopPang-iOS"}, {"full_name": "team-PopPang/PopPang-BE"}]'
        if name == "search_pull_requests":
            return "PR 123 fixes admin filter state."
        return ""


class FakeGitMcpClientWithWriteTools:
    def __init__(self):
        self.calls = []

    async def list_tools(self):
        return (
            GitMcpTool(name="create_issue", description="Create an issue"),
            GitMcpTool(name="get_issue", description="Get an issue"),
        )

    async def call_tool(self, name, arguments):
        self.calls.append({"name": name, "arguments": arguments})
        if name == "create_issue":
            return "created issue"
        if name == "get_issue":
            return "Issue 42 is about the admin filter failure."
        return ""


def test_git_mcp_context_provider_fetches_git_context(tmp_path):
    async def scenario():
        settings = Settings.from_env(valid_env(tmp_path))
        client = FakeGitMcpClient()
        provider = GitMcpContextProvider(settings=settings, mcp_client_factory=lambda: client)

        context = await provider.fetch_context(
            text="PopPang-FE PR 123 요약해줘",
            user_id="U123",
            channel_id="C123",
            thread_ts="1710000000.000001",
        )

        assert "PR 123 fixes admin filter state" in context.markdown
        assert context.sources[0].source_type == "git-mcp"
        assert client.calls[0]["name"] == "search_pull_requests"

    asyncio.run(scenario())


def test_git_mcp_context_provider_skips_write_tools_by_default(tmp_path):
    async def scenario():
        settings = Settings.from_env(valid_env(tmp_path))
        client = FakeGitMcpClientWithWriteTools()
        provider = GitMcpContextProvider(settings=settings, mcp_client_factory=lambda: client)

        context = await provider.fetch_context(
            text="이슈 42 정리해줘",
            user_id="U123",
            channel_id="C123",
            thread_ts="1710000000.000001",
        )

        assert "Issue 42 is about the admin filter failure" in context.markdown
        assert [call["name"] for call in client.calls] == ["get_issue"]

    asyncio.run(scenario())


def test_git_mcp_context_provider_merges_repo_catalog(tmp_path):
    async def scenario():
        settings = Settings.from_env(valid_env(tmp_path))
        client = FakeGitMcpClient()
        provider = GitMcpContextProvider(settings=settings, mcp_client_factory=lambda: client)

        catalog = await provider.fetch_repo_catalog(local_repo_keys=("PopPang-iOS", "PopPang-FE"))

        assert [(item.name, item.status) for item in catalog.items] == [
            ("PopPang-BE", "not_cloned"),
            ("PopPang-FE", "local_only"),
            ("PopPang-iOS", "ready"),
        ]
        assert catalog.org == "team-PopPang"

    asyncio.run(scenario())


def test_git_mcp_context_provider_blocks_when_disabled(tmp_path):
    async def scenario():
        settings = Settings.from_env(valid_env(tmp_path, PANGI_GIT_MCP_ENABLED="0"))
        provider = GitMcpContextProvider(settings=settings, mcp_client_factory=lambda: FakeGitMcpClient())

        with pytest.raises(GitContextDisabledError):
            await provider.fetch_context(text="PR 123", user_id="U123", channel_id="C123", thread_ts="1")

    asyncio.run(scenario())


def test_git_mcp_context_provider_maps_auth_error_to_access_denied(tmp_path):
    async def scenario():
        settings = Settings.from_env(valid_env(tmp_path))
        provider = GitMcpContextProvider(
            settings=settings,
            mcp_client_factory=lambda: FakeGitMcpClient(auth_error=True),
        )

        with pytest.raises(GitContextAccessDeniedError):
            await provider.fetch_context(text="PR 123", user_id="U123", channel_id="C123", thread_ts="1")

    asyncio.run(scenario())
