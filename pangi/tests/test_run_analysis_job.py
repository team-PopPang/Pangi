import asyncio
from pathlib import Path

import pytest

from pangi.domain import JobType
from pangi.repository import SQLiteJobRepository
from pangi.usecase.ports import CodexExecutionResult, WorktreeContext
from pangi.usecase.run_analysis_job import AnalysisJobFailed, AnalysisJobTimedOut, RunAnalysisJobUseCase


class FakeWorktreeManager:
    def __init__(self, path: Path) -> None:
        self.path = path

    async def prepare_read_only_worktree(self, *, job_id: str, repo_key: str) -> WorktreeContext:
        return WorktreeContext(
            path=self.path,
            source_repo_path=self.path.parent / "source",
            base_ref="origin/develop",
        )


class FakeCodexRunner:
    def __init__(self, result: CodexExecutionResult) -> None:
        self.result = result
        self.prompts = []

    async def run_read_only(self, *, worktree_path: Path, prompt: str, timeout_seconds: float):
        self.prompts.append(prompt)
        return self.result


class FakeSlackNotifier:
    def __init__(self) -> None:
        self.messages = []

    async def post_message(self, *, channel_id: str, text: str, thread_ts: str | None = None) -> None:
        self.messages.append((channel_id, thread_ts, text))

    async def add_reaction(self, *, channel_id: str, message_ts: str, name: str) -> None:
        raise AssertionError("run analysis should not add reactions")


def make_job(repository: SQLiteJobRepository):
    thread = repository.get_or_create_thread(team_id="T123", channel_id="C123", thread_ts="171.1")
    return repository.create_job(
        event_id="Ev123",
        slack_thread=thread,
        requester_user_id="U123",
        prompt="구조 분석해줘",
        job_type=JobType.ANALYZE,
        repo_key="PopPang-iOS",
    )


def test_run_analysis_job_records_result_and_posts_success(tmp_path):
    async def scenario():
        repository = SQLiteJobRepository(tmp_path / "pangi.sqlite3")
        job = make_job(repository)
        worktree_path = tmp_path / "worktree"
        worktree_path.mkdir()
        runner = FakeCodexRunner(
            CodexExecutionResult(
                command=("codex", "exec", "prompt"),
                stdout="분석 완료 sk-testtoken",
                stderr="",
                exit_code=0,
            )
        )
        slack = FakeSlackNotifier()
        use_case = RunAnalysisJobUseCase(
            repository=repository,
            worktree_manager=FakeWorktreeManager(worktree_path),
            codex_runner=runner,
            slack_notifier=slack,
            timeout_seconds=1,
        )

        await use_case.execute(job)

        updated = repository.get_job(job.id)
        assert updated.worktree_path == str(worktree_path)
        assert updated.stdout == "분석 완료 [REDACTED]"
        assert "[REDACTED]" in slack.messages[-1][2]
        assert "구조 분석해줘" in runner.prompts[0]
        run = repository.list_codex_runs()[0]
        assert run.mode == "read-only"
        assert run.command == '["codex", "exec", "{prompt}"]'

    asyncio.run(scenario())


def test_run_analysis_job_posts_failure_and_raises_on_nonzero_exit(tmp_path):
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
                    stdout="",
                    stderr="boom",
                    exit_code=2,
                )
            ),
            slack_notifier=slack,
            timeout_seconds=1,
        )

        with pytest.raises(AnalysisJobFailed):
            await use_case.execute(job)

        assert "실패했습니다" in slack.messages[-1][2]
        assert repository.get_job(job.id).error_message.startswith("Codex exited with code 2")

    asyncio.run(scenario())


def test_run_analysis_job_posts_timeout_and_raises(tmp_path):
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
                    stdout="",
                    stderr="",
                    exit_code=-15,
                    timed_out=True,
                )
            ),
            slack_notifier=slack,
            timeout_seconds=1,
        )

        with pytest.raises(AnalysisJobTimedOut):
            await use_case.execute(job)

        assert "시간이 초과" in slack.messages[-1][2]
        assert repository.list_codex_runs()[0].timed_out is True

    asyncio.run(scenario())
