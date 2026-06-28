from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path

from pangi.domain import JobStatus, ThreadMessageRole
from pangi.evaluations.models import EvalCase, EvalExecutionResult
from pangi.evaluations.trace import TraceRecorder
from pangi.infra.orchestrator.codex_orchestrator import (
    DeterministicRequestOrchestrator,
    GuardedRequestOrchestrator,
)
from pangi.repository import SQLiteJobRepository
from pangi.usecase.git_context import GitContext, GitContextSource, GitRepoCatalog, GitRepoCatalogItem
from pangi.usecase.input_guardrail import route_request_input
from pangi.usecase.notion_context import NotionContext, NotionContextSource
from pangi.usecase.ports import CodexExecutionResult, ThreadWorkspaceContext
from pangi.usecase.request_decision import ClassifiedRequest, RequestClassification
from pangi.usecase.run_analysis_job import AnalysisJobFailed, AnalysisJobTimedOut, RunAnalysisJobUseCase
from pangi.usecase.submit_slack_request import SubmitSlackRequestInput, SubmitSlackRequestUseCase


async def execute_eval_case(case: EvalCase) -> EvalExecutionResult:
    with tempfile.TemporaryDirectory(prefix=f"pangi-eval-{case.id}-") as temp_dir:
        temp_path = Path(temp_dir)
        trace = TraceRecorder()
        repository = SQLiteJobRepository(temp_path / "pangi.sqlite3")
        queue = TracingQueue(trace)
        slack = TracingSlackNotifier(trace)
        tasks: list[asyncio.Task[None]] = []

        if case.thread_context:
            thread = repository.get_or_create_thread(team_id="T-EVAL", channel_id="C-EVAL", thread_ts="1710000000.000001")
            repository.append_thread_message(
                slack_thread_id=thread.id,
                role=ThreadMessageRole.USER,
                text=case.thread_context,
                event_id=f"{case.id}:context",
            )

        orchestrator = _orchestrator_for_case(case, trace=trace)
        use_case = SubmitSlackRequestUseCase(
            repository=repository,
            job_queue=queue,
            slack_notifier=slack,
            request_orchestrator=orchestrator,
            chat_responder=TracingChatResponder(trace),
            allowed_repo_keys=case.allowed_repo_keys,
            local_repo_keys=case.local_repo_keys,
            notion_context_provider=TracingNotionContextProvider(trace, markdown=case.notion_context_markdown),
            git_context_provider=TracingGitContextProvider(trace, markdown=case.git_context_markdown),
            background_runner=lambda task: tasks.append(asyncio.create_task(task)),
        )

        result = await use_case.execute(
            SubmitSlackRequestInput(
                team_id="T-EVAL",
                channel_id="C-EVAL",
                user_id="U-EVAL",
                text=case.input,
                thread_ts="1710000000.000001",
                event_id=f"eval:{case.id}",
                message_ts="1710000000.000002",
            )
        )
        if tasks:
            await asyncio.gather(*tasks)

        if result.job_id:
            trace.emit("job.create", job_id=result.job_id)
            job = repository.get_job(result.job_id)
            if job is not None:
                runner = RunAnalysisJobUseCase(
                    repository=repository,
                    worktree_manager=TracingWorktreeManager(trace, root=temp_path),
                    codex_runner=TracingCodexRunner(trace, stdout=case.codex_stdout),
                    slack_notifier=slack,
                    timeout_seconds=5,
                    session_idle_timeout_seconds=3600,
                )
                repository.update_job_status(job.id, JobStatus.RUNNING)
                try:
                    await runner.execute(job)
                    repository.update_job_status(job.id, JobStatus.SUCCEEDED)
                except AnalysisJobTimedOut:
                    repository.update_job_status(job.id, JobStatus.TIMED_OUT)
                except AnalysisJobFailed:
                    repository.update_job_status(job.id, JobStatus.FAILED)

        job = repository.get_job(result.job_id) if result.job_id else None
        return EvalExecutionResult(
            case=case,
            classification=result.classification,
            job_id=result.job_id,
            job_repo_key=job.repo_key if job else None,
            trace=trace.events,
            slack_messages=tuple(message["text"] for message in slack.messages),
        )


