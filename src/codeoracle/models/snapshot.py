"""Snapshot — a versioned record of one extraction pass over the corpus.

A Snapshot's identity is its `sha`:
  - clean tree:  <git head SHA>                  (e.g. "abc1234...")
  - dirty tree:  <git head SHA>+wt<12-char hash> (e.g.
    "abc1234+wt5e7a9f0c8b3d")

The +wt suffix is computed by hashing the concatenation of (path, content)
for every git-tracked changed file. This lets dogfooding (`codeoracle index`
against uncommitted edits) work without forcing a commit.
"""

import hashlib
from datetime import datetime
from enum import StrEnum
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StringConstraints


class SnapshotStatus(StrEnum):
    PENDING = "pending"
    APPLYING = "applying"
    APPLIED = "applied"
    FAILED = "failed"


class ExtractorOutcome(StrEnum):
    SUCCESS = "success"
    SKIPPED = "skipped"
    FAILED = "failed"


# Keys used in Snapshot.extractor_results. The canonical bare names match
# Plan 6's IndexingService.DEFAULT_EXTRACTOR_KINDS = ("joern", "scip",
# "opengrep", "embed") — the four snapshot-keyed extractors that fan out
# under the per-file barrier. traces_ingest (Plan 7) is a ContinuousConsumer
# that writes to traces.spans, not extractor_results; Sourcebot is git-pull
# (no indexer worker — see Plan 5 §"Defers to Plan 6"). Kept as a frozenset
# so the closed-set assumption is check-able at instance construction.
KNOWN_EXTRACTORS: frozenset[str] = frozenset(
    {
        "joern",
        "scip",
        "opengrep",
        "embed",
    }
)


class Snapshot(BaseModel):
    """
    One end-to-end extraction pass. Lives in the `snapshots` PG table (Plan 3
    Task 9).
    """

    model_config = ConfigDict(frozen=False)  # status flips during application

    sha: Annotated[str, StringConstraints(min_length=4, max_length=80)]
    parent_sha: str | None = None
    changed_files: list[str] = Field(default_factory=list)
    status: SnapshotStatus = SnapshotStatus.PENDING
    extractor_results: dict[str, ExtractorOutcome] = Field(
        default_factory=dict
    )
    error: str | None = None
    applied_at: datetime | None = None


def compute_dirty_tree_sha(
    head_sha: str, file_contents: list[tuple[str, str]]
) -> str:
    """Compute the dirty-tree snapshot SHA.

    `file_contents` is a list of (path, content) pairs for every git-tracked
    file whose working-tree state differs from the HEAD commit. Order does
    NOT matter (the function sorts internally for determinism). Returns a
    string of the form '<head_sha>+wt<12-char-hex>'.
    """
    if not file_contents:
        return head_sha  # tree is clean — head SHA is the snapshot SHA
    h = hashlib.sha256()
    for path, content in sorted(file_contents, key=lambda x: x[0]):
        h.update(path.encode("utf-8"))
        h.update(b"\0")  # delimiter so ("ab", "cd") != ("a", "bcd")
        h.update(content.encode("utf-8"))
        h.update(b"\0")
    return f"{head_sha}+wt{h.hexdigest()[:12]}"
