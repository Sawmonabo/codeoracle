"""Plan 7 Task 3 — ContinuousConsumer unit tests.

Uses an in-memory fake source + in-memory fake CursorStore so the tests
exercise only lifecycle (cursor read/advance, batch persist, error
classification, stop_event response, backoff). External dependencies
(httpx, PG) live in Tasks 5/8.
"""

import asyncio
from typing import Any

import pytest
from pydantic import BaseModel

from codeoracle.core.exceptions import (
    TerminalFetchError,
    TransientFetchError,
)
from codeoracle.workers._base.continuous_consumer import (
    ContinuousConsumer,
    ContinuousConsumerRuntime,
    CursorStore,
)

# -----------------------------------------------------------------------------
# Fakes
# -----------------------------------------------------------------------------


class _FakeCursor(BaseModel):
    pos: int


class _FakeStore(CursorStore[_FakeCursor]):
    def __init__(self, initial: _FakeCursor | None = None) -> None:
        self._cursor = initial
        self.update_calls: list[_FakeCursor] = []

    async def get_cursor(self, source: str) -> _FakeCursor | None:
        return self._cursor

    async def update_cursor(self, source: str, cursor: _FakeCursor) -> None:
        self._cursor = cursor
        self.update_calls.append(cursor)


class _RecordingLogger:
    """
    Minimal duck-type stand-in for the `Logger` Protocol in
    continuous_consumer.py.

    Signatures match the Protocol's `(event, *args, **kwargs)` shape so the
    fake satisfies the structural check without casts or suppressions.
    """

    def __init__(self) -> None:
        self.records: list[
            tuple[str, str, tuple[Any, ...], dict[str, Any]]
        ] = []

    def info(self, event: str, *args: Any, **fields: Any) -> None:
        self.records.append(("info", event, args, fields))

    def warning(self, event: str, *args: Any, **fields: Any) -> None:
        self.records.append(("warning", event, args, fields))

    def error(self, event: str, *args: Any, **fields: Any) -> None:
        self.records.append(("error", event, args, fields))

    def debug(self, event: str, *args: Any, **fields: Any) -> None:
        self.records.append(("debug", event, args, fields))


class _RecordingConsumer(ContinuousConsumer[_FakeCursor, list[int]]):
    """Records every fetch + persist call for assertions."""

    def __init__(
        self,
        *,
        batches: list[tuple[_FakeCursor, list[int]] | Exception],
        empty_after_n: int | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs)
        self._batches = batches
        self._batch_idx = 0
        self._empty_after_n = empty_after_n
        self.fetch_calls: list[_FakeCursor | None] = []
        self.persist_calls: list[list[int]] = []

    async def fetch_batch(
        self, cursor: _FakeCursor | None
    ) -> tuple[_FakeCursor, list[int]]:
        self.fetch_calls.append(cursor)
        if (
            self._empty_after_n is not None
            and self._batch_idx >= self._empty_after_n
        ):
            # After N batches, return same cursor + empty list to signal
            # "nothing new"
            return (cursor or _FakeCursor(pos=0), [])
        if self._batch_idx >= len(self._batches):
            # Exhausted — return empty
            return (cursor or _FakeCursor(pos=0), [])
        next_item = self._batches[self._batch_idx]
        self._batch_idx += 1
        if isinstance(next_item, Exception):
            raise next_item
        return next_item

    async def persist(self, batch: list[int]) -> int:
        self.persist_calls.append(batch)
        return len(batch)

    def batch_is_empty(self, batch: list[int]) -> bool:
        return len(batch) == 0

    def cursor_to_unix_nano(self, cursor: _FakeCursor) -> int:
        return cursor.pos


# -----------------------------------------------------------------------------
# Validation / construction
# -----------------------------------------------------------------------------


