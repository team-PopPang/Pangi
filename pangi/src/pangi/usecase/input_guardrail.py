from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable

from pangi.usecase.request_decision import (
    NEEDS_REPO_MESSAGE,
    UNSUPPORTED_MESSAGE,
    WEB_ANALYSIS_BLOCKED_MESSAGE,
    ClassifiedRequest,
    RequestClassification,
)


URL_PATTERN = re.compile(r"(?i)(https?://|www\.)\S+")
COMPACT_PATTERN = re.compile(r"[^a-z0-9가-힣]+")

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
SECRET_KEYWORDS = (
    ".env",
    "env 파일",
    "token",
    "토큰",
    "secret",
    "시크릿",
    "api key",
    "apikey",
    "비밀키",
    "인증키",
    "password",
    "비밀번호",
)
CHAT_KEYWORDS = (
    "안녕",
    "하이",
    "hello",
    "고마워",
    "감사",
    "땡큐",
    "설명해줘",
    "문장",
    "말투",
    "작성",
    "다듬",
    "번역",
)
AMBIGUOUS_REFERENCE_KEYWORDS = (
    "어제",
    "아까",
    "전에",
    "방금",
    "위에",
    "그거",
    "저거",
    "그 내용",
    "해당 내용",
    "이 흐름",
    "그 흐름",
)


@dataclass(frozen=True)
class RequestFeatures:
    raw_text: str
    normalized_text: str
    repo_key: str | None
    has_url: bool
    has_web_intent: bool
    has_write_intent: bool
    has_secret_risk: bool
    has_repo_target: bool
    has_analysis_intent: bool
    has_chat_intent: bool
    has_ambiguous_reference: bool
    matched_terms: tuple[str, ...]


@dataclass(frozen=True)
class InputGuardrailRoute:
    decision: ClassifiedRequest | None
    needs_ai_orchestrator: bool
    confidence: str
    features: RequestFeatures
    reason: str


def guard_request_input(text: str, *, allowed_repo_keys: Iterable[str] = ()) -> ClassifiedRequest | None:
    features = extract_request_features(text, allowed_repo_keys=allowed_repo_keys)
    repo_key = features.repo_key

    if features.has_url or (features.has_web_intent and features.has_analysis_intent):
        return ClassifiedRequest(
            kind=RequestClassification.BLOCKED_WEB_ANALYSIS,
            should_create_job=False,
            reply_text=WEB_ANALYSIS_BLOCKED_MESSAGE,
            reason="외부 웹/인터넷 또는 URL 분석 요청입니다.",
        )

    if features.has_write_intent or features.has_secret_risk:
        return ClassifiedRequest(
            kind=RequestClassification.UNSUPPORTED,
            should_create_job=False,
            repo_key=repo_key,
            reply_text=UNSUPPORTED_MESSAGE,
            reason="MVP 범위 밖의 쓰기/배포/민감 정보 요청입니다.",
        )

    return None


def route_request_input(text: str, *, allowed_repo_keys: Iterable[str] = ()) -> InputGuardrailRoute:
    features = extract_request_features(text, allowed_repo_keys=allowed_repo_keys)
    policy_decision = guard_request_input(text, allowed_repo_keys=allowed_repo_keys)
    if policy_decision is not None:
        return InputGuardrailRoute(
            decision=policy_decision,
            needs_ai_orchestrator=False,
            confidence="high",
            features=features,
            reason=policy_decision.reason or "입력 가드레일 정책으로 판정했습니다.",
        )

    if features.repo_key is not None and features.has_analysis_intent:
        decision = ClassifiedRequest(
            kind=RequestClassification.REPO_ANALYSIS,
            should_create_job=True,
            repo_key=features.repo_key,
            reason="허용된 repo와 분석 의도가 모두 명확합니다.",
        )
        return InputGuardrailRoute(
            decision=decision,
            needs_ai_orchestrator=False,
            confidence="high",
            features=features,
            reason=decision.reason or "",
        )

    if features.repo_key is None and features.has_repo_target and features.has_analysis_intent:
        decision = ClassifiedRequest(
            kind=RequestClassification.NEEDS_REPO,
            should_create_job=False,
            reply_text=NEEDS_REPO_MESSAGE,
            reason="repo 분석 의도는 있지만 대상 repo가 명확하지 않습니다.",
        )
        return InputGuardrailRoute(
            decision=decision,
            needs_ai_orchestrator=False,
            confidence="high",
            features=features,
            reason=decision.reason or "",
        )

    if _needs_ai_orchestrator(features):
        return InputGuardrailRoute(
            decision=None,
            needs_ai_orchestrator=True,
            confidence="low",
            features=features,
            reason="지시 대상이나 맥락이 모호해서 AI Orchestrator 보조 판정이 필요합니다.",
        )

    decision = ClassifiedRequest(
        kind=RequestClassification.CODEX_CHAT,
        should_create_job=False,
        reason="일반 대화 또는 repo를 직접 읽지 않는 요청입니다.",
    )
    return InputGuardrailRoute(
        decision=decision,
        needs_ai_orchestrator=False,
        confidence="medium" if features.has_analysis_intent else "high",
        features=features,
        reason=decision.reason or "",
    )


