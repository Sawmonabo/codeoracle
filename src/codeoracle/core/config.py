"""Platform-wide configuration root.

Pydantic Settings reads env vars + .env file. Subsequent settings classes
(LLMConfig in Plan 4, DBConfig here) inherit from BaseSettings so the
same env_prefix + nested-delimiter rules apply uniformly.

Convention: CODEORACLE_<SECTION>__<FIELD>      — env var
    settings.<section>.<field> — Python access

Examples: CODEORACLE_DB__HOST=localhost   → settings.db.host == "localhost"
    CODEORACLE_DB__PASSWORD=secret  → settings.db.password == "secret"
"""

from functools import lru_cache
from pathlib import Path  # 2026-05-26 amendment — IndexingConfig.repo_root
from typing import Any, cast

from pydantic import (
    BaseModel,
    Field,
    SecretStr,
    field_validator,
    model_validator,
)
from pydantic_settings import BaseSettings, SettingsConfigDict

from codeoracle.core.exceptions import ConfigError
from codeoracle.core.extractors import DEFAULT_EXTRACTOR_KINDS


class DBConfig(BaseModel):
    """Connection parameters for the platform's own Postgres database.

    These resolve a single async URL (`postgresql+asyncpg://...`) used by
    db/session.py and a sync URL (`postgresql+psycopg://...`) used by alembic.
    """

    host: str = "localhost"
    port: int = 5432
    user: str = "codeoracle"
    password: SecretStr = Field(
        description=(
            "DB password — required, NO default "
            "(env `CODEORACLE_DB__PASSWORD`). A "
            "hardcoded default ships an insecure credential a misconfigured "
            "deploy silently falls back to; fail fast at Settings() "
            "construction "
            "instead (NFR D5-4). `SecretStr` keeps it out of repr / "
            "`model_dump()` "
            "/ logs (D5-2); `.get_secret_value()` is unwrapped only inside "
            "async_url() / sync_url() where the driver needs the literal."
        ),
    )
    database: str = "codeoracle"

    # Connection-pool budget (env: CODEORACLE_DB__POOL_SIZE /
    # CODEORACLE_DB__MAX_OVERFLOW /
    # CODEORACLE_DB__POOL_TIMEOUT). Explicit + tunable so the aggregate budget
    # across
    # all independently-pooled process types (api, mcp, agent, each worker) can
    # be summed under the server's max_connections per deployment WITHOUT a
    # code
    # change, and each process right-sizes its own pool to real concurrency (a
    # serial looping worker needs far fewer than a fan-out service). Consumed
    # by
    # make_async_engine().
    pool_size: int = Field(default=5, ge=1)
    max_overflow: int = Field(default=10, ge=0)
    pool_timeout: float = Field(default=30.0, gt=0)

    def async_url(self) -> str:
        return (
            f"postgresql+asyncpg://{self.user}:{self.password.get_secret_value()}"
            f"@{self.host}:{self.port}/{self.database}"
        )

    def sync_url(self) -> str:
        return (
            f"postgresql+psycopg://{self.user}:{self.password.get_secret_value()}"
            f"@{self.host}:{self.port}/{self.database}"
        )


class EmbeddingConfig(BaseModel):
    """Embedding model + dimension. Read at boot, baked into pgvector schema.

    Required values — no defaults. These come from infra/versions/pinned.env
    via env injection in docker-compose / CI / dev shells. Defaults here would
    silently mismatch the pgvector column dim baked by migration 0002 (which
    is sed-synced from pinned.env, Task 10 Step 3a); a process that boots
    without env injection would happily produce wrong-dim embeddings. Fail
    fast at Settings() construction instead.

    Set via env: `CODEORACLE_EMBEDDING__MODEL`, `CODEORACLE_EMBEDDING__DIM`
    (docker-compose and dev `.env` files derive these from pinned.env).
    """

    model: str = Field(min_length=1)
    dim: int = Field(ge=64, le=4096)
    embed_chunk_lines: int = Field(default=100, ge=1)
    embed_chunk_overlap: int = Field(default=20, ge=0)
    doc_chunk_max_lines: int = Field(default=200, ge=1)
    kind: str = "openai"
    base_url: str = "https://api.openai.com"
    api_key: str = ""


class AgentConfig(BaseModel):
    """Bind host/port for the agent's FastAPI HTTP surface (Plan 10 Task 11).

    Defaults are appropriate for single-host slice deployment. Set via env:
    `CODEORACLE_AGENT__HOST`, `CODEORACLE_AGENT__PORT`. `shared_token` is
    reserved for
    Slice+1 reactivation per Plan 10 Deviation 9 (currently deferred per
    agent-localhost-trust posture).
    """

    host: str = "127.0.0.1"
    port: int = 8081
    shared_token: SecretStr | None = (
        None  # secret — masked in repr/logs (D5-1)
    )


