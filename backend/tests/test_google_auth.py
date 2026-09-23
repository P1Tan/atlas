"""Per-user Google credential storage (app.google_auth).

These replaced a single shared token file (backend/.data/google_token.json):
with more than one signed-in user, the second person to connect Gmail
overwrote the first, and every user's /gmail/candidates then read that one
inbox. The isolation tests below are the ones that would have caught it.

The Supabase client is faked the way the storage tests in test_memory.py
fake it (monkeypatching the module's get_supabase_client), but with a fake
that actually honours the .eq("user_id", ...) filters -- that filter is the
only authorization check google_auth has (the backend uses the service_role
client, which bypasses Row Level Security), so a fake that ignored filters
would let the isolation tests pass for the wrong reason.
"""

from types import SimpleNamespace
from typing import Dict, List, Optional, Tuple

import pytest
from google.oauth2.credentials import Credentials

from app import google_auth

USER_A = "11111111-1111-1111-1111-111111111111"
USER_B = "22222222-2222-2222-2222-222222222222"


class _FakeTable:
    def __init__(self, client: "FakeSupabaseClient", name: str) -> None:
        self._client = client
        self._name = name
        self._operation: Optional[str] = None
        self._payload: Optional[dict] = None
        self._filters: List[Tuple[str, str]] = []

    def upsert(self, payload: dict, on_conflict: Optional[str] = None) -> "_FakeTable":
        self._operation = "upsert"
        self._payload = payload
        self._client.on_conflict_args.append(on_conflict)
        return self

    def select(self, columns: str) -> "_FakeTable":
        self._operation = "select"
        return self

    def delete(self) -> "_FakeTable":
        self._operation = "delete"
        return self

    def eq(self, column: str, value: str) -> "_FakeTable":
        self._filters.append((column, value))
        return self

    def _matching_rows(self) -> List[dict]:
        rows = list(self._client.rows.values())
        for column, value in self._filters:
            rows = [row for row in rows if row.get(column) == value]
        return rows

    def execute(self) -> SimpleNamespace:
        self._client.calls.append((self._name, self._operation, list(self._filters)))
        if self._operation == "upsert":
            assert self._payload is not None
            # user_id is the table's primary key, so an upsert replaces in
            # place rather than adding a row.
            self._client.rows[self._payload["user_id"]] = dict(self._payload)
            return SimpleNamespace(data=[dict(self._payload)])
        if self._operation == "select":
            return SimpleNamespace(data=[dict(row) for row in self._matching_rows()])
        if self._operation == "delete":
            deleted = self._matching_rows()
            for row in deleted:
                del self._client.rows[row["user_id"]]
            return SimpleNamespace(data=[dict(row) for row in deleted])
        raise AssertionError(f"unexpected PostgREST operation {self._operation!r}")


class FakeSupabaseClient:
    """In-memory stand-in for the service_role client, keyed on user_id like
    the real user_google_credentials table."""

    def __init__(self, rows: Optional[Dict[str, dict]] = None) -> None:
        self.rows: Dict[str, dict] = dict(rows or {})
        self.calls: List[Tuple[str, Optional[str], List[Tuple[str, str]]]] = []
        self.on_conflict_args: List[Optional[str]] = []
        self.table_names: List[str] = []

    def table(self, name: str) -> _FakeTable:
        self.table_names.append(name)
        return _FakeTable(self, name)


def install_fake_supabase(monkeypatch, rows: Optional[Dict[str, dict]] = None) -> FakeSupabaseClient:
    fake_client = FakeSupabaseClient(rows)
    monkeypatch.setattr(google_auth, "get_supabase_client", lambda: fake_client)
    return fake_client


def make_credentials(token: str = "access-token", refresh_token: str = "refresh-token") -> Credentials:
    return Credentials(
        token=token,
        refresh_token=refresh_token,
        token_uri="https://oauth2.googleapis.com/token",
        client_id="test-client-id.apps.googleusercontent.com",
        client_secret="test-secret",
        scopes=google_auth.SCOPES,
    )


def test_save_credentials_upserts_a_single_row_keyed_on_the_user(monkeypatch) -> None:
    fake_client = install_fake_supabase(monkeypatch)

    google_auth.save_credentials(USER_A, make_credentials())

    assert fake_client.table_names == [google_auth.CREDENTIALS_TABLE]
    assert fake_client.on_conflict_args == ["user_id"]
    assert list(fake_client.rows) == [USER_A]
    row = fake_client.rows[USER_A]
    # jsonb column: the dict itself, not a JSON string stuffed into one.
    assert isinstance(row["credentials"], dict)
    assert row["credentials"]["refresh_token"] == "refresh-token"
    # `default now()` only fires on insert, and this is usually an update.
    assert row["updated_at"]


