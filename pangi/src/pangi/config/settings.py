from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from types import MappingProxyType
from typing import Mapping


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
ALLOW_ALL_MARKER = "*"
JOB_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]*$")
GIT_REF_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]*$")
MODEL_NAME_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/+-]*$")
ENV_NAME_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
REASONING_EFFORT_VALUES = frozenset({"minimal", "low", "medium", "high", "xhigh"})


class SettingsError(ValueError):
    """Raised when Pangi configuration is missing or unsafe."""


class AccessDeniedError(PermissionError):
    """Raised when a Slack user or channel is outside the allowlist."""


class UnknownRepoError(KeyError):
    """Raised when a requested repo key is not in the repo allowlist."""


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
            "PANGI_ALLOWED_REPOS",
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
            _parse_repo_allowlist(
                values["PANGI_ALLOWED_REPOS"],
                source_repo_root=source_repo_root,
            )
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
            raise UnknownRepoError(repo_key) from None

    def base_branch_for_key(self, repo_key: str) -> str:
        if repo_key not in self.allowed_repos:
            raise UnknownRepoError(repo_key)
        return self.default_base_branch

    def base_branch_candidates_for_key(self, repo_key: str) -> tuple[str, ...]:
        if repo_key not in self.allowed_repos:
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
    if len(raw_value) >= 2 and raw_value[0] == raw_value[-1] and raw_value[0] in {"'", '"'}:
        return raw_value[1:-1]
    return raw_value


def _parse_repo_allowlist(raw_value: str, *, source_repo_root: Path) -> dict[str, Path]:
    repos: dict[str, Path] = {}
    for entry in raw_value.split(","):
        stripped = entry.strip()
        if not stripped:
            continue
        if "=" not in stripped:
            raise SettingsError("PANGI_ALLOWED_REPOS entries must use RepoKey=/absolute/path format")

        repo_key, raw_path = (part.strip() for part in stripped.split("=", 1))
        if not repo_key or not raw_path:
            raise SettingsError("PANGI_ALLOWED_REPOS entries must include both repo key and path")
        if repo_key in repos:
            raise SettingsError(f"Duplicate repo key in PANGI_ALLOWED_REPOS: {repo_key}")

        repo_path = _parse_absolute_path(raw_path, f"PANGI_ALLOWED_REPOS[{repo_key}]")
        _ensure_path_under_root(repo_path, source_repo_root, f"repo path for {repo_key}")
        repos[repo_key] = repo_path

    if not repos:
        raise SettingsError("PANGI_ALLOWED_REPOS must contain at least one repo")
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
