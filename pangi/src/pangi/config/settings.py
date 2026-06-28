from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from string import Formatter
from types import MappingProxyType
from typing import Mapping
from urllib.parse import urlparse


DEFAULT_JOB_TIMEOUT_SECONDS = 600
DEFAULT_CHAT_TIMEOUT_SECONDS = 120
DEFAULT_ORCHESTRATOR_TIMEOUT_SECONDS = 20
DEFAULT_BASE_BRANCH = "develop"
FALLBACK_BASE_BRANCH = "main"
DEFAULT_LIGHT_MODEL = "gpt-5.4-mini"
DEFAULT_ANALYSIS_MODEL = "gpt-5.5"
DEFAULT_CHAT_MODEL = DEFAULT_LIGHT_MODEL
DEFAULT_ORCHESTRATOR_MODEL = DEFAULT_LIGHT_MODEL
DEFAULT_LIGHT_REASONING_EFFORT = "low"
DEFAULT_ANALYSIS_REASONING_EFFORT = "high"
DEFAULT_CHAT_REASONING_EFFORT = DEFAULT_LIGHT_REASONING_EFFORT
DEFAULT_ORCHESTRATOR_REASONING_EFFORT = DEFAULT_LIGHT_REASONING_EFFORT
DEFAULT_NOTION_MCP_URL = "https://mcp.notion.com/mcp"
DEFAULT_NOTION_CONTEXT_MAX_CHARS = 6000
DEFAULT_NOTION_TIMEOUT_SECONDS = 20
DEFAULT_NOTION_TOKEN_STORE_NAME = "notion-oauth.json"
DEFAULT_GIT_MCP_URL = "https://api.githubcopilot.com/mcp/"
DEFAULT_GIT_MCP_CONTEXT_URL = "https://api.githubcopilot.com/mcp/readonly"
DEFAULT_GIT_MCP_ORGS_URL = "https://api.githubcopilot.com/mcp/x/orgs/readonly"
DEFAULT_GIT_MCP_REPOS_URL = "https://api.githubcopilot.com/mcp/x/repos/readonly"
DEFAULT_GIT_MCP_ISSUES_URL = "https://api.githubcopilot.com/mcp/x/issues/readonly"
DEFAULT_GIT_MCP_PULL_REQUESTS_URL = "https://api.githubcopilot.com/mcp/x/pull_requests/readonly"
DEFAULT_GIT_MCP_ACTIONS_URL = "https://api.githubcopilot.com/mcp/x/actions/readonly"
DEFAULT_GIT_MCP_CONTEXT_MAX_CHARS = 6000
DEFAULT_GIT_MCP_TIMEOUT_SECONDS = 20
DEFAULT_GIT_CLONE_URL_TEMPLATE = "https://github.com/{org}/{repo}.git"
DEFAULT_CODEX_SESSION_IDLE_TIMEOUT_SECONDS = 3600
DEFAULT_SCHEDULER_TICK_SECONDS = 30
DEFAULT_EVAL_SCHEDULER_INTERVAL_SECONDS = 86400
ALLOW_ALL_MARKER = "*"
JOB_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]*$")
THREAD_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]*$")
GIT_REF_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]*$")
MODEL_NAME_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/+-]*$")
ENV_NAME_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
GIT_ORG_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")
NOTION_ID_PATTERN = re.compile(r"^[0-9a-f]{32}$")
REASONING_EFFORT_VALUES = frozenset({"minimal", "low", "medium", "high", "xhigh"})
GIT_CLONE_URL_TEMPLATE_FIELDS = frozenset({"org", "repo"})


class SettingsError(ValueError):
    """Raised when Pangi configuration is missing or unsafe."""


class AccessDeniedError(PermissionError):
    """Raised when a Slack user or channel is outside the allowlist."""


class UnknownRepoError(KeyError):
    """Raised when a requested repo key is not under the source repo root."""


