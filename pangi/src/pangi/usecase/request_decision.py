from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


WEB_ANALYSIS_BLOCKED_MESSAGE = (
    "팡이는 PopPang 내부 repo 중심으로 동작합니다. "
    "외부 웹/인터넷 URL 분석은 서버 부하와 보안 이유로 지원하지 않습니다. "
    "PopPang repo 분석이 필요하면 허용된 repo 이름과 함께 요청해주세요."
)
NEEDS_REPO_MESSAGE = "어느 repo를 볼까요? 허용된 repo 이름과 함께 다시 요청해주세요."
UNSUPPORTED_MESSAGE = "현재 MVP에서는 코드 수정, PR 생성, 배포는 지원하지 않고 read-only 분석만 지원합니다."


class RequestClassification(StrEnum):
    CODEX_CHAT = "codex_chat"
    BLOCKED_WEB_ANALYSIS = "blocked_web_analysis"
    NEEDS_REPO = "needs_repo"
    REPO_ANALYSIS = "repo_analysis"
    UNSUPPORTED = "unsupported"


@dataclass(frozen=True)
class ClassifiedRequest:
    kind: RequestClassification
    should_create_job: bool
    repo_key: str | None = None
    reply_text: str | None = None
    reason: str | None = None
