"""Integration-test fixtures.

Every integration test uses these fixtures so DB setup is not reinvented
per file. Tests that need a fresh schema get the `bootstrapped_db` fixture
(session-scoped so docker compose doesn't restart between tests); tests
that need to mutate the schema get the `clean_session` fixture (per-function).
"""

import os
import subprocess
from collections.abc import AsyncIterator, Iterator
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.engine import Engine
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession
from sqlalchemy.sql.elements import TextClause

from codeoracle.core.config import get_settings
from codeoracle.db.session import (
    make_async_engine,
    make_async_session_factory,
    make_sync_engine,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
BOOTSTRAP_SCRIPT = REPO_ROOT / "infra" / "scripts" / "bootstrap_db.sh"


@pytest.fixture(scope="session")
def bootstrapped_db() -> Iterator[None]:
    """Run bootstrap_db.sh once per test session.

    Assumes the docker-compose stack is already up. If
    CODEORACLE_SKIP_BOOTSTRAP=1
    is set (CI may bootstrap externally), skip the subprocess invocation.
    """
    if os.environ.get("CODEORACLE_SKIP_BOOTSTRAP") == "1":
        yield
        return
    # bust the settings cache so any env overrides set by the test run are
    # picked up
    get_settings.cache_clear()
    result = subprocess.run(
        [str(BOOTSTRAP_SCRIPT)],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        pytest.fail(
            f"bootstrap_db.sh failed:\n{result.stdout}\n{result.stderr}"
        )
    yield


@pytest.fixture(scope="session")
def sync_engine(bootstrapped_db) -> Iterator[Engine]:
    engine = make_sync_engine()
    yield engine
    engine.dispose()


@pytest.fixture(scope="session")
def async_engine(bootstrapped_db) -> AsyncEngine:
    return make_async_engine()
    # synchronous dispose is OK for a test-session-scoped fixture


@pytest.fixture
async def async_session(
    async_engine: AsyncEngine,
) -> AsyncIterator[AsyncSession]:
    """Per-test async session with automatic rollback at the end.

    Under `asyncio_mode = "auto"`, plain `@pytest.fixture` on an async
    generator function is what pytest-asyncio adapts. `@pytest_asyncio.fixture`
    still works but emits a DeprecationWarning in newer versions.

    Use for tests that mutate DB state — the rollback keeps tests isolated.
    """
    factory = make_async_session_factory(async_engine)
    async with factory() as session:
        try:
            yield session
        finally:
            await session.rollback()


@pytest.fixture
def cypher_query():
    """
    Helper that builds an AGE cypher query as a SQLAlchemy text() construct.
    """

    def _build(cypher: str, columns: str = "v agtype") -> TextClause:
        return text(
            "SELECT * FROM cypher('codeoracle', "
            f"$$ {cypher} $$) AS ({columns})"
        )

    return _build
