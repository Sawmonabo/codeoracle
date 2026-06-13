"""ReindexRequestMessage — Pydantic envelope for an async-reindex job.

Producer: `IndexingService.enqueue_reindex` (in the API process, after
the POST /v1/reindex route lands the PENDING snapshot row).
Consumer: `ReindexOrchestratorConsumer` (in this worker process).

The PENDING row created by `enqueue_reindex` carries `changed_files`
already, so this message is intentionally minimal — `snapshot_sha` is
the only field the worker needs; `base_sha` is informational (logged
for traceability when the API caller passed an explicit base).
"""

from codeoracle.workers._base.message import WorkerMessage


class ReindexRequestMessage(WorkerMessage):
    """Async reindex request.

    Fields: batch_id: == snapshot_sha (inherited from WorkerMessage; tied to
            the parent reindex's pre-allocated Snapshot row).
        snapshot_sha: target snapshot (== batch_id; redundant for clarity,
            matching the JoernIndexerMessage convention).
        base_sha: caller's base hint (None → DeltaComputer picked latest
            applied at enqueue time). Diagnostic only; the worker does
            NOT re-run DeltaComputer (see IndexingService.reindex docstring
            — the row is the canonical job definition).
    """

    snapshot_sha: str
    base_sha: str | None = None
