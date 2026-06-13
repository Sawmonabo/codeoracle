"""ScipIndexerMessage — payload schema for `indexing.scip` stream.

Project-mode shape: ONE message per (snapshot, scip-indexer) carrying the
file list. The worker runs scip-python on `repo_root` and writes one outcome
row per file in `files`.
"""

from pydantic import field_validator

from codeoracle.workers._base.message import WorkerMessage


class ScipIndexerMessage(WorkerMessage):
    snapshot_sha: str
    repo_root: str
    files: list[str]

    @field_validator("snapshot_sha")
    @classmethod
    def _snapshot_sha_eq_batch_id(cls, v: str, info) -> str:
        # info.data has the already-validated fields.
        batch_id = info.data.get("batch_id")
        if batch_id is not None and batch_id != v:
            raise ValueError(
                f"snapshot_sha ({v!r}) must equal batch_id ({batch_id!r})"
            )
        return v
