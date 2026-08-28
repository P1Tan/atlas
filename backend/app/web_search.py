from dataclasses import dataclass
from typing import List, Optional, Protocol

import httpx

from app.config import TAVILY_API_KEY

TAVILY_SEARCH_URL = "https://api.tavily.com/search"


@dataclass
class SearchResult:
    title: str
    url: str
    snippet: str


@dataclass
class SearchResponse:
    query: str
    answer: Optional[str]
    results: List[SearchResult]


class WebSearchClient(Protocol):
    def search(self, query: str) -> SearchResponse:
        """Run a web search for the given query. Zero results is a normal,
        valid outcome (an empty results list), not an error -- unlike
        WeatherClient.get_weather this never returns None for an expected
        failure case. Only real errors (network/auth failures) raise."""
        ...


class TavilyWebSearchClient:
    """Searches the web via the Tavily Search API (https://tavily.com), an
    API purpose-built for LLM/agent use. Requires TAVILY_API_KEY."""

    def __init__(self, client: Optional[httpx.Client] = None) -> None:
        self._client = client or httpx.Client(timeout=15.0)

    def search(self, query: str) -> SearchResponse:
        response = self._client.post(
            TAVILY_SEARCH_URL,
            json={
                "api_key": TAVILY_API_KEY,
                "query": query,
                "search_depth": "basic",
                "max_results": 5,
                "include_answer": True,
            },
        )
        response.raise_for_status()
        payload = response.json()

        results = [
            SearchResult(
                title=item.get("title", ""),
                url=item.get("url", ""),
                snippet=item.get("content", ""),
            )
            for item in payload.get("results") or []
        ]

        return SearchResponse(query=query, answer=payload.get("answer"), results=results)


def get_default_web_search_client() -> WebSearchClient:
    return TavilyWebSearchClient()


def get_web_search_client() -> WebSearchClient:
    """FastAPI dependency -- the one function every route depending on a web
    search client should use, mirroring app.extraction.get_extractor so
    dependency_overrides actually takes effect in tests."""
    return get_default_web_search_client()
