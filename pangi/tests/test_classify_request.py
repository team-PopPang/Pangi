import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from pangi.usecase.classify_request import RequestClassification, classify_request  # noqa: E402


def test_classifies_url_as_blocked_web_analysis():
    result = classify_request("https://example.com 기사 요약해줘")

    assert result.kind == RequestClassification.BLOCKED_WEB_ANALYSIS
    assert result.reply_text is not None


def test_classifies_web_search_request_as_blocked_web_analysis():
    result = classify_request("인터넷에서 최신 iOS 정책 찾아줘")

    assert result.kind == RequestClassification.BLOCKED_WEB_ANALYSIS


def test_classifies_plain_repo_request_as_repo_analysis():
    result = classify_request(
        "PopPang-iOS 구조 분석해줘",
        allowed_repo_keys=("PopPang-iOS",),
    )

    assert result.kind == RequestClassification.REPO_ANALYSIS
    assert result.should_create_job is True
    assert result.repo_key == "PopPang-iOS"
    assert result.reply_text is None


def test_classifies_plain_analysis_as_codex_chat():
    result = classify_request("이거 분석해줘", allowed_repo_keys=("PopPang-iOS",))

    assert result.kind == RequestClassification.CODEX_CHAT
    assert result.should_create_job is False


def test_classifies_repo_analysis_without_repo_key_as_needs_repo():
    result = classify_request("레포 구조 분석해줘", allowed_repo_keys=("PopPang-iOS",))

    assert result.kind == RequestClassification.NEEDS_REPO
    assert result.should_create_job is False


def test_classifies_edit_request_as_unsupported():
    result = classify_request("PopPang-iOS 수정해줘", allowed_repo_keys=("PopPang-iOS",))

    assert result.kind == RequestClassification.UNSUPPORTED
    assert result.should_create_job is False
    assert result.repo_key == "PopPang-iOS"
