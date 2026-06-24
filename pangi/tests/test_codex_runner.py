import asyncio
import json
import sys
from pathlib import Path

import pytest

from pangi.config import clear_settings_cache
from pangi.repository import SQLiteJobRepository
from pangi.infra.codex import CodexChatResponder, CodexExecRunner, CodexRunnerError


def write_script(tmp_path, source: str):
    script = tmp_path / "dummy_codex.py"
    script.write_text(source, encoding="utf-8")
    return script


def test_codex_runner_collects_stdout(tmp_path):
    async def scenario():
        script = write_script(
            tmp_path,
            "import json, pathlib, sys\n"
            "args = sys.argv[1:]\n"
            "output_path = pathlib.Path(args[args.index('--output-last-message') + 1])\n"
            "output_path.write_text(args[-1], encoding='utf-8')\n"
            "print(json.dumps({'type': 'thread.started', 'thread_id': 'codex-thread-123'}))\n",
        )
        runner = CodexExecRunner(
            command_prefix=(sys.executable, str(script)),
            model="gpt-5.5",
            reasoning_effort="high",
        )

        result = await runner.run_read_only(
            workspace_path=tmp_path,
            prompt="분석해줘",
            timeout_seconds=1,
        )

        assert result.stdout == "분석해줘"
        assert result.stderr == ""
        assert result.exit_code == 0
        assert result.timed_out is False
        assert result.codex_session_id == "codex-thread-123"
        assert result.command[-1] == "분석해줘"
        assert result.command[-3:-1] == ("--model", "gpt-5.5")
        assert ("-c", 'model_reasoning_effort="high"') == result.command[-5:-3]
        assert "--json" in result.command
        assert "--output-last-message" in result.command

    asyncio.run(scenario())


def test_codex_runner_collects_stderr_and_exit_code(tmp_path):
    async def scenario():
        script = write_script(
            tmp_path,
            "import sys\nprint('boom', file=sys.stderr)\nsys.exit(7)\n",
        )
        runner = CodexExecRunner(command_prefix=(sys.executable, str(script)))

        result = await runner.run_read_only(
            workspace_path=tmp_path,
            prompt="분석해줘",
            timeout_seconds=1,
        )

        assert result.stderr == "boom"
        assert result.exit_code == 7
        assert result.timed_out is False

    asyncio.run(scenario())


def test_codex_runner_marks_timeout(tmp_path):
    async def scenario():
        script = write_script(
            tmp_path,
            "import time\ntime.sleep(2)\n",
        )
        runner = CodexExecRunner(command_prefix=(sys.executable, str(script)))

        result = await runner.run_read_only(
            workspace_path=tmp_path,
            prompt="분석해줘",
            timeout_seconds=0.01,
        )

        assert result.timed_out is True
        assert result.exit_code is not None

    asyncio.run(scenario())


def test_codex_runner_passes_shell_injection_text_as_single_prompt_arg(tmp_path):
    async def scenario():
        script = write_script(
            tmp_path,
            "import json, pathlib, sys\n"
            "args = sys.argv[1:]\n"
            "output_path = pathlib.Path(args[args.index('--output-last-message') + 1])\n"
            "output_path.write_text(json.dumps(args, ensure_ascii=False), encoding='utf-8')\n"
            "print(json.dumps({'type': 'thread.started', 'thread_id': 'codex-thread-123'}))\n",
        )
        runner = CodexExecRunner(command_prefix=(sys.executable, str(script)))
        prompt = "hello; rm -rf /"

        result = await runner.run_read_only(
            workspace_path=tmp_path,
            prompt=prompt,
            timeout_seconds=1,
        )

        args = json.loads(result.stdout)
        assert args[-1] == prompt
        assert "rm" not in args

    asyncio.run(scenario())


def test_codex_runner_reports_missing_command(tmp_path):
    async def scenario():
        runner = CodexExecRunner(command_prefix=("definitely-missing-pangi-codex-command",))

        with pytest.raises(CodexRunnerError, match="Codex command not found"):
            await runner.run_read_only(
                workspace_path=tmp_path,
                prompt="분석해줘",
                timeout_seconds=1,
            )

    asyncio.run(scenario())


