"""EmbeddingsRepository: per-chunk vector writes + KNN reads.

Backed by Plan 3's `embeddings` table and HNSW index
`ix_embeddings_embedding_hnsw` (cosine distance operator `<=>`).

The writer is `workers/embed_indexer` (Plan 6 Task 13): one call to
`replace_file_embeddings` per consumed per-file message, with the file's full
chunk list.

The reader is Plan 8's `CodeSpanResolver` and the agent's similarity-search
proof generator: `knn(query_embedding, snapshot_sha, k)` returns the k nearest
chunks for one snapshot.

Atomicity: `replace_file_embeddings` performs DELETE-WHERE + INSERT inside a
single transaction (no commit; caller commits). A second call with the same
(snapshot_sha, source_path) replaces the prior chunk set entirely.
"""

from sqlalchemy import delete, insert, text
from sqlalchemy.ext.asyncio import AsyncSession

from codeoracle.db.models.embedding import EmbeddingRecord
from codeoracle.models import embedding


class EmbeddingsRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def replace_file_embeddings(
        self,
        *,
        snapshot_sha: str,
        source_path: str,
        chunks: list[embedding.EmbeddingChunk],
    ) -> None:
        """Atomically replace all embeddings for (snapshot_sha, source_path).

        Empty `chunks` list is legal — pure delete of any prior chunks.
        """
        await self._session.execute(
            delete(EmbeddingRecord).where(
                EmbeddingRecord.snapshot_sha == snapshot_sha,
                EmbeddingRecord.source_path == source_path,
            )
        )
        if chunks:
            await self._session.execute(
                insert(EmbeddingRecord),
                [
                    {
                        "snapshot_sha": c.snapshot_sha,
                        "source_path": c.source_path,
                        "chunk_start_line": c.chunk_start_line,
                        "chunk_end_line": c.chunk_end_line,
                        "content": c.content,
                        "embedding": c.embedding,
                    }
                    for c in chunks
                ],
            )

    async def knn(
        self,
        *,
        query_embedding: list[float],
        snapshot_sha: str,
        k: int,
    ) -> list[embedding.EmbeddingHit]:
        """Top-k chunks by cosine distance.

        Uses pgvector's `<=>` (cosine) operator and the HNSW index defined
        in migration 0002. The index is unnamed in the query path (PG picks
        it by data type + operator class). The per-query `SET LOCAL` GUCs
        below (`hnsw.ef_search` + `hnsw.iterative_scan`) stop the post-scan
        `snapshot_sha` filter from silently under-returning < k rows on a
        multi-snapshot table (NFR D3-2).

        Results are ordered by distance ascending — best match first.
        """
        # Use a parameterized raw SQL because pgvector's distance operator
        # isn't first-class in SQLAlchemy's expression language. The query
        # is parameterized (`:query`, `:snapshot_sha`, `:k`); no injection.
        stmt = text(
            """
            SELECT
                   snapshot_sha,
                   source_path,
                   chunk_start_line,
                   chunk_end_line,
                   content,
                   (embedding <=> :query) AS distance
            FROM embeddings
            WHERE snapshot_sha = :snapshot_sha
            ORDER BY embedding <=> :query
            LIMIT :k
            """
        )
        # Filtered-HNSW correctness (NFR D3-2): the snapshot_sha predicate is
        # applied AFTER the ANN scan, so the default ef_search=40 returns the
        # 40 globally-nearest rows and the filter can drop them all → silently
        # < k (down to 0) results as snapshots accumulate. Widen the candidate
        # pool (ef_search >= k) and enable iterative scanning so PG keeps
        # fetching until k rows satisfy the filter. SET LOCAL is txn-scoped —
        # the AsyncSession holds one transaction across these executes — so the
        # GUCs apply to the SELECT below and never leak onto the pooled
        # connection. Requires pgvector >= 0.8.0 (pinned for PG18 in Plan 2).
        await self._session.execute(
            text(f"SET LOCAL hnsw.ef_search = {max(k, 100)}")
        )
        await self._session.execute(
            text("SET LOCAL hnsw.iterative_scan = strict_order")
        )
        result = await self._session.execute(
            stmt,
            {
                "query": query_embedding,
                "snapshot_sha": snapshot_sha,
                "k": k,
            },
        )
        return [
            embedding.EmbeddingHit(
                snapshot_sha=row[0],
                source_path=row[1],
                chunk_start_line=row[2],
                chunk_end_line=row[3],
                content=row[4],
                distance=float(row[5]),
            )
            for row in result.all()
        ]
