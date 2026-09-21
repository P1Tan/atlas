"""Milestone 9.3 (cost/abuse guardrails, spec §18): per-user rate limits and
daily usage caps on the cost-incurring endpoints, so a client bug (a tight
retry loop) or deliberate abuse can't run up the LLM/STT/TTS bill.

In-memory state, appropriate for this project's current single-process dev
deployment (the backend only binds `127.0.0.1`, run as one `uvicorn`
process) -- no Redis/external store needed at this stage, that would be
premature infrastructure for a personal, unscaled project. Revisit if this
ever runs as multiple workers/instances, where per-process state would stop
being an accurate shared view.

Applied to `/chat`, `/voice/token`, `/facts`, `/gmail/candidates`, and
`/extract` -- every endpoint that has an authenticated user to key a limit
on (`/facts`'s own limit added in a later bug audit: authenticated same as
the other two, and had no limiter at all despite that; `/gmail/candidates`
and `/extract` became limitable only once each gained `get_current_user`,
in the same change that fixed its missing auth). `/health` is deliberately
left open: no user, no cost, and it's the liveness check.
"""

import threading
import time
from collections import defaultdict, deque
from typing import Callable

from fastapi import Depends, HTTPException

from app.config import (
    CHAT_DAILY_USAGE_CAP,
    CHAT_RATE_LIMIT_PER_MINUTE,
    EXTRACT_DAILY_USAGE_CAP,
    EXTRACT_RATE_LIMIT_PER_MINUTE,
    FACTS_DAILY_USAGE_CAP,
    FACTS_RATE_LIMIT_PER_MINUTE,
    GMAIL_CANDIDATES_DAILY_USAGE_CAP,
    GMAIL_CANDIDATES_RATE_LIMIT_PER_MINUTE,
    VOICE_TOKEN_DAILY_USAGE_CAP,
    VOICE_TOKEN_RATE_LIMIT_PER_MINUTE,
)
from app.supabase_client import AuthenticatedUser, get_current_user

_DAY_SECONDS = 86400.0


class RateLimiter:
    """A per-user sliding-window burst limit plus a rolling 24h usage cap.

    Two independent deques per user rather than one scanned two ways: the
    burst window is checked on every call (must stay cheap), and keeping it
    separate from the day-long deque avoids an O(day's call count) scan each
    time just to answer "how many in the last minute."
    """

    def __init__(
        self,
        per_window_limit: int,
        window_seconds: float,
        daily_limit: int,
        time_fn: Callable[[], float] = time.monotonic,
    ) -> None:
        self._per_window_limit = per_window_limit
        self._window_seconds = window_seconds
        self._daily_limit = daily_limit
        self._time_fn = time_fn
        self._recent: dict[str, deque] = defaultdict(deque)
        self._daily: dict[str, deque] = defaultdict(deque)
        # FastAPI/Starlette runs sync dependencies (both enforce_*_rate_limit
        # functions below are sync) via a thread pool, not serialized on the
        # event loop -- confirmed via Starlette's own run_in_threadpool
        # usage for sync path operations/dependencies. Without this lock,
        # two genuinely concurrent requests for the SAME user (exactly the
        # "buggy tight retry loop" scenario this guardrail exists to catch)
        # could both read the count as under the limit before either
        # appends, letting both through -- a real TOCTOU race, caught by
        # code review, not a false alarm.
        self._lock = threading.Lock()

    def check(self, user_id: str) -> None:
        """Raises HTTPException(429) if `user_id` is over either limit;
        otherwise records this call and returns normally."""
        with self._lock:
            now = self._time_fn()

            recent = self._recent[user_id]
            while recent and now - recent[0] > self._window_seconds:
                recent.popleft()
            if len(recent) >= self._per_window_limit:
                raise HTTPException(
                    status_code=429,
                    detail="Too many requests -- please slow down and try again in a moment.",
                )

            daily = self._daily[user_id]
            while daily and now - daily[0] > _DAY_SECONDS:
                daily.popleft()
            if len(daily) >= self._daily_limit:
                raise HTTPException(
                    status_code=429,
                    detail="Daily usage limit reached -- please try again tomorrow.",
                )

            recent.append(now)
            daily.append(now)


_default_chat_limiter = RateLimiter(
    per_window_limit=CHAT_RATE_LIMIT_PER_MINUTE, window_seconds=60.0, daily_limit=CHAT_DAILY_USAGE_CAP
)
_default_voice_token_limiter = RateLimiter(
    per_window_limit=VOICE_TOKEN_RATE_LIMIT_PER_MINUTE, window_seconds=60.0, daily_limit=VOICE_TOKEN_DAILY_USAGE_CAP
)
_default_facts_limiter = RateLimiter(
    per_window_limit=FACTS_RATE_LIMIT_PER_MINUTE, window_seconds=60.0, daily_limit=FACTS_DAILY_USAGE_CAP
)
_default_gmail_candidates_limiter = RateLimiter(
    per_window_limit=GMAIL_CANDIDATES_RATE_LIMIT_PER_MINUTE,
    window_seconds=60.0,
    daily_limit=GMAIL_CANDIDATES_DAILY_USAGE_CAP,
)
_default_extract_limiter = RateLimiter(
    per_window_limit=EXTRACT_RATE_LIMIT_PER_MINUTE,
    window_seconds=60.0,
    daily_limit=EXTRACT_DAILY_USAGE_CAP,
)


def get_chat_rate_limiter() -> RateLimiter:
    """FastAPI dependency -- the one function `/chat` should depend on, so
    `dependency_overrides` actually takes effect in tests (mirrors
    `app.extraction.get_extractor`'s own reasoning)."""
    return _default_chat_limiter


def get_voice_token_rate_limiter() -> RateLimiter:
    return _default_voice_token_limiter


def get_facts_rate_limiter() -> RateLimiter:
    return _default_facts_limiter


def get_gmail_candidates_rate_limiter() -> RateLimiter:
    return _default_gmail_candidates_limiter


def get_extract_rate_limiter() -> RateLimiter:
    return _default_extract_limiter


def enforce_chat_rate_limit(
    user: AuthenticatedUser = Depends(get_current_user),
    limiter: RateLimiter = Depends(get_chat_rate_limiter),
) -> None:
    limiter.check(user.id)


def enforce_voice_token_rate_limit(
    user: AuthenticatedUser = Depends(get_current_user),
    limiter: RateLimiter = Depends(get_voice_token_rate_limiter),
) -> None:
    limiter.check(user.id)


def enforce_facts_rate_limit(
    user: AuthenticatedUser = Depends(get_current_user),
    limiter: RateLimiter = Depends(get_facts_rate_limiter),
) -> None:
    limiter.check(user.id)


def enforce_gmail_candidates_rate_limit(
    user: AuthenticatedUser = Depends(get_current_user),
    limiter: RateLimiter = Depends(get_gmail_candidates_rate_limiter),
) -> None:
    limiter.check(user.id)


def enforce_extract_rate_limit(
    user: AuthenticatedUser = Depends(get_current_user),
    limiter: RateLimiter = Depends(get_extract_rate_limiter),
) -> None:
    limiter.check(user.id)
