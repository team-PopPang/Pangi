from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import timedelta
import logging
from pathlib import Path

from pangi.domain.models import CodexSession, CodexSessionStatus, utc_now
from pangi.repository import JobRepository
from pangi.usecase.ports import CodexExecutionResult, CodexRunner


logger = logging.getLogger(__name__)

CleanupThreadWorkspace = Callable[[str], Awaitable[None]]


class CodexSessionError(RuntimeError):
    pass


@dataclass(frozen=True)
class PreparedCodexSession:
    active_session: CodexSession | None
    expired_previous_session: bool = False


@dataclass(frozen=True)
class CodexSessionService:
    repository: JobRepository
    codex_runner: CodexRunner
    idle_timeout_seconds: int

    async def prepare_for_turn(self, slack_thread_id: str) -> PreparedCodexSession:
        active_session = self.repository.get_active_codex_session(slack_thread_id)
        if active_session is None:
            return PreparedCodexSession(active_session=None, expired_previous_session=False)
        if active_session.expires_at > utc_now():
            return PreparedCodexSession(active_session=active_session, expired_previous_session=False)
        await self._archive_session(active_session)
        return PreparedCodexSession(active_session=None, expired_previous_session=True)

    def record_turn_result(
        self,
        *,
        slack_thread_id: str,
        workspace_path: Path,
        existing_session: CodexSession | None,
        result: CodexExecutionResult,
    ) -> CodexSession:
        now = utc_now()
        expires_at = now + timedelta(seconds=self.idle_timeout_seconds)
        if existing_session is None:
            if not result.codex_session_id:
                raise CodexSessionError("Codex run did not return a session id for a new thread session")
            return self.repository.create_codex_session(
                slack_thread_id=slack_thread_id,
                codex_thread_id=result.codex_session_id,
                workspace_path=str(workspace_path),
                status=CodexSessionStatus.ACTIVE,
                last_used_at=now,
                expires_at=expires_at,
            )

        if result.codex_session_id and result.codex_session_id != existing_session.codex_thread_id:
            raise CodexSessionError("Codex returned a different session id while resuming an active thread session")
        return self.repository.update_codex_session_activity(
            existing_session.id,
            status=CodexSessionStatus.ACTIVE,
            last_used_at=now,
            expires_at=expires_at,
        )

    async def expire_due_sessions(self, *, cleanup_thread_workspace: CleanupThreadWorkspace | None = None) -> int:
        sessions = self.repository.list_expired_active_codex_sessions(now=utc_now(), limit=100)
        expired_count = 0
        for session in sessions:
            await self._archive_session(session)
            if cleanup_thread_workspace is not None:
                try:
                    await cleanup_thread_workspace(session.slack_thread_id)
                except Exception:
                    logger.exception("Failed to cleanup thread workspace for expired session %s", session.id)
            expired_count += 1
        return expired_count

    async def _archive_session(self, session: CodexSession) -> None:
        try:
            await self.codex_runner.archive_session(codex_session_id=session.codex_thread_id)
        except Exception:
            logger.exception("Failed to archive Codex session %s", session.codex_thread_id)
            self.repository.archive_codex_session(
                session.id,
                status=CodexSessionStatus.ARCHIVE_FAILED,
                archived_at=utc_now(),
            )
            return

        self.repository.archive_codex_session(
            session.id,
            status=CodexSessionStatus.ARCHIVED,
            archived_at=utc_now(),
        )
