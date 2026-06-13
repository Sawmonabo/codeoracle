"""IndexingService — public-facing reindex orchestration.

Lifecycle (one call):
  1. Acquire pg_advisory_lock (single-flight).
  2. DeltaComputer.compute() → Changeset.
  3. SnapshotRepository.create_pending(...)
  4. SnapshotRepository.transition(...→APPLYING)
  5. IndexingOrchestrator.fan_out_and_wait(...) — enqueue + barrier.
  6. SnapshotRepository.transition(...→APPLIED) with extractor_results.
  7. Release advisory lock (via the context manager's finally).

If any step raises, the snapshot is transitioned to FAILED before the
exception propagates — keeps the DB consistent with whatever the user
sees on the CLI.
"""

import logging

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
)

from codeoracle.adapters.valkey.client import ValkeyQueueClient
from codeoracle.core.exceptions import (
    SnapshotConflictError,
)
from codeoracle.core.extractors import DEFAULT_EXTRACTOR_KINDS
from codeoracle.core.streams import STREAM_REINDEX_REQUESTS
from codeoracle.db.repositories.graph import GraphRepository
from codeoracle.db.repositories.snapshots import SnapshotRepository
from codeoracle.models.snapshot import (
    ExtractorOutcome,
    Snapshot,
    SnapshotStatus,
)
from codeoracle.services.indexing.advisory_lock import reindex_single_flight
from codeoracle.services.indexing.delta import DeltaComputer
from codeoracle.services.indexing.orchestrator import IndexingOrchestrator

logger = logging.getLogger(__name__)


# Per-extractor outcome mapping. `Snapshot.extractor_results` is the
# per-snapshot diagnostic record of how each extractor fared — surfaced
# by the CLI (Task 14) for operators; it is NOT a queryability gate.
# Queryability is enforced downstream by the agent's graph_query
# existence check: an empty / all-failed graph yields a terminal UNKNOWN
# (Plan 10), never a false answer. partial = SUCCESS is correct: the
# graph has *some* nodes/edges from this extractor, and per-file failures
# are already recorded in `snapshot_file_outcomes` for operators who need
# the fine-grained view. If a future caller needs to distinguish
# partial-vs-full success at snapshot scope, ADD a third enum value
# (ExtractorOutcome.PARTIAL) rather than flipping this mapping silently.
_OUTCOME_TO_EXTRACTOR_OUTCOME: dict[str, ExtractorOutcome] = {
    "success": ExtractorOutcome.SUCCESS,
    "partial": ExtractorOutcome.SUCCESS,
    "failed": ExtractorOutcome.FAILED,
    "in_progress": ExtractorOutcome.FAILED,  # timeout → failed
}


