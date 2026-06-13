"""Test the BaseSettings loading + URL construction."""

from pathlib import Path

import pytest
from pydantic import SecretStr, ValidationError

from codeoracle.core.config import (
    DBConfig,
    EmbeddingConfig,
    Settings,
    get_settings,
)


@pytest.fixture(autouse=True)
def _clear_settings_cache():
    """Bust the get_settings lru_cache before AND after each test so cached
    Settings from one test cannot leak into another."""
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def test_db_async_url_format():
    cfg = DBConfig.model_validate(
        {
            "host": "db",
            "port": 5432,
            "user": "u",
            "password": "p",
            "database": "d",
        }
    )
    assert isinstance(
        cfg.password, SecretStr
    )  # D5-4: secret type, no plain-str default
    # D5-2: async_url() unwraps via get_secret_value() — the driver needs the
    # literal
    assert cfg.async_url() == "postgresql+asyncpg://u:p@db:5432/d"


def test_db_sync_url_format():
    cfg = DBConfig.model_validate(
        {
            "host": "db",
            "port": 5432,
            "user": "u",
            "password": "p",
            "database": "d",
        }
    )
    assert cfg.sync_url() == "postgresql+psycopg://u:p@db:5432/d"


def test_embedding_dim_must_be_positive():
    with pytest.raises(ValidationError, match="greater than or equal to 64"):
        EmbeddingConfig(model="text-embedding-3-small", dim=0)


def test_embedding_dim_within_bounds():
    cfg = EmbeddingConfig(model="text-embedding-3-small", dim=1536)
    assert cfg.dim == 1536


def test_embedding_dim_required_no_default(monkeypatch, tmp_path):
    """EmbeddingConfig has no default — Settings() with no env injection
    must fail rather than silently producing wrong-dim embeddings.

    `chdir(tmp_path)` escapes the repo's `.env` so pydantic-settings can't
    fill in values from the .env file on disk; only direct construction
    matters here, but the chdir keeps the test-suite intent uniform with
    test_settings_fails_without_embedding_env below.
    """
    monkeypatch.chdir(tmp_path)
    with pytest.raises(ValidationError, match="Field required"):
        EmbeddingConfig.model_validate({})


def test_settings_fails_without_embedding_env(monkeypatch, tmp_path):
    """The whole Settings constructor must fail-fast when EmbeddingConfig
    cannot be assembled — protects production from a misconfigured boot.

    Must `chdir(tmp_path)` so pydantic-settings doesn't read
    CODEORACLE_EMBEDDING__*
    out of the repo's `.env` (which the dev flow `cp .env.example .env`
    creates). Otherwise this test silently passes — the worst case.
    """
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("CODEORACLE_EMBEDDING__MODEL", raising=False)
    monkeypatch.delenv("CODEORACLE_EMBEDDING__DIM", raising=False)
    with pytest.raises(ValidationError, match="embedding"):
        Settings.model_validate({})


def test_settings_fails_without_db_password_env(monkeypatch, tmp_path):
    """D5-4: `DBConfig.password` is required with no default — Settings() must
    fail-fast when CODEORACLE_DB__PASSWORD is absent rather than silently
    booting with a
    guessable `codeoracle` credential. CODEORACLE_EMBEDDING__* are set so the
    failure isolates to the missing password, not the embedding precondition.

    `chdir(tmp_path)` escapes the repo's `.env` (which `cp .env.example .env`
    seeds with CODEORACLE_DB__PASSWORD=codeoracle), so the delenv actually
    bites.
    """
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("CODEORACLE_EMBEDDING__MODEL", "text-embedding-3-small")
    monkeypatch.setenv("CODEORACLE_EMBEDDING__DIM", "1536")
    monkeypatch.delenv("CODEORACLE_DB__PASSWORD", raising=False)
    with pytest.raises(ValidationError, match="password"):
        Settings.model_validate({})


def test_settings_loads_with_required_env(monkeypatch):
    monkeypatch.setenv("CODEORACLE_EMBEDDING__MODEL", "text-embedding-3-small")
    monkeypatch.setenv("CODEORACLE_EMBEDDING__DIM", "1536")
    monkeypatch.setenv(
        "CODEORACLE_DB__PASSWORD", "test-db-pw"
    )  # D5-4: required, no default
    s = Settings.model_validate({})
    assert s.environment == "dev"
    assert s.db.host == "localhost"
    assert s.embedding.model == "text-embedding-3-small"
    assert s.embedding.dim == 1536


