import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from pangi.infra.orchestrator.openai_orchestrator import (  # noqa: E402
    GuardedRequestOrchestrator,
    OpenAIRequestOrchestrator,
    _load_orchestrator_instructions,
)
from pangi.usecase.request_decision import ClassifiedRequest, RequestClassification  # noqa: E402


class FakeInnerOrchestrator:
    def __init__(self, decision):
        self.decision = decision
        self.calls = []

    async def decide(self, *, text: str, allowed_repo_keys: tuple[str, ...]):
        self.calls.append({"text": text, "allowed_repo_keys": allowed_repo_keys})
        return self.decision


def test_openai_orchestrator_loads_instructions_from_markdown():
    orchestrator = OpenAIRequestOrchestrator(
        api_key="test-api-key",
        model="test-model",
        reasoning_effort="medium",
        service_tier="default",
    )

    payload = orchestrator._payload("PopPang-iOS 구조 분석해줘", ("PopPang-iOS",))

    assert payload["instructions"] == _load_orchestrator_instructions()
    assert "팡이 요청 오케스트레이터" in payload["instructions"]
    assert "repo_analysis" in payload["instructions"]
    assert payload["input"] == "Allowed repo keys: PopPang-iOS\nSlack message:\nPopPang-iOS 구조 분석해줘"


def test_guarded_orchestrator_blocks_web_analysis_before_inner_orchestrator():
    async def scenario():
        inner = FakeInnerOrchestrator(
            ClassifiedRequest(
                kind=RequestClassification.REPO_ANALYSIS,
                should_create_job=True,
                repo_key="PopPang-iOS",
            )
        )
        orchestrator = GuardedRequestOrchestrator(inner)

        result = await orchestrator.decide(
            text="https://example.com 분석해줘",
            allowed_repo_keys=("PopPang-iOS",),
        )

        assert result.kind == RequestClassification.BLOCKED_WEB_ANALYSIS
        assert result.should_create_job is False
        assert inner.calls == []

    asyncio.run(scenario())


def test_guarded_orchestrator_rejects_inner_repo_choice_when_original_text_has_no_repo():
    async def scenario():
        inner = FakeInnerOrchestrator(
            ClassifiedRequest(
                kind=RequestClassification.REPO_ANALYSIS,
                should_create_job=True,
                repo_key="PopPang-iOS",
            )
        )
        orchestrator = GuardedRequestOrchestrator(inner)

        result = await orchestrator.decide(
            text="레포 구조 분석해줘",
            allowed_repo_keys=("PopPang-iOS",),
        )

        assert result.kind == RequestClassification.NEEDS_REPO
        assert result.should_create_job is False
        assert inner.calls == [
            {
                "text": "레포 구조 분석해줘",
                "allowed_repo_keys": ("PopPang-iOS",),
            }
        ]

    asyncio.run(scenario())
