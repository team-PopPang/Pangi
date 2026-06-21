import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from pangi.repository import SQLiteJobRepository  # noqa: E402
from pangi.usecase.request_decision import ClassifiedRequest, RequestClassification  # noqa: E402
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
        self.removed_reactions = []

    async def post_message(self, *, channel_id: str, text: str, thread_ts: str | None = None) -> None:
        self.messages.append({"channel_id": channel_id, "thread_ts": thread_ts, "text": text})

    async def add_reaction(self, *, channel_id: str, message_ts: str, name: str) -> None:
        self.reactions.append({"channel_id": channel_id, "message_ts": message_ts, "name": name})

    async def remove_reaction(self, *, channel_id: str, message_ts: str, name: str) -> None:
        self.removed_reactions.append({"channel_id": channel_id, "message_ts": message_ts, "name": name})


class FakeOrchestrator:
    def __init__(self, decision):
        self.decision = decision

    async def decide(self, *, text: str, allowed_repo_keys: tuple[str, ...]):
        return self.decision


class FailingOrchestrator:
    async def decide(self, *, text: str, allowed_repo_keys: tuple[str, ...]):
        raise RuntimeError("classification boom")


class FakeChatResponder:
    async def respond(self, *, text: str, user_id: str, channel_id: str, thread_ts: str) -> str:
        return f"답장: {text}"


class MarkdownChatResponder:
    async def respond(self, *, text: str, user_id: str, channel_id: str, thread_ts: str) -> str:
        return "# 결론\n**강조** [문서](https://example.com)"


class FailingChatResponder:
    async def respond(self, *, text: str, user_id: str, channel_id: str, thread_ts: str) -> str:
        raise RuntimeError("boom")


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
            },
            {
                "channel_id": "C123",
                "message_ts": "1710000000.000002",
                "name": "white_check_mark",
            }
        ]
        assert slack.removed_reactions == [
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


def test_codex_chat_keeps_canonical_markdown_before_slack_adapter(tmp_path):
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
            chat_responder=MarkdownChatResponder(),
            allowed_repo_keys=("PopPang-iOS",),
            background_runner=collect_task,
        )

        await use_case.execute(make_request())
        await asyncio.gather(*tasks)

        assert slack.messages == [
            {
                "channel_id": "C123",
                "thread_ts": "1710000000.000001",
                "text": "# 결론\n**강조** [문서](https://example.com)",
            }
        ]

    asyncio.run(scenario())


def test_codex_chat_marks_failure_reaction_when_reply_generation_fails(tmp_path):
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
            chat_responder=FailingChatResponder(),
            allowed_repo_keys=("PopPang-iOS",),
            background_runner=collect_task,
        )

        result = await use_case.execute(make_request())
        await asyncio.gather(*tasks)

        assert result.classification == RequestClassification.CODEX_CHAT
        assert slack.messages == [
            {
                "channel_id": "C123",
                "thread_ts": "1710000000.000001",
                "text": "팡이 대화 응답 생성에 실패했습니다.",
            }
        ]
        assert slack.reactions == [
            {
                "channel_id": "C123",
                "message_ts": "1710000000.000002",
                "name": "eyes",
            },
            {
                "channel_id": "C123",
                "message_ts": "1710000000.000002",
                "name": "x",
            },
        ]
        assert slack.removed_reactions == [
            {
                "channel_id": "C123",
                "message_ts": "1710000000.000002",
                "name": "eyes",
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
        assert slack.reactions == [
            {
                "channel_id": "C123",
                "message_ts": "1710000000.000002",
                "name": "eyes",
            },
            {
                "channel_id": "C123",
                "message_ts": "1710000000.000002",
                "name": "white_check_mark",
            },
        ]
        assert slack.removed_reactions == [
            {
                "channel_id": "C123",
                "message_ts": "1710000000.000002",
                "name": "eyes",
            }
        ]
        assert slack.messages[0]["text"] == "어느 repo를 볼까요?"

    asyncio.run(scenario())


def test_orchestrator_failure_posts_failure_message_and_reaction(tmp_path):
    async def scenario():
        repository = SQLiteJobRepository(tmp_path / "pangi.sqlite3")
        queue = FakeQueue()
        slack = FakeSlack()
        use_case = SubmitSlackRequestUseCase(
            repository=repository,
            job_queue=queue,
            slack_notifier=slack,
            request_orchestrator=FailingOrchestrator(),
            chat_responder=FakeChatResponder(),
            allowed_repo_keys=("PopPang-iOS",),
        )

        result = await use_case.execute(make_request("안녕"))

        assert result.classification == RequestClassification.UNSUPPORTED
        assert result.job_id is None
        assert repository.list_jobs() == []
        assert queue.job_ids == []
        assert slack.messages == [
            {
                "channel_id": "C123",
                "thread_ts": "1710000000.000001",
                "text": "팡이 요청 분류가 지연되어 실패했습니다. 잠시 후 다시 요청해주세요.",
            }
        ]
        assert slack.reactions == [
            {
                "channel_id": "C123",
                "message_ts": "1710000000.000002",
                "name": "eyes",
            },
            {
                "channel_id": "C123",
                "message_ts": "1710000000.000002",
                "name": "x",
            },
        ]
        assert slack.removed_reactions == [
            {
                "channel_id": "C123",
                "message_ts": "1710000000.000002",
                "name": "eyes",
            }
        ]

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
        assert job.slack_message_ts == "1710000000.000002"
        assert queue.job_ids == [result.job_id]

    asyncio.run(scenario())