def test_settings_env_override(monkeypatch):
    monkeypatch.setenv("CODEORACLE_DB__HOST", "remote")
    monkeypatch.setenv("CODEORACLE_DB__PORT", "6543")
    monkeypatch.setenv(
        "CODEORACLE_DB__PASSWORD", "test-db-pw"
    )  # D5-4: required, no default
    monkeypatch.setenv("CODEORACLE_EMBEDDING__MODEL", "text-embedding-3-small")
    monkeypatch.setenv("CODEORACLE_EMBEDDING__DIM", "512")
    # bust the lru_cache so the new env is read
    get_settings.cache_clear()
    s = get_settings()
    assert s.db.host == "remote"
    assert s.db.port == 6543
    assert s.embedding.dim == 512
    get_settings.cache_clear()


def test_db_pool_config_env_override(monkeypatch):
    """Pool sizing is env-tunable so a deployment can fit the aggregate DB
    connection budget under the server's max_connections without a code
    change (D0-1). Each process/container sets its own CODEORACLE_DB__POOL_*
    knobs.
    """
    monkeypatch.setenv("CODEORACLE_DB__POOL_SIZE", "3")
    monkeypatch.setenv("CODEORACLE_DB__MAX_OVERFLOW", "2")
    monkeypatch.setenv("CODEORACLE_DB__POOL_TIMEOUT", "10")
    monkeypatch.setenv(
        "CODEORACLE_DB__PASSWORD", "test-db-pw"
    )  # D5-4: required, no default
    monkeypatch.setenv("CODEORACLE_EMBEDDING__MODEL", "text-embedding-3-small")
    monkeypatch.setenv("CODEORACLE_EMBEDDING__DIM", "1536")
    get_settings.cache_clear()
    s = get_settings()
    assert s.db.pool_size == 3
    assert s.db.max_overflow == 2
    assert s.db.pool_timeout == 10.0
    get_settings.cache_clear()


def test_api_config_defaults(monkeypatch, tmp_path):
    """ApiConfig defaults must match the slice's single-host posture:
    bind 127.0.0.1:8080, outbound agent base url matches AgentConfig
    defaults (127.0.0.1:8081), and the three timeout knobs default to
    values sized for the §8.5 performance table.

    Loaded via Settings() rather than constructed directly, so the
    `Settings.api` Field(default_factory=ApiConfig) wiring is exercised too.

    `chdir(tmp_path)` escapes the repo's `.env` so pydantic-settings
    can't fill `CODEORACLE_API__*` values from disk — same defensive isolation
    L651 + L667 use against the repo's `cp .env.example .env` dev
    convention. Without it, a dev who set `CODEORACLE_API__HOST` in `.env` to
    debug a local API would silently break this test.
    """
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("CODEORACLE_EMBEDDING__MODEL", "text-embedding-3-small")
    monkeypatch.setenv("CODEORACLE_EMBEDDING__DIM", "1536")
    monkeypatch.setenv(
        "CODEORACLE_DB__PASSWORD", "test-db-pw"
    )  # D5-4: required, no default
    s = Settings.model_validate({})
    assert s.api.host == "127.0.0.1"
    assert s.api.port == 8080
    assert s.api.agent_base_url == "http://127.0.0.1:8081"
    assert s.api.agent_request_timeout_seconds == 35.0
    assert s.api.reindex_timeout_seconds == 300.0
    assert s.api.lock_acquisition_timeout_seconds == 5.0


