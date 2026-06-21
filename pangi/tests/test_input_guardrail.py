import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from pangi.usecase.input_guardrail import (  # noqa: E402
    decide_guarded_request,
    enforce_orchestrator_decision,
    extract_request_features,
    guard_request_input,
    route_request_input,
)
from pangi.usecase.request_decision import ClassifiedRequest, RequestClassification  # noqa: E402


def test_guardrail_blocks_url_analysis_before_orchestrator():
    result = guard_request_input("https://example.com 기사 요약해줘")

    assert result is not None
    assert result.kind == RequestClassification.BLOCKED_WEB_ANALYSIS
    assert result.reply_text is not None


def test_guardrail_blocks_web_search_before_orchestrator():
    result = guard_request_input("인터넷에서 최신 iOS 정책 찾아줘")

    assert result is not None
    assert result.kind == RequestClassification.BLOCKED_WEB_ANALYSIS


def test_guardrail_blocks_edit_request_before_orchestrator():
    result = guard_request_input("PopPang-iOS 수정해줘", allowed_repo_keys=("PopPang-iOS",))

    assert result is not None
    assert result.kind == RequestClassification.UNSUPPORTED
    assert result.should_create_job is False
    assert result.repo_key == "PopPang-iOS"


def test_guardrail_allows_safe_request_to_reach_orchestrator():
    result = guard_request_input("PopPang-iOS 구조 분석해줘", allowed_repo_keys=("PopPang-iOS",))

    assert result is None


def test_deterministic_orchestrator_fallback_routes_repo_analysis():
    result = decide_guarded_request(
        "PopPang-iOS 구조 분석해줘",
        allowed_repo_keys=("PopPang-iOS",),
    )

    assert result.kind == RequestClassification.REPO_ANALYSIS
    assert result.should_create_job is True
    assert result.repo_key == "PopPang-iOS"
    assert result.reply_text is None


def test_input_guardrail_routes_repo_analysis_without_ai():
    route = route_request_input(
        "PopPang iOS 로그인 흐름 봐줘",
        allowed_repo_keys=("PopPang-iOS",),
    )

    assert route.needs_ai_orchestrator is False
    assert route.confidence == "high"
    assert route.decision is not None
    assert route.decision.kind == RequestClassification.REPO_ANALYSIS
    assert route.decision.repo_key == "PopPang-iOS"


def test_input_guardrail_routes_chat_without_ai():
    route = route_request_input("안녕 팡이야", allowed_repo_keys=("PopPang-iOS",))

    assert route.needs_ai_orchestrator is False
    assert route.decision is not None
    assert route.decision.kind == RequestClassification.CODEX_CHAT


def test_input_guardrail_marks_ambiguous_reference_for_ai_orchestrator():
    route = route_request_input("어제 말한 그 흐름 좀 봐줘", allowed_repo_keys=("PopPang-iOS",))

    assert route.decision is None
    assert route.needs_ai_orchestrator is True
    assert route.confidence == "low"


def test_input_guardrail_blocks_secret_request_without_ai():
    route = route_request_input("PopPang-iOS .env 토큰 읽어줘", allowed_repo_keys=("PopPang-iOS",))

    assert route.needs_ai_orchestrator is False
    assert route.decision is not None
    assert route.decision.kind == RequestClassification.UNSUPPORTED


def test_input_guardrail_extracts_features():
    features = extract_request_features(
        "PopPang-iOS 구조 분석해줘",
        allowed_repo_keys=("PopPang-iOS",),
    )

    assert features.repo_key == "PopPang-iOS"
    assert features.has_analysis_intent is True
    assert features.has_repo_target is True


def test_deterministic_orchestrator_fallback_keeps_plain_analysis_as_chat():
    result = decide_guarded_request("이거 분석해줘", allowed_repo_keys=("PopPang-iOS",))

    assert result.kind == RequestClassification.CODEX_CHAT
    assert result.should_create_job is False


def test_deterministic_orchestrator_fallback_asks_for_missing_repo():
    result = decide_guarded_request("레포 구조 분석해줘", allowed_repo_keys=("PopPang-iOS",))

    assert result.kind == RequestClassification.NEEDS_REPO
    assert result.should_create_job is False


def test_guardrail_enforcement_rejects_ai_selected_repo_not_in_original_text():
    decision = ClassifiedRequest(
        kind=RequestClassification.REPO_ANALYSIS,
        should_create_job=True,
        repo_key="PopPang-iOS",
    )

    result = enforce_orchestrator_decision(
        decision,
        text="레포 구조 분석해줘",
        allowed_repo_keys=("PopPang-iOS",),
    )

    assert result.kind == RequestClassification.NEEDS_REPO
    assert result.should_create_job is False
    assert result.repo_key is None


def test_guardrail_enforcement_allows_repo_explicitly_present_in_original_text():
    decision = ClassifiedRequest(
        kind=RequestClassification.REPO_ANALYSIS,
        should_create_job=True,
        repo_key="PopPang-iOS",
    )

    result = enforce_orchestrator_decision(
        decision,
        text="PopPang-iOS 구조 분석해줘",
        allowed_repo_keys=("PopPang-iOS",),
    )

    assert result.kind == RequestClassification.REPO_ANALYSIS
    assert result.should_create_job is True
    assert result.repo_key == "PopPang-iOS"
