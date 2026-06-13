"""Plan 7 Task 4 — PGCursorStore unit tests.

Uses an in-memory session shim (mock SQLAlchemy) to verify the SQL shape
+ JSON round-trip without spinning up Postgres. The real PG round-trip
is covered by tests/integration/workers/_base/test_cursor_store_pg_int.py.
"""

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from pydantic import BaseModel

from codeoracle.workers._base.cursor_store_pg import PGCursorStore


class _SampleCursor(BaseModel):
    last_unix_nano: int
    overlap_seconds: int = 5


@pytest.mark.asyncio
async def test_get_cursor_returns_none_when_row_absent() -> None:
    session = AsyncMock()
    session.execute = AsyncMock()
    # Simulate "no row found"
    result_mock = MagicMock()
    result_mock.first = MagicMock(return_value=None)
    session.execute.return_value = result_mock

    factory = MagicMock(return_value=_aenter_session(session))

    store = PGCursorStore(session_factory=factory, cursor_type=_SampleCursor)
    out = await store.get_cursor("tempo")
    assert out is None
    session.execute.assert_awaited_once()
    execute_args = session.execute.await_args
    assert execute_args is not None
    sql_text = str(execute_args.args[0])
    assert "FROM traces.ingest_cursor" in sql_text
    assert "WHERE source = :source" in sql_text


@pytest.mark.asyncio
async def test_get_cursor_deserializes_jsonb_blob() -> None:
    session = AsyncMock()
    row_mock = MagicMock()
    row_mock.cursor_data = {
        "last_unix_nano": 1_700_000_000_000_000_000,
        "overlap_seconds": 7,
    }
    result_mock = MagicMock()
    result_mock.first = MagicMock(return_value=row_mock)
    session.execute = AsyncMock(return_value=result_mock)

    factory = MagicMock(return_value=_aenter_session(session))

    store = PGCursorStore(session_factory=factory, cursor_type=_SampleCursor)
    out = await store.get_cursor("tempo")
    assert out == _SampleCursor(
        last_unix_nano=1_700_000_000_000_000_000, overlap_seconds=7
    )


@pytest.mark.asyncio
async def test_get_cursor_raises_on_invalid_blob_shape() -> None:
    session = AsyncMock()
    row_mock = MagicMock()
    row_mock.cursor_data = {"wrong_field": "wrong"}
    result_mock = MagicMock()
    result_mock.first = MagicMock(return_value=row_mock)
    session.execute = AsyncMock(return_value=result_mock)

    factory = MagicMock(return_value=_aenter_session(session))

    store = PGCursorStore(session_factory=factory, cursor_type=_SampleCursor)
    with pytest.raises(ValueError, match="last_unix_nano"):
        await store.get_cursor("tempo")


@pytest.mark.asyncio
async def test_update_cursor_uses_upsert_with_jsonb_cast() -> None:
    session = AsyncMock()
    session.execute = AsyncMock()
    session.begin = MagicMock(return_value=_AsyncContext(None))

    factory = MagicMock(return_value=_aenter_session(session))

    store = PGCursorStore(session_factory=factory, cursor_type=_SampleCursor)
    await store.update_cursor("tempo", _SampleCursor(last_unix_nano=42))

    session.execute.assert_awaited_once()
    execute_args = session.execute.await_args
    assert execute_args is not None
    sql_text = str(execute_args.args[0])
    assert "INSERT INTO traces.ingest_cursor" in sql_text
    assert "ON CONFLICT (source) DO UPDATE" in sql_text
    # JSONB cast is the load-bearing detail — `:data::jsonb` ensures the
    # parameter is recognized as JSONB, not text.
    assert "::jsonb" in sql_text


@pytest.mark.asyncio
async def test_update_cursor_serializes_via_model_dump_json() -> None:
    """Verify the persisted blob is the canonical Pydantic JSON output
    (not an ad-hoc dict.dumps). Future cursor types with custom
    serializers (e.g., datetime → ISO 8601) depend on this."""
    session = AsyncMock()
    session.execute = AsyncMock()
    session.begin = MagicMock(return_value=_AsyncContext(None))

    factory = MagicMock(return_value=_aenter_session(session))

    store = PGCursorStore(session_factory=factory, cursor_type=_SampleCursor)
    cursor = _SampleCursor(last_unix_nano=42, overlap_seconds=8)
    await store.update_cursor("tempo", cursor)

    execute_args = session.execute.await_args
    assert execute_args is not None
    bind_params = execute_args.args[1]
    assert bind_params["source"] == "tempo"
    # Stored as serialized JSON string (consumed by ::jsonb cast)
    assert bind_params["data"] == cursor.model_dump_json()


# -----------------------------------------------------------------------------
# Test helpers — minimal async-context-manager shims so AsyncMock can stand
# in for AsyncSession / async session begin().
# -----------------------------------------------------------------------------


class _AsyncContext:
    def __init__(self, value: Any) -> None:
        self._value = value

    async def __aenter__(self) -> Any:
        return self._value

    async def __aexit__(self, *exc: Any) -> None:
        pass


def _aenter_session(session: Any) -> _AsyncContext:
    return _AsyncContext(session)