@dataclass(frozen=True)
class Settings:
    slack_signing_secret: str = field(repr=False)
    slack_bot_token: str = field(repr=False)
    slack_allowed_user_ids: frozenset[str]
    slack_allowed_channel_ids: frozenset[str]
    allowed_repos: Mapping[str, Path]
    worktree_root: Path
    source_repo_root: Path
    default_base_branch: str = DEFAULT_BASE_BRANCH
    job_timeout_seconds: int = DEFAULT_JOB_TIMEOUT_SECONDS
    chat_timeout_seconds: int = DEFAULT_CHAT_TIMEOUT_SECONDS
    chat_model: str = DEFAULT_CHAT_MODEL
    chat_reasoning_effort: str = DEFAULT_CHAT_REASONING_EFFORT
    orchestrator_timeout_seconds: int = DEFAULT_ORCHESTRATOR_TIMEOUT_SECONDS
    orchestrator_model: str = DEFAULT_ORCHESTRATOR_MODEL
    orchestrator_reasoning_effort: str = DEFAULT_ORCHESTRATOR_REASONING_EFFORT
    analysis_model: str = DEFAULT_ANALYSIS_MODEL
    analysis_reasoning_effort: str = DEFAULT_ANALYSIS_REASONING_EFFORT
    chat_workspace_root: Path | None = None
    public_base_url: str | None = None
    notion_enabled: bool = False
    notion_mcp_url: str = DEFAULT_NOTION_MCP_URL
    notion_allowed_page_ids: frozenset[str] = frozenset()
    notion_allowed_database_ids: frozenset[str] = frozenset()
    notion_context_max_chars: int = DEFAULT_NOTION_CONTEXT_MAX_CHARS
    notion_timeout_seconds: int = DEFAULT_NOTION_TIMEOUT_SECONDS
    notion_token_store_path: Path | None = None
    notion_write_enabled: bool = False
    git_mcp_enabled: bool = False
    git_mcp_url: str = DEFAULT_GIT_MCP_URL
    git_mcp_context_url: str = DEFAULT_GIT_MCP_CONTEXT_URL
    git_mcp_orgs_url: str = DEFAULT_GIT_MCP_ORGS_URL
    git_mcp_repos_url: str = DEFAULT_GIT_MCP_REPOS_URL
    git_mcp_issues_url: str = DEFAULT_GIT_MCP_ISSUES_URL
    git_mcp_pull_requests_url: str = DEFAULT_GIT_MCP_PULL_REQUESTS_URL
    git_mcp_actions_url: str = DEFAULT_GIT_MCP_ACTIONS_URL
    git_mcp_token: str | None = field(default=None, repr=False)
    git_mcp_org: str | None = None
    git_mcp_context_max_chars: int = DEFAULT_GIT_MCP_CONTEXT_MAX_CHARS
    git_mcp_timeout_seconds: int = DEFAULT_GIT_MCP_TIMEOUT_SECONDS
    git_mcp_write_enabled: bool = False
    git_clone_url_template: str | None = None
    codex_session_idle_timeout_seconds: int = DEFAULT_CODEX_SESSION_IDLE_TIMEOUT_SECONDS
    scheduler_enabled: bool = False
    scheduler_tick_seconds: int = DEFAULT_SCHEDULER_TICK_SECONDS
    eval_scheduler_enabled: bool = False
    eval_scheduler_interval_seconds: int = DEFAULT_EVAL_SCHEDULER_INTERVAL_SECONDS
    eval_alert_channel_id: str | None = None
    enable_admin_pages: bool = False
    admin_password: str | None = field(default=None, repr=False)

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> "Settings":
        if environ is None:
            load_dotenv_files()
        values = environ if environ is not None else os.environ
        required_names = (
            "SLACK_SIGNING_SECRET",
            "SLACK_BOT_TOKEN",
            "SLACK_ALLOWED_USER_IDS",
            "SLACK_ALLOWED_CHANNEL_IDS",
            "PANGI_WORKTREE_ROOT",
            "PANGI_SOURCE_REPO_ROOT",
        )
        missing = [name for name in required_names if not values.get(name, "").strip()]
        if missing:
            raise SettingsError(f"Missing required environment variables: {', '.join(missing)}")

        source_repo_root = _parse_absolute_path(values["PANGI_SOURCE_REPO_ROOT"], "PANGI_SOURCE_REPO_ROOT")
        worktree_root = _parse_absolute_path(values["PANGI_WORKTREE_ROOT"], "PANGI_WORKTREE_ROOT")

        enable_admin_pages = _parse_bool(
            values.get("PANGI_ENABLE_ADMIN_PAGES", "0"),
            "PANGI_ENABLE_ADMIN_PAGES",
        )
        admin_password = values.get("PANGI_ADMIN_PASSWORD", "").strip() or None
        if enable_admin_pages and admin_password is None:
            raise SettingsError("PANGI_ADMIN_PASSWORD is required when PANGI_ENABLE_ADMIN_PAGES=1")

        allowed_repos = MappingProxyType(
            _discover_source_repos(source_repo_root)
        )
        default_base_branch = _parse_git_ref(
            values.get("PANGI_DEFAULT_BASE_BRANCH") or DEFAULT_BASE_BRANCH,
            "PANGI_DEFAULT_BASE_BRANCH",
        )
        raw_chat_workspace_root = values.get("PANGI_CHAT_WORKSPACE_ROOT", "").strip()
        chat_workspace_root = (
            _parse_absolute_path(raw_chat_workspace_root, "PANGI_CHAT_WORKSPACE_ROOT")
            if raw_chat_workspace_root
            else (worktree_root / "_chat").resolve(strict=False)
        )
        _ensure_path_under_root(chat_workspace_root, worktree_root, "chat workspace path")
        raw_notion_token_store_path = values.get("PANGI_NOTION_TOKEN_STORE_PATH", "").strip()
        notion_token_store_path = (
            _parse_absolute_path(raw_notion_token_store_path, "PANGI_NOTION_TOKEN_STORE_PATH")
            if raw_notion_token_store_path
            else (worktree_root / "_notion" / DEFAULT_NOTION_TOKEN_STORE_NAME).resolve(strict=False)
        )
        _ensure_path_under_root(notion_token_store_path, worktree_root, "Notion token store path")

        return cls(
            slack_signing_secret=values["SLACK_SIGNING_SECRET"],
            slack_bot_token=values["SLACK_BOT_TOKEN"],
            slack_allowed_user_ids=_parse_csv_set(
                values["SLACK_ALLOWED_USER_IDS"],
                "SLACK_ALLOWED_USER_IDS",
            ),
            slack_allowed_channel_ids=_parse_csv_set(
                values["SLACK_ALLOWED_CHANNEL_IDS"],
                "SLACK_ALLOWED_CHANNEL_IDS",
            ),
            allowed_repos=allowed_repos,
            worktree_root=worktree_root,
            source_repo_root=source_repo_root,
            default_base_branch=default_base_branch,
            job_timeout_seconds=_parse_positive_int(
                values.get("PANGI_JOB_TIMEOUT_SECONDS", str(DEFAULT_JOB_TIMEOUT_SECONDS)),
                "PANGI_JOB_TIMEOUT_SECONDS",
            ),
            chat_timeout_seconds=_parse_positive_int(
                values.get("PANGI_CHAT_TIMEOUT_SECONDS", str(DEFAULT_CHAT_TIMEOUT_SECONDS)),
                "PANGI_CHAT_TIMEOUT_SECONDS",
            ),
            chat_model=_parse_model_name(
                values.get("PANGI_CHAT_MODEL"),
                DEFAULT_CHAT_MODEL,
                "PANGI_CHAT_MODEL",
            ),
            chat_reasoning_effort=_parse_reasoning_effort(
                values.get("PANGI_CHAT_REASONING_EFFORT"),
                DEFAULT_CHAT_REASONING_EFFORT,
                "PANGI_CHAT_REASONING_EFFORT",
            ),
            orchestrator_timeout_seconds=_parse_positive_int(
                values.get("PANGI_ORCHESTRATOR_TIMEOUT_SECONDS", str(DEFAULT_ORCHESTRATOR_TIMEOUT_SECONDS)),
                "PANGI_ORCHESTRATOR_TIMEOUT_SECONDS",
            ),
            orchestrator_model=_parse_model_name(
                values.get("PANGI_ORCHESTRATOR_MODEL"),
                DEFAULT_ORCHESTRATOR_MODEL,
                "PANGI_ORCHESTRATOR_MODEL",
            ),
            orchestrator_reasoning_effort=_parse_reasoning_effort(
                values.get("PANGI_ORCHESTRATOR_REASONING_EFFORT"),
                DEFAULT_ORCHESTRATOR_REASONING_EFFORT,
                "PANGI_ORCHESTRATOR_REASONING_EFFORT",
            ),
            analysis_model=_parse_model_name(
                values.get("PANGI_ANALYSIS_MODEL"),
                DEFAULT_ANALYSIS_MODEL,
                "PANGI_ANALYSIS_MODEL",
            ),
            analysis_reasoning_effort=_parse_reasoning_effort(
                values.get("PANGI_ANALYSIS_REASONING_EFFORT"),
                DEFAULT_ANALYSIS_REASONING_EFFORT,
                "PANGI_ANALYSIS_REASONING_EFFORT",
            ),
            chat_workspace_root=chat_workspace_root,
            public_base_url=_parse_optional_url(values.get("PANGI_PUBLIC_BASE_URL"), "PANGI_PUBLIC_BASE_URL"),
            notion_enabled=_parse_bool(values.get("PANGI_NOTION_ENABLED", "0"), "PANGI_NOTION_ENABLED"),
            notion_mcp_url=_parse_url(
                values.get("PANGI_NOTION_MCP_URL"),
                DEFAULT_NOTION_MCP_URL,
                "PANGI_NOTION_MCP_URL",
            ),
            notion_allowed_page_ids=_parse_notion_id_set(
                values.get("PANGI_NOTION_ALLOWED_PAGE_IDS", ""),
                "PANGI_NOTION_ALLOWED_PAGE_IDS",
            ),
            notion_allowed_database_ids=_parse_notion_id_set(
                values.get("PANGI_NOTION_ALLOWED_DATABASE_IDS", ""),
                "PANGI_NOTION_ALLOWED_DATABASE_IDS",
            ),
            notion_context_max_chars=_parse_positive_int(
                values.get("PANGI_NOTION_CONTEXT_MAX_CHARS", str(DEFAULT_NOTION_CONTEXT_MAX_CHARS)),
                "PANGI_NOTION_CONTEXT_MAX_CHARS",
            ),
            notion_timeout_seconds=_parse_positive_int(
                values.get("PANGI_NOTION_TIMEOUT_SECONDS", str(DEFAULT_NOTION_TIMEOUT_SECONDS)),
                "PANGI_NOTION_TIMEOUT_SECONDS",
            ),
            notion_token_store_path=notion_token_store_path,
            notion_write_enabled=_parse_bool(
                values.get("PANGI_NOTION_WRITE_ENABLED", "0"),
                "PANGI_NOTION_WRITE_ENABLED",
            ),
            git_mcp_enabled=_parse_bool(values.get("PANGI_GIT_MCP_ENABLED", "0"), "PANGI_GIT_MCP_ENABLED"),
            git_mcp_url=_parse_url(
                values.get("PANGI_GIT_MCP_URL"),
                DEFAULT_GIT_MCP_URL,
                "PANGI_GIT_MCP_URL",
            ),
            git_mcp_context_url=_parse_url(
                values.get("PANGI_GIT_MCP_CONTEXT_URL"),
                DEFAULT_GIT_MCP_CONTEXT_URL,
                "PANGI_GIT_MCP_CONTEXT_URL",
            ),
            git_mcp_orgs_url=_parse_url(
                values.get("PANGI_GIT_MCP_ORGS_URL"),
                DEFAULT_GIT_MCP_ORGS_URL,
                "PANGI_GIT_MCP_ORGS_URL",
            ),
            git_mcp_repos_url=_parse_url(
                values.get("PANGI_GIT_MCP_REPOS_URL"),
                DEFAULT_GIT_MCP_REPOS_URL,
                "PANGI_GIT_MCP_REPOS_URL",
            ),
            git_mcp_issues_url=_parse_url(
                values.get("PANGI_GIT_MCP_ISSUES_URL"),
                DEFAULT_GIT_MCP_ISSUES_URL,
                "PANGI_GIT_MCP_ISSUES_URL",
            ),
            git_mcp_pull_requests_url=_parse_url(
                values.get("PANGI_GIT_MCP_PULL_REQUESTS_URL"),
                DEFAULT_GIT_MCP_PULL_REQUESTS_URL,
                "PANGI_GIT_MCP_PULL_REQUESTS_URL",
            ),
            git_mcp_actions_url=_parse_url(
                values.get("PANGI_GIT_MCP_ACTIONS_URL"),
                DEFAULT_GIT_MCP_ACTIONS_URL,
                "PANGI_GIT_MCP_ACTIONS_URL",
            ),
            git_mcp_token=values.get("PANGI_GIT_MCP_TOKEN", "").strip() or None,
            git_mcp_org=_parse_optional_git_org(values.get("PANGI_GIT_MCP_ORG"), "PANGI_GIT_MCP_ORG"),
            git_mcp_context_max_chars=_parse_positive_int(
                values.get("PANGI_GIT_MCP_CONTEXT_MAX_CHARS", str(DEFAULT_GIT_MCP_CONTEXT_MAX_CHARS)),
                "PANGI_GIT_MCP_CONTEXT_MAX_CHARS",
            ),
            git_mcp_timeout_seconds=_parse_positive_int(
                values.get("PANGI_GIT_MCP_TIMEOUT_SECONDS", str(DEFAULT_GIT_MCP_TIMEOUT_SECONDS)),
                "PANGI_GIT_MCP_TIMEOUT_SECONDS",
            ),
            git_mcp_write_enabled=_parse_bool(
                values.get("PANGI_GIT_MCP_WRITE_ENABLED", "0"),
                "PANGI_GIT_MCP_WRITE_ENABLED",
            ),
            git_clone_url_template=_parse_git_clone_url_template(
                values.get("PANGI_GIT_CLONE_URL_TEMPLATE"),
                "PANGI_GIT_CLONE_URL_TEMPLATE",
            ),
            codex_session_idle_timeout_seconds=_parse_positive_int(
                values.get(
                    "PANGI_CODEX_SESSION_IDLE_TIMEOUT_SECONDS",
                    str(DEFAULT_CODEX_SESSION_IDLE_TIMEOUT_SECONDS),
                ),
                "PANGI_CODEX_SESSION_IDLE_TIMEOUT_SECONDS",
            ),
            scheduler_enabled=_parse_bool(values.get("PANGI_SCHEDULER_ENABLED", "0"), "PANGI_SCHEDULER_ENABLED"),
            scheduler_tick_seconds=_parse_positive_int(
                values.get("PANGI_SCHEDULER_TICK_SECONDS", str(DEFAULT_SCHEDULER_TICK_SECONDS)),
                "PANGI_SCHEDULER_TICK_SECONDS",
            ),
            eval_scheduler_enabled=_parse_bool(
                values.get("PANGI_EVAL_SCHEDULER_ENABLED", "0"),
                "PANGI_EVAL_SCHEDULER_ENABLED",
            ),
            eval_scheduler_interval_seconds=_parse_positive_int(
                values.get(
                    "PANGI_EVAL_SCHEDULER_INTERVAL_SECONDS",
                    str(DEFAULT_EVAL_SCHEDULER_INTERVAL_SECONDS),
                ),
                "PANGI_EVAL_SCHEDULER_INTERVAL_SECONDS",
            ),
            eval_alert_channel_id=values.get("PANGI_EVAL_ALERT_CHANNEL_ID", "").strip() or None,
            enable_admin_pages=enable_admin_pages,
            admin_password=admin_password,
        )

    def is_user_allowed(self, user_id: str) -> bool:
        return _allows_value(self.slack_allowed_user_ids, user_id)

    def is_channel_allowed(self, channel_id: str) -> bool:
        return _allows_value(self.slack_allowed_channel_ids, channel_id)

    def validate_slack_access(self, *, user_id: str, channel_id: str) -> None:
        if not self.is_user_allowed(user_id):
            raise AccessDeniedError("Slack user is not allowed")
        if not self.is_channel_allowed(channel_id):
            raise AccessDeniedError("Slack channel is not allowed")

    def repo_path_for_key(self, repo_key: str) -> Path:
        try:
            return self.allowed_repos[repo_key]
        except KeyError:
            if not self._is_known_or_cloneable_repo_key(repo_key):
                raise UnknownRepoError(repo_key) from None
            repo_path = (self.source_repo_root / repo_key).resolve(strict=False)
            _ensure_path_under_root(repo_path, self.source_repo_root, f"repo path for {repo_key}")
            return repo_path

    def clone_url_for_key(self, repo_key: str) -> str:
        if not self._is_known_or_cloneable_repo_key(repo_key) or not self.git_mcp_org:
            raise UnknownRepoError(repo_key)
        template = self.git_clone_url_template or DEFAULT_GIT_CLONE_URL_TEMPLATE
        return template.format(org=self.git_mcp_org, repo=repo_key)

    def available_repo_keys(self) -> tuple[str, ...]:
        return tuple(sorted(self.allowed_repos))

    def base_branch_for_key(self, repo_key: str) -> str:
        if not self._is_known_or_cloneable_repo_key(repo_key):
            raise UnknownRepoError(repo_key)
        return self.default_base_branch

    def base_branch_candidates_for_key(self, repo_key: str) -> tuple[str, ...]:
        if not self._is_known_or_cloneable_repo_key(repo_key):
            raise UnknownRepoError(repo_key)
        if self.default_base_branch == FALLBACK_BASE_BRANCH:
            return (self.default_base_branch,)
        return (self.default_base_branch, FALLBACK_BASE_BRANCH)

    def worktree_path_for_job(self, job_id: str) -> Path:
        if not JOB_ID_PATTERN.fullmatch(job_id):
            raise SettingsError("Job id must contain only letters, numbers, hyphen, or underscore")
        worktree_path = (self.worktree_root / job_id).resolve(strict=False)
        _ensure_path_under_root(worktree_path, self.worktree_root, "job worktree path")
        return worktree_path

    def thread_workspace_path(self, slack_thread_id: str) -> Path:
        if not THREAD_ID_PATTERN.fullmatch(slack_thread_id):
            raise SettingsError("Thread id must contain only letters, numbers, hyphen, or underscore")
        workspace_path = (self.worktree_root / "_threads" / slack_thread_id).resolve(strict=False)
        _ensure_path_under_root(workspace_path, self.worktree_root, "thread workspace path")
        return workspace_path

    def repo_workspace_path(self, slack_thread_id: str, repo_key: str) -> Path:
        workspace_path = (self.thread_workspace_path(slack_thread_id) / "repos" / repo_key).resolve(strict=False)
        _ensure_path_under_root(workspace_path, self.worktree_root, "repo workspace path")
        return workspace_path

    def is_notion_page_allowed(self, notion_id: str) -> bool:
        return normalize_notion_id(notion_id) in self.notion_allowed_page_ids

    def is_notion_database_allowed(self, notion_id: str) -> bool:
        return normalize_notion_id(notion_id) in self.notion_allowed_database_ids

    def _is_known_or_cloneable_repo_key(self, repo_key: str) -> bool:
        if repo_key in self.allowed_repos:
            return True
        return bool(
            self.git_mcp_enabled
            and self.git_mcp_org
            and GIT_ORG_PATTERN.fullmatch(repo_key)
            and not repo_key.startswith("-")
        )


