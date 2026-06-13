# Core Foundation + DB Bootstrap Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Land `src/codeoracle/core/` (exceptions, config, logging), `src/codeoracle/models/` (all pure Pydantic domain types), `src/codeoracle/db/` (session factory with the AGE-per-connection contract + alembic migrations for the application schema), and the docker-compose stack (PG18+AGE, pgvector, Valkey) — all pinned to digests verified in Plan 2 — so Plans 4–7 inherit a database that boots reproducibly and an application schema migrated end-to-end.

**Architecture:** Two-layer database bootstrap — Layer 1 (`infra/scripts/bootstrap_db.sh`) is the SUPERUSER-only one-shot that creates extensions (`age`, `vector`), sets the per-database `search_path`, and calls `create_graph('codeoracle')`. Layer 2 (`src/codeoracle/db/session.py`) is the per-connection SQLAlchemy `Engine` event listener that runs `LOAD 'age'` + `SET search_path` on every new connection (both async app + sync alembic), making AGE context a structural property of the DB layer rather than bootstrap discipline. Alembic migrations carry the *application* schema (snapshots, embeddings, traces) only — they NEVER `CREATE EXTENSION`. Models live in `src/codeoracle/models/` as pure Pydantic; the LLM-cannot-forge-trust invariant is enforced by `DraftClaim`/`Claim` being separate types (only `TrustMatrixService.score_each()` can produce a `Claim`, scheduled for Plan 9).

**Tech Stack:** Python 3.14 · `pydantic` v2 · `pydantic-settings` · `structlog` · `SQLAlchemy` 2.0 async · `asyncpg` (app) · `psycopg` (alembic sync) · `alembic` · `apache/age:release_PG18` · `pgvector` · `valkey` · `docker compose` · `pytest` + `pytest-asyncio`.

