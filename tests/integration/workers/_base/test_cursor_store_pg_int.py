"""Plan 7 Task 4 — PGCursorStore integration tests.

Requires:
  - The traces schema + ingest_cursor table from migration 0006 (Task 7).
    This test sets up the schema directly via DDL so it can run BEFORE
    Task 7 lands the migration runner step. Once migration 0006 ships,
    the conftest fixture can replace the inline DDL with a migrate-up.

Marks: pytest.mark.traces (slow), pytest.mark.integration
"""

import pytest
from pydantic import BaseModel, ValidationError
from sqlalchemy import text

from codeoracle.workers._base.cursor_store_pg import PGCursorStore

pytestmark = [pytest.mark.traces, pytest.mark.integration]


class _IntCursor(BaseModel):
    last_unix_nano: int
    overlap_seconds: int = 5


@pytest.fixture
async def traces_schema(async_session_factory):
    """Bring up the traces.ingest_cursor table for this test module.

    Replaces this with the migration runner once Task 7 lands."""
    async with async_session_factory() as session, session.begin():
        await session.execute(text("CREATE SCHEMA IF NOT EXISTS traces"))
        await session.execute(
            text("""
                CREATE TABLE IF NOT EXISTS traces.ingest_cursor (
                    source       TEXT        PRIMARY KEY,
                    cursor_data  JSONB       NOT NULL,
                    updated_at   TIMESTAMPTZ NOT NULL DEFAULT now()
                )
            """)
        )
    yield
    async with async_session_factory() as session, session.begin():
        await session.execute(
            text("DROP TABLE IF EXISTS traces.ingest_cursor")
        )
        await session.execute(text("DROP SCHEMA IF EXISTS traces"))


@pytest.mark.asyncio
async def test_get_cursor_returns_none_initially(
    async_session_factory, traces_schema
):
    store = PGCursorStore(
        session_factory=async_session_factory, cursor_type=_IntCursor
    )
    assert await store.get_cursor("tempo") is None


@pytest.mark.asyncio
async def test_update_then_get_round_trip(
    async_session_factory, traces_schema
):
    store = PGCursorStore(
        session_factory=async_session_factory, cursor_type=_IntCursor
    )
    await store.update_cursor(
        "tempo", _IntCursor(last_unix_nano=42, overlap_seconds=7)
    )
    out = await store.get_cursor("tempo")
    assert out == _IntCursor(last_unix_nano=42, overlap_seconds=7)


@pytest.mark.asyncio
async def test_update_is_idempotent(async_session_factory, traces_schema):
    store = PGCursorStore(
        session_factory=async_session_factory, cursor_type=_IntCursor
    )
    await store.update_cursor("tempo", _IntCursor(last_unix_nano=42))
    await store.update_cursor(
        "tempo", _IntCursor(last_unix_nano=42)
    )  # same value, no error
    out = await store.get_cursor("tempo")
    assert out == _IntCursor(last_unix_nano=42)


@pytest.mark.asyncio
async def test_update_advances_value(async_session_factory, traces_schema):
    store = PGCursorStore(
        session_factory=async_session_factory, cursor_type=_IntCursor
    )
    await store.update_cursor("tempo", _IntCursor(last_unix_nano=10))
    await store.update_cursor("tempo", _IntCursor(last_unix_nano=20))
    out = await store.get_cursor("tempo")
    assert out is not None
    assert out.last_unix_nano == 20


@pytest.mark.asyncio
async def test_distinct_sources_independent(
    async_session_factory, traces_schema
):
    store = PGCursorStore(
        session_factory=async_session_factory, cursor_type=_IntCursor
    )
    await store.update_cursor("tempo", _IntCursor(last_unix_nano=10))
    await store.update_cursor("loki", _IntCursor(last_unix_nano=99))
    tempo = await store.get_cursor("tempo")
    loki = await store.get_cursor("loki")
    assert tempo is not None
    assert tempo.last_unix_nano == 10
    assert loki is not None
    assert loki.last_unix_nano == 99


@pytest.mark.asyncio
async def test_invalid_blob_raises_on_read(
    async_session_factory, traces_schema
):
    """If a future schema migration leaves a row with wrong shape, the
    Pydantic validator should surface it instead of silently returning
    None or a default."""
    # Inject a row with bad shape via raw SQL
    async with async_session_factory() as session, session.begin():
        await session.execute(
            text("""
                INSERT INTO traces.ingest_cursor
                    (source, cursor_data, updated_at)
                VALUES ('bad', '{"wrong_field": "x"}'::jsonb, now())
            """)
        )

    store = PGCursorStore(
        session_factory=async_session_factory, cursor_type=_IntCursor
    )
    with pytest.raises(ValidationError, match="last_unix_nano"):
        await store.get_cursor("bad")
