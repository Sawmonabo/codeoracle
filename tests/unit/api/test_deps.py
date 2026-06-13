"""Unit tests for API dependency helpers."""

from types import SimpleNamespace
from typing import cast
from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI, Request

from codeoracle.api.dependencies import (
    get_agent_client,
    get_api_resources,
    get_engine,
    get_indexing_service,
    get_session_factory,
    get_settings_dep,
    get_valkey_client,
)
from codeoracle.api.resources import ApiResources
from codeoracle.core.config import Settings


@pytest.fixture
def app_with_state() -> FastAPI:
    """A FastAPI app with the resource container the lifespan creates."""

    resources = ApiResources(
        settings=cast(
            Settings,
            SimpleNamespace(api=SimpleNamespace(host="127.0.0.1")),
        ),
        engine=MagicMock(name="engine"),
        session_factory=MagicMock(name="session_factory"),
        valkey_client=MagicMock(name="valkey_client"),
        agent_client=MagicMock(name="agent_client"),
        indexing_service=MagicMock(name="indexing_service"),
    )
    app = FastAPI()
    app.state.resources = resources
    return app


def _build_request(app: FastAPI) -> Request:
    """Build a Request with the given app — enough surface for the
    helpers, which only read `request.app.state`.
    """
    scope = {"type": "http", "app": app}
    return Request(scope=scope)


def test_get_api_resources_returns_app_state_resources(app_with_state):
    request = _build_request(app_with_state)
    assert get_api_resources(request) is app_with_state.state.resources


def test_get_settings_dep_returns_app_state_settings(app_with_state):
    request = _build_request(app_with_state)
    assert get_settings_dep(request) is app_with_state.state.resources.settings


def test_get_engine_returns_app_state_engine(app_with_state):
    request = _build_request(app_with_state)
    assert get_engine(request) is app_with_state.state.resources.engine


def test_get_session_factory_returns_app_state_session_factory(app_with_state):
    request = _build_request(app_with_state)
    assert (
        get_session_factory(request)
        is app_with_state.state.resources.session_factory
    )


def test_get_valkey_client_returns_app_state_valkey_client(app_with_state):
    request = _build_request(app_with_state)
    assert (
        get_valkey_client(request)
        is app_with_state.state.resources.valkey_client
    )


def test_get_agent_client_returns_app_state_agent_client(app_with_state):
    request = _build_request(app_with_state)
    assert (
        get_agent_client(request)
        is app_with_state.state.resources.agent_client
    )


def test_get_indexing_service_returns_app_state_indexing_service(
    app_with_state,
):
    request = _build_request(app_with_state)
    assert (
        get_indexing_service(request)
        is app_with_state.state.resources.indexing_service
    )


def test_helpers_raise_runtime_error_when_state_unset():
    """If a helper is called before the lifespan ran (or after a wrong
    lifespan branch), the missing-attribute access is a programmer
    error. The helper raises RuntimeError with a clear message naming
    the missing slot — silent None passthrough would surface as a
    confusing AttributeError deep inside a route handler.
    """
    empty_app = FastAPI()
    request = _build_request(empty_app)
    with pytest.raises(RuntimeError, match="resources"):
        get_agent_client(request)
    with pytest.raises(RuntimeError, match="resources"):
        get_indexing_service(request)
    with pytest.raises(RuntimeError, match="resources"):
        get_engine(request)
    with pytest.raises(RuntimeError, match="resources"):
        get_session_factory(request)
    with pytest.raises(RuntimeError, match="resources"):
        get_settings_dep(request)
