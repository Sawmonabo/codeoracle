"""vLLM OpenAI-compatible embeddings provider."""

from codeoracle.adapters.llm.providers.openai_embed import (
    OpenAIEmbeddingsProvider,
)


class VLLMEmbeddingsProvider(OpenAIEmbeddingsProvider):
    """Embedding adapter for vLLM's OpenAI-compatible /v1/embeddings API."""

    def __init__(
        self,
        *,
        model: str,
        dim: int,
        base_url: str,
        api_key: str = "EMPTY",
        timeout_seconds: float = 30.0,
    ) -> None:
        super().__init__(
            model=model,
            dim=dim,
            base_url=base_url,
            api_key=api_key or "EMPTY",
            timeout_seconds=timeout_seconds,
        )