def _parse_csv_set(raw_value: str, name: str) -> frozenset[str]:
    values = frozenset(item.strip() for item in raw_value.split(",") if item.strip())
    if not values:
        raise SettingsError(f"{name} must contain at least one value")
    return values


def _allows_value(allowed_values: frozenset[str], value: str) -> bool:
    return ALLOW_ALL_MARKER in allowed_values or value in allowed_values


def load_dotenv_files() -> None:
    seen_paths: set[Path] = set()
    for path in _dotenv_candidate_paths():
        if path in seen_paths:
            continue
        seen_paths.add(path)
        if path.is_file():
            _load_dotenv_file(path)


def _dotenv_candidate_paths() -> tuple[Path, ...]:
    current_dir = Path.cwd()
    package_root = Path(__file__).resolve().parents[3]
    return (
        current_dir / ".env",
        current_dir / ".env.example",
        current_dir / "pangi" / ".env",
        current_dir / "pangi" / ".env.example",
        package_root / ".env",
        package_root / ".env.example",
    )


def _load_dotenv_file(path: Path) -> None:
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue

        raw_name, raw_value = stripped.split("=", 1)
        name = raw_name.strip()
        value = _parse_dotenv_value(raw_value.strip())
        if not ENV_NAME_PATTERN.fullmatch(name) or name in os.environ or not value:
            continue

        os.environ[name] = value


