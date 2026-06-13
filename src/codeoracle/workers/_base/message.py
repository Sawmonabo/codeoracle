"""WorkerMessage base + RetryPolicy.

WorkerMessage is the typed envelope every worker payload extends.
Subclasses add domain fields (e.g., JoernIndexerMessage adds
`snapshot_sha` + `file_path` + `parent_sha`).

RetryPolicy is computed per-message — the consumer calls
`policy.should_retry(delivered_count)` to decide ack-vs-retry-vs-DLQ.
"""

from dataclasses import dataclass

from pydantic import BaseModel


class WorkerMessage(BaseModel):
    """Base Pydantic model for every worker payload.

    Subclasses add domain-specific fields. The framework only reads
    `.batch_id` (for the IndexingService barrier) and the rest is opaque to it.

    `batch_id` ties a fan-out of per-file messages back to the parent
    snapshot's reindex job — Plan 5's IndexingService uses it to wait
    on completion via `snapshot_file_outcomes`.
    """

    model_config = {"frozen": True, "extra": "forbid"}

    batch_id: str  # = snapshot_sha for indexing workers


@dataclass(frozen=True)
class RetryPolicy:
    """Retry budget for the consumer's reclaim ladder.

    On the Nth delivery (1-indexed, as Valkey XPENDING reports):
      - N == 1 .. max_attempts-1   → retry (left in PEL for reclaim)
      - N == max_attempts           → dead-letter

    `should_retry(n)` is the live signal: the WorkerConsumer leaves a
    transiently-failed message in the PEL, and its reclaim step
    (reclaim_stale → XAUTOCLAIM, gated by a FIXED per-worker
    reclaim_min_idle_ms) hands the message back with an advanced
    delivered_count until should_retry returns False.

    Re-delivery pacing is owned entirely by that fixed reclaim min-idle,
    not by a per-attempt backoff curve — so `max_attempts` is the only knob.

    Defaults: 5 attempts.
    """

    max_attempts: int = 5

    def should_retry(self, delivered_count: int) -> bool:
        """True if delivered_count < max_attempts (still within budget)."""
        return delivered_count < self.max_attempts
