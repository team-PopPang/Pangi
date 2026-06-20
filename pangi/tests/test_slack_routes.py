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
from pangi.infra.queue import set_job_queue  # noqa: E402
from pangi.infra.slack import reset_processed_event_ids, set_slack_client  # noqa: E402
from pangi.repository import SQLiteJobRepository, set_job_repository  # noqa: E402


TEST_SECRET = "test-signing-secret"


class FakeSlackClient:
    def __init__(self):
        self.messages = []
        self.reactions = []

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


def setup_function():
    set_job_repository(None)
    set_job_queue(None)
    set_slack_client(None)
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
    monkeypatch.setenv("SLACK_SIGNING_SECRET", TEST_SECRET)
    monkeypatch.setenv("SLACK_BOT_TOKEN", "placeholder-bot-token")
    monkeypatch.setenv("SLACK_ALLOWED_USER_IDS", "U123")
    monkeypatch.setenv("SLACK_ALLOWED_CHANNEL_IDS", "C123")
    monkeypatch.setenv("PANGI_ALLOWED_REPOS", "PopPang-iOS=/tmp/pangi/sources/PopPang-iOS")
    monkeypatch.setenv("PANGI_WORKTREE_ROOT", "/tmp/pangi/worktrees")
    monkeypatch.setenv("PANGI_SOURCE_REPO_ROOT", "/tmp/pangi/sources")
    clear_settings_cache()
    reset_processed_event_ids()
    set_job_repository(SQLiteJobRepository(tmp_path / "pangi.sqlite3"))
    set_job_queue(None)
    fake_slack = FakeSlackClient()
    set_slack_client(fake_slack)
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
                "text": "<@U999> 분석해줘",
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
        "text": "분석해줘",
        "thread_ts": "1710000000.000001",
        "event_id": "Ev123",
    }
    assert fake_slack.messages == [
        {
            "channel_id": "C123",
            "thread_ts": "1710000000.000001",
            "text": f"팡이가 요청을 접수했습니다. job_id: {body['job_id']}",
        }
    ]
    assert fake_slack.reactions == [
        {
            "channel_id": "C123",
            "message_ts": "1710000000.000002",
            "name": "eyes",
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
                "text": "<@U999> ping",
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
            "text": "<@U999> ping",
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
        "text": "분석해줘",
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
        "text": "분석해줘",
        "thread_ts": "",
        "event_id": "trigger-123",
    }
    assert response_body["response_type"] == "ephemeral"
    assert response_body["text"].startswith("팡이가 요청을 접수했습니다. job_id: job_")


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
