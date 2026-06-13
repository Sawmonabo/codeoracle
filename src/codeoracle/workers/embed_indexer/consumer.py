"""EmbedIndexerConsumer — per-file WorkerConsumer.

Stream: indexing.embed Group:  embed-indexer
count_per_poll = 5 (per-file embed is bottlenecked by the provider; sibling
workers can parallelize across files).

Chunker dispatch (G14): the consumer carries the EmbeddingConfig and calls
`chunker_for(message.file_path, embedding_config=...)` per message — code
files get LineChunker, doc files get ParagraphChunker. Both chunker classes
are pure / cheap to construct; we do not cache them on `self` because the
config is the only state and is already cached.
"""

import asyncio

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from codeoracle.adapters.llm.embeddings import EmbeddingsProvider
from codeoracle.core.config import EmbeddingConfig
from codeoracle.core.streams import GROUP_EMBED_INDEXER, STREAM_INDEXING_EMBED
from codeoracle.db.repositories.embeddings import EmbeddingsRepository
from codeoracle.db.repositories.snapshot_file_outcome import (
    SnapshotFileOutcomeRepository,
)
from codeoracle.services.embedding.chunker import chunker_for
from codeoracle.workers._base.consumer import (
    QueueClientProtocol,
    WorkerConsumer,
)
from codeoracle.workers._base.message import RetryPolicy
from codeoracle.workers.embed_indexer.message import EmbedIndexerMessage
from codeoracle.workers.embed_indexer.tasks import extract_file_to_embeddings


class EmbedIndexerConsumer(WorkerConsumer[EmbedIndexerMessage]):
    stream = STREAM_INDEXING_EMBED
    group = GROUP_EMBED_INDEXER
    message_type = EmbedIndexerMessage
    count_per_poll = 5
    block_ms = 5000

    def __init__(
        self,
        *,
        queue_client: QueueClientProtocol,
        retry_policy: RetryPolicy,
        consumer_name: str,
        stop_event: asyncio.Event,
        embeddings_provider: EmbeddingsProvider,
        embedding_config: EmbeddingConfig,
        db_session_factory: async_sessionmaker[AsyncSession],
    ) -> None:
        super().__init__(
            queue_client=queue_client,
            retry_policy=retry_policy,
            consumer_name=consumer_name,
            stop_event=stop_event,
            # reclaim_min_idle_ms intentionally omitted → the inherited reaper
            # stays OFF for this stream: a handle embeds N chunks bounded only
            # by
            # the provider's per-request HTTP timeout (no per-handle ceiling),
            # so
            # min-idle can't be sized > handle by construction the way Joern's
            # is.
            # Safe activation is gated on a per-handle bound (banked
            # unbounded-subprocess discovery).
        )
        self._provider = embeddings_provider
        self._embedding_config = embedding_config
        self._db_factory = db_session_factory

    async def handle(self, message: EmbedIndexerMessage) -> None:
        # G14: dispatch chunker per message — code files get LineChunker,
        # doc files (.md, .rst, .adoc) get ParagraphChunker. The Chunker ABC
        # erases the difference for the downstream task function.
        chunker = chunker_for(
            message.file_path, embedding_config=self._embedding_config
        )
        async with self._db_factory() as session:
            await extract_file_to_embeddings(
                message=message,
                embeddings_provider=self._provider,
                chunker=chunker,
                embeddings_repo=EmbeddingsRepository(session),
                outcome_repo=SnapshotFileOutcomeRepository(session),
                session=session,
                embedding_dim=self._embedding_config.dim,
            )
