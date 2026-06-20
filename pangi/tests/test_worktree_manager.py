import asyncio
import subprocess
from pathlib import Path

import pytest

from pangi.config import Settings
from pangi.infra.git import GitCommandError, GitWorktreeManager, WorktreePathExistsError


def settings_for(tmp_path: Path, source_repo: Path, **overrides: str) -> Settings:
    source_root = tmp_path / "sources"
    values = {
        "SLACK_SIGNING_SECRET": "placeholder-signing-secret",
        "SLACK_BOT_TOKEN": "placeholder-bot-token",
        "SLACK_ALLOWED_USER_IDS": "U123",
        "SLACK_ALLOWED_CHANNEL_IDS": "C123",
        "PANGI_ALLOWED_REPOS": f"PopPang-iOS={source_repo}",
        "PANGI_WORKTREE_ROOT": str(tmp_path / "worktrees"),
        "PANGI_SOURCE_REPO_ROOT": str(source_root),
        "PANGI_DEFAULT_BASE_BRANCH": "develop",
    }
    values.update(overrides)
    return Settings.from_env(values)


def run_git(cwd: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(cwd), *args],
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout


def create_source_repo(tmp_path: Path, *, base_branch: str = "develop") -> Path:
    source_root = tmp_path / "sources"
    source_repo = source_root / "PopPang-iOS"
    remote_repo = tmp_path / "remote.git"
    source_repo.mkdir(parents=True)

    subprocess.run(["git", "init", "--bare", str(remote_repo)], check=True, capture_output=True)
    subprocess.run(["git", "init", str(source_repo)], check=True, capture_output=True)
    run_git(source_repo, "checkout", "-b", base_branch)
    run_git(source_repo, "config", "user.email", "pangi@example.com")
    run_git(source_repo, "config", "user.name", "Pangi Test")
    (source_repo / "README.md").write_text("# Pangi test repo\n", encoding="utf-8")
    run_git(source_repo, "add", "README.md")
    run_git(source_repo, "commit", "-m", "Initial commit")
    run_git(source_repo, "remote", "add", "origin", str(remote_repo))
    run_git(source_repo, "push", "-u", "origin", base_branch)
    return source_repo


def test_worktree_manager_creates_detached_read_only_worktree(tmp_path):
    async def scenario():
        source_repo = create_source_repo(tmp_path)
        settings = settings_for(tmp_path, source_repo)
        manager = GitWorktreeManager(settings=settings)

        context = await manager.prepare_read_only_worktree(
            job_id="job_123",
            repo_key="PopPang-iOS",
        )

        assert context.path == settings.worktree_path_for_job("job_123")
        assert context.base_ref == "origin/develop"
        assert (context.path / "README.md").read_text(encoding="utf-8") == "# Pangi test repo\n"
        assert run_git(context.path, "branch", "--show-current").strip() == ""

    asyncio.run(scenario())


def test_worktree_manager_falls_back_to_main_when_develop_is_missing(tmp_path):
    async def scenario():
        source_repo = create_source_repo(tmp_path, base_branch="main")
        settings = settings_for(tmp_path, source_repo)
        manager = GitWorktreeManager(settings=settings)

        context = await manager.prepare_read_only_worktree(
            job_id="job_123",
            repo_key="PopPang-iOS",
        )

        assert context.base_ref == "origin/main"
        assert (context.path / "README.md").is_file()

    asyncio.run(scenario())


def test_worktree_manager_rejects_missing_git_repo(tmp_path):
    async def scenario():
        source_root = tmp_path / "sources"
        source_repo = source_root / "PopPang-iOS"
        source_repo.mkdir(parents=True)
        manager = GitWorktreeManager(settings=settings_for(tmp_path, source_repo))

        with pytest.raises(GitCommandError):
            await manager.prepare_read_only_worktree(job_id="job_123", repo_key="PopPang-iOS")

    asyncio.run(scenario())


def test_worktree_manager_rejects_existing_worktree_path(tmp_path):
    async def scenario():
        source_repo = create_source_repo(tmp_path)
        settings = settings_for(tmp_path, source_repo)
        settings.worktree_path_for_job("job_123").mkdir(parents=True)
        manager = GitWorktreeManager(settings=settings)

        with pytest.raises(WorktreePathExistsError):
            await manager.prepare_read_only_worktree(job_id="job_123", repo_key="PopPang-iOS")

    asyncio.run(scenario())
