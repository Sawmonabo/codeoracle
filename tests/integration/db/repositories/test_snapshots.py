"""Plan 5 Task 7: SnapshotRepository integration tests against a real PG."""

import pytest

from codeoracle.core.exceptions import SnapshotConflictError
from codeoracle.db.repositories.snapshots import SnapshotRepository
from codeoracle.models.snapshot import ExtractorOutcome, SnapshotStatus

pytestmark = pytest.mark.integration


async def test_create_pending_returns_snapshot(async_session):
    repo = SnapshotRepository(async_session)
    snap = await repo.create_pending(
        sha="abc1234", parent_sha=None, changed_files=["src/x.py"]
    )
    await async_session.commit()
    assert snap.sha == "abc1234"
    assert snap.status == SnapshotStatus.PENDING
    assert snap.changed_files == ["src/x.py"]


async def test_create_pending_duplicate_sha_raises_conflict(async_session):
    """Duplicate sha (PRIMARY KEY clash) → SnapshotConflictError, NOT a
    raw IntegrityError — the contract enqueue_reindex's idempotent-retry
    branch depends on. The second flush poisons the session (needs
    rollback), exactly as enqueue_reindex handles it before re-reading."""
    repo = SnapshotRepository(async_session)
    await repo.create_pending(sha="dup", parent_sha=None, changed_files=[])
    await async_session.commit()
    with pytest.raises(SnapshotConflictError):
        await repo.create_pending(sha="dup", parent_sha=None, changed_files=[])


async def test_get_by_sha_returns_none_when_missing(async_session):
    repo = SnapshotRepository(async_session)
    assert await repo.get_by_sha("does-not-exist") is None


async def test_pending_to_applying_to_applied_is_legal(async_session):
    repo = SnapshotRepository(async_session)
    await repo.create_pending(sha="s1", parent_sha=None, changed_files=[])
    await repo.transition(sha="s1", to=SnapshotStatus.APPLYING)
    await repo.transition(
        sha="s1",
        to=SnapshotStatus.APPLIED,
        extractor_results={"joern": ExtractorOutcome.SUCCESS},
    )
    await async_session.commit()
    snap = await repo.get_by_sha("s1")
    assert snap is not None
    assert snap.status == SnapshotStatus.APPLIED
    assert snap.extractor_results == {"joern": ExtractorOutcome.SUCCESS}
    assert snap.applied_at is not None


async def test_pending_to_failed_is_legal(async_session):
    repo = SnapshotRepository(async_session)
    await repo.create_pending(sha="s2", parent_sha=None, changed_files=[])
    await repo.transition(
        sha="s2", to=SnapshotStatus.FAILED, error="lock contention"
    )
    snap = await repo.get_by_sha("s2")
    assert snap is not None
    assert snap.status == SnapshotStatus.FAILED
    assert snap.error == "lock contention"


async def test_applied_to_applying_raises_conflict(async_session):
    repo = SnapshotRepository(async_session)
    await repo.create_pending(sha="s3", parent_sha=None, changed_files=[])
    await repo.transition(sha="s3", to=SnapshotStatus.APPLYING)
    await repo.transition(sha="s3", to=SnapshotStatus.APPLIED)
    with pytest.raises(SnapshotConflictError):
        await repo.transition(sha="s3", to=SnapshotStatus.APPLYING)


async def test_unknown_sha_raises_conflict(async_session):
    repo = SnapshotRepository(async_session)
    with pytest.raises(SnapshotConflictError, match="not found"):
        await repo.transition(sha="ghost", to=SnapshotStatus.APPLYING)


async def test_get_latest_applied_returns_most_recent(async_session):
    repo = SnapshotRepository(async_session)
    for sha in ("old", "newer", "newest"):
        await repo.create_pending(sha=sha, parent_sha=None, changed_files=[])
        await repo.transition(sha=sha, to=SnapshotStatus.APPLYING)
        await repo.transition(sha=sha, to=SnapshotStatus.APPLIED)
    latest = await repo.get_latest_applied()
    assert latest is not None
    assert latest.sha == "newest"


async def test_mark_stale_applying_failed_returns_zero_when_no_stale_rows(
    async_session,
):
    """No APPLYING rows in the table → method returns 0 and changes
    nothing. Callers use this to suppress the "swept N stale rows"
    log line on clean startup."""
    repo = SnapshotRepository(async_session)
    await repo.create_pending(sha="clean", parent_sha=None, changed_files=[])
    await async_session.commit()
    assert await repo.mark_stale_applying_failed() == 0
    # Pending row untouched
    snap = await repo.get_by_sha("clean")
    assert snap is not None
    assert snap.status == SnapshotStatus.PENDING


async def test_mark_stale_applying_failed_transitions_all_applying_rows(
    async_session,
):
    """N stale APPLYING rows are bulk-transitioned to FAILED with the
    fixed error string; PENDING/APPLIED/FAILED rows are not touched."""
    repo = SnapshotRepository(async_session)

    # Three stale APPLYING rows
    for sha in ("stale_a", "stale_b", "stale_c"):
        await repo.create_pending(sha=sha, parent_sha=None, changed_files=[])
        await repo.transition(sha=sha, to=SnapshotStatus.APPLYING)

    # One pending row (should not be touched)
    await repo.create_pending(sha="pending", parent_sha=None, changed_files=[])

    # One already-APPLIED row (should not be touched)
    await repo.create_pending(sha="done", parent_sha=None, changed_files=[])
    await repo.transition(sha="done", to=SnapshotStatus.APPLYING)
    await repo.transition(sha="done", to=SnapshotStatus.APPLIED)

    await async_session.commit()

    swept = await repo.mark_stale_applying_failed()
    await async_session.commit()
    assert swept == 3

    # All three stale rows: FAILED + canonical error string
    for sha in ("stale_a", "stale_b", "stale_c"):
        snap = await repo.get_by_sha(sha)
        assert snap is not None
        assert snap.status == SnapshotStatus.FAILED
        assert snap.error == "process died mid-apply"

    # Untouched rows
    pending = await repo.get_by_sha("pending")
    assert pending is not None
    assert pending.status == SnapshotStatus.PENDING
    done = await repo.get_by_sha("done")
    assert done is not None
    assert done.status == SnapshotStatus.APPLIED
