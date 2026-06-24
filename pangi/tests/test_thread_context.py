from pangi.domain import ThreadMessage, ThreadMessageRole
from pangi.domain.models import utc_now
from pangi.usecase.thread_context import build_thread_context


def make_message(
    role: ThreadMessageRole,
    text: str,
    *,
    event_id: str | None = None,
) -> ThreadMessage:
    return ThreadMessage(
        id=f"msg_{role.value}",
        slack_thread_id="thread_123",
        role=role,
        text=text,
        message_ts=None,
        event_id=event_id,
        source_job_id=None,
        created_at=utc_now(),
    )


def test_build_thread_context_formats_recent_messages():
    context = build_thread_context(
        [
            make_message(ThreadMessageRole.USER, "안녕", event_id="Ev1"),
            make_message(ThreadMessageRole.ASSISTANT, "안녕하세요"),
        ]
    )

    assert "이전 Slack thread 대화" in context
    assert "참고 맥락" in context
    assert "- 사용자: 안녕" in context
    assert "- 팡이: 안녕하세요" in context


def test_build_thread_context_excludes_current_event_and_redacts_secrets():
    context = build_thread_context(
        [
            make_message(ThreadMessageRole.USER, "토큰 sk-testtoken", event_id="Ev1"),
            make_message(ThreadMessageRole.USER, "현재 요청", event_id="Ev2"),
        ],
        exclude_event_id="Ev2",
    )

    assert "[REDACTED]" in context
    assert "sk-testtoken" not in context
    assert "현재 요청" not in context
