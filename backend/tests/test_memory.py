from typing import List

from app.tools import _build_remember_fact_tool


class FakeMemoryStore:
    def __init__(self) -> None:
        self.received_user_id = None
        self.received_fact_text = None

    def remember_fact(self, user_id: str, fact_text: str) -> None:
        self.received_user_id = user_id
        self.received_fact_text = fact_text

    def list_facts(self, user_id: str) -> List[str]:
        return []


def test_remember_fact_tool_calls_the_store_with_user_id_and_fact_text() -> None:
    memory_store = FakeMemoryStore()
    tool = _build_remember_fact_tool("user-123", memory_store)

    result = tool.handler({"fact_text": "I'm vegetarian"})

    assert memory_store.received_user_id == "user-123"
    assert memory_store.received_fact_text == "I'm vegetarian"
    assert result == {"ok": True, "remembered": "I'm vegetarian"}


def test_remember_fact_tool_rejects_overly_long_fact_text() -> None:
    memory_store = FakeMemoryStore()
    tool = _build_remember_fact_tool("user-123", memory_store)

    result = tool.handler({"fact_text": "x" * 501})

    assert result["ok"] is False
    assert memory_store.received_fact_text is None


def test_remember_fact_tool_scopes_to_the_user_id_it_was_built_with() -> None:
    memory_store = FakeMemoryStore()
    tool = _build_remember_fact_tool("a-different-user", memory_store)

    tool.handler({"fact_text": "My sister's name is Maya"})

    assert memory_store.received_user_id == "a-different-user"


def test_remember_fact_tool_schema() -> None:
    tool = _build_remember_fact_tool("user-123", FakeMemoryStore())

    schema = tool.to_openai_schema()

    assert schema["function"]["name"] == "remember_fact"
    parameters = schema["function"]["parameters"]
    assert parameters["required"] == ["fact_text"]
    assert set(parameters["properties"].keys()) == {"fact_text"}
    assert "user_id" not in parameters["properties"]
