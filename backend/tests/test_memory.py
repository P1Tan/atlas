from types import SimpleNamespace
from typing import List, Optional

from app import memory
from app.memory import FactRecord, SupabaseMemoryStore
from app.tools import _build_remember_fact_tool


class FakeMemoryStore:
    def __init__(self) -> None:
        self.received_user_id = None
        self.received_fact_text = None

    def remember_fact(self, user_id: str, fact_text: str) -> None:
        self.received_user_id = user_id
        self.received_fact_text = fact_text

    def update_fact(self, user_id: str, fact_id: str, fact_text: str):
        # Part of the MemoryStore Protocol; the remember_fact tool only
        # ever inserts, never edits.
        return None


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


class _FakeEmbeddingClient:
    def __init__(self, embedding: Optional[List[float]] = None) -> None:
        self._embedding = embedding if embedding is not None else [0.1, 0.2, 0.3]
        self.embedded_texts: List[str] = []

    def embed(self, text: str) -> List[float]:
        self.embedded_texts.append(text)
        return self._embedding


class _FakeSupabaseTable:
    """Records the PostgREST call chain so the tests can assert on the
    filters -- the .eq("user_id", ...) scoping is the only authorization
    check on fact writes (the backend uses the service_role client, which
    bypasses Row Level Security)."""

    def __init__(self, rows: List[dict]) -> None:
        self._rows = rows
        self.updated_values = None
        self.filters = []

    def update(self, values: dict) -> "_FakeSupabaseTable":
        self.updated_values = values
        return self

    def eq(self, column: str, value: str) -> "_FakeSupabaseTable":
        self.filters.append((column, value))
        return self

    def execute(self):
        return SimpleNamespace(data=self._rows)


class _FakeSupabaseClient:
    def __init__(self, rows: List[dict]) -> None:
        self.table_obj = _FakeSupabaseTable(rows)
        self.table_names = []

    def table(self, name: str) -> _FakeSupabaseTable:
        self.table_names.append(name)
        return self.table_obj


_ROW = {
    "id": "11111111-1111-1111-1111-111111111111",
    "fact_text": "I'm vegan now",
    "created_at": "2026-08-20T12:00:00Z",
}


def _patch_supabase(monkeypatch, rows: List[dict]) -> _FakeSupabaseClient:
    fake_client = _FakeSupabaseClient(rows)
    monkeypatch.setattr(memory, "get_supabase_client", lambda: fake_client)
    return fake_client


def test_update_fact_recomputes_the_embedding_and_updates_both_columns(monkeypatch) -> None:
    """search_facts matches on the embedding, so an edit that changed
    fact_text alone would leave the fact recallable only by its old
    wording."""
    fake_client = _patch_supabase(monkeypatch, [_ROW])
    embedding_client = _FakeEmbeddingClient([0.4, 0.5, 0.6])
    store = SupabaseMemoryStore(embedding_client=embedding_client)

    record = store.update_fact("user-123", _ROW["id"], "I'm vegan now")

    assert embedding_client.embedded_texts == ["I'm vegan now"]
    assert fake_client.table_names == ["user_facts"]
    assert fake_client.table_obj.updated_values == {
        "fact_text": "I'm vegan now",
        "embedding": [0.4, 0.5, 0.6],
    }
    assert record == FactRecord(
        id=_ROW["id"], fact_text="I'm vegan now", created_at="2026-08-20T12:00:00Z"
    )


def test_update_fact_scopes_the_update_to_both_the_fact_id_and_the_user_id(monkeypatch) -> None:
    fake_client = _patch_supabase(monkeypatch, [_ROW])
    store = SupabaseMemoryStore(embedding_client=_FakeEmbeddingClient())

    store.update_fact("user-123", _ROW["id"], "I'm vegan now")

    assert fake_client.table_obj.filters == [("id", _ROW["id"]), ("user_id", "user-123")]


def test_update_fact_returns_none_when_no_row_matched(monkeypatch) -> None:
    _patch_supabase(monkeypatch, [])
    store = SupabaseMemoryStore(embedding_client=_FakeEmbeddingClient())

    assert store.update_fact("user-123", _ROW["id"], "I'm vegan now") is None
