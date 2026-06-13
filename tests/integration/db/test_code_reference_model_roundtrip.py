"""ORM round-trip test: insert + read a CodeReferenceRecord row."""

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from codeoracle.db.models.code_reference import CodeReferenceRecord

pytestmark = pytest.mark.integration


@pytest.mark.asyncio
async def test_insert_and_read_back(
    async_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with async_session_factory() as session:
        row = CodeReferenceRecord(
            snapshot_sha="sha-roundtrip",
            file_path="a.py",
            symbol_name="my_func",
            line_number=42,
            kind="call",
        )
        session.add(row)
        await session.commit()

        result = await session.execute(
            select(CodeReferenceRecord).where(
                CodeReferenceRecord.snapshot_sha == "sha-roundtrip"
            )
        )
        loaded = result.scalar_one()
        assert loaded.symbol_name == "my_func"
        assert loaded.line_number == 42
        assert loaded.kind == "call"
        assert loaded.file_path == "a.py"
