import asyncio
import hashlib
import hmac
import json
import sys
import time
from pathlib import Path
from urllib.parse import urlencode

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from pangi.app import app  # noqa: E402
from pangi.config import clear_settings_cache  # noqa: E402
from pangi.infra.codex import set_chat_responder  # noqa: E402
from pangi.infra.git_mcp import set_git_context_provider  # noqa: E402
from pangi.infra.notion import set_notion_context_provider  # noqa: E402
from pangi.infra.orchestrator import (  # noqa: E402
    DeterministicRequestOrchestrator,
    GuardedRequestOrchestrator,
    set_request_orchestrator,
)
from pangi.infra.queue import set_job_queue  # noqa: E402
from pangi.infra.slack import reset_processed_event_ids, set_slack_client  # noqa: E402
from pangi.repository import SQLiteJobRepository, get_job_repository, set_job_repository  # noqa: E402
from pangi.usecase.git_context import GitContext, GitRepoCatalog, GitRepoCatalogItem  # noqa: E402


TEST_SECRET = "test-signing-secret"


class FakeSlackClient:
    def __init__(self):
        self.messages = []
        self.reactions = []
        self.removed_reactions = []

    async def post_message(self, *, channel_id: str, text: str, thread_ts: str | None = None) -> None:
        self.messages.append(
            {
                "channel_id": channel_id,
                "thread_ts": thread_ts,
                "text": text,
            }
        )

    async def add_reaction(self, *, channel_id: str, message_ts: str, name: str) -> None:
        self.reactions.append(
            {
                "channel_id": channel_id,
                "message_ts": message_ts,
                "name": name,
            }
        )

    async def remove_reaction(self, *, channel_id: str, message_ts: str, name: str) -> None:
        self.removed_reactions.append(
            {
                "channel_id": channel_id,
                "message_ts": message_ts,
                "name": name,
            }
        )


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


class CapturingGitContextProvider:
    def __init__(self):
        self.repo_catalog_calls = 0
        self.context_calls = 0

    async def fetch_context(self, *, text: str, user_id: str, channel_id: str, thread_ts: str) -> GitContext:
        self.context_calls += 1
        return GitContext(markdown="## Git context")

    async def fetch_repo_catalog(self, *, local_repo_keys: tuple[str, ...]) -> GitRepoCatalog:
        self.repo_catalog_calls += 1
        return GitRepoCatalog(
            items=(
                GitRepoCatalogItem(name="PopPang-iOS", status="ready"),
                GitRepoCatalogItem(name="PopPang-AOS", status="clone_on_demand"),
            ),
            git_mcp_enabled=True,
            org="team-PopPang",
        )


def setup_function():
    set_job_repository(None)
    set_job_queue(None)
    set_slack_client(None)
    set_chat_responder(None)
    set_notion_context_provider(None)
    set_git_context_provider(None)
    set_request_orchestrator(None)
    reset_processed_event_ids()


def build_signature(secret: str, timestamp: int, body: bytes) -> str:
    base_string = b"v0:" + str(timestamp).encode("utf-8") + b":" + body
    digest = hmac.new(secret.encode("utf-8"), base_string, hashlib.sha256).hexdigest()
    return f"v0={digest}"


async def asgi_request(
    method: str,
    path: str,
    body: bytes = b"",
    headers: dict[str, str] | None = None,
):
    response_messages = []
    request_sent = False
    request_headers = [
        (key.lower().encode("utf-8"), value.encode("utf-8"))
        for key, value in (headers or {}).items()
    ]

    scope = {
        "type": "http",
        "asgi": {"version": "3.0", "spec_version": "2.3"},
        "http_version": "1.1",
        "method": method,
        "scheme": "http",
        "path": path,
        "raw_path": path.encode("utf-8"),
        "query_string": b"",
        "headers": request_headers,
        "client": ("testclient", 50000),
        "server": ("testserver", 80),
    }

    async def receive():
        nonlocal request_sent
        if request_sent:
            return {"type": "http.disconnect"}
        request_sent = True
        return {"type": "http.request", "body": body, "more_body": False}

    async def send(message):
        response_messages.append(message)

    await app(scope, receive, send)
    status = next(
        message["status"]
        for message in response_messages
        if message["type"] == "http.response.start"
    )
    response_body = b"".join(
        message.get("body", b"")
        for message in response_messages
        if message["type"] == "http.response.body"
    )
    return status, json.loads(response_body.decode("utf-8") or "{}")