def _parse_dotenv_value(raw_value: str) -> str:
    raw_value = _strip_dotenv_inline_comment(raw_value)
    if len(raw_value) >= 2 and raw_value[0] == raw_value[-1] and raw_value[0] in {"'", '"'}:
        return raw_value[1:-1]
    return raw_value


def _strip_dotenv_inline_comment(raw_value: str) -> str:
    quote: str | None = None
    escaped = False
    for index, char in enumerate(raw_value):
        if escaped:
            escaped = False
            continue
        if quote == '"' and char == "\\":
            escaped = True
            continue
        if quote:
            if char == quote:
                quote = None
            continue
        if char in {"'", '"'}:
            quote = char
            continue
        if char == "#" and (index == 0 or raw_value[index - 1].isspace()):
            return raw_value[:index].rstrip()
    return raw_value.strip()


def _discover_source_repos(source_repo_root: Path) -> dict[str, Path]:
    repos: dict[str, Path] = {}
    if not source_repo_root.exists():
        return repos
    if not source_repo_root.is_dir():
        raise SettingsError("PANGI_SOURCE_REPO_ROOT must be a directory")

    for child in sorted(source_repo_root.iterdir(), key=lambda path: path.name):
        if child.name.startswith(".") or not child.is_dir():
            continue
        _ensure_path_under_root(child, source_repo_root, f"repo path for {child.name}")
        repos[child.name] = child.resolve(strict=False)
    return repos


