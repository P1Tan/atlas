import threading

import pytest
from fastapi import HTTPException

from app.rate_limit import RateLimiter


class FakeClock:
    """An injectable, manually-advanced time source -- real elapsed-time
    behavior (window eviction, daily-cap reset) is tested for real rather
    than skipped, without a real test actually sleeping for a day."""

    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def test_allows_calls_under_the_burst_limit() -> None:
    limiter = RateLimiter(per_window_limit=3, window_seconds=60, daily_limit=100)
    for _ in range(3):
        limiter.check("user-1")  # should not raise


def test_blocks_the_call_that_exceeds_the_burst_limit() -> None:
    limiter = RateLimiter(per_window_limit=3, window_seconds=60, daily_limit=100)
    for _ in range(3):
        limiter.check("user-1")

    with pytest.raises(HTTPException) as exc_info:
        limiter.check("user-1")
    assert exc_info.value.status_code == 429


def test_burst_limit_recovers_once_the_window_elapses() -> None:
    clock = FakeClock()
    limiter = RateLimiter(per_window_limit=2, window_seconds=60, daily_limit=100, time_fn=clock)
    limiter.check("user-1")
    limiter.check("user-1")
    with pytest.raises(HTTPException):
        limiter.check("user-1")

    clock.advance(61)
    limiter.check("user-1")  # should not raise -- the earlier calls have aged out


def test_daily_cap_is_independent_of_the_burst_window() -> None:
    """A caller pacing well under the burst limit can still hit the daily
    cap -- the two limits are separate, not just the same counter reused."""
    clock = FakeClock()
    limiter = RateLimiter(per_window_limit=100, window_seconds=60, daily_limit=2, time_fn=clock)
    limiter.check("user-1")
    clock.advance(120)
    limiter.check("user-1")
    clock.advance(120)

    with pytest.raises(HTTPException) as exc_info:
        limiter.check("user-1")
    assert exc_info.value.status_code == 429
    assert "daily" in exc_info.value.detail.lower()


def test_daily_cap_recovers_after_a_full_day() -> None:
    clock = FakeClock()
    limiter = RateLimiter(per_window_limit=100, window_seconds=60, daily_limit=1, time_fn=clock)
    limiter.check("user-1")
    with pytest.raises(HTTPException):
        limiter.check("user-1")

    clock.advance(86401)
    limiter.check("user-1")  # should not raise -- yesterday's call has aged out


def test_limits_are_tracked_per_user_not_globally() -> None:
    limiter = RateLimiter(per_window_limit=1, window_seconds=60, daily_limit=100)
    limiter.check("user-1")
    limiter.check("user-2")  # a different user must not be blocked by user-1's usage


def test_concurrent_calls_for_the_same_user_cannot_exceed_the_limit() -> None:
    """FastAPI runs sync dependencies (both enforce_*_rate_limit functions
    are sync) via a thread pool, not serialized on the event loop -- two
    requests for the same user genuinely can call check() concurrently, the
    exact "buggy tight retry loop" scenario this guardrail exists to catch.
    Without RateLimiter's internal lock, a read-check-write race lets more
    than `per_window_limit` calls through when fired from real threads
    (not just sequentially, which every other test in this file does)."""
    limiter = RateLimiter(per_window_limit=5, window_seconds=60, daily_limit=1000)
    accepted = 0
    lock = threading.Lock()

    def call() -> None:
        nonlocal accepted
        try:
            limiter.check("user-1")
        except HTTPException:
            return
        with lock:
            accepted += 1

    threads = [threading.Thread(target=call) for _ in range(50)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert accepted == 5
