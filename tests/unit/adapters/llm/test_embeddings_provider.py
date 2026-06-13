"""EmbeddingsProvider and mock-provider contract tests."""

import asyncio
import math
from typing import Any, cast

import pytest

from codeoracle.adapters.llm.embeddings import EmbeddingsProvider
from codeoracle.adapters.llm.providers.mock_embed import MockEmbeddingsProvider
from codeoracle.adapters.llm.providers.openai_embed import (
    OpenAIEmbeddingsProvider,
)
from codeoracle.core.config import EmbeddingConfig


def test_abc_cannot_be_instantiated() -> None:
    provider_type = cast(Any, EmbeddingsProvider)
    with pytest.raises(TypeError):
        provider_type()


@pytest.mark.asyncio
async def test_mock_returns_per_input_vector() -> None:
    provider = MockEmbeddingsProvider(dim=1536)
    out = await provider.embed(["alpha", "beta", "gamma"])
    assert len(out) == 3
    for vec in out:
        assert len(vec) == 1536
        assert all(isinstance(x, float) for x in vec)


@pytest.mark.asyncio
async def test_mock_deterministic_for_same_input() -> None:
    """The MockEmbeddingsProvider hashes inputs to vectors deterministically.
    This lets tests assert specific neighbor relationships without flakiness.
    """
    provider = MockEmbeddingsProvider(dim=1536)
    out1 = await provider.embed(["alpha"])
    out2 = await provider.embed(["alpha"])
    assert out1 == out2

    out_alpha = await provider.embed(["alpha"])
    out_beta = await provider.embed(["beta"])
    assert out_alpha != out_beta


@pytest.mark.asyncio
async def test_mock_empty_input_returns_empty_list() -> None:
    provider = MockEmbeddingsProvider(dim=1536)
    out = await provider.embed([])
    assert out == []


@pytest.mark.asyncio
async def test_mock_dim_mismatch_raises_on_construction() -> None:
    """Sanity: zero or negative dim is invalid."""
    with pytest.raises(ValueError, match="dim must be positive"):
        MockEmbeddingsProvider(dim=0)
    with pytest.raises(ValueError, match="dim must be positive"):
        MockEmbeddingsProvider(dim=-100)


def test_mock_vectors_are_unit_normalized() -> None:
    """For cosine-distance KNN tests, unit-normalized vectors are easier to
    reason about. The mock returns vectors with magnitude == 1.0 (within
    floating-point tolerance) so the cosine distance between identical inputs
    is exactly 0."""
    provider = MockEmbeddingsProvider(dim=1536)

    vecs = asyncio.run(provider.embed(["alpha", "beta"]))
    for vec in vecs:
        magnitude = math.sqrt(sum(x * x for x in vec))
        assert abs(magnitude - 1.0) < 1e-6


def test_openai_embeddings_provider_rejects_empty_api_key() -> None:
    """Pin the design decision: the api_key precondition lives on
    OpenAIEmbeddingsProvider.__init__, NOT on EmbeddingConfig's model
    validator.

    Without this test, a future engineer could "consolidate" the check into
    EmbeddingConfig and break Plan 3's `tests/unit/core/test_config.py` (which
    directly constructs EmbeddingConfig(model=..., dim=...) at lines 447, 451)
    plus the `_ec()` helper in
    `tests/unit/core/test_embedding_config_chunk_constants.py`
    — both of which intentionally construct EmbeddingConfig without an api_key.
    See the rationale block in OpenAIEmbeddingsProvider.__init__."""
    with pytest.raises(ValueError, match="api_key is required"):
        OpenAIEmbeddingsProvider(
            base_url="https://api.openai.com",
            api_key="",
            model="text-embedding-3-small",
            dim=1536,
        )


def test_embedding_config_constructs_without_api_key() -> None:
    """Pin the dual of `test_openai_embeddings_provider_rejects_empty_api_key`:
    constructing EmbeddingConfig with no api_key succeeds. Task 7 added
    `kind` / `base_url` / `api_key` as fields with defaults but MUST NOT
    add a cross-plan break to Plan 3's `tests/unit/core/test_config.py`
    where lines 447, 451 directly construct `EmbeddingConfig(model=...,
    dim=...)`.

    The pair of tests (this + the provider-rejection one above) enforces
    the architecture: precondition lives on the consumer class, not the
    config carrier."""
    cfg = EmbeddingConfig(model="text-embedding-3-small", dim=1536)
    assert cfg.kind == "openai"  # default
    assert cfg.api_key == ""  # default — note: NOT validated at this layer
    assert cfg.base_url == "https://api.openai.com"  # default
    assert cfg.embed_chunk_lines == 100  # default (added Task 3)
    assert cfg.embed_chunk_overlap == 20  # default (added Task 3)
