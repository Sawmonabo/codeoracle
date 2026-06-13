"""Snapshot file outcome repository."""

from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from codeoracle.db.models.snapshot_file_outcome import (
    SnapshotFileOutcomeRecord,
)


@dataclass(frozen=True, slots=True)
class SnapshotFileOutcomeRow:
    snapshot_sha: str
    file_path: str
    extractor: str
    outcome: str
    error_message: str | None = None


class SnapshotFileOutcomeRepository:
    """Records per-file-per-extractor outcomes."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def upsert(
        self,
        row: SnapshotFileOutcomeRow | None = None,
        *,
        snapshot_sha: str | None = None,
        file_path: str | None = None,
        extractor: str | None = None,
        outcome: str | None = None,
        error: str | None = None,
    ) -> None:
        """Insert or update one outcome row. Caller owns commit."""
        if row is not None:
            snapshot_sha = row.snapshot_sha
            file_path = row.file_path
            extractor = row.extractor
            outcome = row.outcome
            error = row.error_message
        if None in (snapshot_sha, file_path, extractor, outcome):
            raise ValueError(
                "snapshot_sha, file_path, extractor, and outcome are required"
            )
        now = datetime.now(UTC)
        stmt = (
            insert(SnapshotFileOutcomeRecord)
            .values(
                snapshot_sha=snapshot_sha,
                file_path=file_path,
                extractor=extractor,
                outcome=outcome,
                error=error,
                applied_at=now,
            )
            .on_conflict_do_update(
                constraint="pk_snapshot_file_outcomes",
                set_={
                    "outcome": outcome,
                    "error": error,
                    "applied_at": now,
                },
            )
        )
        await self._session.execute(stmt)
        await self._session.flush()

    async def count_completed(
        self, *, snapshot_sha: str, extractor: str
    ) -> int:
        """Number of files completed with any outcome for this extractor."""
        result = await self._session.execute(
            select(func.count())
            .select_from(SnapshotFileOutcomeRecord)
            .where(
                SnapshotFileOutcomeRecord.snapshot_sha == snapshot_sha,
                SnapshotFileOutcomeRecord.extractor == extractor,
            )
        )
        return int(result.scalar_one())

    async def count_failed(self, *, snapshot_sha: str, extractor: str) -> int:
        """Number of failed files for this extractor."""
        result = await self._session.execute(
            select(func.count())
            .select_from(SnapshotFileOutcomeRecord)
            .where(
                SnapshotFileOutcomeRecord.snapshot_sha == snapshot_sha,
                SnapshotFileOutcomeRecord.extractor == extractor,
                SnapshotFileOutcomeRecord.outcome == "failed",
            )
        )
        return int(result.scalar_one())
