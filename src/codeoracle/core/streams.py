"""Stream-name constants for the Valkey-backed worker queues.

Centralizing the names here means Plan 6 can extend the family by adding a
new constant + a new WorkerConsumer subclass without grepping for string
literals scattered across the codebase. Tests assert against these
constants, not the raw strings, so renames are safe.

Stream naming: `indexing.<extractor>` for per-file extraction jobs,
`traces.<sub>` for trace ingestion (Plan 6), `<stream>.dlq` for the
dead-letter mirror (one per producer stream).

Consumer-group names match the worker process name; multi-process
parallelism within one worker kind is handled by Valkey's XREADGROUP
load-balancing across consumers in the same group.
"""

from typing import Final

# Producer streams — IndexingService XADDs here; workers XREADGROUP from here.
STREAM_INDEXING_JOERN: Final[str] = "indexing.joern"
STREAM_INDEXING_SCIP: Final[str] = "indexing.scip"
STREAM_INDEXING_OPENGREP: Final[str] = "indexing.opengrep"
STREAM_INDEXING_EMBED: Final[str] = "indexing.embed"

# Reindex orchestrator stream (added by 2026-05-26 amendment — async-job
# pattern, Plan 11 driver). IndexingService.enqueue_reindex XADDs a
# ReindexRequestMessage here when the API route receives POST /v1/reindex;
# the ReindexOrchestratorConsumer (Plan 5 Task 13a) XREADGROUPs and drives
# IndexingService.reindex(pre_allocated_snapshot_sha=…) in the worker
# process. See "What Plan 11 inherits from Plan 5" at end of file.
STREAM_REINDEX_REQUESTS: Final[str] = "codeoracle:reindex:requests"


# Consumer-group names — one per worker kind.
GROUP_JOERN_INDEXER: Final[str] = "joern_indexer"
GROUP_SCIP_INDEXER: Final[str] = "scip_indexer"
GROUP_OPENGREP_RUNNER: Final[str] = "opengrep_runner"
GROUP_EMBED_INDEXER: Final[str] = "embed_indexer"
GROUP_REINDEX_ORCHESTRATOR: Final[str] = "reindex_orchestrator"

# Continuous-consumer identifiers. These are not Valkey streams.
TRACES_INGEST_SOURCE: Final[str] = "tempo"
TRACES_INGEST_ADVISORY_LOCK_ID: Final[int] = 0x46AE0002


# Dead-letter streams — `<stream>.dlq` convention.
def dlq_for(stream_name: str) -> str:
    """Return the dead-letter stream paired with `stream_name`.

    DLQ pairing is by convention (suffix '.dlq'), not by Valkey-server
    config — `ValkeyQueueClient.dead_letter()` XADDs to the result of this
    function. Plan 6's workers reuse this helper.
    """
    if not stream_name or stream_name.endswith(".dlq"):
        raise ValueError(
            f"dlq_for() called with invalid stream name {stream_name!r} — "
            "must be a non-empty producer stream name."
        )
    return f"{stream_name}.dlq"
