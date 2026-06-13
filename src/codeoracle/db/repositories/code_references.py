"""CodeReferencesRepository: textual reference writes/reads.

The writer is `workers/opengrep_runner` (Plan 6 Task 12): one call to
`replace_file_references` per consumed per-file message, inside the worker's
session/transaction.

The reader is Plan 9's verifier service (`VerifierService.cross_check_symbol`),
which calls `count_by_symbol` and `count_by_symbol_and_kind`.

Idempotency: `replace_file_references` performs DELETE-WHERE + INSERT inside a
single transaction. A second call with the same (snapshot_sha, file_path) and
identical refs produces an identical row set (modulo `created_at`); the
underlying composite PK (snapshot_sha, file_path, symbol_name, line_number,
kind) prevents accidental duplicates.
"""

from dataclasses import dataclass

from sqlalchemy import delete, func, insert, select
from sqlalchemy.ext.asyncio import AsyncSession

from codeoracle.db.models.code_reference import (
    CodeReferenceKind,
    CodeReferenceRecord,
)


@dataclass(frozen=True, slots=True)
class CodeReferenceRow:
    """Plain data carrier passed across the worker → repo boundary.

    Frozen to make it suitable for use in `dict`/`set` semantics inside
    the worker (deduplication of identical references from overlapping rule
    pattern matches).
    """

    snapshot_sha: str
    file_path: str
    symbol_name: str
    line_number: int
    kind: CodeReferenceKind


class CodeReferencesRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def replace_file_references(
        self,
        *,
        snapshot_sha: str,
        file_path: str,
        references: list[CodeReferenceRow],
    ) -> None:
        """Atomically replace all references for (snapshot_sha, file_path).

        Empty `references` list is legal — it's a pure delete of any prior
        rows for that file. The session is NOT committed here; the caller
        (worker) commits when its overall handle() transaction completes.
        """
        await self._session.execute(
            delete(CodeReferenceRecord).where(
                CodeReferenceRecord.snapshot_sha == snapshot_sha,
                CodeReferenceRecord.file_path == file_path,
            )
        )
        if references:
            await self._session.execute(
                insert(CodeReferenceRecord),
                [
                    {
                        "snapshot_sha": r.snapshot_sha,
                        "file_path": r.file_path,
                        "symbol_name": r.symbol_name,
                        "line_number": r.line_number,
                        "kind": r.kind,
                    }
                    for r in references
                ],
            )

    async def count_by_symbol(
        self,
        *,
        symbol_name: str,
        snapshot_sha: str,
    ) -> int:
        """Total count of references to `symbol_name` in `snapshot_sha` across
        all files and kinds."""
        result = await self._session.execute(
            select(func.count())
            .select_from(CodeReferenceRecord)
            .where(
                CodeReferenceRecord.symbol_name == symbol_name,
                CodeReferenceRecord.snapshot_sha == snapshot_sha,
            )
        )
        return int(result.scalar_one())

    async def count_by_symbol_and_kind(
        self,
        *,
        symbol_name: str,
        snapshot_sha: str,
    ) -> dict[str, int]:
        """Per-kind count of references for one symbol in one snapshot.

        Returns a dict keyed by `kind`. Kinds with zero references are NOT
        included in the result; callers should `.get(kind, 0)` rather than
        indexing directly.
        """
        result = await self._session.execute(
            select(CodeReferenceRecord.kind, func.count())
            .where(
                CodeReferenceRecord.symbol_name == symbol_name,
                CodeReferenceRecord.snapshot_sha == snapshot_sha,
            )
            .group_by(CodeReferenceRecord.kind)
        )
        return {row[0]: int(row[1]) for row in result.all()}