def _parse_absolute_path(raw_value: str, name: str) -> Path:
    path = Path(raw_value).expanduser()
    if not path.is_absolute():
        raise SettingsError(f"{name} must be an absolute path")
    return path.resolve(strict=False)


def _parse_positive_int(raw_value: str, name: str) -> int:
    try:
        value = int(raw_value)
    except ValueError:
        raise SettingsError(f"{name} must be a positive integer") from None
    if value <= 0:
        raise SettingsError(f"{name} must be a positive integer")
    return value


def _parse_model_name(raw_value: str | None, default_value: str, name: str) -> str:
    value = (raw_value or "").strip() or default_value
    if not MODEL_NAME_PATTERN.fullmatch(value) or value.startswith("-"):
        raise SettingsError(f"{name} must be a safe model name")
    return value


def _parse_reasoning_effort(raw_value: str | None, default_value: str, name: str) -> str:
    value = ((raw_value or "").strip() or default_value).lower()
    if value not in REASONING_EFFORT_VALUES:
        allowed = ", ".join(sorted(REASONING_EFFORT_VALUES))
        raise SettingsError(f"{name} must be one of: {allowed}")
    return value


def _parse_url(raw_value: str | None, default_value: str, name: str) -> str:
    value = (raw_value or "").strip() or default_value
    _validate_http_url(value, name)
    return value


