import asyncio
from typing import Optional

from fastapi.testclient import TestClient

from app import voice_routes
from app.main import app
from app.rate_limit import RateLimiter, get_voice_token_rate_limiter
from app.supabase_client import AuthenticatedUser, get_current_user

client = TestClient(app)

# Every (room_name, user_id, timezone) the route asked for a session with,
# in call order -- the only way to assert on arguments that never appear in
# the HTTP response (notably the session timezone, review finding F5).
_spawned_sessions: list[dict] = []


async def _fake_run_voice_session(
    room_name: str, user_id: str, timezone: str, ready: Optional[asyncio.Event] = None
) -> bool:
    """Stands in for the real Pipecat/LiveKit pipeline in these tests.
    /voice/token's own job -- mint a token, name a room, spawn and track a
    session task -- is what's under test here (the pipeline itself is
    covered by the voice bridge tests), and the real function would
    otherwise make a real network connection attempt using the fake
    LIVEKIT_* credentials these tests set.

    Setting `ready` is not optional dressing: as of the bot-readiness fix
    (review finding F1) the route does not respond until the real function
    signals that the bot has actually joined the room, so a fake that
    didn't set it would make every test here wait out the full timeout and
    then get a 503."""
    _spawned_sessions.append({"room_name": room_name, "user_id": user_id, "timezone": timezone})
    if ready is not None:
        ready.set()
    # The real function returns whether a human ever joined the room (only
    # `_run_dev_session_loop` reads it; this route ignores it).
    return True


async def _fake_run_voice_session_that_never_joins(
    room_name: str, user_id: str, timezone: str, ready: Optional[asyncio.Event] = None
) -> bool:
    """A bot that starts but never connects to the room -- the case the
    readiness wait exists for (a wedged/slow provider init)."""
    await asyncio.sleep(3600)


def setup_function() -> None:
    _spawned_sessions.clear()
    app.dependency_overrides[get_current_user] = lambda: AuthenticatedUser(
        id="test-user-id", email="test@example.com"
    )


def teardown_function() -> None:
    app.dependency_overrides.clear()
    voice_routes._active_voice_sessions.clear()


def _configure_voice(monkeypatch) -> None:
    monkeypatch.setattr(voice_routes, "LIVEKIT_URL", "wss://example.livekit.cloud")
    monkeypatch.setattr(voice_routes, "LIVEKIT_API_KEY", "fake-api-key")
    monkeypatch.setattr(voice_routes, "LIVEKIT_API_SECRET", "fake-api-secret-that-is-long-enough")
    monkeypatch.setattr(voice_routes, "CARTESIA_API_KEY", "fake-cartesia-key")


def test_create_voice_token_requires_authentication() -> None:
    app.dependency_overrides.pop(get_current_user, None)

    response = client.post("/voice/token")

    assert response.status_code == 401


def test_create_voice_token_returns_503_when_livekit_not_configured(monkeypatch) -> None:
    monkeypatch.setattr(voice_routes, "LIVEKIT_URL", "")
    monkeypatch.setattr(voice_routes, "LIVEKIT_API_KEY", "")
    monkeypatch.setattr(voice_routes, "LIVEKIT_API_SECRET", "")

    response = client.post("/voice/token")

    assert response.status_code == 503


def test_create_voice_token_returns_503_when_cartesia_not_configured(monkeypatch) -> None:
    """The spawned bot needs Cartesia for TTS just as much as it needs
    LiveKit -- added alongside the per-session room fix (the token endpoint
    didn't check this before, since it used to only mint a token and never
    itself spawned anything that would need Cartesia configured)."""
    monkeypatch.setattr(voice_routes, "LIVEKIT_URL", "wss://example.livekit.cloud")
    monkeypatch.setattr(voice_routes, "LIVEKIT_API_KEY", "fake-api-key")
    monkeypatch.setattr(voice_routes, "LIVEKIT_API_SECRET", "fake-api-secret-that-is-long-enough")
    monkeypatch.setattr(voice_routes, "CARTESIA_API_KEY", "")

    response = client.post("/voice/token")

    assert response.status_code == 503


