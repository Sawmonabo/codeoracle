"""ScipIndexerConsumer — project-mode WorkerConsumer.

Stream: indexing.scip Group:  scip-indexer
count_per_poll = 1 (project messages are slow; running multiple in parallel
risks Valkey connection idle timeouts and double-running scip-python on
the same project).
"""

import asyncio

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from codeoracle.adapters.scip.client import ScipClient
from codeoracle.adapters.scip.parser import ScipIndexParser
from codeoracle.core.streams import GROUP_SCIP_INDEXER, STREAM_INDEXING_SCIP
from codeoracle.db.repositories.scip_graph import ScipGraphRepository
from codeoracle.db.repositories.snapshot_file_outcome import (
    SnapshotFileOutcomeRepository,
)
from codeoracle.workers._base.consumer import (
    QueueClientProtocol,
    WorkerConsumer,
)
from codeoracle.workers._base.message import RetryPolicy
from codeoracle.workers.scip_indexer.message import ScipIndexerMessage
from codeoracle.workers.scip_indexer.tasks import extract_project_to_graph


class ScipIndexerConsumer(WorkerConsumer[ScipIndexerMessage]):
    stream = STREAM_INDEXING_SCIP
    group = GROUP_SCIP_INDEXER
    message_type = ScipIndexerMessage
    count_per_poll = 1
    block_ms = 5000

    def __init__(
        self,
        *,
        queue_client: QueueClientProtocol,
        retry_policy: RetryPolicy,
        consumer_name: str,
        stop_event: asyncio.Event,
        scip_client: ScipClient,
        parser: ScipIndexParser,
        db_session_factory: async_sessionmaker[AsyncSession],
    ) -> None:
        super().__init__(
            queue_client=queue_client,
            retry_policy=retry_policy,
            consumer_name=consumer_name,
            stop_event=stop_event,
            # reclaim_min_idle_ms intentionally omitted → the inherited reaper
            # stays OFF for this stream. The scip handle is now bounded by the
            # ScipClient index/print timeouts (default 300s + 60s), so the
            # double-run hazard that previously gated reclaim is gone. Turning
            # reclaim ON is a deferred follow-up: size reclaim_min_idle_ms off
            # the worst-case handle (index + print) while keeping
            # 2 * worst_case + one_handle < the orchestrator completion barrier
            # (600s, Plan 5).
        )
        self._scip_client = scip_client
        self._parser = parser
        self._db_factory = db_session_factory

    async def handle(self, message: ScipIndexerMessage) -> None:
        async with self._db_factory() as session:
            scip_graph_repo = ScipGraphRepository(session)
            outcome_repo = SnapshotFileOutcomeRepository(session)
            await extract_project_to_graph(
                message=message,
                scip_client=self._scip_client,
                parser=self._parser,
                scip_graph_repo=scip_graph_repo,
                outcome_repo=outcome_repo,
                session=session,
            )