**Spec source:** `docs/superpowers/specs/2026-05-21-platform-scaffolding-design.md` §3.2, §3.3, §3.4 (`infra/`), §3.5 (the per-connection AGE contract — load-bearing), §5.4 partial (BaseSettings pattern, *not* LLMConfig — that's Plan 4), §6.1 (evidence models).

**Inherits from Plan 1A:** `src/codeoracle/core/telemetry.py` (OTel SDK initialization) + `infra/compose/docker-compose.yml` (skeleton with the observability service block — Plan 3 appends postgres + valkey). Plan 1A and Plan 3 must both be merged before any process can boot end-to-end.

**Inherits from Plan 2:** `infra/versions/pinned.env` provides `IMAGE_POSTGRES_AGE`, `PGVECTOR_INSTALL_METHOD` ∈ {`preinstalled`, `separate-image`, `custom-dockerfile`} (the canonical install-method key recorded by Plan 2 Task 3 — audit D-2.6 / P0-9), `IMAGE_PGVECTOR` (digest pin, present only in the `separate-image` branch), `IMAGE_VALKEY`, `IMAGE_SOURCEBOT` (recorded/pinned by Plan 2 but deliberately NOT presence-gated here — its first consumer is Plan 8, which wires the `sourcebot` compose service and substitutes `${IMAGE_SOURCEBOT}`; gating an as-yet-unconsumed pin would fail Plan 3 for a dependency it never touches, so it is intentionally absent from the Preconditions Step C `required=()` array, the pgvector grep-gate, and the prints), `OPENAI_EMBEDDING_DIM`, `OPENAI_EMBEDDING_MODEL`, `OPENAI_DEV_MODEL`. Plan 3 fails fast at the preconditions step if any required pin is missing.

**Hands off to:**
- Plan 4 (LLM provider abstraction): inherits `core/config.py`'s `BaseSettings` pattern + `core/logging.py`'s secret-redaction processor (LLM API keys never appear in logs).
- Plan 5 (Worker framework + Joern vertical): inherits `db/session.py` + the application schema + the docker-compose stack (Plan 5 appends worker + joern service entries) + `models/snapshot.py`.
- Plan 6 (Snapshot extractors + embeddings + project-mode): inherits the `Embedding` table + `models/snapshot.py`; writes `embed_indexer` worker rows against the `vector(dim)` column.
- Plan 7 (ContinuousConsumer + traces_ingest worker + TracesRepository): inherits the `traces` table from migration 0003 + `EvidenceHandle`/`HandleType.TRACE_SPAN` from `models/evidence.py`.
- Plan 8 (HandleResolverService + 4 resolvers): inherits the `EvidenceHandle`/`HandleType` model contract.
- Plan 9 (VerifierService + TrustMatrixService): inherits the full `DraftClaim`/`Claim`/`Citation`/`DraftCitation`/`Verdict`/`VerdictStatus`/`TrustLevel`/`SourceKind` type-system invariant.
- Plan 10 (LangGraph agent): inherits all `models/` types + `core/config.py`'s `Settings` + `core/logging.py`'s `configure_logging` + (from Plan 1A) `core/telemetry.py` for OTel SDK initialization.
- Plan 11+ (API + MCP processes): inherits the full DB layer + models + compose stack + telemetry SDK (from Plan 1A).

**Commit cadence:** every task ends with a local commit (`git commit -m "..."` — no push). Plan 3 ends with one push to `origin/main` after Task 13.

**Working tree assumption:** All file paths in this plan are relative to repo root `/home/sabossedgh/dev/codeoracle/`.

---

## Plan-wide preconditions

Before starting Task 1, **three** gates must pass.

### Preconditions Step A: Working tree is clean of unrelated changes

```bash
git status --short
```

Expected: empty, OR only generated orchestration artifacts. Those are uncoupled audit work and stay untouched; the dirty-tree check is about *platform* files.

If any of `src/codeoracle/`, `infra/`, `pyproject.toml`, `tests/`, `.github/workflows/`, or `docs/superpowers/plans/` has uncommitted modifications, STOP and reconcile before starting Plan 3.

### Preconditions Step B: Plan 1 + Plan 1A + Plan 2 artifacts present

```bash
# Plan 1 deliverables
test -f pyproject.toml && echo "OK pyproject.toml"
test -f .github/workflows/ci.yml && echo "OK ci.yml"
test -d src/codeoracle && echo "OK src/codeoracle/ scaffold"
test -f infra/scripts/check_file_length.py && echo "OK check_file_length.py"

# Plan 1A deliverables (Operational Infrastructure — between Plan 1 and Plan 2 in the DAG;
# Plan 3 Task 7 EXTENDS docker-compose.yml; downstream plans IMPORT core/telemetry.py.
# If either is missing, Task 7 would create the file from scratch and silently drop the
# observability service block — exactly the bug this Precondition prevents.)
test -f infra/compose/docker-compose.yml && echo "OK docker-compose.yml (Plan 1A)"
test -f src/codeoracle/core/telemetry.py && echo "OK telemetry.py (Plan 1A)"

# Plan 2 deliverables
test -f infra/versions/pinned.env && echo "OK pinned.env"
test -f docs/verification-report.md && echo "OK verification-report.md"
```

Expected: all eight echo OK. If any FAIL, the prerequisite plan is not done — STOP.

### Preconditions Step C: pinned.env carries the required keys

```bash
set -a
source infra/versions/pinned.env
set +a

required=(
  IMAGE_POSTGRES_AGE
  IMAGE_VALKEY
  OPENAI_EMBEDDING_DIM
  OPENAI_EMBEDDING_MODEL
  OPENAI_DEV_MODEL
)

missing=()
for var in "${required[@]}"; do
  if [ -z "${!var}" ]; then
    missing+=("$var")
  fi
done

if [ ${#missing[@]} -ne 0 ]; then
  echo "Plan 2 did not pin: ${missing[*]}"
  echo "Re-run those Plan 2 tasks before starting Plan 3."
  exit 1
fi

# pgvector install method is recorded under PGVECTOR_INSTALL_METHOD ∈
# {preinstalled, separate-image, custom-dockerfile} — the canonical key
# Plan 2 Task 3 writes (audit D-2.6 / P0-9 converged). This gate verifies
# BOTH presence and value-set membership: a stale or typo'd value (e.g., the
# legacy PGVECTOR_SOURCE=preinstalled-in-age-image string) breaks docker-
# compose downstream, and catching it at the contract boundary is strictly
# more rigorous than catching it at compose-up. The value drives Plan 3's
# downstream docker-compose / Dockerfile handling.
grep -qE '^PGVECTOR_INSTALL_METHOD=(preinstalled|separate-image|custom-dockerfile)$' infra/versions/pinned.env || {
  echo "Plan 2 Task 3 did not record a valid PGVECTOR_INSTALL_METHOD value."
  echo "Expected one of: preinstalled, separate-image, custom-dockerfile."
  echo "Actual line(s):"
  grep -E '^PGVECTOR_INSTALL_METHOD=' infra/versions/pinned.env || echo "  (key absent — re-run Plan 2 Task 3 Step 3)"
  exit 1
}

echo "All required Plan 2 pins present."
echo "  IMAGE_POSTGRES_AGE     = $IMAGE_POSTGRES_AGE"
echo "  IMAGE_VALKEY           = $IMAGE_VALKEY"
echo "  OPENAI_EMBEDDING_DIM   = $OPENAI_EMBEDDING_DIM"
echo "  OPENAI_EMBEDDING_MODEL = $OPENAI_EMBEDDING_MODEL"
echo "  OPENAI_DEV_MODEL       = $OPENAI_DEV_MODEL"
```

Expected: prints all five values and the "All required Plan 2 pins present" line. Note: `OPENAI_*` pins are present even when Plan 2 Task 10 returned UNKNOWN (no API key); only the verdict is unknown, the dim + model name are recorded for schema use.

If anything fails, STOP. Plan 3 will not be able to migrate without the dim + cannot compose without the image pins.

---

## Task 1: Add Plan 3 runtime dependencies to `pyproject.toml`

**What this task delivers:** the new runtime + dev dependencies Plan 3 introduces, locked into `uv.lock`. After this task, `uv sync` produces a venv with Pydantic v2, SQLAlchemy 2.0 async, alembic, asyncpg, psycopg, structlog. No new code yet — just dependency surface.

**Files:**
- Modify: `pyproject.toml`
- Regenerated: `uv.lock`

- [ ] **Step 1: Read current `pyproject.toml`** to confirm the existing `[project] dependencies = []` block (Plan 1 left this empty)

```bash
grep -A 1 'dependencies = \[\]' pyproject.toml
```

Expected: shows `dependencies = []` from Plan 1.

- [ ] **Step 2: Replace the empty dependencies list with Plan 3's runtime deps**

Edit `pyproject.toml` — locate the `[project]` table's `dependencies = []` and replace with:

```toml
dependencies = [
    # Pydantic v2 — used everywhere in models/ and core/config.py
    "pydantic>=2.7,<3.0",
    "pydantic-settings>=2.3,<3.0",
    # Structured logging — JSON in prod, dev console in local
    "structlog>=24.1,<25.0",
    # DB layer — async app + sync alembic
    "sqlalchemy[asyncio]>=2.0.30,<3.0",
    "asyncpg>=0.29,<1.0",
    "psycopg[binary]>=3.1,<4.0",  # alembic uses sync psycopg
    "alembic>=1.13,<2.0",
    # pgvector Python types — needed for Pydantic ↔ SQLAlchemy ↔ pgvector wiring
    "pgvector>=0.3,<0.4",
]
```

- [ ] **Step 3: Add Plan 3's dev/test dependencies**

In the existing `[dependency-groups]` table's `dev = [...]` list (Plan 1 left this with pytest etc.), append:

```toml
    "pytest-postgresql>=6.0",  # ephemeral PG for migration tests
    "testcontainers[postgres,redis]>=4.7",  # docker compose'd PG18+AGE + Valkey in integration tests
```

The full updated block reads:

```toml
[dependency-groups]
dev = [
    "pytest>=8.0",
    "pytest-asyncio>=0.23",
    "pytest-cov>=5.0",
    "ruff>=0.6.0",
    "ty>=0.0.1",
    "pre-commit>=3.7",
    "pytest-postgresql>=6.0",
    "testcontainers[postgres,redis]>=4.7",
]
```

- [ ] **Step 4: Ensure pytest config registers Plan 3's marker + asyncio mode**

Check whether Plan 1 already configured these. View the current `[tool.pytest.ini_options]` block:

```bash
grep -A 20 '\[tool\.pytest\.ini_options\]' pyproject.toml || echo "(block missing)"
```

If `markers` or `asyncio_mode` is missing, edit `pyproject.toml` so `[tool.pytest.ini_options]` reads (merge — do not delete unrelated keys Plan 1 may have set, e.g., `addopts`, `testpaths`, `python_files`):

```toml
[tool.pytest.ini_options]
markers = [
    "integration: requires a running docker-compose stack (postgres + valkey)",
]
asyncio_mode = "auto"
testpaths = ["tests"]
```

Rationale:
- `markers = [...]` — without this, `pytestmark = pytest.mark.integration` emits a warning on every test run + `-m integration` selectors silently filter to empty.
- `asyncio_mode = "auto"` — without this, async test functions need explicit `@pytest.mark.asyncio` on every one; with auto-mode, the import-time discovery handles it.

- [ ] **Step 5: Regenerate the lockfile**

```bash
uv lock
```

Expected: `uv.lock` is rewritten (likely +200-400 lines). No errors.

- [ ] **Step 6: Sync the venv to confirm the new deps install cleanly**

```bash
uv sync --frozen
```

Expected: "Resolved N packages" then "Installed M packages." No errors.

- [ ] **Step 7: Smoke-test pytest discovery still works after the new config**

```bash
uv run pytest --collect-only -q
```

Expected: pytest reports the existing Plan 1 tests (if any) and `0 tests collected` is fine for now. No `PytestUnknownMarkWarning` for `integration`.

- [ ] **Step 8: Commit**

```bash
git add pyproject.toml uv.lock
git commit -m "Plan 3 task 1: add Plan 3 runtime + dev dependencies + pytest config"
```

---

## Task 2: `src/codeoracle/core/exceptions.py` + `src/codeoracle/core/config.py`

**What this task delivers:** the platform-wide exception hierarchy (one base class + a handful of subclasses) and the root `BaseSettings` that all subsequent settings inherit from. After this task, every layer can `raise CodeOracleError(...)` instead of bare `Exception`, and `from codeoracle.core.config import settings` is importable.

**Files:**
- Create: `src/codeoracle/core/__init__.py`
- Create: `src/codeoracle/core/exceptions.py`
- Create: `src/codeoracle/core/config.py`
- Create: `tests/unit/core/__init__.py`
- Create: `tests/unit/core/test_exceptions.py`
- Create: `tests/unit/core/test_config.py`

- [ ] **Step 1: Write `src/codeoracle/core/__init__.py`**

```python
"""Core cross-cutting infrastructure: exceptions, config, logging.

This package holds the platform-wide primitives that every other layer
(models, db, adapters, services, processes) depends on. It must not
import from any other codeoracle.* layer to keep dependency direction inward.
"""
```

- [ ] **Step 2: Write `src/codeoracle/core/exceptions.py`**

```python
"""Platform-wide exception hierarchy.

Every domain-specific exception inherits from CodeOracleError. Process
entrypoints (api, mcp, agent, workers) catch CodeOracleError at the top
level to log + return a structured error response; uncaught builtins
or third-party exceptions bubble up as 500-class infrastructure faults.
"""


class CodeOracleError(Exception):
    """Base exception for all platform-defined errors."""


class ConfigError(CodeOracleError):
    """Raised when configuration is missing, malformed, or self-inconsistent."""


class DatabaseError(CodeOracleError):
    """Raised by db/ for any persistence-layer failure not modeled more precisely."""


class MigrationError(DatabaseError):
    """Raised when alembic migrations fail to apply or rollback safely."""
```

- [ ] **Step 3: Write `src/codeoracle/core/config.py`**

```python
"""Platform-wide configuration root.

Pydantic Settings reads env vars + .env file. Subsequent settings classes
(LLMConfig in Plan 4, DBConfig here) inherit from BaseSettings so the
same env_prefix + nested-delimiter rules apply uniformly.

Convention:
    CODEORACLE_<SECTION>__<FIELD>      — env var
    settings.<section>.<field> — Python access

Examples:
    CODEORACLE_DB__HOST=localhost   → settings.db.host == "localhost"
    CODEORACLE_DB__PASSWORD=secret  → settings.db.password == "secret"
"""

from functools import lru_cache
from pathlib import Path  # 2026-05-26 amendment — IndexingConfig.repo_root

from pydantic import BaseModel, Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

from codeoracle.core.exceptions import ConfigError


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
            "DB password — required, NO default (env `CODEORACLE_DB__PASSWORD`). A "
            "hardcoded default ships an insecure credential a misconfigured "
            "deploy silently falls back to; fail fast at Settings() construction "
            "instead (NFR D5-4). `SecretStr` keeps it out of repr / `model_dump()` "
            "/ logs (D5-2); `.get_secret_value()` is unwrapped only inside "
            "async_url() / sync_url() where the driver needs the literal."
        ),
    )
    database: str = "codeoracle"

    # Connection-pool budget (env: CODEORACLE_DB__POOL_SIZE / CODEORACLE_DB__MAX_OVERFLOW /
    # CODEORACLE_DB__POOL_TIMEOUT). Explicit + tunable so the aggregate budget across
    # all independently-pooled process types (api, mcp, agent, each worker) can
    # be summed under the server's max_connections per deployment WITHOUT a code
    # change, and each process right-sizes its own pool to real concurrency (a
    # serial looping worker needs far fewer than a fan-out service). Consumed by
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

    Set via env: `CODEORACLE_EMBEDDING__MODEL`, `CODEORACLE_EMBEDDING__DIM` (docker-compose
    and dev `.env` files derive these from pinned.env).
    """

    model: str = Field(min_length=1)
    dim: int = Field(ge=64, le=4096)


class AgentConfig(BaseModel):
    """Bind host/port for the agent's FastAPI HTTP surface (Plan 10 Task 11).

    Defaults are appropriate for single-host slice deployment. Set via env:
    `CODEORACLE_AGENT__HOST`, `CODEORACLE_AGENT__PORT`. `shared_token` is reserved for
    Slice+1 reactivation per Plan 10 Deviation 9 (currently deferred per
    agent-localhost-trust posture).
    """

    host: str = "127.0.0.1"
    port: int = 8081
    shared_token: SecretStr | None = None  # secret — masked in repr/logs (D5-1)


class ApiConfig(BaseModel):
    """Bind host/port for the API process's FastAPI HTTP surface (Plan 11)
    and outbound configuration for the API → agent HTTP call.

    Defaults are appropriate for single-host slice deployment. Set via env:
    `CODEORACLE_API__HOST`, `CODEORACLE_API__PORT`, `CODEORACLE_API__AGENT_BASE_URL`,
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
    explicit env override; override with ``CODEORACLE_INDEXING__REPO_ROOT`` when
    the worker process runs outside the repo tree. Set via env:
    ``CODEORACLE_INDEXING__REPO_ROOT``, ``CODEORACLE_INDEXING__EXTRACTOR_KINDS``.

    `extractor_kinds` defaults to ``("joern",)`` matching Plan 5's slice
    baseline. Plan 6 extends the tuple to ``("joern", "scip", "opengrep",
    "embed")`` via env override (the IndexingOrchestrator iterates this
    list to enqueue one message per extractor per file). Stored here as
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
    extractor_kinds: list[str] = Field(default_factory=lambda: ["joern"])


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
        description="One of: dev, ci, prod. Affects logging format and log level defaults.",
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
            "CODEORACLE_DB__PASSWORD from env (or be given `db=DBConfig(...)`) or fail "
            "at construction; mirrors the EmbeddingConfig fail-fast wiring (D5-4)."
        ),
    )
    embedding: EmbeddingConfig = Field(
        description=(
            "Required — no default. Settings() must read CODEORACLE_EMBEDDING__MODEL "
            "+ CODEORACLE_EMBEDDING__DIM from env (pinned.env-derived) or fail at "
            "construction. See EmbeddingConfig docstring for the fail-fast rationale."
        ),
    )
    agent: AgentConfig = Field(default_factory=AgentConfig)
    api: ApiConfig = Field(default_factory=ApiConfig)
    indexing: IndexingConfig = Field(default_factory=IndexingConfig)  # 2026-05-26 amendment


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Process-wide settings singleton. Lazy + cached so import time stays cheap."""
    try:
        return Settings()
    except Exception as exc:
        raise ConfigError(f"failed to load settings: {exc}") from exc
```

- [ ] **Step 4: Write `tests/unit/core/__init__.py`** (empty file — marks the directory as a Python package for test collection)

```python
```

- [ ] **Step 5: Write `tests/unit/core/test_exceptions.py`**

```python
"""Test the exception hierarchy preserves expected inheritance."""

import pytest

from codeoracle.core.exceptions import (
    ConfigError,
    DatabaseError,
    MigrationError,
    CodeOracleError,
)


def test_config_error_is_oracle_error():
    assert issubclass(ConfigError, CodeOracleError)


def test_database_error_is_oracle_error():
    assert issubclass(DatabaseError, CodeOracleError)


def test_migration_error_is_database_error():
    assert issubclass(MigrationError, DatabaseError)
    assert issubclass(MigrationError, CodeOracleError)


def test_can_raise_and_catch_as_oracle_error():
    with pytest.raises(CodeOracleError):
        raise MigrationError("test")
```

- [ ] **Step 6: Write `tests/unit/core/test_config.py`**

```python
"""Test the BaseSettings loading + URL construction."""

import pytest
from pydantic import SecretStr

from codeoracle.core.config import DBConfig, EmbeddingConfig, Settings, get_settings


@pytest.fixture(autouse=True)
def _clear_settings_cache():
    """Bust the get_settings lru_cache before AND after each test so cached
    Settings from one test cannot leak into another."""
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def test_db_async_url_format():
    cfg = DBConfig(host="db", port=5432, user="u", password="p", database="d")
    assert isinstance(cfg.password, SecretStr)  # D5-4: secret type, no plain-str default
    # D5-2: async_url() unwraps via get_secret_value() — the driver needs the literal
    assert cfg.async_url() == "postgresql+asyncpg://u:p@db:5432/d"


def test_db_sync_url_format():
    cfg = DBConfig(host="db", port=5432, user="u", password="p", database="d")
    assert cfg.sync_url() == "postgresql+psycopg://u:p@db:5432/d"


def test_embedding_dim_must_be_positive():
    with pytest.raises(ValueError):
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
    with pytest.raises(ValueError):
        EmbeddingConfig()  # type: ignore[call-arg]


def test_settings_fails_without_embedding_env(monkeypatch, tmp_path):
    """The whole Settings constructor must fail-fast when EmbeddingConfig
    cannot be assembled — protects production from a misconfigured boot.

    Must `chdir(tmp_path)` so pydantic-settings doesn't read CODEORACLE_EMBEDDING__*
    out of the repo's `.env` (which the dev flow `cp .env.example .env`
    creates). Otherwise this test silently passes — the worst case.
    """
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("CODEORACLE_EMBEDDING__MODEL", raising=False)
    monkeypatch.delenv("CODEORACLE_EMBEDDING__DIM", raising=False)
    with pytest.raises(Exception):  # pydantic.ValidationError wrapped, or RuntimeError
        Settings()


def test_settings_fails_without_db_password_env(monkeypatch, tmp_path):
    """D5-4: `DBConfig.password` is required with no default — Settings() must
    fail-fast when CODEORACLE_DB__PASSWORD is absent rather than silently booting with a
    guessable `codeoracle` credential. CODEORACLE_EMBEDDING__* are set so the failure
    isolates to the missing password, not the embedding precondition.

    `chdir(tmp_path)` escapes the repo's `.env` (which `cp .env.example .env`
    seeds with CODEORACLE_DB__PASSWORD=codeoracle), so the delenv actually bites.
    """
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("CODEORACLE_EMBEDDING__MODEL", "text-embedding-3-small")
    monkeypatch.setenv("CODEORACLE_EMBEDDING__DIM", "1536")
    monkeypatch.delenv("CODEORACLE_DB__PASSWORD", raising=False)
    with pytest.raises(Exception):  # ValidationError: db.password required
        Settings()


def test_settings_loads_with_required_env(monkeypatch):
    monkeypatch.setenv("CODEORACLE_EMBEDDING__MODEL", "text-embedding-3-small")
    monkeypatch.setenv("CODEORACLE_EMBEDDING__DIM", "1536")
    monkeypatch.setenv("CODEORACLE_DB__PASSWORD", "test-db-pw")  # D5-4: required, no default
    s = Settings()
    assert s.environment == "dev"
    assert s.db.host == "localhost"
    assert s.embedding.model == "text-embedding-3-small"
    assert s.embedding.dim == 1536


def test_settings_env_override(monkeypatch):
    monkeypatch.setenv("CODEORACLE_DB__HOST", "remote")
    monkeypatch.setenv("CODEORACLE_DB__PORT", "6543")
    monkeypatch.setenv("CODEORACLE_DB__PASSWORD", "test-db-pw")  # D5-4: required, no default
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
    change (D0-1). Each process/container sets its own CODEORACLE_DB__POOL_* knobs."""
    monkeypatch.setenv("CODEORACLE_DB__POOL_SIZE", "3")
    monkeypatch.setenv("CODEORACLE_DB__MAX_OVERFLOW", "2")
    monkeypatch.setenv("CODEORACLE_DB__POOL_TIMEOUT", "10")
    monkeypatch.setenv("CODEORACLE_DB__PASSWORD", "test-db-pw")  # D5-4: required, no default
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
    `Settings.api` Field(default_factory=ApiConfig) wiring is exercised
    too.

    `chdir(tmp_path)` escapes the repo's `.env` so pydantic-settings
    can't fill `CODEORACLE_API__*` values from disk — same defensive isolation
    L651 + L667 use against the repo's `cp .env.example .env` dev
    convention. Without it, a dev who set `CODEORACLE_API__HOST` in `.env` to
    debug a local API would silently break this test.
    """
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("CODEORACLE_EMBEDDING__MODEL", "text-embedding-3-small")
    monkeypatch.setenv("CODEORACLE_EMBEDDING__DIM", "1536")
    monkeypatch.setenv("CODEORACLE_DB__PASSWORD", "test-db-pw")  # D5-4: required, no default
    s = Settings()
    assert s.api.host == "127.0.0.1"
    assert s.api.port == 8080
    assert s.api.agent_base_url == "http://127.0.0.1:8081"
    assert s.api.agent_request_timeout_seconds == 35.0
    assert s.api.reindex_timeout_seconds == 300.0
    assert s.api.lock_acquisition_timeout_seconds == 5.0


def test_api_config_env_override(monkeypatch, tmp_path):
    """`CODEORACLE_API__*` env vars override every ApiConfig field — including the
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
    monkeypatch.setenv("CODEORACLE_DB__PASSWORD", "test-db-pw")  # D5-4: required, no default
    monkeypatch.setenv("CODEORACLE_API__HOST", "0.0.0.0")
    monkeypatch.setenv("CODEORACLE_API__PORT", "9090")
    monkeypatch.setenv("CODEORACLE_API__AGENT_BASE_URL", "http://agent.internal:8081")
    monkeypatch.setenv("CODEORACLE_API__AGENT_REQUEST_TIMEOUT_SECONDS", "60.0")
    monkeypatch.setenv("CODEORACLE_API__REINDEX_TIMEOUT_SECONDS", "600.0")
    monkeypatch.setenv("CODEORACLE_API__LOCK_ACQUISITION_TIMEOUT_SECONDS", "10.0")
    s = Settings()
    assert s.api.host == "0.0.0.0"
    assert s.api.port == 9090
    assert s.api.agent_base_url == "http://agent.internal:8081"
    assert s.api.agent_request_timeout_seconds == 60.0
    assert s.api.reindex_timeout_seconds == 600.0
    assert s.api.lock_acquisition_timeout_seconds == 10.0


def test_indexing_config_defaults(monkeypatch, tmp_path):
    """IndexingConfig defaults: repo_root resolves to the process CWD,
    extractor_kinds is the Plan 5 slice baseline ["joern"]. Loaded via
    Settings() so Settings.indexing Field(default_factory=IndexingConfig)
    wiring is exercised.

    `chdir(tmp_path)` is doubly important here:
      1. Escapes the repo's `.env` (same as the ApiConfig tests).
      2. Resolves the `repo_root` default to `tmp_path` rather than the
         repo — so the assertion is deterministic regardless of where
         the test runs (CI / dev / sub-worktree).
    """
    from pathlib import Path

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("CODEORACLE_EMBEDDING__MODEL", "text-embedding-3-small")
    monkeypatch.setenv("CODEORACLE_EMBEDDING__DIM", "1536")
    monkeypatch.setenv("CODEORACLE_DB__PASSWORD", "test-db-pw")  # D5-4: required, no default
    s = Settings()
    # `.resolve()` on both sides — on macOS pytest's `tmp_path` is under
    # `/var/folders/...` but `Path.cwd()` resolves through
    # `/private/var/folders/...`; raw equality fails without resolution.
    assert s.indexing.repo_root.resolve() == Path(tmp_path).resolve()
    assert s.indexing.extractor_kinds == ["joern"]


def test_indexing_config_env_override(monkeypatch, tmp_path):
    """`CODEORACLE_INDEXING__*` env vars override IndexingConfig fields.

    `extractor_kinds` is parsed as a JSON list per pydantic-settings'
    default behavior for `list[str]` fields. The Plan 6 extension shape
    (`["joern","scip","opengrep","embed"]`) is the canonical override
    payload — pin it as the test fixture so a Plan 6 author who breaks
    the parse here learns immediately.
    """
    from pathlib import Path

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("CODEORACLE_EMBEDDING__MODEL", "text-embedding-3-small")
    monkeypatch.setenv("CODEORACLE_EMBEDDING__DIM", "1536")
    monkeypatch.setenv("CODEORACLE_DB__PASSWORD", "test-db-pw")  # D5-4: required, no default
    monkeypatch.setenv("CODEORACLE_INDEXING__REPO_ROOT", "/repo")
    monkeypatch.setenv(
        "CODEORACLE_INDEXING__EXTRACTOR_KINDS",
        '["joern","scip","opengrep","embed"]',
    )
    s = Settings()
    assert s.indexing.repo_root == Path("/repo")
    assert s.indexing.extractor_kinds == ["joern", "scip", "opengrep", "embed"]
```

- [ ] **Step 7: Run the new tests**

```bash
uv run pytest tests/unit/core/ -v
```

Expected: 18 tests pass (14 in `test_config.py` + 4 in `test_exceptions.py`). If any fail, fix the code in `core/` (not the tests) and re-run.

- [ ] **Step 8: Run the full lint+type gate**

```bash
uv run ruff check src/codeoracle/core/ tests/unit/core/
uv run ruff format --check src/codeoracle/core/ tests/unit/core/
uv run ty check src/codeoracle/core/
```

Expected: all three exit 0.

- [ ] **Step 9: Commit**

```bash
git add src/codeoracle/core/ tests/unit/core/
git commit -m "Plan 3 task 2: core exceptions + Pydantic Settings root"
```

---

## Task 3: `src/codeoracle/core/logging.py` — structlog + secret redaction

**What this task delivers:** the platform-wide logging setup. Two formats — JSON for `ci`/`prod`, console for `dev`. A redaction processor strips any field whose key contains `api_key`, `secret`, `token`, or `password` before the log line is rendered. Configured once at process start by every entrypoint (api, mcp, agent, workers).

**Note:** OTel telemetry is **owned by Plan 1A** (Operational Infrastructure — G7 in the orphan-ownership map). Plan 1A lands `src/codeoracle/core/telemetry.py` alongside the observability stack (OTel Collector + Tempo + Prometheus + Grafana); shipping the SDK initialization without exporter infrastructure to consume the spans would be dead code, so the two ship together. Plan 3's `core/logging.py` is the structlog foundation; Plan 1A's `core/telemetry.py` is a sibling observability module configured alongside it at process start — it does **not** import `core/logging.py` (Plan 1A's build-time-dependencies note removed that spurious `telemetry → logging` edge, which had created a false Plan-1A↔Plan-3 build-order cycle).

**Files:**
- Create: `src/codeoracle/core/logging.py`
- Create: `tests/unit/core/test_logging.py`

- [ ] **Step 1: Write `src/codeoracle/core/logging.py`**

```python
"""Structured logging setup.

Two output formats:
    dev:        Pretty console (structlog.dev.ConsoleRenderer)
    ci / prod:  JSON (structlog.processors.JSONRenderer)

A redaction processor runs before the renderer to strip secret-like
fields. Any key matching the regex /api_key|secret|token|password/
(case-insensitive) is replaced with the literal string '<redacted>'.
"""

from __future__ import annotations

import logging
import re
import sys
from typing import TYPE_CHECKING, Any, TextIO

import structlog

if TYPE_CHECKING:
    # Type-only import — `Settings` is consumed as an annotation; `configure_logging`
    # reads it at runtime by attribute (settings.log_level / settings.environment),
    # so no runtime import of core.config is needed. Keeps the intra-core/ coupling
    # annotation-only (config.py does not import logging.py — no cycle either way).
    from codeoracle.core.config import Settings

_REDACT_KEY_PATTERN = re.compile(r"api_key|secret|token|password", re.IGNORECASE)
_REDACTED = "<redacted>"


def _redact_secrets(
    _logger: Any,
    _method_name: str,
    event_dict: dict[str, Any],
) -> dict[str, Any]:
    """Strip secret-like fields from the event dict before rendering."""
    for key in list(event_dict.keys()):
        if _REDACT_KEY_PATTERN.search(key):
            event_dict[key] = _REDACTED
    return event_dict


def configure_logging(settings: Settings, *, stream: TextIO = sys.stdout) -> None:
    """Configure structlog + stdlib logging from Settings.

    Idempotent: calling twice is harmless (structlog.configure replaces
    state, and force=True rewires basicConfig). Worker / API / MCP
    entrypoints call this once at startup, passing the process Settings.

    The optional keyword-only `stream` overrides the destination for BOTH stdlib
    logging (logging.basicConfig(stream=...)) and structlog (PrintLoggerFactory(file=...)).
    Default stdout matches legacy behavior; the MCP stdio transport (Plan 12) passes
    sys.stderr to keep JSON-RPC framing on stdout clean (Plan 12 invariant 5).

    Level comes from `settings.log_level`; the renderer is chosen by
    `settings.environment` (ConsoleRenderer in dev, JSONRenderer otherwise).
    """
    log_level = getattr(logging, settings.log_level.upper(), logging.INFO)
    # force=True replaces any existing root-logger config so re-invocations
    # (notably from tests that pass a fresh stream buffer between cases)
    # actually rewire the stream. Without force=True, basicConfig is a no-op
    # after the first call and log output goes to the original (often dead) stream.
    logging.basicConfig(
        format="%(message)s",
        stream=stream,
        level=log_level,
        force=True,
    )

    shared_processors: list[structlog.types.Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        _redact_secrets,
    ]

    if settings.environment == "dev":
        renderer: structlog.types.Processor = structlog.dev.ConsoleRenderer(colors=True)
    else:
        renderer = structlog.processors.JSONRenderer()

    structlog.configure(
        processors=[
            *shared_processors,
            renderer,
        ],
        wrapper_class=structlog.make_filtering_bound_logger(log_level),
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(file=stream),
        cache_logger_on_first_use=True,
    )


def get_logger(name: str | None = None) -> structlog.stdlib.BoundLogger:
    """Convenience accessor — modules call this instead of structlog.get_logger directly."""
    return structlog.get_logger(name)


def get_audit_logger(name: str | None = None) -> structlog.stdlib.BoundLogger:
    """Audit logger with a floor fixed at INFO regardless of `settings.log_level`.

    Used for records that must survive at any production verbosity (e.g. the
    per-citation resolution outcome, spec §8.5 crit. 11). Overrides only the
    wrapper class; processors + logger-factory defer to the global config, so
    it shares the redaction/render pipeline and remains observable under
    `structlog.testing.capture_logs()`.
    """
    return structlog.wrap_logger(
        None,
        wrapper_class=structlog.make_filtering_bound_logger(logging.INFO),
        logger_factory_args=(name,) if name is not None else (),
    )
```

- [ ] **Step 2: Write `tests/unit/core/test_logging.py`**

```python
"""Test the structlog setup + secret redaction."""

import io
import json
import logging

import structlog

from codeoracle.core.config import DBConfig, EmbeddingConfig, Settings
from codeoracle.core.logging import configure_logging, get_logger


def _settings(**overrides) -> Settings:
    """Construct a Settings instance hermetically (embedding is required-no-default).

    Pass the required `embedding` + `db` sub-models as constructor args so
    init-args take precedence over the env / `.env` sources — no monkeypatch.chdir
    or CODEORACLE_EMBEDDING__* / CODEORACLE_DB__PASSWORD setenv needed. Override `environment` /
    `log_level` per test.
    """
    base = dict(
        environment="ci",
        log_level="DEBUG",
        embedding=EmbeddingConfig(model="text-embedding-3-small", dim=1536),
        db=DBConfig(password="test-db-pw"),  # D5-4: required, no default — hermetic init arg
    )
    base.update(overrides)
    return Settings(**base)


def _capture_json_log() -> io.StringIO:
    """Reconfigure logging into JSON mode (environment="ci") + capture into a buffer.

    Passes `stream=buf` EXPLICITLY: `configure_logging`'s `stream` default is a
    def-time `sys.stdout`, so monkeypatching `sys.stdout` after import would NOT
    be picked up — the buffer must be threaded through the call.
    """
    buf = io.StringIO()
    configure_logging(_settings(), stream=buf)
    return buf


def test_normal_field_passes_through():
    buf = _capture_json_log()
    get_logger().info("hello", user_id="u123", count=42)
    line = buf.getvalue().strip().splitlines()[-1]
    payload = json.loads(line)
    assert payload["event"] == "hello"
    assert payload["user_id"] == "u123"
    assert payload["count"] == 42


def test_api_key_field_is_redacted():
    buf = _capture_json_log()
    get_logger().info("auth_attempt", api_key="sk-very-secret", user_id="u1")
    line = buf.getvalue().strip().splitlines()[-1]
    payload = json.loads(line)
    assert payload["api_key"] == "<redacted>"
    assert payload["user_id"] == "u1"


def test_token_field_is_redacted():
    buf = _capture_json_log()
    get_logger().info("upstream_call", access_token="abc.def.ghi", endpoint="/v1/chat")
    line = buf.getvalue().strip().splitlines()[-1]
    payload = json.loads(line)
    assert payload["access_token"] == "<redacted>"
    assert payload["endpoint"] == "/v1/chat"


def test_password_field_case_insensitive():
    buf = _capture_json_log()
    get_logger().info("db_connect", DB_Password="pw-shh", host="localhost")
    line = buf.getvalue().strip().splitlines()[-1]
    payload = json.loads(line)
    assert payload["DB_Password"] == "<redacted>"
    assert payload["host"] == "localhost"


def test_configure_logging_second_call_wins():
    """Calling configure_logging twice — the second config must actually take effect.

    Without force=True on logging.basicConfig, the second call no-ops and log
    output still goes to whatever stream the first call captured. This test
    proves the redirection actually happens by:
      1. First-call'ing with stream=A
      2. Second-call'ing with stream=B
      3. Logging once
      4. Asserting B contains output and A does not
    Each call threads its buffer through `stream=` directly (the def-time
    `sys.stdout` default cannot be monkeypatched in after import).
    """
    settings = _settings(log_level="INFO")
    buf_a = io.StringIO()
    configure_logging(settings, stream=buf_a)

    buf_b = io.StringIO()
    configure_logging(settings, stream=buf_b)

    get_logger().info("after_second_configure")

    assert "after_second_configure" in buf_b.getvalue(), (
        f"Second configure_logging did not redirect output. buf_b={buf_b.getvalue()!r}"
    )


def test_dev_mode_uses_console_renderer():
    """Dev mode should not emit JSON; smoke-check via the configured pipeline."""
    configure_logging(_settings(environment="dev", log_level="INFO"))
    # ConsoleRenderer outputs human-readable lines; we just assert it doesn't crash
    logger = get_logger("test")
    logger.info("dev_format_smoke", count=1)


def teardown_module():
    """Reset structlog state so other tests aren't affected."""
    structlog.reset_defaults()
    logging.getLogger().handlers.clear()
```

- [ ] **Step 3: Run the new tests**

```bash
uv run pytest tests/unit/core/test_logging.py -v
```

Expected: 6 tests pass.

- [ ] **Step 4: Run lint + type gate**

```bash
uv run ruff check src/codeoracle/core/logging.py tests/unit/core/test_logging.py
uv run ruff format --check src/codeoracle/core/logging.py tests/unit/core/test_logging.py
uv run ty check src/codeoracle/core/logging.py
```

Expected: all exit 0.

- [ ] **Step 5: Commit**

```bash
git add src/codeoracle/core/logging.py tests/unit/core/test_logging.py
git commit -m "Plan 3 task 3: structlog setup with secret redaction"
```

---

## Task 4: Models — evidence + trust

**What this task delivers:** the load-bearing Pydantic types for the entire Verifier flow (`HandleType`, `EvidenceHandle`, `DraftCitation`/`Citation`, `DraftClaim`/`Claim`, `Verdict`, `VerdictStatus`, `TrustLevel`, `SourceKind`). The DraftClaim/Claim split is the type-system invariant that prevents the LLM from forging `source_trust` — `VerifierService.verify()` (Plan 9) will accept only `Claim`, so the only path is through `TrustMatrixService.score_each()` (also Plan 9).

**Files:**
- Create: `src/codeoracle/models/__init__.py`
- Create: `src/codeoracle/models/evidence.py`
- Create: `src/codeoracle/models/trust.py`
- Create: `tests/unit/models/__init__.py`
- Create: `tests/unit/models/test_evidence.py`
- Create: `tests/unit/models/test_trust.py`

- [ ] **Step 1: Write `src/codeoracle/models/__init__.py`**

```python
"""Pure Pydantic domain types. No I/O. No imports from db/, adapters/, services/.

Public re-exports below let callers do `from codeoracle.models import Claim, Verdict`
without knowing the file layout.
"""

from codeoracle.models.evidence import (
    Citation,
    Claim,
    DraftCitation,
    DraftClaim,
    EvidenceHandle,
    HandleType,
    ResolvedCitation,
    Verdict,
    VerdictStatus,
)
from codeoracle.models.trust import SourceKind, TrustLevel

__all__ = [
    "Citation",
    "Claim",
    "DraftCitation",
    "DraftClaim",
    "EvidenceHandle",
    "HandleType",
    "ResolvedCitation",
    "SourceKind",
    "TrustLevel",
    "Verdict",
    "VerdictStatus",
]
```

- [ ] **Step 2: Write `src/codeoracle/models/trust.py`**

```python
"""Trust-related enums. Consumed by models/evidence.py and (in Plan 9)
by services/trust_matrix.py."""

from enum import Enum


class SourceKind(str, Enum):
    """Where a citation comes from. Distinct from HandleType — same physical
    file can be evidence of different *kinds* (source code, test, doc, schema)."""

    CODE = "code"
    TEST = "test"
    DOC = "doc"
    SCHEMA = "schema"
    TRACE = "trace"


class TrustLevel(int, Enum):
    """Coarse-grained trust bands used in policy decisions.

    Plan 9's TrustMatrixService produces fine-grained 0-10 scores; this enum
    lets services compare those scores against named thresholds without magic
    numbers leaking into business logic.
    """

    UNTRUSTED = 0
    LOW = 3
    MEDIUM = 6
    HIGH = 9
    AUTHORITATIVE = 10
```

- [ ] **Step 3: Write `src/codeoracle/models/evidence.py`**

```python
"""The evidence + verdict types.

The Draft* / non-Draft split is a load-bearing type-system invariant:
the LLM produces Draft{Citation,Claim} which carry no source_trust.
Plan 9's TrustMatrixService.score_each() converts Draft → final by
attaching platform-computed source_trust. Plan 9's VerifierService.verify()
accepts only Claim — so the LLM cannot forge trust scores.
"""

from enum import Enum
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator


class HandleType(str, Enum):
    CODE_SPAN = "code_span"
    GRAPH_QUERY = "graph_query"
    TRACE_SPAN = "trace_span"
    DB_RECORD = "db_record"
    DOC_PARAGRAPH = "doc_paragraph"


class VerdictStatus(str, Enum):
    PROVEN = "PROVEN"
    UNKNOWN = "UNKNOWN"
    REFUTED = "REFUTED"


class EvidenceHandle(BaseModel):
    """A pointer to evidence. Carries enough info to fetch the actual content."""

    model_config = ConfigDict(frozen=True)

    type: HandleType
    locator: str
    snapshot_sha: Annotated[str, StringConstraints(min_length=4, max_length=80)]

    @field_validator("locator")
    @classmethod
    def _scheme_required(cls, v: str) -> str:
        if "://" not in v:
            raise ValueError("EvidenceHandle.locator must include a scheme:// prefix")
        return v


class DraftCitation(BaseModel):
    """LLM-produced citation. No source_trust — that's platform-owned."""

    handle: EvidenceHandle
    excerpt: Annotated[str, StringConstraints(min_length=1)]


class Citation(BaseModel):
    """Post-scoring citation. source_trust is platform-computed by TrustMatrixService.

    The agent's `score` node (Plan 10) is the only producer of Citation instances
    in non-test code. Tests can construct directly.
    """

    handle: EvidenceHandle
    excerpt: Annotated[str, StringConstraints(min_length=1)]
    source_trust: int = Field(ge=0, le=10)


class ResolvedCitation(BaseModel):
    """A Citation paired with the outcome of attempting to fetch its actual content."""

    citation: Citation
    resolved: bool
    matches_excerpt: bool
    actual_content: str | None


class DraftClaim(BaseModel):
    """LLM output. Caller must run TrustMatrixService.score_each() before passing to Verifier."""

    statement: str
    citations: list[DraftCitation] = Field(min_length=1)
    confidence: float = Field(ge=0.0, le=1.0)
    contradictions: list[DraftCitation] = Field(default_factory=list)

    @field_validator("statement")
    @classmethod
    def _not_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("DraftClaim.statement cannot be empty")
        return v


class Claim(BaseModel):
    """The Verifier's input contract. Citations carry platform-owned source_trust."""

    statement: str
    citations: list[Citation] = Field(min_length=1)
    confidence: float = Field(ge=0.0, le=1.0)
    contradictions: list[Citation] = Field(default_factory=list)

    @field_validator("statement")
    @classmethod
    def _not_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("Claim.statement cannot be empty")
        return v


class Verdict(BaseModel):
    """The Verifier's output. PROVEN / UNKNOWN / REFUTED + reason + per-citation outcome."""

    status: VerdictStatus
    reason: str
    resolved_citations: list[ResolvedCitation]
```

- [ ] **Step 4: Write `tests/unit/models/__init__.py`** (empty)

```python
```

- [ ] **Step 5: Write `tests/unit/models/test_trust.py`**

```python
"""Smoke tests for the trust enums."""

from codeoracle.models.trust import SourceKind, TrustLevel


def test_source_kind_values():
    assert SourceKind.CODE.value == "code"
    assert SourceKind.SCHEMA.value == "schema"


def test_trust_level_band_ordering():
    assert TrustLevel.UNTRUSTED.value < TrustLevel.LOW.value
    assert TrustLevel.LOW.value < TrustLevel.MEDIUM.value
    assert TrustLevel.MEDIUM.value < TrustLevel.HIGH.value
    assert TrustLevel.HIGH.value < TrustLevel.AUTHORITATIVE.value


def test_trust_level_is_int_subclass():
    assert int(TrustLevel.HIGH) == 9
```

- [ ] **Step 6: Write `tests/unit/models/test_evidence.py`**

```python
"""Test the evidence model validators + type-system invariants."""

import pytest
from pydantic import ValidationError

from codeoracle.models.evidence import (
    Citation,
    Claim,
    DraftCitation,
    DraftClaim,
    EvidenceHandle,
    HandleType,
    ResolvedCitation,
    Verdict,
    VerdictStatus,
)


def _handle(handle_type: HandleType = HandleType.CODE_SPAN) -> EvidenceHandle:
    return EvidenceHandle(
        type=handle_type,
        locator="file://src/codeoracle/services/verifier/service.py:14-38",
        snapshot_sha="abc1234",
    )


def test_evidence_handle_scheme_required():
    with pytest.raises(ValidationError):
        EvidenceHandle(type=HandleType.CODE_SPAN, locator="no-scheme:path", snapshot_sha="abc1234")


def test_evidence_handle_scheme_accepted():
    h = EvidenceHandle(
        type=HandleType.CODE_SPAN,
        locator="file://path:1-10",
        snapshot_sha="abc1234",
    )
    assert h.locator == "file://path:1-10"


def test_evidence_handle_is_frozen():
    h = _handle()
    with pytest.raises(ValidationError):
        h.locator = "file://other:1-10"


def test_evidence_handle_snapshot_sha_min_length():
    with pytest.raises(ValidationError):
        EvidenceHandle(type=HandleType.CODE_SPAN, locator="file://x:1-2", snapshot_sha="abc")


def test_draft_claim_empty_statement_rejected():
    with pytest.raises(ValidationError):
        DraftClaim(
            statement="   ",
            citations=[DraftCitation(handle=_handle(), excerpt="...")],
            confidence=0.9,
        )


def test_draft_claim_requires_at_least_one_citation():
    with pytest.raises(ValidationError):
        DraftClaim(statement="ok", citations=[], confidence=0.9)


def test_claim_confidence_bounds():
    with pytest.raises(ValidationError):
        Claim(
            statement="ok",
            citations=[Citation(handle=_handle(), excerpt="...", source_trust=10)],
            confidence=1.1,
        )
    with pytest.raises(ValidationError):
        Claim(
            statement="ok",
            citations=[Citation(handle=_handle(), excerpt="...", source_trust=10)],
            confidence=-0.1,
        )


def test_citation_source_trust_bounds():
    with pytest.raises(ValidationError):
        Citation(handle=_handle(), excerpt="...", source_trust=11)
    with pytest.raises(ValidationError):
        Citation(handle=_handle(), excerpt="...", source_trust=-1)


def test_draft_claim_cannot_satisfy_verifier_signature():
    """Type-system invariant: passing a DraftClaim where a Claim is expected is a type error.

    Pydantic itself is duck-typed at runtime, but the Verifier's signature accepts only Claim,
    and DraftCitation lacks source_trust — so a DraftClaim cannot be reused as a Claim without
    going through TrustMatrixService.score_each().
    """
    draft = DraftClaim(
        statement="ok",
        citations=[DraftCitation(handle=_handle(), excerpt="x")],
        confidence=0.9,
    )
    # Pydantic dump confirms the shape difference: DraftClaim's citations have no source_trust
    payload = draft.model_dump()
    assert "source_trust" not in payload["citations"][0]


def test_verdict_construction():
    citation = Citation(handle=_handle(), excerpt="x", source_trust=8)
    resolved = ResolvedCitation(
        citation=citation,
        resolved=True,
        matches_excerpt=True,
        actual_content="x",
    )
    v = Verdict(
        status=VerdictStatus.PROVEN,
        reason="all good",
        resolved_citations=[resolved],
    )
    assert v.status == VerdictStatus.PROVEN
    assert len(v.resolved_citations) == 1
```

- [ ] **Step 7: Run tests + lint + type**

```bash
uv run pytest tests/unit/models/ -v
uv run ruff check src/codeoracle/models/ tests/unit/models/
uv run ruff format --check src/codeoracle/models/ tests/unit/models/
uv run ty check src/codeoracle/models/
```

Expected: 13 tests pass, lint/format/type all exit 0.

- [ ] **Step 8: Commit**

```bash
git add src/codeoracle/models/ tests/unit/models/
git commit -m "Plan 3 task 4: evidence + trust Pydantic models (Draft/Claim type-system invariant)"
```

---

## Task 5: Models — llm + query

**What this task delivers:** the LLM request/response types (`ChatMessage`, `TokenUsage`, `ChatResponse`, `ToolDefinition`, `ToolCall`) and the public query types (`Query`, `QueryResult`, `QueryStatus`). Plan 4 (LLM provider abstraction) builds against `models/llm.py`; Plan 11 (API) builds against `models/query.py`.

**Files:**
- Create: `src/codeoracle/models/llm.py`
- Create: `src/codeoracle/models/query.py`
- Modify: `src/codeoracle/models/__init__.py` (extend re-exports)
- Create: `tests/unit/models/test_llm.py`
- Create: `tests/unit/models/test_query.py`

- [ ] **Step 1: Write `src/codeoracle/models/llm.py`**

```python
"""LLM call types — provider-agnostic.

Every LLMProvider implementation (Plan 4) accepts ChatMessage[] and returns
ChatResponse. Tool calls are modeled here (not OpenAI-specific) so the
abstraction does not leak vendor schema.
"""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

Role = Literal["system", "user", "assistant", "tool"]
FinishReason = Literal["stop", "length", "tool_calls", "content_filter"]


class ToolCall(BaseModel):
    """An LLM-requested tool invocation. Mirrors the OpenAI / Anthropic surface
    without binding to either's JSON shape."""

    id: str
    name: str
    arguments_json: str


class ChatMessage(BaseModel):
    role: Role
    content: str
    tool_call_id: str | None = None
    tool_calls: list[ToolCall] = Field(default_factory=list)


class TokenUsage(BaseModel):
    model_config = ConfigDict(frozen=True)

    input_tokens: int = Field(ge=0)
    output_tokens: int = Field(ge=0)

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens


class ChatResponse(BaseModel):
    content: str
    usage: TokenUsage
    finish_reason: FinishReason
    tool_calls: list[ToolCall] = Field(default_factory=list)


class ToolDefinition(BaseModel):
    """A tool the LLM may call. Schema is JSON-Schema (the dialect both
    OpenAI function-calling and Anthropic tool-use accept)."""

    name: Annotated[str, StringConstraints(min_length=1, max_length=64)]
    description: str
    parameters_schema: dict[str, object]
    """JSON-Schema document. Heterogeneous nested types — `object` is the
    honest annotation; `Any` would lose type-checker signal entirely."""
```

- [ ] **Step 2: Write `src/codeoracle/models/query.py`**

```python
"""The platform's public query types.

Exposed by the API (`POST /v1/query`, Plan 11) and the MCP server's
`explain_symbol` tool (Plan 12). Plan 3 ships the types so the agent (Plan 10)
and the consumer services (Plans 8-9) can build against them.
"""

from enum import Enum
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from codeoracle.models.evidence import Claim, Verdict


class QueryStatus(str, Enum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    COMPLETE = "COMPLETE"
    FAILED = "FAILED"


class Query(BaseModel):
    """Input to /v1/query.

    `type` discriminates the query family. For the slice, only `explain_symbol`
    is supported. Plan 10 (LangGraph agent) will validate `type` against a closed
    enum once the intent classifier lands.
    """

    model_config = ConfigDict(extra="forbid")

    type: Literal["explain_symbol"] = "explain_symbol"
    symbol: Annotated[str, StringConstraints(min_length=1, max_length=512)]
    corpus: Literal["self"] = "self"  # slice supports only the platform's own source
    snapshot_sha: str | None = None  # None → resolve at execution time to current HEAD


class QueryResult(BaseModel):
    """Output of /v1/query."""

    model_config = ConfigDict(extra="forbid")

    trace_id: str
    status: QueryStatus
    claim: Claim | None = None
    verdict: Verdict | None = None
    error: str | None = None

    @classmethod
    def proven(cls, trace_id: str, claim: Claim, verdict: Verdict) -> "QueryResult":
        return cls(trace_id=trace_id, status=QueryStatus.COMPLETE, claim=claim, verdict=verdict)

    @classmethod
    def failed(cls, trace_id: str, error: str) -> "QueryResult":
        return cls(trace_id=trace_id, status=QueryStatus.FAILED, error=error)
```

- [ ] **Step 3: Extend `src/codeoracle/models/__init__.py` re-exports**

Replace the current file with the extended version:

```python
"""Pure Pydantic domain types. No I/O. No imports from db/, adapters/, services/.

Public re-exports below let callers do `from codeoracle.models import Claim, Verdict`
without knowing the file layout.
"""

from codeoracle.models.evidence import (
    Citation,
    Claim,
    DraftCitation,
    DraftClaim,
    EvidenceHandle,
    HandleType,
    ResolvedCitation,
    Verdict,
    VerdictStatus,
)
from codeoracle.models.llm import (
    ChatMessage,
    ChatResponse,
    FinishReason,
    Role,
    ToolCall,
    ToolDefinition,
    TokenUsage,
)
from codeoracle.models.query import Query, QueryResult, QueryStatus
from codeoracle.models.trust import SourceKind, TrustLevel

__all__ = [
    "ChatMessage",
    "ChatResponse",
    "Citation",
    "Claim",
    "DraftCitation",
    "DraftClaim",
    "EvidenceHandle",
    "FinishReason",
    "HandleType",
    "Query",
    "QueryResult",
    "QueryStatus",
    "ResolvedCitation",
    "Role",
    "SourceKind",
    "TokenUsage",
    "ToolCall",
    "ToolDefinition",
    "TrustLevel",
    "Verdict",
    "VerdictStatus",
]
```

- [ ] **Step 4: Write `tests/unit/models/test_llm.py`**

```python
"""LLM model tests."""

import pytest
from pydantic import ValidationError

from codeoracle.models.llm import (
    ChatMessage,
    ChatResponse,
    TokenUsage,
    ToolCall,
    ToolDefinition,
)


def test_chat_message_minimum():
    m = ChatMessage(role="user", content="hello")
    assert m.role == "user"
    assert m.tool_calls == []


def test_chat_message_rejects_bad_role():
    with pytest.raises(ValidationError):
        ChatMessage(role="root", content="hello")  # type: ignore[arg-type]


def test_token_usage_negative_rejected():
    with pytest.raises(ValidationError):
        TokenUsage(input_tokens=-1, output_tokens=10)


def test_token_usage_total():
    u = TokenUsage(input_tokens=10, output_tokens=20)
    assert u.total_tokens == 30


def test_token_usage_is_frozen():
    u = TokenUsage(input_tokens=10, output_tokens=20)
    with pytest.raises(ValidationError):
        u.input_tokens = 99


def test_chat_response_construction():
    resp = ChatResponse(
        content="pong",
        usage=TokenUsage(input_tokens=5, output_tokens=2),
        finish_reason="stop",
    )
    assert resp.tool_calls == []


def test_tool_definition_name_constraints():
    with pytest.raises(ValidationError):
        ToolDefinition(name="", description="x", parameters_schema={})


def test_tool_call_round_trip():
    tc = ToolCall(id="call_abc", name="search", arguments_json='{"q":"x"}')
    assert tc.model_dump()["arguments_json"] == '{"q":"x"}'
```

- [ ] **Step 5: Write `tests/unit/models/test_query.py`**

```python
"""Public query model tests."""

import pytest
from pydantic import ValidationError

from codeoracle.models.evidence import (
    Citation,
    Claim,
    EvidenceHandle,
    HandleType,
    ResolvedCitation,
    Verdict,
    VerdictStatus,
)
from codeoracle.models.query import Query, QueryResult, QueryStatus


def _claim_and_verdict() -> tuple[Claim, Verdict]:
    handle = EvidenceHandle(
        type=HandleType.CODE_SPAN,
        locator="file://x:1-2",
        snapshot_sha="abc1234",
    )
    cit = Citation(handle=handle, excerpt="x", source_trust=8)
    claim = Claim(statement="s", citations=[cit], confidence=0.9)
    verdict = Verdict(
        status=VerdictStatus.PROVEN,
        reason="r",
        resolved_citations=[
            ResolvedCitation(citation=cit, resolved=True, matches_excerpt=True, actual_content="x"),
        ],
    )
    return claim, verdict


def test_query_minimum():
    q = Query(symbol="codeoracle.services.verifier.service.VerifierService.verify")
    assert q.type == "explain_symbol"
    assert q.corpus == "self"
    assert q.snapshot_sha is None


def test_query_symbol_required():
    with pytest.raises(ValidationError):
        Query(symbol="")


def test_query_result_proven_factory():
    claim, verdict = _claim_and_verdict()
    r = QueryResult.proven(trace_id="t1", claim=claim, verdict=verdict)
    assert r.status == QueryStatus.COMPLETE
    assert r.error is None


def test_query_result_failed_factory():
    r = QueryResult.failed(trace_id="t1", error="boom")
    assert r.status == QueryStatus.FAILED
    assert r.claim is None
    assert r.verdict is None
```

- [ ] **Step 6: Run all model tests + lint + type**

```bash
uv run pytest tests/unit/models/ -v
uv run ruff check src/codeoracle/models/ tests/unit/models/
uv run ruff format --check src/codeoracle/models/ tests/unit/models/
uv run ty check src/codeoracle/models/
```

Expected: 12 new tests pass (25 total in models/); lint/format/type all exit 0.

- [ ] **Step 7: Commit**

```bash
git add src/codeoracle/models/llm.py src/codeoracle/models/query.py src/codeoracle/models/__init__.py tests/unit/models/test_llm.py tests/unit/models/test_query.py
git commit -m "Plan 3 task 5: llm + query Pydantic models"
```

---

## Task 6: Models — snapshot

**What this task delivers:** the `Snapshot` Pydantic model with its three enums (`SnapshotStatus`, `ExtractorOutcome`, plus the per-extractor key set) and the dirty-working-tree SHA convention. Per spec §8.4: `<head-sha>+wt<sha256-of-tracked-changes>[12]` lets a developer reindex without forcing a commit. Plans 5 and 6 (worker framework + extractor wiring) will construct Snapshots; Plan 3 just lands the type.

**Files:**
- Create: `src/codeoracle/models/snapshot.py`
- Modify: `src/codeoracle/models/__init__.py` (extend re-exports)
- Create: `tests/unit/models/test_snapshot.py`

- [ ] **Step 1: Write `src/codeoracle/models/snapshot.py`**

```python
"""Snapshot — a versioned record of one extraction pass over the corpus.

A Snapshot's identity is its `sha`:
  - clean tree:  <git head SHA>                  (e.g. "abc1234...")
  - dirty tree:  <git head SHA>+wt<12-char hash> (e.g. "abc1234+wt5e7a9f0c8b3d")

The +wt suffix is computed by hashing the concatenation of (path, content)
for every git-tracked changed file. This lets dogfooding (`codeoracle index`
against uncommitted edits) work without forcing a commit.
"""

import hashlib
from datetime import datetime
from enum import Enum
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StringConstraints


class SnapshotStatus(str, Enum):
    PENDING = "pending"
    APPLYING = "applying"
    APPLIED = "applied"
    FAILED = "failed"


class ExtractorOutcome(str, Enum):
    SUCCESS = "success"
    SKIPPED = "skipped"
    FAILED = "failed"


# Keys used in Snapshot.extractor_results. The canonical bare names match
# Plan 6's IndexingService.DEFAULT_EXTRACTOR_KINDS = ("joern", "scip",
# "opengrep", "embed") — the four snapshot-keyed extractors that fan out
# under the per-file barrier. traces_ingest (Plan 7) is a ContinuousConsumer
# that writes to traces.spans, not extractor_results; Sourcebot is git-pull
# (no indexer worker — see Plan 5 §"Defers to Plan 6"). Kept as a frozenset
# so the closed-set assumption is check-able at instance construction.
KNOWN_EXTRACTORS: frozenset[str] = frozenset(
    {
        "joern",
        "scip",
        "opengrep",
        "embed",
    }
)


class Snapshot(BaseModel):
    """One end-to-end extraction pass. Lives in the `snapshots` PG table (Plan 3 Task 9)."""

    model_config = ConfigDict(frozen=False)  # status flips during application

    sha: Annotated[str, StringConstraints(min_length=4, max_length=80)]
    parent_sha: str | None = None
    changed_files: list[str] = Field(default_factory=list)
    status: SnapshotStatus = SnapshotStatus.PENDING
    extractor_results: dict[str, ExtractorOutcome] = Field(default_factory=dict)
    error: str | None = None
    applied_at: datetime | None = None


def compute_dirty_tree_sha(head_sha: str, file_contents: list[tuple[str, str]]) -> str:
    """Compute the dirty-tree snapshot SHA.

    `file_contents` is a list of (path, content) pairs for every git-tracked
    file whose working-tree state differs from the HEAD commit. Order does
    NOT matter (the function sorts internally for determinism). Returns a
    string of the form '<head_sha>+wt<12-char-hex>'.
    """
    if not file_contents:
        return head_sha  # tree is clean — head SHA is the snapshot SHA
    h = hashlib.sha256()
    for path, content in sorted(file_contents, key=lambda x: x[0]):
        h.update(path.encode("utf-8"))
        h.update(b"\0")  # delimiter so ("ab", "cd") != ("a", "bcd")
        h.update(content.encode("utf-8"))
        h.update(b"\0")
    return f"{head_sha}+wt{h.hexdigest()[:12]}"
```

- [ ] **Step 2: Extend `src/codeoracle/models/__init__.py`**

Add to the `from codeoracle.models.X import ...` block and `__all__`:

```python
from codeoracle.models.snapshot import (
    KNOWN_EXTRACTORS,
    ExtractorOutcome,
    Snapshot,
    SnapshotStatus,
    compute_dirty_tree_sha,
)
```

Add to `__all__` (alphabetical position):

```python
    "ExtractorOutcome",
    "KNOWN_EXTRACTORS",
    "Snapshot",
    "SnapshotStatus",
    "compute_dirty_tree_sha",
```

The final file looks like:

```python
"""Pure Pydantic domain types. No I/O. No imports from db/, adapters/, services/.

Public re-exports below let callers do `from codeoracle.models import Claim, Verdict`
without knowing the file layout.
"""

from codeoracle.models.evidence import (
    Citation,
    Claim,
    DraftCitation,
    DraftClaim,
    EvidenceHandle,
    HandleType,
    ResolvedCitation,
    Verdict,
    VerdictStatus,
)
from codeoracle.models.llm import (
    ChatMessage,
    ChatResponse,
    FinishReason,
    Role,
    ToolCall,
    ToolDefinition,
    TokenUsage,
)
from codeoracle.models.query import Query, QueryResult, QueryStatus
from codeoracle.models.snapshot import (
    KNOWN_EXTRACTORS,
    ExtractorOutcome,
    Snapshot,
    SnapshotStatus,
    compute_dirty_tree_sha,
)
from codeoracle.models.trust import SourceKind, TrustLevel

__all__ = [
    "ChatMessage",
    "ChatResponse",
    "Citation",
    "Claim",
    "DraftCitation",
    "DraftClaim",
    "EvidenceHandle",
    "ExtractorOutcome",
    "FinishReason",
    "HandleType",
    "KNOWN_EXTRACTORS",
    "Query",
    "QueryResult",
    "QueryStatus",
    "ResolvedCitation",
    "Role",
    "Snapshot",
    "SnapshotStatus",
    "SourceKind",
    "TokenUsage",
    "ToolCall",
    "ToolDefinition",
    "TrustLevel",
    "Verdict",
    "VerdictStatus",
    "compute_dirty_tree_sha",
]
```

- [ ] **Step 3: Write `tests/unit/models/test_snapshot.py`**

```python
"""Snapshot model + dirty-tree SHA tests."""

from datetime import datetime

import pytest
from pydantic import ValidationError

from codeoracle.models.snapshot import (
    KNOWN_EXTRACTORS,
    ExtractorOutcome,
    Snapshot,
    SnapshotStatus,
    compute_dirty_tree_sha,
)


def test_clean_tree_returns_head_sha_unchanged():
    assert compute_dirty_tree_sha("abc1234", []) == "abc1234"


def test_dirty_tree_returns_prefixed_sha():
    sha = compute_dirty_tree_sha("abc1234", [("file.py", "x = 1\n")])
    assert sha.startswith("abc1234+wt")
    assert len(sha) == len("abc1234") + 3 + 12  # "abc1234" + "+wt" + 12 hex chars


def test_dirty_tree_sha_is_order_independent():
    a = compute_dirty_tree_sha("abc", [("a", "1"), ("b", "2")])
    b = compute_dirty_tree_sha("abc", [("b", "2"), ("a", "1")])
    assert a == b


def test_dirty_tree_sha_changes_when_content_changes():
    a = compute_dirty_tree_sha("abc", [("a", "1")])
    b = compute_dirty_tree_sha("abc", [("a", "2")])
    assert a != b


def test_dirty_tree_sha_distinguishes_path_boundaries():
    """The b'\\0' delimiter ensures ('ab', 'cd') != ('a', 'bcd')."""
    a = compute_dirty_tree_sha("abc", [("ab", "cd")])
    b = compute_dirty_tree_sha("abc", [("a", "bcd")])
    assert a != b


def test_snapshot_defaults():
    s = Snapshot(sha="abc1234")
    assert s.status == SnapshotStatus.PENDING
    assert s.parent_sha is None
    assert s.changed_files == []
    assert s.extractor_results == {}


def test_snapshot_sha_min_length():
    with pytest.raises(ValidationError):
        Snapshot(sha="abc")  # 3 chars — below min 4


def test_snapshot_extractor_results_with_known_keys():
    s = Snapshot(
        sha="abc1234",
        extractor_results={"joern": ExtractorOutcome.SUCCESS},
    )
    assert s.extractor_results["joern"] == ExtractorOutcome.SUCCESS


def test_known_extractors_is_snapshot_extractor_set():
    """KNOWN_EXTRACTORS is the closed set of keys valid in Snapshot.extractor_results.

    These are the four snapshot-keyed extractors fanned out by Plan 6's
    IndexingService (DEFAULT_EXTRACTOR_KINDS). traces_ingest (Plan 7) is a
    ContinuousConsumer that writes to traces.spans, not extractor_results;
    Sourcebot is git-pull (no indexer worker — Plan 5 §"Defers to Plan 6").
    """
    assert KNOWN_EXTRACTORS == {"joern", "scip", "opengrep", "embed"}


def test_snapshot_status_transitions_are_strings():
    """Defensive: status is a str enum so JSON serialization is stable."""
    assert SnapshotStatus.APPLIED.value == "applied"
    assert SnapshotStatus.FAILED.value == "failed"


def test_snapshot_applied_at_can_be_set():
    when = datetime(2026, 5, 21, 12, 0, 0)
    s = Snapshot(sha="abc1234", status=SnapshotStatus.APPLIED, applied_at=when)
    assert s.applied_at == when
```

- [ ] **Step 4: Run tests + lint + type**

```bash
uv run pytest tests/unit/models/test_snapshot.py -v
uv run ruff check src/codeoracle/models/snapshot.py tests/unit/models/test_snapshot.py
uv run ruff format --check src/codeoracle/models/snapshot.py tests/unit/models/test_snapshot.py
uv run ty check src/codeoracle/models/snapshot.py
```

Expected: 10 tests pass; lint/format/type clean.

- [ ] **Step 5: Commit**

```bash
git add src/codeoracle/models/snapshot.py src/codeoracle/models/__init__.py tests/unit/models/test_snapshot.py
git commit -m "Plan 3 task 6: Snapshot model + dirty-tree SHA convention"
```

---

## Task 7: extend `infra/compose/docker-compose.yml` + add `infra/scripts/bootstrap_db.sh`

**What this task delivers:** appends `postgres` + `valkey` services to the docker-compose file Plan 1A Task 4 created (Plan 1A scaffolds the compose file with the observability service block only — OTel Collector, Tempo, Prometheus, Grafana). After this task the compose stack boots the full local platform: PG18+AGE + Valkey + the observability backplane. Also adds the SUPERUSER-only one-shot bootstrap script that creates the `age` + `vector` extensions and the `codeoracle` graph. The new services use image digests pinned in `infra/versions/pinned.env` from Plan 2. The bootstrap script is idempotent so it's safe to re-run.

**Key contract:** `bootstrap_db.sh` is the ONLY place that runs `CREATE EXTENSION`. Alembic migrations (Tasks 9-11) NEVER create extensions — they assume `age` and `vector` are already installed. This avoids the SUPERUSER-required failure mode in CI / managed-Postgres environments.

**Files:**
- Modify: `infra/compose/docker-compose.yml` (Plan 1A Task 4 created the file with the observability service block; Plan 3 appends `postgres` + `valkey` services under the existing top-level `services:` key — do NOT replace the file, append only)
- Create: `infra/scripts/bootstrap_db.sh`
- Create: `infra/scripts/bootstrap_db.sql`
- Modify: `.env.example` (Plan 1 may have created a minimal stub; Plan 3 writes the full version below — overwriting a stub-only file is acceptable, but if Plan 1A or future plans add `.env.example` keys, this Step 2 needs to merge rather than overwrite)

- [ ] **Step 1: Check what `.env.example` looks like now**

```bash
test -f .env.example && cat .env.example || echo "(not present)"
```

Expected: either a Plan 1 stub or "(not present)". Either way, Step 2 writes the full version.

- [ ] **Step 2: Write `.env.example`** (the dev-default values)

```bash
# CodeOracle — local development environment template.
# Copy to .env (gitignored) and fill in real values where placeholders are obvious.
#
# Naming convention — two patterns coexist:
#   1. Root Settings (config.py):  CODEORACLE_<SECTION>__<FIELD>
#      (env_prefix="CODEORACLE_", env_nested_delimiter="__")
#      Used for: CODEORACLE_DB__*, CODEORACLE_EMBEDDING__*
#   2. Standalone child classes (e.g. LLMSettings in Plan 4):
#      env_prefix="CODEORACLE_<CHILD>_" — SINGLE underscore between prefix and field,
#      double underscore only for further nesting inside the child.
#      Used for: CODEORACLE_LLM_PROVIDER, CODEORACLE_LLM_OPENAI__API_KEY (see spec §5.4)
# Both patterns are valid pydantic-settings configurations; they differ because
# LLMSettings is instantiated standalone by Plan 4's provider factory, not as
# a sub-field of the root Settings instance.

# Process mode — dev | ci | prod
CODEORACLE_ENVIRONMENT=dev

# Postgres connection (matches docker-compose defaults)
CODEORACLE_DB__HOST=localhost
CODEORACLE_DB__PORT=5432
CODEORACLE_DB__USER=codeoracle
CODEORACLE_DB__PASSWORD=codeoracle
CODEORACLE_DB__DATABASE=codeoracle

# Embedding model + dim — Plan 2 verified these. DO NOT edit dim here
# without also writing an alembic migration; pgvector column type is fixed at
# creation time.
CODEORACLE_EMBEDDING__MODEL=text-embedding-3-small
CODEORACLE_EMBEDDING__DIM=1536

# LLM provider config — Plan 4 wires this. Uses the standalone child-class
# env_prefix pattern (env_prefix="CODEORACLE_LLM_"). Placeholders only:
# CODEORACLE_LLM_PROVIDER=openai
# CODEORACLE_LLM_OPENAI__API_KEY=sk-fake-replace-me
```

- [ ] **Step 3: Write `infra/scripts/bootstrap_db.sql`**

```sql
-- Layer 1: one-shot DB bootstrap.
--
-- Creates the AGE + pgvector extensions, sets the per-database search_path,
-- and creates the 'codeoracle' AGE graph. Idempotent: re-running is a no-op.
--
-- Run as the Postgres SUPERUSER (the docker-compose 'codeoracle' user has
-- SUPERUSER on the dev DB; in prod this would be a one-time DBA action).
--
-- Plan 3 Task 9+ alembic migrations assume this script has already run.
-- Migrations NEVER call CREATE EXTENSION — that requires SUPERUSER which
-- the application role does not have in non-dev environments.

CREATE EXTENSION IF NOT EXISTS age;
CREATE EXTENSION IF NOT EXISTS vector;

-- Per-database default search_path. `public` is FIRST so unqualified
-- CREATE TABLE in alembic migrations lands in `public`, not in ag_catalog.
-- ag_catalog stays in the path so AGE functions (cypher, create_graph, etc.)
-- still resolve unqualified. Reordering this line would cause every
-- application table to land in ag_catalog — a load-bearing decision.
ALTER DATABASE :"db_name" SET search_path = "$user", public, ag_catalog;

-- LOAD age on the bootstrap connection so create_graph resolves.
LOAD 'age';

-- Idempotently create the 'codeoracle' graph.
DO $$ BEGIN
  PERFORM create_graph('codeoracle');
EXCEPTION WHEN duplicate_schema THEN
  NULL;
END $$;

-- ── Graph property indexes (NFR D3-1) ──────────────────────────────────────
-- AGE auto-indexes only each vertex's `id` and each edge's start/end id — never
-- user properties. The agent's hot path filters vertices BY property on every
-- query: the symbol-existence check `MATCH (s:Symbol {fqn, snapshot_sha})`
-- (Plan 10 `_EXISTS_CYPHER`) and the callers entry
-- `MATCH (:File {snapshot_sha})-[...]->(:Symbol {fqn})`. With no property index
-- each is a full Seq Scan over the whole label table — once per user query,
-- over millions of multi-snapshot vertices. Plan 5 owns graph *writes* but
-- treats this graph schema as Plan 3's ("Inherits from Plan 3"), so the indexes
-- live here, beside create_graph, and exist BEFORE the first write (no
-- unindexed-scan window).
--
-- Pre-create the two foundational vertex labels so the indexes can be built now
-- rather than waiting for the graph-writer to create each label lazily on its
-- first MERGE. create_vlabel has no IF-NOT-EXISTS form and raises on a
-- duplicate; guard on the label's backing table (`<graph>."<Label>"`, an
-- ordinary table) via the standard `to_regclass` instead of swallowing the
-- error broadly. In dev/CI the single superuser `codeoracle` role owns these; a
-- split-role prod deployment grants the app role DML on them in this same DBA
-- bootstrap step.
DO $$ BEGIN
  IF to_regclass('codeoracle."Symbol"') IS NULL THEN PERFORM create_vlabel('codeoracle', 'Symbol'); END IF;
  IF to_regclass('codeoracle."File"')   IS NULL THEN PERFORM create_vlabel('codeoracle', 'File');   END IF;
END $$;

-- Btree indexes on the exact agtype property-access expression AGE emits for a
-- `{key: $val}` vertex-map match. The expression MUST match the planner's
-- predicate or the index is never chosen — an integration test EXPLAINs the
-- existence check under `enable_seqscan = off` and asserts it plans on
-- symbol_fqn_idx. `create_property_index('codeoracle','Symbol','fqn')` builds the
-- same index via AGE's helper; the explicit DDL is kept so the indexed
-- expression is self-documenting.
CREATE INDEX IF NOT EXISTS symbol_fqn_idx
  ON codeoracle."Symbol" USING btree (ag_catalog.agtype_access_operator(properties, '"fqn"'::agtype));
CREATE INDEX IF NOT EXISTS symbol_snapshot_sha_idx
  ON codeoracle."Symbol" USING btree (ag_catalog.agtype_access_operator(properties, '"snapshot_sha"'::agtype));
CREATE INDEX IF NOT EXISTS file_snapshot_sha_idx
  ON codeoracle."File" USING btree (ag_catalog.agtype_access_operator(properties, '"snapshot_sha"'::agtype));
```

- [ ] **Step 4: Write `infra/scripts/bootstrap_db.sh`**

```bash
#!/usr/bin/env bash
# Layer 1 of the DB bootstrap (see docs/superpowers/specs/2026-05-21-platform-scaffolding-design.md §3.5).
# Runs the bootstrap_db.sql as Postgres SUPERUSER. Idempotent — safe to re-run.
#
# Environment:
#   CODEORACLE_DB__HOST       (default: localhost)
#   CODEORACLE_DB__PORT       (default: 5432)
#   CODEORACLE_DB__USER       (default: codeoracle — must be SUPERUSER in dev/CI)
#   CODEORACLE_DB__PASSWORD   (default: codeoracle)
#   CODEORACLE_DB__DATABASE   (default: codeoracle)
#
# Exit codes:
#   0 — bootstrap succeeded (or was already in the desired state)
#   1 — psql connection failed (DB not up, wrong credentials)
#   2 — SQL execution failed (likely AGE / pgvector not installed in the image)

set -euo pipefail

HOST="${CODEORACLE_DB__HOST:-localhost}"
PORT="${CODEORACLE_DB__PORT:-5432}"
USER="${CODEORACLE_DB__USER:-codeoracle}"
PASSWORD="${CODEORACLE_DB__PASSWORD:-codeoracle}"
DATABASE="${CODEORACLE_DB__DATABASE:-codeoracle}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SQL_FILE="$SCRIPT_DIR/bootstrap_db.sql"

if [ ! -f "$SQL_FILE" ]; then
  echo "::error::bootstrap_db.sql not found at $SQL_FILE"
  exit 2
fi

echo "Bootstrapping $USER@$HOST:$PORT/$DATABASE ..."

export PGPASSWORD="$PASSWORD"

# Wait up to 30s for Postgres to accept connections (CI / cold compose start).
for _ in $(seq 1 30); do
  if psql -h "$HOST" -p "$PORT" -U "$USER" -d "$DATABASE" -c 'SELECT 1' >/dev/null 2>&1; then
    break
  fi
  sleep 1
done

if ! psql -h "$HOST" -p "$PORT" -U "$USER" -d "$DATABASE" -c 'SELECT 1' >/dev/null 2>&1; then
  echo "::error::cannot connect to Postgres after 30s"
  unset PGPASSWORD
  exit 1
fi

# Run the bootstrap SQL with the db name substituted into ALTER DATABASE.
if ! psql -h "$HOST" -p "$PORT" -U "$USER" -d "$DATABASE" \
     -v db_name="$DATABASE" -v ON_ERROR_STOP=1 -f "$SQL_FILE"; then
  echo "::error::bootstrap_db.sql failed — is the image AGE+pgvector-enabled?"
  unset PGPASSWORD
  exit 2
fi

unset PGPASSWORD
echo "Bootstrap complete."
```

- [ ] **Step 5: Make the bootstrap script executable**

```bash
chmod +x infra/scripts/bootstrap_db.sh
```

- [ ] **Step 6: Append `postgres` + `valkey` services to the existing `infra/compose/docker-compose.yml`**

Plan 1A Task 4 created this file with the observability service block (OTel Collector, Tempo, Prometheus, Grafana). Plan 3 layers the platform datastores (`postgres` AGE+pgvector, `valkey`) under the existing top-level `services:` key without disturbing the observability block. We use `ruamel.yaml` for the merge because it preserves comments + key order on round-trip (PyYAML strips both). The merge is idempotent — re-running is a no-op.

**(a)** Pre-flight: verify Plan 1A's file exists and contains the observability block.

```bash
test -f infra/compose/docker-compose.yml || { echo "::error::Plan 1A artifact missing"; exit 1; }
grep -qE '^[[:space:]]*(otel-collector|tempo|prometheus|grafana|loki):' infra/compose/docker-compose.yml \
  || { echo "::error::observability block missing — Plan 1A artifact looks wrong"; exit 1; }
echo "OK: file present, observability block detected"
```

Expected: `OK: file present, observability block detected`.

**(b)** Merge `postgres` + `valkey` services and the `pgdata` volume into the existing document.

```bash
uv run --with ruamel.yaml python <<'PY'
from pathlib import Path
from ruamel.yaml import YAML

p = Path("infra/compose/docker-compose.yml")
yaml = YAML()
yaml.preserve_quotes = True
yaml.indent(mapping=2, sequence=4, offset=2)
doc = yaml.load(p)

services = doc.setdefault("services", {})

# Idempotent: leave a pre-existing platform block untouched.
if "postgres" in services or "valkey" in services:
    print("postgres/valkey already present — leaving as-is")
    raise SystemExit(0)

# Plan 2 verified these image refs in infra/versions/pinned.env.
# IMAGE_POSTGRES_AGE example: apache/age:release_PG18@sha256:<digest>
services["postgres"] = {
    "image": "${IMAGE_POSTGRES_AGE}",
    # Raise max_connections above PG's default 100 (≈97 usable) so the slice's
    # independently-pooled process types (api + mcp + agent + N workers, each a
    # DBConfig pool of pool_size+max_overflow) sum comfortably under the ceiling
    # rather than exhausting it at the first scale-out. 300 ≈ full peak + headroom
    # for replicas + AGE/maintenance + superuser-reserved (sizing math in the
    # connection-budget note below); a deployment overrides it per its own Σ.
    # Production fan-out beyond a sane ceiling fronts PG with pgbouncer
    # (transaction mode), NOT an unbounded max_connections.
    "command": [
        "postgres",
        "-c", "max_connections=300",
        # NFR D0-3: make the superuser headroom explicit + scale it for the 3x
        # ceiling (PG's silent default of 3 is thin against 300) — keeps slots
        # free for a superuser to connect and diagnose when the app pools
        # saturate, instead of locking out the very session needed to see why.
        "-c", "superuser_reserved_connections=5",
    ],
    "environment": {
        "POSTGRES_USER": "${CODEORACLE_DB__USER:-codeoracle}",
        "POSTGRES_PASSWORD": "${CODEORACLE_DB__PASSWORD:-codeoracle}",
        "POSTGRES_DB": "${CODEORACLE_DB__DATABASE:-codeoracle}",
    },
    "ports": ["5432:5432"],
    "volumes": ["pgdata:/var/lib/postgresql/data"],
    "healthcheck": {
        "test": ["CMD-SHELL", "pg_isready -U ${CODEORACLE_DB__USER:-codeoracle} -d ${CODEORACLE_DB__DATABASE:-codeoracle}"],
        "interval": "5s",
        "timeout": "3s",
        "retries": 10,
    },
}

# IMAGE_VALKEY example: valkey/valkey:7@sha256:<digest>
services["valkey"] = {
    "image": "${IMAGE_VALKEY}",
    "command": ["valkey-server", "--save", "", "--appendonly", "no"],
    "ports": ["6379:6379"],
    "healthcheck": {
        "test": ["CMD", "valkey-cli", "ping"],
        "interval": "5s",
        "timeout": "3s",
        "retries": 10,
    },
}

volumes = doc.setdefault("volumes", {})
volumes["pgdata"] = {"name": "codeoracle_pgdata"}

yaml.dump(doc, p)
print("Appended postgres + valkey services + pgdata volume")
PY
```

Expected output: `Appended postgres + valkey services + pgdata volume` (first run) OR `postgres/valkey already present — leaving as-is` (idempotent re-run).

**(c)** Post-merge validation: file parses, observability block survived, platform services are now present.

```bash
docker compose -f infra/compose/docker-compose.yml --env-file infra/versions/pinned.env config >/dev/null \
  && echo "OK: merged compose file parses"

grep -qE '^[[:space:]]*(otel-collector|tempo):' infra/compose/docker-compose.yml \
  && echo "OK: observability block survived merge"
grep -qE '^[[:space:]]*postgres:' infra/compose/docker-compose.yml \
  && echo "OK: postgres present"
grep -qE '^[[:space:]]*valkey:' infra/compose/docker-compose.yml \
  && echo "OK: valkey present"
```

Expected: four `OK` lines. If any FAIL, restore the file from git (`git checkout -- infra/compose/docker-compose.yml`) and investigate before retrying the merge.

**Connection budget (why `max_connections=300`).** The platform runs ~8-9
independently-pooled process types against this one Postgres: `api`, `mcp`,
`agent`, one consumer per extractor (`joern` / `scip` / `opengrep` / `embed`),
and the `reindex_orchestrator`. Each opens a SQLAlchemy async pool of
`pool_size + max_overflow` (default 5 + 10 = 15) connections. At a *single*
replica the worst-case ceiling is already ~9 × 15 ≈ 135 — and PG's default
`max_connections=100` (≈97 usable after `superuser_reserved_connections`) is
exhausted *before* any horizontal scale-out. The fix is two-sided: (1) the server
ceiling is raised here to 300 (a deployment overrides it in the compose `command`
to match its own Σ), and
(2) each pool is env-tunable per process (`CODEORACLE_DB__POOL_SIZE` /
`CODEORACLE_DB__MAX_OVERFLOW`, Plan 3 `DBConfig`) so a serial looping worker can be sized
down to 2 + 3 rather than 5 + 10. A deployment sizes
`max_connections ≥ Σ(per-process pool_size+max_overflow) × replicas +
AGE/maintenance headroom`. Past the point where raising `max_connections` costs
too much PG backend memory, production fan-out fronts PG with **pgbouncer in
`transaction` pooling mode** (a connection multiplexer) — the correct scale lever,
not an unbounded ceiling. The empirical "handles enterprise concurrency" claim is
proven by a dedicated load/soak test (the Slice+1 acceptance gate per spec §8.5),
not the slice e2e, which only demonstrates multi-process coexistence under this
budget.

- [ ] **Step 7: Boot the stack + run the bootstrap script (smoke test the wiring)**

```bash
cd infra/compose
docker compose --env-file ../versions/pinned.env up -d
cd ../..

# Wait for healthy
docker compose -f infra/compose/docker-compose.yml ps

# Run the bootstrap
bash infra/scripts/bootstrap_db.sh
```

Expected: postgres and valkey both report `(healthy)`; bootstrap script prints "Bootstrap complete."

If bootstrap fails with "extension age does not exist", the pinned image doesn't bundle AGE — that means Plan 2 Task 2 verified the wrong image. Re-run Plan 2 Task 2.

- [ ] **Step 8: Verify AGE + pgvector via psql**

```bash
export PGPASSWORD=codeoracle
psql -h localhost -p 5432 -U codeoracle -d codeoracle -c \
  "SELECT extname, extversion FROM pg_extension WHERE extname IN ('age', 'vector') ORDER BY extname;"
unset PGPASSWORD
```

Expected: two rows, `age` and `vector`, with their versions.

- [ ] **Step 9: Tear the stack back down + remove the volume**

```bash
docker compose -f infra/compose/docker-compose.yml down -v
```

(The `-v` removes the named volume so the next task starts from a fresh DB. This is the convention for Plan 3 — tasks reset state.)

- [ ] **Step 10: Commit**

```bash
git add infra/compose/docker-compose.yml infra/scripts/bootstrap_db.sh infra/scripts/bootstrap_db.sql .env.example
git commit -m "Plan 3 task 7: extend compose with postgres+valkey + add bootstrap_db.sh (idempotent, SUPERUSER-only)"
```

---

## Task 8: `src/codeoracle/db/session.py` + `tests/integration/conftest.py` + dual-engine AGE-listener test

**What this task delivers:** Layer 2 of the AGE-per-connection contract. A SQLAlchemy `event.listens_for(Engine, "connect")` listener fires on every new connection from any process (async app, sync alembic, worker, CLI) and runs `LOAD 'age'` + `SET search_path = "$user", public, ag_catalog`. Without this, alembic / API / workers / CLI all blow up on first `cypher()` call.

**The load-bearing test:** the spec's whole DB story assumes the listener fires for BOTH the async asyncpg-based engine (used by the app) AND the sync psycopg-based engine (used by alembic). They share `event.listens_for(Engine, "connect")` because both are SQLAlchemy `Engine`s, but the dbapi layer is different. This task's integration test proves both paths work.

**Files:**
- Create: `src/codeoracle/db/__init__.py`
- Create: `src/codeoracle/db/session.py`
- Create: `tests/integration/__init__.py`
- Create: `tests/integration/conftest.py`
- Create: `tests/integration/test_age_per_connection.py`

- [ ] **Step 1: Boot the stack + bootstrap (fresh state for the test)**

```bash
cd infra/compose
docker compose --env-file ../versions/pinned.env up -d
cd ../..
# Wait for healthy state, then bootstrap
sleep 3
bash infra/scripts/bootstrap_db.sh
```

Expected: "Bootstrap complete."

- [ ] **Step 2: Write `src/codeoracle/db/__init__.py`**

```python
"""The platform's own Postgres (PG18 + AGE + pgvector) database access layer.

Layer purity (see CLAUDE.md §strict-layer-purity):
  - this package OWNS connection lifecycle, transaction management, and the
    AGE-per-connection contract
  - it does NOT own business logic — that's services/
  - it does NOT wrap external systems — that's adapters/ (and this DB is internal)
"""
```

- [ ] **Step 3: Write `src/codeoracle/db/session.py`**

```python
"""Async + sync SQLAlchemy session factories with the AGE-per-connection contract.

The single `event.listens_for(Engine, "connect")` listener fires on every new
DB-API connection regardless of which SQLAlchemy Engine (async/sync) created it.
This makes `LOAD 'age'` + `SET search_path` a structural property of the DB
layer rather than discipline that can drift.
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import Session, sessionmaker

from codeoracle.core.config import get_settings


@event.listens_for(Engine, "connect")
def _setup_age_per_connection(dbapi_conn, _connection_record) -> None:  # noqa: ANN001
    """Every new DB connection loads AGE and sets search_path.

    Required because LOAD and SET search_path are session-scoped in Postgres.
    Fires for BOTH the async-engine (asyncpg) and sync-engine (psycopg) code paths
    because both are SQLAlchemy Engines underneath.

    search_path order: 'public' is FIRST so unqualified CREATE TABLE (alembic
    migrations) lands in public; ag_catalog stays in the path so cypher() and
    other AGE functions resolve unqualified. Mirrors the per-database default
    set by bootstrap_db.sql. Reordering would silently misplace tables.
    """
    with dbapi_conn.cursor() as cur:
        cur.execute("LOAD 'age'")
        cur.execute('SET search_path = "$user", public, ag_catalog')


def make_async_engine() -> AsyncEngine:
    """Create the async engine the app uses.

    Pool sizing comes from DBConfig (env: CODEORACLE_DB__POOL_SIZE / CODEORACLE_DB__MAX_OVERFLOW
    / CODEORACLE_DB__POOL_TIMEOUT) so the aggregate connection budget across every
    independently-pooled process replica can be fitted under the server's
    max_connections per deployment without a code change.

    Every process that needs the platform DB (api, mcp, agent, workers) MUST
    build its engine through this factory rather than calling
    create_async_engine directly: importing this module registers the AGE
    LOAD/search_path connect listener (above), so routing through here is what
    guarantees the listener fires for that process's connections.
    """
    settings = get_settings()
    return create_async_engine(
        settings.db.async_url(),
        pool_size=settings.db.pool_size,
        max_overflow=settings.db.max_overflow,
        pool_timeout=settings.db.pool_timeout,
        pool_pre_ping=True,
    )


def make_sync_engine() -> Engine:
    """Create the sync engine alembic uses."""
    settings = get_settings()
    return create_engine(
        settings.db.sync_url(),
        pool_size=2,
        max_overflow=5,
        pool_pre_ping=True,
    )


def make_async_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


def make_sync_session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(engine, expire_on_commit=False)


@asynccontextmanager
async def session_scope(engine: AsyncEngine) -> AsyncIterator[AsyncSession]:
    """Async session context manager — opens, commits on success, rolls back on exception."""
    factory = make_async_session_factory(engine)
    async with factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
```

- [ ] **Step 4: Write `tests/integration/__init__.py`** (empty)

```python
```

- [ ] **Step 5: Write `tests/integration/conftest.py`**

```python
"""Integration-test fixtures.

Every integration test uses these fixtures so DB setup is not reinvented
per file. Tests that need a fresh schema get the `bootstrapped_db` fixture
(session-scoped so docker compose doesn't restart between tests); tests
that need to mutate the schema get the `clean_session` fixture (per-function).
"""

import os
import subprocess
from collections.abc import AsyncIterator, Iterator
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.engine import Engine
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from codeoracle.core.config import get_settings
from codeoracle.db.session import (
    make_async_engine,
    make_async_session_factory,
    make_sync_engine,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
BOOTSTRAP_SCRIPT = REPO_ROOT / "infra" / "scripts" / "bootstrap_db.sh"


@pytest.fixture(scope="session")
def bootstrapped_db() -> Iterator[None]:
    """Run bootstrap_db.sh once per test session.

    Assumes the docker-compose stack is already up. If CODEORACLE_SKIP_BOOTSTRAP=1
    is set (CI may bootstrap externally), skip the subprocess invocation.
    """
    if os.environ.get("CODEORACLE_SKIP_BOOTSTRAP") == "1":
        yield
        return
    # bust the settings cache so any env overrides set by the test run are picked up
    get_settings.cache_clear()
    result = subprocess.run(
        [str(BOOTSTRAP_SCRIPT)],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        pytest.fail(f"bootstrap_db.sh failed:\n{result.stdout}\n{result.stderr}")
    yield


@pytest.fixture(scope="session")
def sync_engine(bootstrapped_db) -> Iterator[Engine]:
    engine = make_sync_engine()
    yield engine
    engine.dispose()


@pytest.fixture(scope="session")
def async_engine(bootstrapped_db) -> Iterator[AsyncEngine]:
    engine = make_async_engine()
    yield engine
    # synchronous dispose is OK for a test-session-scoped fixture


@pytest.fixture
async def async_session(async_engine: AsyncEngine) -> AsyncIterator[AsyncSession]:
    """Per-test async session with automatic rollback at the end.

    Under `asyncio_mode = "auto"`, plain `@pytest.fixture` on an async
    generator function is what pytest-asyncio adapts. `@pytest_asyncio.fixture`
    still works but emits a DeprecationWarning in newer versions.

    Use for tests that mutate DB state — the rollback keeps tests isolated.
    """
    factory = make_async_session_factory(async_engine)
    async with factory() as session:
        try:
            yield session
        finally:
            await session.rollback()


@pytest.fixture
def cypher_query():
    """Helper that builds an AGE cypher query as a SQLAlchemy text() construct."""
    def _build(cypher: str, columns: str = "v agtype") -> text:
        return text(f"SELECT * FROM cypher('codeoracle', $$ {cypher} $$) AS ({columns})")
    return _build
```

- [ ] **Step 6: Write `tests/integration/test_age_per_connection.py`**

```python
"""The load-bearing AGE-per-connection contract test.

Proves both the async (asyncpg) and sync (psycopg) engines honor the
event.listens_for(Engine, "connect") listener and can run cypher() queries
on a freshly-opened connection without "type agtype does not exist" failures.
"""

import pytest
from sqlalchemy import text
from sqlalchemy.engine import Engine
from sqlalchemy.ext.asyncio import AsyncSession


pytestmark = pytest.mark.integration


async def test_async_engine_loads_age_on_new_connection(async_session: AsyncSession):
    """Async path: open a session, run a cypher() query, no LOAD 'age' boilerplate
    from the test (the event listener already ran).

    No @pytest.mark.asyncio needed — pyproject.toml sets asyncio_mode = "auto".
    """
    result = await async_session.execute(
        text("SELECT * FROM cypher('codeoracle', $$ RETURN 1 $$) AS (one agtype);")
    )
    row = result.one()
    # AGE returns an agtype string '1' — check it's coercible
    assert str(row[0]).strip().strip("'\"") in ("1", "1.0")


async def test_async_engine_each_new_connection_independently_loads_age(async_engine):
    """Repeated session opens — each gets a (potentially different) pooled connection.
    All of them must be AGE-loaded; pool reuse must not leave a non-AGE connection accessible."""
    from codeoracle.db.session import make_async_session_factory

    factory = make_async_session_factory(async_engine)
    for _ in range(5):
        async with factory() as session:
            result = await session.execute(
                text("SELECT * FROM cypher('codeoracle', $$ RETURN 'hi' AS s $$) AS (s agtype);")
            )
            result.one()


def test_sync_engine_loads_age_on_new_connection(sync_engine: Engine):
    """Sync path (alembic uses this): open a sync connection and run cypher().
    The same Engine event listener that fired for asyncpg fires here for psycopg."""
    with sync_engine.connect() as conn:
        result = conn.execute(
            text("SELECT * FROM cypher('codeoracle', $$ RETURN 42 $$) AS (n agtype);")
        )
        row = result.one()
        assert str(row[0]).strip().strip("'\"") in ("42", "42.0")


def test_sync_engine_repeated_connections_all_age_loaded(sync_engine: Engine):
    """Pool reuse path for the sync engine."""
    for _ in range(5):
        with sync_engine.connect() as conn:
            conn.execute(text("SELECT * FROM cypher('codeoracle', $$ RETURN 1 $$) AS (n agtype);")).one()


def test_pgvector_extension_is_available(sync_engine: Engine):
    """Bonus — Task 9+ migrations create vector(N) columns; verify vector type resolves here."""
    with sync_engine.connect() as conn:
        result = conn.execute(text("SELECT extname FROM pg_extension WHERE extname='vector'"))
        assert result.scalar() == "vector"


def test_search_path_is_set_per_connection(sync_engine: Engine):
    """Verify SET search_path applied — public BEFORE ag_catalog.

    This ordering matters: alembic migrations issue unqualified CREATE TABLE,
    which lands in the first schema where the role has CREATE permission.
    With `public` first, tables land in `public`. With `ag_catalog` first
    (the spec §3.5 original draft), tables would land in ag_catalog — caught
    by Task 12's test_all_three_plan3_tables_present.
    """
    with sync_engine.connect() as conn:
        result = conn.execute(text("SHOW search_path")).scalar()
        assert result is not None
        # Both schemas must be present, public before ag_catalog
        assert "public" in result
        assert "ag_catalog" in result
        # The substring index of 'public' must be less than 'ag_catalog'
        assert result.index("public") < result.index("ag_catalog"), (
            f"public must precede ag_catalog in search_path, got: {result}"
        )
```

- [ ] **Step 7: Run the integration test against the running stack**

```bash
uv run pytest tests/integration/test_age_per_connection.py -v -m integration
```

Expected: 6 tests pass. If `test_async_engine_loads_age_on_new_connection` fails with `psycopg.errors.UndefinedObject: type "agtype" does not exist`, the event listener is not firing for asyncpg — that's the load-bearing bug Plan 3 exists to prevent. Investigate `event.listens_for` registration before any other change.

- [ ] **Step 8: Run lint + type + the full unit suite to confirm nothing regressed**

```bash
uv run ruff check src/codeoracle/db/ tests/integration/
uv run ruff format --check src/codeoracle/db/ tests/integration/
uv run ty check src/codeoracle/db/
uv run pytest tests/unit/ -v
```

Expected: lint/format/type clean; all unit tests still pass.

- [ ] **Step 9: Commit**

```bash
git add src/codeoracle/db/__init__.py src/codeoracle/db/session.py tests/integration/__init__.py tests/integration/conftest.py tests/integration/test_age_per_connection.py
git commit -m "Plan 3 task 8: AGE-per-connection contract + dual-engine integration test"
```

(Stack stays up for Tasks 9-12.)

---

## Task 9: Alembic init + migration 0001 — `snapshots` table

**What this task delivers:** alembic configured to use the sync engine via `db/session.py`, the alembic env script that imports models metadata, and the first migration that creates the `snapshots` table. **Critical**: migrations DO NOT call `CREATE EXTENSION` — `bootstrap_db.sh` owns extensions. If a future migration needs a new extension, it goes in `bootstrap_db.sql`.

**`Settings()` cascade — required env precondition for `alembic upgrade head`:** `env.py` calls `make_sync_engine()` (db/session.py) which calls `get_settings()` (core/config.py). After Task 2 made `EmbeddingConfig` required-no-default, `Settings()` raises `ConfigError` if `CODEORACLE_EMBEDDING__MODEL` / `CODEORACLE_EMBEDDING__DIM` are not set. Migrations don't actually use embedding config (the dim is sed-synced into migration 0002 body — see Task 10 Step 3a), but constructing `Settings` still requires the env. **Precondition before every `uv run alembic ...` invocation in this task and Tasks 10/11/12**: ensure `.env` exists and is loaded. The dev workflow `cp .env.example .env` from Task 7 Step 2 satisfies this; CI must `export` the same env vars or `source .env` before invoking alembic. If you see `ConfigError: invalid Settings configuration` during `alembic upgrade head`, this precondition was skipped.

**Files:**
- Create: `alembic.ini`
- Create: `src/codeoracle/db/migrations/__init__.py`
- Create: `src/codeoracle/db/migrations/env.py`
- Create: `src/codeoracle/db/migrations/script.py.mako`
- Create: `src/codeoracle/db/migrations/versions/__init__.py`
- Create: `src/codeoracle/db/migrations/versions/0001_snapshots_table.py`
- Create: `src/codeoracle/db/orm.py` (the SQLAlchemy declarative base used by migrations + Plans 5-7 repositories)
- Create: `tests/integration/test_migrations_0001.py`

- [ ] **Step 1: Write `alembic.ini`**

```ini
# Alembic configuration. We use a hand-written env.py (Plan 3 Task 9 Step 3)
# that builds the engine via db/session.py — so [alembic] sqlalchemy.url stays
# empty and `script_location` points at the in-package migrations dir.

[alembic]
script_location = src/codeoracle/db/migrations
prepend_sys_path = .
version_path_separator = os
sqlalchemy.url =

[loggers]
keys = root,sqlalchemy,alembic

[handlers]
keys = console

[formatters]
keys = generic

[logger_root]
level = WARN
handlers = console
qualname =

[logger_sqlalchemy]
level = WARN
handlers =
qualname = sqlalchemy.engine

[logger_alembic]
level = INFO
handlers =
qualname = alembic

[handler_console]
class = StreamHandler
args = (sys.stderr,)
level = NOTSET
formatter = generic

[formatter_generic]
format = %(levelname)-5.5s [%(name)s] %(message)s
datefmt = %H:%M:%S
```

- [ ] **Step 2: Write `src/codeoracle/db/orm.py`**

```python
"""SQLAlchemy declarative base + Mapped-type helpers.

Migrations and (in Plans 5-7) repositories share `Base`. Models in
src/codeoracle/models/ are Pydantic-only — never SQLAlchemy. The translation
is per-repository (Plans 5-7 own the four repositories: snapshots/graph
in Plan 5, embeddings in Plan 6, traces in Plan 7).
"""

from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    """Single declarative base for all platform tables."""
```

- [ ] **Step 3: Write `src/codeoracle/db/migrations/__init__.py`** (empty)

```python
```

- [ ] **Step 4: Write `src/codeoracle/db/migrations/env.py`**

```python
"""Alembic environment — uses db/session.py's sync engine factory so the
AGE-per-connection event listener fires for migrations too."""

from logging.config import fileConfig

from alembic import context

from codeoracle.db.orm import Base
from codeoracle.db.session import make_sync_engine

# Import all model modules so Base.metadata sees their tables.
# This list grows as later migrations add tables; Plan 3 has one such module.
from codeoracle.db import schemas  # noqa: F401

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """We don't use offline mode — fail fast if invoked."""
    raise RuntimeError("Offline migration mode is not supported. Run with --online.")


def run_migrations_online() -> None:
    engine = make_sync_engine()
    with engine.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
```

- [ ] **Step 5: Write `src/codeoracle/db/migrations/script.py.mako`** (alembic's per-revision template)

```mako
"""${message}

Revision ID: ${up_revision}
Revises: ${down_revision | comma,n}
Create Date: ${create_date}

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
${imports if imports else ""}

revision: str = ${repr(up_revision)}
down_revision: Union[str, None] = ${repr(down_revision)}
branch_labels: Union[str, Sequence[str], None] = ${repr(branch_labels)}
depends_on: Union[str, Sequence[str], None] = ${repr(depends_on)}


def upgrade() -> None:
    ${upgrades if upgrades else "pass"}


def downgrade() -> None:
    ${downgrades if downgrades else "pass"}
```

- [ ] **Step 6: Write `src/codeoracle/db/migrations/versions/__init__.py`** (empty)

```python
```

- [ ] **Step 7: Write `src/codeoracle/db/schemas.py`** (the declarative table definitions migrations reference)

Uses the SQLAlchemy 2.0 `Mapped[T]` + `mapped_column()` idiom. This is the modern declarative pattern — `Column[T]` typing pre-2.0 is deprecated and trips `ty check`.

```python
"""SQLAlchemy table definitions shared by migrations + (in Plans 5-7) repositories.

Adding a new table:
  1. Define it here as a `Base` subclass using Mapped[T] + mapped_column().
  2. Write an alembic migration in db/migrations/versions/ that mirrors the schema.
  3. Both files are committed together.
"""

from datetime import datetime

from sqlalchemy import JSON, DateTime, String, Text
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import Mapped, mapped_column

from codeoracle.db.orm import Base


class Snapshot(Base):
    """One end-to-end extraction pass.

    Mirrors codeoracle.models.snapshot.Snapshot — translation happens in
    `db/repositories/snapshots.py` (written in Plan 5). Kept separate so
    models/ stays pure Pydantic.
    """

    __tablename__ = "snapshots"

    sha: Mapped[str] = mapped_column(String(80), primary_key=True)
    parent_sha: Mapped[str | None] = mapped_column(String(80), nullable=True)
    changed_files: Mapped[list[str]] = mapped_column(
        ARRAY(String), nullable=False, default=list
    )
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending")
    extractor_results: Mapped[dict[str, str]] = mapped_column(JSON, nullable=False, default=dict)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    applied_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
```

- [ ] **Step 8: Generate migration 0001 (autogenerate from schemas + manual edits)**

Generate the migration scaffold:

```bash
uv run alembic revision -m "snapshots table" --rev-id 0001
```

Expected: a file is created at `src/codeoracle/db/migrations/versions/0001_snapshots_table.py` (the filename will be `0001_snapshots_table.py` because of the message + rev-id; alembic may append a timestamp — rename to drop it if so).

Then **replace its body** with the explicit form (autogenerate is unreliable on first run before there's any history):

```python
"""snapshots table

Revision ID: 0001
Revises:
Create Date: 2026-05-21

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import ARRAY


revision: str = "0001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "snapshots",
        sa.Column("sha", sa.String(80), primary_key=True),
        sa.Column("parent_sha", sa.String(80), nullable=True),
        sa.Column("changed_files", ARRAY(sa.String), nullable=False, server_default="{}"),
        sa.Column("status", sa.String(16), nullable=False, server_default="pending"),
        sa.Column("extractor_results", sa.JSON, nullable=False, server_default="{}"),
        sa.Column("error", sa.Text, nullable=True),
        sa.Column("applied_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_snapshots_status", "snapshots", ["status"])
    op.create_index("ix_snapshots_parent_sha", "snapshots", ["parent_sha"])


def downgrade() -> None:
    op.drop_index("ix_snapshots_parent_sha", "snapshots")
    op.drop_index("ix_snapshots_status", "snapshots")
    op.drop_table("snapshots")
```

- [ ] **Step 9: Run the migration against the live stack**

```bash
uv run alembic upgrade head
```

Expected:
```
INFO  [alembic.runtime.migration] Context impl PostgresqlImpl.
INFO  [alembic.runtime.migration] Will assume transactional DDL.
INFO  [alembic.runtime.migration] Running upgrade  -> 0001, snapshots table
```

If you see `psycopg.errors.UndefinedObject: type "agtype" does not exist`, the env.py is NOT using db/session.py's engine — fix that before continuing.

- [ ] **Step 10: Smoke-check the table exists**

```bash
export PGPASSWORD=codeoracle
psql -h localhost -p 5432 -U codeoracle -d codeoracle -c "\d snapshots"
unset PGPASSWORD
```

Expected: schema listing with all 7 columns + the two indexes.

- [ ] **Step 11: Write `tests/integration/test_migrations_0001.py`**

```python
"""Migration 0001 (snapshots table) — schema-shape + downgrade tests."""

import pytest
from sqlalchemy import text
from sqlalchemy.engine import Engine


pytestmark = pytest.mark.integration


def test_snapshots_table_exists(sync_engine: Engine):
    with sync_engine.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT column_name, data_type "
                "FROM information_schema.columns "
                "WHERE table_name='snapshots' "
                "ORDER BY ordinal_position"
            )
        ).all()
    columns = {r[0]: r[1] for r in rows}
    assert "sha" in columns
    assert "parent_sha" in columns
    assert "changed_files" in columns
    assert "status" in columns
    assert "extractor_results" in columns
    assert "error" in columns
    assert "applied_at" in columns


def test_snapshots_indexes_present(sync_engine: Engine):
    with sync_engine.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT indexname FROM pg_indexes "
                "WHERE tablename='snapshots' AND schemaname='public'"
            )
        ).all()
    index_names = {r[0] for r in rows}
    assert "ix_snapshots_status" in index_names
    assert "ix_snapshots_parent_sha" in index_names


def test_snapshots_insert_and_select(sync_engine: Engine):
    """Round-trip: insert a row, query back, delete (no test pollution)."""
    with sync_engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO snapshots (sha, status, changed_files, extractor_results) "
                "VALUES ('test_sha_0001', 'pending', '{}', '{}')"
            )
        )
        row = conn.execute(
            text("SELECT sha, status FROM snapshots WHERE sha='test_sha_0001'")
        ).one()
        assert row == ("test_sha_0001", "pending")
        conn.execute(text("DELETE FROM snapshots WHERE sha='test_sha_0001'"))
```

- [ ] **Step 12: Run the new test + the full integration suite**

```bash
uv run pytest tests/integration/ -v -m integration
```

Expected: 9 integration tests pass (6 from Task 8 + 3 from Task 9).

- [ ] **Step 13: Run lint + type**

```bash
uv run ruff check src/codeoracle/db/ alembic.ini tests/integration/test_migrations_0001.py
uv run ruff format --check src/codeoracle/db/ tests/integration/test_migrations_0001.py
uv run ty check src/codeoracle/db/
```

Expected: all exit 0. (`alembic.ini` is INI, not Python — ruff skips it.)

- [ ] **Step 14: Commit**

```bash
git add alembic.ini src/codeoracle/db/orm.py src/codeoracle/db/schemas.py src/codeoracle/db/migrations/ tests/integration/test_migrations_0001.py
git commit -m "Plan 3 task 9: alembic init + migration 0001 snapshots table"
```

---

## Task 10: Alembic migration 0002 — `embeddings` table with `vector(OPENAI_EMBEDDING_DIM)`

**What this task delivers:** the embeddings table sized to the dim Plan 2 pinned in `pinned.env`. The dim is read **at migration time** (not hardcoded) by sourcing `infra/versions/pinned.env` in the alembic invocation. The migration uses `pgvector.sqlalchemy.Vector`. Plan 6's `embed_indexer` worker will write rows; Plan 9's `Verifier.knn` uses the table.

**Why dim at migration time, not runtime:** pgvector column types are fixed at creation. If we read it from `settings.embedding.dim` and `Settings()` later returns a different value, the column doesn't change — and pgvector silently truncates or rejects inserts. Reading from `pinned.env` at migration time matches the Plan 2 contract (pinned.env IS the dim source of truth).

**Files:**
- Modify: `src/codeoracle/db/schemas.py` (add `Embedding`)
- Create: `src/codeoracle/db/migrations/versions/0002_embeddings_table.py`
- Create: `tests/integration/test_migrations_0002.py`

- [ ] **Step 1: Read OPENAI_EMBEDDING_DIM from pinned.env**

```bash
source infra/versions/pinned.env
echo "Migration 0002 will create vector($OPENAI_EMBEDDING_DIM) column"
```

Expected: `Migration 0002 will create vector(1536) column` (or whatever Plan 2 pinned).

Step 4 will write the migration with a default constant; Step 4a auto-syncs that constant from pinned.env so the operator never has to remember to hand-edit it. Note the value here for visual confirmation only.

After migration 0002 lands, this dim is **frozen** in the migration body. To change it later, write a new migration that ALTERs the column type — never edit migration 0002 retroactively.

- [ ] **Step 2: Extend `src/codeoracle/db/schemas.py`**

Append the following at the bottom of the file (do NOT replace existing content), and add `Integer` + `Vector` to the import block at the top.

Append:

```python
class Embedding(Base):
    """One embedded chunk of source/doc content, written by the embed_indexer worker.

    `dim` is read from infra/versions/pinned.env at migration time. Changing it
    requires a new migration to ALTER TYPE the column (pgvector column types are
    fixed at creation). The `Vector(1)` placeholder in the declarative class is
    intentional — it never round-trips to a CREATE TABLE because the migration
    is the source of truth. The placeholder exists so mapper_registry.configure()
    has a concrete type to wire up.

    LOAD-BEARING WARNING: tests MUST NOT call `Base.metadata.create_all()`. The
    `Vector(1)` placeholder would produce a 1-dim column — silently incompatible
    with the embed_indexer's pinned-dim vectors. The only schema-creation path is
    `alembic upgrade head` (which reads the `EMBEDDING_DIM` constant in migration
    0002, sed-synced from pinned.env). Tests requiring a DB schema use the
    `bootstrapped_db` fixture from conftest.py, which goes through alembic.
    """

    __tablename__ = "embeddings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    snapshot_sha: Mapped[str] = mapped_column(String(80), nullable=False, index=True)
    source_path: Mapped[str] = mapped_column(String(1024), nullable=False)
    chunk_start_line: Mapped[int] = mapped_column(Integer, nullable=False)
    chunk_end_line: Mapped[int] = mapped_column(Integer, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    embedding: Mapped[Any] = mapped_column(Vector(1), nullable=False)
    """`pgvector.sqlalchemy.Vector` round-trips as `numpy.ndarray` when numpy
    is installed and `list[float]` otherwise — annotating either one would lie
    about the actual return type to `ty`. `Mapped[Any]` is the honest signal;
    consumers (Plan 6 indexer worker) must call `.tolist()` defensively before
    treating the value as a list."""
```

Update the imports block to:

```python
from datetime import datetime
from typing import Any

from pgvector.sqlalchemy import Vector
from sqlalchemy import JSON, DateTime, Integer, String, Text
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import Mapped, mapped_column

from codeoracle.db.orm import Base
```

- [ ] **Step 3: Write `src/codeoracle/db/migrations/versions/0002_embeddings_table.py`**

```python
"""embeddings table

Revision ID: 0002
Revises: 0001
Create Date: 2026-05-21

The pgvector column dimension is hardcoded here to match the value pinned in
infra/versions/pinned.env at the time this migration was authored. To change
the dimension, write a NEW migration that runs ALTER TYPE on the column.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from pgvector.sqlalchemy import Vector


revision: str = "0002"
down_revision: Union[str, None] = "0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# EMBEDDING_DIM is the source of truth for this migration once committed.
# It is initially set to 1536 (text-embedding-3-small native dim from Plan 2),
# then auto-synced from infra/versions/pinned.env by Plan 3 Task 10 Step 3a
# BEFORE the first `alembic upgrade head` runs.
#
# After this migration is applied to any environment, the constant is frozen.
# To change the dim, write a NEW migration that ALTERs the column type — never
# edit this file retroactively.
EMBEDDING_DIM = 1536


def upgrade() -> None:
    op.create_table(
        "embeddings",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("snapshot_sha", sa.String(80), nullable=False),
        sa.Column("source_path", sa.String(1024), nullable=False),
        sa.Column("chunk_start_line", sa.Integer, nullable=False),
        sa.Column("chunk_end_line", sa.Integer, nullable=False),
        sa.Column("content", sa.Text, nullable=False),
        sa.Column("embedding", Vector(EMBEDDING_DIM), nullable=False),
    )
    op.create_index(
        "ix_embeddings_snapshot_sha",
        "embeddings",
        ["snapshot_sha"],
    )
    # HNSW index for ANN queries (Plan 9 Verifier will use this).
    # m=16, ef_construction=64 are pgvector defaults — appropriate for slice scale.
    op.execute(
        "CREATE INDEX ix_embeddings_embedding_hnsw "
        "ON embeddings USING hnsw (embedding vector_cosine_ops) "
        "WITH (m = 16, ef_construction = 64);"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_embeddings_embedding_hnsw;")
    op.drop_index("ix_embeddings_snapshot_sha", "embeddings")
    op.drop_table("embeddings")
```

- [ ] **Step 3a: Auto-sync the EMBEDDING_DIM constant from pinned.env**

The migration body shipped with the literal `1536` (the default Plan 2 records for `text-embedding-3-small` at native dim). If Plan 2 pinned a smaller value via the `dimensions` parameter, rewrite the constant in place. The script guards against three footguns: forgetting to `source pinned.env`, a stale pin that leaves `OPENAI_EMBEDDING_DIM` blank, and a non-integer value that would produce a `SyntaxError` Python only catches at `alembic upgrade` time.

```bash
source infra/versions/pinned.env

# Guard 1: variable must be set (forgot to source pinned.env, or pinned.env missing the line)
[ -n "$OPENAI_EMBEDDING_DIM" ] || {
  echo "ERROR: OPENAI_EMBEDDING_DIM is unset — did you 'source infra/versions/pinned.env'?" >&2
  exit 1
}

# Guard 2: must be an integer (POSIX-portable; [0-9][0-9]* avoids -E)
case "$OPENAI_EMBEDDING_DIM" in
  ''|*[!0-9]*)
    echo "ERROR: OPENAI_EMBEDDING_DIM='$OPENAI_EMBEDDING_DIM' is not an integer" >&2
    exit 1
    ;;
esac

sed -i "s/^EMBEDDING_DIM = [0-9][0-9]*$/EMBEDDING_DIM = $OPENAI_EMBEDDING_DIM/" \
  src/codeoracle/db/migrations/versions/0002_embeddings_table.py

# Guard 3: confirm the substitution happened (sed silently no-ops if the pattern misses)
grep '^EMBEDDING_DIM = ' src/codeoracle/db/migrations/versions/0002_embeddings_table.py | \
  grep -q "= $OPENAI_EMBEDDING_DIM$" || {
  echo "ERROR: sed did not update EMBEDDING_DIM — pattern miss?" >&2
  exit 1
}
```

Expected: silent success (the final `grep -q` exits 0). Run the next line to visually confirm:

```bash
grep '^EMBEDDING_DIM = ' src/codeoracle/db/migrations/versions/0002_embeddings_table.py
```

Expected: prints `EMBEDDING_DIM = <pinned_value>` (matches pinned.env).

Verify the file is well-formed:

```bash
uv run python -c "from pathlib import Path; print(Path('src/codeoracle/db/migrations/versions/0002_embeddings_table.py').read_text()[:600])"
```

Expected: file header + imports + `EMBEDDING_DIM = <pinned_value>` visible.

- [ ] **Step 4: Apply the migration**

```bash
uv run alembic upgrade head
```

Expected: `Running upgrade 0001 -> 0002, embeddings table`.

- [ ] **Step 5: Verify the column dim matches pinned.env**

```bash
source infra/versions/pinned.env
export PGPASSWORD=codeoracle
ACTUAL_DIM=$(psql -h localhost -p 5432 -U codeoracle -d codeoracle -t -A -c \
  "SELECT atttypmod FROM pg_attribute a JOIN pg_class c ON c.oid=a.attrelid \
   WHERE c.relname='embeddings' AND a.attname='embedding';")
unset PGPASSWORD
echo "embeddings.embedding column dim = $ACTUAL_DIM (expected $OPENAI_EMBEDDING_DIM)"
[ "$ACTUAL_DIM" = "$OPENAI_EMBEDDING_DIM" ] || { echo "MISMATCH"; exit 1; }
```

Expected: `embeddings.embedding column dim = 1536 (expected 1536)` and no MISMATCH.

If MISMATCH, edit `EMBEDDING_DIM` in the migration, run `uv run alembic downgrade -1 && uv run alembic upgrade head`, then re-check.

- [ ] **Step 6: Write `tests/integration/test_migrations_0002.py`**

```python
"""Migration 0002 (embeddings table) — schema-shape + vector round-trip."""

import os
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.engine import Engine


pytestmark = pytest.mark.integration

REPO_ROOT = Path(__file__).resolve().parents[2]


def _expected_dim() -> int:
    """Read the dim from infra/versions/pinned.env — the project's source of truth.

    Migration filenames are digit-prefixed (`0002_embeddings_table.py`), which
    are not valid Python identifiers, so we cannot import them as modules.
    Parsing pinned.env directly avoids that constraint entirely.
    """
    env_override = os.environ.get("OPENAI_EMBEDDING_DIM")
    if env_override:
        return int(env_override)
    pinned_env = REPO_ROOT / "infra" / "versions" / "pinned.env"
    if not pinned_env.exists():
        pytest.fail(f"pinned.env not found at {pinned_env} — Plan 2 not run?")
    for line in pinned_env.read_text().splitlines():
        if line.startswith("OPENAI_EMBEDDING_DIM="):
            return int(line.split("=", 1)[1].strip())
    pytest.fail("OPENAI_EMBEDDING_DIM not present in pinned.env")


def test_embeddings_table_exists(sync_engine: Engine):
    with sync_engine.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_name='embeddings' ORDER BY ordinal_position"
            )
        ).all()
    columns = {r[0] for r in rows}
    assert {"id", "snapshot_sha", "source_path", "chunk_start_line",
            "chunk_end_line", "content", "embedding"}.issubset(columns)


def test_embedding_column_dim_matches_pinned_env(sync_engine: Engine):
    """The most load-bearing assertion of Task 10: vector(N) must match pinned.env."""
    with sync_engine.connect() as conn:
        dim = conn.execute(
            text(
                "SELECT atttypmod FROM pg_attribute a JOIN pg_class c ON c.oid=a.attrelid "
                "WHERE c.relname='embeddings' AND a.attname='embedding'"
            )
        ).scalar()
    assert dim == _expected_dim(), (
        f"embeddings.embedding column dim ({dim}) does not match pinned.env "
        f"OPENAI_EMBEDDING_DIM ({_expected_dim()}). Migration 0002's EMBEDDING_DIM "
        "constant is the source of truth — a follow-up migration is required to change it."
    )


def test_hnsw_index_exists(sync_engine: Engine):
    with sync_engine.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT indexname FROM pg_indexes "
                "WHERE tablename='embeddings' AND indexname='ix_embeddings_embedding_hnsw'"
            )
        ).all()
    assert len(rows) == 1


def test_embedding_round_trip(sync_engine: Engine):
    """Insert a vector, query it back, delete. Proves the column accepts the right shape."""
    dim = _expected_dim()
    vec = [0.0] * dim
    vec[0] = 1.0  # unit-length on axis 0
    vec_literal = "[" + ",".join(str(x) for x in vec) + "]"

    with sync_engine.begin() as conn:
        # Use a unique snapshot_sha that won't collide
        conn.execute(
            text(
                "INSERT INTO embeddings "
                "(snapshot_sha, source_path, chunk_start_line, chunk_end_line, content, embedding) "
                f"VALUES ('test_emb_0002', '/test.py', 1, 10, 'hello', '{vec_literal}')"
            )
        )
        result = conn.execute(
            text("SELECT source_path FROM embeddings WHERE snapshot_sha='test_emb_0002'")
        ).one()
        assert result[0] == "/test.py"
        conn.execute(text("DELETE FROM embeddings WHERE snapshot_sha='test_emb_0002'"))


def test_ann_query_works(sync_engine: Engine):
    """Insert two vectors, ANN-query for the closer one."""
    dim = _expected_dim()
    vec_a = [0.0] * dim
    vec_a[0] = 1.0
    vec_b = [0.0] * dim
    vec_b[1] = 1.0  # orthogonal to A
    vec_query = [0.0] * dim
    vec_query[0] = 0.9  # closer to A

    def _lit(v: list[float]) -> str:
        return "[" + ",".join(str(x) for x in v) + "]"

    with sync_engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO embeddings "
                "(snapshot_sha, source_path, chunk_start_line, chunk_end_line, content, embedding) "
                "VALUES "
                f"('test_ann', '/a.py', 1, 10, 'a', '{_lit(vec_a)}'), "
                f"('test_ann', '/b.py', 1, 10, 'b', '{_lit(vec_b)}')"
            )
        )
        # Cosine distance ordering: A should rank first
        result = conn.execute(
            text(
                "SELECT source_path FROM embeddings "
                "WHERE snapshot_sha='test_ann' "
                f"ORDER BY embedding <=> '{_lit(vec_query)}' "
                "LIMIT 2"
            )
        ).all()
        paths = [r[0] for r in result]
        assert paths == ["/a.py", "/b.py"]
        conn.execute(text("DELETE FROM embeddings WHERE snapshot_sha='test_ann'"))
```

- [ ] **Step 7: Run the new test + the full integration suite**

```bash
uv run pytest tests/integration/ -v -m integration
```

Expected: 14 tests pass (9 from Tasks 8-9 + 5 from Task 10).

- [ ] **Step 8: Run lint + type**

```bash
uv run ruff check src/codeoracle/db/ tests/integration/test_migrations_0002.py
uv run ruff format --check src/codeoracle/db/ tests/integration/test_migrations_0002.py
uv run ty check src/codeoracle/db/
```

Expected: all clean.

- [ ] **Step 9: Commit**

```bash
git add src/codeoracle/db/schemas.py src/codeoracle/db/migrations/versions/0002_embeddings_table.py tests/integration/test_migrations_0002.py
git commit -m "Plan 3 task 10: migration 0002 embeddings table (vector dim from pinned.env)"
```

---

## Task 11: Alembic migration 0003 — `traces` table

**What this task delivers:** the `traces` table for ingested OpenTelemetry spans. Plan 7's `workers/traces_ingest` consumer pulls spans from Tempo and writes here; Plan 9's Verifier may use `TRACE_SPAN` evidence handles (slice routes `TRACE_SPAN` to `StubResolver` per spec §6.3 — but the schema needs to exist so a later plan can drop the stub without a schema migration). Plan 3 ships the schema; nothing reads it yet.

**Files:**
- Modify: `src/codeoracle/db/schemas.py` (add `Trace`)
- Create: `src/codeoracle/db/migrations/versions/0003_traces_table.py`
- Create: `tests/integration/test_migrations_0003.py`

- [ ] **Step 1: Extend `src/codeoracle/db/schemas.py`**

Append to the file:

```python
class Trace(Base):
    """One ingested OpenTelemetry span.

    Populated by workers/traces_ingest (Plan 7). Slice does not query this table;
    the schema exists so a later plan can light up TRACE_SPAN evidence resolution
    without a new migration.
    """

    __tablename__ = "traces"

    trace_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    span_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    parent_span_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    service_name: Mapped[str] = mapped_column(String(256), nullable=False)
    operation_name: Mapped[str] = mapped_column(String(256), nullable=False)
    start_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    end_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    attributes: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False, default=dict)
    snapshot_sha: Mapped[str | None] = mapped_column(String(80), nullable=True)
```

- [ ] **Step 2: Write `src/codeoracle/db/migrations/versions/0003_traces_table.py`**

```python
"""traces table

Revision ID: 0003
Revises: 0002
Create Date: 2026-05-21

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0003"
down_revision: Union[str, None] = "0002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "traces",
        sa.Column("trace_id", sa.String(64), primary_key=True),
        sa.Column("span_id", sa.String(64), primary_key=True),
        sa.Column("parent_span_id", sa.String(64), nullable=True),
        sa.Column("service_name", sa.String(256), nullable=False),
        sa.Column("operation_name", sa.String(256), nullable=False),
        sa.Column("start_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("end_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("attributes", sa.JSON, nullable=False, server_default="{}"),
        sa.Column("snapshot_sha", sa.String(80), nullable=True),
    )
    op.create_index("ix_traces_service_op", "traces", ["service_name", "operation_name"])
    op.create_index("ix_traces_snapshot_sha", "traces", ["snapshot_sha"])
    op.create_index("ix_traces_start_time", "traces", ["start_time"])


def downgrade() -> None:
    op.drop_index("ix_traces_start_time", "traces")
    op.drop_index("ix_traces_snapshot_sha", "traces")
    op.drop_index("ix_traces_service_op", "traces")
    op.drop_table("traces")
```

- [ ] **Step 3: Apply the migration**

```bash
uv run alembic upgrade head
```

Expected: `Running upgrade 0002 -> 0003, traces table`.

- [ ] **Step 4: Write `tests/integration/test_migrations_0003.py`**

```python
"""Migration 0003 (traces table) — schema + insert round-trip."""

from datetime import datetime, timezone

import pytest
from sqlalchemy import text
from sqlalchemy.engine import Engine


pytestmark = pytest.mark.integration


def test_traces_table_exists(sync_engine: Engine):
    with sync_engine.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_name='traces' ORDER BY ordinal_position"
            )
        ).all()
    columns = {r[0] for r in rows}
    assert {"trace_id", "span_id", "parent_span_id", "service_name", "operation_name",
            "start_time", "end_time", "attributes", "snapshot_sha"}.issubset(columns)


def test_traces_composite_primary_key(sync_engine: Engine):
    """Same trace_id, different span_id → both rows must be insertable."""
    with sync_engine.begin() as conn:
        now = datetime.now(timezone.utc).isoformat()
        conn.execute(text(
            "INSERT INTO traces (trace_id, span_id, service_name, operation_name, "
            "start_time, end_time, attributes) "
            f"VALUES ('t1', 's1', 'api', 'POST /v1/query', '{now}', '{now}', '{{}}')"
        ))
        conn.execute(text(
            "INSERT INTO traces (trace_id, span_id, service_name, operation_name, "
            "start_time, end_time, attributes) "
            f"VALUES ('t1', 's2', 'agent', 'compose', '{now}', '{now}', '{{}}')"
        ))
        count = conn.execute(text("SELECT COUNT(*) FROM traces WHERE trace_id='t1'")).scalar()
        assert count == 2
        conn.execute(text("DELETE FROM traces WHERE trace_id='t1'"))


def test_traces_indexes_present(sync_engine: Engine):
    with sync_engine.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT indexname FROM pg_indexes "
                "WHERE tablename='traces' AND schemaname='public'"
            )
        ).all()
    index_names = {r[0] for r in rows}
    for ix in ("ix_traces_service_op", "ix_traces_snapshot_sha", "ix_traces_start_time"):
        assert ix in index_names
```

- [ ] **Step 5: Run + lint + type**

```bash
uv run pytest tests/integration/ -v -m integration
uv run ruff check src/codeoracle/db/ tests/integration/test_migrations_0003.py
uv run ruff format --check src/codeoracle/db/ tests/integration/test_migrations_0003.py
uv run ty check src/codeoracle/db/
```

Expected: 17 integration tests pass (14 prior + 3 new); lint/format/type clean.

- [ ] **Step 6: Commit**

```bash
git add src/codeoracle/db/schemas.py src/codeoracle/db/migrations/versions/0003_traces_table.py tests/integration/test_migrations_0003.py
git commit -m "Plan 3 task 11: migration 0003 traces table"
```

---

## Task 12: End-to-end bootstrap reproducibility test

**What this task delivers:** the single test that ties Tasks 7-11 together — a fresh DB volume, then `bootstrap_db.sh` + `alembic upgrade head`, then a round-trip exercise: cypher round-trip + vector ANN + snapshot insert. The test proves Plan 3's deliverables are reproducible end-to-end; if a future change to any of the migrations breaks this, the test fails before merge.

**Files:**
- Create: `tests/integration/test_bootstrap_reproducibility.py`

- [ ] **Step 1: Tear down + rebuild the stack so we test from-scratch behavior**

```bash
cd infra/compose
docker compose --env-file ../versions/pinned.env down -v
docker compose --env-file ../versions/pinned.env up -d
cd ../..
sleep 5
```

Expected: volume removed, fresh compose comes up healthy.

- [ ] **Step 2: Verify a fresh DB has none of Plan 3's tables**

```bash
export PGPASSWORD=codeoracle
psql -h localhost -p 5432 -U codeoracle -d codeoracle -c "\dt"
unset PGPASSWORD
```

Expected: "Did not find any relations."

- [ ] **Step 3: Run bootstrap + alembic upgrade head**

```bash
bash infra/scripts/bootstrap_db.sh
uv run alembic upgrade head
```

Expected:
- Bootstrap prints "Bootstrap complete."
- Alembic runs 0001 → 0002 → 0003 in order.

- [ ] **Step 4: Write `tests/integration/test_bootstrap_reproducibility.py`**

```python
"""End-to-end bootstrap reproducibility — Plan 3 deliverables prove out together.

This test does NOT call bootstrap_db.sh or alembic upgrade itself (the conftest
session fixture already does). Instead it asserts that AFTER bootstrap + migrate,
the DB looks the way Plan 3 promised, and a cypher + vector + snapshot round-trip
all work."""

import pytest
from sqlalchemy import text
from sqlalchemy.engine import Engine
from sqlalchemy.ext.asyncio import AsyncSession


pytestmark = pytest.mark.integration


def test_all_three_plan3_tables_present(sync_engine: Engine):
    with sync_engine.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_schema='public' AND table_type='BASE TABLE'"
            )
        ).all()
    table_names = {r[0] for r in rows}
    assert "snapshots" in table_names
    assert "embeddings" in table_names
    assert "traces" in table_names


def test_alembic_version_is_head(sync_engine: Engine):
    """alembic_version table should hold the latest revision id (0003 in Plan 3)."""
    with sync_engine.connect() as conn:
        version = conn.execute(text("SELECT version_num FROM alembic_version")).scalar()
    assert version == "0003"


def test_both_extensions_installed(sync_engine: Engine):
    with sync_engine.connect() as conn:
        rows = conn.execute(
            text("SELECT extname FROM pg_extension WHERE extname IN ('age', 'vector')")
        ).all()
    names = {r[0] for r in rows}
    assert names == {"age", "vector"}


def test_age_oracle_graph_exists(sync_engine: Engine):
    with sync_engine.connect() as conn:
        names = conn.execute(
            text("SELECT name FROM ag_catalog.ag_graph WHERE name='codeoracle'")
        ).all()
    assert len(names) == 1


async def test_cypher_create_match_round_trip(async_session: AsyncSession):
    """Async path: create a node + edge, match, drop.

    No @pytest.mark.asyncio needed — pyproject.toml sets asyncio_mode = "auto".
    """
    await async_session.execute(text(
        "SELECT * FROM cypher('codeoracle', $$ "
        "CREATE (a:TestNode {name: 'x'}) "
        "CREATE (b:TestNode {name: 'y'}) "
        "CREATE (a)-[:LINKS_TO]->(b) "
        "$$) AS (v agtype)"
    ))
    rows = (await async_session.execute(text(
        "SELECT * FROM cypher('codeoracle', $$ "
        "MATCH (a:TestNode {name:'x'})-[:LINKS_TO]->(b:TestNode) "
        "RETURN b.name "
        "$$) AS (n agtype)"
    ))).all()
    assert len(rows) == 1
    assert "y" in str(rows[0][0])
    # cleanup
    await async_session.execute(text(
        "SELECT * FROM cypher('codeoracle', $$ "
        "MATCH (n:TestNode) DETACH DELETE n "
        "$$) AS (v agtype)"
    ))


async def test_symbol_existence_check_plans_index_scan(async_session: AsyncSession):
    """The agent's hot symbol-existence check must plan an Index Scan, not a
    Seq Scan, on the Symbol label (NFR D3-1). bootstrap_db.sql pre-creates the
    Symbol/File vlabels + btree property indexes on the agtype property-access
    expression AGE emits for a `{fqn, snapshot_sha}` map-match; without them
    every `_EXISTS_CYPHER` (Plan 10) full-scans the whole label table once per
    user query.

    `enable_seqscan = off` makes the assertion deterministic at any table size:
    on a tiny test table PG's cost model would prefer a Seq Scan even with the
    index present, so forcing it off proves the index *can serve the predicate*
    (the property under test) rather than that it merely wins on cost today.
    """
    sha = "d3_1_idx_test"
    await async_session.execute(text(
        "SELECT * FROM cypher('codeoracle', $$ "
        f"CREATE (:Symbol {{fqn: 'pkg.mod.fn', snapshot_sha: '{sha}'}}) "
        "$$) AS (v agtype)"
    ))
    await async_session.execute(text("SET LOCAL enable_seqscan = off"))
    plan_rows = (await async_session.execute(text(
        "EXPLAIN SELECT * FROM cypher('codeoracle', $$ "
        f"MATCH (s:Symbol {{fqn: 'pkg.mod.fn', snapshot_sha: '{sha}'}}) "
        "RETURN s.fqn $$) AS (fqn agtype)"
    ))).all()
    plan = "\n".join(str(r[0]) for r in plan_rows)
    assert "symbol_fqn_idx" in plan, f"existence check did not use the fqn index:\n{plan}"
    # cleanup — scope the delete to this test's sha so it can't touch other
    # tests' Symbol vertices in the session-scoped graph.
    await async_session.execute(text(
        "SELECT * FROM cypher('codeoracle', $$ "
        f"MATCH (n:Symbol {{snapshot_sha: '{sha}'}}) DETACH DELETE n "
        "$$) AS (v agtype)"
    ))


def test_full_snapshot_embedding_trace_insert(sync_engine: Engine):
    """Insert one row into each of the three Plan 3 tables; verify all readable; cleanup."""
    from datetime import datetime, timezone

    # Use a unique sha so this test doesn't collide with other tests' fixtures
    sha = "test_repro_sha"
    now = datetime.now(timezone.utc).isoformat()

    # snapshots
    with sync_engine.begin() as conn:
        conn.execute(text(
            "INSERT INTO snapshots (sha, status, changed_files, extractor_results) "
            f"VALUES ('{sha}', 'applied', ARRAY['/x.py'], '{{}}')"
        ))

    # embeddings (1-dim test value works because we don't enforce dim at insert; we just
    # need the vector column to accept *some* shape — full-dim is tested in 0002 tests)
    # Read the actual dim from the column type:
    with sync_engine.connect() as conn:
        dim = conn.execute(text(
            "SELECT atttypmod FROM pg_attribute a JOIN pg_class c ON c.oid=a.attrelid "
            "WHERE c.relname='embeddings' AND a.attname='embedding'"
        )).scalar()
        assert dim is not None and dim > 0

    vec_literal = "[" + ",".join("0.0" for _ in range(dim)) + "]"
    with sync_engine.begin() as conn:
        conn.execute(text(
            "INSERT INTO embeddings "
            "(snapshot_sha, source_path, chunk_start_line, chunk_end_line, content, embedding) "
            f"VALUES ('{sha}', '/x.py', 1, 10, 'x', '{vec_literal}')"
        ))

    # traces
    with sync_engine.begin() as conn:
        conn.execute(text(
            "INSERT INTO traces (trace_id, span_id, service_name, operation_name, "
            "start_time, end_time, attributes, snapshot_sha) "
            f"VALUES ('tr_repro', 'sp_repro', 'api', '/v1/query', '{now}', '{now}', "
            f"'{{}}', '{sha}')"
        ))

    # cross-table check
    with sync_engine.connect() as conn:
        s_count = conn.execute(text(
            f"SELECT COUNT(*) FROM snapshots WHERE sha='{sha}'"
        )).scalar()
        e_count = conn.execute(text(
            f"SELECT COUNT(*) FROM embeddings WHERE snapshot_sha='{sha}'"
        )).scalar()
        t_count = conn.execute(text(
            f"SELECT COUNT(*) FROM traces WHERE snapshot_sha='{sha}'"
        )).scalar()
        assert s_count == 1
        assert e_count == 1
        assert t_count == 1

    # cleanup
    with sync_engine.begin() as conn:
        conn.execute(text(f"DELETE FROM traces WHERE snapshot_sha='{sha}'"))
        conn.execute(text(f"DELETE FROM embeddings WHERE snapshot_sha='{sha}'"))
        conn.execute(text(f"DELETE FROM snapshots WHERE sha='{sha}'"))
```

- [ ] **Step 5: Run the full integration suite (this is the cumulative gate)**

```bash
uv run pytest tests/integration/ -v -m integration
```

Expected: 23 integration tests pass total (6 Task 8 + 3 Task 9 + 5 Task 10 + 3 Task 11 + 6 Task 12).

- [ ] **Step 6: Run lint + type once over the whole src/codeoracle tree**

```bash
uv run ruff check src/codeoracle/ tests/
uv run ruff format --check src/codeoracle/ tests/
uv run ty check src/codeoracle/
uv run python infra/scripts/check_file_length.py src/codeoracle tests
```

Expected: all four exit 0.

- [ ] **Step 7: Run the full coverage suite — unit + integration**

```bash
uv run pytest tests/ --cov=codeoracle --cov-report=term-missing
```

Expected: coverage on `src/codeoracle/core/`, `src/codeoracle/models/`, `src/codeoracle/db/` reads at least 90%. (The 85% project-wide floor from `pyproject.toml`'s CI gate is the actual ratchet; Plan 3's modules should comfortably exceed it.)

- [ ] **Step 8: Tear down the stack**

```bash
cd infra/compose
docker compose --env-file ../versions/pinned.env down -v
cd ../..
```

- [ ] **Step 9: Commit**

```bash
git add tests/integration/test_bootstrap_reproducibility.py
git commit -m "Plan 3 task 12: end-to-end bootstrap reproducibility test"
```

---

## Task 13: Final verification + push

**What this task delivers:** a final cross-cut sweep over Plan 3's outputs (correctness, layer purity, file-length, no debris), then a single `git push origin main` for the whole plan (per "Commit per task, push per plan" cadence).

**Files:** none. This task only inspects + pushes.

- [ ] **Step 1: Verify the commit log shows 12 commits from Plan 3**

```bash
git log --oneline origin/main..HEAD
```

Expected: 12 lines starting with `Plan 3 task N:`.

If the count is wrong, a task didn't end with a commit OR a commit landed unrelated to Plan 3 — investigate before pushing.

- [ ] **Step 2: Re-run the FULL gate suite (cold, simulating CI)**

```bash
# clean caches
rm -rf .pytest_cache .ruff_cache .ty_cache

# bring stack up, bootstrap, migrate
cd infra/compose
docker compose --env-file ../versions/pinned.env up -d
cd ../..
sleep 5
bash infra/scripts/bootstrap_db.sh
uv run alembic upgrade head

# the four CI gates
uv run ruff check src/codeoracle/ tests/
uv run ruff format --check src/codeoracle/ tests/
uv run ty check src/codeoracle/
uv run python infra/scripts/check_file_length.py src/codeoracle tests
uv run pytest tests/ --cov=codeoracle --cov-fail-under=85 -v

# tear down
cd infra/compose
docker compose --env-file ../versions/pinned.env down -v
cd ../..
```

Expected: every gate passes. If anything fails, fix it as a new commit before pushing — never use `--no-verify`.

- [ ] **Step 3: Confirm layer purity by inspection**

```bash
# models/ must not import from db/, services/, adapters/
grep -rn "^from codeoracle\.\(db\|services\|adapters\)" src/codeoracle/models/ || echo "OK: models/ is pure"

# core/ must not import from any other codeoracle.* package
grep -rn "^from codeoracle\.\(models\|db\|services\|adapters\)" src/codeoracle/core/ || echo "OK: core/ is pure"

# db/ may import from core + models; must not import from services or adapters
grep -rn "^from codeoracle\.\(services\|adapters\)" src/codeoracle/db/ || echo "OK: db/ does not reach into services/adapters"
```

Expected: three "OK" lines and no grep output before them.

- [ ] **Step 4: Confirm no LLM-SDK imports leaked (the Plan 1 ruff banned-api should have caught this, but belt-and-suspenders)**

```bash
grep -rn "^import \(openai\|anthropic\|litellm\)\|^from \(openai\|anthropic\|litellm\)" src/codeoracle/ \
  || echo "OK: no LLM SDK imports in src/codeoracle/"
```

Expected: "OK: no LLM SDK imports in src/codeoracle/".

- [ ] **Step 5: Verify the working tree is clean (only docs/orchestrator-state changes ride along)**

```bash
git status --short -- src/ infra/ tests/ alembic.ini pyproject.toml uv.lock .env.example
```

Expected: empty. Platform-side leftovers would mean a task didn't commit — never push that.

- [ ] **Step 6: Push**

```bash
git push origin main
```

Expected: 12 commits pushed cleanly. The next CI run picks them up and re-runs every gate from the cold-cache state.

- [ ] **Step 7: Tag the Plan 3 boundary so Plans 4-7 can reference it as a known-good baseline**

```bash
git tag plan-3-shipped
git push origin plan-3-shipped
```

Expected: tag created locally + pushed. Plans 4-7 reference this in their preconditions as "this is the baseline you inherit."

---

## What Plan 4 inherits from Plan 3

Plan 4 ("LLM provider abstraction") starts with:

- `src/codeoracle/core/config.py` — extends `Settings` with `LLMConfig` (matching the spec §5.4 nested-delimiter pattern already established).
- `src/codeoracle/core/logging.py` — the redaction processor strips top-level `api_key`/`token`/`secret`/`password` event keys as a second layer. Plan 4's provider `api_key` fields are `SecretStr` (primary defense: repr-masking when a config is nested in a log payload / exception / `model_dump()` / span — which the name-keyed processor cannot catch), matching Plan 8's `SourcebotConfig.api_key`.
- `src/codeoracle/models/llm.py` — `ChatMessage`, `ChatResponse`, `TokenUsage`, `ToolCall`, `ToolDefinition` are the cross-provider data contract. Plan 4's `LLMProvider` ABC accepts and returns these.
- `pyproject.toml` `[tool.ruff.lint.flake8-tidy-imports.banned-api]` — already bans `import openai|anthropic|litellm` outside `adapters/llm/providers/` (Plan 1 wired this up).
- `pyproject.toml` `[tool.pytest.ini_options].asyncio_mode = "auto"` — pytest-asyncio convention established by Plan 3's Task 8 dual-engine AGE-listener test; Plan 4's LLM-provider integration tests inherit the auto-mode marker behavior.
- `src/codeoracle/core/exceptions.py` — `CodeOracleError` base class (shipped in Plan 3 Task 2) subclassed by Plan 4 for its LLM-provider error hierarchy (provider-specific subclasses inherit retry/log semantics from the base).
- `pyproject.toml` `[tool.pytest.ini_options].markers` list — Plan 3 establishes the markers-registration block; Plan 4 appends its `live_llm` marker without triggering pytest-unknown-marker warnings.
- `src/codeoracle/adapters/__init__.py` — adapters package marker (shipped in Plan 3's layered scaffolding); precondition for Plan 4's `adapters/llm/` subpackage import resolution.
- `src/codeoracle/core/config.py` `Settings.model_config.env_nested_delimiter = "__"` — Plan 3 establishes the double-underscore nested-delimiter convention; Plan 4 relies on it for `CODEORACLE_LLM_OPENAI__API_KEY` and other nested env-var parsing in `LLMConfig`.
- `.env.example` LLM-section placeholder block — Plan 1 Task 13 ships the `.env.example` file stub; Plan 3 Task 7 Step 2 overwrites with the full structure (Database / Valkey / Tempo placeholder rows); Plan 4 appends `CODEORACLE_LLM_ENABLED` / `CODEORACLE_LLM_PROVIDER` / `CODEORACLE_LLM_OPENAI__API_KEY` placeholders into the same file.
- `CODEORACLE_EMBEDDING__*` env-var namespace reservation — Plan 3's `Settings` class reserves the `CODEORACLE_EMBEDDING__*` env-prefix as a sibling to `LLMConfig` (concrete `EmbeddingsConfig` fields land in Plan 6); Plan 4's `CODEORACLE_LLM_*` namespace must respect this non-collision invariant.

## What Plan 5 inherits from Plan 3

Plan 5 ("Worker framework + Joern vertical") starts with:

- `infra/compose/docker-compose.yml` — postgres + valkey services up + healthy (Plan 3 appended them onto Plan 1A's observability skeleton); Plan 5 appends worker + joern service entries.
- `infra/scripts/bootstrap_db.sh` — re-runs cleanly in CI integration tests.
- `src/codeoracle/db/session.py` — the engine + session-factory pattern; Plan 5's workers compose this in `workers/_base/wiring.py`.
- `src/codeoracle/db/schemas.py` — `Snapshot`, `Embedding`, `Trace` tables already exist. Plan 5 writes the first repositories (`db/repositories/snapshots.py`, `db/repositories/graph.py`) against the Plan 3 schema.
- `src/codeoracle/models/snapshot.py` — Plan 5's snapshot wiring consumes + emits these.
- alembic — Plan 5 may add migrations (e.g. a `files` registry or graph-extractor state tables); the pattern is established.
- alembic migration `0001_snapshots_table.py` (Plan 3 Task 9) — named-migration precedent; Plan 5's `db/repositories/snapshots.py` writes rows against the `snapshots` table this migration creates.
- alembic migration `0002_embeddings_table.py` (Plan 3 Task 10) — named-migration precedent (and pgvector-aware migration shape); Plan 5's worker-framework migrations follow the same alembic revision-ID style.
- alembic migration `0003_traces_table.py` (Plan 3 Task 11) — named-migration precedent; Plan 5's joern-vertical worker traces inherit the table shape this migration creates.
- `infra/scripts/bootstrap_db.sql` — payload SQL file shipped alongside `bootstrap_db.sh` (Plan 3 Task 7); Plan 5's CI integration tests source it directly (or via the .sh wrapper) to set up the AGE/pgvector extension load order.
- `testcontainers[redis]` dev dep — Plan 3's `pyproject.toml [dependency-groups].dev` ships testcontainers with the redis extra; Plan 5's worker-framework integration tests inherit the dev-dep entry.
- `tests/integration/conftest.py` fixtures (`bootstrapped_db` / `sync_engine` / `async_engine` / `async_session`) — Plan 3 Task 8 ships these alongside `db/session.py`; Plan 5's worker-framework integration tests reuse them directly for engine + session wiring under the testcontainers postgres fixture.
- `src/codeoracle/core/logging.py` `configure_logging` helper — Plan 3 Task 3 ships the structlog wiring + §7.9 secret-redaction processor; Plan 5's worker loggers call `configure_logging` once at worker start-up.
- `src/codeoracle/db/session.py` `make_async_engine()` / `make_async_session_factory()` — Plan 3 Task 8 exports these factory functions; Plan 5's `workers/_base/wiring.py` calls them directly to compose the worker DB-session pattern.
- `src/codeoracle/core/config.py` `get_settings()` accessor + `Settings.environment` field — Plan 3 Task 4 exports the canonical settings source; Plan 5's `workers/_base/wiring.py` reads `get_settings().environment` to branch worker bring-up logic by environment.
- `src/codeoracle/core/config.py` `IndexingConfig` nested model + `Settings.indexing` field (`repo_root` / `extractor_kinds`) — added by the 2026-05-26 amendment alongside `ApiConfig`. Plan 5's `reindex_orchestrator/__main__.py` (Task 13a, added by the same-date Plan 11 async-job pivot) reads `settings.indexing.repo_root` + `tuple(settings.indexing.extractor_kinds)` to construct the `IndexingService` instance the consumer drives. Env-var namespace: `CODEORACLE_INDEXING__REPO_ROOT` + `CODEORACLE_INDEXING__EXTRACTOR_KINDS` (JSON-list parse). `extractor_kinds` defaults to `["joern"]` matching Plan 5's slice baseline; Plan 6 overrides to `["joern","scip","opengrep","embed"]` via env (no Plan 3 sub-amendment needed). `repo_root` defaults to `Path.cwd()` matching the existing CLI `--repo-root` Typer option default. The advisory-lock timeout itself stays on `ApiConfig.lock_acquisition_timeout_seconds` (not moved to IndexingConfig) — see IndexingConfig docstring rationale.
- `src/codeoracle/core/exceptions.py` `CodeOracleError` base exception class — Plan 3 Task 2 ships the base class; Plan 5 subclasses it for worker-specific exceptions (`WorkerExecutionError`, `WorkerStartupError`).

## What Plan 6 inherits from Plan 3

Plan 6 ("Snapshot extractors + embeddings + project-mode framework") starts with:

- `src/codeoracle/db/session.py` + `src/codeoracle/db/schemas.py` (`Embedding` table) — Plan 6's `embed_indexer` worker writes rows against the `vector(dim)` column sized at migration time from `pinned.env`.
- `src/codeoracle/db/repositories/snapshots.py` + `db/repositories/graph.py` (from Plan 5) — Plan 6 writes the third repository, `db/repositories/embeddings.py`, against the same `db/session.py` pattern.
- `tests/conftest.py` `async_session_factory` test fixture — Plan 3 Task 8 ships the fixture (applies migrations on container startup); Plan 6's `embed_indexer` integration tests consume it for per-test session isolation.
- Migration `0002_embeddings_table.py` + HNSW index `ix_embeddings_embedding_hnsw` — Plan 3 Task 6 ships the migration creating the `embeddings` table with the HNSW index; Plan 6's `embed_indexer` performs k-NN queries through this index for semantic search.
- `src/codeoracle/core/config.py` `EmbeddingConfig` (sibling to `LLMConfig`, required-no-default `model` and `dim` fields) — Plan 3 Task 4 reserves the namespace; Plan 6 extends it with `embed_chunk_lines`, `embed_chunk_overlap` (and `doc_chunk_max_lines`) for chunking strategy.
- `src/codeoracle/db/session.py` `AsyncSessionLocal` factory + `db_session_factory` DI shape — Plan 3 Task 8 establishes the constructor-injected session-factory contract; Plan 6's `embed_indexer` consumes the factory via DI.
- Migration baseline `0001_snapshots_table.py` + `0002_embeddings_table.py` + `0003_traces_table.py` — Plan 3 Task 6 ships the chain; Plan 6 Task 4 appends migration `0005` extending the chain (numbering precedent established by Plan 3).
- `src/codeoracle/models/snapshot.py` `SnapshotStatus` enum (members: `PENDING` / `APPLYING` / `APPLIED` / `FAILED`; no `READY` member) — Plan 3 Task 5 ships the enum; Plan 6's `embed_indexer` status-transition logic depends on the exact member set (load-bearing — a `READY` member would mis-signal).
- `src/codeoracle/db/session.py` `make_async_engine()` Settings-reading contract — Plan 3 Task 8 establishes that `make_async_engine()` reads the DSN via `DBConfig.async_url()` + env-tunable pool config (`pool_size`/`max_overflow`/`pool_timeout`, env `CODEORACLE_DB__POOL_SIZE` / `CODEORACLE_DB__MAX_OVERFLOW` / `CODEORACLE_DB__POOL_TIMEOUT`) from `core/config.py`; Plan 6's `embed_indexer` constructs its `AsyncEngine` via this factory (no direct env-var reads).
- `pyproject.toml` `pgvector` SQLAlchemy codec dep — Plan 3 Task 1 pins the dep in `[project].dependencies`; Plan 6's `db/schemas.py` `Embedding` table imports `pgvector.sqlalchemy.Vector` for the `embedding` column type (per-class transitive-lockfile-consumer enumeration).

## What Plan 7 inherits from Plan 3

Plan 7 ("ContinuousConsumer + traces_ingest worker + TracesRepository") starts with:

- `src/codeoracle/db/session.py` + `src/codeoracle/db/schemas.py` (`Trace` table from migration 0003) — Plan 7 writes `db/repositories/traces.py` against the schema established here.
- `src/codeoracle/models/evidence.py` (`EvidenceHandle`, `HandleType.TRACE_SPAN`) — Plan 7's ContinuousConsumer emits `TRACE_SPAN` handles.
- `infra/compose/docker-compose.yml` — postgres + valkey + the observability stack (from Plan 1A) are already present; Plan 7 appends the `traces_ingest` worker service entry.
- `src/codeoracle/core/config.py` `Settings(BaseSettings)` env-cascade pattern (env vars → nested config classes via `env_nested_delimiter = "__"`) — Plan 3 Task 4 establishes the pattern; Plan 7's `TracesConfig` is nested under `Settings` following this convention.
- `src/codeoracle/db/session.py` `async_session_factory` (composition root) — Plan 3 Task 8 exports the factory symbol; Plan 7's `TracesRepository` is constructed at the composition root with this factory injected.
- `src/codeoracle/core/logging.py` `get_logger(name) → structlog.stdlib.BoundLogger` — Plan 3 Task 3 exports the helper; Plan 7's `ContinuousConsumer` uses it for `tick.*` lifecycle events.
- `pyproject.toml` `pytest-asyncio` dev dep — Plan 3 Task 1 declares it in `[dependency-groups].dev`; Plan 7's `ContinuousConsumer` tick-loop tests inherit the dep without redeclaring.
- `tests/integration/conftest.py` `testcontainers-python` Postgres fixture — Plan 3 Task 8 wires `testcontainers[postgres]` for integration tests; Plan 7's `TracesRepository` integration tests reuse this fixture as the DB-backing container.
- Alembic revision `0001_snapshots_table.py` — Plan 3 Task 6 establishes the migration-numbering precedent; Plan 7 Task 5 appends migration `0006` extending the chain (`0001 → 0002 → 0003 → 0006`).
- Alembic revision `0002_embeddings_table.py` — Plan 3 Task 6 ships the second-link migration; Plan 7 inherits it as part of the baseline chain.
- `tests/conftest.py` `async_session_factory` test fixture (applies migrations on container startup) — Plan 3 Task 8 wires the fixture; Plan 7's integration tests reuse it for per-test DB-state isolation (distinct from the production `async_session_factory` symbol enumerated in EDIT-3.36).
- `src/codeoracle/core/config.py` `get_settings()` `@lru_cache` singleton accessor — Plan 3 Task 4 ships it; Plan 7's `TracesConfig` wiring calls `get_settings()` once at module-init to read its `Settings` slice.
- `src/codeoracle/db/session.py` `make_async_engine()` — Plan 3 Task 8 exports the AsyncEngine factory; Plan 7's wiring composes it directly at the composition root (distinct from the umbrella `db/session.py` bullet — this is the per-symbol contract).

## What Plan 8 inherits from Plan 3

Plan 8 ("HandleResolverService + 4 resolvers") starts with:

- `src/codeoracle/models/evidence.py` `Citation` — Plan 3 Task 5 ships the model; Plan 8 already provides `Citation` to Plan 9 in Plan 3's edge table, but Plan 8's canonical table at L24 explicitly inherits it (parallel enumeration).
- `src/codeoracle/models/evidence.py` `Claim` — Plan 3 Task 5 ships the model; Plan 8's canonical table at L24 explicitly inherits it (post-scoring claim type consumed by `HandleResolverService.resolve_one`).
- `src/codeoracle/models/evidence.py` `Verdict` — Plan 3 Task 5 ships the model; Plan 8's canonical table at L24 explicitly inherits it (verdict-status type returned by resolvers' tri-state output).
- `src/codeoracle/models/evidence.py` `EvidenceHandle` — Plan 3 Task 5 ships the model; Plan 8's `HandleResolverService` constructs `EvidenceHandle` instances per resolver verdict (Plan 8 is the primary constructor — Plans 9 and 10 are consumers of resolver output).
- `src/codeoracle/core/exceptions.py` `CodeOracleError` base class — Plan 3 Task 2 ships the base; Plan 8 subclasses it for `SourcebotError` and `ResolverError` (inheriting retry/log semantics from the base).
- `src/codeoracle/core/logging.py` `structlog` SecretStr-redaction processor (§7.9) — Plan 3 Task 3 ships the processor; Plan 8's resolvers rely on it to redact `api_key` fields from log records (Plan 8 L406 explicit dependency).
- `src/codeoracle/core/config.py` `env_nested_delimiter = "__"` double-underscore nested-delimiter convention — Plan 3 Task 4 sets the BaseSettings `model_config`; Plan 8's `SourcebotConfig` follows the convention for nested env-var binding (Plan 8 L513 explicit dependency).
- `pyproject.toml` `[tool.pytest.ini_options].asyncio_mode = "auto"` convention — Plan 3 Task 1 establishes the marker; Plan 8's async resolver tests inherit the auto-mode behavior (Plan 8 L1075 explicit "Plan 3 / Plan 7 convention").
- `src/codeoracle/db/session.py` AGE `LOAD` + `search_path` connect listener — Plan 3 Task 8 ships the SQLAlchemy connect listener that loads the AGE extension and sets `search_path = "$user", public, ag_catalog`; Plan 8's `GraphQueryResolver` inherits the agtype-as-quoted-string contract (no Python-side type codec is registered — Plan 8 L2719 explicit).

## What Plan 9 inherits from Plan 3

Plan 9 ("VerifierService + TrustMatrixService") starts with:

- `src/codeoracle/models/evidence.py` `HandleType` — Plan 3 Task 5 ships the enum; Plan 9 L21 explicitly requires it (Plan 3 currently lists `HandleType` only as a Plan-8 provider; this row enumerates the Plan-9 consumer).
- `src/codeoracle/models/evidence.py` `EvidenceHandle` — Plan 3 Task 5 ships the model; Plan 9 L21 demonstrably consumes it (returned by Plan 8's resolvers and accepted by `VerifierService.verify`).
- `src/codeoracle/models/evidence.py` `ResolvedCitation` — Plan 3 Task 5 ships the model; Plan 9's `VerifierService._resolve_one` returns it (citation enriched with resolver verdict + provenance).
- `src/codeoracle/core/logging.py` `get_audit_logger(name) → structlog.stdlib.BoundLogger` — Plan 3 Task 3 ships the accessor (wrapper floor fixed at INFO, immune to `settings.log_level`); Plan 9 L22/L1369 imports it as `_AUDIT` in `verifier/service.py` so the spec §8.5 crit. 11 citation-resolution lines survive at any prod verbosity (cross-plan edit applied alongside Plan 9 — Plan 9 Deviation 4).
- `src/codeoracle/core/logging.py` `get_logger(name) → structlog.stdlib.BoundLogger` — Plan 3 Task 3 ships the helper; Plan 9 L826 imports it ONLY in `test_service.py`'s anti-mask control (proving a plain `log_level`-filtered `info` is dropped under the prod WARNING floor while the `_AUDIT` channel survives) — NOT in `VerifierService` itself.
- `pyproject.toml` `structlog` dep (with SecretStr redaction pipeline) — Plan 3 Task 1 pins the dep; Plan 9 transitively inherits via `get_audit_logger` / `get_logger` (per-class transitive-lockfile-consumer enumeration).
- `pyproject.toml` `pydantic` dep — Plan 3 Task 1 pins the dep; Plan 9's `Verdict` / `Claim` models extend `pydantic.BaseModel` (per-class transitive-lockfile-consumer enumeration).

## What Plan 10 inherits from Plan 3

Plan 10 ("LangGraph agent orchestration") starts with:

- `src/codeoracle/models/query.py` `Query` — Plan 3 Task 5 ships the model; Plan 10's agent ingress accepts `Query` (umbrella-split of Plan 3 provides.45 — Plan 10 imports this per-class explicitly per Plan 10 L23).
- `src/codeoracle/models/query.py` `QueryResult` — Plan 3 Task 5 ships the model; Plan 10's agent returns `QueryResult` (umbrella-split of provides.45).
- `src/codeoracle/models/query.py` `QueryStatus` — Plan 3 Task 5 ships the enum; Plan 10's agent state machine reads `QueryStatus` (umbrella-split of provides.45).
- `src/codeoracle/models/evidence.py` `DraftClaim` — Plan 3 Task 5 ships the model; Plan 10's agent constructs `DraftClaim` instances pre-verification (umbrella-split of provides.45; mirrors per-class shape Plan 3 already uses for Plan 9).
- `src/codeoracle/models/evidence.py` `Claim` — Plan 3 Task 5 ships the model; Plan 10's agent consumes verified `Claim` instances post-verification (umbrella-split of provides.45).
- `src/codeoracle/models/evidence.py` `DraftCitation` — Plan 3 Task 5 ships the model; Plan 10's agent constructs `DraftCitation` instances during draft assembly (umbrella-split of provides.45).
- `src/codeoracle/models/evidence.py` `EvidenceHandle` — Plan 3 Task 5 ships the model; Plan 10's agent passes `EvidenceHandle` to Plan 8's resolvers and consumes returned handles in the verify chain (umbrella-split of provides.45).
- `src/codeoracle/models/evidence.py` `HandleType` — Plan 3 Task 5 ships the enum; Plan 10's agent branches on `HandleType` to dispatch to the correct resolver (umbrella-split of provides.45).
- `src/codeoracle/models/evidence.py` `Verdict` — Plan 3 Task 5 ships the model; Plan 10's agent receives `Verdict` from Plan 9's `VerifierService.verify` and propagates verdicts into `QueryResult` (umbrella-split of provides.45).
- `src/codeoracle/models/evidence.py` `VerdictStatus` — Plan 3 Task 5 ships the enum; Plan 10's agent inspects `VerdictStatus` to decide refusal-vs-success in the final `QueryResult` (umbrella-split of provides.45).
- `src/codeoracle/models/snapshot.py` `Snapshot` — Plan 3 Task 5 ships the model; Plan 10's agent reads `Snapshot` rows to resolve `snapshot_id` references during query handling (umbrella-split of provides.45).
- `src/codeoracle/models/llm.py` `ChatMessage` — Plan 3 Task 5 ships the model; Plan 10's agent constructs `ChatMessage` instances for LLM turns via Plan 4's provider abstraction (umbrella-split of provides.45).
- `src/codeoracle/models/llm.py` `ChatResponse` — Plan 3 Task 5 ships the model; Plan 10's agent receives `ChatResponse` from Plan 4's `LLMProvider.chat` and inspects tool_calls + content (umbrella-split of provides.45).
- `src/codeoracle/models/llm.py` `ToolCall` — Plan 3 Task 5 ships the model; Plan 10's agent inspects `ToolCall` entries to dispatch tool invocations during the agent loop (umbrella-split of provides.45).
- `src/codeoracle/models/llm.py` `ToolDefinition` — Plan 3 Task 5 ships the model; Plan 10's agent declares available tools by passing `ToolDefinition` list to `LLMProvider.chat` (umbrella-split of provides.45).
- `src/codeoracle/models/llm.py` `TokenUsage` — Plan 3 Task 5 ships the model; Plan 10's agent accumulates `TokenUsage` from each LLM turn into `QueryResult.token_usage_total` (umbrella-split of provides.45).
- `src/codeoracle/core/exceptions.py` `CodeOracleError` base class — Plan 3 Task 2 ships the base; Plan 10's agent nodes catch `CodeOracleError` at node boundaries to propagate structured failures into `QueryResult.error` (inheriting retry/log semantics from the base; mirrors the per-class `CodeOracleError` enumeration Plan 3 already exposes for Plan 8).
- `src/codeoracle/core/config.py` `AgentConfig` nested model + `Settings.agent` field — Plan 3 Task 2 ships the `AgentConfig` BaseModel (`host` / `port` / `shared_token`) and the `agent: AgentConfig = Field(default_factory=AgentConfig)` field on `Settings`; Plan 10's agent process (Task 11) binds uvicorn to `settings.agent.host`:`settings.agent.port` and reads `settings.agent.shared_token` once Slice+1 reactivates auth. Env-var namespace: `CODEORACLE_AGENT__HOST` / `CODEORACLE_AGENT__PORT` / `CODEORACLE_AGENT__SHARED_TOKEN`.

## What Plans 8 – 12 inherit from Plan 3

- **Plan 8** (HandleResolverService + 4 resolvers): the `EvidenceHandle`/`HandleType` model contract from `models/evidence.py`. Plan 8's resolvers consume handles produced by Plans 5-7.
- **Plan 9** (VerifierService + TrustMatrixService): the entire `DraftClaim`/`Claim`/`Citation`/`DraftCitation`/`Verdict`/`VerdictStatus`/`TrustLevel`/`SourceKind` type-system invariant from `models/evidence.py` + `models/trust.py`. Plan 9 enforces the type contract (`verify(claim: Claim)` accepts only post-scoring claims). Plan 9 returns `Verdict` in-memory; it does NOT persist verdicts.
- **Plan 10** (LangGraph agent): all `models/` types; instantiates `Settings` from `core/config.py`; uses `configure_logging` from `core/logging.py`; depends on `core/telemetry.py` from **Plan 1A** for OTel SDK initialization.
- **Plan 11** (API): inherits `models/query.py` (`Query`, `QueryResult`, `QueryStatus`) as the `POST /v1/query` contract; `core/logging.py` `configure_logging` (Plan 3 Task 3 — the API `__main__` calls `configure_logging(settings)` once at startup; **not** Plan 1A, which owns `telemetry.py` / `init_telemetry` only); `core/config.py` `ApiConfig` nested model + `Settings.api` field (`host` / `port` / `agent_base_url` / `agent_request_timeout_seconds` / `reindex_timeout_seconds` / `lock_acquisition_timeout_seconds`) for the API process's bind address + outbound agent client config + reindex lock-wait window. Env-var namespace: `CODEORACLE_API__HOST` / `CODEORACLE_API__PORT` / `CODEORACLE_API__AGENT_BASE_URL` / `CODEORACLE_API__AGENT_REQUEST_TIMEOUT_SECONDS` / `CODEORACLE_API__REINDEX_TIMEOUT_SECONDS` / `CODEORACLE_API__LOCK_ACQUISITION_TIMEOUT_SECONDS`. Plan 11 Task 3 also adds a top-level `Settings.repo_root: str = "."` field (env var `CODEORACLE_REPO_ROOT`) for the repository root the API resolves snapshots against. The `agent_base_url` default `http://127.0.0.1:8081` matches `AgentConfig.host` / `AgentConfig.port` defaults for local single-host slice deployment; override for multi-host (Plan 10 Deviation 9 reactivation trigger). Post the 2026-05-26 sync→async pivot (Plan 11 amendment `fc0894a`; Plan 5 amendment `40885bf`), `lock_acquisition_timeout_seconds` tunes the **worker-side** advisory-lock wait window — `pg_advisory_lock(0x46AE0001)` is acquired and held by the `ReindexOrchestratorConsumer` worker, NOT on any API request thread (see the `ApiConfig.lock_acquisition_timeout_seconds` field docstring above). The API's `POST /v1/reindex` calls `IndexingService.enqueue_reindex`, which never touches the lock, so it cannot raise `AdvisoryLockTimeoutError` and never returns HTTP 409 (Plan 11 invariant 4 amended): reindex is async — POST returns 202 + `Snapshot(status=PENDING)` and the client polls `GET /v1/reindex/{snapshot_sha}` (Plan 11 invariant 11). Plan 11 is also expected to be the first plan that persists verdict rows — see "Cross-plan reconciliation notes" below for the EvidenceRepository ownership pointer.
- **Plan 12** (MCP): inherits `models/query.py` (`Query` / `QueryStatus` / `QueryResult`) as the MCP `explain_symbol` tool contract — the `explain_symbol` tool's `inputSchema` is `Query.model_json_schema()` and the handler does `Query.model_validate(arguments)` then forwards to `AgentClient.run(query)` (Plan 12 DECIDE-F — no separate tool-definition model). Plan 12 (Task 1 amendment) also inherits `core/config.py` `McpConfig` + `core/logging.py` `configure_logging(stream=...)`, and `models/snapshot.py` (`Snapshot` / `SnapshotStatus`) as the reindex-tool output. (Plan 12's `ReindexRequest` tool input comes from `models/reindex.py`, which Plan 12 inherits from **Plan 11**, not Plan 3.)

## What Plan 13 inherits from Plan 3

- **`Settings` + `ApiConfig` + `DBConfig`** — consumed via env by Plan 13's slice processes.
- **`Query` / `QueryResult` / `Snapshot` / `Handle` / `HandleType`** — consumed by every Plan 13 integration test as the assertion-target Pydantic models.

## Deviations from spec

These are points where Plan 3's shipped Pydantic shapes intentionally exceed the spec's *original* plain-model description. The corpus form is the better one; the spec was the stale party and was partially reconciled in #44 (`spec-reconcile: partially applied (#44)` — see the per-field breakdown below).

- **`EvidenceHandle` + `TokenUsage` are stricter than the spec's original §plain-models shape.** Spec §plain-models *originally* described both as plain attribute-bag models: `TokenUsage` (`input_tokens: int` / `output_tokens: int`, no config, no derived field) and `EvidenceHandle` (`type` / `locator` / `snapshot_sha: str`, with only a `field_validator` scheme check). Plan 3 ships them hardened: `EvidenceHandle` (Task 5, `models/evidence.py`) adds `frozen=True` (`ConfigDict(frozen=True)`) and constrains `snapshot_sha` with `StringConstraints(min_length=4, max_length=80)` (it keeps the spec's `scheme://` `locator` validator); `TokenUsage` (Task 5, `models/llm.py`) adds `frozen=True`, constrains `input_tokens` / `output_tokens` with `Field(ge=0)`, and exposes a derived `total_tokens` property the spec does not name. **Rationale:** evidence handles and token tallies are immutable provenance once minted — `frozen=True` makes accidental post-construction mutation a type error rather than a silent corruption, the `StringConstraints` reject malformed SHAs at the boundary, and `total_tokens` is a pure derivation (no stored state) so it cannot drift from its operands. These models are deliberately NOT weakened to the plain-model shape. `spec-reconcile: partially applied (#44)` — the spec §plain-models entries were amended in #44 to add `frozen=True` (`ConfigDict(frozen=True)`) to both `TokenUsage` and `EvidenceHandle` and to constrain `EvidenceHandle.snapshot_sha` with `StringConstraints(min_length=4, max_length=80)` (spec L539 / L766 / L769). The remaining hardening — `Field(ge=0)` on `TokenUsage.input_tokens` / `output_tokens` and the derived `total_tokens` property — was held back from #44 as a DOC-ONLY residual (the #44 spec-edit authorization was scoped to `frozen` + `StringConstraints`); the D-3.3 resolution now proposes the matching spec §5.2 amendment (add `Field` to the `models/llm.py` import, constrain both `TokenUsage` fields with `Field(ge=0)`, and add the `total_tokens` property + a changelog row), completing the #44 partial reconcile so plan and spec agree.

## Cross-plan reconciliation notes

When Plan 3's revisions land, the following downstream items become true and must be audited when their owner plans are revised:

- **Plan 7's "Inherits from Plan 3" block needs re-aligning to the real Plan 7 scope.** Plan 7 is "ContinuousConsumer + traces_ingest worker + TracesRepository", not the originally-imagined "API + MCP + agent". Plan 7's revision task (#11 in the revision sweep) carries this fix.
- **Plan 2 canonicalized the pgvector pin key on `PGVECTOR_INSTALL_METHOD` (audit D-2.6 / P0-9, resolved).** Plan 3's preconditions gate (L98-106 above) is narrowed back to a single accepted key (`PGVECTOR_INSTALL_METHOD` ∈ {`preinstalled`, `separate-image`, `custom-dockerfile`}). The previous 3-key transitional acceptance (`IMAGE_PGVECTOR=` OR `PGVECTOR_INSTALL_METHOD=` OR `PGVECTOR_SOURCE=`) is retired in the same follow-up commit chain that landed Plan 2 Task 3's emission switch. `IMAGE_PGVECTOR` continues to pin the image digest in the `separate-image` branch only — it's an independent piece of metadata, not an install-method indicator, and isn't gated here.
- **EvidenceRepository is Slice+1, not Plan 3 work.** Verified empirically: no plan in the current series (5-10) calls `EvidenceRepository.save_verdict()` or persists `Verdict`/`Claim` rows. Plan 9's `VerifierService.verify()` returns `Verdict` in-memory; no DB write. Whichever later plan first persists verdict rows (likely Plan 11 when the API exposes `GET /v1/queries/<id>`) must own `db/repositories/evidence.py` + a new migration (0004) for `verdicts` + `claims` tables. Until then, the orphan call-out in `docs/superpowers/plan-audit-findings.md` stays open.
- **Plan 1A owns `core/telemetry.py`.** Plan 3 originally deferred telemetry to "Plan 7 (which lands the API + agent + MCP processes)". The real owner is now Plan 1A (Operational Infrastructure — G7 in the orphan-ownership map), because the observability stack (OTel Collector + Tempo + Prometheus + Grafana) and the telemetry SDK ship together. Plan 10 (agent) and Plan 11+ (API/MCP) inherit telemetry from Plan 1A, not from Plan 3 or Plan 7.

---

**End of Plan 3.**
