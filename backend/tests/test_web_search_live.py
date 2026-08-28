"""Exercises the real Tavily Search API.

Live API call (small cost, non-deterministic results), so kept separate from
the fast, deterministic web_search tool tests and skipped automatically when
no API key is configured.
"""

import os

import pytest

from app.web_search import TavilyWebSearchClient

pytestmark = pytest.mark.skipif(not os.getenv("TAVILY_API_KEY"), reason="TAVILY_API_KEY not set")


def test_search_returns_real_results_for_a_simple_factual_query() -> None:
    client = TavilyWebSearchClient()

    response = client.search("what is the capital of France")

    assert len(response.results) >= 1
    for result in response.results:
        assert result.title
        assert result.url
        assert result.snippet
