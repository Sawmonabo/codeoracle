"""Integration test fixtures for the API process.

Pattern reuses Plan 5's `tests/integration/conftest.py` (real
PostgresContainer + Valkey RedisContainer), augmented with a
stub-agent FastAPI server that the AgentClient calls. The stub agent
runs in the SAME process as the test, on a different uvicorn port,
because spinning a real Plan 10 agent process would pull in LLM + DB
+ vector + sourcebot — too much for a Plan 11 contract test.
"""

import asyncio
import socket
from collections.abc import AsyncIterator
from typing import Any, cast

import pytest
import pytest_asyncio
import uvicorn
from fastapi import FastAPI

from codeoracle.api.main import create_app
from codeoracle.core.config import Settings, get_settings
from codeoracle.models.evidence import (
    Citation,
    Claim,
    EvidenceHandle,
    HandleType,
    Verdict,
    VerdictStatus,
)
from codeoracle.models.health import HealthResponse
from codeoracle.models.query import Query, QueryResult


@pytest.fixture(scope="session")
def stub_agent_app() -> FastAPI:
    """A minimal FastAPI app that mimics Plan 10's POST /v1/query +
    GET /v1/health. Returns a canned QueryResult; tests verify the
    AgentClient → API round-trip not the LLM behind it.
    """
    app = FastAPI(title="Stub Agent")

    @app.post("/v1/query", response_model=QueryResult)
    async def stub_query(body: Query) -> QueryResult:
        return QueryResult.proven(
            trace_id="00000000000000000000000000000001",
            claim=Claim(
                statement=f"stub answer for: {body.symbol}",
                citations=[
                    Citation(
                        handle=EvidenceHandle(
                            type=HandleType.CODE_SPAN,
                            locator="file://src/codeoracle/core/security.py:1-10",
                            snapshot_sha=body.snapshot_sha or "stub_sha",
                        ),
                        excerpt="def hash_password(raw: str) -> str:",
                        source_trust=9,
                    )
                ],
                confidence=0.9,
            ),
            verdict=Verdict(
                status=VerdictStatus.PROVEN,
                reason="All citations resolved and match cached excerpts.",
                resolved_citations=[],
            ),
        )

    @app.get("/v1/health", response_model=HealthResponse)
    async def stub_health() -> HealthResponse:
        return HealthResponse(status="ok", db="ok")

    return app


@pytest.fixture(scope="session")
def stub_agent_port() -> int:
    """An unused port for the stub agent. The session-scope ensures
    one stub agent per test session.
    """
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest_asyncio.fixture(scope="session")
async def stub_agent_server(
    stub_agent_app, stub_agent_port
) -> AsyncIterator[None]:
    """Run the stub-agent uvicorn server in a background task for the
    whole session.
    """
    config = uvicorn.Config(
        stub_agent_app,
        host="127.0.0.1",
        port=stub_agent_port,
        log_level="warning",
    )
    server = uvicorn.Server(config)
    task = asyncio.create_task(server.serve())
    # Wait for the server to be ready
    for _ in range(50):
        if server.started:
            break
        await asyncio.sleep(0.05)
    yield
    server.should_exit = True
    await task


@pytest_asyncio.fixture
async def api_settings(
    stub_agent_port,
    async_engine,
    monkeypatch,
) -> Settings:
    """Build a Settings instance pointing at the stub agent + the
    testcontainer DB. `async_engine` is Plan 3's existing session-scoped
    integration fixture (an AsyncEngine bound to the bootstrapped test DB);
    depending on it guarantees the schema exists and the container is
    reachable before the API lifespan opens its own pool, and exposes the
    live DB URL components via `async_engine.url`.

    Uses `monkeypatch.setenv` (auto-reverts after the test exits)
    so test-process environment is not permanently mutated.
    """
    monkeypatch.setenv("CODEORACLE_EMBEDDING__MODEL", "text-embedding-3-small")
    monkeypatch.setenv("CODEORACLE_EMBEDDING__DIM", "1536")
    monkeypatch.setenv(
        "CODEORACLE_API__AGENT_BASE_URL", f"http://127.0.0.1:{stub_agent_port}"
    )
    monkeypatch.setenv("CODEORACLE_API__AGENT_REQUEST_TIMEOUT_SECONDS", "5.0")
    monkeypatch.setenv(
        "CODEORACLE_API__LOCK_ACQUISITION_TIMEOUT_SECONDS", "5.0"
    )
    # Point Settings.db at the same DB Plan 3's `async_engine` fixture
    # bootstrapped, by threading its URL components into the CODEORACLE_DB__*
    # fields.
    # CODEORACLE_DB__ASYNC_URL is NOT a settable field — `async_url()` is a
    # computed
    # method, so setting it is silently ignored; the URL must flow through the
    # host/port/user/password/database fields async_url() reads back. The
    # lifespan builds its engine via make_async_engine() → get_settings(), so
    # clear the cached singleton to force a fresh read of this env (Plan 3
    # conftest L605/607/2723 also bust it; this explicit clear matches the
    # corpus after-setenv convention). `.password` attribute access returns the
    # real secret — only str(url)/repr mask it.
    db_url = async_engine.url
    monkeypatch.setenv("CODEORACLE_DB__HOST", db_url.host or "127.0.0.1")
    monkeypatch.setenv("CODEORACLE_DB__PORT", str(db_url.port or 5432))
    monkeypatch.setenv("CODEORACLE_DB__USER", db_url.username or "codeoracle")
    monkeypatch.setenv(
        "CODEORACLE_DB__PASSWORD", db_url.password or "codeoracle"
    )
    monkeypatch.setenv(
        "CODEORACLE_DB__DATABASE", db_url.database or "codeoracle"
    )
    get_settings.cache_clear()
    settings_cls = cast(Any, Settings)
    return cast(Settings, settings_cls())


@pytest_asyncio.fixture
async def api_app(api_settings, stub_agent_server):
    """Build the API app with the stub-agent-pointing settings. The
    lifespan runs against the testcontainer DB + a real Valkey.
    """
    app = create_app(api_settings)
    async with app.router.lifespan_context(app):
        yield app
