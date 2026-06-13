"""SnapshotRepository — CRUD + state-machine-enforced transitions
for the `snapshots` table (Plan 3 migration 0001).

State machine: PENDING → APPLYING → APPLIED | FAILED
    PENDING → FAILED  (rare: lock acquired then immediately rejected)

Any other transition (e.g., APPLIED → APPLYING) raises
SnapshotConflictError — the IndexingService state machine has been
violated and recovery requires manual intervention.
"""

from datetime import UTC, datetime
from typing import Final

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from codeoracle.core.exceptions import SnapshotConflictError
from codeoracle.db.models.snapshot import SnapshotRecord
from codeoracle.models.snapshot import (
    ExtractorOutcome,
    Snapshot,
    SnapshotStatus,
)

_ALLOWED_TRANSITIONS: Final[
    dict[SnapshotStatus, frozenset[SnapshotStatus]]
] = {
    SnapshotStatus.PENDING: frozenset(
        {SnapshotStatus.APPLYING, SnapshotStatus.FAILED}
    ),
    SnapshotStatus.APPLYING: frozenset(
        {SnapshotStatus.APPLIED, SnapshotStatus.FAILED}
    ),
    SnapshotStatus.APPLIED: frozenset(),  # terminal
    SnapshotStatus.FAILED: frozenset(),  # terminal
}


