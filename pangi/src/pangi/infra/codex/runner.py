from __future__ import annotations

import asyncio
import json
from contextlib import suppress
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
import tempfile

from pangi.infra.codex.options import append_model_reasoning_effort
from pangi.usecase.ports import CodexExecutionResult


CODEX_READ_ONLY_SANDBOX = "read-only"
TERMINATE_GRACE_SECONDS = 5
OUTPUT_FILE_PREFIX = "pangi-codex-last-message-"


class CodexRunnerError(RuntimeError):
    pass


@dataclass(frozen=True)
class CodexExecRunner:
    command_prefix: tuple[str, ...] = ("codex", "exec")
    model: str | None = None
    reasoning_effort: str | None = None

    async def run_read_only(
        self,
        *,
        workspace_path: Path,
        prompt: str,
        timeout_seconds: float,
        resume_session_id: str | None = None,
    ) -> CodexExecutionResult:
        if not workspace_path.is_dir():
            raise CodexRunnerError(f"Workspace path does not exist: {workspace_path}")
        if timeout_seconds <= 0:
            raise CodexRunnerError("timeout_seconds must be positive")

        with tempfile.NamedTemporaryFile(
            prefix=OUTPUT_FILE_PREFIX,
            suffix=".md",
            dir=workspace_path,
            delete=False,
        ) as output_file:
            output_path = Path(output_file.name)
        command = self._build_command(
            workspace_path=workspace_path,
            output_path=output_path,
            prompt=prompt,
            resume_session_id=resume_session_id,
        )
        started_at = _utc_now()
        try:
            process = await asyncio.create_subprocess_exec(
                *command,
                cwd=str(workspace_path),
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
            with suppress(ProcessLookupError):
                process.terminate()
            try:
                stdout_bytes, stderr_bytes = await asyncio.wait_for(
                    process.communicate(),
                    timeout=TERMINATE_GRACE_SECONDS,
                )
            except TimeoutError:
                with suppress(ProcessLookupError):
                    process.kill()
                stdout_bytes, stderr_bytes = await process.communicate()
        finished_at = _utc_now()

        stdout_text = _decode(stdout_bytes)
        stderr_text = _decode(stderr_bytes)
        final_message = _read_output_last_message(output_path)
        diagnostics = _build_diagnostics(stdout_text, stderr_text)
        return CodexExecutionResult(
            command=tuple(command),
            stdout=final_message,
            stderr=diagnostics,
            exit_code=process.returncode,
            timed_out=timed_out,
            codex_session_id=_extract_codex_session_id(stdout_text) or resume_session_id,
            workspace_path=str(workspace_path),
            started_at=started_at,
            finished_at=finished_at,
        )

    async def archive_session(self, *, codex_session_id: str) -> None:
        command = ("codex", "archive", codex_session_id)
        try:
            process = await asyncio.create_subprocess_exec(
                *command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except FileNotFoundError as error:
            raise CodexRunnerError("Codex command not found: codex") from error

        stdout_bytes, stderr_bytes = await process.communicate()
        if process.returncode != 0:
            detail = _decode(stderr_bytes).strip() or _decode(stdout_bytes).strip() or "unknown error"
            raise CodexRunnerError(f"Failed to archive Codex session {codex_session_id}: {detail}")

    def _build_command(
        self,
        *,
        workspace_path: Path,
        output_path: Path,
        prompt: str,
        resume_session_id: str | None,
    ) -> list[str]:
        if resume_session_id:
            command = [
                *self.command_prefix,
                "resume",
                resume_session_id,
                "--json",
                "--output-last-message",
                str(output_path),
            ]
        else:
            command = [
                *self.command_prefix,
                "-C",
                str(workspace_path),
                "--skip-git-repo-check",
                "--sandbox",
                CODEX_READ_ONLY_SANDBOX,
                "--json",
                "--output-last-message",
                str(output_path),
            ]
        append_model_reasoning_effort(command, self.reasoning_effort)
        if self.model:
            command.extend(("--model", self.model))
        command.append(prompt)
        return command


def _decode(value: bytes) -> str:
    return value.decode("utf-8", errors="replace")


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _read_output_last_message(path: Path) -> str:
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return ""
    finally:
        with suppress(FileNotFoundError):
            path.unlink()
    return text


def _build_diagnostics(stdout_text: str, stderr_text: str) -> str:
    diagnostic_lines: list[str] = []
    diagnostic_lines.extend(_non_json_lines(stdout_text))
    diagnostic_lines.extend(line for line in stderr_text.splitlines() if line.strip())
    return "\n".join(diagnostic_lines).strip()


def _non_json_lines(output: str) -> list[str]:
    lines: list[str] = []
    for raw_line in output.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        try:
            json.loads(line)
        except json.JSONDecodeError:
            lines.append(raw_line)
    return lines


def _extract_codex_session_id(output: str) -> str | None:
    for raw_line in output.splitlines():
        line = raw_line.strip()
        if not line.startswith("{"):
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if event.get("type") == "thread.started" and isinstance(event.get("thread_id"), str):
            return event["thread_id"]
    return None
