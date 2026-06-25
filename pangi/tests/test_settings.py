from pathlib import Path

import pytest

from pangi.config import (
    AccessDeniedError,
    Settings,
    SettingsError,
    UnknownRepoError,
    clear_settings_cache,
    normalize_notion_id,
)


def valid_env(**overrides: str) -> dict[str, str]:
    values = {
        "SLACK_SIGNING_SECRET": "placeholder-signing-secret",
        "SLACK_BOT_TOKEN": "placeholder-bot-token",
        "SLACK_ALLOWED_USER_IDS": "U123,U456",
        "SLACK_ALLOWED_CHANNEL_IDS": "C123,C456",
        "PANGI_WORKTREE_ROOT": "/tmp/pangi/worktrees",
        "PANGI_SOURCE_REPO_ROOT": "/tmp/pangi/sources",
    }
    values.update(overrides)
    worktree_root = Path(values["PANGI_WORKTREE_ROOT"])
    source_root = Path(values["PANGI_SOURCE_REPO_ROOT"])
    worktree_root.mkdir(parents=True, exist_ok=True)
    source_root.mkdir(parents=True, exist_ok=True)
    (source_root / "PopPang-iOS").mkdir(parents=True, exist_ok=True)
    return values


def test_settings_parses_allowlists_repo_paths_and_default_timeout():
    settings = Settings.from_env(valid_env())

    assert settings.slack_allowed_user_ids == frozenset({"U123", "U456"})
    assert settings.slack_allowed_channel_ids == frozenset({"C123", "C456"})
    assert settings.repo_path_for_key("PopPang-iOS") == Path(
        "/tmp/pangi/sources/PopPang-iOS"
    ).resolve(strict=False)
    assert settings.available_repo_keys() == ("PopPang-iOS",)
    assert settings.base_branch_for_key("PopPang-iOS") == "develop"
    assert settings.base_branch_candidates_for_key("PopPang-iOS") == ("develop", "main")
    assert settings.worktree_path_for_job("job_123") == Path(
        "/tmp/pangi/worktrees/job_123"
    ).resolve(strict=False)
    assert settings.default_base_branch == "develop"
    assert settings.job_timeout_seconds == 600
    assert settings.chat_timeout_seconds == 120
    assert settings.orchestrator_timeout_seconds == 20
    assert settings.chat_workspace_root == Path("/tmp/pangi/worktrees/_chat").resolve(strict=False)
    assert settings.public_base_url is None
    assert settings.chat_model == "gpt-5.4-mini"
    assert settings.chat_reasoning_effort == "low"
    assert settings.orchestrator_model == "gpt-5.4-mini"
    assert settings.orchestrator_reasoning_effort == "low"
    assert settings.analysis_model == "gpt-5.5"
    assert settings.analysis_reasoning_effort == "high"
    assert settings.notion_enabled is False
    assert settings.notion_mcp_url == "https://mcp.notion.com/mcp"
    assert settings.notion_allowed_page_ids == frozenset()
    assert settings.notion_allowed_database_ids == frozenset()
    assert settings.notion_context_max_chars == 6000
    assert settings.notion_timeout_seconds == 20
    assert settings.notion_token_store_path == Path(
        "/tmp/pangi/worktrees/_notion/notion-oauth.json"
    ).resolve(strict=False)
    assert settings.notion_write_enabled is False
    assert settings.git_mcp_enabled is False
    assert settings.git_mcp_url == "https://api.githubcopilot.com/mcp/"
    assert settings.git_mcp_context_url == "https://api.githubcopilot.com/mcp/readonly"
    assert settings.git_mcp_orgs_url == "https://api.githubcopilot.com/mcp/x/orgs/readonly"
    assert settings.git_mcp_repos_url == "https://api.githubcopilot.com/mcp/x/repos/readonly"
    assert settings.git_mcp_issues_url == "https://api.githubcopilot.com/mcp/x/issues/readonly"
    assert settings.git_mcp_pull_requests_url == "https://api.githubcopilot.com/mcp/x/pull_requests/readonly"
    assert settings.git_mcp_actions_url == "https://api.githubcopilot.com/mcp/x/actions/readonly"
    assert settings.git_mcp_token is None
    assert settings.git_mcp_org is None
    assert settings.git_mcp_context_max_chars == 6000
    assert settings.git_mcp_timeout_seconds == 20
    assert settings.git_mcp_write_enabled is False
    assert settings.git_clone_url_template is None
    assert settings.enable_admin_pages is False
    assert settings.admin_password is None