def test_api_config_env_override(monkeypatch, tmp_path):
    """
    `CODEORACLE_API__*` env vars override every ApiConfig field — including the
    string `agent_base_url` (multi-host reactivation trigger per Plan 10
    Deviation 9) and the three float timeouts (Pydantic coerces from str).

    `chdir(tmp_path)` is defensive — `monkeypatch.setenv` overrides OS
    env, which pydantic-settings prefers over `.env` file values, so a
    `.env` setting wouldn't actually beat our setenv here. The chdir
    keeps test-suite intent uniform with `test_api_config_defaults`
    above; both tests fully control their input env.
    """
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("CODEORACLE_EMBEDDING__MODEL", "text-embedding-3-small")
    monkeypatch.setenv("CODEORACLE_EMBEDDING__DIM", "1536")
    monkeypatch.setenv(
        "CODEORACLE_DB__PASSWORD", "test-db-pw"
    )  # D5-4: required, no default
    monkeypatch.setenv("CODEORACLE_API__HOST", "0.0.0.0")
    monkeypatch.setenv("CODEORACLE_API__PORT", "9090")
    monkeypatch.setenv(
        "CODEORACLE_API__AGENT_BASE_URL", "http://agent.internal:8081"
    )
    monkeypatch.setenv("CODEORACLE_API__AGENT_REQUEST_TIMEOUT_SECONDS", "60.0")
    monkeypatch.setenv("CODEORACLE_API__REINDEX_TIMEOUT_SECONDS", "600.0")
    monkeypatch.setenv(
        "CODEORACLE_API__LOCK_ACQUISITION_TIMEOUT_SECONDS", "10.0"
    )
    s = Settings.model_validate({})
    assert s.api.host == "0.0.0.0"
    assert s.api.port == 9090
    assert s.api.agent_base_url == "http://agent.internal:8081"
    assert s.api.agent_request_timeout_seconds == 60.0
    assert s.api.reindex_timeout_seconds == 600.0
    assert s.api.lock_acquisition_timeout_seconds == 10.0


def test_indexing_config_defaults(monkeypatch, tmp_path):
    """IndexingConfig defaults: repo_root resolves to the process CWD,
    extractor_kinds is the four-extractor default set. Loaded via
    Settings() so Settings.indexing Field(default_factory=IndexingConfig)
    wiring is exercised.

    `chdir(tmp_path)` is doubly important here:
      1. Escapes the repo's `.env` (same as the ApiConfig tests).
      2. Resolves the `repo_root` default to `tmp_path` rather than the
         repo — so the assertion is deterministic regardless of where
         the test runs (CI / dev / sub-worktree).
    """
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("CODEORACLE_EMBEDDING__MODEL", "text-embedding-3-small")
    monkeypatch.setenv("CODEORACLE_EMBEDDING__DIM", "1536")
    monkeypatch.setenv(
        "CODEORACLE_DB__PASSWORD", "test-db-pw"
    )  # D5-4: required, no default
    s = Settings.model_validate({})
    # `.resolve()` on both sides — on macOS pytest's `tmp_path` is under
    # `/var/folders/...` but `Path.cwd()` resolves through
    # `/private/var/folders/...`; raw equality fails without resolution.
    assert s.indexing.repo_root.resolve() == Path(tmp_path).resolve()
    assert s.indexing.extractor_kinds == [
        "joern",
        "scip",
        "opengrep",
        "embed",
    ]


def test_indexing_config_env_override(monkeypatch, tmp_path):
    """`CODEORACLE_INDEXING__*` env vars override IndexingConfig fields.

    `extractor_kinds` is parsed as a JSON list per pydantic-settings'
    default behavior for `list[str]` fields. The Plan 6 extension shape
    (`["joern","scip","opengrep","embed"]`) is the canonical override
    payload — pin it as the test fixture so a Plan 6 author who breaks
    the parse here learns immediately.
    """
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("CODEORACLE_EMBEDDING__MODEL", "text-embedding-3-small")
    monkeypatch.setenv("CODEORACLE_EMBEDDING__DIM", "1536")
    monkeypatch.setenv(
        "CODEORACLE_DB__PASSWORD", "test-db-pw"
    )  # D5-4: required, no default
    monkeypatch.setenv("CODEORACLE_INDEXING__REPO_ROOT", "/repo")
    monkeypatch.setenv(
        "CODEORACLE_INDEXING__EXTRACTOR_KINDS",
        '["joern","scip","opengrep","embed"]',
    )
    s = Settings.model_validate({})
    assert s.indexing.repo_root == Path("/repo")
    assert s.indexing.extractor_kinds == ["joern", "scip", "opengrep", "embed"]
