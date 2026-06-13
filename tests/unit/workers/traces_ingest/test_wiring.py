"""Plan 7 Task 11 — wiring composition-root tests.

Verifies that wire_consumer() returns a fully-constructed
TracesIngestConsumer with the right collaborators wired in.
Does NOT exercise actual ingest — that's the E2E test in Task 12.
"""

import pathlib

import pytest

from codeoracle.core.config import get_settings
from codeoracle.core.exceptions import ConfigError
from codeoracle.workers.traces_ingest.consumer import TracesIngestConsumer
from codeoracle.workers.traces_ingest.wiring import wire_consumer


@pytest.mark.asyncio
async def test_wire_consumer_returns_traces_ingest_consumer(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
) -> None:
    monkeypatch.chdir(tmp_path)
    # Minimal env kit for Settings(). DBConfig defaults host/port/user/database
    # but password is required (no default, D5-4) — set CODEORACLE_DB__PASSWORD
    # here;
    # make_async_engine() never actually connects during wiring construction,
    # so the value is unused even on a host with no PG.
    monkeypatch.setenv("CODEORACLE_EMBEDDING__MODEL", "text-embedding-3-small")
    monkeypatch.setenv("CODEORACLE_EMBEDDING__DIM", "1536")
    monkeypatch.setenv(
        "CODEORACLE_DB__PASSWORD", "test-db-pw"
    )  # D5-4: required, no default
    monkeypatch.setenv(
        "CODEORACLE_TRACES__TEMPO_URL", "http://tempo.test:3200"
    )

    # Bust the get_settings() lru_cache so the new env above is picked up
    # (this test deliberately constructs a fresh Settings via wire_consumer's
    # `get_settings()` call inside; without cache_clear, an earlier test
    # could leak its Settings into this one).
    get_settings.cache_clear()

    consumer, cleanup = await wire_consumer()
    try:
        assert isinstance(consumer, TracesIngestConsumer)
    finally:
        await cleanup()


@pytest.mark.asyncio
async def test_wire_consumer_raises_without_tempo_url(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("CODEORACLE_EMBEDDING__MODEL", "x")
    monkeypatch.setenv("CODEORACLE_EMBEDDING__DIM", "1536")
    monkeypatch.setenv(
        "CODEORACLE_DB__PASSWORD", "test-db-pw"
    )  # D5-4: isolate failure to the missing TEMPO_URL
    monkeypatch.delenv("CODEORACLE_TRACES__TEMPO_URL", raising=False)

    get_settings.cache_clear()

    with pytest.raises(ConfigError, match="traces"):
        await wire_consumer()
