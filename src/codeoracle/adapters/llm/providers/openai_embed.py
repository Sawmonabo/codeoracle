"""OpenAI-compatible embeddings provider."""

import openai

from codeoracle.adapters.llm.embedding_base import EmbeddingsProvider
from codeoracle.core.exceptions import (
    EmbedExtractionError,
    TransientWorkerError,
)

_SERVER_ERROR_MIN_STATUS = 500


def _normalize_base_url(base_url: str) -> str:
    stripped = base_url.rstrip("/")
    if stripped == "https://api.openai.com":
        return f"{stripped}/v1"
    return stripped


class OpenAIEmbeddingsProvider(EmbeddingsProvider):
    """Adapter boundary for OpenAI-compatible embedding endpoints."""

    def __init__(
        self,
        *,
        model: str,
        dim: int,
        base_url: str = "https://api.openai.com",
        api_key: str,
        timeout_seconds: float = 30.0,
    ) -> None:
        if dim <= 0:
            raise ValueError(f"dim must be positive, got {dim}")
        if not api_key:
            raise ValueError(
                "api_key is required for OpenAIEmbeddingsProvider"
            )
        self._model = model
        self._dim = dim
        self._client = openai.AsyncOpenAI(
            base_url=_normalize_base_url(base_url),
            api_key=api_key,
            timeout=timeout_seconds,
            max_retries=0,
        )

    async def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        try:
            response = await self._client.embeddings.create(
                model=self._model,
                input=texts,
            )
        except openai.RateLimitError as exc:
            raise TransientWorkerError(f"openai rate limit: {exc}") from exc
        except (
            openai.APITimeoutError,
            openai.APIConnectionError,
            TimeoutError,
        ) as exc:
            raise TransientWorkerError(f"openai transient: {exc}") from exc
        except openai.APIStatusError as exc:
            if exc.status_code >= _SERVER_ERROR_MIN_STATUS:
                raise TransientWorkerError(f"openai 5xx: {exc}") from exc
            raise EmbedExtractionError(
                f"openai 4xx: {exc.status_code} {exc.message}"
            ) from exc

        if len(response.data) != len(texts):
            raise EmbedExtractionError(
                f"openai returned {len(response.data)} vectors for "
                f"{len(texts)} inputs"
            )

        vectors: list[list[float]] = []
        for index, item in enumerate(response.data):
            vector = list(item.embedding)
            if len(vector) != self._dim:
                raise EmbedExtractionError(
                    f"openai vector {index} has dim {len(vector)}, "
                    f"expected {self._dim}"
                )
            vectors.append(vector)
        return vectors

    @property
    def dim(self) -> int:
        return self._dim

    async def aclose(self) -> None:
        """Release the underlying OpenAI HTTP client."""
        await self._client.close()
