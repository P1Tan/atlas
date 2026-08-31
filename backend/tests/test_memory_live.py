"""Exercises SupabaseMemoryStore against the real Supabase project end to end.

Live calls against the real Supabase project (small/free, no cost) -- kept
separate from the fast, deterministic tests in test_memory.py and skipped
automatically when Supabase isn't configured. Mirrors the throwaway-user
pattern in test_supabase_client_live.py so the inserted row satisfies the
user_facts.user_id foreign key against auth.users.
"""

import logging
import os
import uuid

import pytest

from app.memory import SupabaseMemoryStore
from app.supabase_client import get_supabase_client

pytestmark = pytest.mark.skipif(
    not os.getenv("SUPABASE_URL") or not os.getenv("SUPABASE_SERVICE_ROLE_KEY"),
    reason="SUPABASE_URL/SUPABASE_SERVICE_ROLE_KEY not set",
)


@pytest.fixture
def throwaway_user_id():
    admin_client = get_supabase_client()
    email = f"atlas-test-{uuid.uuid4().hex}@example.com"

    created = admin_client.auth.admin.create_user(
        {"email": email, "password": uuid.uuid4().hex, "email_confirm": True}
    )
    user_id = created.user.id

    try:
        yield user_id
    finally:
        try:
            admin_client.auth.admin.delete_user(user_id)
        except Exception:
            logging.getLogger(__name__).warning(
                "failed to delete throwaway test user %s -- may need manual cleanup", user_id
            )


def test_remember_fact_inserts_a_row_that_is_readable_back(throwaway_user_id) -> None:
    store = SupabaseMemoryStore()
    fact_text = "test fact from live test"

    store.remember_fact(throwaway_user_id, fact_text)

    try:
        response = (
            get_supabase_client()
            .table("user_facts")
            .select("*")
            .eq("user_id", throwaway_user_id)
            .execute()
        )
        rows = response.data
        assert any(row["fact_text"] == fact_text for row in rows)
    finally:
        try:
            get_supabase_client().table("user_facts").delete().eq(
                "user_id", throwaway_user_id
            ).execute()
        except Exception:
            logging.getLogger(__name__).warning(
                "failed to delete test fact rows for user %s -- may need manual cleanup",
                throwaway_user_id,
            )
