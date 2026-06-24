from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path
import shutil

from pangi.config import Settings, get_settings
from pangi.usecase.ports import ThreadWorkspaceContext


DEFAULT_GIT_TIMEOUT_SECONDS = 60


class GitWorktreeError(RuntimeError):
    pass


class WorktreePathExistsError(GitWorktreeError):
    pass


class UnsafeWorktreePathError(GitWorktreeError):
    pass


class GitCommandError(GitWorktreeError):
    def __init__(self, command: tuple[str, ...], stderr: str) -> None:
        self.command = command
        self.stderr = stderr.strip()
        super().__init__(self.stderr or f"Git command failed: {' '.join(command)}")


@dataclass(frozen=True)
class GitWorktreeManager:
    settings: Settings
    git_binary: str = "git"
    command_timeout_seconds: float = DEFAULT_GIT_TIMEOUT_SECONDS

    async def prepare_thread_repo_workspace(
        self,
        *,
        slack_thread_id: str,
        repo_key: str,
    ) -> ThreadWorkspaceContext:
        source_repo_path = self.settings.repo_path_for_key(repo_key)
        workspace_path = self.settings.thread_workspace_path(slack_thread_id)
        repo_path = self.settings.repo_workspace_path(slack_thread_id, repo_key)
        self._validate_worktree_path(repo_path, source_repo_path)
        workspace_path.mkdir(parents=True, exist_ok=True)

        await self._ensure_source_repo(repo_key=repo_key, source_repo_path=source_repo_path)
        base_ref = await self._resolve_base_ref(
            source_repo_path=source_repo_path,
            repo_key=repo_key,
        )
        context = await self._prepare_repo_workspace(
            source_repo_path=source_repo_path,
            workspace_path=workspace_path,
            repo_path=repo_path,
            base_ref=base_ref,
        )
        await self._ensure_git_repo(context.repo_path)

        return context

    async def cleanup_thread_workspace(self, *, slack_thread_id: str) -> None:
        workspace_path = self.settings.thread_workspace_path(slack_thread_id)
        if workspace_path.exists():
            shutil.rmtree(workspace_path)

    async def _ensure_source_repo(self, *, repo_key: str, source_repo_path: Path) -> None:
        if source_repo_path.exists():
            await self._ensure_git_repo(source_repo_path)
            return

        source_repo_path.parent.mkdir(parents=True, exist_ok=True)
        clone_url = self.settings.clone_url_for_key(repo_key)
        await self._run_git(
            self.settings.source_repo_root,
            "clone",
            "--",
            clone_url,
            str(source_repo_path),
        )
        await self._ensure_git_repo(source_repo_path)

    async def _resolve_base_ref(
        self,
        *,
        source_repo_path: Path,
        repo_key: str,
    ) -> str:
        last_error: GitCommandError | None = None
        for base_branch in self.settings.base_branch_candidates_for_key(repo_key):
            try:
                await self._run_git(source_repo_path, "fetch", "origin", base_branch)
            except GitCommandError as error:
                last_error = error
                continue

            return f"origin/{base_branch}"

        if last_error is not None:
            raise last_error
        raise GitWorktreeError("No base branch candidates configured")

    async def _prepare_repo_workspace(
        self,
        *,
        source_repo_path: Path,
        workspace_path: Path,
        repo_path: Path,
        base_ref: str,
    ) -> ThreadWorkspaceContext:
        if repo_path.exists():
            if not repo_path.is_dir():
                raise WorktreePathExistsError(f"Repo workspace path already exists and is not a directory: {repo_path}")
        else:
            repo_path.parent.mkdir(parents=True, exist_ok=True)
            await self._run_git(
                source_repo_path,
                "worktree",
                "add",
                "--detach",
                str(repo_path),
                base_ref,
            )
        return ThreadWorkspaceContext(
            workspace_path=workspace_path,
            repo_path=repo_path,
            source_repo_path=source_repo_path,
            base_ref=base_ref,
        )

    def _validate_worktree_path(self, worktree_path: Path, source_repo_path: Path) -> None:
        resolved_worktree = worktree_path.resolve(strict=False)
        resolved_source = source_repo_path.resolve(strict=False)
        resolved_root = self.settings.worktree_root.resolve(strict=False)
        try:
            resolved_worktree.relative_to(resolved_root)
        except ValueError:
            raise UnsafeWorktreePathError("Worktree path must stay under PANGI_WORKTREE_ROOT") from None
        if resolved_worktree == resolved_source:
            raise UnsafeWorktreePathError("Worktree path must not be the source repo path")

    async def _ensure_git_repo(self, path: Path) -> None:
        result = await self._run_git(path, "rev-parse", "--is-inside-work-tree")
        if result.stdout.strip() != "true":
            raise GitWorktreeError(f"Path is not a git work tree: {path}")

    async def _run_git(self, cwd: Path, *args: str) -> "_CommandResult":
        command = (self.git_binary, "-C", str(cwd), *args)
        process = await asyncio.create_subprocess_exec(
            *command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout_bytes, stderr_bytes = await asyncio.wait_for(
                process.communicate(),
                timeout=self.command_timeout_seconds,
            )
        except TimeoutError:
            process.kill()
            stdout_bytes, stderr_bytes = await process.communicate()
            stderr = _decode(stderr_bytes) or "Git command timed out"
            raise GitCommandError(command, stderr) from None

        stdout = _decode(stdout_bytes)
        stderr = _decode(stderr_bytes)
        if process.returncode != 0:
            raise GitCommandError(command, stderr)
        return _CommandResult(command=command, stdout=stdout, stderr=stderr)


@dataclass(frozen=True)
class _CommandResult:
    command: tuple[str, ...]
    stdout: str
    stderr: str


def _decode(value: bytes) -> str:
    return value.decode("utf-8", errors="replace")


def get_worktree_manager() -> GitWorktreeManager:
    return GitWorktreeManager(settings=get_settings())
