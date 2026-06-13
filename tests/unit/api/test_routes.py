"""Unit tests for API route handlers."""

from types import SimpleNamespace
from typing import cast
from unittest.mock import AsyncMock, MagicMock

from fastapi import FastAPI
from fastapi.testclient import TestClient

from codeoracle.api.resources import ApiResources
from codeoracle.api.v1.routes.health import router as health_router
from codeoracle.core.config import Settings

# ---- /v1/health ------------------------------------------------------


def _build_app_with_resources(session_factory, valkey_client) -> FastAPI:
    """Mount the health router on an app with stub API resources."""

    app = FastAPI()
    app.state.resources = ApiResources(
        settings=cast(Settings, SimpleNamespace()),
        engine=MagicMock(),
        session_factory=session_factory,
        valkey_client=valkey_client,
        agent_client=MagicMock(),
        indexing_service=MagicMock(),
    )
    app.include_router(health_router, prefix="/v1")
    return app


class _SessionFactory:
    def __init__(self, session):
        self._session = session

    def __call__(self):
        return _SessionCtx(self._session)


class _SessionCtx:
    def __init__(self, session):
        self._session = session

    async def __aenter__(self):
        return self._session

    async def __aexit__(self, *args):
        return None


def _db_session(value: int = 1):
    mock_session = AsyncMock()
    mock_result = MagicMock()
    mock_result.scalar_one.return_value = value
    mock_session.execute = AsyncMock(return_value=mock_result)
    return mock_session


def _valkey_client():
    client = MagicMock(name="valkey_client")
    client.ping = AsyncMock(return_value=None)
    return client


def test_liveness_does_not_require_resources():
    app = FastAPI()
    app.include_router(health_router, prefix="/v1")
    client = TestClient(app)

    r = client.get("/v1/health/live")

    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


def test_readiness_returns_ok_when_db_and_valkey_ping():
    """Happy path returns ready when DB and Valkey are reachable."""

    valkey_client = _valkey_client()
    app = _build_app_with_resources(
        _SessionFactory(_db_session()),
        valkey_client,
    )
    client = TestClient(app)

    r = client.get("/v1/health/ready")

    assert r.status_code == 200
    assert r.json() == {"status": "ok", "db": "ok", "valkey": "ok"}
    valkey_client.ping.assert_awaited_once()


def test_health_alias_uses_readiness_contract():
    valkey_client = _valkey_client()
    app = _build_app_with_resources(
        _SessionFactory(_db_session()),
        valkey_client,
    )
    client = TestClient(app)

    r = client.get("/v1/health")

    assert r.status_code == 200
    assert r.json() == {"status": "ok", "db": "ok", "valkey": "ok"}


def test_readiness_returns_503_when_db_raises():
    """DB failure makes readiness degraded and returns HTTP 503."""

    mock_session = AsyncMock()
    mock_session.execute = AsyncMock(side_effect=RuntimeError("db down"))
    valkey_client = _valkey_client()

    app = _build_app_with_resources(
        _SessionFactory(mock_session),
        valkey_client,
    )
    client = TestClient(app)

    r = client.get("/v1/health/ready")

    assert r.status_code == 503
    assert r.json() == {"status": "degraded", "db": "fail", "valkey": "ok"}


def test_readiness_returns_503_when_valkey_raises():
    valkey_client = _valkey_client()
    valkey_client.ping = AsyncMock(side_effect=RuntimeError("valkey down"))
    app = _build_app_with_resources(
        _SessionFactory(_db_session()),
        valkey_client,
    )
    client = TestClient(app)

    r = client.get("/v1/health/ready")

    assert r.status_code == 503
    assert r.json() == {"status": "degraded", "db": "ok", "valkey": "fail"}
