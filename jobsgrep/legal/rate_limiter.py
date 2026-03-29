"""Per-source and per-user rate limiting (in-memory; Redis-backed optional)."""
from __future__ import annotations

import asyncio
import time
from collections import defaultdict, deque
from typing import Any


class InMemoryRateLimiter:
    """Sliding-window rate limiter using deques.

    Thread-safe via asyncio lock (single-process use).
    """

    def __init__(self) -> None:
        # key → deque of timestamps
        self._windows: dict[str, deque] = defaultdict(deque)
        self._lock = asyncio.Lock()

    async def is_allowed(self, key: str, limit: int, window_seconds: int) -> bool:
        async with self._lock:
            now = time.monotonic()
            cutoff = now - window_seconds
            dq = self._windows[key]
            while dq and dq[0] < cutoff:
                dq.popleft()
            if len(dq) >= limit:
                return False
            dq.append(now)
            return True

    async def remaining(self, key: str, limit: int, window_seconds: int) -> int:
        async with self._lock:
            now = time.monotonic()
            cutoff = now - window_seconds
            dq = self._windows[key]
            while dq and dq[0] < cutoff:
                dq.popleft()
            return max(0, limit - len(dq))


# Singleton limiter
_limiter = InMemoryRateLimiter()


async def check_user_rate_limit(user_key: str) -> bool:
    """Check if a user/IP is within the search rate limit (searches/hour)."""
    from ..config import get_settings
    settings = get_settings()
    return await _limiter.is_allowed(
        f"user:{user_key}",
        limit=settings.search_rate_limit,
        window_seconds=3600,
    )


async def check_source_rate_limit(source_name: str) -> bool:
    """Check per-source per-minute rate limit (50% of published limit = conservative)."""
    from ..config import SOURCE_REGISTRY
    meta = SOURCE_REGISTRY.get(source_name)
    if meta is None:
        return True
    # Apply 50% buffer: if they allow 60/min, we do 30/min
    conservative_limit = meta.rate_limit.calls_per_minute // 2 or 1
    return await _limiter.is_allowed(
        f"source:{source_name}",
        limit=conservative_limit,
        window_seconds=60,
    )


async def wait_for_source(source_name: str) -> None:
    """Block until source rate limit allows the next call (max wait 60s)."""
    for _ in range(120):
        if await check_source_rate_limit(source_name):
            return
        await asyncio.sleep(0.5)
