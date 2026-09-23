from typing import List
from unittest.mock import MagicMock

from fastapi.testclient import TestClient
from google.auth.exceptions import RefreshError

from app import gmail_routes, supabase_client
from app.extraction import ExtractedEventDraft
from app.gmail_client import GmailMessage
from app.main import app, get_extractor
from app.rate_limit import RateLimiter, get_gmail_candidates_rate_limiter
from app.supabase_client import AuthenticatedUser, get_current_user

client = TestClient(app)


class FakeExtractor:
    model_name = "fake-extractor"

    def __init__(self, drafts_by_text: dict) -> None:
        self._drafts_by_text = drafts_by_text

    def extract(self, text: str) -> List[ExtractedEventDraft]:
        return self._drafts_by_text.get(text, [])


def _query() -> dict:
    return {"reference_datetime": "2026-08-18T12:00:00", "timezone": "America/New_York"}


def setup_function() -> None:
    app.dependency_overrides[get_current_user] = lambda: AuthenticatedUser(
        id="test-user-id", email="test@example.com"
    )
    # A fresh, effectively-unlimited limiter per test, so the process-wide
    # default one doesn't carry counts across tests (this route's real
    # per-minute limit is smaller than the number of tests in this file
    # that call it). Tests that care about limiting override this again
    # with a tight one.
    permissive = RateLimiter(per_window_limit=1000, window_seconds=60, daily_limit=10000)
    app.dependency_overrides[get_gmail_candidates_rate_limiter] = lambda: permissive


def teardown_function() -> None:
    app.dependency_overrides.clear()


def test_candidates_requires_connected_gmail(monkeypatch) -> None:
    monkeypatch.setattr(gmail_routes, "load_credentials", lambda user_id: None)

    response = client.get("/gmail/candidates", params=_query())
    assert response.status_code == 401
    assert response.json()["detail"] == gmail_routes.GMAIL_NOT_CONNECTED_DETAIL


def test_candidates_rejects_an_unauthenticated_request(monkeypatch) -> None:
    """The inbox read (and the LLM calls it triggers) must not be reachable
    without a token -- previously anyone who could reach the port could
    trigger a real Gmail fetch."""
    del app.dependency_overrides[get_current_user]

    def _must_not_be_called(user_id: str) -> None:
        raise AssertionError("load_credentials reached without authentication")

    monkeypatch.setattr(gmail_routes, "load_credentials", _must_not_be_called)

    response = client.get("/gmail/candidates", params=_query())
    assert response.status_code == 401
    assert response.json()["detail"] == "missing bearer token"


def test_unauthenticated_401_is_distinguishable_from_gmail_not_connected(monkeypatch) -> None:
    """Both failures are 401, so a client branching on status alone would
    tell a signed-out user to reconnect Gmail. The `detail` string is what
    separates them -- assert they really are different."""
    del app.dependency_overrides[get_current_user]
    monkeypatch.setattr(gmail_routes, "load_credentials", lambda user_id: None)

    unauthenticated = client.get("/gmail/candidates", params=_query())

    # Invalid (rather than absent) token: the Supabase lookup failing is
    # what get_current_user turns into its other 401.
    def _raise(*args, **kwargs):
        raise RuntimeError("supabase says no")

    monkeypatch.setattr(supabase_client, "get_supabase_client", _raise)
    invalid_token = client.get(
        "/gmail/candidates", params=_query(), headers={"Authorization": "Bearer nope"}
    )

    app.dependency_overrides[get_current_user] = lambda: AuthenticatedUser(
        id="test-user-id", email="test@example.com"
    )
    not_connected = client.get("/gmail/candidates", params=_query())

    assert unauthenticated.status_code == invalid_token.status_code == 401
    assert not_connected.status_code == 401
    assert unauthenticated.json()["detail"] == "missing bearer token"
    assert invalid_token.json()["detail"] == "invalid or expired token"
    assert not_connected.json()["detail"] == gmail_routes.GMAIL_NOT_CONNECTED_DETAIL
    assert not_connected.json()["detail"] not in {
        unauthenticated.json()["detail"],
        invalid_token.json()["detail"],
    }


