"""Plan 5 Task 13: IndexingOrchestrator integration tests."""

import pytest

from codeoracle.adapters.valkey.client import ValkeyQueueClient
from codeoracle.adapters.valkey.config import ValkeyConfig
from codeoracle.db.repositories.snapshot_file_outcome import (
    SnapshotFileOutcomeRepository,
)
from codeoracle.services.indexing.orchestrator import IndexingOrchestrator

pytestmark = [pytest.mark.integration, pytest.mark.worker]


async def test_fan_out_enqueues_one_message_per_file(
    async_session,
    async_session_factory,
    valkey_url,
):
    queue = ValkeyQueueClient(
        ValkeyConfig(
            url=valkey_url,
            pool_min=1,
            pool_max=4,
            block_ms_default=1000,
            max_in_flight_per_consumer=10,
            stream_maxlen=1000,
        )
    )
    await queue.start()

    file_outcomes_repo = SnapshotFileOutcomeRepository(async_session)
    orch = IndexingOrchestrator(
        queue_client=queue,
        db_session_factory=async_session_factory,
        extractor_kinds=("joern",),
        completion_timeout_seconds=2.0,  # short — we won't actually drain
    )

    # Pre-populate the snapshot_file_outcomes table so the barrier
    # passes immediately (no real worker running).
    files = ["src/a.py", "src/b.py", "src/c.py"]
    for f in files:
        await file_outcomes_repo.upsert(
            snapshot_sha="x",
            file_path=f,
            extractor="joern",
            outcome="success",
        )
    await async_session.commit()

    outcomes = await orch.fan_out_and_wait(
        snapshot_sha="x",
        repo_root="/tmp",
        files=files,
    )
    assert outcomes == {"joern": "success"}

    # Verify the messages were enqueued
    msgs = await queue.read_pending(
        stream="indexing.joern",
        group="g_test",
        consumer="c",
        count=10,
        block_ms=500,
    )
    assert len(msgs) == 3
    assert {m.payload["file_path"] for m in msgs} == set(files)
    await queue.aclose()


async def test_timeout_with_no_acks_reports_in_progress(
    async_session_factory,
    valkey_url,
):
    queue = ValkeyQueueClient(
        ValkeyConfig(
            url=valkey_url,
            pool_min=1,
            pool_max=4,
            block_ms_default=1000,
            max_in_flight_per_consumer=10,
            stream_maxlen=1000,
        )
    )
    await queue.start()

    orch = IndexingOrchestrator(
        queue_client=queue,
        db_session_factory=async_session_factory,
        extractor_kinds=("joern",),
        completion_poll_interval_seconds=0.1,
        completion_timeout_seconds=0.5,
    )

    outcomes = await orch.fan_out_and_wait(
        snapshot_sha="y",
        repo_root="/tmp",
        files=["a.py", "b.py"],
    )
    assert outcomes["joern"] == "in_progress"
    await queue.aclose()


async def test_partial_failure_classified(
    async_session,
    async_session_factory,
    valkey_url,
):
    queue = ValkeyQueueClient(
        ValkeyConfig(
            url=valkey_url,
            pool_min=1,
            pool_max=4,
            block_ms_default=1000,
            max_in_flight_per_consumer=10,
            stream_maxlen=1000,
        )
    )
    await queue.start()
    file_outcomes_repo = SnapshotFileOutcomeRepository(async_session)

    # 2 success, 1 failed
    for f, outcome in [
        ("a.py", "success"),
        ("b.py", "success"),
        ("c.py", "failed"),
    ]:
        await file_outcomes_repo.upsert(
            snapshot_sha="z",
            file_path=f,
            extractor="joern",
            outcome=outcome,
        )
    await async_session.commit()

    orch = IndexingOrchestrator(
        queue_client=queue,
        db_session_factory=async_session_factory,
        extractor_kinds=("joern",),
    )
    outcomes = await orch.fan_out_and_wait(
        snapshot_sha="z",
        repo_root="/tmp",
        files=["a.py", "b.py", "c.py"],
    )
    assert outcomes == {"joern": "partial"}
    await queue.aclose()
