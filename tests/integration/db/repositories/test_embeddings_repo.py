"""
EmbeddingsRepository — unit tests against testcontainers PG with pgvector +
HNSW index.
"""

from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from codeoracle.core.config import EmbeddingConfig
from codeoracle.db.repositories.embeddings import EmbeddingsRepository
from codeoracle.models import EmbeddingChunk

pytestmark = pytest.mark.integration


@pytest_asyncio.fixture
async def session(
    async_session_factory: async_sessionmaker[AsyncSession],
) -> AsyncIterator[AsyncSession]:
    async with async_session_factory() as s:
        yield s


def _dummy_vec(dim: int, base: float = 0.0) -> list[float]:
    """Stable test vector — small bias toward index `(i + base) % dim`.

    Useful for KNN tests: we can construct two vectors that are obviously
    closer/farther in cosine space.
    """
    v = [0.0] * dim
    v[int(base) % dim] = 1.0
    return v


@pytest.mark.asyncio
async def test_replace_inserts_chunks(session: AsyncSession) -> None:
    repo = EmbeddingsRepository(session)
    config = EmbeddingConfig(
        model="text-embedding-3-small", dim=1536, kind="mock"
    )

    chunks = [
        EmbeddingChunk(
            snapshot_sha="s1",
            source_path="a.py",
            chunk_start_line=1,
            chunk_end_line=100,
            content="def foo(): pass",
            embedding=_dummy_vec(config.dim, 0),
        ),
        EmbeddingChunk(
            snapshot_sha="s1",
            source_path="a.py",
            chunk_start_line=81,
            chunk_end_line=180,
            content="def bar(): pass",
            embedding=_dummy_vec(config.dim, 1),
        ),
    ]
    await repo.replace_file_embeddings(
        snapshot_sha="s1", source_path="a.py", chunks=chunks
    )
    await session.commit()

    hits = await repo.knn(
        query_embedding=_dummy_vec(config.dim, 0),
        snapshot_sha="s1",
        k=10,
    )
    assert len(hits) == 2
    # The first hit should be the chunk whose vector is exactly equal to the
    # query.
    assert hits[0].chunk_start_line == 1
    assert hits[0].chunk_end_line == 100


@pytest.mark.asyncio
async def test_replace_is_atomic(session: AsyncSession) -> None:
    """
    A subsequent replace_file_embeddings call MUST clear prior chunks for that
    file.
    """
    repo = EmbeddingsRepository(session)
    config = EmbeddingConfig(
        model="text-embedding-3-small", dim=1536, kind="mock"
    )

    initial = [
        EmbeddingChunk(
            snapshot_sha="s2",
            source_path="a.py",
            chunk_start_line=1,
            chunk_end_line=100,
            content="v1",
            embedding=_dummy_vec(config.dim, 0),
        ),
        EmbeddingChunk(
            snapshot_sha="s2",
            source_path="a.py",
            chunk_start_line=101,
            chunk_end_line=200,
            content="v2",
            embedding=_dummy_vec(config.dim, 1),
        ),
    ]
    await repo.replace_file_embeddings(
        snapshot_sha="s2", source_path="a.py", chunks=initial
    )
    await session.commit()

    # Re-run with a different chunk set; the prior two MUST be gone.
    second = [
        EmbeddingChunk(
            snapshot_sha="s2",
            source_path="a.py",
            chunk_start_line=50,
            chunk_end_line=150,
            content="vNEW",
            embedding=_dummy_vec(config.dim, 2),
        ),
    ]
    await repo.replace_file_embeddings(
        snapshot_sha="s2", source_path="a.py", chunks=second
    )
    await session.commit()

    hits = await repo.knn(
        query_embedding=_dummy_vec(config.dim, 2),
        snapshot_sha="s2",
        k=10,
    )
    assert len(hits) == 1
    assert hits[0].content == "vNEW"


