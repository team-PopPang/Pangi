import asyncio
import json
import sys

import pytest

from pangi.infra.codex import CodexExecRunner, CodexRunnerError


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
        runner = CodexExecRunner(command_prefix=(sys.executable, str(script)))

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