class SnapshotRepository:
    """All snapshot row operations live here. Construct per-request /
    per-orchestration-cycle (DON'T cache across the AsyncSession lifetime)."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create_pending(
        self,
        *,
        sha: str,
        parent_sha: str | None,
        changed_files: list[str],
    ) -> Snapshot:
        """INSERT a fresh PENDING snapshot. Caller must commit.

        Raises :class:`SnapshotConflictError` if a row with this ``sha``
        already exists: ``sha`` is the PRIMARY KEY (Plan 3 migration
        0001), so a duplicate INSERT surfaces as ``IntegrityError`` on
        ``flush()``, which this method translates to the domain
        exception. :meth:`IndexingService.enqueue_reindex` depends on
        this to make retried/concurrent ``POST /v1/reindex`` idempotent
        (catch → rollback → re-read the existing row → HTTP 202). The
        caller MUST ``rollback()`` the poisoned session before reuse.
        """
        row = SnapshotRecord(
            sha=sha,
            parent_sha=parent_sha,
            changed_files=changed_files,
            status=SnapshotStatus.PENDING.value,
            extractor_results={},
            error=None,
            applied_at=None,
        )
        self._session.add(row)
        try:
            await self._session.flush()
        except IntegrityError as exc:
            raise SnapshotConflictError(
                f"Snapshot sha={sha!r} already exists."
            ) from exc
        return self._row_to_model(row)

    async def get_by_sha(self, sha: str) -> Snapshot | None:
        """SELECT one snapshot or None."""
        result = await self._session.execute(
            select(SnapshotRecord).where(SnapshotRecord.sha == sha)
        )
        row = result.scalar_one_or_none()
        return self._row_to_model(row) if row else None

    async def get_latest_applied(self) -> Snapshot | None:
        """SELECT the most recent APPLIED snapshot."""
        result = await self._session.execute(
            select(SnapshotRecord)
            .where(SnapshotRecord.status == SnapshotStatus.APPLIED.value)
            .order_by(SnapshotRecord.applied_at.desc())
            .limit(1)
        )
        row = result.scalar_one_or_none()
        return self._row_to_model(row) if row else None

    async def transition(
        self,
        *,
        sha: str,
        to: SnapshotStatus,
        extractor_results: dict[str, ExtractorOutcome] | None = None,
        error: str | None = None,
    ) -> Snapshot:
        """Transition `sha` to `to`. Raises SnapshotConflictError on
        invalid moves. Caller must commit.

        On `to == APPLIED`: `applied_at` is set to now(UTC) and the
        extractor_results map is persisted.
        """
        current = await self.get_by_sha(sha)
        if current is None:
            raise SnapshotConflictError(f"Snapshot sha={sha!r} not found.")
        if to not in _ALLOWED_TRANSITIONS[current.status]:
            message = (
                f"Cannot transition snapshot {sha!r} from "
                f"{current.status.value} to {to.value}."
            )
            raise SnapshotConflictError(message)

        values: dict[str, object] = {"status": to.value}
        if to == SnapshotStatus.APPLIED:
            values["applied_at"] = datetime.now(UTC)
        if extractor_results is not None:
            values["extractor_results"] = {
                k: v.value for k, v in extractor_results.items()
            }
        if error is not None:
            values["error"] = error

        await self._session.execute(
            update(SnapshotRecord)
            .where(SnapshotRecord.sha == sha)
            .values(**values)
        )
        await self._session.flush()
        updated = await self.get_by_sha(sha)
        # get_by_sha just returned non-None — re-assert for ty
        assert updated is not None
        return updated

    async def mark_stale_applying_failed(self) -> int:
        """Bulk-transition every `APPLYING` row to `FAILED` with
        `error='process died mid-apply'`. Returns the row count (0 when
        no stale rows exist — caller can suppress the log line).

        **Precondition (load-bearing — DO NOT call without this):** the
        *calling process* MUST be holding `pg_try_advisory_lock(0x46AE0001)`
        on *some* connection (session-level, the same lock that
        `reindex_single_flight` takes — see Task 9). That lock-holder
        connection is typically a *different* session from the one this
        method runs against; advisory locks are scoped to the holding
        connection at the Postgres backend, not transaction-scoped to
        this method's session. Any `APPLYING` row visible to the sweep
        while the per-process lock-holder connection is alive is, by
        construction, a SIGKILL/OOM leftover from a prior process whose
        connection died — Postgres auto-released its advisory lock on
        backend cleanup, so no concurrent legitimate reindex on any
        connection in any process can exist while our lock-holder is
        alive. If the precondition is violated (no process-wide
        lock-holder), this method will clobber a legitimately-running
        reindex's in-flight `APPLYING` row.

        **Why raw UPDATE instead of looping `transition()`?** Two
        reasons, in priority order:

        1. *Atomicity.* `transition()` does `get_by_sha` → check →
           UPDATE per row. Across N stale rows it issues N round-trips
           with TOCTOU windows between each read and write. One bulk
           SQL `UPDATE ... WHERE status='applying'` is atomic at the
           database level — no window where two callers can race.
        2. *State-machine bypass is intentional.* `_ALLOWED_TRANSITIONS`
           permits `APPLYING → FAILED`, so a `transition()` loop would
           succeed; we still avoid it because the per-row read+check
           is dead weight when the `WHERE` clause already constrains
           the source state.

        Alternative considered + rejected: assert the precondition at
        runtime by probing for a process-wide lock-holder from this
        method's session (e.g., `SELECT pg_try_advisory_lock(0x46AE0001)`
        — returns `false` if some OTHER session is holding it, which is
        what we want — then immediately `pg_advisory_unlock` if we
        accidentally acquired it). Rejected because it costs a
        round-trip per call to defend against a callsite that doesn't
        exist in Plan 5; the load-bearing-precondition docstring above
        is the contract.
        """
        result = await self._session.execute(
            update(SnapshotRecord)
            .where(SnapshotRecord.status == SnapshotStatus.APPLYING.value)
            .values(
                status=SnapshotStatus.FAILED.value,
                error="process died mid-apply",
            )
        )
        await self._session.flush()
        # asyncpg returns the real count; SQLAlchemy `result.rowcount`
        # can be `-1` for dialects that don't report it — fall through
        # to 0 so callers can treat the number as a count for logging.
        return max(getattr(result, "rowcount", 0) or 0, 0)

    @staticmethod
    def _row_to_model(row: SnapshotRecord) -> Snapshot:
        return Snapshot(
            sha=row.sha,
            parent_sha=row.parent_sha,
            changed_files=list(row.changed_files or []),
            status=SnapshotStatus(row.status),
            extractor_results={
                k: ExtractorOutcome(v)
                for k, v in (row.extractor_results or {}).items()
            },
            error=row.error,
            applied_at=row.applied_at,
        )