def test_save_credentials_replaces_that_users_previous_credential(monkeypatch) -> None:
    fake_client = install_fake_supabase(monkeypatch)

    google_auth.save_credentials(USER_A, make_credentials(token="first"))
    google_auth.save_credentials(USER_A, make_credentials(token="second"))

    assert list(fake_client.rows) == [USER_A]
    assert fake_client.rows[USER_A]["credentials"]["token"] == "second"


def test_load_credentials_round_trips_what_save_stored(monkeypatch) -> None:
    install_fake_supabase(monkeypatch)
    google_auth.save_credentials(USER_A, make_credentials())

    loaded = google_auth.load_credentials(USER_A)

    assert loaded is not None
    assert loaded.token == "access-token"
    assert loaded.refresh_token == "refresh-token"
    assert loaded.scopes == google_auth.SCOPES


def test_one_users_credentials_are_invisible_to_another_user(monkeypatch) -> None:
    """The privacy leak this whole change exists to fix: with a single
    shared credential, user B's /gmail/candidates read user A's inbox."""
    install_fake_supabase(monkeypatch)
    google_auth.save_credentials(USER_A, make_credentials())

    assert google_auth.load_credentials(USER_B) is None
    assert google_auth.has_credentials(USER_B) is False
    # ...and A is untouched by B having looked.
    assert google_auth.has_credentials(USER_A) is True


def test_a_second_user_connecting_does_not_overwrite_the_first(monkeypatch) -> None:
    """Exactly the old file-based failure: one path, last writer wins."""
    install_fake_supabase(monkeypatch)

    google_auth.save_credentials(USER_A, make_credentials(refresh_token="refresh-a"))
    google_auth.save_credentials(USER_B, make_credentials(refresh_token="refresh-b"))

    loaded_a = google_auth.load_credentials(USER_A)
    loaded_b = google_auth.load_credentials(USER_B)
    assert loaded_a is not None and loaded_b is not None
    assert loaded_a.refresh_token == "refresh-a"
    assert loaded_b.refresh_token == "refresh-b"


def test_load_credentials_returns_none_when_the_user_has_never_connected(monkeypatch) -> None:
    install_fake_supabase(monkeypatch)

    assert google_auth.load_credentials(USER_A) is None
    assert google_auth.has_credentials(USER_A) is False


def test_clear_credentials_deletes_only_that_users_row(monkeypatch) -> None:
    fake_client = install_fake_supabase(monkeypatch)
    google_auth.save_credentials(USER_A, make_credentials())
    google_auth.save_credentials(USER_B, make_credentials())

    google_auth.clear_credentials(USER_A)

    assert list(fake_client.rows) == [USER_B]
    assert google_auth.has_credentials(USER_A) is False
    assert google_auth.has_credentials(USER_B) is True


def test_every_read_and_write_filters_on_user_id(monkeypatch) -> None:
    """The .eq("user_id", ...) filter is the sole authorization check --
    the service_role client bypasses Row Level Security -- so assert it is
    present on every query, not just that the results happen to look right
    (same reasoning as memory.delete_fact's own contract)."""
    fake_client = install_fake_supabase(monkeypatch)

    google_auth.save_credentials(USER_A, make_credentials())
    google_auth.load_credentials(USER_A)
    google_auth.has_credentials(USER_A)
    google_auth.clear_credentials(USER_A)

    operations = [(operation, filters) for _, operation, filters in fake_client.calls]
    assert operations == [
        # The upsert is keyed by the user_id in its payload, checked above.
        ("upsert", []),
        ("select", [("user_id", USER_A)]),
        ("select", [("user_id", USER_A)]),
        ("delete", [("user_id", USER_A)]),
    ]
    assert {name for name, _, _ in fake_client.calls} == {google_auth.CREDENTIALS_TABLE}


def test_load_credentials_clears_a_malformed_row_and_reports_not_connected(monkeypatch) -> None:
    """Tolerance preserved from the file-based version: an undecodable
    stored credential collapses into the already-handled "not connected"
    case instead of raising a bare 500, and is cleared so has_credentials()
    stops claiming a connection that doesn't work."""
    fake_client = install_fake_supabase(
        monkeypatch,
        rows={
            USER_A: {"user_id": USER_A, "credentials": {"not": "a credential"}},
            USER_B: {"user_id": USER_B, "credentials": {"also": "broken"}},
        },
    )

    assert google_auth.load_credentials(USER_A) is None
    assert google_auth.has_credentials(USER_A) is False
    # Only the unreadable row it was asked for, not everyone's.
    assert list(fake_client.rows) == [USER_B]


def test_load_credentials_propagates_a_real_database_error(monkeypatch) -> None:
    """A Supabase failure is not "this user isn't connected" -- only an
    undecodable credential is. It must not be swallowed into None."""

    class _ExplodingClient:
        def table(self, name: str):
            raise RuntimeError("supabase is down")

    monkeypatch.setattr(google_auth, "get_supabase_client", _ExplodingClient)

    with pytest.raises(RuntimeError):
        google_auth.load_credentials(USER_A)
