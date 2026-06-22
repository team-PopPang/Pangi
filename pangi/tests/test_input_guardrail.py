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


def test_guardrail_routes_notion_url_without_web_block():
    route = route_request_input("https://poppang.notion.site/abc123 회의록 읽어줘")

    assert route.needs_ai_orchestrator is False
    assert route.decision is not None
    assert route.decision.kind == RequestClassification.NOTION_CONTEXT_CHAT
    assert route.decision.should_create_job is False
    assert route.features.has_url is True
    assert route.features.has_notion_url is True


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


def test_guardrail_blocks_notion_write_request_before_orchestrator():
    result = guard_request_input("노션에 회의록 추가해줘")

    assert result is not None
    assert result.kind == RequestClassification.UNSUPPORTED
    assert result.should_create_job is False


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


def test_input_guardrail_routes_notion_keyword_without_ai():
    route = route_request_input("노션 회의록에서 결정사항 알려줘", allowed_repo_keys=("PopPang-iOS",))

    assert route.needs_ai_orchestrator is False
    assert route.decision is not None
    assert route.decision.kind == RequestClassification.NOTION_CONTEXT_CHAT
    assert route.features.has_notion_intent is True


def test_input_guardrail_routes_git_context_without_ai():
    route = route_request_input("PopPang-iOS PR 123 요약해줘", allowed_repo_keys=("PopPang-iOS",))

    assert route.needs_ai_orchestrator is False
    assert route.decision is not None
    assert route.decision.kind == RequestClassification.GIT_CONTEXT_CHAT
    assert route.decision.should_create_job is False
    assert route.features.has_git_context_intent is True


def test_input_guardrail_routes_github_url_as_git_context_without_web_block():
    route = route_request_input("https://github.com/team-PopPang/PopPang-FE/pull/123 요약해줘")

    assert route.needs_ai_orchestrator is False
    assert route.decision is not None
    assert route.decision.kind == RequestClassification.GIT_CONTEXT_CHAT
    assert route.features.has_url is True
    assert route.features.has_git_context_intent is True


def test_input_guardrail_routes_repo_catalog_without_ai():
    route = route_request_input("분석 가능한 레포 목록 알려줘", allowed_repo_keys=("PopPang-iOS",))

    assert route.needs_ai_orchestrator is False
    assert route.decision is not None
    assert route.decision.kind == RequestClassification.REPO_CATALOG
    assert route.decision.should_create_job is False
    assert route.features.has_repo_catalog_intent is True


def test_input_guardrail_routes_allowed_repository_list_as_catalog():
    route = route_request_input("허용된 레포지토리 리스트 출력해줘", allowed_repo_keys=("PopPang-iOS",))

    assert route.needs_ai_orchestrator is False
    assert route.decision is not None
    assert route.decision.kind == RequestClassification.REPO_CATALOG
    assert route.decision.should_create_job is False
    assert route.features.has_repo_catalog_intent is True


def test_input_guardrail_routes_poppang_team_repo_output_as_catalog():
    route = route_request_input("팝팡팀 레포 출력해라", allowed_repo_keys=("PopPang-iOS",))

    assert route.needs_ai_orchestrator is False
    assert route.decision is not None
    assert route.decision.kind == RequestClassification.REPO_CATALOG
    assert route.decision.should_create_job is False
    assert route.features.has_repo_catalog_intent is True


def test_input_guardrail_resolves_ios_alias_to_repo_analysis():
    route = route_request_input(
        "ios 팀원이 어떤 UI를 개편하고 있는지 분석해줄래?",
        allowed_repo_keys=("PopPang-iOS", "PopPang-BE"),
    )

    assert route.needs_ai_orchestrator is False
    assert route.decision is not None
    assert route.decision.kind == RequestClassification.REPO_ANALYSIS
    assert route.decision.should_create_job is True
    assert route.decision.repo_key == "PopPang-iOS"
    assert route.features.repo_key == "PopPang-iOS"


def test_input_guardrail_resolves_aos_alias_to_repo_analysis():
    route = route_request_input(
        "aos 로그인 흐름 봐줘",
        allowed_repo_keys=("PopPang-AOS", "PopPang-iOS"),
    )

    assert route.needs_ai_orchestrator is False
    assert route.decision is not None
    assert route.decision.kind == RequestClassification.REPO_ANALYSIS
    assert route.decision.should_create_job is True
    assert route.decision.repo_key == "PopPang-AOS"
    assert route.features.repo_key == "PopPang-AOS"


def test_input_guardrail_resolves_android_repo_for_aos_alias():
    route = route_request_input(
        "aos 코드 구조 분석해줘",
        allowed_repo_keys=("PopPang-Android", "PopPang-iOS"),
    )

    assert route.needs_ai_orchestrator is False
    assert route.decision is not None
    assert route.decision.kind == RequestClassification.REPO_ANALYSIS
    assert route.decision.repo_key == "PopPang-Android"


def test_input_guardrail_does_not_resolve_ambiguous_repo_alias():
    route = route_request_input(
        "ios 로그인 흐름 봐줘",
        allowed_repo_keys=("PopPang-iOS", "Admin-iOS"),
    )

    assert route.needs_ai_orchestrator is False
    assert route.decision is not None
    assert route.decision.kind == RequestClassification.CODEX_CHAT
    assert route.decision.should_create_job is False
    assert route.features.repo_key is None


def test_input_guardrail_blocks_git_write_request_without_ai():
    route = route_request_input("PopPang-iOS PR 생성해줘", allowed_repo_keys=("PopPang-iOS",))

    assert route.needs_ai_orchestrator is False
    assert route.decision is not None
    assert route.decision.kind == RequestClassification.UNSUPPORTED
    assert route.decision.should_create_job is False


def test_input_guardrail_keeps_notion_code_request_as_repo_request():
    route = route_request_input("노션 연동 코드 구조 분석해줘", allowed_repo_keys=("PopPang-iOS",))

    assert route.needs_ai_orchestrator is False
    assert route.decision is not None
    assert route.decision.kind == RequestClassification.NEEDS_REPO
    assert route.features.has_notion_intent is False
    assert route.features.has_repo_target is True


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
    assert "PopPang-iOS" in (result.reply_text or "")


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


def test_guardrail_enforcement_allows_repo_alias_in_original_text():
    decision = ClassifiedRequest(
        kind=RequestClassification.REPO_ANALYSIS,
        should_create_job=True,
        repo_key="PopPang-iOS",
    )

    result = enforce_orchestrator_decision(
        decision,
        text="ios 코드 구조 분석해줘",
        allowed_repo_keys=("PopPang-iOS",),
    )

    assert result.kind == RequestClassification.REPO_ANALYSIS
    assert result.should_create_job is True
    assert result.repo_key == "PopPang-iOS"
