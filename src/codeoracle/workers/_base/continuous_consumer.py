"""Plan 7 — ContinuousConsumer ABC + CursorStore ABC.

ContinuousConsumer is a SIBLING of Plan 5's WorkerConsumer, NOT a
subclass. WorkerConsumer is per-message ACK/NACK against Valkey
streams; ContinuousConsumer is cursor-based polling against an
external source with no message-level semantics.

The two share nothing structurally — different lifecycle, different
failure mode, different concurrency story. They sit in the same
`workers/_base/` package because they're both worker-framework code,
but no inheritance chain ties them.
"""

import asyncio
import time
from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable, Sized
from contextlib import suppress
from dataclasses import dataclass
from typing import Any, Protocol, cast

from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from codeoracle.core.exceptions import (
    SingleInstanceContended,
    TerminalFetchError,
    TransientFetchError,
)
from codeoracle.models.traces import IngestStats


class Logger(Protocol):
    """Structural type for the logger the consumer receives.

    Why a Protocol (not the concrete `structlog.stdlib.BoundLogger`)?
    Tests substitute a `_RecordingLogger` that captures emitted events
    without needing to mock the whole structlog wrapping pipeline.
    The Protocol lets ty validate the duck-type at the call sites
    without dragging structlog into the framework module.

    Both `structlog.stdlib.BoundLogger` (the production type returned
    by `codeoracle.core.logging.get_logger`) and any plain fake satisfy
    this Protocol.

    Exported (no leading underscore) because subclass-consumer modules
    (e.g., `workers/traces_ingest/consumer.py`) import it for their
    own constructor type-hints.

    Signature shape: `(event: str, *args: Any, **kwargs: Any) -> None`.
    `structlog.stdlib.BoundLogger`'s methods take `(event, *args, **kw)`
    where positional args feed into stdlib `%`-formatting on the
    `event` template. Protocol matching in `ty` strict-mode validates
    parameter shape positionally, so the framework's Protocol MUST
    include `*args: Any` to satisfy the structural check against
    `BoundLogger`. If `ty check` rejects this Protocol at the
    `codeoracle.core.logging.get_logger` call site in `wire_consumer`,
    the implementer's first move is to verify `BoundLogger`'s ty-
    discoverable signature with `uv run ty inspect codeoracle.core.logging`
    and widen this Protocol further (e.g., add a `bind` method) before
    introducing casts or static-analysis suppressions.
    """

    def info(self, event: str, *args: Any, **kwargs: Any) -> None: ...
    def warning(self, event: str, *args: Any, **kwargs: Any) -> None: ...
    def error(self, event: str, *args: Any, **kwargs: Any) -> None: ...
    def debug(self, event: str, *args: Any, **kwargs: Any) -> None: ...


# =============================================================================
# Exceptions
# =============================================================================
# The ContinuousConsumer exception family (ContinuousConsumerError,
# TransientFetchError, TerminalFetchError, SingleInstanceContended) lives in
# core/exceptions.py under CodeOracleError — see Task 1 Step 4. It is imported
# at
# the top of this module so run() can raise/catch it; this module does NOT
# define it (spec §3.2 + Plan 5's WorkerError-family placement).


# =============================================================================
# CursorStore ABC
# =============================================================================


class CursorStore[TCursor: BaseModel](ABC):
    """Persistence interface for ContinuousConsumer cursors.

    Implementations:
      - PGCursorStore (Plan 7 Task 4) — JSONB blob in traces.ingest_cursor
      - InMemoryCursorStore (test utility) — dict-backed for unit tests

    The cursor type TCursor is a Pydantic BaseModel — implementations
    are responsible for serializing/deserializing it (e.g., via
    model_dump_json + model_validate_json). The TCursor type is
    captured at __init__ time (not on each call) so the impl can
    validate the on-disk blob against the expected shape.
    """

    @abstractmethod
    async def get_cursor(self, source: str) -> TCursor | None:
        """Return the cursor for `source`, or None if never set."""

    @abstractmethod
    async def update_cursor(self, source: str, cursor: TCursor) -> None:
        """Persist `cursor` for `source`.

        Atomic upsert avoids read-modify-write races.
        """


@dataclass(frozen=True, slots=True)
class ContinuousConsumerRuntime:
    """Runtime knobs for ContinuousConsumer construction."""

    sleep_fn: Callable[[float], Awaitable[None]] = asyncio.sleep
    time_fn: Callable[[], float] = time.monotonic
    max_backoff_seconds: float = 60.0
    advisory_lock_id: int | None = None
    engine: AsyncEngine | None = None


