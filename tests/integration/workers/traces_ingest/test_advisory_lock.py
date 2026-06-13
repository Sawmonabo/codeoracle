"""Plan 7 Task 10 — verify advisory-lock single-instance guard works in PG.

Two TracesIngestConsumer instances trying to acquire the same lock
simultaneously: the second must fail with SingleInstanceContended and
the first must continue running normally."""

import asyncio

import pytest

from codeoracle.adapters.traces.tempo_client import TempoClient
from codeoracle.core.config import TracesConfig
from codeoracle.core.exceptions import SingleInstanceContended
from codeoracle.core.logging import get_logger
from codeoracle.db.repositories.traces import TracesRepository
from codeoracle.models.traces import TimestampCursor
from codeoracle.workers._base.cursor_store_pg import PGCursorStore
from codeoracle.workers.traces_ingest.consumer import TracesIngestConsumer

pytestmark = [pytest.mark.traces, pytest.mark.integration]


@pytest.mark.asyncio
async def test_second_instance_refuses_to_start(
    async_session_factory, async_engine
) -> None:
    cs = PGCursorStore(
        session_factory=async_session_factory, cursor_type=TimestampCursor
    )
    repo = TracesRepository(
        session_factory=async_session_factory, cursor_store=cs
    )
    logger = get_logger("traces_ingest_test")

    # We don't actually want the tempo client to be called — the second
    # instance should fail at lock-acquire, BEFORE entering the loop.
    # Provide a TempoClient pointed at a nonsense URL; if the lock works
    # right, the URL is irrelevant.
    tempo = TempoClient(
        base_url="http://nowhere.invalid:3200", timeout_seconds=1.0
    )
    try:
        cfg = TracesConfig(
            tempo_url="http://nowhere.invalid:3200",
            tick_interval_seconds=0.001,
            max_backoff_seconds=0.05,
        )

        stop_a = asyncio.Event()
        stop_b = asyncio.Event()
        consumer_a = TracesIngestConsumer(
            tempo_client=tempo,
            traces_repo=repo,
            config=cfg,
            cursor_store=cs,
            stop_event=stop_a,
            logger=logger,
            engine=async_engine,
        )
        consumer_b = TracesIngestConsumer(
            tempo_client=tempo,
            traces_repo=repo,
            config=cfg,
            cursor_store=cs,
            stop_event=stop_b,
            logger=logger,
            engine=async_engine,
        )

        # Manually acquire the lock for consumer_a by entering its run() and
        # immediately tripping stop_a so it doesn't actually do work — but
        # the lock is still held during the run() body.
        # The cleanest test pattern: start A, give it time to grab the lock,
        # try to start B, assert B raises.

        task_a = asyncio.create_task(consumer_a.run())
        await asyncio.sleep(0.1)  # let A acquire the lock + enter the loop

        with pytest.raises(SingleInstanceContended):
            await consumer_b.run()

        # Tear down A
        stop_a.set()
        await task_a
    finally:
        await tempo.aclose()
