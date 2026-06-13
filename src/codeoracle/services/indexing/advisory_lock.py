"""pg_advisory_lock-based single-flight primitive.

Spec §8.4: `pg_advisory_lock(0x46AE0001)` (constant lock-id CODEORACLE-REIDX-1)
acquired by both `codeoracle index` and POST /v1/reindex before any extractor
runs; released by `pg_advisory_unlock` in the `finally` block. Cross-process
safe (single Postgres) — second concurrent caller blocks (or fail-fast via
`pg_try_advisory_lock` for the CLI's preferred behavior).

Plan 5 uses pg_try_advisory_lock + a poll loop. Advisory locks are
session-scoped — if the holding connection dies, Postgres releases the
lock automatically. We pin the lock to a dedicated long-lived connection
inside the context manager so it does NOT get returned to the pool
mid-reindex (which would release the lock via connection-close).
"""

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from codeoracle.core.exceptions import AdvisoryLockTimeoutError

logger = logging.getLogger(__name__)

# Spec §8.4: lock id constant — mnemonic CODEORACLE-REIDX-1, hex 0x46AE0001.
REINDEX_LOCK_ID: int = 0x46AE0001


@asynccontextmanager
async def reindex_single_flight(
    engine: AsyncEngine,
    *,
    timeout_seconds: float = 0.0,
    poll_interval_seconds: float = 0.25,
) -> AsyncIterator[None]:
    """Acquire the reindex single-flight lock.

    `timeout_seconds`:
      - 0.0 (default): fail-fast — raise AdvisoryLockTimeoutError on
        the first failed try. Use this from the CLI so users see
        "another reindex is in flight" immediately.
      - > 0: poll for up to N seconds. Use this from the API or worker
        invocations that should serialize gracefully.

    Holds the lock on a DEDICATED connection (acquired via engine.connect())
    so the lock isn't released by pool-checkin during the body.
    """
    conn = await engine.connect()
    try:
        deadline_loops = max(1, int(timeout_seconds / poll_interval_seconds))
        acquired = False
        for _attempt in range(deadline_loops):
            result = await conn.execute(
                text("SELECT pg_try_advisory_lock(:lock_id) AS got"),
                {"lock_id": REINDEX_LOCK_ID},
            )
            if result.scalar_one():
                acquired = True
                break
            if timeout_seconds <= 0:
                # Fail-fast
                break
            await asyncio.sleep(poll_interval_seconds)
        if not acquired:
            raise AdvisoryLockTimeoutError(
                f"pg_try_advisory_lock({REINDEX_LOCK_ID:#x}) failed after "
                f"{timeout_seconds:.2f}s — another reindex is in flight."
            )
        logger.info(
            "reindex_lock_acquired", extra={"lock_id": REINDEX_LOCK_ID}
        )
        try:
            yield
        finally:
            # Explicit release; connection-close also releases but
            # explicit is safer (and lets the connection return to the
            # pool cleanly).
            await conn.execute(
                text("SELECT pg_advisory_unlock(:lock_id)"),
                {"lock_id": REINDEX_LOCK_ID},
            )
            logger.info(
                "reindex_lock_released", extra={"lock_id": REINDEX_LOCK_ID}
            )
    finally:
        await conn.close()
