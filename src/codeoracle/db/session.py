"""
Async + sync SQLAlchemy session factories with the AGE-per-connection contract.

The single `event.listens_for(Engine, "connect")` listener fires on every new
DB-API connection regardless of which SQLAlchemy Engine (async/sync) created
it. This makes `LOAD 'age'` + `SET search_path` a structural property of the DB
layer rather than discipline that can drift.
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import Session, sessionmaker

from codeoracle.core.config import get_settings


@event.listens_for(Engine, "connect")
def _setup_age_per_connection(dbapi_conn, _connection_record) -> None:
    """Every new DB connection loads AGE and sets search_path.

    Required because LOAD and SET search_path are session-scoped in Postgres.
    Fires for BOTH the async-engine (asyncpg) and sync-engine (psycopg) code
    paths because both are SQLAlchemy Engines underneath.

    search_path order: 'public' is FIRST so unqualified CREATE TABLE (alembic
    migrations) lands in public; ag_catalog stays in the path so cypher() and
    other AGE functions resolve unqualified. Mirrors the per-database default
    set by bootstrap_db.sql. Reordering would silently misplace tables.
    """
    with dbapi_conn.cursor() as cur:
        cur.execute("LOAD 'age'")
        cur.execute('SET search_path = "$user", public, ag_catalog')


def make_async_engine() -> AsyncEngine:
    """Create the async engine the app uses.

    Pool sizing comes from DBConfig (env: CODEORACLE_DB__POOL_SIZE /
    CODEORACLE_DB__MAX_OVERFLOW
    / CODEORACLE_DB__POOL_TIMEOUT) so the aggregate connection budget across
    every independently-pooled process replica can be fitted under the server's
    max_connections per deployment without a code change.

    Every process that needs the platform DB (api, mcp, agent, workers) MUST
    build its engine through this factory rather than calling
    create_async_engine directly: importing this module registers the AGE
    LOAD/search_path connect listener (above), so routing through here is what
    guarantees the listener fires for that process's connections.
    """
    settings = get_settings()
    return create_async_engine(
        settings.db.async_url(),
        pool_size=settings.db.pool_size,
        max_overflow=settings.db.max_overflow,
        pool_timeout=settings.db.pool_timeout,
        pool_pre_ping=True,
    )


def make_sync_engine() -> Engine:
    """Create the sync engine alembic uses."""
    settings = get_settings()
    return create_engine(
        settings.db.sync_url(),
        pool_size=2,
        max_overflow=5,
        pool_pre_ping=True,
    )


def make_async_session_factory(
    engine: AsyncEngine,
) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(
        engine, class_=AsyncSession, expire_on_commit=False
    )


def make_sync_session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(engine, expire_on_commit=False)


@asynccontextmanager
async def session_scope(engine: AsyncEngine) -> AsyncIterator[AsyncSession]:
    """
    Async session context manager — opens, commits on success, rolls back on
    exception.
    """
    factory = make_async_session_factory(engine)
    async with factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
