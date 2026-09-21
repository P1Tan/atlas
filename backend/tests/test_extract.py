from typing import List

from fastapi.testclient import TestClient

from app.extraction import ExtractedEventDraft
from app.main import app, get_extractor
from app.rate_limit import RateLimiter, get_extract_rate_limiter
from app.supabase_client import AuthenticatedUser, get_current_user

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


def setup_function() -> None:
    app.dependency_overrides[get_current_user] = lambda: AuthenticatedUser(
        id="test-user-id", email="test@example.com"
    )
    # A fresh, effectively-unlimited limiter per test, so the process-wide
    # default one doesn't carry counts across tests (this route's real
    # per-minute limit is smaller than the number of tests in this file
    # that call it). The test that cares about limiting overrides this
    # again with a tight one.
    permissive = RateLimiter(per_window_limit=1000, window_seconds=60, daily_limit=10000)
    app.dependency_overrides[get_extract_rate_limiter] = lambda: permissive


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
    # reference_datetime in _valid_request() is a Tuesday; "next Thursday at
    # 3pm" should resolve to that Thursday, 3pm, in America/New_York.
    assert event["resolved_start"] == "2026-08-20T15:00:00-04:00"
    assert event["resolved_end"] is None
    assert event["all_day"] is False


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


def test_extract_downgrades_confidence_when_date_is_unresolvable() -> None:
    _override_extractor(
        [
            ExtractedEventDraft(
                title="Follow up",
                date_phrase="sometime soon",
                source_excerpt="Let's follow up sometime soon.",
                confidence="high",
            )
        ]
    )
    client = TestClient(app)

    response = client.post("/extract", json=_valid_request())
    assert response.status_code == 200

    event = response.json()[0]
    assert event["resolved_start"] is None
    assert event["confidence"] == "low"
    assert any("sometime soon" in note for note in event["ambiguities"])


def test_extract_caps_high_confidence_when_llm_reports_ambiguities() -> None:
    _override_extractor(
        [
            ExtractedEventDraft(
                title="Team sync",
                date_phrase="next Thursday at 3pm",
                source_excerpt="Let's meet next Thursday at 3pm to sync on the launch.",
                confidence="high",
                ambiguities=["year not specified"],
            )
        ]
    )
    client = TestClient(app)

    response = client.post("/extract", json=_valid_request())
    assert response.status_code == 200

    event = response.json()[0]
    assert event["resolved_start"] is not None
    assert event["confidence"] == "medium"


def test_extract_returns_422_on_unresolvable_timezone() -> None:
    _override_extractor(
        [
            ExtractedEventDraft(
                title="Team sync",
                date_phrase="next Thursday at 3pm",
                source_excerpt="Let's meet next Thursday at 3pm to sync on the launch.",
                confidence="medium",
            )
        ]
    )
    client = TestClient(app)

    request = _valid_request()
    request["timezone"] = "Not/A_Zone"
    response = client.post("/extract", json=request)
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


def test_extract_rejects_an_unauthenticated_request() -> None:
    """The LLM call this route spends must not be reachable without a token
    -- previously anyone who could reach the port could trigger a real
    extraction."""
    del app.dependency_overrides[get_current_user]

    class MustNotBeCalledExtractor:
        model_name = "must-not-be-called"

        def extract(self, text: str) -> List[ExtractedEventDraft]:
            raise AssertionError("extractor reached without authentication")

    app.dependency_overrides[get_extractor] = lambda: MustNotBeCalledExtractor()
    client = TestClient(app)

    response = client.post("/extract", json=_valid_request())
    assert response.status_code == 401
    assert response.json()["detail"] == "missing bearer token"


def test_extract_returns_429_once_the_rate_limit_is_exceeded() -> None:
    """Confirms enforce_extract_rate_limit is actually wired into the route,
    via a tiny override limiter rather than the real one."""
    _override_extractor([])
    # One instance, reused across both requests -- the override is called
    # per request, so building it inside the lambda would hand each request
    # a limiter with an empty window.
    limiter = RateLimiter(per_window_limit=1, window_seconds=60, daily_limit=100)
    app.dependency_overrides[get_extract_rate_limiter] = lambda: limiter
    client = TestClient(app)

    first = client.post("/extract", json=_valid_request())
    second = client.post("/extract", json=_valid_request())

    assert first.status_code == 200
    assert second.status_code == 429
