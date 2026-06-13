"""JoernIndexerMessage — Pydantic envelope for a per-file extraction job.

Producer (IndexingService) sets all four fields; consumer validates
before dispatch.
"""

from codeoracle.workers._base.message import WorkerMessage


class JoernIndexerMessage(WorkerMessage):
    """Per-file Joern extraction request.

    Fields: batch_id: == snapshot_sha (parent reindex job)
        snapshot_sha: target snapshot (== batch_id; redundant for clarity)
        file_path: repo-root-relative path to the source file
        repo_root: absolute path to the repo containing the file (lets
            workers run from any CWD)
    """

    snapshot_sha: str
    file_path: str
    repo_root: str