def request(
    method: str,
    path: str,
    body: bytes = b"",
    headers: dict[str, str] | None = None,
):
    return asyncio.run(asgi_request(method, path, body, headers))


def configure_settings(monkeypatch, tmp_path):
    source_root = Path("/tmp/pangi/sources")
    source_root.mkdir(parents=True, exist_ok=True)
    (source_root / "PopPang-iOS").mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("SLACK_SIGNING_SECRET", TEST_SECRET)
    monkeypatch.setenv("SLACK_BOT_TOKEN", "placeholder-bot-token")
    monkeypatch.setenv("SLACK_ALLOWED_USER_IDS", "U123")
    monkeypatch.setenv("SLACK_ALLOWED_CHANNEL_IDS", "C123")
    monkeypatch.setenv("PANGI_WORKTREE_ROOT", "/tmp/pangi/worktrees")
    monkeypatch.setenv("PANGI_SOURCE_REPO_ROOT", "/tmp/pangi/sources")
    clear_settings_cache()
    reset_processed_event_ids()
    set_job_repository(SQLiteJobRepository(tmp_path / "pangi.sqlite3"))
    set_job_queue(None)
    fake_slack = FakeSlackClient()
    set_slack_client(fake_slack)
    set_chat_responder(FakeChatResponder())
    set_request_orchestrator(GuardedRequestOrchestrator(DeterministicRequestOrchestrator()))
    return fake_slack


def slack_headers(body: bytes, *, timestamp: int | None = None, secret: str = TEST_SECRET):
    request_time = timestamp if timestamp is not None else int(time.time())
    return {
        "Content-Type": "application/json",
        "X-Slack-Request-Timestamp": str(request_time),
        "X-Slack-Signature": build_signature(secret, request_time, body),
    }


def post_slack_event(payload: dict, headers: dict[str, str] | None = None):
    body = json.dumps(payload).encode("utf-8")
    request_headers = headers or slack_headers(body)
    return request("POST", "/slack/events", body=body, headers=request_headers)


def test_slack_events_url_verification(monkeypatch, tmp_path):
    configure_settings(monkeypatch, tmp_path)

    status, body = post_slack_event({"type": "url_verification", "challenge": "challenge-value"})

    assert status == 200
    assert body == {"challenge": "challenge-value"}


def test_slack_events_rejects_invalid_signature(monkeypatch, tmp_path):
    configure_settings(monkeypatch, tmp_path)
    body = json.dumps({"type": "event_callback"}).encode("utf-8")
    headers = slack_headers(body)
    headers["X-Slack-Signature"] = "v0=invalid"

    status, _ = request("POST", "/slack/events", body=body, headers=headers)

    assert status == 401


def test_slack_events_rejects_stale_timestamp(monkeypatch, tmp_path):
    configure_settings(monkeypatch, tmp_path)
    payload = {"type": "event_callback"}
    body = json.dumps(payload).encode("utf-8")
    timestamp = int(time.time()) - 600

    status, _ = request("POST", "/slack/events", body=body, headers=slack_headers(body, timestamp=timestamp))

    assert status == 401


def test_slack_events_normalizes_app_mention(monkeypatch, tmp_path):
    fake_slack = configure_settings(monkeypatch, tmp_path)

    status, body = post_slack_event(
        {
            "type": "event_callback",
            "team_id": "T123",
            "event_id": "Ev123",
            "event": {
                "type": "app_mention",
                "channel": "C123",
                "user": "U123",
                "text": "<@U999> PopPang-iOS 분석해줘",
                "thread_ts": "1710000000.000001",
                "ts": "1710000000.000002",
            },
        }
    )

    assert status == 200
    assert body["ok"] is True
    assert body["command"] == {
        "team_id": "T123",
        "channel_id": "C123",
        "user_id": "U123",
        "text": "PopPang-iOS 분석해줘",
        "thread_ts": "1710000000.000001",
        "event_id": "Ev123",
    }
    assert body["accepted"] is True
    job = get_job_repository().list_jobs()[0]
    assert fake_slack.messages == [
        {
            "channel_id": "C123",
            "thread_ts": "1710000000.000001",
            "text": f"팡이가 요청을 접수했습니다. job_id: {job.id}",
        }
    ]
    assert fake_slack.reactions == [
        {
            "channel_id": "C123",
            "message_ts": "1710000000.000002",
            "name": "eyes",
        }
    ]
    assert job.slack_message_ts == "1710000000.000002"


