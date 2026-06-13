"""Plan 7 Task 7 — verify migration 0006 creates the expected schema.

Reuses the `async_session_factory` fixture introduced in Plan 5 Task 1
Step 7 (`tests/integration/conftest.py`). Preconditions before pytest:
  1. `docker compose -f infra/compose/docker-compose.yml up -d`
  2. `bash infra/scripts/bootstrap_db.sh`
  3. `uv run alembic upgrade head`  (applies 0001 + 0002 + 0003)
This is the same external-prereq pattern Plan 3 Task 12 established —
no programmatic alembic call from inside the test (Plan 3 deliberately
keeps the alembic CLI as the only public migration surface)."""

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

pytestmark = [pytest.mark.traces, pytest.mark.integration]


@pytest.mark.asyncio
async def test_schema_traces_exists(async_session_factory) -> None:
    async with async_session_factory() as session:
        result = await session.execute(
            text(
                "SELECT schema_name "
                "FROM information_schema.schemata "
                "WHERE schema_name = 'traces'"
            )
        )
        assert result.scalar_one() == "traces"


@pytest.mark.asyncio
async def test_spans_table_shape(async_session_factory) -> None:
    """Every column must exist with the expected type."""
    async with async_session_factory() as session:
        result = await session.execute(
            text("""
            SELECT column_name, data_type, is_nullable
            FROM information_schema.columns
            WHERE table_schema = 'traces' AND table_name = 'spans'
            ORDER BY ordinal_position
        """)
        )
        rows = list(result.all())

    cols = {r.column_name: (r.data_type, r.is_nullable) for r in rows}
    assert cols["trace_id"] == ("bytea", "NO")
    assert cols["span_id"] == ("bytea", "NO")
    assert cols["parent_span_id"] == ("bytea", "YES")
    assert cols["service_name"] == ("text", "NO")
    assert cols["start_unix_nano"] == ("bigint", "NO")
    assert cols["end_unix_nano"] == ("bigint", "NO")
    assert cols["status_code"] == ("text", "NO")
    assert cols["status_message"] == ("text", "YES")
    assert cols["attributes"] == ("jsonb", "NO")
    assert cols["scope_name"] == ("text", "YES")
    assert cols["scope_version"] == ("text", "YES")
    assert cols["ingested_at"] == ("timestamp with time zone", "NO")


@pytest.mark.asyncio
async def test_spans_pk_is_composite(async_session_factory) -> None:
    async with async_session_factory() as session:
        result = await session.execute(
            text("""
            SELECT a.attname
            FROM pg_index i
            JOIN pg_attribute a
              ON a.attrelid = i.indrelid
             AND a.attnum = ANY(i.indkey)
            JOIN pg_class c ON c.oid = i.indrelid
            JOIN pg_namespace n ON n.oid = c.relnamespace
            WHERE i.indisprimary
              AND n.nspname = 'traces'
              AND c.relname = 'spans'
            ORDER BY array_position(i.indkey, a.attnum)
        """)
        )
        pk_cols = [r.attname for r in result.all()]
    assert pk_cols == ["trace_id", "span_id"]


@pytest.mark.asyncio
async def test_spans_indexes_present(async_session_factory) -> None:
    async with async_session_factory() as session:
        result = await session.execute(
            text("""
            SELECT indexname
            FROM pg_indexes
            WHERE schemaname = 'traces' AND tablename = 'spans'
        """)
        )
        index_names = {r.indexname for r in result.all()}
    # We don't assert exact names (PG auto-prefixes the PK) — just presence
    assert any("service_start" in n for n in index_names)
    assert any("start_range" in n or "_range" in n for n in index_names)
    assert any("attributes_gin" in n or "_gin" in n for n in index_names)
    assert any("parent" in n for n in index_names)


@pytest.mark.asyncio
async def test_spans_check_constraints_reject_invalid_rows(
    async_session_factory,
) -> None:
    """Every CHECK constraint must reject bad input."""
    async with async_session_factory() as session:
        # trace_id < 16 bytes → reject
        with pytest.raises(DBAPIError):
            await _execute_rejected_span_insert(
                session,
                """
                INSERT INTO traces.spans
                    (
                        trace_id,
                        span_id,
                        service_name,
                        operation_name,
                        kind,
                        start_unix_nano,
                        end_unix_nano
                    )
                VALUES (
                    E'\\x0102', E'\\x0102030405060708', 'api', 'op',
                    'server', 1, 2
                )
                """,
            )
        # status_code='error' without message → reject
        with pytest.raises(DBAPIError):
            await _execute_rejected_span_insert(
                session,
                """
                INSERT INTO traces.spans
                    (
                        trace_id,
                        span_id,
                        service_name,
                        operation_name,
                        kind,
                        start_unix_nano,
                        end_unix_nano,
                        status_code,
                        status_message
                    )
                VALUES (
                    E'\\x01010101010101010101010101010101',
                    E'\\x0202020202020202',
                    'api', 'op', 'server', 1, 2, 'error', NULL
                )
                """,
            )
        # kind not in enum → reject
        with pytest.raises(DBAPIError):
            await _execute_rejected_span_insert(
                session,
                """
                INSERT INTO traces.spans
                    (
                        trace_id,
                        span_id,
                        service_name,
                        operation_name,
                        kind,
                        start_unix_nano,
                        end_unix_nano
                    )
                VALUES (
                    E'\\x01010101010101010101010101010101',
                    E'\\x0202020202020202',
                    'api', 'op', 'banana', 1, 2
                )
                """,
            )
        # end < start → reject
        with pytest.raises(DBAPIError):
            await _execute_rejected_span_insert(
                session,
                """
                INSERT INTO traces.spans
                    (
                        trace_id,
                        span_id,
                        service_name,
                        operation_name,
                        kind,
                        start_unix_nano,
                        end_unix_nano
                    )
                VALUES (
                    E'\\x01010101010101010101010101010101',
                    E'\\x0202020202020202',
                    'api', 'op', 'server', 100, 50
                )
                """,
            )


@pytest.mark.asyncio
async def test_ingest_cursor_table_shape(async_session_factory) -> None:
    async with async_session_factory() as session:
        result = await session.execute(
            text("""
            SELECT column_name, data_type, is_nullable
            FROM information_schema.columns
            WHERE table_schema = 'traces' AND table_name = 'ingest_cursor'
            ORDER BY ordinal_position
        """)
        )
        cols = {
            r.column_name: (r.data_type, r.is_nullable) for r in result.all()
        }
    assert cols["source"] == ("text", "NO")
    assert cols["cursor_data"] == ("jsonb", "NO")
    assert cols["updated_at"] == ("timestamp with time zone", "NO")


@pytest.mark.asyncio
async def test_ingest_cursor_pk_is_source(async_session_factory) -> None:
    async with async_session_factory() as session:
        result = await session.execute(
            text("""
            SELECT a.attname FROM pg_index i
            JOIN pg_attribute a
              ON a.attrelid = i.indrelid
             AND a.attnum = ANY(i.indkey)
            JOIN pg_class c ON c.oid = i.indrelid
            JOIN pg_namespace n ON n.oid = c.relnamespace
            WHERE i.indisprimary
              AND n.nspname = 'traces'
              AND c.relname = 'ingest_cursor'
        """)
        )
        pk = [r.attname for r in result.all()]
    assert pk == ["source"]


async def _execute_rejected_span_insert(session, sql: str) -> None:
    async with session.begin():
        await session.execute(text(sql))
