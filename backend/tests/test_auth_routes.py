"""The Google OAuth routes, now that credentials are per Supabase user.

GET /auth/google/login is gone: it redirected straight to Google, and since
iOS opens that URL with UIApplication.shared.open (no bearer token), it had
no way to know which user it was connecting. POST /auth/google/start
replaces it -- authenticated, returns the URL as JSON -- and the OAuth
`state` carries the user id across the browser round trip to /callback.

Supabase storage is faked via the helpers in test_google_auth, so these
tests assert the real per-user scoping rather than a fake that ignores it.
"""

import time
from urllib.parse import parse_qs, urlparse

from fastapi.testclient import TestClient

from app import auth_routes, google_auth
from app.main import app
from app.supabase_client import AuthenticatedUser, get_current_user
from tests.test_google_auth import USER_A, USER_B, install_fake_supabase, make_credentials

client = TestClient(app, follow_redirects=False)


def _sign_in_as(user_id: str) -> None:
    app.dependency_overrides[get_current_user] = lambda: AuthenticatedUser(
        id=user_id, email=f"{user_id}@example.com"
    )


def setup_function() -> None:
    _sign_in_as(USER_A)
    auth_routes._pending_logins.clear()


def teardown_function() -> None:
    app.dependency_overrides.clear()
    auth_routes._pending_logins.clear()


def _use_test_google_client(monkeypatch) -> None:
    monkeypatch.setattr(
        google_auth, "GOOGLE_CLIENT_ID", "test-client-id.apps.googleusercontent.com"
    )
    monkeypatch.setattr(google_auth, "GOOGLE_CLIENT_SECRET", "test-secret")
    monkeypatch.setattr(
        google_auth, "GOOGLE_REDIRECT_URI", "http://localhost:8000/auth/google/callback"
    )


class _FakeFlow:
    """Stands in for google_auth_oauthlib's Flow across both legs of the
    round trip -- the object /start builds and the one /callback rebuilds
    from the stored state and code_verifier."""

    def __init__(self, states, credentials, fetch_errors=None) -> None:
        self.credentials = credentials
        self.code_verifier = "pkce-code-verifier"
        # One state per /start call, so a test can have two logins in flight
        # at once; the last one repeats if a test starts more than it listed.
        self._states = list(states)
        self._fetch_errors = list(fetch_errors or [])
        self.fetch_token_calls = []
        self.build_calls = []

    def authorization_url(self, **kwargs):
        self.authorization_url_kwargs = kwargs
        state = self._states.pop(0) if len(self._states) > 1 else self._states[0]
        return f"https://accounts.google.com/o/oauth2/auth?state={state}", state

    def fetch_token(self, code: str) -> None:
        self.fetch_token_calls.append(code)
        if self._fetch_errors:
            raise self._fetch_errors.pop(0)


def _install_fake_flow(
    monkeypatch, *, states=("oauth-state-abc",), credentials=None, fetch_errors=None
) -> _FakeFlow:
    flow = _FakeFlow(
        states=states, credentials=credentials or make_credentials(), fetch_errors=fetch_errors
    )

    def _build_flow(state=None, code_verifier=None):
        flow.build_calls.append({"state": state, "code_verifier": code_verifier})
        return flow

    monkeypatch.setattr(auth_routes, "build_flow", _build_flow)
    return flow


def test_start_returns_a_google_authorization_url_with_readonly_gmail_scope(monkeypatch) -> None:
    _use_test_google_client(monkeypatch)
    install_fake_supabase(monkeypatch)

    response = client.post("/auth/google/start")
    assert response.status_code == 200

    authorization_url = response.json()["authorization_url"]
    assert authorization_url.startswith("https://accounts.google.com/o/oauth2/auth")

    query = parse_qs(urlparse(authorization_url).query)
    assert query["scope"] == ["https://www.googleapis.com/auth/gmail.readonly"]
    assert query["access_type"] == ["offline"]
    assert query["prompt"] == ["consent"]
    assert query["redirect_uri"] == ["http://localhost:8000/auth/google/callback"]
    # The state in the URL is the one the callback will be looked up by.
    assert query["state"][0] in auth_routes._pending_logins


def test_start_records_the_pending_login_against_the_calling_user(monkeypatch) -> None:
    _use_test_google_client(monkeypatch)
    _sign_in_as(USER_B)

    response = client.post("/auth/google/start")

    (pending,) = auth_routes._pending_logins.values()
    assert pending.user_id == USER_B
    assert pending.code_verifier
    assert pending.expires_at > time.monotonic()
    assert response.json()["authorization_url"]


def test_start_requires_authentication(monkeypatch) -> None:
    """The whole point of /start: it is the one leg of the flow that CAN
    carry a bearer token, and the user it identifies is the only way the
    callback later knows whose credential it is storing."""
    _use_test_google_client(monkeypatch)
    del app.dependency_overrides[get_current_user]

    response = client.post("/auth/google/start")

    assert response.status_code == 401
    assert response.json()["detail"] == "missing bearer token"
    assert auth_routes._pending_logins == {}


