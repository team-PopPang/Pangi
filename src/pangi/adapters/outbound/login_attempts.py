"""Bounded in-memory failed-login limiter for the single-process profile."""

from __future__ import annotations

import math
from collections import deque
from datetime import UTC, datetime, timedelta
from threading import Lock


class InMemoryLoginAttemptLimiter:
    def __init__(
        self,
        *,
        attempt_limit: int,
        window_seconds: int,
        max_keys: int = 10_000,
    ) -> None:
        self._attempt_limit = attempt_limit
        self._window = timedelta(seconds=window_seconds)
        self._max_keys = max_keys
        self._attempts: dict[str, deque[datetime]] = {}
        self._lock = Lock()

    def reserve(self, key: str, *, at: datetime) -> int | None:
        now = at.astimezone(UTC)
        with self._lock:
            attempts = self._active_attempts(key, now)
            if len(attempts) >= self._attempt_limit:
                remaining = self._window - (now - attempts[0])
                return max(1, math.ceil(remaining.total_seconds()))
            if key not in self._attempts and len(self._attempts) >= self._max_keys:
                oldest_key = min(
                    self._attempts,
                    key=lambda candidate: self._attempts[candidate][-1],
                )
                del self._attempts[oldest_key]
            attempts.append(now)
            self._attempts[key] = attempts
            return None

    def clear(self, key: str) -> None:
        with self._lock:
            self._attempts.pop(key, None)

    def _active_attempts(self, key: str, now: datetime) -> deque[datetime]:
        attempts = self._attempts.get(key, deque())
        cutoff = now - self._window
        while attempts and attempts[0] <= cutoff:
            attempts.popleft()
        if not attempts:
            self._attempts.pop(key, None)
        return attempts
