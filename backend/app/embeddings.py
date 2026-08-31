from typing import List, Optional, Protocol

from openai import OpenAI

from app.config import EMBEDDING_MODEL


class EmbeddingClient(Protocol):
    def embed(self, text: str) -> List[float]:
        """Return a vector embedding for text, for semantic similarity search."""
        ...


class OpenAIEmbeddingClient:
    def __init__(self, model_name: str = EMBEDDING_MODEL, client: Optional[OpenAI] = None) -> None:
        self.model_name = model_name
        self._client = client or OpenAI()

    def embed(self, text: str) -> List[float]:
        response = self._client.embeddings.create(model=self.model_name, input=text)
        return response.data[0].embedding


def get_default_embedding_client() -> EmbeddingClient:
    return OpenAIEmbeddingClient()
