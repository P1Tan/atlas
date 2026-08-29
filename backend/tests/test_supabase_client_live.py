"""Exercises real Supabase Auth token validation end to end.

Live calls against the real Supabase project (small/free, no cost) -- kept
separate from the fast, deterministic tests in test_supabase_client.py and
skipped automatically when Supabase isn't configured. Creates and tears down
a throwaway test user via the Admin API + a password sign-in, rather than
the real magic-link flow (which needs an actual email inbox) -- this
exercises the same token validation path get_current_user uses, just via a
different sign-in method to obtain a genuine token deterministically.
"""

import logging
import os
import uuid

import pytest
from supabase import create_client

from app.config import SUPABASE_ANON_KEY, SUPABASE_URL
from app.supabase_client import get_current_user, get_supabase_client

pytestmark = pytest.mark.skipif(
    not os.getenv("SUPABASE_URL") or not os.getenv("SUPABASE_SERVICE_ROLE_KEY"),
    reason="SUPABASE_URL/SUPABASE_SERVICE_ROLE_KEY not set",
)


@pytest.fixture
def real_user_token():
    admin_client = get_supabase_client()
    email = f"atlas-test-{uuid.uuid4().hex}@example.com"
    password = uuid.uuid4().hex

    created = admin_client.auth.admin.create_user(
        {"email": email, "password": password, "email_confirm": True}
    )
    user_id = created.user.id

    try:
        anon_client = create_client(SUPABASE_URL, SUPABASE_ANON_KEY)
        session = anon_client.auth.sign_in_with_password({"email": email, "password": password})
        yield session.session.access_token, user_id, email
    finally:
        try:
            admin_client.auth.admin.delete_user(user_id)
        except Exception:
            logging.getLogger(__name__).warning(
                "failed to delete throwaway test user %s -- may need manual cleanup", user_id
            )


def test_get_current_user_resolves_a_real_valid_token(real_user_token) -> None:
    token, user_id, email = real_user_token

    user = get_current_user(authorization=f"Bearer {token}")

    assert user.id == user_id
    assert user.email == email


def test_get_current_user_rejects_a_garbage_token() -> None:
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as exc_info:
        get_current_user(authorization="Bearer not-a-real-token")

    assert exc_info.value.status_code == 401
