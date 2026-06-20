import hashlib
import hmac
import asyncio
import json
import sys
import time
from pathlib import Path
from urllib.parse import urlencode

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import app  # noqa: E402


TEST_SECRET = "test-signing-secret"


def build_signature(secret: str, timestamp: int, body: bytes) -> str:
    base_string = b"v0:" + str(timestamp).encode("utf-8") + b":" + body
    digest = hmac.new(secret.encode("utf-8"), base_string, hashlib.sha256).hexdigest()
    return f"v0={digest}"


async def asgi_request(method: str, path: str, body: bytes = b"", headers: dict[str, str] | None = None):
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


def request(method: str, path: str, body: bytes = b"", headers: dict[str, str] | None = None):
    return asyncio.run(asgi_request(method, path, body, headers))


def post_slack_command(payload: dict[str, str], secret: str = TEST_SECRET):
    body = urlencode(payload).encode("utf-8")
    timestamp = int(time.time())
    return request(
        "POST",
        "/slack/commands",
        body=body,
        headers={
            "Content-Type": "application/x-www-form-urlencoded",
            "X-Slack-Request-Timestamp": str(timestamp),
            "X-Slack-Signature": build_signature(secret, timestamp, body),
        },
    )


def post_slack_event(payload: dict, secret: str = TEST_SECRET):
    body = json.dumps(payload).encode("utf-8")
    timestamp = int(time.time())
    return request(
        "POST",
        "/slack/events",
        body=body,
        headers={
            "Content-Type": "application/json",
            "X-Slack-Request-Timestamp": str(timestamp),
            "X-Slack-Signature": build_signature(secret, timestamp, body),
        },
    )


def test_health():
    status, body = request("GET", "/health")

    assert status == 200
    assert body == {"status": "ok"}


def test_slack_command_returns_test_response(monkeypatch):
    monkeypatch.setenv("SLACK_SIGNING_SECRET", TEST_SECRET)
    monkeypatch.setenv("SLACK_ALLOWED_COMMANDS", "/팝팡,/poppang")

    status, body = post_slack_command(
        {
            "command": "/팝팡",
            "text": "",
            "user_id": "U123",
            "channel_id": "C123",
            "response_url": "https://hooks.slack.com/commands/example",
        },
    )

    assert status == 200
    assert body == {
        "response_type": "ephemeral",
        "text": "팝팡봇 테스트 응답입니다",
    }


def test_slack_command_rejects_invalid_signature(monkeypatch):
    monkeypatch.setenv("SLACK_SIGNING_SECRET", TEST_SECRET)
    body = urlencode({"command": "/팝팡"}).encode("utf-8")

    status, _ = request(
        "POST",
        "/slack/commands",
        body=body,
        headers={
            "Content-Type": "application/x-www-form-urlencoded",
            "X-Slack-Request-Timestamp": str(int(time.time())),
            "X-Slack-Signature": "v0=invalid",
        },
    )

    assert status == 401


def test_slack_command_rejects_stale_timestamp(monkeypatch):
    monkeypatch.setenv("SLACK_SIGNING_SECRET", TEST_SECRET)
    body = urlencode({"command": "/팝팡"}).encode("utf-8")
    timestamp = int(time.time()) - 600

    status, _ = request(
        "POST",
        "/slack/commands",
        body=body,
        headers={
            "Content-Type": "application/x-www-form-urlencoded",
            "X-Slack-Request-Timestamp": str(timestamp),
            "X-Slack-Signature": build_signature(TEST_SECRET, timestamp, body),
        },
    )

    assert status == 401


def test_slack_events_returns_url_verification_challenge(monkeypatch):
    monkeypatch.setenv("SLACK_SIGNING_SECRET", TEST_SECRET)
    monkeypatch.delenv("SLACK_BOT_TOKEN", raising=False)

    status, body = post_slack_event(
        {
            "type": "url_verification",
            "challenge": "challenge-value",
        }
    )

    assert status == 200
    assert body == {"challenge": "challenge-value"}


def test_slack_events_receives_app_mention_without_bot_token(monkeypatch):
    monkeypatch.setenv("SLACK_SIGNING_SECRET", TEST_SECRET)
    monkeypatch.delenv("SLACK_BOT_TOKEN", raising=False)

    status, body = post_slack_event(
        {
            "type": "event_callback",
            "event": {
                "type": "app_mention",
                "channel": "C123",
                "user": "U123",
                "text": "<@U999> 테스트",
                "ts": "1710000000.000001",
            },
        }
    )

    assert status == 200
    assert body == {"ok": True, "reply_skipped": "SLACK_BOT_TOKEN is not configured"}


def test_slack_events_rejects_invalid_signature(monkeypatch):
    monkeypatch.setenv("SLACK_SIGNING_SECRET", TEST_SECRET)
    body = json.dumps({"type": "event_callback"}).encode("utf-8")

    status, _ = request(
        "POST",
        "/slack/events",
        body=body,
        headers={
            "Content-Type": "application/json",
            "X-Slack-Request-Timestamp": str(int(time.time())),
            "X-Slack-Signature": "v0=invalid",
        },
    )

    assert status == 401
