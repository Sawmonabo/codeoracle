# tests/integration/slice/conftest.py
"""Session-scoped fixtures for the slice integration suite.

Four layers:

1. `pytest-docker` plugin owns compose lifecycle. The plugin reads
   `docker_compose_file` / `docker_compose_command` to find the overlay
   and brings the stack up at session start; tears down at session end.
2. `compose_stack` session-scoped fixture yields once every service's
   healthcheck has passed (`pytest-docker.wait_until_responsive`).
3. `api_client` (httpx.AsyncClient @ host:8080) + `mcp_base_url`
   (URL string for the MCP SDK client) — both pinned at the overlay's
   published host ports.
4. `db_conn` session-scoped `asyncpg.Connection` bound to the compose
   Postgres at host port 5432. `clean_snapshot` function-scoped fixture
   uses it to TRUNCATE the current snapshot / index / trace tables +
   AGE graph state between tests. Per DECIDE-U the cleanup path is
   direct DB I/O, NOT an admin HTTP endpoint — admin routes would
   violate invariant 1 and create silent-skip risk in CI.

The host ports (8080 / 8082 / 5432) are stable per the overlay —
`pytest-docker`'s `get_docker_services` fixture exposes container IPs,
but the overlay publishes fixed host ports for simplicity (slice scope;
no concurrent test runs on the same host).
"""

import socket
from collections.abc import AsyncIterator
from pathlib import Path

import asyncpg
import httpx
import pytest
import pytest_asyncio

from codeoracle.core.config import Settings, get_settings

_REPO_ROOT = Path(__file__).resolve().parents[3]
_API_BASE_URL = "http://127.0.0.1:8080"
_MCP_BASE_URL = "http://127.0.0.1:8082"

# Compose Postgres is published on host port 5432 by the base compose
# (Plan 2). The credentials are the dev defaults the overlay sets in
# the api/mcp/agent/worker env
# (CODEORACLE_DB__URL=postgresql+asyncpg://codeoracle:
# codeoracle@postgres:5432/codeoracle).
_DB_HOST = "127.0.0.1"
_DB_PORT = 5432
_DB_USER = "codeoracle"
_DB_PASS = "codeoracle"
_DB_NAME = "codeoracle"


@pytest.fixture(scope="session")
def docker_compose_file() -> list[str]:
    """`pytest-docker` reads this to find the compose stack(s) to bring up."""
    return [
        str(_REPO_ROOT / "infra" / "compose" / "docker-compose.yml"),
        str(_REPO_ROOT / "infra" / "compose" / "docker-compose.e2e.yml"),
    ]


@pytest.fixture(scope="session")
def docker_compose_project_name() -> str:
    """Stable project name so logs are inspectable post-run."""
    return "codeoracle-slice-e2e"


@pytest.fixture(scope="session")
def compose_stack(docker_services) -> None:
    """Yield once every service is responsive.

    `docker_services` is the `pytest-docker` fixture that brings up the
    stack. We add per-service health probes here (in addition to the
    docker-level healthchecks in the overlay) so the test session never
    yields against a half-ready stack.
    """
    docker_services.wait_until_responsive(
        timeout=180,
        pause=2,
        check=_all_endpoints_healthy,
    )


def _all_endpoints_healthy() -> bool:
    """Check all three host-side service surfaces are reachable."""
    try:
        with httpx.Client(timeout=2.0) as c:
            api_ok = c.get(f"{_API_BASE_URL}/v1/health").status_code == 200
        return api_ok and _mcp_alive() and _db_alive()
    except httpx.HTTPError, OSError:
        return False


def _mcp_alive() -> bool:
    """Send an MCP `initialize` handshake; 200 means MCP is reachable."""
    body = {
        "jsonrpc": "2.0",
        "id": 0,
        "method": "initialize",
        "params": {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "healthcheck", "version": "0"},
        },
    }
    try:
        r = httpx.post(
            f"{_MCP_BASE_URL}/mcp",
            json=body,
            headers={"Content-Type": "application/json"},
            timeout=3.0,
        )
        return r.status_code == 200
    except httpx.HTTPError, OSError:
        return False


def _db_alive() -> bool:
    """Probe Postgres host-port; succeeds when the DB accepts a connect."""
    try:
        with socket.create_connection((_DB_HOST, _DB_PORT), timeout=2.0):
            return True
    except OSError:
        return False


