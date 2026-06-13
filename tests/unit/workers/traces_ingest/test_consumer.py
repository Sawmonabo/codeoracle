"""Plan 7 Task 10 — TracesIngestConsumer unit tests.

Uses fakes for TempoClient + TracesRepository + clock so each test
exercises one ingest behavior in isolation.
"""

import asyncio
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from codeoracle.adapters.traces.tempo_client import (
    TempoTerminalError,
    TempoTransientError,
    TraceSummary,
)
from codeoracle.core.config import TracesConfig
from codeoracle.core.exceptions import TerminalFetchError, TransientFetchError
from codeoracle.models.traces import Span, TimestampCursor
from codeoracle.workers._base.continuous_consumer import CursorStore
from codeoracle.workers.traces_ingest.consumer import TracesIngestConsumer

# -----------------------------------------------------------------------------
# Fakes
# -----------------------------------------------------------------------------


class _FakeCursorStore(CursorStore[TimestampCursor]):
    def __init__(self) -> None:
        self.cursor: TimestampCursor | None = None
        self.updates: list[TimestampCursor] = []

    async def get_cursor(self, source: str) -> TimestampCursor | None:
        return self.cursor

    async def update_cursor(
        self, source: str, cursor: TimestampCursor
    ) -> None:
        self.cursor = cursor
        self.updates.append(cursor)


class _RecordingLogger:
    """
    Same shape as Task 3 `_RecordingLogger` — `(event, *args, **kw)` matches
    Protocol.
    """

    def __init__(self) -> None:
        self.records: list[
            tuple[str, str, tuple[Any, ...], dict[str, Any]]
        ] = []

    def info(self, event: str, *args: Any, **kw: Any) -> None:
        self.records.append(("info", event, args, kw))

    def warning(self, event: str, *args: Any, **kw: Any) -> None:
        self.records.append(("warning", event, args, kw))

    def error(self, event: str, *args: Any, **kw: Any) -> None:
        self.records.append(("error", event, args, kw))

    def debug(self, event: str, *args: Any, **kw: Any) -> None:
        self.records.append(("debug", event, args, kw))


def _span(
    trace_id: bytes, span_id: bytes, start: int = 1_700_000_000_000_000_000
) -> Span:
    return Span(
        trace_id=trace_id,
        span_id=span_id,
        parent_span_id=None,
        service_name="api",
        operation_name="op",
        kind="server",
        start_unix_nano=start,
        end_unix_nano=start + 1_000_000,
        status_code="ok",
        status_message=None,
        attributes={},
        scope_name=None,
        scope_version=None,
    )


def _summary(trace_id: bytes, start: int) -> TraceSummary:
    return TraceSummary(
        trace_id=trace_id,
        root_service_name="api",
        root_span_name="op",
        start_unix_nano=start,
        duration_nanos=1_000_000,
    )


def _make_consumer(
    *,
    tempo_client: Any,
    traces_repo: Any,
    cursor_store: CursorStore[TimestampCursor],
    stop_event: asyncio.Event,
    logger: _RecordingLogger,
    clock_value: int = 1_700_000_000_500_000_000,
    config: TracesConfig | None = None,
) -> TracesIngestConsumer:
    cfg = config or TracesConfig(
        tempo_url="http://x:3200",
        tick_interval_seconds=0.001,
        max_backoff_seconds=0.05,
        search_limit=50,
        cursor_overlap_seconds=0,  # 0 to make assertions simpler
    )
    return TracesIngestConsumer(
        tempo_client=tempo_client,
        traces_repo=traces_repo,
        config=cfg,
        cursor_store=cursor_store,
        stop_event=stop_event,
        logger=logger,
        engine=None,  # no advisory lock for unit tests
        clock=lambda: clock_value,
    )


# -----------------------------------------------------------------------------
# Happy-path
# -----------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fetch_then_persist_returns_advanced_cursor() -> None:
    tempo = MagicMock()
    tempo.search_traces_in_window = AsyncMock(
        return_value=[
            _summary(b"\x01" * 16, 1_700_000_000_100_000_000),
            _summary(b"\x02" * 16, 1_700_000_000_200_000_000),
        ]
    )
    tempo.get_trace_by_id = AsyncMock(
        side_effect=[
            [_span(b"\x01" * 16, b"\xaa" * 8)],
            [_span(b"\x02" * 16, b"\xbb" * 8)],
        ]
    )
    repo = MagicMock()
    repo.upsert_spans = AsyncMock(return_value=2)
    consumer = _make_consumer(
        tempo_client=tempo,
        traces_repo=repo,
        cursor_store=_FakeCursorStore(),
        stop_event=asyncio.Event(),
        logger=_RecordingLogger(),
    )

    new_cursor, spans = await consumer.fetch_batch(None)
    assert len(spans) == 2
    # Search-result-count < limit (50), so cursor advances to clock value
    assert new_cursor.last_ingested_unix_nano == 1_700_000_000_500_000_000


