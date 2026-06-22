from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Iterable


WEB_ANALYSIS_BLOCKED_MESSAGE = (
    "팡이는 PopPang 내부 repo 중심으로 동작합니다. "
    "외부 웹/인터넷 URL 분석은 서버 부하와 보안 이유로 지원하지 않습니다. "
    "PopPang repo 분석이 필요하면 허용된 repo 이름과 함께 요청해주세요."
)
NEEDS_REPO_MESSAGE = "어느 repo를 볼까요? 허용된 repo 이름과 함께 다시 요청해주세요."
UNSUPPORTED_MESSAGE = "현재 MVP에서는 코드 수정, PR 생성, 배포는 지원하지 않고 read-only 분석만 지원합니다."
NOTION_CONTEXT_DISABLED_MESSAGE = (
    "Notion 문서 읽기는 아직 팡이 서버에 연결되어 있지 않습니다. "
    "Notion MCP 연결과 allowlist가 설정되면, 허용된 Notion 문서를 읽고 답할 수 있어요."
)
NOTION_CONTEXT_ACCESS_DENIED_MESSAGE = (
    "이 Notion 문서는 아직 팡이가 읽을 수 있는 allowlist에 없습니다. "
    "허용할 page 또는 database를 설정한 뒤 다시 요청해주세요."
)
GIT_CONTEXT_DISABLED_MESSAGE = (
    "Git MCP context는 아직 팡이 서버에 연결되어 있지 않습니다. "
    "Git MCP 연결과 조직 설정이 준비되면 repo, PR, issue, Actions 맥락을 읽고 답할 수 있어요."
)
GIT_CONTEXT_ACCESS_DENIED_MESSAGE = (
    "이 Git context는 아직 팡이가 읽을 수 있는 범위가 아닙니다. "
    "Git MCP 조직 또는 권한 설정을 확인한 뒤 다시 요청해주세요."
)


class RequestClassification(StrEnum):
    CODEX_CHAT = "codex_chat"
    BLOCKED_WEB_ANALYSIS = "blocked_web_analysis"
    NEEDS_REPO = "needs_repo"
    REPO_ANALYSIS = "repo_analysis"
    NOTION_CONTEXT_CHAT = "notion_context_chat"
    GIT_CONTEXT_CHAT = "git_context_chat"
    REPO_CATALOG = "repo_catalog"
    UNSUPPORTED = "unsupported"


@dataclass(frozen=True)
class ClassifiedRequest:
    kind: RequestClassification
    should_create_job: bool
    repo_key: str | None = None
    reply_text: str | None = None
    reason: str | None = None


def build_needs_repo_message(allowed_repo_keys: Iterable[str]) -> str:
    repo_keys = tuple(key for key in allowed_repo_keys if key)
    if not repo_keys:
        return (
            "현재 팡이가 분석할 수 있는 repo를 찾지 못했습니다. "
            "서버의 PANGI_SOURCE_REPO_ROOT 아래에 PopPang repo clone이 있는지 확인해주세요."
        )

    repo_lines = "\n".join(f"- {repo_key}" for repo_key in sorted(repo_keys))
    return f"""\
어느 repo를 볼까요? 아래 이름 중 하나를 메시지에 넣어 다시 요청해주세요.

{repo_lines}
"""