def test_slack_events_accepts_lowercase_repo_name_in_message(monkeypatch, tmp_path):
    fake_slack = configure_settings(monkeypatch, tmp_path)

    status, body = post_slack_event(
        {
            "type": "event_callback",
            "team_id": "T123",
            "event_id": "EvLowercaseRepo123",
            "event": {
                "type": "app_mention",
                "channel": "C123",
                "user": "U123",
                "text": (
                    "<@U999> 나는 디자이너야. `poppang-ios` 이름의 레포지토리에서 "
                    "iOS 팀원이 어떤 UI를 개편하고 있는지 분석해줄래?"
                ),
                "thread_ts": "1710000000.000001",
                "ts": "1710000000.000002",
            },
        }
    )

    assert status == 200
    assert body["ok"] is True
    job = get_job_repository().list_jobs()[0]
    assert job.repo_key == "PopPang-iOS"
    assert fake_slack.messages == [
        {
            "channel_id": "C123",
            "thread_ts": "1710000000.000001",
            "text": f"팡이가 요청을 접수했습니다. job_id: {job.id}",
        }
    ]


def test_slack_events_uses_event_ts_when_thread_ts_is_missing(monkeypatch, tmp_path):
    fake_slack = configure_settings(monkeypatch, tmp_path)

    status, body = post_slack_event(
        {
            "type": "event_callback",
            "team_id": "T123",
            "event_id": "Ev124",
            "event": {
                "type": "app_mention",
                "channel": "C123",
                "user": "U123",
                "text": "<@U999> PopPang-iOS 분석해줘",
                "ts": "1710000000.000002",
            },
        }
    )

    assert status == 200
    assert body["command"]["thread_ts"] == "1710000000.000002"
    assert fake_slack.reactions == [
        {
            "channel_id": "C123",
            "message_ts": "1710000000.000002",
            "name": "eyes",
        }
    ]


