import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from pangi.app import app  # noqa: E402


async def asgi_request(method: str, path: str):
    response_messages = []
    request_sent = False

    scope = {
        "type": "http",
        "asgi": {"version": "3.0", "spec_version": "2.3"},
        "http_version": "1.1",
        "method": method,
        "scheme": "http",
        "path": path,
        "raw_path": path.encode("utf-8"),
        "query_string": b"",
        "headers": [],
        "client": ("testclient", 50000),
        "server": ("testserver", 80),
    }

    async def receive():
        nonlocal request_sent
        if request_sent:
            return {"type": "http.disconnect"}
        request_sent = True
        return {"type": "http.request", "body": b"", "more_body": False}

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


def request(method: str, path: str):
    return asyncio.run(asgi_request(method, path))


def test_health_returns_ok():
    status, body = request("GET", "/health")

    assert status == 200
    assert body == {"status": "ok"}

