"""Protected local Run admission and orchestration handoff."""

from __future__ import annotations

import uuid
from collections.abc import Callable
from datetime import UTC, datetime

from pangi.application.contracts.auth import AuthenticatedPrincipal
from pangi.application.contracts.runs import RunSubmission
from pangi.application.ports.run_queue import (
    RunQueueRuntimeNotifier,
    RunQueueUnavailableError,
)
from pangi.application.ports.runs import RunOperations
from pangi.application.services.input_guardrails import GuardedRunSubmissionService
from pangi.application.services.orchestration_lifecycle import OrchestrationSubmissionService
from pangi.domain.model_routing import DataClass
from pangi.domain.runs import Principal, PrincipalChannel, RunRequest, RunState

Clock = Callable[[], datetime]
IdFactory = Callable[[], str]

_ROUTE_KEY = "api.runs.create"


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _identifier() -> str:
    return uuid.uuid4().hex


class LocalRunSubmissionService:
    """Construct trusted transport metadata around one untrusted local request."""

    def __init__(
        self,
        guarded_runs: GuardedRunSubmissionService,
        *,
        orchestrator: OrchestrationSubmissionService,
        runs: RunOperations,
        queue: RunQueueRuntimeNotifier,
        data_classes: frozenset[DataClass],
        clock: Clock = _utc_now,
        id_factory: IdFactory = _identifier,
    ) -> None:
        if not isinstance(data_classes, frozenset) or not data_classes:
            raise ValueError("run data_classes must be a non-empty immutable frozenset")
        try:
            self._data_classes = frozenset(DataClass(value) for value in data_classes)
        except ValueError as error:
            raise ValueError("run data_classes contains an invalid value") from error
        self._guarded_runs = guarded_runs
        self._orchestrator = orchestrator
        self._runs = runs
        self._queue = queue
        self._clock = clock
        self._id_factory = id_factory

    async def submit_run(
        self,
        *,
        actor: AuthenticatedPrincipal,
        text: str,
        idempotency_key: str,
        thread_key: str | None,
        explicit_skill: str | None,
    ) -> RunSubmission:
        if not self._queue.ready:
            raise RunQueueUnavailableError("The Run Queue dispatcher is unavailable")
        request = RunRequest(
            request_id=self._id_factory(),
            principal=Principal(
                user_id=actor.user_id,
                role=actor.role,
                channel=PrincipalChannel.DASHBOARD,
            ),
            text=text,
            idempotency_key=idempotency_key,
            created_at=self._now(),
            thread_key=thread_key,
            explicit_skill=explicit_skill,
        )
        guarded = await self._guarded_runs.submit(
            actor=actor,
            request=request,
            route_key=_ROUTE_KEY,
        )
        submitted = await self._orchestrator.submit(
            guarded,
            data_classes=self._data_classes,
        )
        if submitted.state is RunState.QUEUED:
            self._queue.wake()
        run = await self._runs.get_run(actor=actor, run_id=submitted.run_id)
        return RunSubmission(run=run, replayed=submitted.replayed)

    def _now(self) -> datetime:
        value = self._clock()
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("run submission clock must return a timezone-aware datetime")
        return value.astimezone(UTC)
