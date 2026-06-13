"""EmbedIndexerMessage — same shape as Joern/OpenGrep per-file messages."""

import pytest
from pydantic import ValidationError

from codeoracle.workers.embed_indexer.message import EmbedIndexerMessage


def test_required_fields() -> None:
    msg = EmbedIndexerMessage(
        batch_id="sha-e",
        snapshot_sha="sha-e",
        repo_root="/tmp/repo",
        file_path="a.py",
    )
    assert msg.snapshot_sha == "sha-e"
    assert msg.file_path == "a.py"


def test_batch_id_must_equal_snapshot_sha() -> None:
    with pytest.raises(ValidationError):
        EmbedIndexerMessage(
            batch_id="other",
            snapshot_sha="sha-e",
            repo_root="/tmp/repo",
            file_path="a.py",
        )
