import asyncio
import json
import sys

import pytest

from pangi.config import clear_settings_cache
from pangi.infra.codex import CodexChatResponder, CodexExecRunner, CodexRunnerError


def write_script(tmp_path, source: str):
    script = tmp_path / "dummy_codex.py"
    script.write_text(source, encoding="utf-8")
    return script


def test_codex_runner_collects_stdout(tmp_path):
    async def scenario():
        script = write_script(
            tmp_path,
            "import sys\nprint(sys.argv[-1])\n",
        )
        runner = CodexExecRunner(command_prefix=(sys.executable, str(script)), model="gpt-5.5")

        result = await runner.run_read_only(
            worktree_path=tmp_path,
            prompt="분석해줘",
            timeout_seconds=1,
        )

        assert result.stdout == "분석해줘\n"
        assert result.stderr == ""
        assert result.exit_code == 0
        assert result.timed_out is False
        assert result.command[-1] == "분석해줘"
        assert result.command[-3:-1] == ("--model", "gpt-5.5")
        assert "--ask-for-approval" not in result.command

    asyncio.run(scenario())


def test_codex_runner_collects_stderr_and_exit_code(tmp_path):
    async def scenario():
        script = write_script(
            tmp_path,
            "import sys\nprint('boom', file=sys.stderr)\nsys.exit(7)\n",
        )
        runner = CodexExecRunner(command_prefix=(sys.executable, str(script)))

        result = await runner.run_read_only(
            worktree_path=tmp_path,
            prompt="분석해줘",
            timeout_seconds=1,
        )

        assert result.stderr == "boom\n"
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
            worktree_path=tmp_path,
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
            "import json, sys\nprint(json.dumps(sys.argv[1:], ensure_ascii=False))\n",
        )
        runner = CodexExecRunner(command_prefix=(sys.executable, str(script)))
        prompt = "hello; rm -rf /"

        result = await runner.run_read_only(
            worktree_path=tmp_path,
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
                worktree_path=tmp_path,
                prompt="분석해줘",
                timeout_seconds=1,
            )

    asyncio.run(scenario())


def test_codex_chat_responder_uses_scratch_workspace_and_skip_git_check(tmp_path, monkeypatch):
    async def scenario():
        source_root = tmp_path / "sources"
        worktree_root = tmp_path / "worktrees"
        chat_root = worktree_root / "chat"
        monkeypatch.setenv("SLACK_SIGNING_SECRET", "placeholder-signing-secret")
        monkeypatch.setenv("SLACK_BOT_TOKEN", "placeholder-bot-token")
        monkeypatch.setenv("SLACK_ALLOWED_USER_IDS", "U123")
        monkeypatch.setenv("SLACK_ALLOWED_CHANNEL_IDS", "C123")
        monkeypatch.setenv("PANGI_ALLOWED_REPOS", f"PopPang-iOS={source_root / 'PopPang-iOS'}")
        monkeypatch.setenv("PANGI_WORKTREE_ROOT", str(worktree_root))
        monkeypatch.setenv("PANGI_SOURCE_REPO_ROOT", str(source_root))
        monkeypatch.setenv("PANGI_CHAT_WORKSPACE_ROOT", str(chat_root))
        clear_settings_cache()

        script = write_script(
            tmp_path,
            "import json, sys\nprint(json.dumps(sys.argv[1:], ensure_ascii=False))\n",
        )
        responder = CodexChatResponder(command_prefix=(sys.executable, str(script)))

        response = await responder.respond(
            text="안녕",
            user_id="U123",
            channel_id="C123",
            thread_ts="1710000000.000001",
        )

        args = json.loads(response)
        assert args[:6] == [
            "exec",
            "-C",
            str(chat_root.resolve(strict=False)),
            "--skip-git-repo-check",
            "--sandbox",
            "read-only",
        ]
        assert args[6:8] == ["--model", "gpt-5.4-mini"]
        assert "팡이 공통 스타일" in args[-1]
        assert "일반 대화 모드" in args[-1]
        assert "사용자 메시지:\n안녕" in args[-1]

    asyncio.run(scenario())
    clear_settings_cache()
