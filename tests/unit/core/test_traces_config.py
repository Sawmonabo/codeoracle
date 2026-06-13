"""Plan 7 Task 9 — TracesConfig unit tests.

Verifies the env cascade (CODEORACLE_TRACES__*), defaults, and the
max_backoff >= tick_interval invariant. The pattern mirrors Plan 3 +
Plan 6's EmbeddingConfig tests.
"""

import pathlib

import pytest
from pydantic import ValidationError

from codeoracle.core.config import Settings, TracesConfig


def _tc(**overrides: object) -> TracesConfig:
    base: dict[str, object] = {"tempo_url": "http://tempo.test:3200"}
    base.update(overrides)
    return TracesConfig.model_validate(base)


def test_traces_config_default_tempo_url() -> None:
    assert TracesConfig().tempo_url == "http://localhost:3200"


def test_traces_config_tempo_url_must_be_non_empty() -> None:
    with pytest.raises(ValueError, match="min_length"):
        TracesConfig.model_validate({"tempo_url": ""})


def test_traces_config_defaults() -> None:
    cfg = _tc()
    assert cfg.tempo_timeout_seconds == 5.0
    assert cfg.tick_interval_seconds == 30.0
    assert cfg.search_limit == 50
    assert cfg.max_backoff_seconds == 60.0
    assert cfg.cursor_overlap_seconds == 10


def test_traces_config_rejects_max_backoff_less_than_tick() -> None:
    with pytest.raises(ValueError, match="max_backoff_seconds"):
        _tc(tick_interval_seconds=30.0, max_backoff_seconds=10.0)


def test_traces_config_rejects_non_positive_tick() -> None:
    with pytest.raises(ValueError, match="greater than 0"):
        _tc(tick_interval_seconds=0.0)


def test_traces_config_rejects_search_limit_above_1000() -> None:
    with pytest.raises(ValueError, match="less than or equal to 1000"):
        _tc(search_limit=1001)


def test_traces_config_rejects_negative_overlap() -> None:
    with pytest.raises(ValueError, match="greater than or equal to 0"):
        _tc(cursor_overlap_seconds=-1)


# ----- Env cascade through parent Settings -----
#
# These tests construct `Settings()` directly rather than calling
# `get_settings()`. This is deliberate: `get_settings()` is
# `@lru_cache(maxsize=1)`-decorated (Plan 3 core/config.py:369), so
# successive `monkeypatch.setenv()` calls in a test session would be
# masked by the cache. Direct `Settings()` construction reads the
# current env every time and is the right pattern for env-cascade
# tests. (If a test really needs `get_settings()`, call
# `get_settings.cache_clear()` first — see Plan 3 conftest.py
# line ~430 for the autouse cache-busting fixture.)


def test_env_cascade_for_tempo_url(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
) -> None:
    monkeypatch.chdir(tmp_path)
    # Plan 3's other required env vars must still be set; minimal kit:
    monkeypatch.setenv("CODEORACLE_EMBEDDING__MODEL", "text-embedding-3-small")
    monkeypatch.setenv("CODEORACLE_EMBEDDING__DIM", "1536")
    monkeypatch.setenv(
        "CODEORACLE_DB__PASSWORD", "test-db-pw"
    )  # D5-4: required, no default
    monkeypatch.setenv(
        "CODEORACLE_TRACES__TEMPO_URL", "http://tempo.cluster.local:3200"
    )
    settings = Settings.model_validate({})
    assert settings.traces.tempo_url == "http://tempo.cluster.local:3200"


def test_env_cascade_for_tick_interval(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("CODEORACLE_EMBEDDING__MODEL", "text-embedding-3-small")
    monkeypatch.setenv("CODEORACLE_EMBEDDING__DIM", "1536")
    monkeypatch.setenv(
        "CODEORACLE_DB__PASSWORD", "test-db-pw"
    )  # D5-4: required, no default
    monkeypatch.setenv("CODEORACLE_TRACES__TEMPO_URL", "http://x:3200")
    monkeypatch.setenv("CODEORACLE_TRACES__TICK_INTERVAL_SECONDS", "15.0")
    settings = Settings.model_validate({})
    assert settings.traces.tick_interval_seconds == 15.0


def test_env_cascade_failures_surface(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
) -> None:
    """
    If CODEORACLE_TRACES__TEMPO_URL is not set, Settings() raises at
    construction — fail-fast posture inherited from Plan 3."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("CODEORACLE_EMBEDDING__MODEL", "text-embedding-3-small")
    monkeypatch.setenv("CODEORACLE_EMBEDDING__DIM", "1536")
    monkeypatch.setenv(
        "CODEORACLE_DB__PASSWORD", "test-db-pw"
    )  # D5-4: isolate failure to the missing TEMPO_URL
    monkeypatch.delenv("CODEORACLE_TRACES__TEMPO_URL", raising=False)
    with pytest.raises(ValidationError, match="traces"):
        Settings.model_validate({})
