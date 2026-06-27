import pytest

from pangi.usecase.output_guardrail import prepare_error_markdown, prepare_output_markdown


def test_prepare_output_markdown_redacts_and_neutralizes_broadcast_mentions():
    result = prepare_output_markdown("결과 sk-testtoken\r\n@channel 확인\x00")

    assert result == "결과 [REDACTED]\n`@channel` 확인"


def test_prepare_output_markdown_uses_fallback_for_empty_output():
    assert prepare_output_markdown(" \n ") == "팡이 응답이 비어 있습니다."


def test_prepare_output_markdown_truncates_long_output():
    result = prepare_output_markdown("abcdef", max_chars=3)

    assert result == "abc\n\n... 3 chars truncated ..."


def test_prepare_output_markdown_rejects_invalid_limit():
    with pytest.raises(ValueError, match="max_chars must be positive"):
        prepare_output_markdown("hello", max_chars=0)


def test_prepare_error_markdown_keeps_full_detail_without_truncation():
    result = prepare_error_markdown(
        stage="notion_context",
        kind="permission_error",
        summary="Notion query failed",
        detail="line1\nline2\nline3",
        next_action="verify permission",
        job_id="job_123",
    )

    assert "⚠️ Pangi Error" in result
    assert "stage: notion_context" in result
    assert "kind: permission_error" in result
    assert "detail:\nline1\nline2\nline3" in result
    assert "job_id: job_123" in result
