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
    build_needs_repo_message,
)


URL_PATTERN = re.compile(r"(?i)(https?://|www\.)\S+")
NOTION_URL_PATTERN = re.compile(r"(?i)https?://(?:www\.)?[\w.-]*(?:notion\.so|notion\.site)/\S+")
GITHUB_URL_PATTERN = re.compile(r"(?i)https?://(?:www\.)?github\.com/\S+")
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
    "뭐",
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
CODE_WRITE_KEYWORDS = (
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
GIT_CONTEXT_KEYWORDS = (
    "github",
    "깃허브",
    "git",
    "깃",
    "pr",
    "pull request",
    "풀리퀘",
    "issue",
    "이슈",
    "actions",
    "action",
    "액션",
    "workflow",
    "워크플로",
    "ci",
    "커밋",
    "commit",
    "branch",
    "브랜치",
    "release",
    "릴리즈",
)
GIT_WRITE_TARGET_KEYWORDS = (
    "pr",
    "pull request",
    "풀리퀘",
    "issue",
    "이슈",
    "commit",
    "커밋",
    "branch",
    "브랜치",
    "release",
    "릴리즈",
)
WRITE_ACTION_KEYWORDS = (
    "생성",
    "만들",
    "열어",
    "등록",
    "추가",
    "수정해",
    "고쳐",
    "작성",
    "써줘",
    "올려",
    "merge",
    "머지",
    "push",
    "푸시",
    "배포",
    "deploy",
)
REPO_CATALOG_PHRASES = (
    "분석 가능한 레포",
    "분석가능한 레포",
    "분석가능한레포",
    "분석 가능한 레포지토리",
    "분석 가능한 repository",
    "분석 가능한 저장소",
    "분석 가능한 repo",
    "허용된 레포",
    "허용된 레포지토리",
    "허용된 repo",
    "허용된 repository",
    "허용 repo",
    "사용 가능한 레포",
    "사용 가능한 레포지토리",
    "사용 가능한 저장소",
    "사용가능한레포",
    "볼 수 있는 레포",
    "볼 수 있는 레포지토리",
    "볼 수 있는 저장소",
    "읽을 수 있는 레포",
    "읽을 수 있는 레포지토리",
    "읽을 수 있는 저장소",
    "레포 목록",
    "레포 리스트",
    "레포지토리 목록",
    "레포지토리 리스트",
    "repo 목록",
    "repo 리스트",
    "repository 목록",
    "repository 리스트",
    "저장소 목록",
    "저장소 리스트",
    "어떤 레포",
    "어떤 레포지토리",
    "어떤 저장소",
    "무슨 레포",
    "무슨 레포지토리",
    "무슨 저장소",
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
NOTION_KEYWORDS = (
    "notion",
    "노션",
)
NOTION_WRITE_KEYWORDS = (
    "생성",
    "만들",
    "추가",
    "업데이트",
    "수정",
    "고쳐",
    "삭제",
    "작성해",
    "써줘",
    "기록해",
    "저장해",
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
    has_notion_url: bool
    has_notion_intent: bool
    has_notion_write_intent: bool
    has_git_context_intent: bool
    has_repo_catalog_intent: bool
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

    if (features.has_url and not features.has_notion_intent and not features.has_git_context_intent) or (
        features.has_web_intent
        and features.has_analysis_intent
        and not features.has_notion_intent
        and not features.has_git_context_intent
    ):
        return ClassifiedRequest(
            kind=RequestClassification.BLOCKED_WEB_ANALYSIS,
            should_create_job=False,
            reply_text=WEB_ANALYSIS_BLOCKED_MESSAGE,
            reason="외부 웹/인터넷 또는 URL 분석 요청입니다.",
        )

    if features.has_write_intent or features.has_secret_risk or features.has_notion_write_intent:
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

    if features.has_notion_intent:
        decision = ClassifiedRequest(
            kind=RequestClassification.NOTION_CONTEXT_CHAT,
            should_create_job=False,
            reason="Notion 문서 또는 Notion 데이터 맥락 요청입니다.",
        )
        return InputGuardrailRoute(
            decision=decision,
            needs_ai_orchestrator=False,
            confidence="high",
            features=features,
            reason=decision.reason or "",
        )

    if features.has_repo_catalog_intent:
        decision = ClassifiedRequest(
            kind=RequestClassification.REPO_CATALOG,
            should_create_job=False,
            reason="분석 가능한 repo 목록 요청입니다.",
        )
        return InputGuardrailRoute(
            decision=decision,
            needs_ai_orchestrator=False,
            confidence="high",
            features=features,
            reason=decision.reason or "",
        )

    if features.has_git_context_intent:
        decision = ClassifiedRequest(
            kind=RequestClassification.GIT_CONTEXT_CHAT,
            should_create_job=False,
            reason="Git MCP로 조회할 수 있는 repo, PR, issue, Actions 맥락 요청입니다.",
        )
        return InputGuardrailRoute(
            decision=decision,
            needs_ai_orchestrator=False,
            confidence="high",
            features=features,
            reason=decision.reason or "",
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
            reply_text=build_needs_repo_message(allowed_repo_keys),
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
        reply_text=build_needs_repo_message(allowed_keys),
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
    has_github_url = GITHUB_URL_PATTERN.search(normalized) is not None
    if has_url:
        matched_terms.append("url")
    if has_github_url:
        matched_terms.append("github-url")

    has_web_intent = _has_any(lowered, WEB_ANALYSIS_KEYWORDS, matched_terms)
    has_write_intent = _has_write_intent(lowered, matched_terms)
    has_secret_risk = _has_any(lowered, SECRET_KEYWORDS, matched_terms)
    has_repo_target = repo_key is not None or _has_any(lowered, REPO_TARGET_KEYWORDS, matched_terms)
    has_notion_url = NOTION_URL_PATTERN.search(normalized) is not None
    has_notion_keyword = _has_any(lowered, NOTION_KEYWORDS, matched_terms)
    has_notion_intent = has_notion_url or (has_notion_keyword and not has_repo_target)
    has_notion_write_intent = has_notion_keyword and _has_any(lowered, NOTION_WRITE_KEYWORDS, matched_terms)
    if has_notion_url:
        matched_terms.append("notion-url")
    has_repo_catalog_intent = _has_repo_catalog_intent(normalized, matched_terms)
    has_git_context_intent = (
        not has_notion_intent
        and not has_repo_catalog_intent
        and (has_github_url or _has_any(lowered, GIT_CONTEXT_KEYWORDS, matched_terms))
    )
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
        has_notion_url=has_notion_url,
        has_notion_intent=has_notion_intent,
        has_notion_write_intent=has_notion_write_intent,
        has_git_context_intent=has_git_context_intent,
        has_repo_catalog_intent=has_repo_catalog_intent,
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


def _has_repo_catalog_intent(text: str, matched_terms: list[str] | None = None) -> bool:
    lowered = text.lower()
    compacted = _compact_text(text)
    for phrase in REPO_CATALOG_PHRASES:
        if phrase.lower() in lowered or _compact_text(phrase) in compacted:
            if matched_terms is not None:
                matched_terms.append(phrase)
            return True
    return False


def _has_write_intent(text: str, matched_terms: list[str] | None = None) -> bool:
    if _has_git_write_intent(text, matched_terms):
        return True

    for keyword in CODE_WRITE_KEYWORDS:
        if keyword in {"pr", "pull request", "풀리퀘", "commit", "커밋"}:
            continue
        if keyword == "수정" and "수정사항" in text:
            continue
        if keyword in text:
            if matched_terms is not None:
                matched_terms.append(keyword)
            return True
    return False


def _has_git_write_intent(text: str, matched_terms: list[str] | None = None) -> bool:
    has_git_target = any(keyword in text for keyword in GIT_WRITE_TARGET_KEYWORDS)
    has_write_action = any(keyword in text for keyword in WRITE_ACTION_KEYWORDS)
    if has_git_target and has_write_action:
        if matched_terms is not None:
            matched_terms.append("git-write")
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
    return _has_write_intent(lowered)


def _looks_like_analysis(text: str) -> bool:
    lowered = text.lower()
    return any(keyword in lowered for keyword in ANALYSIS_KEYWORDS + REPO_TARGET_KEYWORDS)


def _looks_like_repo_request(text: str) -> bool:
    lowered = text.lower()
    has_repo_target = any(keyword in lowered for keyword in REPO_TARGET_KEYWORDS)
    has_analysis = any(keyword in lowered for keyword in ANALYSIS_KEYWORDS)
    return has_repo_target and has_analysis