def test_start_prunes_expired_pending_logins(monkeypatch) -> None:
    """Nothing else ever removes an abandoned login (the user just closes
    the consent screen), so without pruning the dict only grows."""
    _use_test_google_client(monkeypatch)
    auth_routes._pending_logins["stale-state"] = auth_routes._PendingLogin(
        user_id=USER_B, code_verifier="old", expires_at=time.monotonic() - 1
    )

    client.post("/auth/google/start")

    assert "stale-state" not in auth_routes._pending_logins
    assert len(auth_routes._pending_logins) == 1


def test_callback_stores_credentials_for_the_user_who_started_the_login(monkeypatch) -> None:
    """The callback arrives straight from Google with no bearer token --
    the state is what recovers the user, and the credential must land on
    that user's row and no one else's."""
    fake_client = install_fake_supabase(monkeypatch)
    flow = _install_fake_flow(monkeypatch, credentials=make_credentials(refresh_token="refresh-a"))
    _sign_in_as(USER_A)
    state = urlparse(client.post("/auth/google/start").json()["authorization_url"])
    state = parse_qs(state.query)["state"][0]

    # Google itself calls back: no authentication at all on this request.
    del app.dependency_overrides[get_current_user]
    response = client.get("/auth/google/callback", params={"code": "auth-code", "state": state})

    assert response.status_code == 200
    assert flow.fetch_token_calls == ["auth-code"]
    assert list(fake_client.rows) == [USER_A]
    assert fake_client.rows[USER_A]["credentials"]["refresh_token"] == "refresh-a"


def test_callback_credits_the_state_it_was_given_not_whichever_login_is_pending(
    monkeypatch,
) -> None:
    """Two logins in flight at once. /callback is unauthenticated, so the
    state -> user mapping is its entire authorization: a callback carrying
    A's state must write A's row and leave B's login untouched and still
    completable. Without two pending entries, an implementation that just
    used "the only pending login" would pass every other test here."""
    fake_client = install_fake_supabase(monkeypatch)
    _install_fake_flow(
        monkeypatch,
        states=["state-a", "state-b"],
        credentials=make_credentials(refresh_token="refresh-a"),
    )

    _sign_in_as(USER_A)
    client.post("/auth/google/start")
    _sign_in_as(USER_B)
    client.post("/auth/google/start")
    assert auth_routes._pending_logins["state-a"].user_id == USER_A
    assert auth_routes._pending_logins["state-b"].user_id == USER_B

    # Google itself calls back, with no authentication at all.
    del app.dependency_overrides[get_current_user]
    response = client.get("/auth/google/callback", params={"code": "auth-code", "state": "state-a"})

    assert response.status_code == 200
    assert list(fake_client.rows) == [USER_A]
    assert fake_client.rows[USER_A]["credentials"]["refresh_token"] == "refresh-a"
    assert "state-a" not in auth_routes._pending_logins
    assert auth_routes._pending_logins["state-b"].user_id == USER_B


def test_callback_reports_a_failure_to_store_the_credential(monkeypatch) -> None:
    """The user has already consented and the authorization code is spent by
    this point, so a storage failure is unrecoverable -- it should say so,
    not surface as a bare 500 with nothing recording that a credential was
    obtained and then dropped."""
    install_fake_supabase(monkeypatch)
    _install_fake_flow(monkeypatch)

    def _explode(user_id: str, credentials) -> None:
        raise RuntimeError("supabase is down")

    monkeypatch.setattr(auth_routes, "save_credentials", _explode)
    client.post("/auth/google/start")
    (state,) = auth_routes._pending_logins

    response = client.get("/auth/google/callback", params={"code": "auth-code", "state": state})

    assert response.status_code == 502
    assert response.json()["detail"] == "failed to store Google credentials"


def test_callback_presents_the_code_verifier_from_start(monkeypatch) -> None:
    """PKCE: the verifier generated while building the authorization URL has
    to survive into the token exchange, a separate request entirely."""
    install_fake_supabase(monkeypatch)
    flow = _install_fake_flow(monkeypatch)
    client.post("/auth/google/start")
    (state,) = auth_routes._pending_logins

    client.get("/auth/google/callback", params={"code": "auth-code", "state": state})

    assert flow.build_calls[-1] == {"state": state, "code_verifier": "pkce-code-verifier"}