def test_candidates_returns_429_once_the_rate_limit_is_exceeded(monkeypatch) -> None:
    """Confirms enforce_gmail_candidates_rate_limit is actually wired into
    the route, via a tiny override limiter rather than the real one."""
    monkeypatch.setattr(gmail_routes, "load_credentials", lambda user_id: None)
    # One instance, reused across both requests -- the override is called
    # per request, so building it inside the lambda would hand each request
    # a limiter with an empty window.
    limiter = RateLimiter(per_window_limit=1, window_seconds=60, daily_limit=100)
    app.dependency_overrides[get_gmail_candidates_rate_limiter] = lambda: limiter

    first = client.get("/gmail/candidates", params=_query())
    second = client.get("/gmail/candidates", params=_query())

    # 401 (not connected) -- the limiter ran and allowed it through.
    assert first.status_code == 401
    assert second.status_code == 429


def test_candidates_returns_per_message_events(monkeypatch) -> None:
    fake_credentials = MagicMock(expired=False)
    monkeypatch.setattr(gmail_routes, "load_credentials", lambda user_id: fake_credentials)

    messages = [
        GmailMessage(id="msg-1", subject="Lunch?", body_text="Lunch next Tuesday at noon."),
        GmailMessage(id="msg-2", subject="Newsletter", body_text="Nothing to schedule here."),
    ]
    monkeypatch.setattr(
        gmail_routes,
        "fetch_recent_unread_messages",
        lambda credentials, max_results, exclude_ids: (messages, 0),
    )

    app.dependency_overrides[get_extractor] = lambda: FakeExtractor(
        {
            "Lunch next Tuesday at noon.": [
                ExtractedEventDraft(
                    title="Lunch",
                    date_phrase="next Tuesday at noon",
                    source_excerpt="Lunch next Tuesday at noon.",
                    confidence="high",
                )
            ],
            "Nothing to schedule here.": [],
        }
    )

    response = client.get("/gmail/candidates", params=_query())
    assert response.status_code == 200

    body = response.json()
    assert body["skipped_reviewed_count"] == 0
    candidates = body["candidates"]
    assert len(candidates) == 2
    assert candidates[0]["message_id"] == "msg-1"
    assert candidates[0]["subject"] == "Lunch?"
    assert len(candidates[0]["events"]) == 1
    assert candidates[0]["events"][0]["title"] == "Lunch"
    assert candidates[1]["events"] == []


def test_candidates_refreshes_expired_credentials(monkeypatch) -> None:
    fake_credentials = MagicMock(expired=True, refresh_token="a-refresh-token")
    monkeypatch.setattr(gmail_routes, "load_credentials", lambda user_id: fake_credentials)
    monkeypatch.setattr(
        gmail_routes,
        "fetch_recent_unread_messages",
        lambda credentials, max_results, exclude_ids: ([], 0),
    )

    saved = {}

    def _save(user_id: str, creds) -> None:
        saved["user_id"] = user_id
        saved["creds"] = creds

    monkeypatch.setattr(gmail_routes, "save_credentials", _save)

    app.dependency_overrides[get_extractor] = lambda: FakeExtractor({})

    response = client.get("/gmail/candidates", params=_query())
    assert response.status_code == 200
    fake_credentials.refresh.assert_called_once()
    assert saved["creds"] is fake_credentials
    # The refreshed credential goes back to the caller's own row.
    assert saved["user_id"] == "test-user-id"


