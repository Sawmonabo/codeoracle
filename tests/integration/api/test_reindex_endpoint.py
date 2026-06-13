"""§8.5 acceptance gate — /v1/reindex (snapshot lifecycle).

Amended 2026-05-26: the route is async (POST 202 + Snapshot(PENDING);
poll GET /v1/reindex/{sha} until APPLIED). The integration test starts
a ReindexOrchestratorConsumer (Plan 5 Task 13a) in-process as a
background asyncio task — same fixture pattern Plan 5 Task 12 uses for
its joern_indexer integration test.

Spec §8.5 row: BL-RX01 (D4-audit-assigned) — amended to "POST /v1/reindex
returns 202 + PENDING Snapshot; polling GET /v1/reindex/{sha} transitions
to APPLIED within the cold-3K-LOC budget (3 min p95)." Idempotent retry
also covered.
"""

import asyncio
import time
from collections.abc import AsyncIterator

import httpx
import pytest
import pytest_asyncio

from codeoracle.api.dependencies import get_indexing_service
from codeoracle.models.snapshot import Snapshot, SnapshotStatus
from codeoracle.services.indexing.service import IndexingService
from codeoracle.workers._base.message import RetryPolicy
from codeoracle.workers.reindex_orchestrator.consumer import (
    ReindexOrchestratorConsumer,
)

_POLL_INTERVAL_SECONDS = 0.5
_POLL_BUDGET_SECONDS = 180.0


@pytest_asyncio.fixture
async def shared_indexing_service(
    api_app,
    tmp_git_repo,
) -> AsyncIterator[IndexingService]:
    """Build a single IndexingService rooted at tmp_git_repo and install
    it as the API route's dependency_override. The SAME instance is
    consumed by the ReindexOrchestratorConsumer fixture below — both
    share the engine, queue_client, and repo_root so the producer's
    XADD is visible to the consumer's XREADGROUP.

    `tmp_git_repo` is Plan 5's session-scoped fixture (declared in
    `tests/integration/conftest.py` per Plan 5 Task 12 precedent);
    pytest auto-discovers it from `tests/integration/api/`.

    Override is cleared in teardown. Async fixture for consistency with
    `api_app` + `api_settings` (other async fixtures in conftest); makes
    it forward-compatible if setup or teardown ever needs to await.
    """
    resources = api_app.state.resources
    settings = resources.settings
    svc = IndexingService(
        engine=resources.engine,
        db_session_factory=resources.session_factory,
        queue_client=resources.valkey_client,
        repo_root=str(tmp_git_repo),
        extractor_kinds=("joern",),
        advisory_lock_timeout_seconds=settings.api.lock_acquisition_timeout_seconds,
    )
    api_app.dependency_overrides[get_indexing_service] = lambda: svc
    yield svc
    api_app.dependency_overrides.pop(get_indexing_service, None)


@pytest_asyncio.fixture
async def reindex_consumer(
    api_app,
    shared_indexing_service,
    tmp_git_repo,
    monkeypatch,
):
    """Start a ReindexOrchestratorConsumer in-process for the duration
    of the test. The consumer shares the api_app's queue_client and the
    shared_indexing_service — so a POST /v1/reindex from the test client
    XADDs a message the consumer XREADGROUPs on its next poll.

    `RetryPolicy()` constructs zero-arg because Plan 5 Task 3
    (`src/codeoracle/workers/_base/message.py`) declares it as
    `@dataclass(frozen=True)` with its single field defaulted
    (`max_attempts=5`); if that default is removed later, this test
    will fail at construction with a clear TypeError, which is the right
    surface for the contract change.

    Teardown signals stop_event and awaits the consumer task to drain.
    """
    monkeypatch.chdir(tmp_git_repo)
    stop_event = asyncio.Event()
    consumer = ReindexOrchestratorConsumer(
        queue_client=api_app.state.resources.valkey_client,
        retry_policy=RetryPolicy(),
        consumer_name="reindex-orchestrator-test",
        stop_event=stop_event,
        indexing_service=shared_indexing_service,
    )
    consumer_task = asyncio.create_task(consumer.run())
    try:
        yield consumer
    finally:
        stop_event.set()
        try:
            await asyncio.wait_for(consumer_task, timeout=5.0)
        except TimeoutError:
            consumer_task.cancel()


async def _drive_reindex_to_applied(
    api_client: httpx.AsyncClient,
    *,
    base_sha: str | None,
) -> Snapshot:
    """POST /v1/reindex then poll GET until status terminal."""
    post = await api_client.post("/v1/reindex", json={"base_sha": base_sha})
    assert post.status_code == 202
    enqueued = Snapshot.model_validate(post.json())
    assert enqueued.status in {SnapshotStatus.PENDING, SnapshotStatus.APPLYING}
    snapshot_sha = enqueued.sha

    deadline = time.monotonic() + _POLL_BUDGET_SECONDS
    while time.monotonic() < deadline:
        get_response = await api_client.get(f"/v1/reindex/{snapshot_sha}")
        assert get_response.status_code == 200
        current = Snapshot.model_validate(get_response.json())
        if current.status in {SnapshotStatus.APPLIED, SnapshotStatus.FAILED}:
            return current
        await asyncio.sleep(_POLL_INTERVAL_SECONDS)
    raise AssertionError(
        f"Reindex {snapshot_sha!r} did not converge within "
        f"{_POLL_BUDGET_SECONDS}s"
    )


@pytest.mark.integration
async def test_reindex_endpoint_enqueues_and_polls_to_applied(
    api_app,
    tmp_git_repo,
    reindex_consumer,
):
    """POST returns 202 + PENDING Snapshot; polling reaches APPLIED.

    `tmp_git_repo` is Plan 5's fixture — a tiny git repo with files Joern
    can parse. The `reindex_consumer` fixture starts the worker in-process.
    """
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=api_app),
        base_url="http://test",
    ) as c:
        final = await _drive_reindex_to_applied(c, base_sha=None)

    assert final.status == SnapshotStatus.APPLIED, (
        f"Expected APPLIED, got {final.status!r} with error={final.error!r}"
    )
    # Spec §8.5 contract: response.sha is the new snapshot.
    assert final.sha
    assert final.error is None


@pytest.mark.integration
async def test_reindex_endpoint_idempotent_on_duplicate_post(
    api_app,
    tmp_git_repo,
    shared_indexing_service,
):
    """Two POSTs with the same tree state return the same snapshot_sha.
    Does NOT require a running consumer — idempotence is producer-side
    (Plan 5 IndexingService.enqueue_reindex catches SnapshotConflictError
    and returns the existing PENDING row).
    """
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=api_app),
        base_url="http://test",
    ) as c:
        r1 = await c.post("/v1/reindex", json={"base_sha": None})
        r2 = await c.post("/v1/reindex", json={"base_sha": None})
    assert r1.status_code == 202
    assert r2.status_code == 202
    assert r1.json()["sha"] == r2.json()["sha"]