class IndexingService:
    def __init__(
        self,
        *,
        engine: AsyncEngine,
        db_session_factory: async_sessionmaker[AsyncSession],
        queue_client: ValkeyQueueClient,
        repo_root: str,
        extractor_kinds: tuple[str, ...] = DEFAULT_EXTRACTOR_KINDS,
        advisory_lock_timeout_seconds: float = 0.0,
    ) -> None:
        self._engine = engine
        self._db = db_session_factory
        self._queue = queue_client
        self._repo_root = repo_root
        self._extractor_kinds = extractor_kinds
        self._lock_timeout = advisory_lock_timeout_seconds

    async def reindex(
        self,
        *,
        base_sha: str | None,
        pre_allocated_snapshot_sha: str | None = None,  # 2026-05-26 amendment
    ) -> Snapshot:
        """Perform one reindex pass. Returns the final Snapshot row.

        Two callers, two shapes:

        - **CLI** (``codeoracle index`` — Task 14) leaves
          ``pre_allocated_snapshot_sha=None``; the method runs
          :class:`DeltaComputer` and creates the PENDING snapshot row itself.
        - **Worker** (:class:`ReindexOrchestratorConsumer` — Task 13a,
          added 2026-05-26 for the Plan 11 async-job pivot) passes the
          ``snapshot_sha`` already created by :meth:`enqueue_reindex`;
          this method reads ``changed_files`` and ``parent_sha`` from
          that row and does NOT re-run :class:`DeltaComputer`. The row
          is the canonical job definition — re-deriving it from the
          tree would (a) waste compute on large-repository walks and
          (b) force defining behavior on tree-shift mismatch, which is
          a footgun under the WorkerConsumer NACK/retry/DLQ flow.
        """
        async with reindex_single_flight(
            self._engine, timeout_seconds=self._lock_timeout
        ):
            return await self._reindex_locked(
                base_sha=base_sha,
                pre_allocated_snapshot_sha=pre_allocated_snapshot_sha,
            )

    async def enqueue_reindex(self, *, base_sha: str | None) -> Snapshot:
        """Schedule an async reindex; return the PENDING ``Snapshot`` row.

        Added 2026-05-26 — Plan 11 async-job pivot driver. Returns
        immediately after persisting a PENDING ``Snapshot`` row +
        ``XADD`` to ``STREAM_REINDEX_REQUESTS``. Does **NOT** acquire
        ``pg_advisory_lock(0x46AE0001)`` — that lock lives entirely
        inside the worker process (:class:`ReindexOrchestratorConsumer`,
        Task 13a). Per Plan 11 invariant 4 (amended), the API process
        therefore never observes lock contention.

        **Idempotent on the same tree state.** :class:`DeltaComputer`
        produces a deterministic ``new_snapshot_sha`` for a given tree;
        if the corresponding row already exists (concurrent POST, or
        client retry), :meth:`SnapshotRepository.create_pending` raises
        :class:`SnapshotConflictError`. This method catches it, returns
        the existing row (status ∈ {PENDING, APPLYING, APPLIED, FAILED}
        depending on the worker's progress), and does NOT re-XADD. The
        ``POST /v1/reindex`` route inherits this idempotency.
        """
        async with self._db() as session:
            graph_repo = GraphRepository(session)
            snap_repo = SnapshotRepository(session)
            delta_computer = DeltaComputer(
                graph_repo=graph_repo, repo_root=None
            )
            cs = await delta_computer.compute(base_sha=base_sha)

            if base_sha is not None:
                parent_sha: str | None = base_sha
            else:
                latest = await snap_repo.get_latest_applied()
                parent_sha = latest.sha if latest is not None else None

            try:
                snap = await snap_repo.create_pending(
                    sha=cs.new_snapshot_sha,
                    parent_sha=parent_sha,
                    changed_files=cs.all_files,
                )
                await session.commit()
                newly_enqueued = True
            except SnapshotConflictError:
                await session.rollback()
                snap = await snap_repo.get_by_sha(cs.new_snapshot_sha)
                assert snap is not None, (
                    "create_pending raised SnapshotConflictError but "
                    "get_by_sha returned None — DB invariant violated"
                )
                newly_enqueued = False

        if newly_enqueued:
            # ReindexRequestMessage extends WorkerMessage, which requires
            # batch_id (no default). The consumer validates the payload
            # via ReindexRequestMessage.model_validate; a missing batch_id
            # would raise ValidationError → NACK → retry → DLQ on every
            # message. Bind batch_id to snap.sha — the message's
            # snapshot_sha is already its natural correlation key, so
            # using it for batch_id keeps log-tracing single-keyed.
            # We pass a plain dict (not a constructed ReindexRequestMessage)
            # to keep services/indexing/ free of an import on
            # workers/reindex_orchestrator/message.py (which would create
            # a services/ → workers/ cycle via the consumer module).
            await self._queue.enqueue(
                STREAM_REINDEX_REQUESTS,
                {
                    "batch_id": snap.sha,
                    "snapshot_sha": snap.sha,
                    "base_sha": base_sha,
                },
            )
            logger.info(
                "indexing_enqueued_reindex",
                extra={"snapshot_sha": snap.sha, "base_sha": base_sha},
            )
        else:
            logger.info(
                "indexing_reindex_idempotent",
                extra={
                    "snapshot_sha": snap.sha,
                    "base_sha": base_sha,
                    "current_status": snap.status,
                },
            )
        return snap

    async def get_snapshot(self, snapshot_sha: str) -> Snapshot | None:
        """Return the Snapshot row, or ``None`` if not found.

        Added 2026-05-26 — used by Plan 11's
        ``GET /v1/reindex/{snapshot_sha}`` polling endpoint. Thin facade
        over :meth:`SnapshotRepository.get_by_sha`; lives on the service
        so the API layer never reaches into ``db/repositories/`` directly
        (preserves Plan 11 invariant 1 — thin entrypoint, no DB access
        from ``api/``).
        """
        async with self._db() as session:
            return await SnapshotRepository(session).get_by_sha(snapshot_sha)

    async def _reindex_locked(
        self,
        *,
        base_sha: str | None,
        pre_allocated_snapshot_sha: str | None,
    ) -> Snapshot:
        await self._sweep_stale_applying_rows()
        snap, files = await self._prepare_snapshot_for_reindex(
            base_sha=base_sha,
            pre_allocated_snapshot_sha=pre_allocated_snapshot_sha,
        )

        snapshot_sha = snap.sha
        try:
            await self._transition_snapshot(
                snapshot_sha=snapshot_sha,
                status=SnapshotStatus.APPLYING,
            )
            kind_outcomes = await self._fan_out_and_wait(
                snapshot_sha=snapshot_sha,
                files=files,
            )
            final_status, final_error, extractor_results = (
                self._resolve_final_snapshot_state(kind_outcomes)
            )
            await self._link_cross_file_symbols_if_applied(
                snapshot_sha=snapshot_sha,
                final_status=final_status,
            )
            return await self._transition_snapshot(
                snapshot_sha=snapshot_sha,
                status=final_status,
                extractor_results=extractor_results,
                error=final_error,
            )
        except KeyboardInterrupt:
            logger.warning("indexing_interrupted_by_user")
            await self._mark_interrupted_snapshot_failed(snapshot_sha)
            raise
        except Exception as exc:
            logger.exception("indexing_failed")
            failed_snap = await self._mark_failed_after_exception(
                snapshot_sha=snapshot_sha,
                exc=exc,
            )
            if failed_snap is not None:
                return failed_snap
            raise

    async def _sweep_stale_applying_rows(self) -> None:
        """Recover APPLYING rows abandoned by a dead reindex process."""
        async with self._db() as session:
            snap_repo = SnapshotRepository(session)
            stale_count = await snap_repo.mark_stale_applying_failed()
            await session.commit()

        if stale_count > 0:
            logger.warning(
                "indexing_swept_stale_applying_rows",
                extra={"count": stale_count},
            )

    async def _prepare_snapshot_for_reindex(
        self,
        *,
        base_sha: str | None,
        pre_allocated_snapshot_sha: str | None,
    ) -> tuple[Snapshot, list[str]]:
        async with self._db() as session:
            snap_repo = SnapshotRepository(session)
            if pre_allocated_snapshot_sha is None:
                result = await self._create_cli_pending_snapshot(
                    session=session,
                    snap_repo=snap_repo,
                    base_sha=base_sha,
                )
            else:
                result = await self._load_preallocated_snapshot(
                    snap_repo=snap_repo,
                    snapshot_sha=pre_allocated_snapshot_sha,
                )
            await session.commit()
            return result

    async def _create_cli_pending_snapshot(
        self,
        *,
        session: AsyncSession,
        snap_repo: SnapshotRepository,
        base_sha: str | None,
    ) -> tuple[Snapshot, list[str]]:
        delta_computer = DeltaComputer(
            graph_repo=GraphRepository(session),
            repo_root=None,
        )
        changeset = await delta_computer.compute(base_sha=base_sha)
        parent_sha = await self._resolve_parent_sha(
            snap_repo=snap_repo,
            base_sha=base_sha,
        )
        snap = await snap_repo.create_pending(
            sha=changeset.new_snapshot_sha,
            parent_sha=parent_sha,
            changed_files=changeset.all_files,
        )
        return snap, changeset.all_files

    async def _load_preallocated_snapshot(
        self,
        *,
        snap_repo: SnapshotRepository,
        snapshot_sha: str,
    ) -> tuple[Snapshot, list[str]]:
        snap = await snap_repo.get_by_sha(snapshot_sha)
        assert snap is not None, (
            f"pre_allocated_snapshot_sha {snapshot_sha!r} missing from "
            "snapshots table — worker invariant violation"
        )
        return snap, snap.changed_files

    @staticmethod
    async def _resolve_parent_sha(
        *,
        snap_repo: SnapshotRepository,
        base_sha: str | None,
    ) -> str | None:
        if base_sha is not None:
            return base_sha
        latest = await snap_repo.get_latest_applied()
        return latest.sha if latest is not None else None

    async def _transition_snapshot(
        self,
        *,
        snapshot_sha: str,
        status: SnapshotStatus,
        extractor_results: dict[str, ExtractorOutcome] | None = None,
        error: str | None = None,
    ) -> Snapshot:
        async with self._db() as session:
            snap_repo = SnapshotRepository(session)
            snap = await snap_repo.transition(
                sha=snapshot_sha,
                to=status,
                extractor_results=extractor_results,
                error=error,
            )
            await session.commit()
            return snap

    async def _fan_out_and_wait(
        self,
        *,
        snapshot_sha: str,
        files: list[str],
    ) -> dict[str, str]:
        orchestrator = IndexingOrchestrator(
            queue_client=self._queue,
            db_session_factory=self._db,
            extractor_kinds=self._extractor_kinds,
        )
        return await orchestrator.fan_out_and_wait(
            snapshot_sha=snapshot_sha,
            repo_root=self._repo_root,
            files=files,
        )

    @staticmethod
    def _resolve_final_snapshot_state(
        kind_outcomes: dict[str, str],
    ) -> tuple[SnapshotStatus, str | None, dict[str, ExtractorOutcome]]:
        extractor_results = {
            kind: _OUTCOME_TO_EXTRACTOR_OUTCOME[outcome]
            for kind, outcome in kind_outcomes.items()
        }
        if any(outcome == "in_progress" for outcome in kind_outcomes.values()):
            return (
                SnapshotStatus.FAILED,
                "orchestrator timed out",
                extractor_results,
            )
        if not any(
            outcome in ("success", "partial")
            for outcome in kind_outcomes.values()
        ):
            return (
                SnapshotStatus.FAILED,
                "all extractors failed — no snapshot data extracted",
                extractor_results,
            )
        return SnapshotStatus.APPLIED, None, extractor_results

    async def _link_cross_file_symbols_if_applied(
        self,
        *,
        snapshot_sha: str,
        final_status: SnapshotStatus,
    ) -> None:
        if final_status != SnapshotStatus.APPLIED:
            return
        async with self._db() as session:
            await GraphRepository(session).link_cross_file_symbols(
                snapshot_sha
            )
            await session.commit()

    async def _mark_interrupted_snapshot_failed(
        self, snapshot_sha: str
    ) -> None:
        try:
            await self._transition_snapshot(
                snapshot_sha=snapshot_sha,
                status=SnapshotStatus.FAILED,
                error="interrupted by KeyboardInterrupt",
            )
        except SnapshotConflictError:
            return

    async def _mark_failed_after_exception(
        self,
        *,
        snapshot_sha: str,
        exc: Exception,
    ) -> Snapshot | None:
        try:
            return await self._transition_snapshot(
                snapshot_sha=snapshot_sha,
                status=SnapshotStatus.FAILED,
                error=str(exc),
            )
        except SnapshotConflictError:
            return None