def test_candidates_reads_only_the_calling_users_mailbox(monkeypatch) -> None:
    """Google credentials are per Supabase user now, so whose inbox this
    reads is decided by the authenticated caller -- not by whoever happened
    to connect Gmail last, which is what the old single shared token file
    meant."""
    fake_credentials = MagicMock(expired=False)
    requested_user_ids = []

    def _load(user_id: str):
        requested_user_ids.append(user_id)
        return fake_credentials if user_id == "user-a" else None

    monkeypatch.setattr(gmail_routes, "load_credentials", _load)
    monkeypatch.setattr(
        gmail_routes,
        "fetch_recent_unread_messages",
        lambda credentials, max_results, exclude_ids: ([], 0),
    )
    app.dependency_overrides[get_extractor] = lambda: FakeExtractor({})

    app.dependency_overrides[get_current_user] = lambda: AuthenticatedUser(
        id="user-a", email="a@example.com"
    )
    connected = client.get("/gmail/candidates", params=_query())

    app.dependency_overrides[get_current_user] = lambda: AuthenticatedUser(
        id="user-b", email="b@example.com"
    )
    other_user = client.get("/gmail/candidates", params=_query())

    assert requested_user_ids == ["user-a", "user-b"]
    assert connected.status_code == 200
    assert other_user.status_code == 401
    assert other_user.json()["detail"] == gmail_routes.GMAIL_NOT_CONNECTED_DETAIL


def test_candidates_clears_only_the_callers_credentials_when_refresh_fails(monkeypatch) -> None:
    """An unusable refresh token clears the stored credential so /status
    stops claiming a working connection -- for that user alone."""
    fake_credentials = MagicMock(expired=True, refresh_token="a-refresh-token")
    fake_credentials.refresh.side_effect = RefreshError("token revoked")
    monkeypatch.setattr(gmail_routes, "load_credentials", lambda user_id: fake_credentials)
    cleared = []
    monkeypatch.setattr(gmail_routes, "clear_credentials", lambda user_id: cleared.append(user_id))

    response = client.get("/gmail/candidates", params=_query())

    assert response.status_code == 401
    assert response.json()["detail"] == gmail_routes.GMAIL_NOT_CONNECTED_DETAIL
    assert cleared == ["test-user-id"]


def test_candidates_returns_502_on_fetch_failure(monkeypatch) -> None:
    fake_credentials = MagicMock(expired=False)
    monkeypatch.setattr(gmail_routes, "load_credentials", lambda user_id: fake_credentials)

    def _raise(credentials, max_results, exclude_ids):
        raise RuntimeError("boom")

    monkeypatch.setattr(gmail_routes, "fetch_recent_unread_messages", _raise)

    response = client.get("/gmail/candidates", params=_query())
    assert response.status_code == 502


def test_candidates_skips_message_that_fails_extraction(monkeypatch) -> None:
    fake_credentials = MagicMock(expired=False)
    monkeypatch.setattr(gmail_routes, "load_credentials", lambda user_id: fake_credentials)

    messages = [
        GmailMessage(id="msg-1", subject="Broken", body_text="trigger failure"),
        GmailMessage(id="msg-2", subject="Fine", body_text="Lunch next Tuesday at noon."),
    ]
    monkeypatch.setattr(
        gmail_routes,
        "fetch_recent_unread_messages",
        lambda credentials, max_results, exclude_ids: (messages, 0),
    )

    class PartiallyFailingExtractor:
        model_name = "fake-extractor"

        def extract(self, text: str) -> List[ExtractedEventDraft]:
            if text == "trigger failure":
                raise RuntimeError("LLM blew up")
            return [
                ExtractedEventDraft(
                    title="Lunch",
                    date_phrase="next Tuesday at noon",
                    source_excerpt=text,
                    confidence="high",
                )
            ]

    app.dependency_overrides[get_extractor] = lambda: PartiallyFailingExtractor()

    response = client.get("/gmail/candidates", params=_query())
    assert response.status_code == 200
    candidates = response.json()["candidates"]
    assert len(candidates) == 1
    assert candidates[0]["message_id"] == "msg-2"


def test_max_results_is_capped_by_query_validation() -> None:
    query = _query()
    query["max_results"] = 999
    response = client.get("/gmail/candidates", params=query)
    assert response.status_code == 422


