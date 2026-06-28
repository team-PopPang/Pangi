from __future__ import annotations

import asyncio
import logging

from pangi.evaluations.operations import EvalSuiteRun, run_eval_suite
from pangi.repository import JobRepository
from pangi.usecase.ports import SlackNotifier


logger = logging.getLogger(__name__)


class InProcessEvalScheduler:
    def __init__(
        self,
        *,
        repository: JobRepository,
        interval_seconds: int,
        slack_notifier: SlackNotifier | None = None,
        alert_channel_id: str | None = None,
    ) -> None:
        self._repository = repository
        self._interval_seconds = interval_seconds
        self._slack_notifier = slack_notifier
        self._alert_channel_id = alert_channel_id
        self._task: asyncio.Task[None] | None = None
        self._stop_event = asyncio.Event()

    async def start(self) -> None:
        if self._task is not None:
            return
        self._stop_event.clear()
        self._task = asyncio.create_task(self._run_loop(), name="pangi-eval-scheduler")

    async def stop(self) -> None:
        if self._task is None:
            return
        self._stop_event.set()
        self._task.cancel()
        await asyncio.gather(self._task, return_exceptions=True)
        self._task = None

    async def run_once(self) -> EvalSuiteRun:
        suite_run = await run_eval_suite(
            repository=self._repository,
            suite_name="scheduled",
            persist=True,
            include_approved_red_team=True,
        )
        if not suite_run.result.passed:
            await self._notify_failure(suite_run)
        return suite_run

    async def _run_loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                await asyncio.wait_for(self._stop_event.wait(), timeout=self._interval_seconds)
                continue
            except TimeoutError:
                pass
            try:
                await self.run_once()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Failed to run scheduled Eval suite")

    async def _notify_failure(self, suite_run: EvalSuiteRun) -> None:
        if self._slack_notifier is None or not self._alert_channel_id:
            return
        failed_cases = [
            result.case.id
            for result in suite_run.result.results
            if not result.passed
        ]
        run_id = suite_run.persisted_run.id if suite_run.persisted_run else "-"
        text = (
            "Pangi Eval 실패\n"
            f"- run_id: {run_id}\n"
            f"- passed: {suite_run.result.passed_count}/{suite_run.result.total_count}\n"
            f"- failed_cases: {', '.join(failed_cases[:8])}"
        )
        await self._slack_notifier.post_message(channel_id=self._alert_channel_id, text=text)