# =============================================================================
# ContinuousConsumer ABC
# =============================================================================


class ContinuousConsumer[TCursor: BaseModel, TBatch](ABC):
    """Cursor-loop consumer over an external source.

    Lifecycle:
      1. (optional) Acquire advisory lock → SingleInstanceContended if held.
      2. Loop: a. cursor = await cursor_store.get_cursor(source_name)
         b. new_cursor, batch = await fetch_batch(cursor)
         c. if batch_is_empty(batch): sleep, continue.
         d. persisted = await persist(batch)
         e. await cursor_store.update_cursor(source_name, new_cursor)
         f. emit IngestStats; sleep tick_interval.
      3. On stop_event: exit cleanly between ticks (NEVER mid-batch).

    Error handling: TransientFetchError → backoff + retry;
    TerminalFetchError → re-raise (worker dies). All other exceptions
    propagate (orchestrator restarts).
    """

    def __init__(
        self,
        *,
        source_name: str,
        cursor_store: CursorStore[TCursor],
        tick_interval_seconds: float,
        stop_event: asyncio.Event,
        logger: Logger,
        runtime: ContinuousConsumerRuntime | None = None,
    ) -> None:
        runtime = runtime or ContinuousConsumerRuntime()
        if not source_name:
            raise ValueError("source_name must be non-empty")
        if tick_interval_seconds <= 0:
            raise ValueError(
                "tick_interval_seconds must be > 0, got "
                f"{tick_interval_seconds}"
            )
        if runtime.max_backoff_seconds < tick_interval_seconds:
            raise ValueError(
                f"max_backoff_seconds ({runtime.max_backoff_seconds}) "
                "must be >= "
                f"tick_interval_seconds ({tick_interval_seconds})"
            )
        if (runtime.advisory_lock_id is None) != (runtime.engine is None):
            raise ValueError(
                "advisory_lock_id and engine must be provided together "
                "(both None → no single-instance guard; both set → "
                "guard active)"
            )

        self._source_name = source_name
        self._cursor_store = cursor_store
        self._tick_interval = tick_interval_seconds
        self._stop_event = stop_event
        self._logger = logger
        self._sleep = runtime.sleep_fn
        self._monotonic = runtime.time_fn
        self._max_backoff = runtime.max_backoff_seconds
        self._lock_id = runtime.advisory_lock_id
        self._engine = runtime.engine

    # ----- Public API -----

    def stop(self) -> None:
        """Request clean loop shutdown between ticks."""
        self._stop_event.set()

    async def run(self) -> None:
        """Run the cursor loop until stop_event is set."""
        if self._lock_id is not None and self._engine is not None:
            async with self._engine.connect() as lock_conn:
                result = await lock_conn.execute(
                    text("SELECT pg_try_advisory_lock(:id)"),
                    {"id": self._lock_id},
                )
                if not result.scalar_one():
                    raise SingleInstanceContended(
                        f"advisory lock {self._lock_id:#x} for "
                        f"source={self._source_name!r} "
                        "is held by another worker — refusing to start"
                    )
                self._logger.info(
                    "continuous_consumer.lock_acquired",
                    source=self._source_name,
                    lock_id=f"{self._lock_id:#x}",
                )
                try:
                    await self._run_with_top_level_error_log()
                finally:
                    await lock_conn.execute(
                        text("SELECT pg_advisory_unlock(:id)"),
                        {"id": self._lock_id},
                    )
                    self._logger.info(
                        "continuous_consumer.lock_released",
                        source=self._source_name,
                        lock_id=f"{self._lock_id:#x}",
                    )
        else:
            await self._run_with_top_level_error_log()

    async def _run_with_top_level_error_log(self) -> None:
        """Wraps `_run_loop()` so any uncaught exception (i.e., anything other
        than the per-tick TransientFetchError / TerminalFetchError the loop
        already handles) emits exactly one structured error line before
        propagating. Without this, exceptions from CursorStore I/O failures
        or unexpected adapter errors would still abort the worker but the
        operator would only see the bare traceback in stderr — no structured
        log line tying the failure to the source / consumer identity. The
        re-raise preserves the current control flow (the process exits non-
        zero so the orchestrator restarts it)."""
        try:
            await self._run_loop()
        except TransientFetchError, TerminalFetchError:
            # Already classified + logged by _run_loop; let it propagate
            # untouched.
            raise
        except Exception:
            self._logger.error(
                "continuous_consumer.unexpected_error",
                source=self._source_name,
                exc_info=True,
            )
            raise

    # ----- Subclass surface -----

    @abstractmethod
    async def fetch_batch(
        self, cursor: TCursor | None
    ) -> tuple[TCursor, TBatch]:
        """Fetch the next batch from the external source.

        Returns (new_cursor, batch). The new_cursor MUST be monotonically
        non-decreasing — if the source has nothing new, return the input
        cursor unchanged and an empty batch (the loop will sleep + retry).

        On transient error (network, 5xx, timeout): raise TransientFetchError.
        On terminal error: raise TerminalFetchError.
        """

    @abstractmethod
    async def persist(self, batch: TBatch) -> int:
        """Idempotently persist the batch. Returns count of new items written
        (after dedup at the storage layer — e.g., ON CONFLICT DO NOTHING)."""

    @abstractmethod
    def batch_is_empty(self, batch: TBatch) -> bool:
        """Return True iff batch has no items to persist."""

    @abstractmethod
    def cursor_to_unix_nano(self, cursor: TCursor) -> int:
        """Project the cursor onto a Unix-nano timestamp for IngestStats.

        Most cursors are timestamp-based and this is trivial; opaque
        cursors (e.g., paging tokens) should return 0 here and rely on
        log-line counts for telemetry.
        """

    # ----- Internal -----

    async def _run_loop(self) -> None:
        consecutive_transient_errors = 0
        while not self._stop_event.is_set():
            tick_start = self._monotonic()
            try:
                cursor_before = await self._cursor_store.get_cursor(
                    self._source_name
                )
                new_cursor, batch = await self.fetch_batch(cursor_before)

                if self.batch_is_empty(batch):
                    self._logger.debug(
                        "continuous_consumer.tick.empty",
                        source=self._source_name,
                    )
                    consecutive_transient_errors = 0
                    await self._sleep_or_exit(self._tick_interval)
                    continue

                # fetch_batch may have returned items WITHOUT advancing the
                # cursor (a paging-token source could re-emit on retry).
                # We persist + advance even if cursor_before == new_cursor;
                # idempotency at the storage layer absorbs the duplicate.
                spans_fetched = self._batch_size(batch)
                persisted = await self.persist(batch)
                await self._cursor_store.update_cursor(
                    self._source_name, new_cursor
                )

                tick_elapsed = self._monotonic() - tick_start
                stats = IngestStats(
                    source=self._source_name,
                    cursor_before_unix_nano=self.cursor_to_unix_nano(
                        cursor_before
                    )
                    if cursor_before is not None
                    else 0,
                    cursor_after_unix_nano=self.cursor_to_unix_nano(
                        new_cursor
                    ),
                    spans_fetched=spans_fetched,
                    spans_persisted=persisted,
                    elapsed_seconds=tick_elapsed,
                )
                self._logger.info(
                    "continuous_consumer.tick.persisted", **stats.model_dump()
                )
                consecutive_transient_errors = 0
                await self._sleep_or_exit(self._tick_interval)

            except TransientFetchError as e:
                consecutive_transient_errors += 1
                backoff = min(
                    self._tick_interval
                    * (2 ** (consecutive_transient_errors - 1)),
                    self._max_backoff,
                )
                self._logger.warning(
                    "continuous_consumer.tick.transient_error",
                    source=self._source_name,
                    error=str(e),
                    consecutive=consecutive_transient_errors,
                    backoff_seconds=backoff,
                )
                await self._sleep_or_exit(backoff)

            except TerminalFetchError as e:
                self._logger.error(
                    "continuous_consumer.tick.terminal_error",
                    source=self._source_name,
                    error=str(e),
                )
                raise

    def _batch_size(self, batch: TBatch) -> int:
        """Subclasses can override if `len()` is not defined for TBatch.

        Default uses len(), which works for list[Span].
        """
        return len(
            cast(Sized, batch)
        )  # subclasses may override for unsized batches

    async def _sleep_or_exit(self, seconds: float) -> None:
        """Sleep `seconds` but wake immediately if stop_event fires."""
        with suppress(TimeoutError):
            await asyncio.wait_for(self._stop_event.wait(), timeout=seconds)