@pytest.mark.asyncio
async def test_replace_only_affects_target_file(session: AsyncSession) -> None:
    """
    Replacing chunks for one file MUST NOT touch another file in the same
    snapshot.
    """
    repo = EmbeddingsRepository(session)
    config = EmbeddingConfig(
        model="text-embedding-3-small", dim=1536, kind="mock"
    )

    await repo.replace_file_embeddings(
        snapshot_sha="s3",
        source_path="a.py",
        chunks=[
            EmbeddingChunk(
                snapshot_sha="s3",
                source_path="a.py",
                chunk_start_line=1,
                chunk_end_line=100,
                content="A",
                embedding=_dummy_vec(config.dim, 0),
            ),
        ],
    )
    await repo.replace_file_embeddings(
        snapshot_sha="s3",
        source_path="b.py",
        chunks=[
            EmbeddingChunk(
                snapshot_sha="s3",
                source_path="b.py",
                chunk_start_line=1,
                chunk_end_line=100,
                content="B",
                embedding=_dummy_vec(config.dim, 5),
            ),
        ],
    )
    await session.commit()

    # Clear `a.py` only.
    await repo.replace_file_embeddings(
        snapshot_sha="s3", source_path="a.py", chunks=[]
    )
    await session.commit()

    hits = await repo.knn(
        query_embedding=_dummy_vec(config.dim, 5),
        snapshot_sha="s3",
        k=10,
    )
    assert len(hits) == 1
    assert hits[0].source_path == "b.py"


@pytest.mark.asyncio
async def test_knn_isolated_by_snapshot(session: AsyncSession) -> None:
    repo = EmbeddingsRepository(session)
    config = EmbeddingConfig(
        model="text-embedding-3-small", dim=1536, kind="mock"
    )

    await repo.replace_file_embeddings(
        snapshot_sha="s4-A",
        source_path="a.py",
        chunks=[
            EmbeddingChunk(
                snapshot_sha="s4-A",
                source_path="a.py",
                chunk_start_line=1,
                chunk_end_line=100,
                content="content-A",
                embedding=_dummy_vec(config.dim, 0),
            ),
        ],
    )
    await repo.replace_file_embeddings(
        snapshot_sha="s4-B",
        source_path="a.py",
        chunks=[
            EmbeddingChunk(
                snapshot_sha="s4-B",
                source_path="a.py",
                chunk_start_line=1,
                chunk_end_line=100,
                content="content-B",
                embedding=_dummy_vec(config.dim, 0),
            ),
        ],
    )
    await session.commit()

    hits_a = await repo.knn(
        query_embedding=_dummy_vec(config.dim, 0),
        snapshot_sha="s4-A",
        k=10,
    )
    hits_b = await repo.knn(
        query_embedding=_dummy_vec(config.dim, 0),
        snapshot_sha="s4-B",
        k=10,
    )
    assert all(h.snapshot_sha == "s4-A" for h in hits_a)
    assert all(h.snapshot_sha == "s4-B" for h in hits_b)
    assert hits_a[0].content == "content-A"
    assert hits_b[0].content == "content-B"


@pytest.mark.asyncio
async def test_knn_respects_k(session: AsyncSession) -> None:
    repo = EmbeddingsRepository(session)
    config = EmbeddingConfig(
        model="text-embedding-3-small", dim=1536, kind="mock"
    )
    chunks = []
    for i in range(10):
        chunks.append(
            EmbeddingChunk(
                snapshot_sha="s5",
                source_path="a.py",
                chunk_start_line=i * 100 + 1,
                chunk_end_line=(i + 1) * 100,
                content=f"chunk-{i}",
                embedding=_dummy_vec(config.dim, i),
            )
        )
    await repo.replace_file_embeddings(
        snapshot_sha="s5", source_path="a.py", chunks=chunks
    )
    await session.commit()

    hits = await repo.knn(
        query_embedding=_dummy_vec(config.dim, 0), snapshot_sha="s5", k=3
    )
    assert len(hits) == 3
    # All hits have ascending distance.
    for i in range(len(hits) - 1):
        assert hits[i].distance <= hits[i + 1].distance


@pytest.mark.asyncio
async def test_replace_empty_chunk_list_is_legal(
    session: AsyncSession,
) -> None:
    repo = EmbeddingsRepository(session)
    await repo.replace_file_embeddings(
        snapshot_sha="s6", source_path="empty.py", chunks=[]
    )
    await session.commit()
    hits = await repo.knn(
        query_embedding=[0.1]
        * EmbeddingConfig(
            model="text-embedding-3-small", dim=1536, kind="mock"
        ).dim,
        snapshot_sha="s6",
        k=10,
    )
    assert hits == []
