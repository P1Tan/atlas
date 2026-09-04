from fastapi.testclient import TestClient

from app import voice_routes
from app.main import app
from app.rate_limit import RateLimiter, get_voice_token_rate_limiter
from app.supabase_client import AuthenticatedUser, get_current_user

client = TestClient(app)


def setup_function() -> None:
    app.dependency_overrides[get_current_user] = lambda: AuthenticatedUser(
        id="test-user-id", email="test@example.com"
    )


def teardown_function() -> None:
    app.dependency_overrides.clear()


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


def test_create_voice_token_returns_200_with_a_jwt_shaped_token(monkeypatch) -> None:
    monkeypatch.setattr(voice_routes, "LIVEKIT_URL", "wss://example.livekit.cloud")
    monkeypatch.setattr(voice_routes, "LIVEKIT_API_KEY", "fake-api-key")
    monkeypatch.setattr(voice_routes, "LIVEKIT_API_SECRET", "fake-api-secret-that-is-long-enough")

    response = client.post("/voice/token")

    assert response.status_code == 200
    body = response.json()
    assert body["url"] == "wss://example.livekit.cloud"
    assert body["room_name"] == "atlas-dev"

    token = body["token"]
    segments = token.split(".")
    assert len(segments) == 3
    assert all(segments)


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

    first = client.post("/voice/token")
    second = client.post("/voice/token")

    assert first.status_code == 200
    assert second.status_code == 429
