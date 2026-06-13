"""Pure-function pipeline for per-file embedding extraction.

The consumer's `handle()` method delegates to this function. The consumer
constructs the appropriate chunker per message via `chunker_for(file_path,
embedding_config=...)` (LineChunker for code, ParagraphChunker for docs —
G14) and passes it in via DI. This function only sees the Chunker ABC —
it does not know or care which concrete implementation it received.

Pipeline:
1. Read file as text (UTF-8, errors="replace" — corrupt bytes get replaced
   characters, NOT a hard failure).
2. Split into lines (`text.splitlines(keepends=False)`).
3. Chunk via the injected Chunker (LineChunker for code, ParagraphChunker for
   docs).
4. Embed all chunk texts in one batch call (efficient — single HTTP
   round-trip).
5. Validate every returned vector has length == embedding_dim.
6. `EmbeddingsRepository.replace_file_embeddings(...)` (atomic delete +
   insert).
7. `SnapshotFileOutcomeRepository.upsert(outcome=success)`.
8. Commit.

On any error: rollback + write outcome=failed (with truncated message).
TransientWorkerError from the provider (rate limit, 5xx) is re-raised so the
WorkerConsumer's retry path picks it up.
"""

import logging
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession

from codeoracle.adapters.llm.embeddings import EmbeddingsProvider
from codeoracle.core.exceptions import (
    EmbedExtractionError,
    TransientWorkerError,
)
from codeoracle.db.repositories.embeddings import EmbeddingsRepository
from codeoracle.db.repositories.snapshot_file_outcome import (
    SnapshotFileOutcomeRepository,
    SnapshotFileOutcomeRow,
)
from codeoracle.models import EmbeddingChunk
from codeoracle.services.embedding.chunker import Chunk, Chunker
from codeoracle.workers.embed_indexer.message import EmbedIndexerMessage

_LOG = logging.getLogger(__name__)
_ERROR_MESSAGE_CHARS = 512


async def extract_file_to_embeddings(
    *,
    message: EmbedIndexerMessage,
    embeddings_provider: EmbeddingsProvider,
    chunker: Chunker,
    embeddings_repo: EmbeddingsRepository,
    outcome_repo: SnapshotFileOutcomeRepository,
    session: AsyncSession,
    embedding_dim: int,
) -> None:
    abs_path = Path(message.repo_root) / message.file_path
    text = await _read_source_text(
        abs_path=abs_path,
        message=message,
        outcome_repo=outcome_repo,
        session=session,
    )
    if text is None:
        return

    lines = text.splitlines()
    chunk_records = chunker.chunk(lines)
    embedding_chunks: list[EmbeddingChunk] = []

    if chunk_records:
        vectors = await _embed_chunks(
            chunk_records=chunk_records,
            embeddings_provider=embeddings_provider,
            message=message,
            outcome_repo=outcome_repo,
            session=session,
        )
        if vectors is None:
            return
        if not await _validate_vectors(
            vectors=vectors,
            chunk_records=chunk_records,
            embedding_dim=embedding_dim,
            message=message,
            outcome_repo=outcome_repo,
            session=session,
        ):
            return
        embedding_chunks = _build_embedding_chunks(
            message=message,
            chunk_records=chunk_records,
            vectors=vectors,
        )

    try:
        await embeddings_repo.replace_file_embeddings(
            snapshot_sha=message.snapshot_sha,
            source_path=message.file_path,
            chunks=embedding_chunks,
        )
        await outcome_repo.upsert(
            SnapshotFileOutcomeRow(
                snapshot_sha=message.snapshot_sha,
                file_path=message.file_path,
                extractor="embed",
                outcome="success",
                error_message=None,
            )
        )
        await session.commit()
    except Exception as e:
        await session.rollback()
        await _record_failed_outcome(
            message=message,
            outcome_repo=outcome_repo,
            session=session,
            error_message=f"db write: {str(e)[:_ERROR_MESSAGE_CHARS]}",
        )
        raise


async def _read_source_text(
    *,
    abs_path: Path,
    message: EmbedIndexerMessage,
    outcome_repo: SnapshotFileOutcomeRepository,
    session: AsyncSession,
) -> str | None:
    try:
        return abs_path.read_text(encoding="utf-8", errors="replace")
    except FileNotFoundError:
        _LOG.error("embed: file not found: %s", abs_path)
        await _record_failed_outcome(
            message=message,
            outcome_repo=outcome_repo,
            session=session,
            error_message=f"file not found: {abs_path}",
        )
        return None
    except OSError as e:
        _LOG.exception("embed: file read error for %s", abs_path)
        await _record_failed_outcome(
            message=message,
            outcome_repo=outcome_repo,
            session=session,
            error_message=f"file read: {str(e)[:_ERROR_MESSAGE_CHARS]}",
        )
        return None


async def _embed_chunks(
    *,
    chunk_records: list[Chunk],
    embeddings_provider: EmbeddingsProvider,
    message: EmbedIndexerMessage,
    outcome_repo: SnapshotFileOutcomeRepository,
    session: AsyncSession,
) -> list[list[float]] | None:
    try:
        return await embeddings_provider.embed([c.text for c in chunk_records])
    except TransientWorkerError:
        raise
    except EmbedExtractionError as e:
        _LOG.exception("embed: provider terminal error")
        await _record_failed_outcome(
            message=message,
            outcome_repo=outcome_repo,
            session=session,
            error_message=f"embed provider: {str(e)[:_ERROR_MESSAGE_CHARS]}",
        )
        return None
    except Exception as e:
        _LOG.exception("embed: unexpected provider error")
        await _record_failed_outcome(
            message=message,
            outcome_repo=outcome_repo,
            session=session,
            error_message=f"embed unexpected: {str(e)[:_ERROR_MESSAGE_CHARS]}",
        )
        return None


async def _validate_vectors(
    *,
    vectors: list[list[float]],
    chunk_records: list[Chunk],
    embedding_dim: int,
    message: EmbedIndexerMessage,
    outcome_repo: SnapshotFileOutcomeRepository,
    session: AsyncSession,
) -> bool:
    if len(vectors) != len(chunk_records):
        await _record_failed_outcome(
            message=message,
            outcome_repo=outcome_repo,
            session=session,
            error_message=(
                f"provider returned {len(vectors)} vectors for "
                f"{len(chunk_records)} chunks"
            ),
        )
        return False

    for i, vec in enumerate(vectors):
        if len(vec) != embedding_dim:
            await _record_failed_outcome(
                message=message,
                outcome_repo=outcome_repo,
                session=session,
                error_message=(
                    f"vector {i} dim={len(vec)} expected={embedding_dim}"
                ),
            )
            return False
    return True


def _build_embedding_chunks(
    *,
    message: EmbedIndexerMessage,
    chunk_records: list[Chunk],
    vectors: list[list[float]],
) -> list[EmbeddingChunk]:
    return [
        EmbeddingChunk(
            snapshot_sha=message.snapshot_sha,
            source_path=message.file_path,
            chunk_start_line=c.start_line,
            chunk_end_line=c.end_line,
            content=c.text,
            embedding=vectors[i],
        )
        for i, c in enumerate(chunk_records)
    ]


async def _record_failed_outcome(
    *,
    message: EmbedIndexerMessage,
    outcome_repo: SnapshotFileOutcomeRepository,
    session: AsyncSession,
    error_message: str,
) -> None:
    await outcome_repo.upsert(
        SnapshotFileOutcomeRow(
            snapshot_sha=message.snapshot_sha,
            file_path=message.file_path,
            extractor="embed",
            outcome="failed",
            error_message=error_message,
        )
    )
    await session.commit()