def test_create_voice_token_returns_200_with_a_jwt_shaped_token(monkeypatch) -> None:
    monkeypatch.setattr(voice_routes, "LIVEKIT_URL", "wss://example.livekit.cloud")
    monkeypatch.setattr(voice_routes, "LIVEKIT_API_KEY", "fake-api-key")
    monkeypatch.setattr(voice_routes, "LIVEKIT_API_SECRET", "fake-api-secret-that-is-long-enough")
    monkeypatch.setattr(voice_routes, "CARTESIA_API_KEY", "fake-cartesia-key")
    monkeypatch.setattr(voice_routes, "run_voice_session", _fake_run_voice_session)

    response = client.post("/voice/token")

    assert response.status_code == 200
    body = response.json()
    assert body["url"] == "wss://example.livekit.cloud"
    # Found live (bug audit, per-session room fix): every user used to get
    # the same fixed "atlas-dev" room name -- now a fresh, unique room per
    # call is the actual fix, so asserting a random UUID-based name here
    # (not a fixed string) is the whole point of this test post-fix.
    assert body["room_name"].startswith("atlas-voice-")
    assert body["room_name"] != "atlas-voice-"

    token = body["token"]
    segments = token.split(".")
    assert len(segments) == 3
    assert all(segments)


def test_create_voice_token_returns_a_different_room_name_each_call(monkeypatch) -> None:
    monkeypatch.setattr(voice_routes, "LIVEKIT_URL", "wss://example.livekit.cloud")
    monkeypatch.setattr(voice_routes, "LIVEKIT_API_KEY", "fake-api-key")
    monkeypatch.setattr(voice_routes, "LIVEKIT_API_SECRET", "fake-api-secret-that-is-long-enough")
    monkeypatch.setattr(voice_routes, "CARTESIA_API_KEY", "fake-cartesia-key")
    monkeypatch.setattr(voice_routes, "run_voice_session", _fake_run_voice_session)

    first = client.post("/voice/token")
    second = client.post("/voice/token")

    assert first.json()["room_name"] != second.json()["room_name"]


def test_create_voice_token_returns_429_once_the_rate_limit_is_exceeded(monkeypatch) -> None:
    """Confirms enforce_voice_token_rate_limit is actually wired into the
    route (not just present in app.rate_limit) -- a tiny override limiter,
    not the real 30/min default. The dependency runs before the route body,
    so this doesn't need LIVEKIT_* configured to prove the 429 itself."""
    # The SAME instance for every dependency resolution -- see the identical
    # comment in test_chat_routes.py's analogous test.
    limiter = RateLimiter(per_window_limit=1, window_seconds=60, daily_limit=100)
    app.dependency_overrides[get_voice_token_rate_limiter] = lambda: limiter
    monkeypatch.setattr(voice_routes, "LIVEKIT_URL", "wss://example.livekit.cloud")
    monkeypatch.setattr(voice_routes, "LIVEKIT_API_KEY", "fake-api-key")
    monkeypatch.setattr(voice_routes, "LIVEKIT_API_SECRET", "fake-api-secret-that-is-long-enough")
    monkeypatch.setattr(voice_routes, "CARTESIA_API_KEY", "fake-cartesia-key")
    monkeypatch.setattr(voice_routes, "run_voice_session", _fake_run_voice_session)

    first = client.post("/voice/token")
    second = client.post("/voice/token")

    assert first.status_code == 200
    assert second.status_code == 429


def test_create_voice_token_uses_the_requested_timezone(monkeypatch) -> None:
    """Review finding F5: every real user's session used to run with the
    hardcoded dev timezone, so voice-mode date resolution and
    set_reminder's "already passed?" check were wrong for anyone outside
    that zone. The spawned session is what carries the timezone -- it never
    appears in the response -- hence asserting on the recorded call."""
    _configure_voice(monkeypatch)
    monkeypatch.setattr(voice_routes, "run_voice_session", _fake_run_voice_session)

    response = client.post("/voice/token", params={"timezone": "Asia/Tokyo"})

    assert response.status_code == 200
    assert _spawned_sessions[-1]["timezone"] == "Asia/Tokyo"


def test_create_voice_token_falls_back_to_the_dev_timezone_when_omitted(monkeypatch) -> None:
    """The parameter stays optional on purpose: an already-installed iOS
    build that doesn't send one must keep getting a working session rather
    than a 422."""
    _configure_voice(monkeypatch)
    monkeypatch.setattr(voice_routes, "run_voice_session", _fake_run_voice_session)

    response = client.post("/voice/token")

    assert response.status_code == 200
    assert _spawned_sessions[-1]["timezone"] == voice_routes.VOICE_DEV_TIMEZONE


