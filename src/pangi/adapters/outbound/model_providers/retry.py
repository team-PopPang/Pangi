"""Pangi-owned Transport Retry boundary for one logical Model call."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable
from dataclasses import replace
from typing import Protocol

from pangi.application.contracts.model_routing import (
    GuardedModelRequest,
    ModelProviderFailure,
    ModelProviderResponse,
    ProviderRetryPolicy,
)
from pangi.application.ports.model_routing import ModelProvider
from pangi.domain.model_routing import ModelProviderErrorCode

_RETRYABLE_CODES = frozenset(
    {
        ModelProviderErrorCode.UNAVAILABLE,
        ModelProviderErrorCode.RATE_LIMITED,
        ModelProviderErrorCode.TIMEOUT,
    }
)


class RetrySleeper(Protocol):
    async def sleep(self, seconds: float) -> None:
        """Wait before the next Network Request."""

        ...


class AsyncioRetrySleeper:
    async def sleep(self, seconds: float) -> None:
        await asyncio.sleep(seconds)


class RetryingModelProvider:
    """Retry only safe Transport failures and report exact request counts."""

    def __init__(
        self,
        provider: ModelProvider,
        policy: ProviderRetryPolicy,
        *,
        clock: Callable[[], float] = time.monotonic,
        sleeper: RetrySleeper | None = None,
    ) -> None:
        self._provider = provider
        self._policy = policy
        self._clock = clock
        self._sleeper = sleeper or AsyncioRetrySleeper()

    async def invoke(self, request: GuardedModelRequest) -> ModelProviderResponse:
        started = self._clock()
        last_failure: ModelProviderFailure | None = None
        for attempt in range(1, self._policy.max_attempts + 1):
            remaining = self._policy.total_timeout_seconds - (self._clock() - started)
            if remaining <= 0:
                break
            timeout = min(self._policy.attempt_timeout_seconds, remaining)
            try:
                response = await asyncio.wait_for(self._provider.invoke(request), timeout=timeout)
            except TimeoutError:
                last_failure = ModelProviderFailure(
                    ModelProviderErrorCode.TIMEOUT,
                    retryable=True,
                )
            except ModelProviderFailure as error:
                last_failure = error
            except Exception:
                last_failure = ModelProviderFailure(
                    ModelProviderErrorCode.UNKNOWN,
                    retryable=False,
                )
            else:
                return replace(
                    response,
                    provider_request_count=attempt,
                    duration_ms=self._elapsed_ms(started),
                )

            assert last_failure is not None
            if (
                attempt >= self._policy.max_attempts
                or not last_failure.retryable
                or last_failure.code not in _RETRYABLE_CODES
            ):
                raise self._with_metadata(last_failure, attempt, started) from None

            delay = self._policy.retry_backoff_seconds[attempt - 1]
            remaining = self._policy.total_timeout_seconds - (self._clock() - started)
            if delay >= remaining:
                raise self._with_metadata(last_failure, attempt, started) from None
            await self._sleeper.sleep(delay)

        failure = last_failure or ModelProviderFailure(
            ModelProviderErrorCode.TIMEOUT,
            retryable=True,
        )
        attempted = max(1, min(self._policy.max_attempts, failure.provider_request_count))
        raise self._with_metadata(failure, attempted, started) from None

    def _elapsed_ms(self, started: float) -> int:
        return max(0, round((self._clock() - started) * 1000))

    def _with_metadata(
        self,
        failure: ModelProviderFailure,
        attempts: int,
        started: float,
    ) -> ModelProviderFailure:
        return ModelProviderFailure(
            failure.code,
            retryable=False,
            provider_request_count=attempts,
            duration_ms=self._elapsed_ms(started),
        )
