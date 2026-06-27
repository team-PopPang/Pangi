import asyncio
from pathlib import Path

import pytest

from pangi.domain import JobType, ThreadMessageRole
from pangi.repository import SQLiteJobRepository
from pangi.usecase.ports import CodexExecutionResult, ThreadWorkspaceContext
from pangi.usecase.run_analysis_job import AnalysisJobFailed, AnalysisJobTimedOut, RunAnalysisJobUseCase


class FakeWorktreeManager:
    def __init__(self, path: Path) -> None:
        self.path = path

    async def prepare_thread_repo_workspace(self, *, slack_thread_id: str, repo_key: str) -> ThreadWorkspaceContext:
        return ThreadWorkspaceContext(
            workspace_path=self.path.parent / "thread-workspace",
            repo_path=self.path,
            source_repo_path=self.path.parent / "source",
            base_ref="origin/develop",
        )

    async def cleanup_thread_workspace(self, *, slack_thread_id: str) -> None:
        return None


class FakeCodexRunner:
    def __init__(self, result: CodexExecutionResult) -> None:
        self.result = result
        self.prompts = []

    async def run_read_only(
        self,
        *,
        workspace_path: Path,
        prompt: str,
        timeout_seconds: float,
        resume_session_id: str | None = None,
    ):
        self.prompts.append(prompt)
        return self.result

    async def archive_session(self, *, codex_session_id: str) -> None:
        return None


class FakeSlackNotifier:
    def __init__(self) -> None:
        self.messages = []
        self.reactions = []
        self.removed_reactions = []

    async def post_message(self, *, channel_id: str, text: str, thread_ts: str | None = None) -> None:
        self.messages.append((channel_id, thread_ts, text))

    async def add_reaction(self, *, channel_id: str, message_ts: str, name: str) -> None:
        self.reactions.append((channel_id, message_ts, name))

    async def remove_reaction(self, *, channel_id: str, message_ts: str, name: str) -> None:
        self.removed_reactions.append((channel_id, message_ts, name))


def make_job(repository: SQLiteJobRepository, *, slack_message_ts: str | None = None):
    thread = repository.get_or_create_thread(team_id="T123", channel_id="C123", thread_ts="171.1")
    return repository.create_job(
        event_id="Ev123",
        slack_thread=thread,
        codex_session_id=None,
        requester_user_id="U123",
        prompt="구조 분석해줘",
        slack_message_ts=slack_message_ts,
        job_type=JobType.ANALYZE,
        repo_key="PopPang-iOS",
    )


def test_run_analysis_job_records_result_and_posts_success(tmp_path):
    async def scenario():
        repository = SQLiteJobRepository(tmp_path / "pangi.sqlite3")
        thread = repository.get_or_create_thread(team_id="T123", channel_id="C123", thread_ts="171.1")
        repository.append_thread_message(
            slack_thread_id=thread.id,
            role=ThreadMessageRole.USER,
            text="이전에는 로그인 흐름을 봤어",
            event_id="EvPrev",
        )
        repository.append_thread_message(
            slack_thread_id=thread.id,
            role=ThreadMessageRole.ASSISTANT,
            text="로그인 흐름은 AuthViewController에서 시작됩니다.",
        )
        job = make_job(repository, slack_message_ts="171.2")
        worktree_path = tmp_path / "worktree"
        worktree_path.mkdir()
        runner = FakeCodexRunner(
            CodexExecutionResult(
                command=("codex", "exec", "prompt"),
                stdout="분석 완료 sk-testtoken",
                stderr="",
                exit_code=0,
                codex_session_id="codex-thread-123",
                workspace_path=str(tmp_path / "thread-workspace"),
            )
        )
        slack = FakeSlackNotifier()
        use_case = RunAnalysisJobUseCase(
            repository=repository,
            worktree_manager=FakeWorktreeManager(worktree_path),
            codex_runner=runner,
            slack_notifier=slack,
            timeout_seconds=1,
            session_idle_timeout_seconds=3600,
        )

        await use_case.execute(job)

        updated = repository.get_job(job.id)
        assert updated.worktree_path == str(worktree_path)
        assert updated.codex_session_id is not None
        assert updated.stdout == "분석 완료 [REDACTED]"
        assert "[REDACTED]" in slack.messages[-1][2]
        assert slack.removed_reactions == [("C123", "171.2", "eyes")]
        assert slack.reactions == [("C123", "171.2", "white_check_mark")]
        assert "팡이 공통 스타일" in runner.prompts[0]
        assert "Read-only 코드 분석 모드" in runner.prompts[0]
        assert "repo_path:" in runner.prompts[0]
        assert "구조 분석해줘" in runner.prompts[0]
        messages = repository.list_thread_messages(job.slack_thread_id, limit=10)
        assert messages[-1].role == ThreadMessageRole.ASSISTANT
        assert "read-only 분석을 완료" in messages[-1].text
        assert "codex_session_id: codex-thread-123" in messages[-1].text
        run = repository.list_codex_runs()[0]
        assert run.mode == "read-only"
        assert run.command == '["codex", "exec", "{prompt}"]'
        assert repository.get_active_codex_session(job.slack_thread_id).codex_thread_id == "codex-thread-123"

    asyncio.run(scenario())


