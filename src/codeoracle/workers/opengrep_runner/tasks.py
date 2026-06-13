"""Pure-function pipeline for per-file OpenGrep extraction."""

import logging
from pathlib import Path
from typing import Protocol

from codeoracle.adapters.opengrep.runner import OpengrepFinding, OpengrepRunner
from codeoracle.core.exceptions import OpengrepRunError
from codeoracle.db.repositories.code_references import CodeReferenceRow
from codeoracle.db.repositories.snapshot_file_outcome import (
    SnapshotFileOutcomeRow,
)
from codeoracle.workers.opengrep_runner.message import OpengrepRunnerMessage

_LOG = logging.getLogger(__name__)
_ERROR_MESSAGE_CHARS = 512


class _ReferencesRepo(Protocol):
    async def replace_file_references(
        self,
        *,
        snapshot_sha: str,
        file_path: str,
        references: list[CodeReferenceRow],
    ) -> None: ...


class _OutcomeRepo(Protocol):
    async def upsert(
        self, row: SnapshotFileOutcomeRow | None = None
    ) -> None: ...


class _Session(Protocol):
    async def commit(self) -> None: ...
    async def rollback(self) -> None: ...


async def run_opengrep_for_file(
    *, runner: OpengrepRunner, path: Path
) -> list[OpengrepFinding]:
    return await runner.scan_file(path)


async def extract_file_to_references(
    *,
    message: OpengrepRunnerMessage,
    runner: OpengrepRunner,
    refs_repo: _ReferencesRepo,
    outcome_repo: _OutcomeRepo,
    session: _Session,
) -> None:
    abs_file_path = Path(message.repo_root) / message.file_path

    try:
        matches = await runner.scan_file(abs_file_path)
    except OpengrepRunError as exc:
        _LOG.exception("opengrep run failed for %s", message.file_path)
        await _record_failed_outcome(
            message=message,
            outcome_repo=outcome_repo,
            session=session,
            error_message=(f"opengrep: {str(exc)[:_ERROR_MESSAGE_CHARS]}"),
        )
        return

    rows = _reference_rows(message=message, matches=matches)
    try:
        await refs_repo.replace_file_references(
            snapshot_sha=message.snapshot_sha,
            file_path=message.file_path,
            references=rows,
        )
        await outcome_repo.upsert(
            SnapshotFileOutcomeRow(
                snapshot_sha=message.snapshot_sha,
                file_path=message.file_path,
                extractor="opengrep",
                outcome="success",
                error_message=None,
            )
        )
        await session.commit()
    except Exception as exc:
        await session.rollback()
        await _record_failed_outcome(
            message=message,
            outcome_repo=outcome_repo,
            session=session,
            error_message=f"db write: {str(exc)[:_ERROR_MESSAGE_CHARS]}",
        )
        raise


def _reference_rows(
    *,
    message: OpengrepRunnerMessage,
    matches: list[OpengrepFinding],
) -> list[CodeReferenceRow]:
    seen: set[CodeReferenceRow] = set()
    for match in matches:
        if match.symbol_name:
            seen.add(
                CodeReferenceRow(
                    snapshot_sha=message.snapshot_sha,
                    file_path=message.file_path,
                    symbol_name=match.symbol_name,
                    line_number=match.line_number,
                    kind=match.kind,
                )
            )
    return list(seen)


async def _record_failed_outcome(
    *,
    message: OpengrepRunnerMessage,
    outcome_repo: _OutcomeRepo,
    session: _Session,
    error_message: str,
) -> None:
    await outcome_repo.upsert(
        SnapshotFileOutcomeRow(
            snapshot_sha=message.snapshot_sha,
            file_path=message.file_path,
            extractor="opengrep",
            outcome="failed",
            error_message=error_message,
        )
    )
    await session.commit()
