from typing import List
from unittest.mock import MagicMock

from fastapi.testclient import TestClient

from app import gmail_routes
from app.extraction import ExtractedEventDraft
from app.gmail_client import GmailMessage
from app.main import app, get_extractor

client = TestClient(app)


class FakeExtractor:
    model_name = "fake-extractor"

    def __init__(self, drafts_by_text: dict) -> None:
        self._drafts_by_text = drafts_by_text

    def extract(self, text: str) -> List[ExtractedEventDraft]:
        return self._drafts_by_text.get(text, [])


def _query() -> dict:
    return {"reference_datetime": "2026-08-18T12:00:00", "timezone": "America/New_York"}


def teardown_function() -> None:
    app.dependency_overrides.clear()


def test_candidates_requires_connected_gmail(monkeypatch) -> None:
    monkeypatch.setattr(gmail_routes, "load_credentials", lambda: None)

    response = client.get("/gmail/candidates", params=_query())
    assert response.status_code == 401


def test_candidates_returns_per_message_events(monkeypatch) -> None:
    fake_credentials = MagicMock(expired=False)
    monkeypatch.setattr(gmail_routes, "load_credentials", lambda: fake_credentials)

    messages = [
        GmailMessage(id="msg-1", subject="Lunch?", body_text="Lunch next Tuesday at noon."),
        GmailMessage(id="msg-2", subject="Newsletter", body_text="Nothing to schedule here."),
    ]
    monkeypatch.setattr(
        gmail_routes, "fetch_recent_unread_messages", lambda credentials, max_results: messages
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

    candidates = response.json()
    assert len(candidates) == 2
    assert candidates[0]["message_id"] == "msg-1"
    assert candidates[0]["subject"] == "Lunch?"
    assert len(candidates[0]["events"]) == 1
    assert candidates[0]["events"][0]["title"] == "Lunch"
    assert candidates[1]["events"] == []


def test_candidates_refreshes_expired_credentials(monkeypatch) -> None:
    fake_credentials = MagicMock(expired=True, refresh_token="a-refresh-token")
    monkeypatch.setattr(gmail_routes, "load_credentials", lambda: fake_credentials)
    monkeypatch.setattr(gmail_routes, "fetch_recent_unread_messages", lambda credentials, max_results: [])

    saved = {}
    monkeypatch.setattr(gmail_routes, "save_credentials", lambda creds: saved.setdefault("creds", creds))

    app.dependency_overrides[get_extractor] = lambda: FakeExtractor({})

    response = client.get("/gmail/candidates", params=_query())
    assert response.status_code == 200
    fake_credentials.refresh.assert_called_once()
    assert saved["creds"] is fake_credentials


def test_candidates_returns_502_on_fetch_failure(monkeypatch) -> None:
    fake_credentials = MagicMock(expired=False)
    monkeypatch.setattr(gmail_routes, "load_credentials", lambda: fake_credentials)

    def _raise(credentials, max_results):
        raise RuntimeError("boom")

    monkeypatch.setattr(gmail_routes, "fetch_recent_unread_messages", _raise)

    response = client.get("/gmail/candidates", params=_query())
    assert response.status_code == 502


def test_candidates_skips_message_that_fails_extraction(monkeypatch) -> None:
    fake_credentials = MagicMock(expired=False)
    monkeypatch.setattr(gmail_routes, "load_credentials", lambda: fake_credentials)

    messages = [
        GmailMessage(id="msg-1", subject="Broken", body_text="trigger failure"),
        GmailMessage(id="msg-2", subject="Fine", body_text="Lunch next Tuesday at noon."),
    ]
    monkeypatch.setattr(
        gmail_routes, "fetch_recent_unread_messages", lambda credentials, max_results: messages
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
    candidates = response.json()
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
    monkeypatch.setattr(gmail_routes, "load_credentials", lambda: fake_credentials)
    monkeypatch.setattr(
        gmail_routes,
        "fetch_recent_unread_messages",
        lambda credentials, max_results: [
            GmailMessage(id="msg-1", subject="Lunch?", body_text="Lunch next Tuesday at noon.")
        ],
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

    candidate = response.json()[0]
    assert set(candidate.keys()) == {"message_id", "subject", "events"}
    assert "body_text" not in candidate
    assert "body" not in candidate
