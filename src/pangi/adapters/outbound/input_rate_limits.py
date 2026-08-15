"""Bounded in-memory input rate limiter for the single-process profile."""

from __future__ import annotations

import math
from collections import deque
from datetime import UTC, datetime, timedelta
from threading import Lock


class InMemoryInputRateLimiter:
    """Reserve sliding-window capacity without defining an organization policy."""

    def __init__(self, *, max_keys: int) -> None:
        if max_keys < 1:
            raise ValueError("max_keys must be positive")
        self._max_keys = max_keys
        self._requests: dict[str, deque[datetime]] = {}
        self._lock = Lock()

    def reserve(
        self,
        key: str,
        *,
        at: datetime,
        limit: int,
        window_seconds: int,
    ) -> int | None:
        if not key:
            raise ValueError("rate limit key cannot be empty")
        if at.tzinfo is None or at.utcoffset() is None:
            raise ValueError("rate limit timestamp must be timezone-aware")
        if limit < 1 or window_seconds < 1:
            raise ValueError("rate limit and window_seconds must be positive")
        now = at.astimezone(UTC)
        window = timedelta(seconds=window_seconds)
        with self._lock:
            requests = self._active_requests(key, now, window)
            if len(requests) >= limit:
                remaining = window - (now - requests[0])
                return max(1, math.ceil(remaining.total_seconds()))
            if key not in self._requests and len(self._requests) >= self._max_keys:
                oldest_key = min(
                    self._requests,
                    key=lambda candidate: self._requests[candidate][-1],
                )
                del self._requests[oldest_key]
            requests.append(now)
            self._requests[key] = requests
            return None

    def _active_requests(
        self,
        key: str,
        now: datetime,
        window: timedelta,
    ) -> deque[datetime]:
        requests = self._requests.get(key, deque())
        cutoff = now - window
        while requests and requests[0] <= cutoff:
            requests.popleft()
        if not requests:
            self._requests.pop(key, None)
        return requests