@pytest.mark.asyncio
async def test_fetch_window_uses_cursor_minus_overlap() -> None:
    tempo = MagicMock()
    tempo.search_traces_in_window = AsyncMock(return_value=[])
    repo = MagicMock()
    repo.upsert_spans = AsyncMock(return_value=0)

    cfg = TracesConfig(
        tempo_url="http://x:3200",
        tick_interval_seconds=0.001,
        max_backoff_seconds=0.05,
        cursor_overlap_seconds=5,  # 5 seconds = 5_000_000_000 nanos
    )
    consumer = _make_consumer(
        tempo_client=tempo,
        traces_repo=repo,
        cursor_store=_FakeCursorStore(),
        stop_event=asyncio.Event(),
        logger=_RecordingLogger(),
        clock_value=1_700_000_000_500_000_000,
        config=cfg,
    )
    cursor_in = TimestampCursor(
        last_ingested_unix_nano=1_700_000_000_100_000_000, overlap_seconds=5
    )

    await consumer.fetch_batch(cursor_in)

    tempo.search_traces_in_window.assert_awaited_once()
    search_args = tempo.search_traces_in_window.await_args
    assert search_args is not None
    kw = search_args.kwargs
    assert (
        kw["start_unix_nano"] == 1_700_000_000_100_000_000 - 5_000_000_000
    )  # cursor - overlap
    assert kw["end_unix_nano"] == 1_700_000_000_500_000_000  # clock now
    assert kw["limit"] == 50


@pytest.mark.asyncio
async def test_fetch_with_limit_hit_narrows_cursor() -> None:
    """When search returns exactly `limit` results, advance cursor only
    to the latest start_unix_nano in the batch (not to `now`) so we don't
    miss anything in the same nanosecond on the next tick."""
    tempo = MagicMock()
    # Return exactly `limit` summaries — cursor must NOT advance to `now`
    cfg = TracesConfig(
        tempo_url="http://x:3200",
        tick_interval_seconds=0.001,
        max_backoff_seconds=0.05,
        search_limit=3,
        cursor_overlap_seconds=0,
    )
    summaries = [
        _summary(
            bytes([i + 1]) * 16, 1_700_000_000_100_000_000 + i * 1_000_000
        )
        for i in range(3)
    ]
    tempo.search_traces_in_window = AsyncMock(return_value=summaries)
    tempo.get_trace_by_id = AsyncMock(
        side_effect=[
            [_span(bytes([i + 1]) * 16, b"\xaa" * 8)] for i in range(3)
        ]
    )
    repo = MagicMock()
    repo.upsert_spans = AsyncMock(return_value=3)

    consumer = _make_consumer(
        tempo_client=tempo,
        traces_repo=repo,
        cursor_store=_FakeCursorStore(),
        stop_event=asyncio.Event(),
        logger=_RecordingLogger(),
        clock_value=1_700_000_000_999_999_999,
        config=cfg,
    )
    new_cursor, _ = await consumer.fetch_batch(None)

    # last_start was 1_700_000_000_102_000_000 (i=2)
    assert new_cursor.last_ingested_unix_nano == 1_700_000_000_102_000_000


@pytest.mark.asyncio
async def test_limit_hit_cursor_does_not_regress() -> None:
    """Framework monotonicity contract: new_cursor.last_ingested_unix_nano
    MUST be >= incoming cursor's. The regression scenario this test pins:
    a limit-hit tick whose summaries all start BEFORE the incoming cursor
    (because the overlap window pulled them in). Without the clamp in
    fetch_batch, `max(summaries.start)` would land below the cursor and
    next tick's lower bound would widen — fall-behind death spiral."""
    tempo = MagicMock()
    cfg = TracesConfig(
        tempo_url="http://x:3200",
        tick_interval_seconds=0.001,
        max_backoff_seconds=0.05,
        search_limit=3,
        cursor_overlap_seconds=0,
    )
    # Summaries start at 1.7e18 + [0, 1, 2]ms.
    summaries = [
        _summary(
            bytes([i + 1]) * 16, 1_700_000_000_000_000_000 + i * 1_000_000
        )
        for i in range(3)
    ]
    tempo.search_traces_in_window = AsyncMock(return_value=summaries)
    tempo.get_trace_by_id = AsyncMock(
        side_effect=[
            [_span(bytes([i + 1]) * 16, b"\xaa" * 8)] for i in range(3)
        ]
    )
    repo = MagicMock()
    repo.upsert_spans = AsyncMock(return_value=3)

    consumer = _make_consumer(
        tempo_client=tempo,
        traces_repo=repo,
        cursor_store=_FakeCursorStore(),
        stop_event=asyncio.Event(),
        logger=_RecordingLogger(),
        clock_value=1_700_000_001_000_000_000,
        config=cfg,
    )
    # Incoming cursor LAGS the latest summary by intentionally placing
    # it AHEAD of max(summary.start) — simulating an overlap-window re-pull.
    incoming = TimestampCursor(
        last_ingested_unix_nano=1_700_000_000_500_000_000,
        overlap_seconds=0,
    )
    new_cursor, _ = await consumer.fetch_batch(incoming)

    assert (
        new_cursor.last_ingested_unix_nano >= incoming.last_ingested_unix_nano
    ), (
        f"cursor regressed: incoming={incoming.last_ingested_unix_nano}, "
        f"new={new_cursor.last_ingested_unix_nano}"
    )