def test_settings_uses_configured_timeout():
    settings = Settings.from_env(valid_env(PANGI_JOB_TIMEOUT_SECONDS="900"))

    assert settings.job_timeout_seconds == 900


def test_settings_uses_configured_chat_and_orchestrator_options():
    settings = Settings.from_env(
        valid_env(
            PANGI_CHAT_TIMEOUT_SECONDS="60",
            PANGI_CHAT_WORKSPACE_ROOT="/tmp/pangi/worktrees/chat",
            PANGI_CHAT_MODEL="gpt-5.4-mini-codex",
            PANGI_CHAT_REASONING_EFFORT="medium",
            PANGI_ORCHESTRATOR_MODEL="gpt-5.5-codex",
            PANGI_ORCHESTRATOR_REASONING_EFFORT="medium",
            PANGI_ORCHESTRATOR_TIMEOUT_SECONDS="30",
            PANGI_ANALYSIS_MODEL="gpt-5.5-codex",
            PANGI_ANALYSIS_REASONING_EFFORT="xhigh",
        )
    )

    assert settings.chat_timeout_seconds == 60
    assert settings.chat_workspace_root == Path("/tmp/pangi/worktrees/chat").resolve(strict=False)
    assert settings.chat_model == "gpt-5.4-mini-codex"
    assert settings.chat_reasoning_effort == "medium"
    assert settings.orchestrator_model == "gpt-5.5-codex"
    assert settings.orchestrator_reasoning_effort == "medium"
    assert settings.orchestrator_timeout_seconds == 30
    assert settings.analysis_model == "gpt-5.5-codex"
    assert settings.analysis_reasoning_effort == "xhigh"


def test_settings_rejects_chat_workspace_outside_worktree_root():
    with pytest.raises(SettingsError, match="chat workspace"):
        Settings.from_env(valid_env(PANGI_CHAT_WORKSPACE_ROOT="/tmp/outside/chat"))


def test_settings_rejects_invalid_orchestrator_timeout():
    with pytest.raises(SettingsError, match="PANGI_ORCHESTRATOR_TIMEOUT_SECONDS"):
        Settings.from_env(valid_env(PANGI_ORCHESTRATOR_TIMEOUT_SECONDS="0"))


def test_settings_rejects_unsafe_model_name():
    with pytest.raises(SettingsError, match="PANGI_CHAT_MODEL"):
        Settings.from_env(valid_env(PANGI_CHAT_MODEL="bad model"))


def test_settings_rejects_invalid_reasoning_effort():
    with pytest.raises(SettingsError, match="PANGI_CHAT_REASONING_EFFORT"):
        Settings.from_env(valid_env(PANGI_CHAT_REASONING_EFFORT="fast"))