def test_candidate_response_never_includes_full_message_body(monkeypatch) -> None:
    """Excerpt-only retention guardrail: the API contract itself must not
    have a field a future change could accidentally populate with a full
    email body -- only message_id, subject, and the already-excerpted
    events are allowed."""
    fake_credentials = MagicMock(expired=False)
    monkeypatch.setattr(gmail_routes, "load_credentials", lambda user_id: fake_credentials)
    monkeypatch.setattr(
        gmail_routes,
        "fetch_recent_unread_messages",
        lambda credentials, max_results, exclude_ids: (
            [GmailMessage(id="msg-1", subject="Lunch?", body_text="Lunch next Tuesday at noon.")],
            0,
        ),
    )
    app.dependency_overrides[get_extractor] = lambda: FakeExtractor(
        {
            "Lunch next Tuesday at noon.": [
                ExtractedEventDraft(
                    title="Lunch",
                    date_phrase="next Tuesday at noon",
                    source_excerpt="Lunch next Tuesday at noon.",
                    confidence="high",
                )
            ]
        }
    )

    response = client.get("/gmail/candidates", params=_query())
    assert response.status_code == 200

    candidate = response.json()["candidates"][0]
    assert set(candidate.keys()) == {"message_id", "subject", "events"}
    assert "body_text" not in candidate
    assert "body" not in candidate


def test_candidates_passes_exclusion_ids_through_to_the_gmail_client(monkeypatch) -> None:
    fake_credentials = MagicMock(expired=False)
    monkeypatch.setattr(gmail_routes, "load_credentials", lambda user_id: fake_credentials)

    seen = {}

    def _fetch(credentials, max_results, exclude_ids):
        seen["exclude_ids"] = list(exclude_ids)
        return [], 2

    monkeypatch.setattr(gmail_routes, "fetch_recent_unread_messages", _fetch)
    app.dependency_overrides[get_extractor] = lambda: FakeExtractor({})

    query = _query()
    query["exclude_message_ids"] = ["msg-1", "msg-2"]
    response = client.get("/gmail/candidates", params=query)

    assert response.status_code == 200
    assert seen["exclude_ids"] == ["msg-1", "msg-2"]
    assert response.json() == {"candidates": [], "skipped_reviewed_count": 2}


def test_candidates_rejects_more_exclusion_ids_than_the_cap() -> None:
    query = _query()
    query["exclude_message_ids"] = [f"msg-{i}" for i in range(gmail_routes.MAX_EXCLUDE_IDS + 1)]

    response = client.get("/gmail/candidates", params=query)
    assert response.status_code == 422


def test_candidates_rejects_an_empty_exclusion_id() -> None:
    query = _query()
    query["exclude_message_ids"] = ["msg-1", ""]

    response = client.get("/gmail/candidates", params=query)
    assert response.status_code == 422


def test_candidates_rejects_an_oversized_exclusion_id() -> None:
    query = _query()
    query["exclude_message_ids"] = ["x" * (gmail_routes.MAX_EXCLUDE_ID_CHARS + 1)]

    response = client.get("/gmail/candidates", params=query)
    assert response.status_code == 422


def test_candidates_accepts_an_exclusion_id_at_the_length_limit(monkeypatch) -> None:
    fake_credentials = MagicMock(expired=False)
    monkeypatch.setattr(gmail_routes, "load_credentials", lambda user_id: fake_credentials)
    monkeypatch.setattr(
        gmail_routes,
        "fetch_recent_unread_messages",
        lambda credentials, max_results, exclude_ids: ([], 0),
    )
    app.dependency_overrides[get_extractor] = lambda: FakeExtractor({})

    query = _query()
    query["exclude_message_ids"] = ["x" * gmail_routes.MAX_EXCLUDE_ID_CHARS]

    response = client.get("/gmail/candidates", params=query)
    assert response.status_code == 200
