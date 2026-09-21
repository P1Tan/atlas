from urllib.parse import parse_qs, urlparse

from fastapi.testclient import TestClient

from app import auth_routes, google_auth
from app.main import app
from app.supabase_client import AuthenticatedUser, get_current_user

client = TestClient(app, follow_redirects=False)


def setup_function() -> None:
    app.dependency_overrides[get_current_user] = lambda: AuthenticatedUser(
        id="test-user-id", email="test@example.com"
    )


def teardown_function() -> None:
    app.dependency_overrides.clear()


def _use_temp_token_path(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(google_auth, "TOKEN_PATH", tmp_path / "google_token.json")
    monkeypatch.setattr(
        google_auth,
        "GOOGLE_CLIENT_ID",
        "test-client-id.apps.googleusercontent.com",
    )
    monkeypatch.setattr(google_auth, "GOOGLE_CLIENT_SECRET", "test-secret")
    monkeypatch.setattr(
        google_auth, "GOOGLE_REDIRECT_URI", "http://localhost:8000/auth/google/callback"
    )


def test_status_reports_not_connected_when_no_token_stored(tmp_path, monkeypatch) -> None:
    _use_temp_token_path(tmp_path, monkeypatch)

    response = client.get("/auth/google/status")
    assert response.status_code == 200
    assert response.json() == {"connected": False}


def test_status_reports_connected_when_token_file_exists(tmp_path, monkeypatch) -> None:
    _use_temp_token_path(tmp_path, monkeypatch)
    google_auth.TOKEN_PATH.parent.mkdir(parents=True, exist_ok=True)
    google_auth.TOKEN_PATH.write_text("{}")

    response = client.get("/auth/google/status")
    assert response.json() == {"connected": True}


def test_login_redirects_to_google_with_readonly_gmail_scope(tmp_path, monkeypatch) -> None:
    _use_temp_token_path(tmp_path, monkeypatch)

    response = client.get("/auth/google/login")
    assert response.status_code in (302, 307)

    location = response.headers["location"]
    assert location.startswith("https://accounts.google.com/o/oauth2/auth")

    query = parse_qs(urlparse(location).query)
    assert query["scope"] == ["https://www.googleapis.com/auth/gmail.readonly"]
    assert query["access_type"] == ["offline"]
    assert query["prompt"] == ["consent"]
    assert query["redirect_uri"] == ["http://localhost:8000/auth/google/callback"]


def test_callback_rejects_mismatched_state(tmp_path, monkeypatch) -> None:
    _use_temp_token_path(tmp_path, monkeypatch)
    monkeypatch.setattr(auth_routes, "_pending_state", "expected-state")

    response = client.get(
        "/auth/google/callback", params={"code": "irrelevant", "state": "wrong-state"}
    )
    assert response.status_code == 400


def test_disconnect_clears_stored_token(tmp_path, monkeypatch) -> None:
    _use_temp_token_path(tmp_path, monkeypatch)
    google_auth.TOKEN_PATH.parent.mkdir(parents=True, exist_ok=True)
    google_auth.TOKEN_PATH.write_text("{}")

    response = client.post("/auth/google/disconnect")
    assert response.json() == {"connected": False}
    assert not google_auth.TOKEN_PATH.exists()


def test_disconnect_rejects_an_unauthenticated_request(tmp_path, monkeypatch) -> None:
    """Disconnecting is destructive -- it drops the stored Google credential
    and forces a full re-login -- so it must not be reachable without a
    token. Previously anyone who could reach the port could POST this."""
    _use_temp_token_path(tmp_path, monkeypatch)
    google_auth.TOKEN_PATH.parent.mkdir(parents=True, exist_ok=True)
    google_auth.TOKEN_PATH.write_text("{}")
    del app.dependency_overrides[get_current_user]

    response = client.post("/auth/google/disconnect")

    assert response.status_code == 401
    assert response.json()["detail"] == "missing bearer token"
    # The credential survived: rejection has to happen before clear_credentials.
    assert google_auth.TOKEN_PATH.exists()
    assert client.get("/auth/google/status").json() == {"connected": True}


def test_status_and_login_stay_reachable_without_a_token(tmp_path, monkeypatch) -> None:
    """Only /disconnect gained the auth dependency: /status is boolean-only
    and /login is opened in a browser that has no bearer token to send."""
    _use_temp_token_path(tmp_path, monkeypatch)
    del app.dependency_overrides[get_current_user]

    assert client.get("/auth/google/status").status_code == 200
    assert client.get("/auth/google/login").status_code in (302, 307)
