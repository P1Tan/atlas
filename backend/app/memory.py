from dataclasses import dataclass
from typing import List, Protocol

from app.supabase_client import get_supabase_client


@dataclass
class FactRecord:
    id: str
    fact_text: str
    created_at: str


class MemoryStore(Protocol):
    def remember_fact(self, user_id: str, fact_text: str) -> None:
        """Durably store a fact about the user for recall in future
        conversations. Raises on failure -- unlike WeatherClient.get_weather
        or WebSearchClient.search there is no expected-failure case here
        (any non-empty fact text is valid to store), so a real DB error is
        the only failure mode, left to propagate to the chat loop's existing
        generic per-tool-call exception handling."""
        ...

    def list_facts(self, user_id: str) -> List[str]:
        """Return the user's remembered facts, oldest-first, capped at the
        200 most recent -- a defensive bound so unbounded growth can't blow
        up every future prompt, complementing the 500-char per-fact cap
        remember_fact already enforces. Raises on failure -- same
        no-expected-failure-case philosophy as remember_fact, a real DB
        error is the only failure mode."""
        ...

    def list_fact_records(self, user_id: str) -> List[FactRecord]:
        """Return ALL of the user's facts with their ids, newest-first, for
        a management UI. Deliberately has NO 200-cap unlike list_facts
        (which is capped because it's injected into every chat turn's
        context) -- a management UI needs to represent everything that
        exists so the user can actually delete their way back under any
        cap. Raises on failure, same philosophy as the other methods."""
        ...

    def delete_fact(self, user_id: str, fact_id: str) -> bool:
        """Delete the fact with the given id, but ONLY if it belongs to
        user_id -- this is the sole authorization check (the backend uses
        the service_role client, which bypasses Row Level Security, so this
        .eq("user_id", ...) filter is the only thing standing between a user
        and someone else's data; the query must filter on BOTH id and
        user_id, never id alone). Returns True if a row was actually
        deleted, False if no matching row existed (either the id doesn't
        exist, or it belongs to a different user -- both cases are
        indistinguishable to the caller on purpose, so a 404 doesn't leak
        whether the id exists under someone else's account). Raises on
        failure (a real DB error), same philosophy as the other methods."""
        ...


class SupabaseMemoryStore:
    """Persists facts to the user_facts table via the service_role Supabase
    client (see app.supabase_client.get_supabase_client)."""

    def remember_fact(self, user_id: str, fact_text: str) -> None:
        get_supabase_client().table("user_facts").insert(
            {"user_id": user_id, "fact_text": fact_text}
        ).execute()

    def list_facts(self, user_id: str) -> List[str]:
        response = (
            get_supabase_client()
            .table("user_facts")
            .select("fact_text")
            .eq("user_id", user_id)
            .order("created_at", desc=True)
            .order("id", desc=True)
            .limit(200)
            .execute()
        )
        rows = list(reversed(response.data))
        return [row["fact_text"] for row in rows]

    def list_fact_records(self, user_id: str) -> List[FactRecord]:
        response = (
            get_supabase_client()
            .table("user_facts")
            .select("id, fact_text, created_at")
            .eq("user_id", user_id)
            .order("created_at", desc=True)
            .order("id", desc=True)
            .execute()
        )
        return [
            FactRecord(id=row["id"], fact_text=row["fact_text"], created_at=row["created_at"])
            for row in response.data
        ]

    def delete_fact(self, user_id: str, fact_id: str) -> bool:
        response = (
            get_supabase_client()
            .table("user_facts")
            .delete()
            .eq("id", fact_id)
            .eq("user_id", user_id)
            .execute()
        )
        return len(response.data) > 0


def get_default_memory_store() -> MemoryStore:
    return SupabaseMemoryStore()


def get_memory_store() -> MemoryStore:
    """FastAPI dependency -- the one function every route depending on a
    memory store should use, mirroring app.weather.get_weather_client so
    dependency_overrides actually takes effect in tests."""
    return get_default_memory_store()
