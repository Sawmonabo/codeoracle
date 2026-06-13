# tests/unit/agent/conftest.py
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from opentelemetry import trace as ot_trace

from codeoracle.adapters.llm.config import MockLLMProviderConfig
from codeoracle.adapters.llm.providers.mock import MockLLMProvider
from codeoracle.models.query import QueryResult, QueryStatus


@pytest.fixture(autouse=True)
def _required_settings_env(monkeypatch):
    """
    Plan 3 L381-387: Settings() requires CODEORACLE_EMBEDDING__MODEL +
    CODEORACLE_EMBEDDING__DIM
    (the embedding field has no default — required by env) AND
    CODEORACLE_DB__PASSWORD
    (DBConfig.password is required, no default — NFR D5-4). Without this
    fixture every test that calls Settings() raises ValidationError before the
    assertion phase runs. autouse=True + monkeypatch.setenv means every test in
    tests/unit/agent/ inherits these env vars automatically. The fake engine +
    fake session factory short-circuit real DB I/O, so the password value is
    never used to connect.
    """
    monkeypatch.setenv("CODEORACLE_EMBEDDING__MODEL", "test-embedding-model")
    monkeypatch.setenv("CODEORACLE_EMBEDDING__DIM", "128")
    monkeypatch.setenv(
        "CODEORACLE_DB__PASSWORD", "test-db-pw"
    )  # D5-4: required, no default


@pytest.fixture
def fake_llm() -> MockLLMProvider:
    """
    Plan 4 MockLLMProvider — inherits the default no-op aclose() from the ABC
    (Plan 4 amendment Commit 2). No drain side-effects in tests."""
    return MockLLMProvider(MockLLMProviderConfig(strict=False))


@pytest.fixture
def fake_sourcebot():
    """SourcebotClient stub — aclose() is an async no-op so any lifespan-drain
    invocation doesn't AttributeError. The composer wires this into
    CodeSpanResolver
    + DocParagraphResolver strategies, but stub_runner short-circuits the
    entire request-handling chain (including build_agent_dependencies), so the
    strategies
    never actually call sourcebot. fake_sourcebot exists only to satisfy
    create_test_app's required kwarg.
    """
    sb = MagicMock()
    sb.aclose = AsyncMock(return_value=None)
    return sb


@pytest.fixture
def fake_engine_ok():
    """Engine whose connect() yields an async-CM connection that runs SELECT 1
    successfully — exercises the 200 path in /v1/health."""
    engine = MagicMock()
    conn = AsyncMock()
    conn.__aenter__.return_value = conn
    conn.__aexit__.return_value = None
    conn.execute = AsyncMock(return_value=None)
    engine.connect = MagicMock(return_value=conn)
    engine.dispose = AsyncMock()
    return engine


@pytest.fixture
def fake_engine_fail():
    """Engine whose connect() raises — exercises the 503 path in /v1/health."""
    engine = MagicMock()
    engine.connect = MagicMock(side_effect=Exception("db down"))
    engine.dispose = AsyncMock()
    return engine


@pytest.fixture
def fake_session_factory():
    """
    Async-CM factory. tests/unit/agent only needs the CM to enter/exit cleanly;
    the MagicMock session returned never has methods called on it because
    stub_runner short-circuits build_agent_dependencies."""

    class _CM:
        async def __aenter__(self):
            return MagicMock()

        async def __aexit__(self, exc_type, exc, tb):
            return None

    def factory():
        return _CM()

    return factory


@pytest.fixture
def stub_runner(monkeypatch):
    """
    Stub the full request-handling chain: dep builder, graph builder, and
    runner.
    The handler runs as: open async session → build deps → build graph → run
    runner.
    Stubbing all three lets HTTP tests verify wire-contract behavior without
    touching
    any real repository, service, or LLM call. Plan 13 slice acceptance is the
    end-to-end real-stack test."""

    async def fake_run(graph, query):
        return QueryResult(
            trace_id=uuid4().hex,
            status=QueryStatus.COMPLETE,
            claim=None,
            verdict=None,
            error=None,
        )

    monkeypatch.setattr("codeoracle.agent.server.run_agent_query", fake_run)
    monkeypatch.setattr(
        "codeoracle.agent.server.build_agent_dependencies",
        lambda **_: MagicMock(),
    )
    monkeypatch.setattr(
        "codeoracle.agent.server.build_agent_graph", lambda _: MagicMock()
    )
    return fake_run


@pytest.fixture
def stub_runner_with_node_spans(monkeypatch):
    """Same as stub_runner, but the fake runner opens 2 child spans inside the
    request lifecycle so test_traceparent_propagates_to_node_spans can assert
    real
    propagation (server span + 2 child spans, all sharing one trace_id)."""

    async def fake_run(graph, query):
        tracer = ot_trace.get_tracer("codeoracle.agent")
        with tracer.start_as_current_span("intent.parse"):
            pass
        with tracer.start_as_current_span("verifier.verify"):
            pass
        return QueryResult(
            trace_id=uuid4().hex,
            status=QueryStatus.COMPLETE,
            claim=None,
            verdict=None,
            error=None,
        )

    monkeypatch.setattr("codeoracle.agent.server.run_agent_query", fake_run)
    monkeypatch.setattr(
        "codeoracle.agent.server.build_agent_dependencies",
        lambda **_: MagicMock(),
    )
    monkeypatch.setattr(
        "codeoracle.agent.server.build_agent_graph", lambda _: MagicMock()
    )
    return fake_run
