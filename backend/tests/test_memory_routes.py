from typing import List, Optional

from fastapi.testclient import TestClient

from app.main import app
from app.memory import MAX_FACT_LENGTH, FactRecord, get_memory_store
from app.supabase_client import AuthenticatedUser, get_current_user

client = TestClient(app)


class FakeMemoryStore:
    def __init__(
        self,
        records: List[FactRecord] = None,
        delete_result: bool = True,
        update_result: Optional[FactRecord] = None,
    ) -> None:
        self._records = records or []
        self._delete_result = delete_result
        self._update_result = update_result
        self.received_list_user_id = None
        self.received_delete_user_id = None
        self.received_delete_fact_id = None
        self.received_update_user_id = None
        self.received_update_fact_id = None
        self.received_update_fact_text = None

    def remember_fact(self, user_id: str, fact_text: str) -> None:
        pass

    def list_facts(self, user_id: str) -> List[str]:
        return []

    def list_fact_records(self, user_id: str) -> List[FactRecord]:
        self.received_list_user_id = user_id
        return self._records

    def delete_fact(self, user_id: str, fact_id: str) -> bool:
        self.received_delete_user_id = user_id
        self.received_delete_fact_id = fact_id
        return self._delete_result

    def update_fact(self, user_id: str, fact_id: str, fact_text: str) -> Optional[FactRecord]:
        self.received_update_user_id = user_id
        self.received_update_fact_id = fact_id
        self.received_update_fact_text = fact_text
        return self._update_result


def setup_function() -> None:
    app.dependency_overrides[get_current_user] = lambda: AuthenticatedUser(
        id="test-user-id", email="test@example.com"
    )


def teardown_function() -> None:
    app.dependency_overrides.clear()


def test_list_facts_requires_authentication() -> None:
    app.dependency_overrides.pop(get_current_user, None)
    app.dependency_overrides[get_memory_store] = lambda: FakeMemoryStore()

    response = client.get("/facts")

    assert response.status_code == 401


def test_list_facts_returns_the_store_records_serialized() -> None:
    records = [
        FactRecord(id="fact-2", fact_text="I'm vegetarian", created_at="2026-08-20T12:00:00Z"),
        FactRecord(id="fact-1", fact_text="My cat is Whiskers", created_at="2026-08-19T12:00:00Z"),
    ]
    fake_store = FakeMemoryStore(records=records)
    app.dependency_overrides[get_memory_store] = lambda: fake_store

    response = client.get("/facts")

    assert response.status_code == 200
    assert fake_store.received_list_user_id == "test-user-id"
    assert response.json() == [
        {"id": "fact-2", "fact_text": "I'm vegetarian", "created_at": "2026-08-20T12:00:00Z"},
        {"id": "fact-1", "fact_text": "My cat is Whiskers", "created_at": "2026-08-19T12:00:00Z"},
    ]


def test_list_facts_returns_502_on_store_failure() -> None:
    class BrokenMemoryStore(FakeMemoryStore):
        def list_fact_records(self, user_id: str) -> List[FactRecord]:
            raise RuntimeError("db unreachable")

    app.dependency_overrides[get_memory_store] = lambda: BrokenMemoryStore()

    response = client.get("/facts")

    assert response.status_code == 502


_FACT_ID = "11111111-1111-1111-1111-111111111111"


def test_delete_fact_requires_authentication() -> None:
    app.dependency_overrides.pop(get_current_user, None)
    app.dependency_overrides[get_memory_store] = lambda: FakeMemoryStore()

    response = client.delete(f"/facts/{_FACT_ID}")

    assert response.status_code == 401


def test_delete_fact_returns_204_and_calls_the_store_with_user_id_and_fact_id() -> None:
    fake_store = FakeMemoryStore(delete_result=True)
    app.dependency_overrides[get_memory_store] = lambda: fake_store

    response = client.delete(f"/facts/{_FACT_ID}")

    assert response.status_code == 204
    assert fake_store.received_delete_user_id == "test-user-id"
    assert fake_store.received_delete_fact_id == _FACT_ID


def test_delete_fact_returns_404_when_store_reports_nothing_deleted() -> None:
    fake_store = FakeMemoryStore(delete_result=False)
    app.dependency_overrides[get_memory_store] = lambda: fake_store

    response = client.delete(f"/facts/{_FACT_ID}")

    assert response.status_code == 404


def test_delete_fact_returns_502_on_store_failure() -> None:
    class BrokenMemoryStore(FakeMemoryStore):
        def delete_fact(self, user_id: str, fact_id: str) -> bool:
            raise RuntimeError("db unreachable")

    app.dependency_overrides[get_memory_store] = lambda: BrokenMemoryStore()

    response = client.delete(f"/facts/{_FACT_ID}")

    assert response.status_code == 502


