"""Plan 5 Task 9: pg_advisory_lock-based single-flight tests.

Plan 5 verifies the primitive against a real Postgres. Plan 6 adds a
contention test that fires two extractors concurrently and asserts they
serialize."""

import asyncio

import pytest
from sqlalchemy import text

from codeoracle.core.exceptions import AdvisoryLockTimeoutError
from codeoracle.services.indexing.advisory_lock import (
    REINDEX_LOCK_ID,
    reindex_single_flight,
)

pytestmark = pytest.mark.integration


async def test_single_acquirer_succeeds(async_engine):
    async with (
        reindex_single_flight(async_engine, timeout_seconds=0.0),
        async_engine.connect() as conn2,
    ):
        # We're inside the lock — sanity-check by trying a non-blocking
        # try on the SAME engine (different connection). Should fail.
        r = await conn2.execute(
            text("SELECT pg_try_advisory_lock(:l)"),
            {"l": REINDEX_LOCK_ID},
        )
        got = r.scalar_one()
        if got:
            # Postgres considers same-session acquires reentrant;
            # ensure release.
            await conn2.execute(
                text("SELECT pg_advisory_unlock(:l)"),
                {"l": REINDEX_LOCK_ID},
            )
        # Plan 5's primitive holds on a dedicated connection, so this
        # check should report False from any OTHER connection. The
        # assertion enforces that property of `reindex_single_flight`.
        assert got is False, (
            "Advisory lock not isolated to its own connection — "
            "another connection was able to acquire it concurrently."
        )


async def test_fail_fast_when_held(async_engine):
    """timeout_seconds=0 raises immediately if the lock is held."""
    async with reindex_single_flight(async_engine, timeout_seconds=0.0):
        with pytest.raises(AdvisoryLockTimeoutError, match="another reindex"):
            async with reindex_single_flight(
                async_engine, timeout_seconds=0.0
            ):
                pass


async def test_timeout_waits_then_acquires(async_engine):
    """timeout_seconds>0 polls until the lock frees."""
    holder_done = asyncio.Event()

    async def hold_briefly():
        async with reindex_single_flight(async_engine, timeout_seconds=0.0):
            await asyncio.sleep(0.3)
        holder_done.set()

    holder = asyncio.create_task(hold_briefly())
    await asyncio.sleep(0.05)  # ensure holder has the lock

    # Waiter polls up to 2s; should succeed before holder finishes
    async with reindex_single_flight(async_engine, timeout_seconds=2.0):
        pass

    await holder
    assert holder_done.is_set()


async def test_lock_released_on_exception_inside_block(async_engine):
    """If the body raises, the finally clause must release the lock."""
    with pytest.raises(ValueError, match="boom"):
        async with reindex_single_flight(async_engine, timeout_seconds=0.0):
            raise ValueError("boom")
    # Subsequent acquirer must not hit AdvisoryLockTimeoutError
    async with reindex_single_flight(async_engine, timeout_seconds=0.0):
        pass
