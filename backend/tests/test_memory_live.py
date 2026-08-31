"""Exercises SupabaseMemoryStore against the real Supabase project end to end.

Live calls against the real Supabase project (small/free, no cost) -- kept
separate from the fast, deterministic tests in test_memory.py and skipped
automatically when Supabase isn't configured. Mirrors the throwaway-user
pattern in test_supabase_client_live.py so the inserted row satisfies the
user_facts.user_id foreign key against auth.users.
"""

import logging
import os
import time
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


def test_list_facts_returns_inserted_facts_oldest_first(throwaway_user_id) -> None:
    store = SupabaseMemoryStore()
    fact_texts = ["fact one", "fact two", "fact three"]

    for fact_text in fact_texts:
        store.remember_fact(throwaway_user_id, fact_text)
        # created_at ordering needs distinct timestamps -- a tiny sleep
        # between inserts keeps this reliable rather than racing DB clock
        # granularity.
        time.sleep(0.05)

    try:
        facts = store.list_facts(throwaway_user_id)
        assert facts == fact_texts
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


def test_list_fact_records_returns_inserted_facts_newest_first_with_ids(throwaway_user_id) -> None:
    store = SupabaseMemoryStore()
    fact_texts = ["fact one", "fact two", "fact three"]

    for fact_text in fact_texts:
        store.remember_fact(throwaway_user_id, fact_text)
        # created_at ordering needs distinct timestamps -- a tiny sleep
        # between inserts keeps this reliable rather than racing DB clock
        # granularity.
        time.sleep(0.05)

    try:
        records = store.list_fact_records(throwaway_user_id)
        assert [r.fact_text for r in records] == list(reversed(fact_texts))
        assert all(r.id for r in records)
        assert len(set(r.id for r in records)) == len(records)
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


def test_delete_fact_removes_the_row_and_returns_true(throwaway_user_id) -> None:
    store = SupabaseMemoryStore()
    store.remember_fact(throwaway_user_id, "fact to delete")

    try:
        records = store.list_fact_records(throwaway_user_id)
        fact_id = next(r.id for r in records if r.fact_text == "fact to delete")

        deleted = store.delete_fact(throwaway_user_id, fact_id)
        assert deleted is True

        remaining = store.list_fact_records(throwaway_user_id)
        assert all(r.id != fact_id for r in remaining)
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


def test_delete_fact_does_not_delete_when_user_id_does_not_match(throwaway_user_id) -> None:
    """The actual authorization-scoping proof: a correct fact_id paired with
    the WRONG user_id must not delete the row -- the .eq("user_id", ...)
    filter in delete_fact is the only thing standing between a user and
    someone else's data, since the service_role client bypasses RLS."""
    admin_client = get_supabase_client()
    email = f"atlas-test-{uuid.uuid4().hex}@example.com"
    created = admin_client.auth.admin.create_user(
        {"email": email, "password": uuid.uuid4().hex, "email_confirm": True}
    )
    other_user_id = created.user.id

    try:
        store = SupabaseMemoryStore()
        store.remember_fact(throwaway_user_id, "fact owned by throwaway_user_id")

        records = store.list_fact_records(throwaway_user_id)
        fact_id = next(r.id for r in records if r.fact_text == "fact owned by throwaway_user_id")

        deleted = store.delete_fact(other_user_id, fact_id)
        assert deleted is False

        remaining = store.list_fact_records(throwaway_user_id)
        assert any(r.id == fact_id for r in remaining)
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
        try:
            admin_client.auth.admin.delete_user(other_user_id)
        except Exception:
            logging.getLogger(__name__).warning(
                "failed to delete throwaway test user %s -- may need manual cleanup", other_user_id
            )
