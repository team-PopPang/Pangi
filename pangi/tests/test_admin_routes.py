import asyncio
import json
import sys
from pathlib import Path
from urllib.parse import urlencode

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from pangi.app import app  # noqa: E402
from pangi.config import clear_settings_cache  # noqa: E402
from pangi.repository import SQLiteJobRepository, set_job_repository  # noqa: E402


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
    response_headers = {
        key.decode("utf-8"): value.decode("utf-8")
        for message in response_messages
        if message["type"] == "http.response.start"
        for key, value in message["headers"]
    }
    response_body = b"".join(
        message.get("body", b"")
        for message in response_messages
        if message["type"] == "http.response.body"
    )
    return status, response_headers, response_body


def request(
    method: str,
    path: str,
    body: bytes = b"",
    headers: dict[str, str] | None = None,
):
    return asyncio.run(asgi_request(method, path, body, headers))


def configure_settings(monkeypatch, tmp_path, *, enable_admin=False):
    monkeypatch.setenv("SLACK_SIGNING_SECRET", "placeholder-signing-secret")
    monkeypatch.setenv("SLACK_BOT_TOKEN", "placeholder-bot-token")
    monkeypatch.setenv("SLACK_ALLOWED_USER_IDS", "U123")
    monkeypatch.setenv("SLACK_ALLOWED_CHANNEL_IDS", "C123")
    monkeypatch.setenv("PANGI_ALLOWED_REPOS", "PopPang-iOS=/tmp/pangi/sources/PopPang-iOS")
    monkeypatch.setenv("PANGI_WORKTREE_ROOT", "/tmp/pangi/worktrees")
    monkeypatch.setenv("PANGI_SOURCE_REPO_ROOT", "/tmp/pangi/sources")
    if enable_admin:
        monkeypatch.setenv("PANGI_ENABLE_ADMIN_PAGES", "1")
        monkeypatch.setenv("PANGI_ADMIN_PASSWORD", "admin-password")
    else:
        monkeypatch.delenv("PANGI_ENABLE_ADMIN_PAGES", raising=False)
        monkeypatch.delenv("PANGI_ADMIN_PASSWORD", raising=False)
    clear_settings_cache()
    repository = SQLiteJobRepository(tmp_path / "pangi.sqlite3")
    set_job_repository(repository)
    return repository


def seed_job(repository):
    thread = repository.get_or_create_thread(team_id="T123", channel_id="C123", thread_ts="171.1")
    return repository.create_job(
        event_id="Ev123",
        slack_thread=thread,
        requester_user_id="U123",
        prompt="<script>alert('x')</script>",
    )


def test_admin_db_is_hidden_by_default(monkeypatch, tmp_path):
    configure_settings(monkeypatch, tmp_path)

    status, _, body = request("GET", "/pangi-admin/db")

    assert status == 404
    assert json.loads(body.decode("utf-8")) == {"detail": "Not found"}


def test_admin_login_page_requires_enabled_admin(monkeypatch, tmp_path):
    configure_settings(monkeypatch, tmp_path, enable_admin=True)

    status, _, body = request("GET", "/pangi-admin/login")

    assert status == 200
    assert "Pangi Admin" in body.decode("utf-8")


def test_admin_rejects_bad_login(monkeypatch, tmp_path):
    configure_settings(monkeypatch, tmp_path, enable_admin=True)
    body = urlencode({"username": "pangi", "password": "wrong"}).encode("utf-8")

    status, _, response_body = request(
        "POST",
        "/pangi-admin/login",
        body=body,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )

    assert status == 401
    assert "올바르지 않습니다" in response_body.decode("utf-8")


def test_admin_login_can_view_db_page(monkeypatch, tmp_path):
    repository = configure_settings(monkeypatch, tmp_path, enable_admin=True)
    job = seed_job(repository)
    body = urlencode({"username": "pangi", "password": "admin-password"}).encode("utf-8")

    login_status, login_headers, _ = request(
        "POST",
        "/pangi-admin/login",
        body=body,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    cookie = login_headers["set-cookie"].split(";", 1)[0]
    db_status, _, db_body = request("GET", "/pangi-admin/db", headers={"Cookie": cookie})
    html = db_body.decode("utf-8")

    assert login_status == 303
    assert db_status == 200
    assert job.id in html
    assert "&lt;script&gt;alert(&#x27;x&#x27;)&lt;/script&gt;" in html
    assert "<script>alert('x')</script>" not in html


def test_admin_does_not_mount_generic_admin_path(monkeypatch, tmp_path):
    configure_settings(monkeypatch, tmp_path, enable_admin=True)

    status, _, body = request("GET", "/admin/login")

    assert status == 404
    assert json.loads(body.decode("utf-8")) == {"detail": "Not Found"}
