"""Plan 5 Task 7: SnapshotFileOutcomeRepository integration tests."""

import pytest

from codeoracle.db.repositories.snapshot_file_outcome import (
    SnapshotFileOutcomeRepository,
)

pytestmark = pytest.mark.integration


async def test_upsert_then_count_completed(async_session):
    repo = SnapshotFileOutcomeRepository(async_session)
    for f in ("src/a.py", "src/b.py", "src/c.py"):
        await repo.upsert(
            snapshot_sha="s1",
            file_path=f,
            extractor="joern",
            outcome="success",
        )
    await async_session.commit()
    assert (
        await repo.count_completed(snapshot_sha="s1", extractor="joern") == 3
    )


async def test_upsert_is_idempotent(async_session):
    repo = SnapshotFileOutcomeRepository(async_session)
    for _ in range(3):
        await repo.upsert(
            snapshot_sha="s2",
            file_path="src/x.py",
            extractor="joern",
            outcome="success",
        )
    await async_session.commit()
    # Three upserts → one row
    assert (
        await repo.count_completed(snapshot_sha="s2", extractor="joern") == 1
    )


async def test_count_failed_distinguishes_outcomes(async_session):
    repo = SnapshotFileOutcomeRepository(async_session)
    await repo.upsert(
        snapshot_sha="s3",
        file_path="src/a.py",
        extractor="joern",
        outcome="success",
    )
    await repo.upsert(
        snapshot_sha="s3",
        file_path="src/b.py",
        extractor="joern",
        outcome="failed",
        error="parse error",
    )
    await async_session.commit()
    assert (
        await repo.count_completed(snapshot_sha="s3", extractor="joern") == 2
    )
    assert await repo.count_failed(snapshot_sha="s3", extractor="joern") == 1
