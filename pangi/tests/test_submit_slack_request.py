import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from pangi.repository import SQLiteJobRepository  # noqa: E402
from pangi.usecase.classify_request import ClassifiedRequest, RequestClassification  # noqa: E402
from pangi.usecase.submit_slack_request import SubmitSlackRequestInput, SubmitSlackRequestUseCase  # noqa: E402


class FakeQueue:
    def __init__(self):
        self.job_ids = []

    async def enqueue(self, job_id: str) -> None:
        self.job_ids.append(job_id)


class FakeSlack:
    def __init__(self):
        self.messages = []
        self.reactions = []

    async def post_message(self, *, channel_id: str, text: str, thread_ts: str | None = None) -> None:
        self.messages.append({"channel_id": channel_id, "thread_ts": thread_ts, "text": text})

    async def add_reaction(self, *, channel_id: str, message_ts: str, name: str) -> None:
        self.reactions.append({"channel_id": channel_id, "message_ts": message_ts, "name": name})


class FakeOrchestrator:
    def __init__(self, decision):
        self.decision = decision

    async def decide(self, *, text: str, allowed_repo_keys: tuple[str, ...]):
        return self.decision


class FakeChatResponder:
    async def respond(self, *, text: str, user_id: str, channel_id: str, thread_ts: str) -> str:
        return f"답장: {text}"


def make_request(text: str = "안녕") -> SubmitSlackRequestInput:
    return SubmitSlackRequestInput(
        team_id="T123",
        channel_id="C123",
        user_id="U123",
        text=text,
        thread_ts="1710000000.000001",
        event_id="Ev123",
        message_ts="1710000000.000002",
    )


def test_codex_chat_posts_reply_without_repo_job(tmp_path):
    async def scenario():
        repository = SQLiteJobRepository(tmp_path / "pangi.sqlite3")
        queue = FakeQueue()
        slack = FakeSlack()
        tasks = []

        def collect_task(task):
            tasks.append(task)

        use_case = SubmitSlackRequestUseCase(
            repository=repository,
            job_queue=queue,
            slack_notifier=slack,
            request_orchestrator=FakeOrchestrator(
                ClassifiedRequest(
                    kind=RequestClassification.CODEX_CHAT,
                    should_create_job=False,
                )
            ),
            chat_responder=FakeChatResponder(),
            allowed_repo_keys=("PopPang-iOS",),
            background_runner=collect_task,
        )

        result = await use_case.execute(make_request())
        await asyncio.gather(*tasks)

        assert result.classification == RequestClassification.CODEX_CHAT
        assert result.job_id is None
        assert repository.list_jobs() == []
        assert queue.job_ids == []
        assert slack.reactions == [
            {
                "channel_id": "C123",
                "message_ts": "1710000000.000002",
                "name": "eyes",
            }
        ]
        assert slack.messages == [
            {
                "channel_id": "C123",
                "thread_ts": "1710000000.000001",
                "text": "답장: 안녕",
            }
        ]

    asyncio.run(scenario())


def test_needs_repo_posts_question_without_job(tmp_path):
    async def scenario():
        repository = SQLiteJobRepository(tmp_path / "pangi.sqlite3")
        queue = FakeQueue()
        slack = FakeSlack()
        use_case = SubmitSlackRequestUseCase(
            repository=repository,
            job_queue=queue,
            slack_notifier=slack,
            request_orchestrator=FakeOrchestrator(
                ClassifiedRequest(
                    kind=RequestClassification.NEEDS_REPO,
                    should_create_job=False,
                    reply_text="어느 repo를 볼까요?",
                )
            ),
            chat_responder=FakeChatResponder(),
            allowed_repo_keys=("PopPang-iOS",),
        )

        result = await use_case.execute(make_request("레포 분석해줘"))

        assert result.classification == RequestClassification.NEEDS_REPO
        assert result.job_id is None
        assert repository.list_jobs() == []
        assert queue.job_ids == []
        assert slack.reactions == []
        assert slack.messages[0]["text"] == "어느 repo를 볼까요?"

    asyncio.run(scenario())


def test_repo_analysis_creates_job_with_selected_repo(tmp_path):
    async def scenario():
        repository = SQLiteJobRepository(tmp_path / "pangi.sqlite3")
        queue = FakeQueue()
        slack = FakeSlack()
        use_case = SubmitSlackRequestUseCase(
            repository=repository,
            job_queue=queue,
            slack_notifier=slack,
            request_orchestrator=FakeOrchestrator(
                ClassifiedRequest(
                    kind=RequestClassification.REPO_ANALYSIS,
                    should_create_job=True,
                    repo_key="PopPang-iOS",
                )
            ),
            chat_responder=FakeChatResponder(),
            allowed_repo_keys=("PopPang-iOS",),
        )

        result = await use_case.execute(make_request("PopPang-iOS 구조 분석해줘"))

        assert result.classification == RequestClassification.REPO_ANALYSIS
        assert result.job_id is not None
        job = repository.get_job(result.job_id)
        assert job is not None
        assert job.repo_key == "PopPang-iOS"
        assert queue.job_ids == [result.job_id]

    asyncio.run(scenario())
