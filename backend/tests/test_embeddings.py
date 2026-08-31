"""Unit tests for app.embeddings.OpenAIEmbeddingClient, against a fake
OpenAI client -- no live API calls, no network.
"""

from types import SimpleNamespace

from app.embeddings import OpenAIEmbeddingClient


class FakeEmbeddingsResource:
    def __init__(self, vector) -> None:
        self._vector = vector
        self.calls: list[dict] = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(data=[SimpleNamespace(embedding=self._vector)])


class FakeEmbeddingClient:
    def __init__(self, vector) -> None:
        self.embeddings = FakeEmbeddingsResource(vector)


def test_embed_returns_the_vector_from_the_api_response() -> None:
    vector = [0.1, 0.2, 0.3]
    client = FakeEmbeddingClient(vector)
    embedding_client = OpenAIEmbeddingClient(model_name="fake-embedding-model", client=client)

    result = embedding_client.embed("Working on the Henderson onboarding redesign")

    assert result == vector


def test_embed_calls_the_api_with_the_configured_model_and_input_text() -> None:
    client = FakeEmbeddingClient([0.0])
    embedding_client = OpenAIEmbeddingClient(model_name="fake-embedding-model", client=client)

    embedding_client.embed("some fact")

    assert client.embeddings.calls == [{"model": "fake-embedding-model", "input": "some fact"}]
