"""Plan 5 Task 13a: ReindexOrchestratorConsumer integration test —
real Valkey + DB + stub IndexingService. Confirms the XADD-from-API →
XREADGROUP-in-worker → handle() wiring round-trips."""

import asyncio
from unittest.mock import AsyncMock

import pytest

from codeoracle.adapters.valkey.client import ValkeyQueueClient
from codeoracle.adapters.valkey.config import ValkeyConfig
from codeoracle.core.streams import STREAM_REINDEX_REQUESTS
from codeoracle.db.session import make_async_engine, make_async_session_factory
from codeoracle.services.indexing.service import IndexingService
from codeoracle.workers._base.message import RetryPolicy
from codeoracle.workers.reindex_orchestrator.consumer import (
    ReindexOrchestratorConsumer,
)
from codeoracle.workers.reindex_orchestrator.message import (
    ReindexRequestMessage,
)

pytestmark = [pytest.mark.integration, pytest.mark.worker, pytest.mark.asyncio]


async def test_xadd_to_stream_routes_through_consumer_handle(valkey_url):
    """Enqueue a ReindexRequestMessage via XADD; verify the consumer
    picks it up and calls IndexingService.reindex with the right args."""
    queue = ValkeyQueueClient(
        ValkeyConfig(
            url=valkey_url,
            pool_min=1,
            pool_max=4,
            block_ms_default=500,
            max_in_flight_per_consumer=10,
            stream_maxlen=1000,
        )
    )
    await queue.start()

    indexing = AsyncMock()
    stop_event = asyncio.Event()
    consumer = ReindexOrchestratorConsumer(
        queue_client=queue,
        retry_policy=RetryPolicy(),
        consumer_name="reindex_orchestrator-integration",
        stop_event=stop_event,
        indexing_service=indexing,
    )

    # Enqueue ONE message
    msg = ReindexRequestMessage(
        batch_id="abc1234",
        snapshot_sha="abc1234",
        base_sha=None,
    )
    await queue.enqueue(STREAM_REINDEX_REQUESTS, msg.model_dump())

    # Run consumer until it processes one message, then stop
    run_task = asyncio.create_task(consumer.run())
    try:
        # Poll up to 5s for the mock to be called
        for _ in range(50):
            if indexing.reindex.await_count >= 1:
                break
            await asyncio.sleep(0.1)
        else:
            pytest.fail("Consumer never invoked IndexingService.reindex")
    finally:
        stop_event.set()
        await run_task

    indexing.reindex.assert_awaited_once_with(
        base_sha=None,
        pre_allocated_snapshot_sha="abc1234",
    )
    await queue.aclose()


async def test_enqueue_reindex_produces_schema_valid_message(
    valkey_url,
    postgres_url,
    tmp_git_repo,
):
    """The dict IndexingService.enqueue_reindex XADDs to
    STREAM_REINDEX_REQUESTS MUST satisfy ReindexRequestMessage's Pydantic
    schema (which inherits batch_id: str from WorkerMessage).

    Catches the BLOCKER class where the producer's dict drops a required
    field and every async reindex fails downstream with ValidationError
    → NACK → DLQ on the consumer side. The consumer-side tests above
    don't catch this because they construct ReindexRequestMessage in
    Python (where Pydantic's required-field default applies on the test
    author's side); this test exercises the WIRE format.
    """
    queue = ValkeyQueueClient(
        ValkeyConfig(
            url=valkey_url,
            pool_min=1,
            pool_max=4,
            block_ms_default=500,
            max_in_flight_per_consumer=10,
            stream_maxlen=1000,
        )
    )
    await queue.start()

    engine = make_async_engine()
    session_factory = make_async_session_factory(engine)
    svc = IndexingService(
        engine=engine,
        db_session_factory=session_factory,
        queue_client=queue,
        repo_root=str(tmp_git_repo),
        extractor_kinds=("joern",),
        advisory_lock_timeout_seconds=5.0,
    )

    snap = await svc.enqueue_reindex(base_sha=None)

    # Read raw payload back via the queue — DO NOT construct the message
    # in Python here, that defeats the test. Pull the JSON envelope from
    # the stream and validate it through Pydantic.
    consumed = await queue.read_pending(
        stream=STREAM_REINDEX_REQUESTS,
        group="reindex_orchestrator",
        consumer="schema_fidelity_probe",
        count=1,
        block_ms=2000,
    )
    assert len(consumed) == 1, "enqueue_reindex did not XADD"
    msg = ReindexRequestMessage.model_validate(
        consumed[0].payload
    )  # MUST NOT raise
    assert msg.batch_id == snap.sha
    assert msg.snapshot_sha == snap.sha
    assert msg.base_sha is None

    await queue.aclose()
