from __future__ import annotations

from dataclasses import dataclass
import shutil

from pangi.config import get_settings
from pangi.domain.models import SlackThread
from pangi.infra.codex.runner import CodexExecRunner
from pangi.prompts.loader import load_prompt
from pangi.repository import JobRepository, get_job_repository
from pangi.usecase.codex_session import CodexSessionService
from pangi.usecase.ports import ChatResponder


DEFAULT_CODEX_COMMAND = ("codex", "exec")


class CodexChatError(RuntimeError):
    pass


@dataclass(frozen=True)
class CodexChatResponder:
    command_prefix: tuple[str, ...] = DEFAULT_CODEX_COMMAND
    model: str | None = None
    reasoning_effort: str | None = None
    repository: JobRepository | None = None

    async def respond(
        self,
        *,
        slack_thread: SlackThread,
        text: str,
        user_id: str,
        channel_id: str,
        thread_ts: str,
    ) -> str:
        settings = get_settings()
        workspace = settings.thread_workspace_path(slack_thread.id)
        repository = self.repository or get_job_repository()
        runner = CodexExecRunner(
            command_prefix=self.command_prefix,
            model=self.model or settings.chat_model,
            reasoning_effort=self.reasoning_effort or settings.chat_reasoning_effort,
        )
        session_service = CodexSessionService(
            repository=repository,
            codex_runner=runner,
            idle_timeout_seconds=settings.codex_session_idle_timeout_seconds,
        )
        prepared = await session_service.prepare_for_turn(slack_thread.id)
        if prepared.expired_previous_session and workspace.exists():
            shutil.rmtree(workspace)
        workspace.mkdir(parents=True, exist_ok=True)

        prompt = _build_chat_prompt(text)
        result = await runner.run_read_only(
            workspace_path=workspace,
            prompt=prompt,
            timeout_seconds=settings.chat_timeout_seconds,
            resume_session_id=prepared.active_session.codex_thread_id if prepared.active_session else None,
        )
        if result.timed_out:
            raise CodexChatError("Codex chat timed out")
        if result.exit_code != 0:
            detail = result.stderr.strip() or result.stdout.strip() or f"exit code {result.exit_code}"
            raise CodexChatError(f"Codex chat failed: {detail}")
        session_service.record_turn_result(
            slack_thread_id=slack_thread.id,
            workspace_path=workspace,
            existing_session=prepared.active_session,
            result=result,
        )
        response = result.stdout.strip()
        if prepared.expired_previous_session:
            notice = "이전 Codex session이 1시간 이상 비활성이라 새 session으로 다시 시작했습니다."
            return f"{notice}\n\n{response}".strip()
        return response


def _build_chat_prompt(text: str) -> str:
    agent_prompt = load_prompt("pangi_agent.md")
    chat_prompt = load_prompt("chat.md")
    return f"""\
{agent_prompt}

{chat_prompt}
사용자 메시지:
{text}
"""


_chat_responder: ChatResponder | None = None


def get_chat_responder() -> ChatResponder:
    global _chat_responder
    if _chat_responder is None:
        _chat_responder = CodexChatResponder()
    return _chat_responder


def set_chat_responder(chat_responder: ChatResponder | None) -> None:
    global _chat_responder
    _chat_responder = chat_responder
