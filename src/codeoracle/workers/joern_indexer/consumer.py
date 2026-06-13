"""JoernIndexerConsumer — the concrete WorkerConsumer for Joern."""

import asyncio

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from codeoracle.adapters.joern.client import JoernClient
from codeoracle.adapters.joern.parser import JoernCpgParser
from codeoracle.core.streams import GROUP_JOERN_INDEXER, STREAM_INDEXING_JOERN
from codeoracle.workers._base.consumer import (
    QueueClientProtocol,
    WorkerConsumer,
)
from codeoracle.workers._base.message import RetryPolicy
from codeoracle.workers.joern_indexer.message import JoernIndexerMessage
from codeoracle.workers.joern_indexer.tasks import extract_file_to_graph


class JoernIndexerConsumer(WorkerConsumer[JoernIndexerMessage]):
    """Consumes indexing.joern → calls JoernClient → MERGEs into AGE."""

    stream = STREAM_INDEXING_JOERN
    group = GROUP_JOERN_INDEXER
    message_type = JoernIndexerMessage
    # ONE message per poll: joern extraction is slow (a JVM cold-start + parse
    # + export per file), so the reclaim min-idle must clear a full handle.
    # Batching would multiply the in-flight window (count_per_poll x handle)
    # past the orchestrator's 600s barrier, leaving no safe min-idle. See the
    # reclaim NOTE in WorkerConsumer.run().
    count_per_poll = 1
    block_ms = 5000

    def __init__(
        self,
        *,
        queue_client: QueueClientProtocol,
        retry_policy: RetryPolicy,
        consumer_name: str,
        stop_event: asyncio.Event,
        joern_client: JoernClient,
        parser: JoernCpgParser,
        db_session_factory: async_sessionmaker[AsyncSession],
    ) -> None:
        super().__init__(
            queue_client=queue_client,
            retry_policy=retry_policy,
            consumer_name=consumer_name,
            stop_event=stop_event,
            # Reclaim entries idle past 2x the worst-case handle. One x
            # covers an extraction running to its parse+export timeout
            # ceiling; the second x is margin so XAUTOCLAIM never re-claims a
            # still-running extraction. (handle() is idempotent — outcome
            # upsert + replace_file_subgraph — so an over-eager reclaim would
            # not corrupt the graph, but it WOULD spawn a concurrent duplicate
            # extraction: a re-claim stampede burning a worker slot + DB conn.)
            # Derived from config, so min-idle > handle holds by construction
            # (no runtime guard needed); count_per_poll=1 keeps the in-flight
            # bound at one handle.
            reclaim_min_idle_ms=(
                2 * joern_client.worst_case_extraction_seconds * 1000
            ),
        )
        self._joern = joern_client
        self._parser = parser
        self._db = db_session_factory

    async def handle(self, message: JoernIndexerMessage) -> None:
        await extract_file_to_graph(
            message=message,
            joern_client=self._joern,
            parser=self._parser,
            db_session_factory=self._db,
        )