@pytest_asyncio.fixture(scope="session", loop_scope="session")
async def api_client(compose_stack: None) -> AsyncIterator[httpx.AsyncClient]:
    """httpx.AsyncClient bound to the compose API service.

    `loop_scope="session"` is REQUIRED — pytest-asyncio's default fixture
    loop scope is `function`, so a session-scoped async fixture without
    explicit `loop_scope` would bind the httpx client to function-1's
    event loop and then raise
    `RuntimeError: got Future <...> attached to a different loop`
    on every subsequent function's first call. Requires pytest-asyncio
    >= 0.24 (the version that introduced `loop_scope=`); pinned in Task 14.
    """
    async with httpx.AsyncClient(
        base_url=_API_BASE_URL, timeout=60.0
    ) as client:
        yield client


@pytest.fixture(scope="session")
def mcp_base_url(compose_stack: None) -> str:
    """Base URL for the MCP streamable-HTTP endpoint. Tests using the MCP SDK
    pass this into `streamablehttp_client(url)` directly per the SDK pattern.
    """
    return f"{_MCP_BASE_URL}/mcp"


@pytest_asyncio.fixture(scope="session", loop_scope="session")
async def db_conn(compose_stack: None) -> AsyncIterator[asyncpg.Connection]:
    """Session-scoped asyncpg connection to the compose Postgres.

    Used by clean_snapshot for TRUNCATE between tests and by the
    incremental tests for direct graph-state introspection. Bound to
    the host-published port 5432.

    Fails hard if the connection cannot be established — silent-skip
    here would let CI green a vacuous test run (per advisor finding).

    `loop_scope="session"` is REQUIRED for the same reason api_client
    needs it (see that fixture's docstring). An asyncpg connection is
    bound to the loop on which it was opened; if the loop scope is
    function-default, every test after the first sees
    `RuntimeError: got Future <...> attached to a different loop`.
    """
    conn = await asyncpg.connect(
        host=_DB_HOST,
        port=_DB_PORT,
        user=_DB_USER,
        password=_DB_PASS,
        database=_DB_NAME,
    )
    try:
        yield conn
    finally:
        await conn.close()


# Tables truncated between tests. Keep this synced to current migrations only;
# stale archived tables here make slice setup fail before it reaches the API.
_TRUNCATABLE_TABLES: tuple[str, ...] = (
    "snapshots",
    "snapshot_file_outcomes",
    "embeddings",
    "code_references",
    "traces.spans",
    "traces.ingest_cursor",
)


@pytest.fixture
async def clean_snapshot(db_conn: asyncpg.Connection) -> None:
    """Truncate current snapshot/index/trace tables + AGE graph state.

    Implemented via direct asyncpg (NOT via an admin HTTP endpoint —
    see DECIDE-U). Any connection or truncate failure is HARD-FAIL,
    never silent-skip: a vacuous test run that signs off the slice
    green is the worst possible CI outcome.

    Truncates BEFORE the test yields (so each test starts clean); the
    next test's clean_snapshot resets again. No after-yield cleanup needed.
    """
    # TRUNCATE the catalogue tables.
    table_list = ", ".join(_TRUNCATABLE_TABLES)
    await db_conn.execute(f"TRUNCATE {table_list} RESTART IDENTITY CASCADE")

    # Drop & recreate the AGE graph so vertex/edge tables are empty.
    # AGE persists graph state in dynamically-named tables under the
    # ag_label_vertex / ag_label_edge prefix per `codeoracle` graph; the
    # safest reset is to drop_graph + create_graph the whole graph.
    await db_conn.execute(
        "SELECT * FROM ag_catalog.drop_graph('codeoracle', true)"
    )
    await db_conn.execute(
        "SELECT * FROM ag_catalog.create_graph('codeoracle')"
    )


@pytest.fixture(scope="session")
def settings() -> Settings:
    """Test-side Settings (NOT the same Settings the compose containers use).

    Used by tests that need to read configuration values for assertion-time
    decisions (e.g. polling-budget alignment with
    `_REINDEX_POLL_BUDGET_SECONDS`
    in `_helpers.py`, or stack-side defaults during ceremony recording).
    """
    return get_settings()
