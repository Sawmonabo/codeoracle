"""OpenGrep worker message."""

from pydantic import field_validator

from codeoracle.workers._base.message import WorkerMessage


class OpengrepRunnerMessage(WorkerMessage):
    snapshot_sha: str
    file_path: str
    repo_root: str

    @field_validator("snapshot_sha")
    @classmethod
    def _snapshot_sha_eq_batch_id(cls, v: str, info) -> str:
        batch_id = info.data.get("batch_id")
        if batch_id is not None and batch_id != v:
            raise ValueError(
                f"snapshot_sha ({v!r}) must equal batch_id ({batch_id!r})"
            )
        return v