class TracingQueue:
    def __init__(self, trace: TraceRecorder) -> None:
        self._trace = trace
        self.job_ids: list[str] = []

    async def enqueue(self, job_id: str) -> None:
        self.job_ids.append(job_id)
        self._trace.emit("job_queue.enqueue", job_id=job_id)


class TracingSlackNotifier:
    def __init__(self, trace: TraceRecorder) -> None:
        self._trace = trace
        self.messages: list[dict[str, str | None]] = []

    async def post_message(self, *, channel_id: str, text: str, thread_ts: str | None = None) -> str | None:
        self.messages.append({"channel_id": channel_id, "thread_ts": thread_ts, "text": text})
        self._trace.emit("slack.post_message", channel_id=channel_id, thread_ts=thread_ts)
        return "1710000000.000003"

    async def add_reaction(self, *, channel_id: str, message_ts: str, name: str) -> None:
        self._trace.emit("slack.add_reaction", channel_id=channel_id, message_ts=message_ts, reaction=name)

    async def remove_reaction(self, *, channel_id: str, message_ts: str, name: str) -> None:
        self._trace.emit("slack.remove_reaction", channel_id=channel_id, message_ts=message_ts, reaction=name)


class TracingChatResponder:
    def __init__(self, trace: TraceRecorder) -> None:
        self._trace = trace

    async def respond(
        self,
        *,
        slack_thread,
        text: str,
        user_id: str,
        channel_id: str,
        thread_ts: str,
    ) -> str:
        self._trace.emit("chat.respond", slack_thread_id=slack_thread.id)
        return (
            "## 요약\n"
            "팡이는 허용된 읽기 경로에서 확인한 맥락만 바탕으로 답했습니다.\n\n"
            "## 근거\n"
            "- source: eval-fixture\n"
            "- 확인한 요청: " + text[:160]
        )


class TracingNotionContextProvider:
    def __init__(self, trace: TraceRecorder, *, markdown: str | None = None) -> None:
        self._trace = trace
        self._markdown = markdown

    async def fetch_context(self, *, text: str, user_id: str, channel_id: str, thread_ts: str) -> NotionContext:
        self._trace.emit("notion.fetch_context", channel_id=channel_id)
        return NotionContext(
            markdown=self._markdown or "## Notion 회의록\n배포 결정사항은 read-only fixture입니다.",
            sources=(
                NotionContextSource(
                    notion_id="0123456789abcdef0123456789abcdef",
                    title="Eval Notion Fixture",
                    url="https://example.notion.site/eval",
                ),
            ),
        )


class TracingGitContextProvider:
    def __init__(self, trace: TraceRecorder, *, markdown: str | None = None) -> None:
        self._trace = trace
        self._markdown = markdown

    async def fetch_context(self, *, text: str, user_id: str, channel_id: str, thread_ts: str) -> GitContext:
        self._trace.emit("git.fetch_context", channel_id=channel_id)
        return GitContext(
            markdown=self._markdown or "## PR 3\nread-only PR context fixture입니다.",
            sources=(
                GitContextSource(
                    title="PR 3",
                    source_type="pull_request",
                    url="https://github.com/team-PopPang/Pangi/pull/3",
                ),
            ),
        )

    async def fetch_repo_catalog(self, *, local_repo_keys: tuple[str, ...]) -> GitRepoCatalog:
        self._trace.emit("git.fetch_repo_catalog", local_repo_count=len(local_repo_keys))
        return GitRepoCatalog(
            items=(
                GitRepoCatalogItem(name="PopPang-iOS", status="ready"),
                GitRepoCatalogItem(name="PopPang-BE", status="clone_on_demand"),
            ),
            git_mcp_enabled=True,
            org="team-PopPang",
        )


