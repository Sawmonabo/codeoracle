"""Wiring for the traces ingest worker."""

import asyncio
from collections.abc import Awaitable, Callable

from codeoracle.adapters.traces.tempo_client import TempoClient
from codeoracle.core.config import get_settings
from codeoracle.core.logging import configure_logging, get_logger
from codeoracle.db.repositories.traces import TracesRepository
from codeoracle.db.session import (
    make_async_engine,
    make_async_session_factory,
)
from codeoracle.models.traces import TimestampCursor
from codeoracle.workers._base.cursor_store_pg import PGCursorStore
from codeoracle.workers.traces_ingest.consumer import TracesIngestConsumer

Cleanup = Callable[[], Awaitable[None]]


async def wire_consumer() -> tuple[TracesIngestConsumer, Cleanup]:
    settings = get_settings()
    configure_logging(settings)
    engine = make_async_engine()
    session_factory = make_async_session_factory(engine)
    cursor_store = PGCursorStore(
        session_factory=session_factory,
        cursor_type=TimestampCursor,
    )
    repository = TracesRepository(
        session_factory=session_factory,
        cursor_store=cursor_store,
    )
    tempo = TempoClient(
        base_url=settings.traces.tempo_url,
        timeout_seconds=settings.traces.tempo_timeout_seconds,
    )
    consumer = TracesIngestConsumer(
        tempo_client=tempo,
        traces_repo=repository,
        config=settings.traces,
        cursor_store=cursor_store,
        stop_event=asyncio.Event(),
        logger=get_logger("codeoracle.workers.traces_ingest"),
        engine=engine,
    )

    async def cleanup() -> None:
        await tempo.aclose()
        await engine.dispose()

    return consumer, cleanup