def decide_guarded_request(text: str, *, allowed_repo_keys: Iterable[str] = ()) -> ClassifiedRequest:
    route = route_request_input(text, allowed_repo_keys=allowed_repo_keys)
    if route.decision is not None:
        return route.decision
    return ClassifiedRequest(
        kind=RequestClassification.CODEX_CHAT,
        should_create_job=False,
        reason="AI Orchestrator 없이 deterministic fallback이 일반 대화로 처리했습니다.",
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
    compacted = _compact_text(text)
    for repo_key in sorted((key for key in allowed_repo_keys if key), key=len, reverse=True):
        lowered_repo_key = repo_key.lower()
        compacted_repo_key = _compact_text(repo_key)
        if lowered_repo_key in lowered or compacted_repo_key in compacted:
            return repo_key
    return None


def extract_request_features(text: str, *, allowed_repo_keys: Iterable[str] = ()) -> RequestFeatures:
    normalized = _normalize_text(text)
    lowered = normalized.lower()
    repo_key = find_repo_key(normalized, allowed_repo_keys)
    matched_terms: list[str] = []

    has_url = URL_PATTERN.search(normalized) is not None
    if has_url:
        matched_terms.append("url")

    has_web_intent = _has_any(lowered, WEB_ANALYSIS_KEYWORDS, matched_terms)
    has_write_intent = _has_any(lowered, UNSUPPORTED_KEYWORDS, matched_terms)
    has_secret_risk = _has_any(lowered, SECRET_KEYWORDS, matched_terms)
    has_repo_target = repo_key is not None or _has_any(lowered, REPO_TARGET_KEYWORDS, matched_terms)
    has_analysis_intent = _has_any(lowered, ANALYSIS_KEYWORDS, matched_terms)
    has_chat_intent = _has_any(lowered, CHAT_KEYWORDS, matched_terms)
    has_ambiguous_reference = _has_any(lowered, AMBIGUOUS_REFERENCE_KEYWORDS, matched_terms)

    if repo_key is not None:
        matched_terms.append(f"repo:{repo_key}")

    return RequestFeatures(
        raw_text=text,
        normalized_text=normalized,
        repo_key=repo_key,
        has_url=has_url,
        has_web_intent=has_web_intent,
        has_write_intent=has_write_intent,
        has_secret_risk=has_secret_risk,
        has_repo_target=has_repo_target,
        has_analysis_intent=has_analysis_intent,
        has_chat_intent=has_chat_intent,
        has_ambiguous_reference=has_ambiguous_reference,
        matched_terms=tuple(dict.fromkeys(matched_terms)),
    )


def _reply_text_for_non_job_decision(decision: ClassifiedRequest) -> str | None:
    if decision.reply_text:
        return decision.reply_text
    if decision.kind == RequestClassification.NEEDS_REPO:
        return NEEDS_REPO_MESSAGE
    return None


def _normalize_text(text: str) -> str:
    return " ".join((text or "").strip().split())


def _compact_text(text: str) -> str:
    return COMPACT_PATTERN.sub("", (text or "").lower())


def _has_any(text: str, keywords: Iterable[str], matched_terms: list[str] | None = None) -> bool:
    for keyword in keywords:
        if keyword in text:
            if matched_terms is not None:
                matched_terms.append(keyword)
            return True
    return False


def _needs_ai_orchestrator(features: RequestFeatures) -> bool:
    if features.repo_key is not None or features.has_repo_target:
        return False
    if not features.has_analysis_intent:
        return False
    return features.has_ambiguous_reference


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
