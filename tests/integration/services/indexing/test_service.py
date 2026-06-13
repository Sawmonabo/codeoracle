"""Plan 5 Task 13: IndexingService integration test.

Mocks the orchestrator's worker side — uses a faux fan-out that
immediately populates `snapshot_file_outcomes` so the barrier passes.
Verifies snapshot lifecycle transitions PENDING → APPLYING → APPLIED."""

import os
import subprocess
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from codeoracle.adapters.valkey.client import ValkeyQueueClient
from codeoracle.adapters.valkey.config import ValkeyConfig
from codeoracle.db.repositories.snapshots import SnapshotRepository
from codeoracle.models.snapshot import ExtractorOutcome, SnapshotStatus
from codeoracle.services.indexing.service import IndexingService

pytestmark = [pytest.mark.integration, pytest.mark.worker]


@pytest.fixture
def tmp_git_repo(tmp_path: Path) -> Path:
    subprocess.run(
        ["git", "init"], cwd=tmp_path, check=True, capture_output=True
    )
    subprocess.run(
        ["git", "config", "user.email", "test@example.com"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Test"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
    )
    # Seed with one file
    (tmp_path / "x.py").write_text("def f(): pass")
    subprocess.run(
        ["git", "add", "."], cwd=tmp_path, check=True, capture_output=True
    )
    subprocess.run(
        ["git", "commit", "-m", "initial"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
    )
    return tmp_path


async def test_full_reindex_transitions_to_applied(
    tmp_git_repo,
    async_engine,
    async_session_factory,
    valkey_url,
):
    qc = ValkeyQueueClient(
        ValkeyConfig(
            url=valkey_url,
            pool_min=1,
            pool_max=4,
            block_ms_default=500,
            max_in_flight_per_consumer=10,
            stream_maxlen=1000,
        )
    )
    await qc.start()
    service = IndexingService(
        engine=async_engine,
        db_session_factory=async_session_factory,
        queue_client=qc,
        repo_root=str(tmp_git_repo),
        extractor_kinds=("joern",),
        advisory_lock_timeout_seconds=0.0,
    )

    # Patch the orchestrator's fan-out so it returns success immediately
    # without needing a live worker.
    with patch(
        "codeoracle.services.indexing.service.IndexingOrchestrator.fan_out_and_wait",
        new=AsyncMock(return_value={"joern": "success"}),
    ):
        # Change CWD so DeltaComputer sees the temp git repo
        original_cwd = os.getcwd()
        os.chdir(tmp_git_repo)
        try:
            snap = await service.reindex(base_sha=None)
        finally:
            os.chdir(original_cwd)

    assert snap.status == SnapshotStatus.APPLIED
    assert snap.extractor_results == {"joern": ExtractorOutcome.SUCCESS}
    await qc.aclose()


async def test_in_progress_timeout_transitions_to_failed(
    tmp_git_repo,
    async_engine,
    async_session_factory,
    valkey_url,
):
    qc = ValkeyQueueClient(
        ValkeyConfig(
            url=valkey_url,
            pool_min=1,
            pool_max=4,
            block_ms_default=500,
            max_in_flight_per_consumer=10,
            stream_maxlen=1000,
        )
    )
    await qc.start()
    service = IndexingService(
        engine=async_engine,
        db_session_factory=async_session_factory,
        queue_client=qc,
        repo_root=str(tmp_git_repo),
    )
    with patch(
        "codeoracle.services.indexing.service.IndexingOrchestrator.fan_out_and_wait",
        new=AsyncMock(return_value={"joern": "in_progress"}),
    ):
        original_cwd = os.getcwd()
        os.chdir(tmp_git_repo)
        try:
            snap = await service.reindex(base_sha=None)
        finally:
            os.chdir(original_cwd)

    assert snap.status == SnapshotStatus.FAILED
    assert snap.error == "orchestrator timed out"
    await qc.aclose()


async def test_extractor_crash_transitions_to_failed_durably(
    tmp_git_repo,
    async_engine,
    async_session_factory,
    valkey_url,
):
    """An exception raised mid-fan-out (Joern crash / OOM) is the COMMON
    terminal path. _reindex_locked's catch-all must DURABLY commit the
    FAILED transition before returning (NFR D1-2). The cross-session
    re-read is load-bearing: transition() re-reads via get_by_sha, so a
    SAME-session assertion sees FAILED even when the UPDATE was rolled
    back (read-your-own-writes) — it would pass against the bug. Only a
    fresh connection distinguishes a committed FAILED row from one left
    stuck APPLYING."""
    qc = ValkeyQueueClient(
        ValkeyConfig(
            url=valkey_url,
            pool_min=1,
            pool_max=4,
            block_ms_default=500,
            max_in_flight_per_consumer=10,
            stream_maxlen=1000,
        )
    )
    await qc.start()
    service = IndexingService(
        engine=async_engine,
        db_session_factory=async_session_factory,
        queue_client=qc,
        repo_root=str(tmp_git_repo),
    )
    with patch(
        "codeoracle.services.indexing.service.IndexingOrchestrator.fan_out_and_wait",
        new=AsyncMock(side_effect=RuntimeError("joern OOM")),
    ):
        original_cwd = os.getcwd()
        os.chdir(tmp_git_repo)
        try:
            snap = await service.reindex(base_sha=None)
        finally:
            os.chdir(original_cwd)

    # Caller contract: the catch-all RETURNS the FAILED snapshot (not
    # raise) — the CLI consumes snap.status, mirroring the timeout path.
    assert snap.status == SnapshotStatus.FAILED
    assert "joern OOM" in (snap.error or "")

    # Load-bearing: re-read on a FRESH connection (only committed state is
    # visible). Pre-D1-2-fix this row was APPLYING (FAILED rolled back).
    async with async_session_factory() as session:
        repo = SnapshotRepository(session)
        persisted = await repo.get_by_sha(snap.sha)
    assert persisted is not None
    assert persisted.status == SnapshotStatus.FAILED
    assert "joern OOM" in (persisted.error or "")
    await qc.aclose()


async def test_all_extractors_failed_marks_snapshot_failed(
    tmp_git_repo,
    async_engine,
    async_session_factory,
    valkey_url,
):
    """A barrier that closes with EVERY kind reporting `failed` extracted
    no data — it must transition to FAILED, never APPLIED. Marking it
    APPLIED would serve a contentless snapshot as live; FAILED leaves the
    prior APPLIED snapshot as latest-APPLIED (last-good availability).
    Regression test for the all-failed→APPLIED barrier bug: no kind is
    `in_progress`, so the pre-fix `FAILED if any_in_progress else APPLIED`
    wrongly chose APPLIED."""
    qc = ValkeyQueueClient(
        ValkeyConfig(
            url=valkey_url,
            pool_min=1,
            pool_max=4,
            block_ms_default=500,
            max_in_flight_per_consumer=10,
            stream_maxlen=1000,
        )
    )
    await qc.start()
    service = IndexingService(
        engine=async_engine,
        db_session_factory=async_session_factory,
        queue_client=qc,
        repo_root=str(tmp_git_repo),
        extractor_kinds=("joern",),
    )
    with patch(
        "codeoracle.services.indexing.service.IndexingOrchestrator.fan_out_and_wait",
        new=AsyncMock(return_value={"joern": "failed"}),
    ):
        original_cwd = os.getcwd()
        os.chdir(tmp_git_repo)
        try:
            snap = await service.reindex(base_sha=None)
        finally:
            os.chdir(original_cwd)

    # All-failed barrier → FAILED (NOT APPLIED), with the canonical error.
    assert snap.status == SnapshotStatus.FAILED
    assert "all extractors failed" in (snap.error or "")

    # Durable on a FRESH connection (committed state, not read-your-writes).
    async with async_session_factory() as session:
        repo = SnapshotRepository(session)
        persisted = await repo.get_by_sha(snap.sha)
    assert persisted is not None
    assert persisted.status == SnapshotStatus.FAILED
    await qc.aclose()


async def test_partial_extractor_failure_stays_applied(
    tmp_git_repo,
    async_engine,
    async_session_factory,
    valkey_url,
):
    """The all-failed→FAILED policy TOLERATES partial failure: when at
    least one kind succeeds, the Snapshot is APPLIED even though another
    kind failed (its coverage is simply absent). This is the
    discriminating test for the policy choice — it would FAIL under the
    rejected "any-failed→FAILED" rule, so it pins the decision against a
    silent future tightening back to all-or-nothing."""
    qc = ValkeyQueueClient(
        ValkeyConfig(
            url=valkey_url,
            pool_min=1,
            pool_max=4,
            block_ms_default=500,
            max_in_flight_per_consumer=10,
            stream_maxlen=1000,
        )
    )
    await qc.start()
    service = IndexingService(
        engine=async_engine,
        db_session_factory=async_session_factory,
        queue_client=qc,
        repo_root=str(tmp_git_repo),
        extractor_kinds=("joern", "scip"),
    )
    with patch(
        "codeoracle.services.indexing.service.IndexingOrchestrator.fan_out_and_wait",
        new=AsyncMock(return_value={"joern": "success", "scip": "failed"}),
    ):
        original_cwd = os.getcwd()
        os.chdir(tmp_git_repo)
        try:
            snap = await service.reindex(base_sha=None)
        finally:
            os.chdir(original_cwd)

    # One kind succeeded → APPLIED despite scip's failure (coverage absent),
    # never marked FAILED. error stays None on the APPLIED path.
    assert snap.status == SnapshotStatus.APPLIED
    assert snap.error is None
    await qc.aclose()


async def test_stale_applying_row_swept_before_new_reindex(
    tmp_git_repo,
    async_engine,
    async_session_factory,
    valkey_url,
):
    """A pre-existing APPLYING row (simulating a prior SIGKILL'd
    process) must be transitioned to FAILED before the new reindex
    creates its own snapshot. Verifies the check-on-use cleanup
    inside `_reindex_locked` (DECIDE-D)."""
    # Plant a stale APPLYING row before any reindex runs.
    async with async_session_factory() as session:
        repo = SnapshotRepository(session)
        await repo.create_pending(
            sha="killed_by_sigkill",
            parent_sha=None,
            changed_files=["src/orphan.py"],
        )
        await repo.transition(
            sha="killed_by_sigkill", to=SnapshotStatus.APPLYING
        )
        await session.commit()

    qc = ValkeyQueueClient(
        ValkeyConfig(
            url=valkey_url,
            pool_min=1,
            pool_max=4,
            block_ms_default=500,
            max_in_flight_per_consumer=10,
            stream_maxlen=1000,
        )
    )
    await qc.start()
    service = IndexingService(
        engine=async_engine,
        db_session_factory=async_session_factory,
        queue_client=qc,
        repo_root=str(tmp_git_repo),
        extractor_kinds=("joern",),
        advisory_lock_timeout_seconds=0.0,
    )

    with patch(
        "codeoracle.services.indexing.service.IndexingOrchestrator.fan_out_and_wait",
        new=AsyncMock(return_value={"joern": "success"}),
    ):
        original_cwd = os.getcwd()
        os.chdir(tmp_git_repo)
        try:
            new_snap = await service.reindex(base_sha=None)
        finally:
            os.chdir(original_cwd)

    # The new reindex completed normally.
    assert new_snap.status == SnapshotStatus.APPLIED
    assert new_snap.sha != "killed_by_sigkill"

    # The stale row was transitioned to FAILED with the canonical
    # error string set by mark_stale_applying_failed.
    async with async_session_factory() as session:
        repo = SnapshotRepository(session)
        orphan = await repo.get_by_sha("killed_by_sigkill")

    assert orphan is not None
    assert orphan.status == SnapshotStatus.FAILED
    assert orphan.error == "process died mid-apply"
    await qc.aclose()