def test_delete_fact_returns_404_for_a_malformed_id_without_reaching_the_store() -> None:
    fake_store = FakeMemoryStore(delete_result=True)
    app.dependency_overrides[get_memory_store] = lambda: fake_store

    response = client.delete("/facts/not-a-uuid")

    assert response.status_code == 404
    assert fake_store.received_delete_fact_id is None


def _updated(fact_text: str = "I'm vegan now") -> FactRecord:
    return FactRecord(id=_FACT_ID, fact_text=fact_text, created_at="2026-08-20T12:00:00Z")


def test_update_fact_requires_authentication() -> None:
    app.dependency_overrides.pop(get_current_user, None)
    app.dependency_overrides[get_memory_store] = lambda: FakeMemoryStore()

    response = client.patch(f"/facts/{_FACT_ID}", json={"fact_text": "I'm vegan now"})

    assert response.status_code == 401


def test_update_fact_returns_the_updated_row_and_scopes_the_store_call_to_the_user() -> None:
    fake_store = FakeMemoryStore(update_result=_updated())
    app.dependency_overrides[get_memory_store] = lambda: fake_store

    response = client.patch(f"/facts/{_FACT_ID}", json={"fact_text": "  I'm vegan now  "})

    assert response.status_code == 200
    assert fake_store.received_update_user_id == "test-user-id"
    assert fake_store.received_update_fact_id == _FACT_ID
    # Stored trimmed -- the route validates and persists the same string.
    assert fake_store.received_update_fact_text == "I'm vegan now"
    assert response.json() == {
        "id": _FACT_ID,
        "fact_text": "I'm vegan now",
        "created_at": "2026-08-20T12:00:00Z",
    }


def test_update_fact_returns_404_when_no_row_matched_this_user() -> None:
    """A fact that exists but belongs to someone else is indistinguishable
    from one that doesn't exist -- the store returns None for both."""
    fake_store = FakeMemoryStore(update_result=None)
    app.dependency_overrides[get_memory_store] = lambda: fake_store

    response = client.patch(f"/facts/{_FACT_ID}", json={"fact_text": "I'm vegan now"})

    assert response.status_code == 404
    assert response.json()["detail"] == "fact not found"


def test_update_fact_returns_404_for_a_malformed_id_without_reaching_the_store() -> None:
    fake_store = FakeMemoryStore(update_result=_updated())
    app.dependency_overrides[get_memory_store] = lambda: fake_store

    response = client.patch("/facts/not-a-uuid", json={"fact_text": "I'm vegan now"})

    assert response.status_code == 404
    assert fake_store.received_update_fact_id is None


def test_update_fact_returns_422_for_whitespace_only_text_without_reaching_the_store() -> None:
    fake_store = FakeMemoryStore(update_result=_updated())
    app.dependency_overrides[get_memory_store] = lambda: fake_store

    response = client.patch(f"/facts/{_FACT_ID}", json={"fact_text": "   "})

    assert response.status_code == 422
    assert fake_store.received_update_fact_text is None


def test_update_fact_returns_422_for_text_over_the_shared_cap() -> None:
    fake_store = FakeMemoryStore(update_result=_updated())
    app.dependency_overrides[get_memory_store] = lambda: fake_store

    response = client.patch(f"/facts/{_FACT_ID}", json={"fact_text": "x" * (MAX_FACT_LENGTH + 1)})

    assert response.status_code == 422
    assert fake_store.received_update_fact_text is None


def test_update_fact_accepts_text_exactly_at_the_shared_cap() -> None:
    fact_text = "x" * MAX_FACT_LENGTH
    fake_store = FakeMemoryStore(update_result=_updated(fact_text))
    app.dependency_overrides[get_memory_store] = lambda: fake_store

    response = client.patch(f"/facts/{_FACT_ID}", json={"fact_text": fact_text})

    assert response.status_code == 200
    assert fake_store.received_update_fact_text == fact_text


def test_update_fact_returns_502_on_store_failure() -> None:
    class BrokenMemoryStore(FakeMemoryStore):
        def update_fact(self, user_id: str, fact_id: str, fact_text: str):
            raise RuntimeError("db unreachable")

    app.dependency_overrides[get_memory_store] = lambda: BrokenMemoryStore()

    response = client.patch(f"/facts/{_FACT_ID}", json={"fact_text": "I'm vegan now"})

    assert response.status_code == 502