def _parse_optional_url(raw_value: str | None, name: str) -> str | None:
    value = (raw_value or "").strip()
    if not value:
        return None
    _validate_http_url(value, name)
    return value.rstrip("/")


def _parse_optional_git_org(raw_value: str | None, name: str) -> str | None:
    value = (raw_value or "").strip()
    if not value:
        return None
    if not GIT_ORG_PATTERN.fullmatch(value) or value.startswith("-"):
        raise SettingsError(f"{name} must be a safe GitHub organization name")
    return value


def _parse_git_clone_url_template(raw_value: str | None, name: str) -> str | None:
    value = (raw_value or "").strip()
    if not value:
        return None
    if any(char.isspace() or ord(char) < 32 for char in value):
        raise SettingsError(f"{name} must not contain whitespace or control characters")

    fields: list[str] = []
    try:
        for _literal_text, field_name, format_spec, conversion in Formatter().parse(value):
            if field_name is None:
                continue
            root_field = field_name.split(".", 1)[0].split("[", 1)[0]
            if field_name != root_field or format_spec or conversion:
                raise SettingsError(f"{name} may only use simple {{org}} and {{repo}} placeholders")
            fields.append(field_name)
    except ValueError as error:
        raise SettingsError(f"{name} must be a valid format string") from error

    if "repo" not in fields:
        raise SettingsError(f"{name} must include {{repo}}")
    invalid_fields = sorted(set(fields) - GIT_CLONE_URL_TEMPLATE_FIELDS)
    if invalid_fields:
        allowed = ", ".join(f"{{{field}}}" for field in sorted(GIT_CLONE_URL_TEMPLATE_FIELDS))
        invalid = ", ".join(f"{{{field}}}" for field in invalid_fields)
        raise SettingsError(f"{name} may only use {allowed}, not {invalid}")
    return value


