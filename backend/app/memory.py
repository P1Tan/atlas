from typing import List, Protocol

from app.supabase_client import get_supabase_client


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


def get_default_memory_store() -> MemoryStore:
    return SupabaseMemoryStore()


def get_memory_store() -> MemoryStore:
    """FastAPI dependency -- the one function every route depending on a
    memory store should use, mirroring app.weather.get_weather_client so
    dependency_overrides actually takes effect in tests."""
    return get_default_memory_store()
