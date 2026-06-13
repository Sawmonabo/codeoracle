"""ReindexOrchestratorConsumer — drains STREAM_REINDEX_REQUESTS by
calling IndexingService.reindex with the pre-allocated snapshot_sha.

Concurrency: one consumer per replica. The advisory lock single-flights
anyway, so a second consumer would just block on lock acquisition; no
benefit from running multiples. Plan 1A's compose YAML provisions
exactly one `reindex_orchestrator` service.

Failure modes:
- IndexingService.reindex records an extractor-phase failure durably and
  RETURNS the FAILED snapshot (commit-before-return, see _reindex_locked) — it
  does NOT raise. handle() returns normally → _process_one ACKs, so the request
  is consumed exactly once and the client polling GET /v1/reindex/{sha} sees
  FAILED. No NACK, no retry, no duplicate run.
- A hard failure that escapes that path (reindex raises) → WorkerConsumer NACKs
  (leaves the message in the PEL). Reclaim is DISABLED on this consumer (see
  the
  class docstring), so the entry is not redelivered; recovery for any orphaned
  APPLYING row is the stale-APPLYING sweep on the next reindex request. This is
  deliberate — re-driving a whole reindex would only churn.
- Process SIGKILL → asyncpg connection closes → Postgres auto-releases
  the advisory lock. The orphaned APPLYING row is swept on next reindex.
"""

import asyncio
import logging

from codeoracle.core.streams import (
    GROUP_REINDEX_ORCHESTRATOR,
    STREAM_REINDEX_REQUESTS,
)
from codeoracle.services.indexing.service import IndexingService
from codeoracle.workers._base.consumer import (
    QueueClientProtocol,
    WorkerConsumer,
)
from codeoracle.workers._base.message import RetryPolicy
from codeoracle.workers.reindex_orchestrator.message import (
    ReindexRequestMessage,
)

logger = logging.getLogger(__name__)


class ReindexOrchestratorConsumer(WorkerConsumer[ReindexRequestMessage]):
    """Consumes codeoracle:reindex:requests → drives IndexingService.reindex.

    Reclaim is deliberately DISABLED here (reclaim_min_idle_ms left None): a
    crashed reindex is recovered by IndexingService's own advisory-lock +
    stale-APPLYING sweep, not by re-delivering the request. Re-driving a whole
    reindex via XAUTOCLAIM would only churn — reprocessing re-runs reindex
    against a row the sweep may already have moved to FAILED, raising
    SnapshotConflictError → NACK → reclaim → … → dead-letter after max_attempts
    (bounded, but pure waste). The per-file extractor streams are where reclaim
    earns its keep.
    """

    stream = STREAM_REINDEX_REQUESTS
    group = GROUP_REINDEX_ORCHESTRATOR
    message_type = ReindexRequestMessage
    count_per_poll = (
        1  # single-flight at the lock — no concurrency win from batching
    )
    block_ms = 5000

    def __init__(
        self,
        *,
        queue_client: QueueClientProtocol,
        retry_policy: RetryPolicy,
        consumer_name: str,
        stop_event: asyncio.Event,
        indexing_service: IndexingService,
    ) -> None:
        super().__init__(
            queue_client=queue_client,
            retry_policy=retry_policy,
            consumer_name=consumer_name,
            stop_event=stop_event,
        )
        self._indexing = indexing_service

    async def handle(self, message: ReindexRequestMessage) -> None:
        logger.info(
            "reindex_consumer_picked_up",
            extra={
                "snapshot_sha": message.snapshot_sha,
                "base_sha": message.base_sha,
            },
        )
        await self._indexing.reindex(
            base_sha=message.base_sha,
            pre_allocated_snapshot_sha=message.snapshot_sha,
        )
