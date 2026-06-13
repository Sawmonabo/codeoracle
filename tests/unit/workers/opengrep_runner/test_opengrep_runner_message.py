"""OpengrepRunnerMessage validation."""

from typing import Any, cast

import pytest
from pydantic import ValidationError

from codeoracle.workers.opengrep_runner.message import OpengrepRunnerMessage


def test_required_fields() -> None:
    msg = OpengrepRunnerMessage(
        batch_id="sha-x",
        snapshot_sha="sha-x",
        repo_root="/tmp/repo",
        file_path="a.py",
    )
    assert msg.snapshot_sha == "sha-x"
    assert msg.file_path == "a.py"
    assert msg.batch_id == msg.snapshot_sha


def test_batch_id_must_equal_snapshot_sha() -> None:
    with pytest.raises(ValidationError, match="snapshot_sha"):
        OpengrepRunnerMessage(
            batch_id="other",
            snapshot_sha="sha-x",
            repo_root="/tmp/repo",
            file_path="a.py",
        )


def test_file_path_required() -> None:
    message_type = cast(Any, OpengrepRunnerMessage)
    with pytest.raises(ValidationError, match="file_path"):
        message_type(
            batch_id="sha-x",
            snapshot_sha="sha-x",
            repo_root="/tmp/repo",
        )
