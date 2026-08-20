"""Exercises the real OpenAI extractor against a few sample emails.

These make live API calls (small cost, non-deterministic wording) so they're
kept separate from the fast, deterministic /extract endpoint tests and are
skipped automatically when no API key is configured.
"""

import os

import pytest

from app.extraction import OpenAIEventExtractor

pytestmark = pytest.mark.skipif(
    not os.getenv("OPENAI_API_KEY"), reason="OPENAI_API_KEY not set"
)


@pytest.fixture(scope="module")
def extractor() -> OpenAIEventExtractor:
    return OpenAIEventExtractor()


def test_extracts_single_event_with_date_phrase_uninterpreted(extractor) -> None:
    text = (
        "Hey, can you do a call next Thursday at 3pm to go over the launch plan? "
        "Let me know if that works."
    )
    events = extractor.extract(text)

    assert len(events) == 1
    event = events[0]
    assert "thursday" in event.date_phrase.lower()
    assert event.confidence in {"high", "medium", "low"}
    assert event.source_excerpt


def test_extracts_multiple_events_from_one_email(extractor) -> None:
    text = (
        "Quick agenda for the week: dentist appointment Monday at 9am, "
        "then a design review Wednesday at 2pm in the downtown office."
    )
    events = extractor.extract(text)

    assert len(events) == 2
    titles = " ".join(e.title.lower() for e in events)
    assert "dentist" in titles or "design" in titles


def test_no_event_in_plain_text_returns_empty_list(extractor) -> None:
    text = "Thanks for the update, sounds great. Talk soon!"
    events = extractor.extract(text)

    assert events == []


def test_ambiguous_year_is_flagged_not_guessed(extractor) -> None:
    text = "Reminder: rent is due on the 1st."
    events = extractor.extract(text)

    assert len(events) == 1
    event = events[0]
    assert event.confidence in {"medium", "low"}
    assert len(event.ambiguities) > 0
