from __future__ import annotations

import re

from pangi.domain.policies import redact_secrets, truncate_text


DEFAULT_OUTPUT_MAX_CHARS = 3500
DEFAULT_EMPTY_OUTPUT = "팡이 응답이 비어 있습니다."
ERROR_BOX_PREFIX = "⚠️ Pangi Error"

BROADCAST_MENTION_PATTERN = re.compile(r"(?<![`@\w])@(channel|here|everyone)\b")
CONTROL_CHARACTER_PATTERN = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def prepare_output_markdown(
    text: str | None,
    *,
    max_chars: int = DEFAULT_OUTPUT_MAX_CHARS,
    empty_fallback: str = DEFAULT_EMPTY_OUTPUT,
) -> str:
    if max_chars <= 0:
        raise ValueError("max_chars must be positive")

    safe_text = _sanitize_output_text(text, empty_fallback=empty_fallback)

    if safe_text.startswith(ERROR_BOX_PREFIX):
        return safe_text
    return truncate_text(safe_text, max_chars=max_chars)


def prepare_error_markdown(
    *,
    stage: str,
    kind: str,
    summary: str,
    detail: str | None,
    next_action: str,
    job_id: str | None = None,
) -> str:
    safe_summary = _sanitize_output_text(summary, empty_fallback="Unknown error")
    safe_detail = _sanitize_output_text(detail, empty_fallback="(no detail)")
    safe_next_action = _sanitize_output_text(next_action, empty_fallback="Check server logs")

    lines = [
        "⚠️ Pangi Error",
        "",
        "```text",
        f"stage: {stage}",
        f"kind: {kind}",
        f"summary: {safe_summary}",
        "detail:",
        safe_detail,
        f"next_action: {safe_next_action}",
    ]
    if job_id:
        lines.append(f"job_id: {job_id}")
    lines.append("```")
    return "\n".join(lines)


def classify_error_kind(detail: str | None) -> str:
    lowered = (detail or "").lower()
    if any(token in lowered for token in ("401", "unauthorized", "token_invalidated", "refresh_token_invalidated", "authentication failed", "auth failed")):
        return "auth_error"
    if any(token in lowered for token in ("permission", "denied", "allowlist", "outside allowlist", "forbidden")):
        return "permission_error"
    if any(token in lowered for token in ("timeout", "timed out")):
        return "timeout"
    if any(token in lowered for token in ("disabled", "not configured", "missing", "not found")):
        return "config_error"
    return "upstream_error"


def next_action_for_error(*, stage: str, kind: str) -> str:
    if stage == "notion_context":
        if kind == "auth_error":
            return "Reconnect Notion OAuth or refresh the server-side Notion token."
        if kind == "permission_error":
            return "Verify Notion page/database sharing and row query permission."
        return "Check Notion MCP logs, allowlist targets, and query/fetch fallback."
    if stage == "git_context":
        if kind == "auth_error":
            return "Verify GitHub MCP token and organization access."
        return "Check GitHub MCP toolset, permissions, and response shape."
    if stage == "repo_catalog":
        return "Check GitHub MCP repo listing toolset, organization access, and local clone state."
    if stage == "codex_chat":
        if kind == "auth_error":
            return "Run codex login again on the server account."
        return "Check Codex CLI auth, workspace path, and stderr detail."
    if stage == "classification":
        return "Check orchestrator auth/config and the raw classification error detail."
    if stage == "repo_analysis":
        if kind == "timeout":
            return "Retry the analysis or increase the server timeout if needed."
        if kind == "auth_error":
            return "Run codex login again on the server account."
        return "Check worktree preparation, Codex stderr, and repository state."
    return "Check server logs and the raw error detail."


def _sanitize_output_text(text: str | None, *, empty_fallback: str) -> str:
    safe_text = redact_secrets(text)
    safe_text = _normalize_line_endings(safe_text)
    safe_text = CONTROL_CHARACTER_PATTERN.sub("", safe_text)
    safe_text = _neutralize_broadcast_mentions(safe_text).strip()

    if not safe_text:
        safe_text = empty_fallback
    return safe_text


def _normalize_line_endings(text: str) -> str:
    return text.replace("\r\n", "\n").replace("\r", "\n")


def _neutralize_broadcast_mentions(text: str) -> str:
    return BROADCAST_MENTION_PATTERN.sub(r"`@\1`", text)