def _validate_http_url(value: str, name: str) -> None:
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise SettingsError(f"{name} must be an http or https URL")
    if parsed.scheme == "http" and parsed.hostname not in {"localhost", "127.0.0.1", "::1"}:
        raise SettingsError(f"{name} must use https unless it points to localhost")


def _parse_notion_id_set(raw_value: str, name: str) -> frozenset[str]:
    values = [item.strip() for item in raw_value.split(",") if item.strip()]
    return frozenset(normalize_notion_id(value, name=name) for value in values)


def normalize_notion_id(raw_value: str, *, name: str = "Notion id") -> str:
    value = raw_value.strip().lower().replace("-", "")
    if not NOTION_ID_PATTERN.fullmatch(value):
        raise SettingsError(f"{name} must be a 32 character Notion UUID")
    return value


def _parse_git_ref(raw_value: str, name: str) -> str:
    value = raw_value.strip()
    if (
        not value
        or not GIT_REF_PATTERN.fullmatch(value)
        or value.startswith("-")
        or value.startswith("/")
        or value.endswith("/")
        or ".." in value
        or "@{" in value
    ):
        raise SettingsError(f"{name} must be a safe git ref name")
    return value


def _parse_bool(raw_value: str, name: str) -> bool:
    normalized = raw_value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off", ""}:
        return False
    raise SettingsError(f"{name} must be a boolean value")


def _ensure_path_under_root(path: Path, root: Path, label: str) -> None:
    try:
        path.relative_to(root)
    except ValueError:
        raise SettingsError(f"{label} must be under configured source/worktree root") from None


@lru_cache
def get_settings() -> Settings:
    return Settings.from_env()


def clear_settings_cache() -> None:
    get_settings.cache_clear()
