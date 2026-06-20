from __future__ import annotations

import re
from typing import Iterable

from pangi.usecase.request_decision import (
    NEEDS_REPO_MESSAGE,
    UNSUPPORTED_MESSAGE,
    WEB_ANALYSIS_BLOCKED_MESSAGE,
    ClassifiedRequest,
    RequestClassification,
)


URL_PATTERN = re.compile(r"(?i)(https?://|www\.)\S+")

WEB_ANALYSIS_KEYWORDS = (
    "인터넷",
    "웹",
    "웹문서",
    "검색",
    "구글",
    "뉴스",
    "기사",
    "블로그",
    "사이트",
    "페이지",
    "링크",
    "url",
)
ANALYSIS_KEYWORDS = (
    "분석",
    "요약",
    "정리",
    "봐줘",
    "검토",
    "읽어",
    "알려줘",
    "찾아",
    "원인",
    "왜",
    "구조",
    "흐름",
    "에러",
    "오류",
    "실패",
    "문제",
)
REPO_TARGET_KEYWORDS = (
    "repo",
    "repository",
    "레포",
    "저장소",
    "코드",
    "소스",
    "프로젝트",
)
UNSUPPORTED_KEYWORDS = (
    "수정",
    "고쳐",
    "구현",
    "리팩터링",
    "refactor",
    "pr",
    "풀리퀘",
    "pull request",
    "배포",
    "deploy",
    "커밋",
    "commit",
    "push",
)


def guard_request_input(text: str, *, allowed_repo_keys: Iterable[str] = ()) -> ClassifiedRequest | None:
    normalized = _normalize_text(text)
    repo_key = find_repo_key(normalized, allowed_repo_keys)

    if _looks_like_web_analysis(normalized):
        return ClassifiedRequest(
            kind=RequestClassification.BLOCKED_WEB_ANALYSIS,
            should_create_job=False,
            reply_text=WEB_ANALYSIS_BLOCKED_MESSAGE,
            reason="외부 웹/인터넷 또는 URL 분석 요청입니다.",
        )

    if _looks_unsupported(normalized):
        return ClassifiedRequest(
            kind=RequestClassification.UNSUPPORTED,
            should_create_job=False,
            repo_key=repo_key,
            reply_text=UNSUPPORTED_MESSAGE,
            reason="MVP 범위 밖의 수정/PR/배포 요청입니다.",
        )

    return None


def decide_guarded_request(text: str, *, allowed_repo_keys: Iterable[str] = ()) -> ClassifiedRequest:
    normalized = _normalize_text(text)
    repo_key = find_repo_key(normalized, allowed_repo_keys)

    if repo_key is not None and _looks_like_analysis(normalized):
        return ClassifiedRequest(
            kind=RequestClassification.REPO_ANALYSIS,
            should_create_job=True,
            repo_key=repo_key,
            reason="허용된 repo를 대상으로 한 분석 요청입니다.",
        )

    if repo_key is None and _looks_like_repo_request(normalized):
        return ClassifiedRequest(
            kind=RequestClassification.NEEDS_REPO,
            should_create_job=False,
            reply_text=NEEDS_REPO_MESSAGE,
            reason="repo 분석 의도는 있지만 대상 repo가 명확하지 않습니다.",
        )

    return ClassifiedRequest(
        kind=RequestClassification.CODEX_CHAT,
        should_create_job=False,
        reason="일반 AI 대화 요청입니다.",
    )


def enforce_orchestrator_decision(
    decision: ClassifiedRequest,
    *,
    text: str,
    allowed_repo_keys: Iterable[str],
) -> ClassifiedRequest:
    allowed_keys = tuple(allowed_repo_keys)
    explicit_repo_key = find_repo_key(_normalize_text(text), allowed_keys)

    if decision.kind == RequestClassification.BLOCKED_WEB_ANALYSIS:
        return ClassifiedRequest(
            kind=RequestClassification.BLOCKED_WEB_ANALYSIS,
            should_create_job=False,
            reply_text=decision.reply_text or WEB_ANALYSIS_BLOCKED_MESSAGE,
            reason=decision.reason,
        )

    if decision.kind == RequestClassification.UNSUPPORTED:
        return ClassifiedRequest(
            kind=RequestClassification.UNSUPPORTED,
            should_create_job=False,
            repo_key=explicit_repo_key,
            reply_text=decision.reply_text or UNSUPPORTED_MESSAGE,
            reason=decision.reason,
        )

    if decision.kind != RequestClassification.REPO_ANALYSIS:
        return ClassifiedRequest(
            kind=decision.kind,
            should_create_job=False,
            repo_key=explicit_repo_key if decision.repo_key == explicit_repo_key else None,
            reply_text=_reply_text_for_non_job_decision(decision),
            reason=decision.reason,
        )

    if decision.repo_key in allowed_keys and decision.repo_key == explicit_repo_key:
        return ClassifiedRequest(
            kind=RequestClassification.REPO_ANALYSIS,
            should_create_job=True,
            repo_key=decision.repo_key,
            reply_text=None,
            reason=decision.reason,
        )

    return ClassifiedRequest(
        kind=RequestClassification.NEEDS_REPO,
        should_create_job=False,
        reply_text=NEEDS_REPO_MESSAGE,
        reason="오케스트레이터가 원문에 명시되지 않았거나 허용되지 않은 repo를 선택했습니다.",
    )


def find_repo_key(text: str, allowed_repo_keys: Iterable[str]) -> str | None:
    lowered = text.lower()
    for repo_key in sorted((key for key in allowed_repo_keys if key), key=len, reverse=True):
        if repo_key.lower() in lowered:
            return repo_key
    return None


def _reply_text_for_non_job_decision(decision: ClassifiedRequest) -> str | None:
    if decision.reply_text:
        return decision.reply_text
    if decision.kind == RequestClassification.NEEDS_REPO:
        return NEEDS_REPO_MESSAGE
    return None


def _normalize_text(text: str) -> str:
    return " ".join((text or "").strip().split())


def _looks_like_web_analysis(text: str) -> bool:
    lowered = text.lower()
    if URL_PATTERN.search(text):
        return True

    has_web_keyword = any(keyword in lowered for keyword in WEB_ANALYSIS_KEYWORDS)
    has_analysis_keyword = any(keyword in lowered for keyword in ANALYSIS_KEYWORDS)
    return has_web_keyword and has_analysis_keyword


def _looks_unsupported(text: str) -> bool:
    lowered = text.lower()
    return any(keyword in lowered for keyword in UNSUPPORTED_KEYWORDS)


def _looks_like_analysis(text: str) -> bool:
    lowered = text.lower()
    return any(keyword in lowered for keyword in ANALYSIS_KEYWORDS + REPO_TARGET_KEYWORDS)


def _looks_like_repo_request(text: str) -> bool:
    lowered = text.lower()
    has_repo_target = any(keyword in lowered for keyword in REPO_TARGET_KEYWORDS)
    has_analysis = any(keyword in lowered for keyword in ANALYSIS_KEYWORDS)
    return has_repo_target and has_analysis
