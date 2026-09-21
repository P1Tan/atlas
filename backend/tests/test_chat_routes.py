from typing import List, Optional

from fastapi.testclient import TestClient

from app.chat import (
    SYSTEM_PROMPT,
    ChatMessage,
    build_memory_note,
    build_system_prompt,
    get_chat_engine,
)
from app.chat_routes import _MAX_CHAT_MESSAGE_CHARS, _MAX_CHAT_MESSAGES
from app.config import PERSONA
from app.extraction import ExtractedEventDraft, get_extractor
from app.main import app
from app.memory import get_memory_store
from app.rate_limit import RateLimiter, get_chat_rate_limiter
from app.supabase_client import AuthenticatedUser, get_current_user
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


class FakeMemoryStore:
    def __init__(self, facts: Optional[List[str]] = None) -> None:
        self._facts = facts or []
        self.received_user_id = None
        self.received_query_text = None
        self.received_limit = None

    def remember_fact(self, user_id: str, fact_text: str) -> None:
        pass

    def search_facts(self, user_id: str, query_text: str, limit: int = 10) -> List[str]:
        self.received_user_id = user_id
        self.received_query_text = query_text
        self.received_limit = limit
        return self._facts

    def update_fact(self, user_id: str, fact_id: str, fact_text: str):
        # Part of the MemoryStore Protocol; /chat never edits facts.
        return None


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
    app.dependency_overrides[get_current_user] = lambda: AuthenticatedUser(
        id="test-user-id", email="test@example.com"
    )
    app.dependency_overrides[get_extractor] = lambda: FakeExtractor()
    app.dependency_overrides[get_weather_client] = lambda: FakeWeatherClient()
    app.dependency_overrides[get_web_search_client] = lambda: FakeWebSearchClient()
    app.dependency_overrides[get_memory_store] = lambda: FakeMemoryStore()


def teardown_function() -> None:
    app.dependency_overrides.clear()


def _request(messages: list[ChatMessage]) -> dict:
    return {
        "messages": [m.model_dump(exclude_none=True) for m in messages],
        "reference_datetime": "2026-08-26T12:00:00",
        "timezone": "America/New_York",
    }


def test_chat_requires_authentication() -> None:
    # Override cleared for this one test -- proves /chat actually enforces
    # get_current_user rather than only working because setup_function
    # overrides it away for every other test in this file.
    app.dependency_overrides.pop(get_current_user, None)
    app.dependency_overrides[get_chat_engine] = lambda: FakeChatEngine()

    response = client.post("/chat", json=_request([ChatMessage(role="user", content="hi")]))

    assert response.status_code == 401


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


def test_chat_injects_remembered_facts_into_the_system_prompt() -> None:
    fake_engine = FakeChatEngine()
    app.dependency_overrides[get_chat_engine] = lambda: fake_engine
    facts = ["The user's cat is named Whiskers.", "I'm vegetarian"]
    fake_store = FakeMemoryStore(facts=facts)
    app.dependency_overrides[get_memory_store] = lambda: fake_store

    response = client.post("/chat", json=_request([ChatMessage(role="user", content="hello")]))

    assert response.status_code == 200
    system_content = fake_engine.received_messages[0].content
    assert system_content == build_system_prompt(PERSONA, facts)
    for fact in facts:
        assert fact in system_content
    assert fake_store.received_user_id == "test-user-id"
    assert fake_store.received_query_text == "hello"
    assert fake_store.received_limit == 10