def test_codex_runner_resume_reuses_existing_session_id(tmp_path):
    async def scenario():
        script = write_script(
            tmp_path,
            "import json, pathlib, sys\n"
            "args = sys.argv[1:]\n"
            "output_path = pathlib.Path(args[args.index('--output-last-message') + 1])\n"
            "output_path.write_text(json.dumps(args, ensure_ascii=False), encoding='utf-8')\n",
        )
        runner = CodexExecRunner(command_prefix=(sys.executable, str(script)))

        result = await runner.run_read_only(
            workspace_path=tmp_path,
            prompt="안녕",
            timeout_seconds=1,
            resume_session_id="codex-thread-123",
        )

        args = json.loads(result.stdout)
        assert args[:5] == [
            "resume",
            "codex-thread-123",
            "--skip-git-repo-check",
            "--json",
            "--output-last-message",
        ]
        assert result.codex_session_id == "codex-thread-123"

    asyncio.run(scenario())


def test_codex_chat_responder_uses_thread_workspace_and_resumes_same_session(tmp_path, monkeypatch):
    async def scenario():
        source_root = tmp_path / "sources"
        worktree_root = tmp_path / "worktrees"
        monkeypatch.setenv("SLACK_SIGNING_SECRET", "placeholder-signing-secret")
        monkeypatch.setenv("SLACK_BOT_TOKEN", "placeholder-bot-token")
        monkeypatch.setenv("SLACK_ALLOWED_USER_IDS", "U123")
        monkeypatch.setenv("SLACK_ALLOWED_CHANNEL_IDS", "C123")
        (source_root / "PopPang-iOS").mkdir(parents=True, exist_ok=True)
        monkeypatch.setenv("PANGI_WORKTREE_ROOT", str(worktree_root))
        monkeypatch.setenv("PANGI_SOURCE_REPO_ROOT", str(source_root))
        clear_settings_cache()

        repository = SQLiteJobRepository(tmp_path / "pangi.sqlite3")
        slack_thread = repository.get_or_create_thread(team_id="T123", channel_id="C123", thread_ts="1710000000.000001")
        script = write_script(
            tmp_path,
            "import json, pathlib, sys\n"
            "args = sys.argv[1:]\n"
            "output_path = pathlib.Path(args[args.index('--output-last-message') + 1])\n"
            "output_path.write_text(json.dumps(args, ensure_ascii=False), encoding='utf-8')\n"
            "if 'resume' not in args:\n"
            "    print(json.dumps({'type': 'thread.started', 'thread_id': 'codex-thread-123'}))\n",
        )
        responder = CodexChatResponder(command_prefix=(sys.executable, str(script)), repository=repository)

        first_response = await responder.respond(
            slack_thread=slack_thread,
            text="안녕",
            user_id="U123",
            channel_id="C123",
            thread_ts="1710000000.000001",
        )
        second_response = await responder.respond(
            slack_thread=slack_thread,
            text="이어서 설명해줘",
            user_id="U123",
            channel_id="C123",
            thread_ts="1710000000.000001",
        )

        first_args = json.loads(first_response)
        second_args = json.loads(second_response)
        assert first_args[:6] == [
            "-C",
            str((worktree_root / "_threads" / slack_thread.id).resolve(strict=False)),
            "--skip-git-repo-check",
            "--sandbox",
            "read-only",
            "--json",
        ]
        assert second_args[:2] == ["resume", "codex-thread-123"]
        assert "팡이 공통 스타일" in first_args[-1]
        assert "일반 대화 모드" in first_args[-1]
        assert "인사와 자기소개" in first_args[-1]
        assert "단순 인사" in first_args[-1]
        assert "기능소개와 자기소개" in first_args[-1]
        assert "PopPang 팀의 Slack AI 동료" in first_args[-1]
        assert "Markdown을 적당히 사용" in first_args[-1]
        assert "*핵심*" in first_args[-1]
        assert "PopPang 팀의 GitHub와 Notion 문서" in first_args[-1]
        assert "GitHub를 읽고 코드, PR, 커밋, 장애 원인" in first_args[-1]
        assert "Notion 문서와 회의록을 읽고 결정사항" in first_args[-1]
        assert "타 팀원의 작업 현황" in first_args[-1]
        assert "불가피하게 회의에 참석하지 못해도 팡이가 회의 내용을 요약" in first_args[-1]
        assert "추후 Notion" not in first_args[-1]
        assert "Notion 준비 중" not in first_args[-1]
        assert "제품, 디자인, 커밋 문구 목록으로 대체하지 않습니다" in first_args[-1]
        assert "코드 수정, PR 생성, issue 생성/수정, commit, push, merge, 배포는 아직 직접 실행하지 않습니다" in first_args[-1]
        assert "GitHub token 권한이 있더라도 지금은 읽기와 설명만 가능" in first_args[-1]
        assert "사용자 메시지:\n안녕" in first_args[-1]
        assert repository.get_active_codex_session(slack_thread.id).codex_thread_id == "codex-thread-123"

    asyncio.run(scenario())
    clear_settings_cache()
