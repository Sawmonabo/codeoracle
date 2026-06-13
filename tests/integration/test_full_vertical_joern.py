"""Plan 5 Task 15: full vertical Joern indexing integration test.

This test proves the WorkerConsumer framework works end-to-end:
  - JoernIndexerConsumer running in a background task,
  - IndexingService running `reindex(base_sha=None)`,
  - Snapshot lifecycle PENDING → APPLYING → APPLIED,
  - CALLS edge from pkg/b.py::main to pkg/a.py::greet materializes in AGE.

Joern is run for real — joern-cli must be on PATH in the test environment.
If joern isn't installed, the test SKIPS gracefully (caught by Step 1
of the test body, not via a pytest skip — we want CI's `worker` job to
own the install).
"""

import asyncio
import shutil
from pathlib import Path

import pytest

from codeoracle.adapters.joern.client import JoernClient
from codeoracle.adapters.joern.config import JoernConfig
from codeoracle.adapters.joern.parser import JoernCpgParser
from codeoracle.adapters.valkey.client import ValkeyQueueClient
from codeoracle.adapters.valkey.config import ValkeyConfig
from codeoracle.db.repositories.graph import GraphRepository
from codeoracle.models.snapshot import SnapshotStatus
from codeoracle.services.indexing.service import IndexingService
from codeoracle.workers._base.message import RetryPolicy
from codeoracle.workers.joern_indexer.consumer import JoernIndexerConsumer

pytestmark = [pytest.mark.integration, pytest.mark.worker]


FIXTURE = Path(__file__).parent.parent / "fixtures/sample_repo"


async def test_full_vertical_indexes_calls_edge(
    async_engine,
    async_session_factory,
    async_session,
    valkey_url,
    tmp_path,
    monkeypatch,
):
    """End-to-end: codeoracle index → CALLS edge materializes in AGE."""
    if shutil.which("joern-parse") is None:
        pytest.skip(
            "joern-parse not on PATH — install joern-cli locally "
            "(https://docs.joern.io/installation) or run in the worker-CI "
            "image once Plan 1A bakes it (the joern Dockerfile is in "
            "Plan 1A's image-build matrix, not Plan 5)."
        )

    # 1) Stage queue + worker
    qc = ValkeyQueueClient(
        ValkeyConfig(
            url=valkey_url,
            pool_min=1,
            pool_max=4,
            block_ms_default=500,
            max_in_flight_per_consumer=5,
            stream_maxlen=1000,
        )
    )
    await qc.start()

    joern_config = JoernConfig(
        joern_parse_bin="joern-parse",
        joern_export_bin="joern-export",
        parse_timeout_seconds=120,
        export_timeout_seconds=120,
        work_dir=str(tmp_path / "joern-work"),
    )
    consumer = JoernIndexerConsumer(
        queue_client=qc,
        retry_policy=RetryPolicy(),
        consumer_name="test_worker",
        stop_event=asyncio.Event(),
        joern_client=JoernClient(joern_config),
        parser=JoernCpgParser(),
        db_session_factory=async_session_factory,
    )
    worker_task = asyncio.create_task(consumer.run())

    # 2) Run reindex over the fixture repo
    monkeypatch.chdir(FIXTURE)
    service = IndexingService(
        engine=async_engine,
        db_session_factory=async_session_factory,
        queue_client=qc,
        repo_root=str(FIXTURE),
        extractor_kinds=("joern",),
        advisory_lock_timeout_seconds=0.0,
    )
    snap = await service.reindex(base_sha=None)

    # 3) Assert lifecycle
    assert snap.status == SnapshotStatus.APPLIED, (
        f"snapshot.status={snap.status.value}, error={snap.error}"
    )

    # 4) Assert the CALLS edge materialized
    graph = GraphRepository(async_session)
    rows = await graph.execute_cypher(
        "MATCH (caller:Symbol)-[:CALLS]->(callee:Symbol) "
        "WHERE caller.snapshot_sha = $sha AND callee.snapshot_sha = $sha "
        "RETURN caller.fqn AS c, callee.fqn AS t",
        {"sha": snap.sha},
        column_spec="c agtype, t agtype",
    )
    callers = [(str(r["c"]).strip('"'), str(r["t"]).strip('"')) for r in rows]
    # We expect at least one CALLS edge from b.py::main → a.py::greet
    assert any("main" in c and "greet" in t for c, t in callers), (
        f"No CALLS edge found from main to greet. rows={callers}"
    )

    # 5) Tear down
    consumer._stop.set()
    await worker_task
    await qc.aclose()
