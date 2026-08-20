from typing import List

from fastapi.testclient import TestClient

from app.extraction import ExtractedEventDraft
from app.main import app, get_extractor

EXPECTED_EVENT_KEYS = {
    "title",
    "date_phrase",
    "resolved_start",
    "resolved_end",
    "all_day",
    "location",
    "notes",
    "source_excerpt",
    "confidence",
    "ambiguities",
    "needs_confirmation",
}


class FakeExtractor:
    """Deterministic stand-in for the real LLM extractor, used to test the
    /extract endpoint's contract without making a network call."""

    model_name = "fake-extractor"

    def __init__(self, drafts: List[ExtractedEventDraft]) -> None:
        self._drafts = drafts

    def extract(self, text: str) -> List[ExtractedEventDraft]:
        return self._drafts


def _valid_request() -> dict:
    return {
        "text": "Let's meet next Thursday at 3pm to sync on the launch.",
        "reference_datetime": "2026-08-18T12:00:00",
        "timezone": "America/New_York",
    }


def _override_extractor(drafts: List[ExtractedEventDraft]) -> None:
    app.dependency_overrides[get_extractor] = lambda: FakeExtractor(drafts)


def teardown_function() -> None:
    app.dependency_overrides.clear()


def test_extract_returns_event_list_with_expected_shape() -> None:
    _override_extractor(
        [
            ExtractedEventDraft(
                title="Team sync",
                date_phrase="next Thursday at 3pm",
                source_excerpt="Let's meet next Thursday at 3pm to sync on the launch.",
                confidence="medium",
                ambiguities=["year not specified"],
            )
        ]
    )
    client = TestClient(app)

    response = client.post("/extract", json=_valid_request())
    assert response.status_code == 200

    events = response.json()
    assert isinstance(events, list)
    assert len(events) == 1

    event = events[0]
    assert EXPECTED_EVENT_KEYS.issubset(event.keys())
    assert event["confidence"] in {"high", "medium", "low"}
    assert isinstance(event["ambiguities"], list)
    assert event["needs_confirmation"] is True
    assert event["resolved_start"] is None
    assert event["resolved_end"] is None


def test_extract_returns_empty_list_when_no_events_found() -> None:
    _override_extractor([])
    client = TestClient(app)

    response = client.post("/extract", json=_valid_request())
    assert response.status_code == 200
    assert response.json() == []


def test_extract_requires_text_and_reference_datetime() -> None:
    client = TestClient(app)
    response = client.post("/extract", json={"timezone": "UTC"})
    assert response.status_code == 422


def test_extract_returns_502_on_extractor_failure() -> None:
    class FailingExtractor:
        model_name = "failing-extractor"

        def extract(self, text: str) -> List[ExtractedEventDraft]:
            raise RuntimeError("boom")

    app.dependency_overrides[get_extractor] = lambda: FailingExtractor()
    client = TestClient(app)

    response = client.post("/extract", json=_valid_request())
    assert response.status_code == 502
