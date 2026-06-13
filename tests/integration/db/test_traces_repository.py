"""Plan 7 Task 8 — TracesRepository unit tests.

Verifies the SQL the repository emits matches the expected shape (column
names, indexes hit, parameter binding pattern). The behavioral integration
test (Step 4) covers the actual round-trip against PG.
"""

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from codeoracle.db.repositories.traces import TracesRepository
from codeoracle.models.traces import Span, SpanFilter, TimestampCursor


def _aenter(value: Any) -> Any:
    class _Ctx:
        async def __aenter__(self):
            return value

        async def __aexit__(self, *a):
            return None

    return _Ctx()


def _span(**overrides: object) -> Span:
    base: dict[str, object] = {
        "trace_id": b"\x01" * 16,
        "span_id": b"\x02" * 8,
        "parent_span_id": None,
        "service_name": "api",
        "operation_name": "GET /x",
        "kind": "server",
        "start_unix_nano": 1_700_000_000_000_000_000,
        "end_unix_nano": 1_700_000_000_010_000_000,
        "status_code": "ok",
        "status_message": None,
        "attributes": {},
        "scope_name": None,
        "scope_version": None,
    }
    base.update(overrides)
    return Span.model_validate(base)


def _last_execute_sql(session: AsyncMock) -> str:
    session.execute.assert_awaited()
    await_args = session.execute.await_args
    assert await_args is not None
    return str(await_args.args[0])


@pytest.mark.asyncio
async def test_upsert_empty_list_is_no_op() -> None:
    session = AsyncMock()
    session.execute = AsyncMock()
    factory = MagicMock(return_value=_aenter(session))
    cursor_store = MagicMock()

    repo = TracesRepository(session_factory=factory, cursor_store=cursor_store)
    inserted = await repo.upsert_spans([])
    assert inserted == 0
    session.execute.assert_not_called()


@pytest.mark.asyncio
async def test_upsert_uses_on_conflict_do_nothing() -> None:
    session = AsyncMock()
    result = MagicMock()
    result.rowcount = 1
    session.execute = AsyncMock(return_value=result)
    session.begin = MagicMock(return_value=_aenter(None))
    factory = MagicMock(return_value=_aenter(session))
    cursor_store = MagicMock()

    repo = TracesRepository(session_factory=factory, cursor_store=cursor_store)
    await repo.upsert_spans([_span()])
    sql = _last_execute_sql(session)
    assert "INSERT INTO traces.spans" in sql
    assert "ON CONFLICT (trace_id, span_id) DO NOTHING" in sql


@pytest.mark.asyncio
async def test_find_spans_trace_lookup_uses_pk_prefix() -> None:
    session = AsyncMock()
    result = MagicMock()
    result.all = MagicMock(return_value=[])
    session.execute = AsyncMock(return_value=result)
    factory = MagicMock(return_value=_aenter(session))
    cursor_store = MagicMock()

    repo = TracesRepository(session_factory=factory, cursor_store=cursor_store)
    await repo.find_spans(SpanFilter(trace_id=b"\x01" * 16))

    sql = _last_execute_sql(session)
    assert "FROM traces.spans" in sql
    assert "WHERE trace_id = :trace_id" in sql
    # No service-window predicate
    assert "service_name" not in sql.lower() or "WHERE service_name" not in sql


@pytest.mark.asyncio
async def test_find_spans_service_window_uses_composite_index() -> None:
    session = AsyncMock()
    result = MagicMock()
    result.all = MagicMock(return_value=[])
    session.execute = AsyncMock(return_value=result)
    factory = MagicMock(return_value=_aenter(session))
    cursor_store = MagicMock()

    repo = TracesRepository(session_factory=factory, cursor_store=cursor_store)
    await repo.find_spans(
        SpanFilter(
            service_name="api",
            start_unix_nano=1_000,
            end_unix_nano=2_000,
        )
    )

    sql = _last_execute_sql(session)
    assert "WHERE service_name = :service_name" in sql
    assert "start_unix_nano BETWEEN :start_unix_nano AND :end_unix_nano" in sql


@pytest.mark.asyncio
async def test_find_spans_route_filter_adds_jsonb_predicate() -> None:
    session = AsyncMock()
    result = MagicMock()
    result.all = MagicMock(return_value=[])
    session.execute = AsyncMock(return_value=result)
    factory = MagicMock(return_value=_aenter(session))
    cursor_store = MagicMock()

    repo = TracesRepository(session_factory=factory, cursor_store=cursor_store)
    await repo.find_spans(
        SpanFilter(
            service_name="api",
            start_unix_nano=1_000,
            end_unix_nano=2_000,
            route="/v1/query",
        )
    )

    sql = _last_execute_sql(session)
    # JSONB predicate; @> uses the GIN index
    assert "attributes @>" in sql or "attributes ->> 'http.route'" in sql


@pytest.mark.asyncio
async def test_get_cursor_delegates_to_cursor_store() -> None:
    cursor_store = MagicMock()
    cursor_store.get_cursor = AsyncMock(
        return_value=TimestampCursor(last_ingested_unix_nano=42)
    )

    repo = TracesRepository(
        session_factory=MagicMock(), cursor_store=cursor_store
    )
    out = await repo.get_cursor()
    assert out is not None
    assert out.last_ingested_unix_nano == 42
    cursor_store.get_cursor.assert_awaited_once_with("tempo")


@pytest.mark.asyncio
async def test_update_cursor_delegates_to_cursor_store() -> None:
    cursor_store = MagicMock()
    cursor_store.update_cursor = AsyncMock()

    repo = TracesRepository(
        session_factory=MagicMock(), cursor_store=cursor_store
    )
    cursor = TimestampCursor(last_ingested_unix_nano=42)
    await repo.update_cursor(cursor)
    cursor_store.update_cursor.assert_awaited_once_with("tempo", cursor)