class TracingWorktreeManager:
    def __init__(self, trace: TraceRecorder, *, root: Path) -> None:
        self._trace = trace
        self._root = root

    async def prepare_thread_repo_workspace(self, *, slack_thread_id: str, repo_key: str) -> ThreadWorkspaceContext:
        workspace_path = self._root / "thread-workspace"
        repo_path = workspace_path / repo_key
        source_path = self._root / "source" / repo_key
        repo_path.mkdir(parents=True, exist_ok=True)
        source_path.mkdir(parents=True, exist_ok=True)
        self._trace.emit("worktree.prepare", slack_thread_id=slack_thread_id, repo_key=repo_key)
        return ThreadWorkspaceContext(
            workspace_path=workspace_path,
            repo_path=repo_path,
            source_repo_path=source_path,
            base_ref="origin/main",
        )

    async def cleanup_thread_workspace(self, *, slack_thread_id: str) -> None:
        self._trace.emit("worktree.cleanup", slack_thread_id=slack_thread_id)


class TracingCodexRunner:
    def __init__(self, trace: TraceRecorder, *, stdout: str | None = None) -> None:
        self._trace = trace
        self._stdout = stdout

    async def run_read_only(
        self,
        *,
        workspace_path: Path,
        prompt: str,
        timeout_seconds: float,
        resume_session_id: str | None = None,
    ) -> CodexExecutionResult:
        command = ("codex", "exec", "--sandbox", "read-only", "{prompt}")
        self._trace.emit(
            "codex.run_read_only",
            workspace_path=str(workspace_path),
            timeout_seconds=timeout_seconds,
            sandbox="read-only",
        )
        return CodexExecutionResult(
            command=command,
            stdout=self._stdout
            or "## 결론\nread-only 분석 완료\n\n## 근거\n- path: PopPang-iOS/App.swift\n- token: sk-live-evaltoken",
            stderr="",
            exit_code=0,
            timed_out=False,
            codex_session_id="eval-codex-session",
            workspace_path=str(workspace_path),
        )

    async def archive_session(self, *, codex_session_id: str) -> None:
        self._trace.emit("codex.archive_session", codex_session_id=codex_session_id)


class TracingRequestOrchestrator:
    def __init__(self, inner, trace: TraceRecorder) -> None:
        self._inner = inner
        self._trace = trace

    async def decide(
        self,
        *,
        text: str,
        allowed_repo_keys: tuple[str, ...],
        thread_context: str = "",
    ) -> ClassifiedRequest:
        route = route_request_input(text, allowed_repo_keys=allowed_repo_keys)
        self._trace.emit(
            "input_guardrail.route",
            needs_ai_orchestrator=route.needs_ai_orchestrator,
            route=route.decision.kind.value if route.decision else "ambiguous",
        )
        decision = await self._inner.decide(
            text=text,
            allowed_repo_keys=allowed_repo_keys,
            thread_context=thread_context,
        )
        self._trace.emit(
            "orchestrator.decide",
            classification=decision.kind.value,
            should_create_job=decision.should_create_job,
            repo_key=decision.repo_key,
        )
        self._trace.emit("policy.enforce", classification=decision.kind.value)
        return decision


class HostileInnerOrchestrator:
    def __init__(self, trace: TraceRecorder, decision: ClassifiedRequest | None) -> None:
        self._trace = trace
        self._decision = decision or ClassifiedRequest(
            kind=RequestClassification.REPO_ANALYSIS,
            should_create_job=True,
            repo_key="PopPang-iOS",
            reason="hostile default",
        )

    async def decide(
        self,
        *,
        text: str,
        allowed_repo_keys: tuple[str, ...],
        thread_context: str = "",
    ) -> ClassifiedRequest:
        self._trace.emit(
            "orchestrator.inner_decide",
            classification=self._decision.kind.value,
            should_create_job=self._decision.should_create_job,
            repo_key=self._decision.repo_key,
        )
        return self._decision


def _orchestrator_for_case(case: EvalCase, *, trace: TraceRecorder) -> TracingRequestOrchestrator:
    if case.mode == "hostile_orchestrator":
        inner = GuardedRequestOrchestrator(HostileInnerOrchestrator(trace, case.hostile_decision))
    else:
        inner = GuardedRequestOrchestrator(DeterministicRequestOrchestrator())
    return TracingRequestOrchestrator(inner, trace)