# -----------------------------------------------------------------------------
# Error mapping: TempoClient → ContinuousConsumer error taxonomy
# -----------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_search_transient_maps_to_consumer_transient() -> None:
    tempo = MagicMock()
    tempo.search_traces_in_window = AsyncMock(
        side_effect=TempoTransientError("503")
    )
    repo = MagicMock()
    consumer = _make_consumer(
        tempo_client=tempo,
        traces_repo=repo,
        cursor_store=_FakeCursorStore(),
        stop_event=asyncio.Event(),
        logger=_RecordingLogger(),
    )
    with pytest.raises(TransientFetchError, match="503"):
        await consumer.fetch_batch(None)


@pytest.mark.asyncio
async def test_search_terminal_maps_to_consumer_terminal() -> None:
    tempo = MagicMock()
    tempo.search_traces_in_window = AsyncMock(
        side_effect=TempoTerminalError("400")
    )
    repo = MagicMock()
    consumer = _make_consumer(
        tempo_client=tempo,
        traces_repo=repo,
        cursor_store=_FakeCursorStore(),
        stop_event=asyncio.Event(),
        logger=_RecordingLogger(),
    )
    with pytest.raises(TerminalFetchError, match="400"):
        await consumer.fetch_batch(None)


@pytest.mark.asyncio
async def test_per_trace_transient_is_swallowed_and_logged() -> None:
    """If get_trace_by_id raises TempoTransientError for one trace, the
    consumer logs + skips that trace and continues — the overlap window
    will re-pull it on the next tick. The fetch_batch call still
    succeeds and persists the other traces."""
    tempo = MagicMock()
    tempo.search_traces_in_window = AsyncMock(
        return_value=[
            _summary(b"\x01" * 16, 1),
            _summary(b"\x02" * 16, 2),
        ]
    )
    tempo.get_trace_by_id = AsyncMock(
        side_effect=[
            TempoTransientError("503 on first trace"),
            [_span(b"\x02" * 16, b"\xbb" * 8)],
        ]
    )
    repo = MagicMock()
    logger = _RecordingLogger()
    consumer = _make_consumer(
        tempo_client=tempo,
        traces_repo=repo,
        cursor_store=_FakeCursorStore(),
        stop_event=asyncio.Event(),
        logger=logger,
    )
    _, spans = await consumer.fetch_batch(None)
    assert len(spans) == 1  # only the second trace's span survived
    # The first trace's failure must be logged
    assert any(
        level == "warning"
        and event == "traces_ingest.per_trace_transient_error"
        for level, event, _args, _fields in logger.records
    )


@pytest.mark.asyncio
async def test_per_trace_terminal_404_is_swallowed_and_logged() -> None:
    """get_trace_by_id 404 means the trace aged out of Tempo between
    search and fetch — log + skip permanently."""
    tempo = MagicMock()
    tempo.search_traces_in_window = AsyncMock(
        return_value=[
            _summary(b"\x01" * 16, 1),
        ]
    )
    tempo.get_trace_by_id = AsyncMock(side_effect=TempoTerminalError("404"))
    repo = MagicMock()
    logger = _RecordingLogger()
    consumer = _make_consumer(
        tempo_client=tempo,
        traces_repo=repo,
        cursor_store=_FakeCursorStore(),
        stop_event=asyncio.Event(),
        logger=logger,
    )
    _, spans = await consumer.fetch_batch(None)
    assert spans == []
    assert any(
        level == "warning"
        and event == "traces_ingest.per_trace_terminal_error"
        for level, event, _args, _fields in logger.records
    )


# -----------------------------------------------------------------------------
# Required overrides
# -----------------------------------------------------------------------------


def test_batch_is_empty() -> None:
    consumer = _make_consumer(
        tempo_client=MagicMock(),
        traces_repo=MagicMock(),
        cursor_store=_FakeCursorStore(),
        stop_event=asyncio.Event(),
        logger=_RecordingLogger(),
    )
    assert consumer.batch_is_empty([])
    assert not consumer.batch_is_empty([_span(b"\x01" * 16, b"\xaa" * 8)])


def test_cursor_to_unix_nano() -> None:
    consumer = _make_consumer(
        tempo_client=MagicMock(),
        traces_repo=MagicMock(),
        cursor_store=_FakeCursorStore(),
        stop_event=asyncio.Event(),
        logger=_RecordingLogger(),
    )
    assert (
        consumer.cursor_to_unix_nano(
            TimestampCursor(last_ingested_unix_nano=42)
        )
        == 42
    )
