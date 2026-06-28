from contextlib import asynccontextmanager
from collections.abc import AsyncIterator
import asyncio
import logging

from fastapi import FastAPI

from pangi.config import get_settings
from pangi.domain.models import AgentJob, JobStatus
from pangi.evaluations.scheduler import InProcessEvalScheduler
from pangi.infra.admin import router as admin_router
from pangi.infra.codex import CodexExecRunner, get_chat_responder
from pangi.infra.git import get_worktree_manager
from pangi.infra.git_mcp import get_git_context_provider
from pangi.infra.notion import get_notion_context_provider
from pangi.infra.orchestrator import get_request_orchestrator
from pangi.infra.queue import InProcessJobQueue, set_job_queue
from pangi.infra.scheduler import InProcessScheduler, ScheduledTaskRunner
from pangi.infra.slack import router as slack_router
from pangi.infra.slack.client import get_slack_client
from pangi.repository import get_job_repository
from pangi.usecase.codex_session import CodexSessionService
from pangi.usecase.output_guardrail import prepare_output_markdown
from pangi.usecase.run_analysis_job import RunAnalysisJobUseCase


logger = logging.getLogger(__name__)
SESSION_SWEEP_INTERVAL_SECONDS = 300


async def slack_progress_hook(job: AgentJob, _status: JobStatus, message: str) -> None:
    if _status == JobStatus.SUCCEEDED:
        return
    try:
        await get_slack_client().post_message(
            channel_id=job.slack_channel_id,
            thread_ts=job.slack_thread_ts,
            text=prepare_output_markdown(f"{message} job_id: {job.id}"),
        )
    except Exception as error:
        logger.warning("Failed to post Slack progress message for job %s: %s", job.id, error)
        return


async def _session_sweeper(
    *,
    session_service: CodexSessionService,
    worktree_manager,
) -> None:
    while True:
        try:
            await session_service.expire_due_sessions(
                cleanup_thread_workspace=worktree_manager.cleanup_thread_workspace,
            )
        except Exception:
            logger.exception("Failed to sweep expired Codex sessions")
        await asyncio.sleep(SESSION_SWEEP_INTERVAL_SECONDS)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    repository = get_job_repository()
    slack_client = get_slack_client()
    worktree_manager = get_worktree_manager()
    codex_runner = CodexExecRunner(
        model=settings.analysis_model,
        reasoning_effort=settings.analysis_reasoning_effort,
    )
    analysis_use_case = RunAnalysisJobUseCase(
        repository=repository,
        worktree_manager=worktree_manager,
        codex_runner=codex_runner,
        slack_notifier=slack_client,
        timeout_seconds=settings.job_timeout_seconds,
        session_idle_timeout_seconds=settings.codex_session_idle_timeout_seconds,
    )
    session_service = CodexSessionService(
        repository=repository,
        codex_runner=codex_runner,
        idle_timeout_seconds=settings.codex_session_idle_timeout_seconds,
    )

    queue = InProcessJobQueue(
        repository=repository,
        runner=analysis_use_case.execute,
        progress_hook=slack_progress_hook,
        job_timeout_seconds=settings.job_timeout_seconds + 120,
        max_concurrency=1,
        repo_concurrency=1,
    )
    set_job_queue(queue)
    await queue.start()
    scheduler: InProcessScheduler | None = None
    if settings.scheduler_enabled:
        scheduler_runner = ScheduledTaskRunner(
            repository=repository,
            job_queue=queue,
            slack_notifier=slack_client,
            request_orchestrator=get_request_orchestrator(),
            chat_responder=get_chat_responder(),
            notion_context_provider=get_notion_context_provider(),
            git_context_provider=get_git_context_provider(),
        )
        scheduler = InProcessScheduler(
            runner=scheduler_runner,
            tick_seconds=settings.scheduler_tick_seconds,
        )
        await scheduler.start()
    eval_scheduler: InProcessEvalScheduler | None = None
    if settings.eval_scheduler_enabled:
        eval_scheduler = InProcessEvalScheduler(
            repository=repository,
            slack_notifier=slack_client,
            alert_channel_id=settings.eval_alert_channel_id,
            interval_seconds=settings.eval_scheduler_interval_seconds,
        )
        await eval_scheduler.start()
    sweeper_task = asyncio.create_task(
        _session_sweeper(session_service=session_service, worktree_manager=worktree_manager),
        name="pangi-session-sweeper",
    )
    try:
        yield
    finally:
        if eval_scheduler is not None:
            await eval_scheduler.stop()
        if scheduler is not None:
            await scheduler.stop()
        sweeper_task.cancel()
        await asyncio.gather(sweeper_task, return_exceptions=True)
        await queue.stop()
        set_job_queue(None)


app = FastAPI(title="Pangi", lifespan=lifespan)
app.include_router(admin_router)
app.include_router(slack_router)


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}
