import hashlib
import hmac
import asyncio
import json
import os
import re
import time
from typing import Dict, Optional
from urllib.parse import parse_qs
from urllib.request import Request as UrlRequest, urlopen

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse

load_dotenv()

app = FastAPI(title="PopPang Slack Bot")

RESPONSE_TEXT = "팝팡봇 테스트 응답입니다"
MENTION_RESPONSE_TEXT = "팝팡봇 멘션 테스트 응답입니다"
DEFAULT_ALLOWED_COMMANDS = "/팝팡,/poppang,/poppangbot"
SIGNATURE_TOLERANCE_SECONDS = 60 * 5
SLACK_POST_MESSAGE_URL = "https://slack.com/api/chat.postMessage"


def parse_urlencoded_body(body: bytes) -> Dict[str, str]:
    parsed = parse_qs(body.decode("utf-8"), keep_blank_values=True)
    return {key: values[0] if values else "" for key, values in parsed.items()}


def configured_allowed_commands() -> set[str]:
    raw_value = os.getenv("SLACK_ALLOWED_COMMANDS", DEFAULT_ALLOWED_COMMANDS)
    return {command.strip() for command in raw_value.split(",") if command.strip()}


def verify_slack_signature(
    *,
    signing_secret: str,
    timestamp: Optional[str],
    signature: Optional[str],
    body: bytes,
    now: Optional[int] = None,
) -> bool:
    if not timestamp or not signature:
        return False

    try:
        request_time = int(timestamp)
    except ValueError:
        return False

    current_time = int(now if now is not None else time.time())
    if abs(current_time - request_time) > SIGNATURE_TOLERANCE_SECONDS:
        return False

    base_string = b"v0:" + timestamp.encode("utf-8") + b":" + body
    digest = hmac.new(signing_secret.encode("utf-8"), base_string, hashlib.sha256).hexdigest()
    expected_signature = f"v0={digest}"
    return hmac.compare_digest(expected_signature, signature)


def slack_response(text: str, response_type: str = "ephemeral") -> JSONResponse:
    return JSONResponse({"response_type": response_type, "text": text})


def clean_mention_text(text: str) -> str:
    return re.sub(r"<@[A-Z0-9]+>", "", text or "").strip()


def post_slack_message(token: str, channel: str, text: str, thread_ts: Optional[str] = None) -> dict:
    payload = {
        "channel": channel,
        "text": text,
    }
    if thread_ts:
        payload["thread_ts"] = thread_ts

    request = UrlRequest(
        SLACK_POST_MESSAGE_URL,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json; charset=utf-8",
        },
        method="POST",
    )
    with urlopen(request, timeout=10) as response:
        return json.loads(response.read().decode("utf-8"))


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/slack/commands")
async def slack_commands(request: Request) -> JSONResponse:
    body = await request.body()
    signing_secret = os.getenv("SLACK_SIGNING_SECRET")
    if not signing_secret:
        raise HTTPException(status_code=500, detail="SLACK_SIGNING_SECRET is not configured")

    is_valid = verify_slack_signature(
        signing_secret=signing_secret,
        timestamp=request.headers.get("X-Slack-Request-Timestamp"),
        signature=request.headers.get("X-Slack-Signature"),
        body=body,
    )
    if not is_valid:
        raise HTTPException(status_code=401, detail="Invalid Slack signature")

    form = parse_urlencoded_body(body)
    if form.get("ssl_check") == "1":
        return slack_response("ok")

    command = form.get("command", "")
    if command and command not in configured_allowed_commands():
        return slack_response("지원하지 않는 명령어입니다")

    return slack_response(RESPONSE_TEXT)


@app.post("/slack/events")
async def slack_events(request: Request) -> JSONResponse:
    body = await request.body()
    signing_secret = os.getenv("SLACK_SIGNING_SECRET")
    if not signing_secret:
        raise HTTPException(status_code=500, detail="SLACK_SIGNING_SECRET is not configured")

    is_valid = verify_slack_signature(
        signing_secret=signing_secret,
        timestamp=request.headers.get("X-Slack-Request-Timestamp"),
        signature=request.headers.get("X-Slack-Signature"),
        body=body,
    )
    if not is_valid:
        raise HTTPException(status_code=401, detail="Invalid Slack signature")

    try:
        payload = json.loads(body.decode("utf-8"))
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="Invalid JSON payload")

    if payload.get("type") == "url_verification":
        return JSONResponse({"challenge": payload.get("challenge", "")})

    if payload.get("type") != "event_callback":
        return JSONResponse({"ok": True})

    event = payload.get("event") or {}
    if event.get("type") != "app_mention":
        return JSONResponse({"ok": True})

    if event.get("bot_id") or event.get("subtype"):
        return JSONResponse({"ok": True})

    channel = event.get("channel")
    if not channel:
        return JSONResponse({"ok": True})

    message_text = clean_mention_text(event.get("text", ""))
    reply_text = MENTION_RESPONSE_TEXT
    if message_text:
        reply_text = f"{MENTION_RESPONSE_TEXT}: {message_text}"

    bot_token = os.getenv("SLACK_BOT_TOKEN")
    if not bot_token:
        return JSONResponse({"ok": True, "reply_skipped": "SLACK_BOT_TOKEN is not configured"})

    slack_result = await asyncio.to_thread(
        post_slack_message,
        bot_token,
        channel,
        reply_text,
        event.get("thread_ts") or event.get("ts"),
    )
    if not slack_result.get("ok"):
        return JSONResponse(
            {"ok": False, "slack_error": slack_result.get("error", "unknown")},
            status_code=502,
        )

    return JSONResponse({"ok": True})
