"""Fast, deterministic tests for get_current_user's own logic -- no network
calls. Live token-validation tests are in test_supabase_client_live.py."""

from unittest.mock import MagicMock, patch

from fastapi import HTTPException
import pytest

from app import supabase_client
from app.supabase_client import AuthenticatedUser, get_current_user


def test_get_current_user_rejects_a_missing_bearer_prefix() -> None:
    with pytest.raises(HTTPException) as exc_info:
        get_current_user(authorization="just-a-token-no-prefix")

    assert exc_info.value.status_code == 401


def test_get_current_user_rejects_an_empty_authorization_header() -> None:
    with pytest.raises(HTTPException) as exc_info:
        get_current_user(authorization="")

    assert exc_info.value.status_code == 401


def test_get_current_user_returns_401_when_supabase_call_raises() -> None:
    fake_client = MagicMock()
    fake_client.auth.get_user.side_effect = RuntimeError("network blip")

    with patch.object(supabase_client, "get_supabase_client", return_value=fake_client):
        with pytest.raises(HTTPException) as exc_info:
            get_current_user(authorization="Bearer some-token")

    assert exc_info.value.status_code == 401


def test_get_current_user_returns_401_when_response_user_is_none() -> None:
    fake_client = MagicMock()
    fake_client.auth.get_user.return_value = MagicMock(user=None)

    with patch.object(supabase_client, "get_supabase_client", return_value=fake_client):
        with pytest.raises(HTTPException) as exc_info:
            get_current_user(authorization="Bearer some-token")

    assert exc_info.value.status_code == 401


def test_get_current_user_returns_401_when_response_is_none() -> None:
    fake_client = MagicMock()
    fake_client.auth.get_user.return_value = None

    with patch.object(supabase_client, "get_supabase_client", return_value=fake_client):
        with pytest.raises(HTTPException) as exc_info:
            get_current_user(authorization="Bearer some-token")

    assert exc_info.value.status_code == 401


def test_get_current_user_returns_401_when_user_object_is_malformed() -> None:
    """A response.user missing an expected attribute must fail closed (401),
    not surface as an unhandled 500 -- the case the security-reviewer
    subagent flagged."""
    fake_user = object()  # has no .id/.email at all
    fake_client = MagicMock()
    fake_client.auth.get_user.return_value = MagicMock(user=fake_user)

    with patch.object(supabase_client, "get_supabase_client", return_value=fake_client):
        with pytest.raises(HTTPException) as exc_info:
            get_current_user(authorization="Bearer some-token")

    assert exc_info.value.status_code == 401


def test_get_current_user_returns_the_authenticated_user_on_success() -> None:
    fake_supabase_user = MagicMock(id="user-123", email="person@example.com")
    fake_client = MagicMock()
    fake_client.auth.get_user.return_value = MagicMock(user=fake_supabase_user)

    with patch.object(supabase_client, "get_supabase_client", return_value=fake_client):
        user = get_current_user(authorization="Bearer some-token")

    assert user == AuthenticatedUser(id="user-123", email="person@example.com")
