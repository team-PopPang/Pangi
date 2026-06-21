from pathlib import Path

import pytest

from pangi.config import (
    AccessDeniedError,
    Settings,
    SettingsError,
    UnknownRepoError,
    clear_settings_cache,
)


def valid_env(**overrides: str) -> dict[str, str]:
    values = {
        "SLACK_SIGNING_SECRET": "placeholder-signing-secret",
        "SLACK_BOT_TOKEN": "placeholder-bot-token",
        "SLACK_ALLOWED_USER_IDS": "U123,U456",
        "SLACK_ALLOWED_CHANNEL_IDS": "C123,C456",
        "PANGI_ALLOWED_REPOS": "PopPang-iOS=/tmp/pangi/sources/PopPang-iOS",
        "PANGI_WORKTREE_ROOT": "/tmp/pangi/worktrees",
        "PANGI_SOURCE_REPO_ROOT": "/tmp/pangi/sources",
    }
    values.update(overrides)
    return values


def test_settings_parses_allowlists_repo_paths_and_default_timeout():
    settings = Settings.from_env(valid_env())

    assert settings.slack_allowed_user_ids == frozenset({"U123", "U456"})
    assert settings.slack_allowed_channel_ids == frozenset({"C123", "C456"})
    assert settings.repo_path_for_key("PopPang-iOS") == Path(
        "/tmp/pangi/sources/PopPang-iOS"
    ).resolve(strict=False)
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
    assert settings.chat_model == "gpt-5.4-mini"
    assert settings.orchestrator_model == "gpt-5.4-mini"
    assert settings.analysis_model == "gpt-5.5"
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
            PANGI_ORCHESTRATOR_MODEL="gpt-5.5-codex",
            PANGI_ORCHESTRATOR_TIMEOUT_SECONDS="30",
            PANGI_ANALYSIS_MODEL="gpt-5.5-codex",
        )
    )

    assert settings.chat_timeout_seconds == 60
    assert settings.chat_workspace_root == Path("/tmp/pangi/worktrees/chat").resolve(strict=False)
    assert settings.chat_model == "gpt-5.4-mini-codex"
    assert settings.orchestrator_model == "gpt-5.5-codex"
    assert settings.orchestrator_timeout_seconds == 30
    assert settings.analysis_model == "gpt-5.5-codex"


def test_settings_rejects_chat_workspace_outside_worktree_root():
    with pytest.raises(SettingsError, match="chat workspace"):
        Settings.from_env(valid_env(PANGI_CHAT_WORKSPACE_ROOT="/tmp/outside/chat"))


def test_settings_rejects_invalid_orchestrator_timeout():
    with pytest.raises(SettingsError, match="PANGI_ORCHESTRATOR_TIMEOUT_SECONDS"):
        Settings.from_env(valid_env(PANGI_ORCHESTRATOR_TIMEOUT_SECONDS="0"))


def test_settings_rejects_unsafe_model_name():
    with pytest.raises(SettingsError, match="PANGI_CHAT_MODEL"):
        Settings.from_env(valid_env(PANGI_CHAT_MODEL="bad model"))


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


def test_settings_rejects_repo_path_outside_source_root():
    values = valid_env(PANGI_ALLOWED_REPOS="Other=/tmp/outside/Other")

    with pytest.raises(SettingsError, match="repo path"):
        Settings.from_env(values)


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
        "PANGI_ALLOWED_REPOS",
        "PANGI_WORKTREE_ROOT",
        "PANGI_SOURCE_REPO_ROOT",
        "PANGI_DEFAULT_BASE_BRANCH",
        "PANGI_JOB_TIMEOUT_SECONDS",
        "PANGI_CHAT_TIMEOUT_SECONDS",
        "PANGI_CHAT_WORKSPACE_ROOT",
        "PANGI_CHAT_MODEL",
        "PANGI_ORCHESTRATOR_MODEL",
        "PANGI_ORCHESTRATOR_TIMEOUT_SECONDS",
        "PANGI_ANALYSIS_MODEL",
        "PANGI_ENABLE_ADMIN_PAGES",
        "PANGI_ADMIN_PASSWORD",
    ):
        monkeypatch.delenv(name, raising=False)

    pangi_dir = tmp_path / "pangi"
    pangi_dir.mkdir()
    source_root = tmp_path / "sources"
    worktree_root = tmp_path / "worktrees"
    env_file = pangi_dir / ".env"
    env_file.write_text(
        "\n".join(
            [
                "SLACK_SIGNING_SECRET=placeholder-signing-secret",
                "SLACK_BOT_TOKEN=placeholder-bot-token",
                "SLACK_ALLOWED_USER_IDS=U123",
                "SLACK_ALLOWED_CHANNEL_IDS=C123",
                f"PANGI_ALLOWED_REPOS=PopPang-iOS={source_root}/PopPang-iOS",
                f"PANGI_WORKTREE_ROOT={worktree_root}",
                f"PANGI_SOURCE_REPO_ROOT={source_root}",
                "PANGI_JOB_TIMEOUT_SECONDS=700",
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    clear_settings_cache()

    settings = Settings.from_env()

    assert settings.slack_allowed_user_ids == frozenset({"U123"})
    assert settings.job_timeout_seconds == 700


def test_settings_uses_env_example_for_empty_local_values(tmp_path, monkeypatch):
    for name in (
        "SLACK_SIGNING_SECRET",
        "SLACK_BOT_TOKEN",
        "SLACK_ALLOWED_USER_IDS",
        "SLACK_ALLOWED_CHANNEL_IDS",
        "PANGI_ALLOWED_REPOS",
        "PANGI_WORKTREE_ROOT",
        "PANGI_SOURCE_REPO_ROOT",
        "PANGI_DEFAULT_BASE_BRANCH",
        "PANGI_JOB_TIMEOUT_SECONDS",
        "PANGI_CHAT_TIMEOUT_SECONDS",
        "PANGI_CHAT_WORKSPACE_ROOT",
        "PANGI_CHAT_MODEL",
        "PANGI_ORCHESTRATOR_MODEL",
        "PANGI_ORCHESTRATOR_TIMEOUT_SECONDS",
        "PANGI_ANALYSIS_MODEL",
        "PANGI_ENABLE_ADMIN_PAGES",
        "PANGI_ADMIN_PASSWORD",
    ):
        monkeypatch.delenv(name, raising=False)

    (tmp_path / ".env").write_text(
        "\n".join(
            [
                "SLACK_SIGNING_SECRET=",
                "SLACK_BOT_TOKEN=",
                "SLACK_ALLOWED_USER_IDS=",
                "SLACK_ALLOWED_CHANNEL_IDS=",
                "PANGI_ALLOWED_REPOS=PopPang-iOS=/tmp/pangi/sources/PopPang-iOS",
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
