"""
EmbedIndexerConsumer — handle() wiring sanity, including G14 chunker dispatch.
"""

import asyncio
import pathlib

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from codeoracle.adapters.llm.providers.mock_embed import MockEmbeddingsProvider
from codeoracle.core.config import EmbeddingConfig
from codeoracle.db.repositories.embeddings import EmbeddingsRepository
from codeoracle.workers._base.message import RetryPolicy
from codeoracle.workers.embed_indexer.consumer import EmbedIndexerConsumer
from codeoracle.workers.embed_indexer.message import EmbedIndexerMessage

pytestmark = [pytest.mark.integration, pytest.mark.worker]


def _make_consumer(
    *,
    queue_client,
    db_session_factory: async_sessionmaker[AsyncSession],
    config: EmbeddingConfig,
) -> EmbedIndexerConsumer:
    return EmbedIndexerConsumer(
        queue_client=queue_client,
        retry_policy=RetryPolicy(
            max_attempts=2,
        ),
        consumer_name="embed-test",
        stop_event=asyncio.Event(),
        embeddings_provider=MockEmbeddingsProvider(dim=config.dim),
        embedding_config=config,
        db_session_factory=db_session_factory,
    )


@pytest.mark.asyncio
async def test_consumer_handle_runs_pipeline_for_code_file(
    async_session_factory: async_sessionmaker[AsyncSession],
    valkey_queue_client,
    tmp_path: pathlib.Path,
) -> None:
    """Code file (.py) → LineChunker dispatch → at least one chunk row."""
    (tmp_path / "a.py").write_text("def foo(): pass\n")
    config = EmbeddingConfig(
        model="text-embedding-3-small", dim=1536, kind="mock"
    )
    consumer = _make_consumer(
        queue_client=valkey_queue_client,
        db_session_factory=async_session_factory,
        config=config,
    )
    msg = EmbedIndexerMessage(
        batch_id="sha-ec-py",
        snapshot_sha="sha-ec-py",
        repo_root=str(tmp_path),
        file_path="a.py",
    )
    await consumer.handle(msg)

    async with async_session_factory() as session:
        repo = EmbeddingsRepository(session)
        provider = MockEmbeddingsProvider(dim=config.dim)
        hits = await repo.knn(
            query_embedding=(await provider.embed(["foo"]))[0],
            snapshot_sha="sha-ec-py",
            k=5,
        )
        assert len(hits) >= 1


@pytest.mark.asyncio
async def test_consumer_handle_dispatches_paragraph_chunker_for_markdown(
    async_session_factory: async_sessionmaker[AsyncSession],
    valkey_queue_client,
    tmp_path: pathlib.Path,
) -> None:
    """
    G14: markdown file → ParagraphChunker dispatch → paragraph-bounded chunks.

    The doc file has three paragraphs (heading, intro, second), separated
    by blank lines; ParagraphChunker emits one chunk per paragraph (3 rows).
    LineChunker dispatch would have emitted a single chunk covering all 6
    lines — the row count + paragraph-bounded spans distinguish the two paths.

    Uses EmbeddingsRepository.knn (Task 6) to read back all rows for the
    snapshot — with MockEmbeddingsProvider's stable vectors and k larger
    than the chunk count, knn returns every row regardless of query content.
    """
    md_path = tmp_path / "README.md"
    md_path.write_text(
        "# Title\n\n"
        "First paragraph line 1.\n"
        "First paragraph line 2.\n\n"
        "Second paragraph.\n"
    )
    config = EmbeddingConfig(
        model="text-embedding-3-small", dim=1536, kind="mock"
    )
    consumer = _make_consumer(
        queue_client=valkey_queue_client,
        db_session_factory=async_session_factory,
        config=config,
    )
    msg = EmbedIndexerMessage(
        batch_id="sha-ec-md",
        snapshot_sha="sha-ec-md",
        repo_root=str(tmp_path),
        file_path="README.md",
    )
    await consumer.handle(msg)

    async with async_session_factory() as session:
        repo = EmbeddingsRepository(session)
        provider = MockEmbeddingsProvider(dim=config.dim)
        hits = await repo.knn(
            query_embedding=(await provider.embed(["anything"]))[0],
            snapshot_sha="sha-ec-md",
            k=10,
        )
        # Three paragraphs → three chunks → three rows.
        assert len(hits) == 3
        # Each row's (start_line, end_line) is paragraph-bounded, NOT a
        # fixed 100-line window — variable spans prove ParagraphChunker ran.
        spans = sorted((h.chunk_start_line, h.chunk_end_line) for h in hits)
        assert spans == [(1, 1), (3, 4), (6, 6)]