def test_slack_events_blocks_web_analysis_without_job(monkeypatch, tmp_path):
    fake_slack = configure_settings(monkeypatch, tmp_path)

    status, body = post_slack_event(
        {
            "type": "event_callback",
            "team_id": "T123",
            "event_id": "EvWeb123",
            "event": {
                "type": "app_mention",
                "channel": "C123",
                "user": "U123",
                "text": "<@U999> https://example.com 이 글 분석해줘",
                "thread_ts": "1710000000.000001",
                "ts": "1710000000.000002",
            },
        }
    )

    assert status == 200
    assert body["ok"] is True
    assert body["accepted"] is True
    assert "job_id" not in body
    assert fake_slack.reactions == [
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
    assert fake_slack.removed_reactions == [
        {
            "channel_id": "C123",
            "message_ts": "1710000000.000002",
            "name": "eyes",
        }
    ]
    assert fake_slack.messages == [
        {
            "channel_id": "C123",
            "thread_ts": "1710000000.000001",
            "text": (
                "팡이는 PopPang 내부 repo 중심으로 동작합니다. "
                "외부 웹/인터넷 URL 분석은 서버 부하와 보안 이유로 지원하지 않습니다. "
                "PopPang repo 분석이 필요하면 허용된 repo 이름과 함께 요청해주세요."
            ),
        }
    ]
    assert get_job_repository().list_jobs() == []


def test_slack_events_skips_git_repo_catalog_for_plain_chat(monkeypatch, tmp_path):
    configure_settings(monkeypatch, tmp_path)
    git_provider = CapturingGitContextProvider()
    set_git_context_provider(git_provider)

    status, body = post_slack_event(
        {
            "type": "event_callback",
            "team_id": "T123",
            "event_id": "EvPlainChat123",
            "event": {
                "type": "app_mention",
                "channel": "C123",
                "user": "U123",
                "text": "<@U999> 안녕",
                "thread_ts": "1710000000.000001",
                "ts": "1710000000.000002",
            },
        }
    )

    assert status == 200
    assert body["accepted"] is True
    assert git_provider.repo_catalog_calls == 0


def test_slack_events_skips_git_repo_catalog_for_notion_context(monkeypatch, tmp_path):
    configure_settings(monkeypatch, tmp_path)
    git_provider = CapturingGitContextProvider()
    set_git_context_provider(git_provider)

    status, body = post_slack_event(
        {
            "type": "event_callback",
            "team_id": "T123",
            "event_id": "EvNotion123",
            "event": {
                "type": "app_mention",
                "channel": "C123",
                "user": "U123",
                "text": "<@U999> 노션 회의록 요약해줘",
                "thread_ts": "1710000000.000001",
                "ts": "1710000000.000002",
            },
        }
    )

    assert status == 200
    assert body["accepted"] is True
    assert git_provider.repo_catalog_calls == 0


def test_slack_events_uses_git_repo_catalog_for_repo_catalog_request(monkeypatch, tmp_path):
    fake_slack = configure_settings(monkeypatch, tmp_path)
    git_provider = CapturingGitContextProvider()
    set_git_context_provider(git_provider)

    status, body = post_slack_event(
        {
            "type": "event_callback",
            "team_id": "T123",
            "event_id": "EvRepoCatalog123",
            "event": {
                "type": "app_mention",
                "channel": "C123",
                "user": "U123",
                "text": "<@U999> 분석 가능한 repo 리스트 나열해",
                "thread_ts": "1710000000.000001",
                "ts": "1710000000.000002",
            },
        }
    )

    assert status == 200
    assert body["accepted"] is True
    assert git_provider.repo_catalog_calls >= 1
    assert "PopPang-AOS" in fake_slack.messages[-1]["text"]


def test_slack_events_uses_git_repo_catalog_for_github_repo_discovery_phrase(monkeypatch, tmp_path):
    fake_slack = configure_settings(monkeypatch, tmp_path)
    git_provider = CapturingGitContextProvider()
    set_git_context_provider(git_provider)

    status, body = post_slack_event(
        {
            "type": "event_callback",
            "team_id": "T123",
            "event_id": "EvGitHubRepoCatalog123",
            "event": {
                "type": "app_mention",
                "channel": "C123",
                "user": "U123",
                "text": "<@U999> 깃허브레포 뭐뭐 분석가능해",
                "thread_ts": "1710000000.000001",
                "ts": "1710000000.000002",
            },
        }
    )

    assert status == 200
    assert body["accepted"] is True
    assert git_provider.repo_catalog_calls >= 1
    assert "PopPang-AOS" in fake_slack.messages[-1]["text"]


def test_slack_events_ignores_bot_message(monkeypatch, tmp_path):
    configure_settings(monkeypatch, tmp_path)

    status, body = post_slack_event(
        {
            "type": "event_callback",
            "team_id": "T123",
            "event_id": "Ev125",
            "event": {
                "type": "app_mention",
                "channel": "C123",
                "user": "U123",
                "bot_id": "B123",
                "text": "<@U999> ping",
                "ts": "1710000000.000002",
            },
        }
    )

    assert status == 200
    assert body == {"ok": True}


def test_slack_events_blocks_disallowed_user(monkeypatch, tmp_path):
    configure_settings(monkeypatch, tmp_path)

    status, _ = post_slack_event(
        {
            "type": "event_callback",
            "team_id": "T123",
            "event_id": "Ev126",
            "event": {
                "type": "app_mention",
                "channel": "C123",
                "user": "U999",
                "text": "<@U999> ping",
                "ts": "1710000000.000002",
            },
        }
    )

    assert status == 403


def test_slack_events_marks_retry_duplicate(monkeypatch, tmp_path):
    configure_settings(monkeypatch, tmp_path)
    payload = {
        "type": "event_callback",
        "team_id": "T123",
        "event_id": "Ev127",
        "event": {
            "type": "app_mention",
            "channel": "C123",
            "user": "U123",
            "text": "<@U999> PopPang-iOS 분석해줘",
            "ts": "1710000000.000002",
        },
    }
    first_status, _ = post_slack_event(payload)
    body_bytes = json.dumps(payload).encode("utf-8")
    headers = slack_headers(body_bytes)
    headers["X-Slack-Retry-Num"] = "1"

    retry_status, retry_body = request("POST", "/slack/events", body=body_bytes, headers=headers)

    assert first_status == 200
    assert retry_status == 200
    assert retry_body["ok"] is True
    assert retry_body["duplicate"] is True
    assert retry_body["job_id"].startswith("job_")


def test_slack_commands_normalizes_payload(monkeypatch, tmp_path):
    configure_settings(monkeypatch, tmp_path)
    form = {
        "team_id": "T123",
        "channel_id": "C123",
        "user_id": "U123",
        "text": "PopPang-iOS 분석해줘",
        "trigger_id": "trigger-123",
    }
    body = urlencode(form).encode("utf-8")
    timestamp = int(time.time())
    headers = {
        "Content-Type": "application/x-www-form-urlencoded",
        "X-Slack-Request-Timestamp": str(timestamp),
        "X-Slack-Signature": build_signature(TEST_SECRET, timestamp, body),
    }

    status, response_body = request("POST", "/slack/commands", body=body, headers=headers)

    assert status == 200
    assert response_body["command"] == {
        "team_id": "T123",
        "channel_id": "C123",
        "user_id": "U123",
        "text": "PopPang-iOS 분석해줘",
        "thread_ts": "",
        "event_id": "trigger-123",
    }
    assert response_body["response_type"] == "ephemeral"
    assert response_body["text"].startswith("팡이가 요청을 접수했습니다. job_id: job_")
    job = get_job_repository().get_job(response_body["job_id"])
    assert job is not None
    assert job.slack_message_ts is None


def test_slack_commands_blocks_web_analysis_without_job(monkeypatch, tmp_path):
    configure_settings(monkeypatch, tmp_path)
    form = {
        "team_id": "T123",
        "channel_id": "C123",
        "user_id": "U123",
        "text": "https://example.com 기사 요약해줘",
        "trigger_id": "trigger-web-123",
    }
    body = urlencode(form).encode("utf-8")
    timestamp = int(time.time())
    headers = {
        "Content-Type": "application/x-www-form-urlencoded",
        "X-Slack-Request-Timestamp": str(timestamp),
        "X-Slack-Signature": build_signature(TEST_SECRET, timestamp, body),
    }

    status, response_body = request("POST", "/slack/commands", body=body, headers=headers)

    assert status == 200
    assert response_body["response_type"] == "ephemeral"
    assert response_body["classification"] == "blocked_web_analysis"
    assert "job_id" not in response_body
    assert "외부 웹/인터넷 URL 분석은 서버 부하와 보안 이유로 지원하지 않습니다." in response_body["text"]
    assert get_job_repository().list_jobs() == []


def test_slack_commands_routes_repository_catalog(monkeypatch, tmp_path):
    configure_settings(monkeypatch, tmp_path)
    form = {
        "team_id": "T123",
        "channel_id": "C123",
        "user_id": "U123",
        "text": "허용된 레포지토리 리스트 출력해줘",
        "trigger_id": "trigger-catalog-123",
    }
    body = urlencode(form).encode("utf-8")
    timestamp = int(time.time())
    headers = {
        "Content-Type": "application/x-www-form-urlencoded",
        "X-Slack-Request-Timestamp": str(timestamp),
        "X-Slack-Signature": build_signature(TEST_SECRET, timestamp, body),
    }

    status, response_body = request("POST", "/slack/commands", body=body, headers=headers)

    assert status == 200
    assert response_body["response_type"] == "ephemeral"
    assert response_body["classification"] == "repo_catalog"
    assert "현재 팡이가 볼 수 있는 repo 상태예요." in response_body["text"]
    assert "PopPang-iOS: 분석 가능" in response_body["text"]
    assert get_job_repository().list_jobs() == []


def test_slack_commands_routes_notion_context_without_repo_job(monkeypatch, tmp_path):
    configure_settings(monkeypatch, tmp_path)
    form = {
        "team_id": "T123",
        "channel_id": "C123",
        "user_id": "U123",
        "text": "노션 회의록 결정사항 알려줘",
        "trigger_id": "trigger-notion-123",
    }
    body = urlencode(form).encode("utf-8")
    timestamp = int(time.time())
    headers = {
        "Content-Type": "application/x-www-form-urlencoded",
        "X-Slack-Request-Timestamp": str(timestamp),
        "X-Slack-Signature": build_signature(TEST_SECRET, timestamp, body),
    }

    status, response_body = request("POST", "/slack/commands", body=body, headers=headers)

    assert status == 200
    assert response_body["response_type"] == "ephemeral"
    assert response_body["classification"] == "notion_context_chat"
    assert "Notion 문서 읽기는 아직 팡이 서버에 연결되어 있지 않습니다." in response_body["text"]
    assert get_job_repository().list_jobs() == []


def test_slack_interactions_placeholder_verifies_signature(monkeypatch, tmp_path):
    configure_settings(monkeypatch, tmp_path)
    body = urlencode({"payload": "{}"}).encode("utf-8")
    timestamp = int(time.time())
    headers = {
        "Content-Type": "application/x-www-form-urlencoded",
        "X-Slack-Request-Timestamp": str(timestamp),
        "X-Slack-Signature": build_signature(TEST_SECRET, timestamp, body),
    }

    status, response_body = request("POST", "/slack/interactions", body=body, headers=headers)

    assert status == 501
    assert response_body == {"ok": False, "detail": "Slack interactions are not implemented yet"}
