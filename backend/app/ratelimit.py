"""Per-IP sliding-window rate limiting for the public /api endpoints.

The site runs on Soren's Groq key (free tier), so this exists to protect that
quota from being burned by one heavy client, not to be a general-purpose
limiter. Stdlib only (collections.deque), no new dependency.

Ceiling: state is an in-process dict, so limits only hold within a single
process/instance and reset on restart. Fine for the single free-tier Render
instance this app targets; if it ever scales to >1 instance, swap for a
shared store (e.g. Redis) keyed the same way.
"""

from __future__ import annotations

import time
from collections import defaultdict, deque

from fastapi import HTTPException, Request

from . import config


class _SlidingWindowLimiter:
    """Allows up to `limit` hits per `window_seconds` for a given key."""

    def __init__(self, limit: int, window_seconds: float) -> None:
        self.limit = limit
        self.window_seconds = window_seconds
        self._hits: dict[str, deque[float]] = defaultdict(deque)

    def allow(self, key: str) -> bool:
        now = time.monotonic()
        hits = self._hits[key]
        while hits and now - hits[0] > self.window_seconds:
            hits.popleft()
        if len(hits) >= self.limit:
            return False
        hits.append(now)
        return True


def client_ip(request: Request) -> str:
    """Client IP from the first X-Forwarded-For hop (Render sits behind a proxy).

    Falls back to the direct connection address for local dev / no proxy.
    """
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


_general = _SlidingWindowLimiter(config.RATE_LIMIT_GENERAL, config.RATE_LIMIT_WINDOW_SECONDS)
_sentiment = _SlidingWindowLimiter(config.RATE_LIMIT_SENTIMENT, config.RATE_LIMIT_WINDOW_SECONDS)


def enforce_general(request: Request) -> None:
    """Dependency: per-IP limit for all /api endpoints."""
    if not _general.allow(client_ip(request)):
        raise HTTPException(
            status_code=429,
            detail="Too many requests. Please slow down and try again shortly.",
        )


def enforce_sentiment(request: Request) -> None:
    """Dependency: stricter per-IP limit for endpoints that can trigger an LLM call."""
    if not _sentiment.allow(client_ip(request)):
        raise HTTPException(
            status_code=429,
            detail="AI request limit reached. Please try again in a minute.",
        )