def test_settings_uses_configured_notion_options():
    settings = Settings.from_env(
        valid_env(
            PANGI_NOTION_ENABLED="1",
            PANGI_NOTION_MCP_URL="https://mcp.notion.com/mcp",
            PANGI_NOTION_ALLOWED_PAGE_IDS=(
                "01234567-89ab-cdef-0123-456789abcdef,"
                "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
            ),
            PANGI_NOTION_ALLOWED_DATABASE_IDS="bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
            PANGI_NOTION_CONTEXT_MAX_CHARS="3000",
            PANGI_NOTION_TIMEOUT_SECONDS="10",
            PANGI_NOTION_TOKEN_STORE_PATH="/tmp/pangi/worktrees/notion/token.json",
            PANGI_NOTION_WRITE_ENABLED="0",
            PANGI_PUBLIC_BASE_URL="https://pangi.example.com",
        )
    )

    assert settings.public_base_url == "https://pangi.example.com"
    assert settings.notion_enabled is True
    assert settings.notion_allowed_page_ids == frozenset(
        {
            "0123456789abcdef0123456789abcdef",
            "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        }
    )
    assert settings.notion_allowed_database_ids == frozenset({"bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"})
    assert settings.is_notion_page_allowed("01234567-89ab-cdef-0123-456789abcdef") is True
    assert settings.is_notion_database_allowed("bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb") is True
    assert settings.notion_context_max_chars == 3000
    assert settings.notion_timeout_seconds == 10
    assert settings.notion_token_store_path == Path("/tmp/pangi/worktrees/notion/token.json").resolve(strict=False)
    assert settings.notion_write_enabled is False


def test_settings_rejects_invalid_notion_options():
    with pytest.raises(SettingsError, match="PANGI_NOTION_ALLOWED_PAGE_IDS"):
        Settings.from_env(valid_env(PANGI_NOTION_ALLOWED_PAGE_IDS="not-a-notion-id"))

    with pytest.raises(SettingsError, match="PANGI_NOTION_MCP_URL"):
        Settings.from_env(valid_env(PANGI_NOTION_MCP_URL="ftp://mcp.notion.com/mcp"))

    with pytest.raises(SettingsError, match="PANGI_PUBLIC_BASE_URL"):
        Settings.from_env(valid_env(PANGI_PUBLIC_BASE_URL="http://pangi.example.com"))

    with pytest.raises(SettingsError, match="Notion token store path"):
        Settings.from_env(valid_env(PANGI_NOTION_TOKEN_STORE_PATH="/tmp/outside/notion.json"))


def test_settings_uses_configured_git_mcp_options():
    settings = Settings.from_env(
        valid_env(
            PANGI_GIT_MCP_ENABLED="1",
            PANGI_GIT_MCP_URL="https://api.githubcopilot.com/mcp/",
            PANGI_GIT_MCP_CONTEXT_URL="https://api.githubcopilot.com/mcp/readonly",
            PANGI_GIT_MCP_ORGS_URL="https://api.githubcopilot.com/mcp/x/orgs/readonly",
            PANGI_GIT_MCP_REPOS_URL="https://api.githubcopilot.com/mcp/x/repos/readonly",
            PANGI_GIT_MCP_ISSUES_URL="https://api.githubcopilot.com/mcp/x/issues/readonly",
            PANGI_GIT_MCP_PULL_REQUESTS_URL="https://api.githubcopilot.com/mcp/x/pull_requests/readonly",
            PANGI_GIT_MCP_ACTIONS_URL="https://api.githubcopilot.com/mcp/x/actions/readonly",
            PANGI_GIT_MCP_TOKEN="placeholder-git-token",
            PANGI_GIT_MCP_ORG="team-PopPang",
            PANGI_GIT_MCP_CONTEXT_MAX_CHARS="3000",
            PANGI_GIT_MCP_TIMEOUT_SECONDS="10",
            PANGI_GIT_MCP_WRITE_ENABLED="0",
            PANGI_GIT_CLONE_URL_TEMPLATE="git@github.com:{org}/{repo}.git",
        )
    )

    assert settings.git_mcp_enabled is True
    assert settings.git_mcp_url == "https://api.githubcopilot.com/mcp/"
    assert settings.git_mcp_context_url == "https://api.githubcopilot.com/mcp/readonly"
    assert settings.git_mcp_orgs_url == "https://api.githubcopilot.com/mcp/x/orgs/readonly"
    assert settings.git_mcp_repos_url == "https://api.githubcopilot.com/mcp/x/repos/readonly"
    assert settings.git_mcp_issues_url == "https://api.githubcopilot.com/mcp/x/issues/readonly"
    assert settings.git_mcp_pull_requests_url == "https://api.githubcopilot.com/mcp/x/pull_requests/readonly"
    assert settings.git_mcp_actions_url == "https://api.githubcopilot.com/mcp/x/actions/readonly"
    assert settings.git_mcp_token == "placeholder-git-token"
    assert settings.git_mcp_org == "team-PopPang"
    assert settings.git_mcp_context_max_chars == 3000
    assert settings.git_mcp_timeout_seconds == 10
    assert settings.git_mcp_write_enabled is False
    assert settings.git_clone_url_template == "git@github.com:{org}/{repo}.git"
    assert settings.repo_path_for_key("PopPang-BE") == Path(
        "/tmp/pangi/sources/PopPang-BE"
    ).resolve(strict=False)
    assert settings.base_branch_for_key("PopPang-BE") == "develop"
    assert settings.base_branch_candidates_for_key("PopPang-BE") == ("develop", "main")
    assert settings.clone_url_for_key("PopPang-BE") == "git@github.com:team-PopPang/PopPang-BE.git"


def test_settings_rejects_invalid_git_mcp_options():
    with pytest.raises(SettingsError, match="PANGI_GIT_MCP_URL"):
        Settings.from_env(valid_env(PANGI_GIT_MCP_URL="ftp://api.githubcopilot.com/mcp/"))

    with pytest.raises(SettingsError, match="PANGI_GIT_MCP_ORGS_URL"):
        Settings.from_env(valid_env(PANGI_GIT_MCP_ORGS_URL="ftp://api.githubcopilot.com/mcp/x/orgs/readonly"))

    with pytest.raises(SettingsError, match="PANGI_GIT_MCP_ORG"):
        Settings.from_env(valid_env(PANGI_GIT_MCP_ORG="../team-PopPang"))

    with pytest.raises(SettingsError, match="PANGI_GIT_MCP_CONTEXT_MAX_CHARS"):
        Settings.from_env(valid_env(PANGI_GIT_MCP_CONTEXT_MAX_CHARS="0"))

    with pytest.raises(SettingsError, match="PANGI_GIT_CLONE_URL_TEMPLATE"):
        Settings.from_env(valid_env(PANGI_GIT_CLONE_URL_TEMPLATE="https://github.com/{org}/repo.git"))


def test_normalize_notion_id_accepts_dash_or_plain_uuid():
    assert normalize_notion_id("01234567-89ab-cdef-0123-456789abcdef") == (
        "0123456789abcdef0123456789abcdef"
    )
    assert normalize_notion_id("AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA") == (
        "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
    )


def test_settings_uses_configured_default_base_branch():
    settings = Settings.from_env(valid_env(PANGI_DEFAULT_BASE_BRANCH="release/1.0"))

    assert settings.default_base_branch == "release/1.0"
    assert settings.base_branch_for_key("PopPang-iOS") == "release/1.0"
    assert settings.base_branch_candidates_for_key("PopPang-iOS") == ("release/1.0", "main")


def test_settings_uses_only_main_when_default_base_branch_is_main():
    settings = Settings.from_env(valid_env(PANGI_DEFAULT_BASE_BRANCH="main"))

    assert settings.base_branch_candidates_for_key("PopPang-iOS") == ("main",)


def test_settings_rejects_unsafe_default_base_branch():
    with pytest.raises(SettingsError, match="PANGI_DEFAULT_BASE_BRANCH"):
        Settings.from_env(valid_env(PANGI_DEFAULT_BASE_BRANCH="../main"))


def test_settings_enables_admin_pages_with_password():
    settings = Settings.from_env(
        valid_env(PANGI_ENABLE_ADMIN_PAGES="1", PANGI_ADMIN_PASSWORD="admin-password")
    )

    assert settings.enable_admin_pages is True
    assert settings.admin_password == "admin-password"


def test_settings_requires_admin_password_when_admin_pages_are_enabled():
    with pytest.raises(SettingsError, match="PANGI_ADMIN_PASSWORD"):
        Settings.from_env(valid_env(PANGI_ENABLE_ADMIN_PAGES="1"))


def test_settings_rejects_missing_required_values():
    values = valid_env(SLACK_ALLOWED_USER_IDS="")

    with pytest.raises(SettingsError, match="SLACK_ALLOWED_USER_IDS"):
        Settings.from_env(values)


def test_settings_blocks_disallowed_slack_access():
    settings = Settings.from_env(valid_env())

    settings.validate_slack_access(user_id="U123", channel_id="C123")

    with pytest.raises(AccessDeniedError):
        settings.validate_slack_access(user_id="U999", channel_id="C123")

    with pytest.raises(AccessDeniedError):
        settings.validate_slack_access(user_id="U123", channel_id="C999")


def test_settings_allows_wildcard_slack_access():
    settings = Settings.from_env(
        valid_env(
            SLACK_ALLOWED_USER_IDS="*",
            SLACK_ALLOWED_CHANNEL_IDS="*",
        )
    )

    settings.validate_slack_access(user_id="U999", channel_id="C999")


def test_settings_discovers_direct_child_repos_from_source_root(tmp_path):
    source_root = tmp_path / "sources"
    (source_root / ".hidden").mkdir(parents=True)
    (source_root / "PopPang-iOS").mkdir(parents=True)
    (source_root / "PopPang-BE").mkdir(parents=True)
    (source_root / "README.txt").write_text("ignore me", encoding="utf-8")

    settings = Settings.from_env(
        {
            "SLACK_SIGNING_SECRET": "placeholder-signing-secret",
            "SLACK_BOT_TOKEN": "placeholder-bot-token",
            "SLACK_ALLOWED_USER_IDS": "U123,U456",
            "SLACK_ALLOWED_CHANNEL_IDS": "C123,C456",
            "PANGI_WORKTREE_ROOT": str(tmp_path / "worktrees"),
            "PANGI_SOURCE_REPO_ROOT": str(source_root),
        }
    )

    assert settings.available_repo_keys() == ("PopPang-BE", "PopPang-iOS")


def test_settings_rejects_unknown_repo_key():
    settings = Settings.from_env(valid_env())

    with pytest.raises(UnknownRepoError):
        settings.repo_path_for_key("Unknown")


def test_settings_rejects_unsafe_job_id():
    settings = Settings.from_env(valid_env())

    with pytest.raises(SettingsError):
        settings.worktree_path_for_job("../escape")


def test_settings_loads_env_file_from_pangi_folder(tmp_path, monkeypatch):
    for name in (
        "SLACK_SIGNING_SECRET",
        "SLACK_BOT_TOKEN",
        "SLACK_ALLOWED_USER_IDS",
        "SLACK_ALLOWED_CHANNEL_IDS",
        "PANGI_WORKTREE_ROOT",
        "PANGI_SOURCE_REPO_ROOT",
        "PANGI_DEFAULT_BASE_BRANCH",
        "PANGI_JOB_TIMEOUT_SECONDS",
        "PANGI_CHAT_TIMEOUT_SECONDS",
        "PANGI_CHAT_WORKSPACE_ROOT",
        "PANGI_CHAT_MODEL",
        "PANGI_CHAT_REASONING_EFFORT",
        "PANGI_ORCHESTRATOR_MODEL",
        "PANGI_ORCHESTRATOR_REASONING_EFFORT",
        "PANGI_ORCHESTRATOR_TIMEOUT_SECONDS",
        "PANGI_ANALYSIS_MODEL",
        "PANGI_ANALYSIS_REASONING_EFFORT",
        "PANGI_NOTION_ENABLED",
        "PANGI_NOTION_MCP_URL",
        "PANGI_NOTION_ALLOWED_PAGE_IDS",
        "PANGI_NOTION_ALLOWED_DATABASE_IDS",
        "PANGI_NOTION_CONTEXT_MAX_CHARS",
        "PANGI_NOTION_TIMEOUT_SECONDS",
        "PANGI_NOTION_TOKEN_STORE_PATH",
        "PANGI_NOTION_WRITE_ENABLED",
        "PANGI_GIT_MCP_ENABLED",
        "PANGI_GIT_MCP_URL",
        "PANGI_GIT_MCP_TOKEN",
        "PANGI_GIT_MCP_ORG",
        "PANGI_GIT_MCP_CONTEXT_MAX_CHARS",
        "PANGI_GIT_MCP_TIMEOUT_SECONDS",
        "PANGI_GIT_MCP_WRITE_ENABLED",
        "PANGI_GIT_CLONE_URL_TEMPLATE",
        "PANGI_PUBLIC_BASE_URL",
        "PANGI_ENABLE_ADMIN_PAGES",
        "PANGI_ADMIN_PASSWORD",
    ):
        monkeypatch.delenv(name, raising=False)

    pangi_dir = tmp_path / "pangi"
    pangi_dir.mkdir()
    source_root = tmp_path / "sources"
    worktree_root = tmp_path / "worktrees"
    (source_root / "PopPang-iOS").mkdir(parents=True)
    env_file = pangi_dir / ".env"
    env_file.write_text(
        "\n".join(
            [
                "SLACK_SIGNING_SECRET=placeholder-signing-secret  # Slack signature",
                'SLACK_BOT_TOKEN="placeholder#bot-token"  # Slack bot token',
                "SLACK_ALLOWED_USER_IDS=U123  # allowed user",
                "SLACK_ALLOWED_CHANNEL_IDS=C123  # allowed channel",
                f"PANGI_WORKTREE_ROOT={worktree_root}",
                f"PANGI_SOURCE_REPO_ROOT={source_root}",
                "PANGI_JOB_TIMEOUT_SECONDS=700  # seconds",
                "PANGI_ENABLE_ADMIN_PAGES=0  # disabled",
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    clear_settings_cache()

    settings = Settings.from_env()

    assert settings.slack_allowed_user_ids == frozenset({"U123"})
    assert settings.slack_bot_token == "placeholder#bot-token"
    assert settings.job_timeout_seconds == 700


def test_settings_uses_env_example_for_empty_local_values(tmp_path, monkeypatch):
    for name in (
        "SLACK_SIGNING_SECRET",
        "SLACK_BOT_TOKEN",
        "SLACK_ALLOWED_USER_IDS",
        "SLACK_ALLOWED_CHANNEL_IDS",
        "PANGI_WORKTREE_ROOT",
        "PANGI_SOURCE_REPO_ROOT",
        "PANGI_DEFAULT_BASE_BRANCH",
        "PANGI_JOB_TIMEOUT_SECONDS",
        "PANGI_CHAT_TIMEOUT_SECONDS",
        "PANGI_CHAT_WORKSPACE_ROOT",
        "PANGI_CHAT_MODEL",
        "PANGI_CHAT_REASONING_EFFORT",
        "PANGI_ORCHESTRATOR_MODEL",
        "PANGI_ORCHESTRATOR_REASONING_EFFORT",
        "PANGI_ORCHESTRATOR_TIMEOUT_SECONDS",
        "PANGI_ANALYSIS_MODEL",
        "PANGI_ANALYSIS_REASONING_EFFORT",
        "PANGI_NOTION_ENABLED",
        "PANGI_NOTION_MCP_URL",
        "PANGI_NOTION_ALLOWED_PAGE_IDS",
        "PANGI_NOTION_ALLOWED_DATABASE_IDS",
        "PANGI_NOTION_CONTEXT_MAX_CHARS",
        "PANGI_NOTION_TIMEOUT_SECONDS",
        "PANGI_NOTION_TOKEN_STORE_PATH",
        "PANGI_NOTION_WRITE_ENABLED",
        "PANGI_GIT_MCP_ENABLED",
        "PANGI_GIT_MCP_URL",
        "PANGI_GIT_MCP_TOKEN",
        "PANGI_GIT_MCP_ORG",
        "PANGI_GIT_MCP_CONTEXT_MAX_CHARS",
        "PANGI_GIT_MCP_TIMEOUT_SECONDS",
        "PANGI_GIT_MCP_WRITE_ENABLED",
        "PANGI_GIT_CLONE_URL_TEMPLATE",
        "PANGI_PUBLIC_BASE_URL",
        "PANGI_ENABLE_ADMIN_PAGES",
        "PANGI_ADMIN_PASSWORD",
    ):
        monkeypatch.delenv(name, raising=False)

    source_root = Path("/tmp/pangi/sources")
    (source_root / "PopPang-iOS").mkdir(parents=True, exist_ok=True)
    (tmp_path / ".env").write_text(
        "\n".join(
            [
                "SLACK_SIGNING_SECRET=",
                "SLACK_BOT_TOKEN=",
                "SLACK_ALLOWED_USER_IDS=",
                "SLACK_ALLOWED_CHANNEL_IDS=",
                "PANGI_WORKTREE_ROOT=/tmp/pangi/worktrees",
                "PANGI_SOURCE_REPO_ROOT=/tmp/pangi/sources",
            ]
        ),
        encoding="utf-8",
    )
    (tmp_path / ".env.example").write_text(
        "\n".join(
            [
                "SLACK_SIGNING_SECRET=dummy-local-signing-secret",
                "SLACK_BOT_TOKEN=dummy-local-bot-token",
                "SLACK_ALLOWED_USER_IDS=U_LOCAL",
                "SLACK_ALLOWED_CHANNEL_IDS=C_LOCAL",
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    clear_settings_cache()

    settings = Settings.from_env()

    assert settings.slack_allowed_user_ids == frozenset({"U_LOCAL"})
    assert settings.slack_allowed_channel_ids == frozenset({"C_LOCAL"})
