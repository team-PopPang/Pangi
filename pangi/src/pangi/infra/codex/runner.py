from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from pangi.usecase.ports import CodexExecutionResult


CODEX_READ_ONLY_SANDBOX = "read-only"
TERMINATE_GRACE_SECONDS = 5


class CodexRunnerError(RuntimeError):
    pass


@dataclass(frozen=True)
class CodexExecRunner:
    command_prefix: tuple[str, ...] = ("codex", "exec")

    async def run_read_only(
        self,
        *,
        worktree_path: Path,
        prompt: str,
        timeout_seconds: float,
    ) -> CodexExecutionResult:
        if not worktree_path.is_dir():
            raise CodexRunnerError(f"Worktree path does not exist: {worktree_path}")
        if timeout_seconds <= 0:
            raise CodexRunnerError("timeout_seconds must be positive")

        command = (
            *self.command_prefix,
            "-C",
            str(worktree_path),
            "--sandbox",
            CODEX_READ_ONLY_SANDBOX,
            prompt,
        )
        started_at = _utc_now()
        try:
            process = await asyncio.create_subprocess_exec(
                *command,
                cwd=str(worktree_path),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except FileNotFoundError as error:
            raise CodexRunnerError(f"Codex command not found: {self.command_prefix[0]}") from error

        timed_out = False
        try:
            stdout_bytes, stderr_bytes = await asyncio.wait_for(
                process.communicate(),
                timeout=timeout_seconds,
            )
        except TimeoutError:
            timed_out = True
            process.terminate()
            try:
                stdout_bytes, stderr_bytes = await asyncio.wait_for(
                    process.communicate(),
                    timeout=TERMINATE_GRACE_SECONDS,
                )
            except TimeoutError:
                process.kill()
                stdout_bytes, stderr_bytes = await process.communicate()
        finished_at = _utc_now()

        return CodexExecutionResult(
            command=command,
            stdout=_decode(stdout_bytes),
            stderr=_decode(stderr_bytes),
            exit_code=process.returncode,
            timed_out=timed_out,
            started_at=started_at,
            finished_at=finished_at,
        )


def _decode(value: bytes) -> str:
    return value.decode("utf-8", errors="replace")


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)