class ApiConfig(BaseModel):
    """Bind host/port for the API process's FastAPI HTTP surface (Plan 11)
    and outbound configuration for the API → agent HTTP call.

    Defaults are appropriate for single-host slice deployment. Set via env:
    `CODEORACLE_API__HOST`, `CODEORACLE_API__PORT`,
    `CODEORACLE_API__AGENT_BASE_URL`,
    `CODEORACLE_API__AGENT_REQUEST_TIMEOUT_SECONDS`,
    `CODEORACLE_API__REINDEX_TIMEOUT_SECONDS`,
    `CODEORACLE_API__LOCK_ACQUISITION_TIMEOUT_SECONDS`.

    `agent_base_url` is the API's outbound URL to the agent process —
    a deliberately separate setting from `AgentConfig.host`/`port`
    (which is the agent's BIND address). Default `http://127.0.0.1:8081`
    matches the agent's local-bind defaults; override for the multi-host
    reactivation trigger named in Plan 10 Deviation 9.

    `agent_request_timeout_seconds` sets the `httpx.AsyncClient` total
    timeout for `POST /v1/query` calls to the agent — sized to the
    slice's p99 query target (§8.5 performance table: 30s) plus
    headroom. Connect-vs-read timeout split is the client's concern;
    this is the wall-clock cap.

    `reindex_timeout_seconds` is vestigial after the 2026-05-26 sync→async
    pivot (Plan 11 amendment `fc0894a`). It formerly sized uvicorn's
    `--timeout-keep-alive` so a synchronous `POST /v1/reindex` could block
    until the cold-3K-LOC reindex completed; post-pivot the route enqueues
    via `IndexingService.enqueue_reindex` and returns the PENDING `Snapshot`
    in <50ms, so uvicorn uses its framework-default keep-alive and this field
    no longer gates any request (Plan 11 invariant 11 amended). Retained for
    now to avoid a wider `Settings`/env-schema change; flagged for removal in
    the Plan 3 reconciliation that owns the field definition + its tests.

    `lock_acquisition_timeout_seconds` is the advisory-lock wait window
    for the reindex single-flight. Post the 2026-05-26 sync→async pivot
    (Plan 11 amendment `fc0894a`; Plan 5 amendment `40885bf`),
    `pg_advisory_lock(0x46AE0001)` is acquired and held by the
    `ReindexOrchestratorConsumer` worker process — NOT on any API
    request thread. The API's `POST /v1/reindex` calls
    `IndexingService.enqueue_reindex`, which never touches the lock, so it
    cannot raise `AdvisoryLockTimeoutError` and never returns HTTP 409
    (Plan 11 invariant 4 amended). This value tunes the worker's lock-wait
    window, not an API-side contract.

    `shared_token` is intentionally NOT exposed here — the API process
    authenticates TO the agent using `AgentConfig.shared_token`
    (Plan 10 Deviation 9, currently None / deferred). Inbound user→API
    auth is deferred to Phase 7 (spec §8.6); no inbound-auth field is
    provisioned at slice scope.
    """

    host: str = "127.0.0.1"
    port: int = 8080
    agent_base_url: str = "http://127.0.0.1:8081"
    agent_request_timeout_seconds: float = 35.0
    reindex_timeout_seconds: float = 300.0
    lock_acquisition_timeout_seconds: float = 5.0


class McpConfig(BaseModel):
    """Bind host/port for the MCP streamable HTTP transport."""

    host: str = "127.0.0.1"
    port: int = 8082


