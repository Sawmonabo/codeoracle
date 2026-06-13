"""Pure-function tests for `extract_file_to_embeddings`."""

import pathlib
from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from codeoracle.adapters.llm.providers.mock_embed import MockEmbeddingsProvider
from codeoracle.core.config import EmbeddingConfig
from codeoracle.db.repositories.embeddings import EmbeddingsRepository
from codeoracle.db.repositories.snapshot_file_outcome import (
    SnapshotFileOutcomeRepository,
)
from codeoracle.services.embedding.chunker import LineChunker
from codeoracle.workers.embed_indexer.message import EmbedIndexerMessage
from codeoracle.workers.embed_indexer.tasks import extract_file_to_embeddings

pytestmark = [pytest.mark.integration, pytest.mark.worker]


@pytest_asyncio.fixture
async def session(
    async_session_factory: async_sessionmaker[AsyncSession],
) -> AsyncIterator[AsyncSession]:
    async with async_session_factory() as s:
        yield s


@pytest.mark.asyncio
async def test_success_writes_chunks_and_outcome(
    session: AsyncSession,
    tmp_path: pathlib.Path,
) -> None:
    f = tmp_path / "a.py"
    f.write_text("\n".join(f"line {i}" for i in range(1, 251)) + "\n")
    msg = EmbedIndexerMessage(
        batch_id="sha-e-ok",
        snapshot_sha="sha-e-ok",
        repo_root=str(tmp_path),
        file_path="a.py",
    )
    config = EmbeddingConfig(
        model="text-embedding-3-small", dim=1536, kind="mock"
    )
    provider = MockEmbeddingsProvider(dim=config.dim)
    chunker = LineChunker(
        chunk_lines=config.embed_chunk_lines,
        overlap=config.embed_chunk_overlap,
    )
    embeddings_repo = EmbeddingsRepository(session)
    outcome_repo = SnapshotFileOutcomeRepository(session)

    await extract_file_to_embeddings(
        message=msg,
        embeddings_provider=provider,
        chunker=chunker,
        embeddings_repo=embeddings_repo,
        outcome_repo=outcome_repo,
        session=session,
        embedding_dim=config.dim,
    )

    hits = await embeddings_repo.knn(
        query_embedding=(await provider.embed(["line 50"]))[0],
        snapshot_sha="sha-e-ok",
        k=10,
    )
    assert (
        len(hits) == 3
    )  # 250 lines / 100 chunk_lines with 20 overlap → 3 chunks
    assert (
        await outcome_repo.count_completed(
            snapshot_sha="sha-e-ok", extractor="embed"
        )
        == 1
    )


@pytest.mark.asyncio
async def test_missing_file_marks_failed(
    session: AsyncSession,
    tmp_path: pathlib.Path,
) -> None:
    msg = EmbedIndexerMessage(
        batch_id="sha-e-miss",
        snapshot_sha="sha-e-miss",
        repo_root=str(tmp_path),
        file_path="does-not-exist.py",
    )
    config = EmbeddingConfig(
        model="text-embedding-3-small", dim=1536, kind="mock"
    )
    provider = MockEmbeddingsProvider(dim=config.dim)
    chunker = LineChunker(
        chunk_lines=config.embed_chunk_lines,
        overlap=config.embed_chunk_overlap,
    )
    embeddings_repo = EmbeddingsRepository(session)
    outcome_repo = SnapshotFileOutcomeRepository(session)

    await extract_file_to_embeddings(
        message=msg,
        embeddings_provider=provider,
        chunker=chunker,
        embeddings_repo=embeddings_repo,
        outcome_repo=outcome_repo,
        session=session,
        embedding_dim=config.dim,
    )
    assert (
        await outcome_repo.count_failed(
            snapshot_sha="sha-e-miss", extractor="embed"
        )
        == 1
    )


@pytest.mark.asyncio
async def test_empty_file_writes_zero_chunks_and_success(
    session: AsyncSession,
    tmp_path: pathlib.Path,
) -> None:
    f = tmp_path / "empty.py"
    f.write_text("")
    msg = EmbedIndexerMessage(
        batch_id="sha-e-empty",
        snapshot_sha="sha-e-empty",
        repo_root=str(tmp_path),
        file_path="empty.py",
    )
    config = EmbeddingConfig(
        model="text-embedding-3-small", dim=1536, kind="mock"
    )
    provider = MockEmbeddingsProvider(dim=config.dim)
    chunker = LineChunker(
        chunk_lines=config.embed_chunk_lines,
        overlap=config.embed_chunk_overlap,
    )
    embeddings_repo = EmbeddingsRepository(session)
    outcome_repo = SnapshotFileOutcomeRepository(session)

    await extract_file_to_embeddings(
        message=msg,
        embeddings_provider=provider,
        chunker=chunker,
        embeddings_repo=embeddings_repo,
        outcome_repo=outcome_repo,
        session=session,
        embedding_dim=config.dim,
    )
    # No chunks → outcome=success (file attempted).
    assert (
        await outcome_repo.count_completed(
            snapshot_sha="sha-e-empty", extractor="embed"
        )
        == 1
    )


@pytest.mark.asyncio
async def test_provider_wrong_dim_marks_failed(
    session: AsyncSession,
    tmp_path: pathlib.Path,
) -> None:
    f = tmp_path / "a.py"
    f.write_text("line one\n")
    msg = EmbedIndexerMessage(
        batch_id="sha-e-wrongdim",
        snapshot_sha="sha-e-wrongdim",
        repo_root=str(tmp_path),
        file_path="a.py",
    )
    # Provider configured for dim=512 but worker expects 1536.
    provider = MockEmbeddingsProvider(dim=512)
    chunker = LineChunker(chunk_lines=100, overlap=20)
    embeddings_repo = EmbeddingsRepository(session)
    outcome_repo = SnapshotFileOutcomeRepository(session)

    await extract_file_to_embeddings(
        message=msg,
        embeddings_provider=provider,
        chunker=chunker,
        embeddings_repo=embeddings_repo,
        outcome_repo=outcome_repo,
        session=session,
        embedding_dim=1536,
    )
    assert (
        await outcome_repo.count_failed(
            snapshot_sha="sha-e-wrongdim", extractor="embed"
        )
        == 1
    )