def test_chat_repeats_remembered_facts_right_before_the_final_user_message() -> None:
    """Facts in the top system prompt alone measured 1/3 correct against
    gpt-5-mini on an edited-memory replay; repeating them immediately
    before the current question measured 3/3 (see build_memory_note)."""
    fake_engine = FakeChatEngine()
    app.dependency_overrides[get_chat_engine] = lambda: fake_engine
    facts = ["The user's brother lives in San Diego."]
    app.dependency_overrides[get_memory_store] = lambda: FakeMemoryStore(facts=facts)
    messages = [
        ChatMessage(role="user", content="my brother lives in San Francisco"),
        ChatMessage(role="assistant", content="Saved that."),
        ChatMessage(role="user", content="where does my brother live?"),
    ]

    response = client.post("/chat", json=_request(messages))

    assert response.status_code == 200
    sent = fake_engine.received_messages
    # The full prompt still leads the conversation...
    assert sent[0].role == "system"
    assert sent[0].content == build_system_prompt(PERSONA, facts)
    # ...and the same facts are restated directly before the question.
    assert sent[-1].role == "user"
    assert sent[-1].content == "where does my brother live?"
    assert sent[-2].role == "system"
    assert sent[-2].content == build_memory_note(facts)
    assert "San Diego" in sent[-2].content


def test_chat_omits_the_memory_note_when_there_are_no_facts() -> None:
    fake_engine = FakeChatEngine()
    app.dependency_overrides[get_chat_engine] = lambda: fake_engine
    app.dependency_overrides[get_memory_store] = lambda: FakeMemoryStore(facts=[])

    response = client.post("/chat", json=_request([ChatMessage(role="user", content="hello")]))

    assert response.status_code == 200
    sent = fake_engine.received_messages
    assert len(sent) == 2
    assert [m.role for m in sent] == ["system", "user"]
    assert sent[0].content == SYSTEM_PROMPT


def test_chat_does_not_return_the_memory_note_to_the_client() -> None:
    """The note is per-request scaffolding: run_turn returns only the
    messages it generated, so the client never sees it and can't echo it
    back into the next turn's history."""
    fake_engine = FakeChatEngine(
        response_messages=[ChatMessage(role="assistant", content="San Diego.")]
    )
    app.dependency_overrides[get_chat_engine] = lambda: fake_engine
    facts = ["The user's brother lives in San Diego."]
    app.dependency_overrides[get_memory_store] = lambda: FakeMemoryStore(facts=facts)

    response = client.post(
        "/chat", json=_request([ChatMessage(role="user", content="where does my brother live?")])
    )

    assert response.status_code == 200
    new_messages = response.json()["new_messages"]
    assert [m["role"] for m in new_messages] == ["assistant"]
    assert all("Current saved memory" not in (m.get("content") or "") for m in new_messages)


def test_chat_falls_back_to_no_facts_when_memory_store_read_fails() -> None:
    class BrokenMemoryStore:
        def remember_fact(self, user_id: str, fact_text: str) -> None:
            pass

        def search_facts(self, user_id: str, query_text: str, limit: int = 10) -> List[str]:
            raise RuntimeError("db unreachable")

    fake_engine = FakeChatEngine()
    app.dependency_overrides[get_chat_engine] = lambda: fake_engine
    app.dependency_overrides[get_memory_store] = lambda: BrokenMemoryStore()

    response = client.post("/chat", json=_request([ChatMessage(role="user", content="hello")]))

    assert response.status_code == 200
    assert fake_engine.received_messages[0].content == SYSTEM_PROMPT


def test_chat_skips_search_facts_when_last_message_has_no_content() -> None:
    """Guards against embedding an empty/missing string -- not meaningful,
    and search_facts shouldn't even be called in that case."""
    fake_engine = FakeChatEngine()
    app.dependency_overrides[get_chat_engine] = lambda: fake_engine
    fake_store = FakeMemoryStore(facts=["should never be returned"])
    app.dependency_overrides[get_memory_store] = lambda: fake_store

    response = client.post(
        "/chat", json=_request([ChatMessage(role="user", tool_calls=[{"id": "call_1"}])])
    )

    assert response.status_code == 200
    assert fake_store.received_query_text is None
    assert fake_engine.received_messages[0].content == SYSTEM_PROMPT


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


