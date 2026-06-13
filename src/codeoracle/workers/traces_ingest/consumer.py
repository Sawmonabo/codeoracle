"""Tempo traces ingest consumer."""

import asyncio
import time
from collections.abc import Callable

from sqlalchemy.ext.asyncio import AsyncEngine

from codeoracle.adapters.traces.tempo_client import (
    TempoClient,
    TempoTerminalError,
    TempoTransientError,
)
from codeoracle.core.config import TracesConfig
from codeoracle.core.exceptions import TerminalFetchError, TransientFetchError
from codeoracle.core.streams import (
    TRACES_INGEST_ADVISORY_LOCK_ID,
    TRACES_INGEST_SOURCE,
)
from codeoracle.db.repositories.traces import TracesRepository
from codeoracle.models.traces import Span, TimestampCursor
from codeoracle.workers._base.continuous_consumer import (
    ContinuousConsumer,
    ContinuousConsumerRuntime,
    CursorStore,
    Logger,
)


def _now_unix_nano() -> int:
    return time.time_ns()


class TracesIngestConsumer(ContinuousConsumer[TimestampCursor, list[Span]]):
    """Continuously ingest trace spans from Tempo."""

    def __init__(
        self,
        *,
        tempo_client: TempoClient,
        traces_repo: TracesRepository,
        config: TracesConfig,
        cursor_store: CursorStore[TimestampCursor],
        stop_event: asyncio.Event,
        logger: Logger,
        engine: AsyncEngine | None,
        clock: Callable[[], int] = _now_unix_nano,
    ) -> None:
        self._tempo = tempo_client
        self._repository = traces_repo
        self._config = config
        self._clock = clock
        super().__init__(
            source_name=TRACES_INGEST_SOURCE,
            cursor_store=cursor_store,
            tick_interval_seconds=config.tick_interval_seconds,
            stop_event=stop_event,
            logger=logger,
            runtime=ContinuousConsumerRuntime(
                max_backoff_seconds=config.max_backoff_seconds,
                advisory_lock_id=(
                    TRACES_INGEST_ADVISORY_LOCK_ID
                    if engine is not None
                    else None
                ),
                engine=engine,
            ),
        )

    async def fetch_batch(
        self, cursor: TimestampCursor | None
    ) -> tuple[TimestampCursor, list[Span]]:
        now = self._clock()
        if cursor is None:
            start = 0
            cursor_before = TimestampCursor(
                last_ingested_unix_nano=0,
                overlap_seconds=self._config.cursor_overlap_seconds,
            )
        else:
            cursor_before = cursor
            start = cursor.fetch_window_lower_bound_nanos()

        try:
            summaries = await self._tempo.search_traces_in_window(
                start_unix_nano=start,
                end_unix_nano=now,
                limit=self._config.search_limit,
            )
        except TempoTransientError as exc:
            raise TransientFetchError(str(exc)) from exc
        except TempoTerminalError as exc:
            raise TerminalFetchError(str(exc)) from exc

        spans: list[Span] = []
        for summary in summaries:
            try:
                spans.extend(
                    await self._tempo.get_trace_by_id(summary.trace_id)
                )
            except TempoTransientError as exc:
                self._logger.warning(
                    "traces_ingest.per_trace_transient_error",
                    trace_id=summary.trace_id.hex(),
                    error=str(exc),
                )
            except TempoTerminalError as exc:
                self._logger.warning(
                    "traces_ingest.per_trace_terminal_error",
                    trace_id=summary.trace_id.hex(),
                    error=str(exc),
                )

        if len(summaries) >= self._config.search_limit and summaries:
            next_nano = max(
                cursor_before.last_ingested_unix_nano,
                *(summary.start_unix_nano for summary in summaries),
            )
        else:
            next_nano = max(cursor_before.last_ingested_unix_nano, now)

        return (
            TimestampCursor(
                last_ingested_unix_nano=next_nano,
                overlap_seconds=self._config.cursor_overlap_seconds,
            ),
            spans,
        )

    async def persist(self, batch: list[Span]) -> int:
        return await self._repository.upsert_spans(batch)

    def batch_is_empty(self, batch: list[Span]) -> bool:
        return len(batch) == 0

    def cursor_to_unix_nano(self, cursor: TimestampCursor) -> int:
        return cursor.last_ingested_unix_nano
