"""The load-bearing AGE-per-connection contract test.

Proves both the async (asyncpg) and sync (psycopg) engines honor the
event.listens_for(Engine, "connect") listener and can run cypher() queries
on a freshly-opened connection without "type agtype does not exist" failures.
"""

import pytest
from sqlalchemy import text
from sqlalchemy.engine import Engine
from sqlalchemy.ext.asyncio import AsyncSession

from codeoracle.db.session import make_async_session_factory

pytestmark = pytest.mark.integration


async def test_async_engine_loads_age_on_new_connection(
    async_session: AsyncSession,
):
    """
    Async path: open a session, run a cypher() query, no LOAD 'age' boilerplate
    from the test (the event listener already ran).

    No @pytest.mark.asyncio needed — pyproject.toml sets asyncio_mode = "auto".
    """
    result = await async_session.execute(
        text(
            "SELECT * FROM cypher('codeoracle', $$ RETURN 1 $$) "
            "AS (one agtype);"
        )
    )
    row = result.one()
    # AGE returns an agtype string '1' — check it's coercible
    assert str(row[0]).strip().strip("'\"") in ("1", "1.0")


async def test_async_engine_each_new_connection_independently_loads_age(
    async_engine,
):
    """
    Repeated session opens — each gets a (potentially different) pooled
    connection.
    All of them must be AGE-loaded; pool reuse must not leave a non-AGE
    connection accessible.
    """
    factory = make_async_session_factory(async_engine)
    for _ in range(5):
        async with factory() as session:
            result = await session.execute(
                text(
                    "SELECT * FROM cypher('codeoracle', "
                    "$$ RETURN 'hi' AS s $$) AS (s agtype);"
                )
            )
            result.one()


def test_sync_engine_loads_age_on_new_connection(sync_engine: Engine):
    """Sync path (alembic uses this): open a sync connection and run cypher().
    The same Engine event listener that fired for asyncpg fires here for
    psycopg.
    """
    with sync_engine.connect() as conn:
        result = conn.execute(
            text(
                "SELECT * FROM cypher('codeoracle', $$ RETURN 42 $$) "
                "AS (n agtype);"
            )
        )
        row = result.one()
        assert str(row[0]).strip().strip("'\"") in ("42", "42.0")


def test_sync_engine_repeated_connections_all_age_loaded(sync_engine: Engine):
    """Pool reuse path for the sync engine."""
    for _ in range(5):
        with sync_engine.connect() as conn:
            conn.execute(
                text(
                    "SELECT * FROM cypher('codeoracle', $$ RETURN 1 $$) "
                    "AS (n agtype);"
                )
            ).one()


def test_pgvector_extension_is_available(sync_engine: Engine):
    """
    Bonus — Task 9+ migrations create vector(N) columns; verify vector type
    resolves here.
    """
    with sync_engine.connect() as conn:
        result = conn.execute(
            text("SELECT extname FROM pg_extension WHERE extname='vector'")
        )
        assert result.scalar() == "vector"


def test_search_path_is_set_per_connection(sync_engine: Engine):
    """Verify SET search_path applied — public BEFORE ag_catalog.

    This ordering matters: alembic migrations issue unqualified CREATE TABLE,
    which lands in the first schema where the role has CREATE permission.
    With `public` first, tables land in `public`. With `ag_catalog` first
    (the spec §3.5 original draft), tables would land in ag_catalog — caught
    by Task 12's test_all_three_plan3_tables_present.
    """
    with sync_engine.connect() as conn:
        result = conn.execute(text("SHOW search_path")).scalar()
        assert result is not None
        # Both schemas must be present, public before ag_catalog
        assert "public" in result
        assert "ag_catalog" in result
        # The substring index of 'public' must be less than 'ag_catalog'
        assert result.index("public") < result.index("ag_catalog"), (
            f"public must precede ag_catalog in search_path, got: {result}"
        )
