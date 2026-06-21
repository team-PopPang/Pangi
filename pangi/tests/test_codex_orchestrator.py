import asyncio
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from pangi.infra.orchestrator.codex_orchestrator import (  # noqa: E402
    CodexRequestOrchestratorError,
    CodexRequestOrchestrator,
    GuardedRequestOrchestrator,
    _build_orchestrator_prompt,
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


def test_codex_orchestrator_loads_instructions_from_markdown():
    prompt = _build_orchestrator_prompt(
        text="PopPang-iOS 구조 분석해줘",
        allowed_repo_keys=("PopPang-iOS",),
    )

    assert _load_orchestrator_instructions() in prompt
    assert "팡이 요청 오케스트레이터" in prompt
    assert "repo_analysis" in prompt
    assert "Allowed repo keys:\nPopPang-iOS" in prompt
    assert "Slack message:\nPopPang-iOS 구조 분석해줘" in prompt


def test_codex_orchestrator_builds_codex_exec_command(tmp_path):
    orchestrator = CodexRequestOrchestrator(
        command_prefix=("codex", "exec"),
        model="test-model",
        workspace_path=tmp_path,
    )

    command = orchestrator._command(
        workspace=tmp_path,
        schema_path=tmp_path / "schema.json",
        output_path=tmp_path / "decision.json",
        prompt="분류해줘",
    )

    assert command[:8] == (
        "codex",
        "exec",
        "-C",
        str(tmp_path),
        "--skip-git-repo-check",
        "--sandbox",
        "read-only",
        "--output-schema",
    )
    assert "--output-last-message" in command
    assert ("--model", "test-model") == command[-3:-1]
    assert command[-1] == "분류해줘"


def test_codex_orchestrator_parses_codex_output_file(tmp_path):
    async def scenario():
        script = tmp_path / "dummy_codex.py"
        script.write_text(
            "\n".join(
                [
                    "import json, sys",
                    "args = sys.argv[1:]",
                    "output_path = args[args.index('--output-last-message') + 1]",
                    "decision = {",
                    "    'classification': 'repo_analysis',",
                    "    'should_create_job': True,",
                    "    'repo_key': 'PopPang-iOS',",
                    "    'reply_text': None,",
                    "    'reason': 'test',",
                    "}",
                    "with open(output_path, 'w', encoding='utf-8') as file:",
                    "    json.dump(decision, file)",
                ]
            ),
            encoding="utf-8",
        )
        orchestrator = CodexRequestOrchestrator(
            command_prefix=(sys.executable, str(script)),
            model="test-model",
            timeout_seconds=1,
            workspace_path=tmp_path,
        )

        result = await orchestrator.decide(
            text="PopPang-iOS 구조 분석해줘",
            allowed_repo_keys=("PopPang-iOS",),
        )

        assert result.kind == RequestClassification.REPO_ANALYSIS
        assert result.should_create_job is True
        assert result.repo_key == "PopPang-iOS"

    asyncio.run(scenario())


def test_codex_orchestrator_timeout_ignores_already_exited_process(tmp_path, monkeypatch):
    class FakeProcess:
        returncode = None

        async def communicate(self):
            await asyncio.sleep(1)
            return b"", b""

        def terminate(self):
            raise ProcessLookupError

        def kill(self):
            raise ProcessLookupError

        async def wait(self):
            return 0

    async def fake_create_subprocess_exec(*args, **kwargs):
        return FakeProcess()

    async def scenario():
        monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create_subprocess_exec)
        orchestrator = CodexRequestOrchestrator(
            command_prefix=("codex", "exec"),
            timeout_seconds=0.01,
            workspace_path=tmp_path,
        )

        with pytest.raises(CodexRequestOrchestratorError, match="timed out"):
            await orchestrator.decide(text="안녕", allowed_repo_keys=("PopPang-iOS",))

    asyncio.run(scenario())


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


def test_guarded_orchestrator_routes_plain_chat_without_inner_orchestrator():
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
            text="안녕 팡이야",
            allowed_repo_keys=("PopPang-iOS",),
        )

        assert result.kind == RequestClassification.CODEX_CHAT
        assert result.should_create_job is False
        assert inner.calls == []

    asyncio.run(scenario())


def test_guarded_orchestrator_routes_explicit_repo_analysis_without_inner_orchestrator():
    async def scenario():
        inner = FakeInnerOrchestrator(
            ClassifiedRequest(
                kind=RequestClassification.CODEX_CHAT,
                should_create_job=False,
            )
        )
        orchestrator = GuardedRequestOrchestrator(inner)

        result = await orchestrator.decide(
            text="PopPang-iOS 로그인 흐름 봐줘",
            allowed_repo_keys=("PopPang-iOS",),
        )

        assert result.kind == RequestClassification.REPO_ANALYSIS
        assert result.should_create_job is True
        assert result.repo_key == "PopPang-iOS"
        assert inner.calls == []

    asyncio.run(scenario())


def test_guarded_orchestrator_calls_inner_only_for_ambiguous_request():
    async def scenario():
        inner = FakeInnerOrchestrator(
            ClassifiedRequest(
                kind=RequestClassification.CODEX_CHAT,
                should_create_job=False,
                reason="ambiguous chat",
            )
        )
        orchestrator = GuardedRequestOrchestrator(inner)

        result = await orchestrator.decide(
            text="어제 말한 그 흐름 좀 봐줘",
            allowed_repo_keys=("PopPang-iOS",),
        )

        assert result.kind == RequestClassification.CODEX_CHAT
        assert result.should_create_job is False
        assert inner.calls == [
            {
                "text": "어제 말한 그 흐름 좀 봐줘",
                "allowed_repo_keys": ("PopPang-iOS",),
            }
        ]

    asyncio.run(scenario())


def test_guarded_orchestrator_routes_missing_repo_without_inner_orchestrator():
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
        assert inner.calls == []

    asyncio.run(scenario())
