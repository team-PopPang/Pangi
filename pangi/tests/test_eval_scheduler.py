import asyncio
from datetime import datetime, timezone

from pangi.domain import EvalRunStatus
from pangi.evaluations.models import EvalCase, EvalCaseResult, EvalExecutionResult, EvalRunResult
from pangi.evaluations.operations import EvalSuiteRun
from pangi.evaluations.scheduler import InProcessEvalScheduler
from pangi.repository import SQLiteJobRepository
from pangi.usecase.request_decision import RequestClassification


class FakeSlackNotifier:
    def __init__(self):
        self.messages = []

    async def post_message(self, *, channel_id: str, text: str, thread_ts: str | None = None) -> str | None:
        self.messages.append({"channel_id": channel_id, "text": text, "thread_ts": thread_ts})
        return "1710000000.000001"

    async def add_reaction(self, *, channel_id: str, message_ts: str, name: str) -> None:
        return None

    async def remove_reaction(self, *, channel_id: str, message_ts: str, name: str) -> None:
        return None


def test_eval_scheduler_run_once_persists_bundled_suite(tmp_path):
    async def scenario():
        repository = SQLiteJobRepository(tmp_path / "pangi.sqlite3")
        scheduler = InProcessEvalScheduler(repository=repository, interval_seconds=999)

        suite_run = await scheduler.run_once()

        assert suite_run.result.passed is True
        assert repository.list_eval_runs(limit=10)[0].suite == "scheduled"
        assert repository.list_eval_case_results(limit=200)

    asyncio.run(scenario())


def test_eval_scheduler_posts_failure_alert(monkeypatch, tmp_path):
    async def scenario():
        repository = SQLiteJobRepository(tmp_path / "pangi.sqlite3")
        now = datetime(2026, 1, 1, tzinfo=timezone.utc)
        persisted_run = repository.create_eval_run(
            suite="scheduled",
            mode="deterministic",
            status=EvalRunStatus.FAILED,
            total_count=1,
            passed_count=0,
            failed_count=1,
            prompt_fingerprint="prompt",
            model_fingerprint="model",
            provider_fingerprint="provider",
            started_at=now,
            finished_at=now,
        )
        case = EvalCase(id="failing_case", name="failing case", input="안녕", suite="unit")
        execution = EvalExecutionResult(
            case=case,
            classification=RequestClassification.CODEX_CHAT,
            job_id=None,
            job_repo_key=None,
            trace=(),
            slack_messages=(),
        )
        failed_result = EvalCaseResult(case=case, execution=execution, passed=False, failures=("boom",))

        async def fake_run_eval_suite(**_kwargs):
            return EvalSuiteRun(result=EvalRunResult(results=(failed_result,)), persisted_run=persisted_run)

        monkeypatch.setattr("pangi.evaluations.scheduler.run_eval_suite", fake_run_eval_suite)
        slack = FakeSlackNotifier()
        scheduler = InProcessEvalScheduler(
            repository=repository,
            interval_seconds=999,
            slack_notifier=slack,
            alert_channel_id="C-EVAL",
        )

        await scheduler.run_once()

        assert slack.messages == [
            {
                "channel_id": "C-EVAL",
                "text": (
                    "Pangi Eval 실패\n"
                    f"- run_id: {persisted_run.id}\n"
                    "- passed: 0/1\n"
                    "- failed_cases: failing_case"
                ),
                "thread_ts": None,
            }
        ]

    asyncio.run(scenario())
