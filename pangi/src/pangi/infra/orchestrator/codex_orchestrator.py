from __future__ import annotations

import asyncio
import json
import tempfile
from dataclasses import dataclass
from pathlib import Path
from contextlib import suppress
from typing import Any

from pangi.config import get_settings
from pangi.prompts.loader import load_prompt
from pangi.usecase.input_guardrail import (
    decide_guarded_request,
    enforce_orchestrator_decision,
    route_request_input,
)
from pangi.usecase.ports import RequestOrchestrator
from pangi.usecase.request_decision import (
    ClassifiedRequest,
    RequestClassification,
)


DEFAULT_CODEX_ORCHESTRATOR_COMMAND = ("codex", "exec")
ORCHESTRATOR_PROMPT_NAME = "orchestrator.md"
ORCHESTRATOR_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "classification": {
            "type": "string",
            "enum": [classification.value for classification in RequestClassification],
        },
        "should_create_job": {"type": "boolean"},
        "repo_key": {"type": ["string", "null"]},
        "reply_text": {"type": ["string", "null"]},
        "reason": {"type": ["string", "null"]},
    },
    "required": ["classification", "should_create_job", "repo_key", "reply_text", "reason"],
}


class CodexRequestOrchestratorError(RuntimeError):
    pass


class DeterministicRequestOrchestrator:
    async def decide(self, *, text: str, allowed_repo_keys: tuple[str, ...]) -> ClassifiedRequest:
        return decide_guarded_request(text, allowed_repo_keys=allowed_repo_keys)


@dataclass(frozen=True)
class GuardedRequestOrchestrator:
    orchestrator: RequestOrchestrator

    async def decide(self, *, text: str, allowed_repo_keys: tuple[str, ...]) -> ClassifiedRequest:
        guardrail_route = route_request_input(text, allowed_repo_keys=allowed_repo_keys)
        if guardrail_route.decision is not None and not guardrail_route.needs_ai_orchestrator:
            return guardrail_route.decision

        decision = await self.orchestrator.decide(text=text, allowed_repo_keys=allowed_repo_keys)
        return enforce_orchestrator_decision(
            decision,
            text=text,
            allowed_repo_keys=allowed_repo_keys,
        )


@dataclass(frozen=True)
class CodexRequestOrchestrator:
    command_prefix: tuple[str, ...] = DEFAULT_CODEX_ORCHESTRATOR_COMMAND
    model: str | None = None
    timeout_seconds: float = 20
    workspace_path: Path | None = None

    async def decide(self, *, text: str, allowed_repo_keys: tuple[str, ...]) -> ClassifiedRequest:
        workspace = self._workspace_path()
        workspace.mkdir(parents=True, exist_ok=True)
        prompt = _build_orchestrator_prompt(text=text, allowed_repo_keys=allowed_repo_keys)

        with tempfile.TemporaryDirectory(prefix="pangi-orchestrator-") as temp_dir:
            temp_path = Path(temp_dir)
            schema_path = temp_path / "decision.schema.json"
            output_path = temp_path / "decision.json"
            schema_path.write_text(json.dumps(ORCHESTRATOR_SCHEMA), encoding="utf-8")

            command = self._command(
                workspace=workspace,
                schema_path=schema_path,
                output_path=output_path,
                prompt=prompt,
            )
            stdout, stderr, returncode = await self._run(command=command, cwd=workspace)
            if returncode != 0:
                detail = stderr.strip() or stdout.strip() or f"exit code {returncode}"
                raise CodexRequestOrchestratorError(f"Codex orchestrator failed: {detail}")

            output_text = output_path.read_text(encoding="utf-8").strip() if output_path.is_file() else ""
            if not output_text:
                output_text = stdout.strip()
            return _parse_decision(output_text)

    def _workspace_path(self) -> Path:
        if self.workspace_path is not None:
            return self.workspace_path
        settings = get_settings()
        if settings.chat_workspace_root is None:
            raise CodexRequestOrchestratorError("PANGI_CHAT_WORKSPACE_ROOT is not configured")
        return settings.chat_workspace_root

    def _command(self, *, workspace: Path, schema_path: Path, output_path: Path, prompt: str) -> tuple[str, ...]:
        command = [
            *self.command_prefix,
            "-C",
            str(workspace),
            "--skip-git-repo-check",
            "--sandbox",
            "read-only",
            "--output-schema",
            str(schema_path),
            "--output-last-message",
            str(output_path),
        ]
        if self.model:
            command.extend(("--model", self.model))
        command.append(prompt)
        return tuple(command)

    async def _run(self, *, command: tuple[str, ...], cwd: Path) -> tuple[str, str, int | None]:
        try:
            process = await asyncio.create_subprocess_exec(
                *command,
                cwd=str(cwd),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except FileNotFoundError as error:
            raise CodexRequestOrchestratorError(f"Codex command not found: {self.command_prefix[0]}") from error

        try:
            stdout_bytes, stderr_bytes = await asyncio.wait_for(
                process.communicate(),
                timeout=self.timeout_seconds,
            )
        except TimeoutError as error:
            with suppress(ProcessLookupError):
                process.terminate()
            try:
                await asyncio.wait_for(process.wait(), timeout=2)
            except TimeoutError:
                with suppress(ProcessLookupError):
                    process.kill()
                await process.wait()
            raise CodexRequestOrchestratorError("Codex orchestrator timed out") from error

        return (
            stdout_bytes.decode("utf-8", errors="replace"),
            stderr_bytes.decode("utf-8", errors="replace"),
            process.returncode,
        )


def _build_orchestrator_prompt(*, text: str, allowed_repo_keys: tuple[str, ...]) -> str:
    repo_list = ", ".join(allowed_repo_keys) or "(none)"
    return f"""\
{_load_orchestrator_instructions()}

출력은 JSON schema에 맞는 객체 하나만 반환합니다.

Allowed repo keys:
{repo_list}

Slack message:
{text}
"""


def _load_orchestrator_instructions() -> str:
    return load_prompt(ORCHESTRATOR_PROMPT_NAME)


def _parse_decision(output_text: str) -> ClassifiedRequest:
    try:
        data = json.loads(_strip_json_fence(output_text))
    except json.JSONDecodeError as error:
        raise CodexRequestOrchestratorError("Codex orchestrator returned non-JSON output") from error

    return ClassifiedRequest(
        kind=RequestClassification(data["classification"]),
        should_create_job=bool(data["should_create_job"]),
        repo_key=data.get("repo_key"),
        reply_text=data.get("reply_text"),
        reason=data.get("reason"),
    )


def _strip_json_fence(output_text: str) -> str:
    stripped = output_text.strip()
    if not stripped.startswith("```"):
        return stripped

    lines = stripped.splitlines()
    if lines and lines[0].startswith("```"):
        lines = lines[1:]
    if lines and lines[-1].strip() == "```":
        lines = lines[:-1]
    return "\n".join(lines).strip()


_request_orchestrator: RequestOrchestrator | None = None


def get_request_orchestrator() -> RequestOrchestrator:
    global _request_orchestrator
    if _request_orchestrator is not None:
        return _request_orchestrator

    settings = get_settings()
    _request_orchestrator = GuardedRequestOrchestrator(
        CodexRequestOrchestrator(
            model=settings.orchestrator_model,
            timeout_seconds=settings.orchestrator_timeout_seconds,
        )
    )
    return _request_orchestrator


def set_request_orchestrator(orchestrator: RequestOrchestrator | None) -> None:
    global _request_orchestrator
    _request_orchestrator = orchestrator