def test_callback_rejects_an_unknown_state(monkeypatch) -> None:
    fake_client = install_fake_supabase(monkeypatch)
    _install_fake_flow(monkeypatch)

    response = client.get(
        "/auth/google/callback", params={"code": "irrelevant", "state": "never-issued"}
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "invalid OAuth state"
    assert fake_client.rows == {}


def test_callback_state_is_single_use(monkeypatch) -> None:
    """A replayed callback URL must not mint a second credential write."""
    install_fake_supabase(monkeypatch)
    flow = _install_fake_flow(monkeypatch)
    client.post("/auth/google/start")
    (state,) = auth_routes._pending_logins

    first = client.get("/auth/google/callback", params={"code": "auth-code", "state": state})
    second = client.get("/auth/google/callback", params={"code": "auth-code", "state": state})

    assert first.status_code == 200
    assert second.status_code == 400
    assert second.json()["detail"] == "invalid OAuth state"
    assert flow.fetch_token_calls == ["auth-code"]
    assert auth_routes._pending_logins == {}


def test_callback_rejects_an_expired_state(monkeypatch) -> None:
    fake_client = install_fake_supabase(monkeypatch)
    _install_fake_flow(monkeypatch)
    client.post("/auth/google/start")
    (state,) = auth_routes._pending_logins
    auth_routes._pending_logins[state].expires_at = time.monotonic() - 1

    response = client.get("/auth/google/callback", params={"code": "auth-code", "state": state})

    assert response.status_code == 400
    assert response.json()["detail"] == "invalid OAuth state"
    assert fake_client.rows == {}
    assert auth_routes._pending_logins == {}


def test_callback_state_survives_a_failed_token_exchange_so_it_can_be_retried(
    monkeypatch,
) -> None:
    """Found live (bug audit), and preserved through the per-user rewrite:
    fetch_token is a network call to Google and the most likely thing to
    fail transiently. If the state were dropped before it, retrying the
    same callback URL (a normal thing to do after a 502) would fail with
    "invalid OAuth state" and force a whole new login."""
    fake_client = install_fake_supabase(monkeypatch)
    flow = _install_fake_flow(monkeypatch, fetch_errors=[RuntimeError("network blip")])
    client.post("/auth/google/start")
    (state,) = auth_routes._pending_logins

    failed = client.get("/auth/google/callback", params={"code": "auth-code", "state": state})
    assert failed.status_code == 502
    assert state in auth_routes._pending_logins
    assert fake_client.rows == {}

    retried = client.get("/auth/google/callback", params={"code": "auth-code", "state": state})
    assert retried.status_code == 200
    assert flow.fetch_token_calls == ["auth-code", "auth-code"]
    assert list(fake_client.rows) == [USER_A]


def test_status_reports_not_connected_for_a_user_who_has_not_connected(monkeypatch) -> None:
    install_fake_supabase(monkeypatch)

    response = client.get("/auth/google/status")

    assert response.status_code == 200
    assert response.json() == {"connected": False}


def test_status_reflects_only_the_calling_user(monkeypatch) -> None:
    """Previously a single boolean for the whole server: once anyone had
    connected Gmail, every user's app showed "Check Gmail"."""
    install_fake_supabase(monkeypatch)
    google_auth.save_credentials(USER_A, make_credentials())

    assert client.get("/auth/google/status").json() == {"connected": True}

    _sign_in_as(USER_B)
    assert client.get("/auth/google/status").json() == {"connected": False}


def test_status_requires_authentication(monkeypatch) -> None:
    install_fake_supabase(monkeypatch)
    del app.dependency_overrides[get_current_user]

    response = client.get("/auth/google/status")

    assert response.status_code == 401
    assert response.json()["detail"] == "missing bearer token"


def test_disconnect_clears_only_the_callers_credentials(monkeypatch) -> None:
    fake_client = install_fake_supabase(monkeypatch)
    google_auth.save_credentials(USER_A, make_credentials())
    google_auth.save_credentials(USER_B, make_credentials())

    response = client.post("/auth/google/disconnect")

    assert response.json() == {"connected": False}
    assert list(fake_client.rows) == [USER_B]
    _sign_in_as(USER_B)
    assert client.get("/auth/google/status").json() == {"connected": True}


def test_disconnect_rejects_an_unauthenticated_request(monkeypatch) -> None:
    """Disconnecting is destructive -- it drops a stored Google credential
    and forces a fresh consent flow -- so it must not be reachable without a
    token."""
    fake_client = install_fake_supabase(monkeypatch)
    google_auth.save_credentials(USER_A, make_credentials())
    del app.dependency_overrides[get_current_user]

    response = client.post("/auth/google/disconnect")

    assert response.status_code == 401
    assert response.json()["detail"] == "missing bearer token"
    # The credential survived: rejection has to happen before clear_credentials.
    assert list(fake_client.rows) == [USER_A]


def test_the_unauthenticated_login_redirect_route_is_gone() -> None:
    """GET /auth/google/login could not know which user it was connecting,
    so it was removed rather than fixed -- clients use POST /start and open
    the returned URL themselves."""
    assert client.get("/auth/google/login").status_code == 404
