from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path

from pangi.config import get_settings
from pangi.usecase.ports import ChatResponder


DEFAULT_CODEX_COMMAND = ("codex",)


class CodexChatError(RuntimeError):
    pass


@dataclass(frozen=True)
class CodexChatResponder:
    command_prefix: tuple[str, ...] = DEFAULT_CODEX_COMMAND

    async def respond(self, *, text: str, user_id: str, channel_id: str, thread_ts: str) -> str:
        settings = get_settings()
        workspace = settings.chat_workspace_root
        if workspace is None:
            raise CodexChatError("PANGI_CHAT_WORKSPACE_ROOT is not configured")
        workspace.mkdir(parents=True, exist_ok=True)

        prompt = _build_chat_prompt(text)
        command = (
            *self.command_prefix,
            "exec",
            "-C",
            str(workspace),
            "--skip-git-repo-check",
            "--sandbox",
            "read-only",
            prompt,
        )
        try:
            process = await asyncio.create_subprocess_exec(
                *command,
                cwd=str(workspace),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except FileNotFoundError as error:
            raise CodexChatError("Codex command not found") from error

        try:
            stdout_bytes, stderr_bytes = await asyncio.wait_for(
                process.communicate(),
                timeout=settings.chat_timeout_seconds,
            )
        except TimeoutError as error:
            process.terminate()
            try:
                await asyncio.wait_for(process.wait(), timeout=2)
            except TimeoutError:
                process.kill()
                await process.wait()
            raise CodexChatError("Codex chat timed out") from error

        stdout = stdout_bytes.decode("utf-8", errors="replace")
        stderr = stderr_bytes.decode("utf-8", errors="replace")
        if process.returncode != 0:
            detail = stderr.strip() or stdout.strip() or f"exit code {process.returncode}"
            raise CodexChatError(f"Codex chat failed: {detail}")
        return stdout.strip()


def _build_chat_prompt(text: str) -> str:
    return (
        "당신은 PopPang 팀 Slack AI 동료 팡이입니다. "
        "repo를 직접 읽지 않는 일반 대화 모드입니다. "
        "외부 웹 검색을 하지 말고, 사용자가 준 내용과 일반 지식만으로 답하세요. "
        "답변은 한국어로 짧고 자연스럽게 작성하세요.\n\n"
        f"사용자 메시지:\n{text}"
    )


_chat_responder: ChatResponder | None = None


def get_chat_responder() -> ChatResponder:
    global _chat_responder
    if _chat_responder is None:
        _chat_responder = CodexChatResponder()
    return _chat_responder


def set_chat_responder(chat_responder: ChatResponder | None) -> None:
    global _chat_responder
    _chat_responder = chat_responder
