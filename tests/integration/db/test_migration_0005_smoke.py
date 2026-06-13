"""
Smoke test: alembic upgrade head applies 0005 cleanly on a fresh testcontainers
PG.

This test does NOT assert specific column types in detail — those are exercised
by the ORM round-trip test (test_code_reference_model_roundtrip.py). It only
asserts the table exists, has the expected composite PK, and has the two
expected indexes.

Fixtures (Plan 3):
- `alembic_config_for_test_pg`: yields a configured alembic Config bound to a
  fresh testcontainers postgres instance.
- `async_session_factory`: factory bound to the SAME instance after upgrade.
"""

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

pytestmark = pytest.mark.integration


@pytest.mark.asyncio
async def test_code_references_table_created(
    async_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with async_session_factory() as session:
        result = await session.execute(
            text(
                """
                SELECT column_name, data_type, is_nullable
                FROM information_schema.columns
                WHERE table_schema = 'public'
                  AND table_name = 'code_references'
                ORDER BY ordinal_position
                """
            )
        )
        rows = result.all()

    by_name = {r[0]: (r[1], r[2]) for r in rows}
    assert "snapshot_sha" in by_name
    assert "file_path" in by_name
    assert "symbol_name" in by_name
    assert "line_number" in by_name
    assert "kind" in by_name
    # All five columns are NOT NULL (they're in the PK).
    for col in (
        "snapshot_sha",
        "file_path",
        "symbol_name",
        "line_number",
        "kind",
    ):
        assert by_name[col][1] == "NO", (
            f"{col} should be NOT NULL (it's in the PK)"
        )


@pytest.mark.asyncio
async def test_code_references_composite_pk(
    async_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with async_session_factory() as session:
        result = await session.execute(
            text(
                """
                SELECT kcu.column_name
                FROM information_schema.table_constraints tc
                JOIN information_schema.key_column_usage kcu
                  ON tc.constraint_name = kcu.constraint_name
                WHERE tc.table_name = 'code_references'
                  AND tc.constraint_type = 'PRIMARY KEY'
                ORDER BY kcu.ordinal_position
                """
            )
        )
        pk_cols = [r[0] for r in result.all()]
    assert pk_cols == [
        "snapshot_sha",
        "file_path",
        "symbol_name",
        "line_number",
        "kind",
    ]


@pytest.mark.asyncio
async def test_code_references_symbol_lookup_index_exists(
    async_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Plan 8's CodeReferencesRepository.count_by_symbol queries by
    (symbol_name, snapshot_sha). An index on this pair must exist."""
    async with async_session_factory() as session:
        result = await session.execute(
            text(
                """
                SELECT indexname FROM pg_indexes
                WHERE schemaname = 'public' AND tablename = 'code_references'
                """
            )
        )
        index_names = {r[0] for r in result.all()}
    # Expected indexes (names from the migration):
    assert "ix_code_references_symbol_lookup" in index_names
    assert "ix_code_references_snapshot" in index_names


@pytest.mark.asyncio
async def test_kind_check_constraint_rejects_invalid_kind(
    async_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """
    The `kind` column has a CHECK constraint pinning the four allowed values.
    """
    async with async_session_factory() as session:
        with pytest.raises(DBAPIError):
            await _insert_invalid_code_reference_kind(session)


async def _insert_invalid_code_reference_kind(session: AsyncSession) -> None:
    await session.execute(
        text(
            """
            INSERT INTO code_references
                (
                    snapshot_sha,
                    file_path,
                    symbol_name,
                    line_number,
                    kind
                )
            VALUES
                ('sha-test', 'a.py', 'foo', 1, 'bogus-kind')
            """
        )
    )
    await session.commit()
