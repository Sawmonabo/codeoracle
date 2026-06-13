"""Plan 5 Task 14: `codeoracle index` CLI unit tests with mocked service."""

from unittest.mock import AsyncMock, MagicMock, patch

from typer.testing import CliRunner

from codeoracle.cli.__main__ import app
from codeoracle.core.exceptions import AdvisoryLockTimeoutError
from codeoracle.models.snapshot import (
    ExtractorOutcome,
    Snapshot,
    SnapshotStatus,
)


def _applied_snapshot(sha="abc1234"):
    return Snapshot(
        sha=sha,
        parent_sha=None,
        changed_files=["a.py"],
        status=SnapshotStatus.APPLIED,
        extractor_results={"joern": ExtractorOutcome.SUCCESS},
        error=None,
        applied_at=None,
    )


def _failed_snapshot(sha="abc1234"):
    return Snapshot(
        sha=sha,
        parent_sha=None,
        changed_files=["a.py"],
        status=SnapshotStatus.FAILED,
        extractor_results={},
        error="orchestrator timed out",
        applied_at=None,
    )


def test_full_flag_invokes_reindex_with_none_base():
    runner = CliRunner()
    with (
        patch("codeoracle.cli.commands.index.IndexingService") as svc,
        patch("codeoracle.cli.commands.index.make_async_engine"),
        patch("codeoracle.cli.commands.index.make_async_session_factory"),
        patch("codeoracle.cli.commands.index.ValkeyQueueClient") as qc,
    ):
        svc.return_value.reindex = AsyncMock(return_value=_applied_snapshot())
        qc.return_value.start = AsyncMock()
        qc.return_value.aclose = AsyncMock()
        result = runner.invoke(app, ["index", "--full"])
    assert result.exit_code == 0
    assert "APPLIED" in result.output


def test_failed_snapshot_exits_2():
    runner = CliRunner()
    with (
        patch("codeoracle.cli.commands.index.IndexingService") as svc,
        patch("codeoracle.cli.commands.index.make_async_engine"),
        patch("codeoracle.cli.commands.index.make_async_session_factory"),
        patch("codeoracle.cli.commands.index.ValkeyQueueClient") as qc,
    ):
        svc.return_value.reindex = AsyncMock(return_value=_failed_snapshot())
        qc.return_value.start = AsyncMock()
        qc.return_value.aclose = AsyncMock()
        result = runner.invoke(app, ["index", "--full"])
    assert result.exit_code == 2
    assert "FAILED" in result.output


def test_advisory_lock_timeout_exits_3():
    runner = CliRunner()
    with (
        patch("codeoracle.cli.commands.index.IndexingService") as svc,
        patch("codeoracle.cli.commands.index.make_async_engine"),
        patch("codeoracle.cli.commands.index.make_async_session_factory"),
        patch("codeoracle.cli.commands.index.ValkeyQueueClient") as qc,
    ):
        svc.return_value.reindex = AsyncMock(
            side_effect=AdvisoryLockTimeoutError("locked"),
        )
        qc.return_value.start = AsyncMock()
        qc.return_value.aclose = AsyncMock()
        result = runner.invoke(app, ["index", "--full"])
    assert result.exit_code == 3
    assert "Lock timeout" in result.output


def test_no_args_defaults_to_latest_applied():
    runner = CliRunner()
    with (
        patch("codeoracle.cli.commands.index.IndexingService") as svc,
        patch("codeoracle.cli.commands.index.make_async_engine"),
        patch(
            "codeoracle.cli.commands.index.make_async_session_factory"
        ) as sf,
        patch("codeoracle.cli.commands.index.ValkeyQueueClient") as qc,
        patch("codeoracle.cli.commands.index.SnapshotRepository") as sr,
    ):
        sf.return_value = MagicMock()
        sf.return_value.return_value.__aenter__ = AsyncMock(
            return_value=MagicMock()
        )
        sf.return_value.return_value.__aexit__ = AsyncMock()
        sr.return_value.get_latest_applied = AsyncMock(
            return_value=_applied_snapshot(sha="prev_sha")
        )
        svc.return_value.reindex = AsyncMock(return_value=_applied_snapshot())
        qc.return_value.start = AsyncMock()
        qc.return_value.aclose = AsyncMock()
        result = runner.invoke(app, ["index"])
    assert result.exit_code == 0
    assert "prev_sha" in result.output
