from contextlib import asynccontextmanager
from collections.abc import AsyncIterator
import logging

from fastapi import FastAPI

from pangi.config import get_settings
from pangi.domain.models import AgentJob, JobStatus
from pangi.infra.admin import router as admin_router
from pangi.infra.codex import CodexExecRunner
from pangi.infra.git import get_worktree_manager
from pangi.infra.queue import InProcessJobQueue, set_job_queue
from pangi.infra.slack import router as slack_router
from pangi.infra.slack.client import get_slack_client
from pangi.repository import get_job_repository
from pangi.usecase.output_guardrail import prepare_output_markdown
from pangi.usecase.run_analysis_job import RunAnalysisJobUseCase


logger = logging.getLogger(__name__)


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


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    repository = get_job_repository()
    slack_client = get_slack_client()
    analysis_use_case = RunAnalysisJobUseCase(
        repository=repository,
        worktree_manager=get_worktree_manager(),
        codex_runner=CodexExecRunner(
            model=settings.analysis_model,
            reasoning_effort=settings.analysis_reasoning_effort,
        ),
        slack_notifier=slack_client,
        timeout_seconds=settings.job_timeout_seconds,
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
    try:
        yield
    finally:
        await queue.stop()
        set_job_queue(None)


app = FastAPI(title="Pangi", lifespan=lifespan)
app.include_router(admin_router)
app.include_router(slack_router)


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}
