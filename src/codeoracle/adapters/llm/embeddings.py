"""EmbeddingsProvider ABC + factory binding.

Plan 6. Sibling of `LLMProvider` (Plan 4). Each CodeOracle deployment selects a
concrete provider via `EmbeddingsProviderFactory.build(settings)`:
- "openai" — `OpenAIEmbeddingsProvider` (default)
- "vllm"   — `VLLMEmbeddingsProvider` (vLLM serves OpenAI-compatible
             embeddings on `/v1/embeddings`; the vLLM-specific class preserves
             the import surface named by the archived preflight fallback)
- "mock"   — `MockEmbeddingsProvider` (deterministic, used in non-live tests)

The concrete ABC lives in `embedding_base.py`; this module keeps the historical
public import path for the ABC and owns provider construction.
"""

from codeoracle.adapters.llm.embedding_base import EmbeddingsProvider
from codeoracle.adapters.llm.providers.mock_embed import MockEmbeddingsProvider
from codeoracle.adapters.llm.providers.openai_embed import (
    OpenAIEmbeddingsProvider,
)
from codeoracle.adapters.llm.providers.vllm_embed import (
    VLLMEmbeddingsProvider,
)
from codeoracle.core.config import Settings


class EmbeddingsProviderFactory:
    """Build the configured embedding provider."""

    @staticmethod
    def build(settings: Settings) -> EmbeddingsProvider:
        config = settings.embedding
        if config.kind == "mock":
            return MockEmbeddingsProvider(config.dim)
        if config.kind == "vllm":
            return VLLMEmbeddingsProvider(
                model=config.model,
                dim=config.dim,
                base_url=config.base_url,
                api_key=config.api_key,
            )
        if config.kind == "openai":
            return OpenAIEmbeddingsProvider(
                model=config.model,
                dim=config.dim,
                base_url=config.base_url,
                api_key=config.api_key,
            )
        raise ValueError(f"unknown embedding provider kind: {config.kind!r}")