def test_run_analysis_job_posts_failure_and_raises_on_nonzero_exit(tmp_path):
    async def scenario():
        repository = SQLiteJobRepository(tmp_path / "pangi.sqlite3")
        job = make_job(repository, slack_message_ts="171.2")
        worktree_path = tmp_path / "worktree"
        worktree_path.mkdir()
        slack = FakeSlackNotifier()
        use_case = RunAnalysisJobUseCase(
            repository=repository,
            worktree_manager=FakeWorktreeManager(worktree_path),
            codex_runner=FakeCodexRunner(
                CodexExecutionResult(
                    command=("codex", "exec", "prompt"),
                    stdout="",
                    stderr="boom",
                    exit_code=2,
                    codex_session_id="codex-thread-123",
                )
            ),
            slack_notifier=slack,
            timeout_seconds=1,
            session_idle_timeout_seconds=3600,
        )

        with pytest.raises(AnalysisJobFailed):
            await use_case.execute(job)

        assert "⚠️ Pangi Error" in slack.messages[-1][2]
        assert "stage: repo_analysis" in slack.messages[-1][2]
        assert "detail:\nCodex exited with code 2. boom" in slack.messages[-1][2]
        assert "job_id: " in slack.messages[-1][2]
        assert slack.removed_reactions == [("C123", "171.2", "eyes")]
        assert slack.reactions == [("C123", "171.2", "x")]
        assert repository.get_job(job.id).error_message.startswith("Codex exited with code 2")

    asyncio.run(scenario())


def test_run_analysis_job_posts_timeout_and_raises(tmp_path):
    async def scenario():
        repository = SQLiteJobRepository(tmp_path / "pangi.sqlite3")
        job = make_job(repository, slack_message_ts="171.2")
        worktree_path = tmp_path / "worktree"
        worktree_path.mkdir()
        slack = FakeSlackNotifier()
        use_case = RunAnalysisJobUseCase(
            repository=repository,
            worktree_manager=FakeWorktreeManager(worktree_path),
            codex_runner=FakeCodexRunner(
                CodexExecutionResult(
                    command=("codex", "exec", "prompt"),
                    stdout="",
                    stderr="",
                    exit_code=-15,
                    timed_out=True,
                    codex_session_id="codex-thread-123",
                )
            ),
            slack_notifier=slack,
            timeout_seconds=1,
            session_idle_timeout_seconds=3600,
        )

        with pytest.raises(AnalysisJobTimedOut):
            await use_case.execute(job)

        assert "⚠️ Pangi Error" in slack.messages[-1][2]
        assert "kind: timeout" in slack.messages[-1][2]
        assert "detail:\nCodex read-only analysis timed out" in slack.messages[-1][2]
        assert slack.removed_reactions == [("C123", "171.2", "eyes")]
        assert slack.reactions == [("C123", "171.2", "x")]
        assert repository.list_codex_runs()[0].timed_out is True

    asyncio.run(scenario())


def test_run_analysis_job_skips_reaction_without_original_message_ts(tmp_path):
    async def scenario():
        repository = SQLiteJobRepository(tmp_path / "pangi.sqlite3")
        job = make_job(repository)
        worktree_path = tmp_path / "worktree"
        worktree_path.mkdir()
        slack = FakeSlackNotifier()
        use_case = RunAnalysisJobUseCase(
            repository=repository,
            worktree_manager=FakeWorktreeManager(worktree_path),
            codex_runner=FakeCodexRunner(
                CodexExecutionResult(
                    command=("codex", "exec", "prompt"),
                    stdout="분석 완료",
                    stderr="",
                    exit_code=0,
                    codex_session_id="codex-thread-123",
                )
            ),
            slack_notifier=slack,
            timeout_seconds=1,
            session_idle_timeout_seconds=3600,
        )

        await use_case.execute(job)

        assert slack.messages
        assert slack.removed_reactions == []
        assert slack.reactions == []

    asyncio.run(scenario())
