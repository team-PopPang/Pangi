import asyncio
from datetime import datetime, timedelta, timezone

from pangi.config import clear_settings_cache
from pangi.domain import ScheduleRunStatus, ScheduleType
from pangi.infra.scheduler import ScheduledTaskRunner
from pangi.repository import SQLiteJobRepository
from pangi.usecase.request_decision import ClassifiedRequest, RequestClassification


class FakeQueue:
    def __init__(self):
        self.job_ids = []

    async def enqueue(self, job_id: str) -> None:
        self.job_ids.append(job_id)


class FakeSlack:
    def __init__(self):
        self.messages = []
        self.reactions = []
        self.removed_reactions = []
        self._message_index = 0

    async def post_message(self, *, channel_id: str, text: str, thread_ts: str | None = None) -> str:
        self._message_index += 1
        ts = f"1710000000.{self._message_index:06d}"
        self.messages.append({"channel_id": channel_id, "thread_ts": thread_ts, "text": text, "ts": ts})
        return ts

    async def add_reaction(self, *, channel_id: str, message_ts: str, name: str) -> None:
        self.reactions.append({"channel_id": channel_id, "message_ts": message_ts, "name": name})

    async def remove_reaction(self, *, channel_id: str, message_ts: str, name: str) -> None:
        self.removed_reactions.append({"channel_id": channel_id, "message_ts": message_ts, "name": name})


class FakeOrchestrator:
    def __init__(self, decision):
        self.decision = decision

    async def decide(self, *, text: str, allowed_repo_keys: tuple[str, ...], thread_context: str = ""):
        return self.decision


class FakeChatResponder:
    async def respond(
        self,
        *,
        slack_thread,
        text: str,
        user_id: str,
        channel_id: str,
        thread_ts: str,
    ) -> str:
        return f"chat: {text}"


def configure_settings(monkeypatch, tmp_path):
    source_root = tmp_path / "repos"
    source_root.mkdir()
    (source_root / "PopPang-iOS").mkdir()
    worktree_root = tmp_path / "worktrees"
    worktree_root.mkdir()
    monkeypatch.setenv("SLACK_SIGNING_SECRET", "secret")
    monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxb-test")
    monkeypatch.setenv("SLACK_ALLOWED_USER_IDS", "U123")
    monkeypatch.setenv("SLACK_ALLOWED_CHANNEL_IDS", "C123")
    monkeypatch.setenv("PANGI_SOURCE_REPO_ROOT", str(source_root))
    monkeypatch.setenv("PANGI_WORKTREE_ROOT", str(worktree_root))
    clear_settings_cache()


def test_scheduled_runner_submits_chat_request(monkeypatch, tmp_path):
    async def scenario():
        configure_settings(monkeypatch, tmp_path)
        repository = SQLiteJobRepository(tmp_path / "pangi.sqlite3")
        now = datetime(2026, 1, 1, 0, 0, tzinfo=timezone.utc)
        repository.create_scheduled_task(
            name="morning note",
            team_id="T123",
            channel_id="C123",
            requester_user_id="U123",
            prompt="오늘 업무 요약해줘",
            schedule_type=ScheduleType.DAILY,
            timezone="Asia/Seoul",
            time_of_day="09:00",
            next_run_at=now - timedelta(seconds=1),
        )
        slack = FakeSlack()
        runner = ScheduledTaskRunner(
            repository=repository,
            job_queue=FakeQueue(),
            slack_notifier=slack,
            request_orchestrator=FakeOrchestrator(
                ClassifiedRequest(kind=RequestClassification.CODEX_CHAT, should_create_job=False)
            ),
            chat_responder=FakeChatResponder(),
        )

        claimed = await runner.run_due(now=now)

        runs = repository.list_scheduled_task_runs(limit=10)
        assert claimed == 1
        assert runs[0].status == ScheduleRunStatus.SUCCEEDED
        assert runs[0].classification == RequestClassification.CODEX_CHAT.value
        assert runs[0].slack_thread_ts == "1710000000.000001"
        assert slack.messages[0]["thread_ts"] is None
        assert slack.messages[-1]["thread_ts"] == "1710000000.000001"

    asyncio.run(scenario())


def test_scheduled_runner_creates_repo_analysis_job(monkeypatch, tmp_path):
    async def scenario():
        configure_settings(monkeypatch, tmp_path)
        repository = SQLiteJobRepository(tmp_path / "pangi.sqlite3")
        queue = FakeQueue()
        now = datetime(2026, 1, 1, 0, 0, tzinfo=timezone.utc)
        repository.create_scheduled_task(
            name="repo report",
            team_id="T123",
            channel_id="C123",
            requester_user_id="U123",
            prompt="PopPang-iOS 구조 분석해줘",
            schedule_type=ScheduleType.DAILY,
            timezone="Asia/Seoul",
            time_of_day="09:00",
            next_run_at=now - timedelta(seconds=1),
        )
        runner = ScheduledTaskRunner(
            repository=repository,
            job_queue=queue,
            slack_notifier=FakeSlack(),
            request_orchestrator=FakeOrchestrator(
                ClassifiedRequest(
                    kind=RequestClassification.REPO_ANALYSIS,
                    should_create_job=True,
                    repo_key="PopPang-iOS",
                )
            ),
            chat_responder=FakeChatResponder(),
        )

        claimed = await runner.run_due(now=now)

        runs = repository.list_scheduled_task_runs(limit=10)
        job = repository.get_job(queue.job_ids[0])
        assert claimed == 1
        assert job is not None
        assert job.event_id == runs[0].event_id
        assert job.slack_message_ts == runs[0].slack_thread_ts
        assert runs[0].status == ScheduleRunStatus.SUBMITTED
        assert runs[0].job_id == job.id

    asyncio.run(scenario())
