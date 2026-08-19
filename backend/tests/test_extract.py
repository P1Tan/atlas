from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)

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


def _valid_request() -> dict:
    return {
        "text": "Let's meet next Thursday at 3pm to sync on the launch.",
        "reference_datetime": "2026-08-18T12:00:00",
        "timezone": "America/New_York",
    }


def test_extract_returns_event_list_with_expected_shape() -> None:
    response = client.post("/extract", json=_valid_request())
    assert response.status_code == 200

    events = response.json()
    assert isinstance(events, list)
    assert len(events) >= 1

    event = events[0]
    assert EXPECTED_EVENT_KEYS.issubset(event.keys())
    assert event["confidence"] in {"high", "medium", "low"}
    assert isinstance(event["ambiguities"], list)
    assert isinstance(event["needs_confirmation"], bool)


def test_extract_requires_text_and_reference_datetime() -> None:
    response = client.post("/extract", json={"timezone": "UTC"})
    assert response.status_code == 422
