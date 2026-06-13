"""Embedding provider base protocol."""

from abc import ABC, abstractmethod


class EmbeddingsProvider(ABC):
    """Async vector provider contract for embedding batches."""

    @abstractmethod
    async def embed(self, texts: list[str]) -> list[list[float]]:
        """Embed each input text. Returns vectors in the same order."""
        raise NotImplementedError

    @property
    @abstractmethod
    def dim(self) -> int:
        """Vector dimension this provider produces."""
        raise NotImplementedError
