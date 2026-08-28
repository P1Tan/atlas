from typing import List

from fastapi.testclient import TestClient

from app.chat import SYSTEM_PROMPT, ChatMessage, get_chat_engine
from app.extraction import ExtractedEventDraft, get_extractor
from app.main import app
from app.weather import get_weather_client
from app.web_search import SearchResponse, get_web_search_client

client = TestClient(app)


class FakeExtractor:
    model_name = "fake-extractor"

    def extract(self, text: str) -> List[ExtractedEventDraft]:
        return []


class FakeWeatherClient:
    def get_weather(self, location: str):
        return None


class FakeWebSearchClient:
    def search(self, query: str) -> SearchResponse:
        return SearchResponse(query=query, answer=None, results=[])


class FakeChatEngine:
    model_name = "fake-chat-engine"

    def __init__(self, response_messages=None, raise_error: bool = False) -> None:
        self._response_messages = response_messages or [ChatMessage(role="assistant", content="hi")]
        self._raise_error = raise_error
        self.received_messages = None
        self.received_tools = None

    def run_turn(self, messages, tools):
        self.received_messages = messages
        self.received_tools = tools
        if self._raise_error:
            raise RuntimeError("boom")
        return self._response_messages


def setup_function() -> None:
    app.dependency_overrides[get_extractor] = lambda: FakeExtractor()
    app.dependency_overrides[get_weather_client] = lambda: FakeWeatherClient()
    app.dependency_overrides[get_web_search_client] = lambda: FakeWebSearchClient()


def teardown_function() -> None:
    app.dependency_overrides.clear()


def _request(messages: list[ChatMessage]) -> dict:
    return {
        "messages": [m.model_dump(exclude_none=True) for m in messages],
        "reference_datetime": "2026-08-26T12:00:00",
        "timezone": "America/New_York",
    }


def test_chat_requires_last_message_to_be_from_user() -> None:
    app.dependency_overrides[get_chat_engine] = lambda: FakeChatEngine()

    response = client.post("/chat", json=_request([ChatMessage(role="assistant", content="hi")]))

    assert response.status_code == 422


def test_chat_prepends_system_prompt_when_missing() -> None:
    fake_engine = FakeChatEngine()
    app.dependency_overrides[get_chat_engine] = lambda: fake_engine

    response = client.post("/chat", json=_request([ChatMessage(role="user", content="hello")]))

    assert response.status_code == 200
    assert fake_engine.received_messages[0].role == "system"
    assert fake_engine.received_messages[0].content == SYSTEM_PROMPT
    assert fake_engine.received_messages[-1].content == "hello"


def test_chat_does_not_duplicate_an_existing_system_prompt() -> None:
    fake_engine = FakeChatEngine()
    app.dependency_overrides[get_chat_engine] = lambda: fake_engine
    messages = [
        ChatMessage(role="system", content="custom system prompt"),
        ChatMessage(role="user", content="hello"),
    ]

    response = client.post("/chat", json=_request(messages))

    assert response.status_code == 200
    assert len([m for m in fake_engine.received_messages if m.role == "system"]) == 1
    assert fake_engine.received_messages[0].content == "custom system prompt"


def test_chat_returns_new_messages_from_the_engine() -> None:
    reply = [ChatMessage(role="assistant", content="Hello! How can I help?")]
    app.dependency_overrides[get_chat_engine] = lambda: FakeChatEngine(response_messages=reply)

    response = client.post("/chat", json=_request([ChatMessage(role="user", content="hi")]))

    assert response.status_code == 200
    data = response.json()
    assert len(data["new_messages"]) == 1
    assert data["new_messages"][0]["content"] == "Hello! How can I help?"


def test_chat_returns_502_when_engine_fails() -> None:
    app.dependency_overrides[get_chat_engine] = lambda: FakeChatEngine(raise_error=True)

    response = client.post("/chat", json=_request([ChatMessage(role="user", content="hi")]))

    assert response.status_code == 502


def test_chat_wires_up_the_registered_tools() -> None:
    fake_engine = FakeChatEngine()
    app.dependency_overrides[get_chat_engine] = lambda: fake_engine

    response = client.post("/chat", json=_request([ChatMessage(role="user", content="hi")]))

    assert response.status_code == 200
    tool_names = {tool.name for tool in fake_engine.received_tools}
    assert tool_names == {"extract_calendar_events", "set_reminder", "get_weather", "web_search"}


def test_email_to_calendar_tool_is_actually_callable_end_to_end() -> None:
    """Proves the pattern, not just the wiring: an engine that behaves like
    a real tool-calling model (calls the tool it was given, then replies)
    gets back real Event data produced by the real extraction pipeline --
    only the LLM extraction step itself is faked, everything else (date
    resolution, ambiguity finalization) runs for real."""

    class ToolCallingFakeEngine:
        model_name = "fake-chat-engine"

        def run_turn(self, messages, tools):
            tool = next(t for t in tools if t.name == "extract_calendar_events")
            result = tool.handler({"text": "Lunch next Tuesday at noon."})
            return [
                ChatMessage(role="assistant", tool_calls=[{"id": "call_1"}]),
                ChatMessage(role="tool", tool_call_id="call_1", content=str(result)),
                ChatMessage(role="assistant", content=f"Found {len(result['events'])} event(s)."),
            ]

    app.dependency_overrides[get_chat_engine] = lambda: ToolCallingFakeEngine()
    app.dependency_overrides[get_extractor] = lambda: RealisticFakeExtractor()

    response = client.post("/chat", json=_request([ChatMessage(role="user", content="Lunch next Tuesday at noon.")]))

    assert response.status_code == 200
    new_messages = response.json()["new_messages"]
    tool_message = next(m for m in new_messages if m["role"] == "tool")
    assert "Lunch" in tool_message["content"]
    assert new_messages[-1]["content"] == "Found 1 event(s)."


class RealisticFakeExtractor:
    model_name = "fake-extractor"

    def extract(self, text: str) -> List[ExtractedEventDraft]:
        return [
            ExtractedEventDraft(
                title="Lunch",
                date_phrase="next Tuesday at noon",
                source_excerpt=text,
                confidence="high",
            )
        ]
