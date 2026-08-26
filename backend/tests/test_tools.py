from datetime import datetime
from typing import List

from app.extraction import ExtractedEventDraft
from app.tools import build_tools


class FakeExtractor:
    model_name = "fake-extractor"

    def __init__(self, drafts: List[ExtractedEventDraft]) -> None:
        self._drafts = drafts

    def extract(self, text: str) -> List[ExtractedEventDraft]:
        return self._drafts


def test_build_tools_returns_the_email_to_calendar_tool() -> None:
    tools = build_tools(datetime(2026, 8, 26, 12, 0), "America/New_York", FakeExtractor([]))

    assert len(tools) == 1
    tool = tools[0]
    assert tool.name == "extract_calendar_events"
    schema = tool.to_openai_schema()
    assert schema["function"]["name"] == "extract_calendar_events"
    assert schema["function"]["parameters"]["required"] == ["text"]


def test_tool_handler_runs_the_real_extraction_pipeline() -> None:
    extractor = FakeExtractor(
        [
            ExtractedEventDraft(
                title="Lunch",
                date_phrase="next Tuesday at noon",
                source_excerpt="Lunch next Tuesday at noon.",
                confidence="high",
            )
        ]
    )
    tools = build_tools(datetime(2026, 8, 26, 12, 0), "America/New_York", extractor)
    tool = tools[0]

    result = tool.handler({"text": "Lunch next Tuesday at noon."})

    assert len(result["events"]) == 1
    event = result["events"][0]
    assert event["title"] == "Lunch"
    # Real date resolution ran (not the model), against the reference datetime given.
    assert event["resolved_start"] is not None
    assert event["needs_confirmation"] is True


def test_tool_handler_returns_empty_events_for_non_scheduling_text() -> None:
    tools = build_tools(datetime(2026, 8, 26, 12, 0), "America/New_York", FakeExtractor([]))

    result = tools[0].handler({"text": "Thanks, sounds great!"})

    assert result == {"events": []}
