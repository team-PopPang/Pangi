from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Protocol

from pangi.domain.models import CodexSession, SlackThread
from pangi.usecase.git_context import GitContext, GitRepoCatalog
from pangi.usecase.notion_context import NotionContext
from pangi.usecase.request_decision import ClassifiedRequest


class SlackNotifier(Protocol):
    """usecase가 Slack thread에 상태와 결과를 알리기 위해 사용하는 포트.

    usecase는 구체적인 Slack Web API client가 아니라 이 프로토콜에만
    의존한다. 그래서 테스트에서는 fake를 주입할 수 있고, 나중에 HTTP
    구현이 바뀌어도 업무 흐름 코드는 그대로 유지할 수 있다.
    """

    async def post_message(self, *, channel_id: str, text: str, thread_ts: str | None = None) -> str | None:
        """지정한 Slack channel 또는 thread에 메시지를 보내고 Slack ts를 반환한다."""
        ...

    async def add_reaction(self, *, channel_id: str, message_ts: str, name: str) -> None:
        """지정한 Slack 메시지에 reaction emoji를 추가한다."""
        ...

    async def remove_reaction(self, *, channel_id: str, message_ts: str, name: str) -> None:
        """지정한 Slack 메시지에서 reaction emoji를 제거한다."""
        ...


class JobQueue(Protocol):
    """usecase가 긴 작업을 background job으로 예약하기 위해 사용하는 포트."""

    async def enqueue(self, job_id: str) -> None:
        """job id를 background 실행 대상으로 예약한다."""
        ...


class RequestOrchestrator(Protocol):
    """Slack 요청을 어떤 Pangi 흐름으로 처리할지 결정하는 포트."""

    async def decide(
        self,
        *,
        text: str,
        allowed_repo_keys: tuple[str, ...],
        thread_context: str = "",
    ) -> ClassifiedRequest:
        """요청 텍스트와 허용 repo 목록을 보고 실행 분기 결정을 반환한다."""
        ...


class ChatResponder(Protocol):
    """repo worktree 없이 일반 AI 대화 응답을 생성하는 포트."""

    async def respond(
        self,
        *,
        slack_thread: SlackThread,
        text: str,
        user_id: str,
        channel_id: str,
        thread_ts: str,
    ) -> str:
        """Slack 일반 대화 요청에 대한 답변 텍스트를 생성한다."""
        ...


class NotionContextProvider(Protocol):
    """usecase가 Notion 문서를 읽어 Codex prompt context로 만들기 위해 사용하는 포트."""

    async def fetch_context(self, *, text: str, user_id: str, channel_id: str, thread_ts: str) -> NotionContext:
        """Slack 요청에 필요한 Notion context를 read-only로 조회한다."""
        ...


class GitContextProvider(Protocol):
    """usecase가 Git MCP를 통해 GitHub/Git 맥락을 읽기 위해 사용하는 포트."""

    async def fetch_context(self, *, text: str, user_id: str, channel_id: str, thread_ts: str) -> GitContext:
        """Slack 요청에 필요한 Git context를 read-only로 조회한다."""
        ...

    async def fetch_repo_catalog(self, *, local_repo_keys: tuple[str, ...]) -> GitRepoCatalog:
        """Git MCP repo 목록과 로컬 source repo 목록을 비교해 반환한다."""
        ...


@dataclass(frozen=True)
class ThreadWorkspaceContext:
    workspace_path: Path
    repo_path: Path
    source_repo_path: Path
    base_ref: str


class WorktreeManager(Protocol):
    """usecase가 thread별 workspace와 repo checkout을 준비하기 위해 사용하는 포트."""

    async def prepare_thread_repo_workspace(
        self,
        *,
        slack_thread_id: str,
        repo_key: str,
    ) -> ThreadWorkspaceContext:
        """Slack thread와 repo key를 기준으로 thread workspace 내부 repo checkout을 준비한다."""
        ...

    async def cleanup_thread_workspace(self, *, slack_thread_id: str) -> None:
        """Slack thread의 workspace를 정리한다."""
        ...


@dataclass(frozen=True)
class CodexExecutionResult:
    command: tuple[str, ...]
    stdout: str
    stderr: str
    exit_code: int | None
    timed_out: bool = False
    codex_session_id: str | None = None
    workspace_path: str | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None


class CodexRunner(Protocol):
    """usecase가 Codex CLI를 read-only 모드로 실행하기 위해 사용하는 포트."""

    async def run_read_only(
        self,
        *,
        workspace_path: Path,
        prompt: str,
        timeout_seconds: float,
        resume_session_id: str | None = None,
    ) -> CodexExecutionResult:
        """지정한 workspace에서 Codex read-only 실행 또는 resume을 수행한다."""
        ...

    async def archive_session(self, *, codex_session_id: str) -> None:
        """저장된 Codex session을 archive한다."""
        ...
