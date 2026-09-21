"""Token-bucket rate limiting.

Each user gets a bucket with ``capacity`` tokens that refills at ``refill_per_second``.
A request costs one token. Bursts up to ``capacity`` are allowed; sustained throughput is
bounded by the refill rate. Both are configurable via ``Settings``.

Why token bucket (vs fixed window): it allows short bursts (a user pasting three follow-up
questions quickly) without letting a single user monopolise the LLM budget over time.

Implementation is in-process and async-safe. In a multi-replica deployment the same interface
would be backed by Redis (``INCR``/Lua) - the ``RateLimiter`` abstraction keeps that swap local.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field


@dataclass
class RateLimitDecision:
    allowed: bool
    remaining: float
    retry_after_seconds: float = 0.0


@dataclass
class TokenBucket:
    capacity: float
    refill_per_second: float
    tokens: float = field(default=None)  # type: ignore[assignment]
    last_refill: float = field(default_factory=time.monotonic)

    def __post_init__(self) -> None:
        if self.tokens is None:
            self.tokens = float(self.capacity)

    def _refill(self, now: float) -> None:
        elapsed = max(0.0, now - self.last_refill)
        self.tokens = min(self.capacity, self.tokens + elapsed * self.refill_per_second)
        self.last_refill = now

    def try_consume(self, cost: float = 1.0, now: float | None = None) -> RateLimitDecision:
        now = time.monotonic() if now is None else now
        self._refill(now)
        if self.tokens >= cost:
            self.tokens -= cost
            return RateLimitDecision(allowed=True, remaining=self.tokens)
        deficit = cost - self.tokens
        retry_after = deficit / self.refill_per_second if self.refill_per_second > 0 else float("inf")
        return RateLimitDecision(allowed=False, remaining=self.tokens, retry_after_seconds=retry_after)


class RateLimiter:
    """Per-key (per-user) bucket registry."""

    def __init__(self, capacity: int, refill_per_second: float) -> None:
        self.capacity = capacity
        self.refill_per_second = refill_per_second
        self._buckets: dict[str, TokenBucket] = {}
        self._lock = asyncio.Lock()

    async def check(self, key: str, cost: float = 1.0) -> RateLimitDecision:
        async with self._lock:
            bucket = self._buckets.get(key)
            if bucket is None:
                bucket = TokenBucket(capacity=self.capacity, refill_per_second=self.refill_per_second)
                self._buckets[key] = bucket
            return bucket.try_consume(cost)

    def snapshot(self, key: str) -> dict[str, float]:
        bucket = self._buckets.get(key)
        if bucket is None:
            return {"tokens": float(self.capacity), "capacity": float(self.capacity)}
        bucket._refill(time.monotonic())
        return {"tokens": round(bucket.tokens, 2), "capacity": float(self.capacity)}
