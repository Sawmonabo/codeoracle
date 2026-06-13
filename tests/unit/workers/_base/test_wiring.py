"""Plan 5 Task 5: build_worker_context unit tests."""

import asyncio
import signal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from codeoracle.workers._base.wiring import (
    WorkerContext,
    build_worker_context,
    teardown_worker_context,
)


@pytest.fixture(autouse=True)
def _isolate(monkeypatch, tmp_path):
    """Same pattern as Plan 4: escape repo .env + clear known env vars."""
    monkeypatch.chdir(tmp_path)
    for var in (
        "CODEORACLE_LLM_PROVIDER",
        "CODEORACLE_LLM_OPENAI__API_KEY",
        "CODEORACLE_LLM_OPENAI__MODEL",
        "CODEORACLE_LLM_MOCK__SCRIPTS_DIR",
        "CODEORACLE_LLM_MOCK__STRICT",
        "CODEORACLE_VALKEY__URL",
        "CODEORACLE_VALKEY__POOL_MIN",
        "CODEORACLE_VALKEY__POOL_MAX",
        "CODEORACLE_VALKEY__BLOCK_MS_DEFAULT",
        "CODEORACLE_VALKEY__MAX_IN_FLIGHT_PER_CONSUMER",
        "CODEORACLE_VALKEY__STREAM_MAXLEN",
        "CODEORACLE_EMBEDDING__MODEL",
        "CODEORACLE_EMBEDDING__DIM",
    ):
        monkeypatch.delenv(var, raising=False)
    # Set what the wiring needs
    monkeypatch.setenv("CODEORACLE_VALKEY__URL", "redis://localhost:6379/0")
    monkeypatch.setenv("CODEORACLE_VALKEY__POOL_MIN", "1")
    monkeypatch.setenv("CODEORACLE_VALKEY__POOL_MAX", "10")
    monkeypatch.setenv("CODEORACLE_VALKEY__BLOCK_MS_DEFAULT", "5000")
    monkeypatch.setenv("CODEORACLE_VALKEY__MAX_IN_FLIGHT_PER_CONSUMER", "10")
    monkeypatch.setenv("CODEORACLE_VALKEY__STREAM_MAXLEN", "1000")
    monkeypatch.setenv("CODEORACLE_LLM_PROVIDER", "mock")
    monkeypatch.setenv(
        "CODEORACLE_LLM_MOCK__SCRIPTS_DIR", str(tmp_path / "scripts")
    )
    monkeypatch.setenv("CODEORACLE_LLM_MOCK__STRICT", "true")
    monkeypatch.setenv("CODEORACLE_EMBEDDING__MODEL", "text-embedding-3-small")
    monkeypatch.setenv("CODEORACLE_EMBEDDING__DIM", "1536")
    monkeypatch.setenv(
        "CODEORACLE_DB__PASSWORD", "test-db-pw"
    )  # D5-4: required, no default


async def test_build_worker_context_starts_queue_and_returns_context():
    with (
        patch("codeoracle.workers._base.wiring.ValkeyQueueClient") as qc,
        patch("codeoracle.workers._base.wiring.make_async_engine") as mae,
        patch(
            "codeoracle.workers._base.wiring.make_async_session_factory"
        ) as mks,
    ):
        qc.return_value.start = AsyncMock()
        qc.return_value.aclose = AsyncMock()
        engine_mock = MagicMock(name="async_engine")
        mae.return_value = engine_mock
        mks.return_value = MagicMock(name="session_factory")

        ctx = await build_worker_context(consumer_name_prefix="test_worker")
        assert isinstance(ctx, WorkerContext)
        qc.return_value.start.assert_awaited_once()
        # 2026-05-26 amendment: engine MUST be the one constructed by
        # make_async_engine (not a second one) so workers that need raw
        # engine access (IndexingService) share the same connection pool
        # as the session factory bound to that same engine.
        assert ctx.engine is engine_mock
        mks.assert_called_once_with(engine_mock)
        # consumer_name includes pid suffix
        assert ctx.consumer_name.startswith("test_worker-")
        assert ctx.consumer_name != "test_worker"
        # stop_event is fresh & unset
        assert isinstance(ctx.stop_event, asyncio.Event)
        assert not ctx.stop_event.is_set()


async def test_teardown_closes_queue_client():
    with (
        patch("codeoracle.workers._base.wiring.ValkeyQueueClient") as qc,
        patch("codeoracle.workers._base.wiring.make_async_engine"),
        patch("codeoracle.workers._base.wiring.make_async_session_factory"),
    ):
        qc.return_value.start = AsyncMock()
        qc.return_value.aclose = AsyncMock()
        ctx = await build_worker_context(consumer_name_prefix="test_worker")
        await teardown_worker_context(ctx)
        qc.return_value.aclose.assert_awaited_once()


async def test_signal_handlers_wired(monkeypatch):
    handlers = []

    def fake_add(sig, cb):
        handlers.append((sig, cb))

    loop = asyncio.get_running_loop()
    monkeypatch.setattr(loop, "add_signal_handler", fake_add)

    with (
        patch("codeoracle.workers._base.wiring.ValkeyQueueClient") as qc,
        patch("codeoracle.workers._base.wiring.make_async_engine"),
        patch("codeoracle.workers._base.wiring.make_async_session_factory"),
    ):
        qc.return_value.start = AsyncMock()
        qc.return_value.aclose = AsyncMock()
        ctx = await build_worker_context(consumer_name_prefix="test_worker")
        # Should have wired SIGTERM + SIGINT
        sigs = {s for s, _ in handlers}
        assert signal.SIGTERM in sigs
        assert signal.SIGINT in sigs
        # Calling the handler sets stop_event
        for _, cb in handlers:
            cb()
        assert ctx.stop_event.is_set()
