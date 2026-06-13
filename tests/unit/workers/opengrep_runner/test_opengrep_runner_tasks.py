"""Pure-function tests for OpenGrep reference extraction."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from codeoracle.adapters.opengrep.runner import (
    OpengrepFinding,
    OpengrepRunner,
)
from codeoracle.core.exceptions import OpengrepRunError
from codeoracle.db.repositories.code_references import CodeReferenceRow
from codeoracle.db.repositories.snapshot_file_outcome import (
    SnapshotFileOutcomeRow,
)
from codeoracle.workers.opengrep_runner.message import OpengrepRunnerMessage
from codeoracle.workers.opengrep_runner.tasks import extract_file_to_references


class _RefsRepo:
    def __init__(self) -> None:
        self.references: list[CodeReferenceRow] = []

    async def replace_file_references(
        self,
        *,
        snapshot_sha: str,
        file_path: str,
        references: list[CodeReferenceRow],
    ) -> None:
        _ = (snapshot_sha, file_path)
        self.references = references


class _OutcomeRepo:
    def __init__(self) -> None:
        self.rows: list[SnapshotFileOutcomeRow | None] = []

    async def upsert(
        self,
        row: SnapshotFileOutcomeRow | None = None,
    ) -> None:
        self.rows.append(row)


class _Session:
    def __init__(self) -> None:
        self.commits = 0
        self.rollbacks = 0

    async def commit(self) -> None:
        self.commits += 1

    async def rollback(self) -> None:
        self.rollbacks += 1


def _message(snapshot_sha: str = "sha-og") -> OpengrepRunnerMessage:
    return OpengrepRunnerMessage(
        batch_id=snapshot_sha,
        snapshot_sha=snapshot_sha,
        repo_root="/tmp/repo",
        file_path="a.py",
    )


@pytest.mark.asyncio
async def test_success_writes_references_and_success_outcome() -> None:
    runner = MagicMock(spec=OpengrepRunner)
    runner.scan_file = AsyncMock(
        return_value=[
            OpengrepFinding(
                file_path="a.py",
                symbol_name="foo",
                line_number=10,
                kind="call",
                rule_id="codeoracle-python-calls",
                message="call",
            ),
            OpengrepFinding(
                file_path="a.py",
                symbol_name="foo",
                line_number=10,
                kind="call",
                rule_id="codeoracle-python-calls",
                message="duplicate",
            ),
            OpengrepFinding(
                file_path="a.py",
                symbol_name="bar",
                line_number=5,
                kind="import",
                rule_id="codeoracle-python-imports",
                message="import",
            ),
        ]
    )
    refs_repo = _RefsRepo()
    outcome_repo = _OutcomeRepo()
    session = _Session()

    await extract_file_to_references(
        message=_message("sha-og-ok"),
        runner=runner,
        refs_repo=refs_repo,
        outcome_repo=outcome_repo,
        session=session,
    )

    references = {
        (r.symbol_name, r.line_number, r.kind) for r in refs_repo.references
    }
    assert references == {
        ("foo", 10, "call"),
        ("bar", 5, "import"),
    }
    row = outcome_repo.rows[-1]
    assert row is not None
    assert row.outcome == "success"
    assert session.commits == 1


@pytest.mark.asyncio
async def test_opengrep_failure_marks_file_failed() -> None:
    runner = MagicMock(spec=OpengrepRunner)
    runner.scan_file = AsyncMock(
        side_effect=OpengrepRunError("opengrep blew up")
    )
    refs_repo = _RefsRepo()
    outcome_repo = _OutcomeRepo()
    session = _Session()

    await extract_file_to_references(
        message=_message("sha-og-fail"),
        runner=runner,
        refs_repo=refs_repo,
        outcome_repo=outcome_repo,
        session=session,
    )

    assert refs_repo.references == []
    row = outcome_repo.rows[-1]
    assert row is not None
    assert row.outcome == "failed"
    assert row.error_message is not None
    assert "opengrep blew up" in row.error_message
    assert session.commits == 1


@pytest.mark.asyncio
async def test_zero_matches_still_writes_success_outcome() -> None:
    runner = MagicMock(spec=OpengrepRunner)
    runner.scan_file = AsyncMock(return_value=[])
    refs_repo = _RefsRepo()
    outcome_repo = _OutcomeRepo()
    session = _Session()

    await extract_file_to_references(
        message=_message("sha-og-empty"),
        runner=runner,
        refs_repo=refs_repo,
        outcome_repo=outcome_repo,
        session=session,
    )

    assert refs_repo.references == []
    row = outcome_repo.rows[-1]
    assert row is not None
    assert row.outcome == "success"
    assert session.commits == 1