def test_continuous_consumer_rejects_empty_source_name() -> None:
    with pytest.raises(ValueError, match="source_name must be non-empty"):
        _RecordingConsumer(
            batches=[],
            source_name="",
            cursor_store=_FakeStore(),
            tick_interval_seconds=0.01,
            stop_event=asyncio.Event(),
            logger=_RecordingLogger(),
        )


def test_continuous_consumer_rejects_non_positive_tick_interval() -> None:
    with pytest.raises(ValueError, match="tick_interval_seconds"):
        _RecordingConsumer(
            batches=[],
            source_name="x",
            cursor_store=_FakeStore(),
            tick_interval_seconds=0.0,
            stop_event=asyncio.Event(),
            logger=_RecordingLogger(),
        )


def test_continuous_consumer_rejects_backoff_less_than_tick() -> None:
    with pytest.raises(ValueError, match="max_backoff_seconds"):
        _RecordingConsumer(
            batches=[],
            source_name="x",
            cursor_store=_FakeStore(),
            tick_interval_seconds=1.0,
            stop_event=asyncio.Event(),
            logger=_RecordingLogger(),
            runtime=ContinuousConsumerRuntime(max_backoff_seconds=0.5),
        )


def test_continuous_consumer_rejects_advisory_lock_without_engine() -> None:
    with pytest.raises(
        ValueError,
        match="advisory_lock_id and engine must be provided together",
    ):
        _RecordingConsumer(
            batches=[],
            source_name="x",
            cursor_store=_FakeStore(),
            tick_interval_seconds=0.01,
            stop_event=asyncio.Event(),
            logger=_RecordingLogger(),
            runtime=ContinuousConsumerRuntime(advisory_lock_id=0x123),
        )


# -----------------------------------------------------------------------------
# Happy path
# -----------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_consumer_persists_each_batch_and_advances_cursor() -> None:
    stop = asyncio.Event()
    store = _FakeStore()
    consumer = _RecordingConsumer(
        batches=[
            (_FakeCursor(pos=10), [1, 2, 3]),
            (_FakeCursor(pos=20), [4, 5]),
            (_FakeCursor(pos=30), [6]),
        ],
        empty_after_n=3,
        source_name="fake",
        cursor_store=store,
        tick_interval_seconds=0.001,
        stop_event=stop,
        logger=_RecordingLogger(),
    )

    # Run for a bounded duration then signal stop
    async def signal_stop() -> None:
        await asyncio.sleep(0.05)
        stop.set()

    await asyncio.gather(consumer.run(), signal_stop())

    assert consumer.persist_calls == [[1, 2, 3], [4, 5], [6]]
    assert store.update_calls == [
        _FakeCursor(pos=10),
        _FakeCursor(pos=20),
        _FakeCursor(pos=30),
    ]


@pytest.mark.asyncio
async def test_consumer_skips_empty_batches() -> None:
    stop = asyncio.Event()
    store = _FakeStore(initial=_FakeCursor(pos=5))
    consumer = _RecordingConsumer(
        batches=[],  # exhausted immediately → every fetch returns empty
        source_name="fake",
        cursor_store=store,
        tick_interval_seconds=0.001,
        stop_event=stop,
        logger=_RecordingLogger(),
    )

    async def signal_stop() -> None:
        await asyncio.sleep(0.03)
        stop.set()

    await asyncio.gather(consumer.run(), signal_stop())

    assert consumer.persist_calls == []
    assert store.update_calls == []
    # fetch_batch was called multiple times though
    assert len(consumer.fetch_calls) >= 2


