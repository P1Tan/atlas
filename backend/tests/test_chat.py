"""Unit tests for the generic tool-calling loop (app.chat.OpenAIChatEngine),
against a fake OpenAI client -- no live API calls, no network.
"""

import json
from types import SimpleNamespace

from app.chat import MAX_TOOL_ITERATIONS, ChatMessage, OpenAIChatEngine, ToolDefinition


class FakeToolCallFunction:
    def __init__(self, name: str, arguments: str) -> None:
        self.name = name
        self.arguments = arguments


class FakeToolCall:
    def __init__(self, call_id: str, name: str, arguments: str) -> None:
        self.id = call_id
        self.function = FakeToolCallFunction(name, arguments)

    def model_dump(self) -> dict:
        return {
            "id": self.id,
            "type": "function",
            "function": {"name": self.function.name, "arguments": self.function.arguments},
        }


class FakeMessage:
    def __init__(self, content=None, tool_calls=None) -> None:
        self.content = content
        self.tool_calls = tool_calls


class FakeResponse:
    def __init__(self, message: FakeMessage) -> None:
        self.choices = [SimpleNamespace(message=message)]


class FakeCompletions:
    def __init__(self, responses) -> None:
        self._responses = list(responses)
        self.calls: list[dict] = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return self._responses.pop(0)


class FakeChatClient:
    def __init__(self, responses) -> None:
        self.chat = SimpleNamespace(completions=FakeCompletions(responses))


def _engine_with_responses(responses):
    client = FakeChatClient(responses)
    return OpenAIChatEngine(model_name="fake-model", client=client), client


def test_plain_reply_with_no_tool_calls() -> None:
    engine, client = _engine_with_responses([FakeResponse(FakeMessage(content="Hi there!"))])
    messages = [
        ChatMessage(role="system", content="sys"),
        ChatMessage(role="user", content="hello"),
    ]

    new_messages = engine.run_turn(messages, tools=[])

    assert len(new_messages) == 1
    assert new_messages[0].role == "assistant"
    assert new_messages[0].content == "Hi there!"
    assert "tools" not in client.chat.completions.calls[0]


def test_tool_call_is_executed_and_looped_back() -> None:
    tool_call = FakeToolCall("call_1", "get_weather", json.dumps({"city": "NYC"}))
    responses = [
        FakeResponse(FakeMessage(content=None, tool_calls=[tool_call])),
        FakeResponse(FakeMessage(content="It's sunny in NYC.")),
    ]
    engine, client = _engine_with_responses(responses)

    calls_seen = []

    def handler(args):
        calls_seen.append(args)
        return {"forecast": "sunny"}

    tool = ToolDefinition(
        name="get_weather", description="Get weather", parameters={"type": "object"}, handler=handler
    )

    new_messages = engine.run_turn([ChatMessage(role="user", content="weather in NYC?")], tools=[tool])

    assert calls_seen == [{"city": "NYC"}]
    assert [m.role for m in new_messages] == ["assistant", "tool", "assistant"]
    assert new_messages[1].tool_call_id == "call_1"
    assert json.loads(new_messages[1].content) == {"forecast": "sunny"}
    assert new_messages[2].content == "It's sunny in NYC."
    second_call_messages = client.chat.completions.calls[1]["messages"]
    assert any(m.get("role") == "tool" for m in second_call_messages)


def test_unknown_tool_name_returns_error_without_crashing() -> None:
    tool_call = FakeToolCall("call_1", "does_not_exist", "{}")
    responses = [
        FakeResponse(FakeMessage(content=None, tool_calls=[tool_call])),
        FakeResponse(FakeMessage(content="ok")),
    ]
    engine, _ = _engine_with_responses(responses)

    new_messages = engine.run_turn([ChatMessage(role="user", content="hi")], tools=[])

    assert new_messages[1].role == "tool"
    assert "unknown tool" in json.loads(new_messages[1].content)["error"]


def test_tool_handler_exception_is_caught_and_reported_to_model() -> None:
    tool_call = FakeToolCall("call_1", "boom", "{}")
    responses = [
        FakeResponse(FakeMessage(content=None, tool_calls=[tool_call])),
        FakeResponse(FakeMessage(content="handled")),
    ]
    engine, _ = _engine_with_responses(responses)

    def handler(args):
        raise RuntimeError("kaboom")

    tool = ToolDefinition(name="boom", description="", parameters={"type": "object"}, handler=handler)

    new_messages = engine.run_turn([ChatMessage(role="user", content="hi")], tools=[tool])

    assert "failed" in json.loads(new_messages[1].content)["error"]


def test_max_tool_iterations_caps_an_infinite_tool_loop() -> None:
    tool_call = FakeToolCall("call_1", "loop_tool", "{}")
    responses = [
        FakeResponse(FakeMessage(content=None, tool_calls=[tool_call])) for _ in range(MAX_TOOL_ITERATIONS)
    ]
    engine, client = _engine_with_responses(responses)

    tool = ToolDefinition(
        name="loop_tool", description="", parameters={"type": "object"}, handler=lambda args: {"ok": True}
    )

    new_messages = engine.run_turn([ChatMessage(role="user", content="hi")], tools=[tool])

    assert len(client.chat.completions.calls) == MAX_TOOL_ITERATIONS
    assert new_messages[-1].role == "tool"
