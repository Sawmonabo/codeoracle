"""OpenGrep worker consumer."""

import asyncio

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from codeoracle.adapters.opengrep.runner import OpengrepRunner
from codeoracle.core import streams
from codeoracle.db.repositories.code_references import CodeReferencesRepository
from codeoracle.db.repositories.snapshot_file_outcome import (
    SnapshotFileOutcomeRepository,
)
from codeoracle.workers._base.consumer import (
    QueueClientProtocol,
    WorkerConsumer,
)
from codeoracle.workers._base.message import RetryPolicy
from codeoracle.workers.opengrep_runner.message import OpengrepRunnerMessage
from codeoracle.workers.opengrep_runner.tasks import extract_file_to_references


class OpengrepRunnerConsumer(WorkerConsumer[OpengrepRunnerMessage]):
    stream = streams.STREAM_INDEXING_OPENGREP
    group = streams.GROUP_OPENGREP_RUNNER
    message_type = OpengrepRunnerMessage
    count_per_poll = 5
    block_ms = 5000

    def __init__(
        self,
        *,
        queue_client: QueueClientProtocol,
        retry_policy: RetryPolicy,
        consumer_name: str,
        stop_event: asyncio.Event,
        runner: OpengrepRunner,
        db_session_factory: async_sessionmaker[AsyncSession],
    ) -> None:
        super().__init__(
            queue_client=queue_client,
            retry_policy=retry_policy,
            consumer_name=consumer_name,
            stop_event=stop_event,
        )
        self._runner = runner
        self._db_factory = db_session_factory

    async def handle(self, message: OpengrepRunnerMessage) -> None:
        async with self._db_factory() as session:
            await extract_file_to_references(
                message=message,
                runner=self._runner,
                refs_repo=CodeReferencesRepository(session),
                outcome_repo=SnapshotFileOutcomeRepository(session),
                session=session,
            )
