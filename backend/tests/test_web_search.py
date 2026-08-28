from app.tools import _build_web_search_tool
from app.web_search import SearchResponse, SearchResult


class FakeWebSearchClient:
    def __init__(self, response: SearchResponse) -> None:
        self._response = response
        self.received_query = None

    def search(self, query: str) -> SearchResponse:
        self.received_query = query
        return self._response


def _sample_response() -> SearchResponse:
    return SearchResponse(
        query="what is the capital of France",
        answer="The capital of France is Paris.",
        results=[
            SearchResult(
                title="France | Britannica",
                url="https://www.britannica.com/place/France",
                snippet="The capital of France is Paris.",
            ),
            SearchResult(
                title="Paris facts",
                url="https://home.adelphi.edu/~ca19535/page%204.html",
                snippet="Paris is the capital of France.",
            ),
        ],
    )


def test_web_search_tool_returns_answer_and_results() -> None:
    search_client = FakeWebSearchClient(_sample_response())
    tool = _build_web_search_tool(search_client)

    result = tool.handler({"query": "what is the capital of France"})

    assert search_client.received_query == "what is the capital of France"
    assert result == {
        "answer": "The capital of France is Paris.",
        "results": [
            {
                "title": "France | Britannica",
                "url": "https://www.britannica.com/place/France",
                "snippet": "The capital of France is Paris.",
            },
            {
                "title": "Paris facts",
                "url": "https://home.adelphi.edu/~ca19535/page%204.html",
                "snippet": "Paris is the capital of France.",
            },
        ],
    }


def test_web_search_tool_zero_results_is_not_an_error() -> None:
    empty_response = SearchResponse(query="asdkjhqwekjhasd nonsense query", answer=None, results=[])
    search_client = FakeWebSearchClient(empty_response)
    tool = _build_web_search_tool(search_client)

    result = tool.handler({"query": "asdkjhqwekjhasd nonsense query"})

    # Zero results is a normal, valid outcome -- not an "ok": False style
    # error shape the way get_weather/set_reminder report expected failures.
    assert result == {"answer": None, "results": []}
    assert "ok" not in result
    assert "error" not in result


def test_web_search_tool_schema() -> None:
    tool = _build_web_search_tool(FakeWebSearchClient(_sample_response()))

    schema = tool.to_openai_schema()

    assert schema["function"]["name"] == "web_search"
    assert schema["function"]["parameters"]["required"] == ["query"]
    assert "query" in schema["function"]["parameters"]["properties"]