def test_chat_returns_429_once_the_rate_limit_is_exceeded() -> None:
    """Confirms enforce_chat_rate_limit is actually wired into the route
    (not just present in app.rate_limit) -- a tiny override limiter, not the
    real 20/min default, so the test doesn't need 21 requests to prove it."""
    app.dependency_overrides[get_chat_engine] = lambda: FakeChatEngine()
    # The SAME instance for every dependency resolution -- a lambda that
    # constructs a fresh RateLimiter each call (dependency_overrides invokes
    # its replacement fresh per request, not just once per test) would give
    # each request its own empty deques and never actually accumulate state.
    limiter = RateLimiter(per_window_limit=1, window_seconds=60, daily_limit=100)
    app.dependency_overrides[get_chat_rate_limiter] = lambda: limiter

    first = client.post("/chat", json=_request([ChatMessage(role="user", content="hi")]))
    second = client.post("/chat", json=_request([ChatMessage(role="user", content="hi again")]))

    assert first.status_code == 200
    assert second.status_code == 429


def test_chat_wires_up_the_registered_tools() -> None:
    fake_engine = FakeChatEngine()
    app.dependency_overrides[get_chat_engine] = lambda: fake_engine

    response = client.post("/chat", json=_request([ChatMessage(role="user", content="hi")]))

    assert response.status_code == 200
    tool_names = {tool.name for tool in fake_engine.received_tools}
    assert tool_names == {
        "extract_calendar_events",
        "set_reminder",
        "get_weather",
        "web_search",
        "remember_fact",
    }


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


def test_chat_trims_an_over_long_history_instead_of_rejecting_it() -> None:
    """Review finding F7: this used to be a 422, which in practice killed
    the conversation for good -- iOS resends its whole in-memory history on
    every call, so once it crossed the cap every subsequent turn 422'd too,
    with nothing the user could do but relaunch the app."""
    fake_engine = FakeChatEngine()
    app.dependency_overrides[get_chat_engine] = lambda: fake_engine
    messages = [
        ChatMessage(role="user" if i % 2 == 0 else "assistant", content=f"turn {i}")
        for i in range(_MAX_CHAT_MESSAGES + 40)
    ]
    messages[-1] = ChatMessage(role="user", content="the newest thing I said")

    response = client.post("/chat", json=_request(messages))

    assert response.status_code == 200
    # One system message is prepended by the route itself (this history has
    # none of its own), on top of the trimmed window.
    assert len(fake_engine.received_messages) == _MAX_CHAT_MESSAGES + 1
    assert fake_engine.received_messages[0].role == "system"
    # The OLDEST turns are the ones dropped; the newest must survive.
    assert fake_engine.received_messages[-1].content == "the newest thing I said"
    assert all(m.content != "turn 0" for m in fake_engine.received_messages)


def test_chat_trimming_keeps_a_leading_system_message() -> None:
    """The client's own system message is the conversation's instructions,
    not history -- dropping it with the rest of the head would silently
    change the assistant's behaviour mid-conversation (and make the route
    substitute a default persona prompt in its place)."""
    fake_engine = FakeChatEngine()
    app.dependency_overrides[get_chat_engine] = lambda: fake_engine
    messages = [ChatMessage(role="system", content="you are a pirate")]
    messages += [
        ChatMessage(role="user" if i % 2 == 0 else "assistant", content=f"turn {i}")
        for i in range(_MAX_CHAT_MESSAGES + 40)
    ]
    messages[-1] = ChatMessage(role="user", content="still here?")

    response = client.post("/chat", json=_request(messages))

    assert response.status_code == 200
    assert len(fake_engine.received_messages) == _MAX_CHAT_MESSAGES
    assert fake_engine.received_messages[0].content == "you are a pirate"
    assert fake_engine.received_messages[-1].content == "still here?"


def test_chat_still_rejects_a_single_oversized_message() -> None:
    """Trimming the history is about how much came along for the ride; an
    individual message over the character cap is still the caller's to fix."""
    app.dependency_overrides[get_chat_engine] = lambda: FakeChatEngine()

    response = client.post(
        "/chat",
        json=_request([ChatMessage(role="user", content="x" * (_MAX_CHAT_MESSAGE_CHARS + 1))]),
    )

    assert response.status_code == 422
