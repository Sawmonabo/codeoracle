"""ScipIndexerMessage — Pydantic model, project-mode shape."""

import pytest
from pydantic import ValidationError

from codeoracle.workers.scip_indexer.message import ScipIndexerMessage


def test_required_fields() -> None:
    msg = ScipIndexerMessage(
        batch_id="sha-x",
        snapshot_sha="sha-x",
        repo_root="/tmp/repo",
        files=["a.py", "b.py"],
    )
    assert msg.snapshot_sha == "sha-x"
    assert msg.batch_id == "sha-x"
    assert msg.files == ["a.py", "b.py"]


def test_batch_id_must_equal_snapshot_sha() -> None:
    """ScipIndexerMessage enforces batch_id == snapshot_sha so the barrier
    invariant ('batch_id is the polling key') is statically checkable."""
    with pytest.raises(ValidationError):
        ScipIndexerMessage(
            batch_id="different",
            snapshot_sha="sha-x",
            repo_root="/tmp/repo",
            files=["a.py"],
        )


def test_empty_files_list_is_legal() -> None:
    """A snapshot with no Python files (none for SCIP to index) is legal —
    the worker still must write zero outcome rows and ACK the message."""
    msg = ScipIndexerMessage(
        batch_id="sha-x",
        snapshot_sha="sha-x",
        repo_root="/tmp/repo",
        files=[],
    )
    assert msg.files == []


def test_files_required() -> None:
    """`files` is required (not optional) — the message MUST carry the file
    list so the worker knows how many outcome rows to write."""
    with pytest.raises(ValidationError):
        ScipIndexerMessage.model_validate(
            {
                "batch_id": "sha-x",
                "snapshot_sha": "sha-x",
                "repo_root": "/tmp/repo",
            }
        )