class IndexingConfig(BaseModel):
    """Process-wide indexing configuration consumed by Plan 5's
    ``IndexingService`` (worker-side reindex orchestrator entrypoint —
    `src/codeoracle/workers/reindex_orchestrator/__main__.py`) and Plan 5's
    `codeoracle index` CLI fallback.

    2026-05-26 amendment — added alongside `ApiConfig` to source the
    `repo_root` + `extractor_kinds` inputs that `IndexingService.__init__`
    requires. Prior to this amendment, the CLI took `--repo-root` as a
    Typer option (defaulting to CWD) and hardcoded `extractor_kinds=("joern",)`
    at the call site; the worker entrypoint (added by the 2026-05-26
    Plan 11 async-job pivot — see Plan 5 Task 13a) has no equivalent flag,
    so the values must come through Settings.

    `repo_root` defaults to ``Path.cwd()`` to mirror the CLI's ``--repo-root``
    default. In container deployments the working directory is set by the
    image to the repo mount point, so the default is correct without
    explicit env override; override with ``CODEORACLE_INDEXING__REPO_ROOT``
    when the worker process runs outside the repo tree. Set via env:
    ``CODEORACLE_INDEXING__REPO_ROOT``,
    ``CODEORACLE_INDEXING__EXTRACTOR_KINDS``.

    `extractor_kinds` defaults to ``("joern", "scip", "opengrep", "embed")``.
    Stored here as
    ``list[str]`` because ``pydantic-settings`` parses comma-or-JSON-list
    env strings into ``list`` natively; callers convert to ``tuple`` if
    they want hashability — see the reindex_orchestrator entrypoint.

    NOT here: ``lock_acquisition_timeout_seconds`` — that lives on
    ``ApiConfig`` because the value was originally chosen by the API's
    per-request lock-wait contract (HTTP 409 mapping). Post-2026-05-26
    amendment the lock is held by the worker process, but the value
    itself stays on ``ApiConfig`` to avoid a Slice+1-incompatible env-var
    rename (``CODEORACLE_API__LOCK_ACQUISITION_TIMEOUT_SECONDS`` is already
    committed). The reindex_orchestrator entrypoint reads it from
    ``settings.api.lock_acquisition_timeout_seconds`` accordingly.
    """

    repo_root: Path = Field(default_factory=Path.cwd)
    extractor_kinds: list[str] = Field(
        default_factory=lambda: list(DEFAULT_EXTRACTOR_KINDS)
    )


class TracesConfig(BaseModel):
    """Tempo-backed traces ingestion configuration."""

    tempo_url: str = "http://localhost:3200"
    tempo_timeout_seconds: float = Field(default=5.0, gt=0)
    tick_interval_seconds: float = Field(default=30.0, gt=0)
    search_limit: int = Field(default=50, ge=1, le=1000)
    max_backoff_seconds: float = Field(default=60.0, gt=0)
    cursor_overlap_seconds: int = Field(default=10, ge=0)

    @field_validator("tempo_url")
    @classmethod
    def _tempo_url_is_non_empty(cls, value: str) -> str:
        if value == "":
            raise ValueError("min_length")
        return value

    @model_validator(mode="after")
    def _validate_backoff_window(self) -> TracesConfig:
        if self.max_backoff_seconds < self.tick_interval_seconds:
            raise ValueError(
                "max_backoff_seconds must be >= tick_interval_seconds"
            )
        return self


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="CODEORACLE_",
        env_nested_delimiter="__",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    environment: str = Field(
        default="dev",
        description=(
            "One of: dev, ci, prod. Affects logging format and log "
            "level defaults."
        ),
    )
    log_level: str = Field(
        default="INFO",
        description=(
            "Root log level (env `CODEORACLE_LOG_LEVEL`) consumed by "
            "`core/logging.py:configure_logging`; a stdlib level name "
            "(DEBUG/INFO/WARNING/ERROR/CRITICAL). Defaults to `INFO`."
        ),
    )
    db: DBConfig = Field(
        description=(
            "Required — `password` has no default. Settings() must read "
            "CODEORACLE_DB__PASSWORD from env "
            "(or be given `db=DBConfig(...)`) or fail at construction; "
            "mirrors the EmbeddingConfig fail-fast wiring (D5-4)."
        ),
    )
    embedding: EmbeddingConfig = Field(
        description=(
            "Required — no default. Settings() must read "
            "CODEORACLE_EMBEDDING__MODEL + CODEORACLE_EMBEDDING__DIM "
            "from env (pinned.env-derived) or fail at construction. "
            "See EmbeddingConfig docstring for the fail-fast rationale."
        ),
    )
    agent: AgentConfig = Field(default_factory=AgentConfig)
    api: ApiConfig = Field(default_factory=ApiConfig)
    mcp: McpConfig = Field(default_factory=McpConfig)
    indexing: IndexingConfig = Field(
        default_factory=IndexingConfig
    )  # 2026-05-26 amendment
    traces: TracesConfig = Field(default_factory=TracesConfig)
    worker_instance_id: str = "default"

    @property
    def repo_root(self) -> str:
        """Compatibility accessor for older Plan 11/12 composition roots."""
        return str(self.indexing.repo_root)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """
    Process-wide settings singleton. Lazy + cached so import time stays cheap.
    """
    try:
        settings_cls = cast(Any, Settings)
        return settings_cls()
    except Exception as exc:
        raise ConfigError(f"failed to load settings: {exc}") from exc
