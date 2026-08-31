from datetime import datetime
from typing import List, Optional

from app.chat import ToolDefinition
from app.extraction import ExtractedEventDraft
from app.tools import build_tools
from app.weather import WeatherResult
from app.web_search import SearchResponse


class FakeExtractor:
    model_name = "fake-extractor"

    def __init__(self, drafts: List[ExtractedEventDraft]) -> None:
        self._drafts = drafts

    def extract(self, text: str) -> List[ExtractedEventDraft]:
        return self._drafts


class FakeWeatherClient:
    def __init__(self, result: Optional[WeatherResult] = None) -> None:
        self._result = result

    def get_weather(self, location: str) -> Optional[WeatherResult]:
        return self._result


class FakeWebSearchClient:
    def __init__(self, response: Optional[SearchResponse] = None) -> None:
        self._response = response or SearchResponse(query="", answer=None, results=[])

    def search(self, query: str) -> SearchResponse:
        return self._response


class FakeMemoryStore:
    def __init__(self) -> None:
        self.received_user_id = None
        self.received_fact_text = None

    def remember_fact(self, user_id: str, fact_text: str) -> None:
        self.received_user_id = user_id
        self.received_fact_text = fact_text


def _build_tools(
    extractor=None, weather_client=None, search_client=None, user_id="test-user-id", memory_store=None
) -> List[ToolDefinition]:
    return build_tools(
        datetime(2026, 8, 26, 12, 0),
        "America/New_York",
        extractor or FakeExtractor([]),
        weather_client or FakeWeatherClient(),
        search_client or FakeWebSearchClient(),
        user_id,
        memory_store or FakeMemoryStore(),
    )


def test_build_tools_returns_the_email_to_calendar_tool() -> None:
    tools = _build_tools()

    assert len(tools) == 5
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
    tools = _build_tools(extractor=extractor)
    tool = tools[0]

    result = tool.handler({"text": "Lunch next Tuesday at noon."})

    assert len(result["events"]) == 1
    event = result["events"][0]
    assert event["title"] == "Lunch"
    # Real date resolution ran (not the model), against the reference datetime given.
    assert event["resolved_start"] is not None
    assert event["needs_confirmation"] is True


def test_tool_handler_returns_empty_events_for_non_scheduling_text() -> None:
    tools = _build_tools()

    result = tools[0].handler({"text": "Thanks, sounds great!"})

    assert result == {"events": []}


def _set_reminder_tool() -> ToolDefinition:
    tools = _build_tools()
    tool = next(t for t in tools if t.name == "set_reminder")
    return tool


def test_set_reminder_succeeds_for_a_specific_date_and_time() -> None:
    tool = _set_reminder_tool()

    result = tool.handler({"title": "Call the dentist", "date_phrase": "tomorrow at 10am"})

    assert result == {
        "ok": True,
        "title": "Call the dentist",
        "trigger_time": "2026-08-27T10:00:00-04:00",
    }


def test_set_reminder_fails_when_only_a_date_is_given() -> None:
    tool = _set_reminder_tool()

    result = tool.handler({"title": "Call the dentist", "date_phrase": "tomorrow"})

    assert result["ok"] is False
    assert "reason" in result


def test_set_reminder_fails_when_phrase_is_unresolvable() -> None:
    tool = _set_reminder_tool()

    result = tool.handler({"title": "Call the dentist", "date_phrase": "blorgle nonsense"})

    assert result["ok"] is False
    assert "reason" in result


def test_set_reminder_fails_when_resolved_time_is_already_past() -> None:
    # Reference is noon; "9am" today resolves but is already behind it -- a
    # notification trigger built from a past time would silently never fire.
    tool = _set_reminder_tool()

    result = tool.handler({"title": "Call the dentist", "date_phrase": "today at 9am"})

    assert result["ok"] is False
    assert "reason" in result