# -----------------------------------------------------------------------------
# Error handling
# -----------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_transient_error_backs_off_and_retries() -> None:
    stop = asyncio.Event()
    logger = _RecordingLogger()
    consumer = _RecordingConsumer(
        batches=[
            TransientFetchError("network blip 1"),
            TransientFetchError("network blip 2"),
            (_FakeCursor(pos=100), [42]),
        ],
        source_name="fake",
        cursor_store=_FakeStore(),
        tick_interval_seconds=0.001,
        stop_event=stop,
        logger=logger,
        runtime=ContinuousConsumerRuntime(
            max_backoff_seconds=0.05
        ),  # allow rapid retries in tests
    )

    async def signal_stop() -> None:
        await asyncio.sleep(0.2)
        stop.set()

    await asyncio.gather(consumer.run(), signal_stop())

    # Both transient errors should have been logged
    transient_log_count = sum(
        1
        for level, event, _args, _fields in logger.records
        if level == "warning"
        and event == "continuous_consumer.tick.transient_error"
    )
    assert transient_log_count == 2

    # And the third tick should have persisted normally
    assert consumer.persist_calls == [[42]]


@pytest.mark.asyncio
async def test_terminal_error_re_raises() -> None:
    stop = asyncio.Event()
    logger = _RecordingLogger()
    consumer = _RecordingConsumer(
        batches=[
            TerminalFetchError("403 forbidden"),
        ],
        source_name="fake",
        cursor_store=_FakeStore(),
        tick_interval_seconds=0.001,
        stop_event=stop,
        logger=logger,
    )

    with pytest.raises(TerminalFetchError, match="403 forbidden"):
        await consumer.run()

    error_logged = any(
        level == "error" and event == "continuous_consumer.tick.terminal_error"
        for level, event, _args, _fields in logger.records
    )
    assert error_logged


@pytest.mark.asyncio
async def test_transient_error_counter_resets_on_success() -> None:
    """Backoff should restart from 1x tick_interval after a successful tick."""
    stop = asyncio.Event()
    logger = _RecordingLogger()
    consumer = _RecordingConsumer(
        batches=[
            TransientFetchError("blip"),
            (_FakeCursor(pos=10), [1]),
            TransientFetchError("blip again"),
            (_FakeCursor(pos=20), [2]),
        ],
        source_name="fake",
        cursor_store=_FakeStore(),
        tick_interval_seconds=0.001,
        stop_event=stop,
        logger=logger,
        runtime=ContinuousConsumerRuntime(max_backoff_seconds=0.05),
    )

    async def signal_stop() -> None:
        await asyncio.sleep(0.2)
        stop.set()

    await asyncio.gather(consumer.run(), signal_stop())

    # Backoff series should have been [tick_interval * 2^0, tick_interval *
    # 2^0]
    # NOT [tick_interval * 2^0, tick_interval * 2^1] — counter must reset.
    backoff_values = [
        fields["backoff_seconds"]
        for level, event, _args, fields in logger.records
        if level == "warning"
        and event == "continuous_consumer.tick.transient_error"
    ]
    assert backoff_values == [0.001, 0.001]


# -----------------------------------------------------------------------------
# Stop semantics
# -----------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_stop_event_during_sleep_exits_promptly() -> None:
    stop = asyncio.Event()
    consumer = _RecordingConsumer(
        batches=[(_FakeCursor(pos=1), [1])],
        empty_after_n=1,
        source_name="fake",
        cursor_store=_FakeStore(),
        # long interval — stop must wake immediately
        tick_interval_seconds=10.0,
        stop_event=stop,
        logger=_RecordingLogger(),
    )

    async def signal_stop() -> None:
        # Give the consumer time to complete one tick then enter sleep
        await asyncio.sleep(0.05)
        stop.set()

    start = asyncio.get_event_loop().time()
    await asyncio.gather(consumer.run(), signal_stop())
    elapsed = asyncio.get_event_loop().time() - start

    # We should NOT have slept the full 10 seconds.
    assert elapsed < 1.0


@pytest.mark.asyncio
async def test_stop_event_set_before_run_exits_immediately() -> None:
    stop = asyncio.Event()
    stop.set()
    consumer = _RecordingConsumer(
        batches=[(_FakeCursor(pos=1), [1])],
        source_name="fake",
        cursor_store=_FakeStore(),
        tick_interval_seconds=1.0,
        stop_event=stop,
        logger=_RecordingLogger(),
    )
    await consumer.run()
    assert consumer.persist_calls == []