def test_create_voice_token_returns_400_for_an_unknown_timezone(monkeypatch) -> None:
    """Rejected here rather than inside the spawned task -- otherwise the
    caller gets a perfectly valid token for a room whose bot immediately
    dies on ZoneInfo()."""
    _configure_voice(monkeypatch)
    monkeypatch.setattr(voice_routes, "run_voice_session", _fake_run_voice_session)

    response = client.post("/voice/token", params={"timezone": "Not/A_Zone"})

    assert response.status_code == 400
    assert "Not/A_Zone" in response.json()["detail"]
    assert _spawned_sessions == []


def test_create_voice_token_returns_503_when_the_bot_never_joins(monkeypatch) -> None:
    """Review finding F1: a token for a room with no bot in it is worse
    than no token at all -- the client would connect, publish its
    context_seed into the void and only notice ~15s later via its own
    heartbeat timeout."""
    _configure_voice(monkeypatch)
    monkeypatch.setattr(voice_routes, "run_voice_session", _fake_run_voice_session_that_never_joins)
    monkeypatch.setattr(voice_routes, "_BOT_READY_TIMEOUT_SECS", 0.05)

    response = client.post("/voice/token")

    assert response.status_code == 503
    assert response.json()["detail"] == "voice pipeline unavailable"
    # The failed session must not be left running -- that's half of what
    # review finding F2 is about.
    assert voice_routes._active_voice_sessions.get("test-user-id") is None or (
        voice_routes._active_voice_sessions["test-user-id"].cancelled()
        or voice_routes._active_voice_sessions["test-user-id"].cancelling() > 0
    )


def test_create_voice_token_cancels_the_users_previous_session(monkeypatch) -> None:
    """Review finding F2: iOS's continuous-conversation mode asks for a new
    token after every reply, so without this the previous bot's full
    pipeline (OpenAI + Cartesia websockets) stayed live in its abandoned
    room until LiveKit's empty-room reaper got to it -- two pipelines
    overlapping on every single turn, and stackable by a retry loop.

    Uses TestClient as a context manager deliberately: that keeps ONE event
    loop across both requests, so the first request's session task is still
    a live task when the second request goes to cancel it (a plain
    `client.post` spins up and tears down its own loop per call)."""
    _configure_voice(monkeypatch)

    async def _fake_long_running_session(
        room_name: str, user_id: str, timezone: str, ready: Optional[asyncio.Event] = None
    ) -> bool:
        _spawned_sessions.append({"room_name": room_name, "user_id": user_id, "timezone": timezone})
        if ready is not None:
            ready.set()
        await asyncio.sleep(3600)

    monkeypatch.setattr(voice_routes, "run_voice_session", _fake_long_running_session)

    with TestClient(app) as context_client:
        first = context_client.post("/voice/token")
        first_task = voice_routes._active_voice_sessions["test-user-id"]

        second = context_client.post("/voice/token")
        second_task = voice_routes._active_voice_sessions["test-user-id"]

        assert first.status_code == 200
        assert second.status_code == 200
        assert first_task is not second_task
        assert first_task.cancelled() or first_task.cancelling() > 0
        # Exactly one live session per user, not a growing pile.
        assert len(voice_routes._active_voice_sessions) == 1

        second_task.cancel()


def test_finished_session_is_only_untracked_if_it_is_still_the_current_one() -> None:
    """The done-callback's identity check, exercised directly: a cancelled
    task's callback runs AFTER the newer request has already stored its own
    task under the same key, so discarding blindly would drop the strong
    reference to a live session (and hide it from the next request's cancel
    step)."""

    async def _scenario() -> None:
        superseded = asyncio.create_task(asyncio.sleep(0))
        current = asyncio.create_task(asyncio.sleep(3600))
        await superseded
        voice_routes._active_voice_sessions["user-1"] = current

        voice_routes._log_session_outcome("user-1", superseded)
        assert voice_routes._active_voice_sessions["user-1"] is current

        current.cancel()
        try:
            await current
        except asyncio.CancelledError:
            pass
        voice_routes._log_session_outcome("user-1", current)
        assert "user-1" not in voice_routes._active_voice_sessions

    asyncio.run(_scenario())
