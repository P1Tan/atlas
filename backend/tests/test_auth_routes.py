from urllib.parse import parse_qs, urlparse

from fastapi.testclient import TestClient

from app import auth_routes, google_auth
from app.main import app

client = TestClient(app, follow_redirects=False)


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
