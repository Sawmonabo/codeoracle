import os

import pytest

from codeoracle.adapters.llm.providers.openai_embed import (
    OpenAIEmbeddingsProvider,
)

pytestmark = [pytest.mark.integration, pytest.mark.live_embeddings]


@pytest.mark.asyncio
async def test_openai_embeddings_provider_live_roundtrip() -> None:
    api_key = os.environ.get("CODEORACLE_EMBEDDING__API_KEY")
    if not api_key:
        pytest.skip(reason="CODEORACLE_EMBEDDING__API_KEY is required")

    dim = int(os.environ.get("CODEORACLE_EMBEDDING__DIM", "1536"))
    provider = OpenAIEmbeddingsProvider(
        api_key=api_key,
        model=os.environ.get(
            "CODEORACLE_EMBEDDING__MODEL", "text-embedding-3-small"
        ),
        dim=dim,
        base_url=os.environ.get("CODEORACLE_EMBEDDING__BASE_URL")
        or "https://api.openai.com",
    )
    try:
        vectors = await provider.embed(["CodeOracle live embeddings smoke"])
    finally:
        await provider.aclose()

    assert len(vectors) == 1
    assert len(vectors[0]) == dim
