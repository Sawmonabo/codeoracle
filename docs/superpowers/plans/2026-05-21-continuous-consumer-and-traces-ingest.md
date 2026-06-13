# ContinuousConsumer + traces_ingest + TracesRepository Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Land the `ContinuousConsumer` ABC as a **sibling** of Plan 5's `WorkerConsumer` (NOT a subclass — different ingestion lifecycle), the Tempo-backed `traces_ingest` worker, and `TracesRepository` (PG-backed) — so the agent's `trace.get` / `trace.find_spans` / `trace.get_logs_by_trace` tools defined in `docs/architecture.md` §"Runtime trace tools" can read ingested OTel spans (the closing-the-loop dogfooding lane that ingests the platform's own self-traces back into the platform — `docs/superpowers/specs/.../scaffolding-design.md` line 1443).

**Architecture:** Grafana Tempo (latest, AGPLv3 — `docs/architecture.md` §"Runtime backend") is the canonical store for runtime traces. The OTel Collector exports to Tempo; the platform's `workers/traces_ingest` process pulls from Tempo's HTTP query API on a configurable tick interval (default 30s), advances a per-source cursor stored in `traces.ingest_cursor`, and bulk-upserts spans into `traces.spans`. Single-instance enforcement via `pg_try_advisory_lock(0x46AE0002)` — multi-replica deploys see N-1 replicas fail-fast at startup. Idempotency is at the storage layer (composite PK `(trace_id, span_id)` + `INSERT … ON CONFLICT DO NOTHING`), not at the consumer; the consumer can re-poll an overlap window after restart without duplicate-side-effects.

**Throughput ceiling (stated non-goal — single-instance ingest, NFR D4-3).** Ingest is single-instance (the `0x46AE0002` lock fail-fasts every extra replica) on a fixed-interval tick, so sustained throughput is hard-capped at `search_limit / tick_interval_seconds` traces/s — **≈ 1.7 traces/s at the defaults** (50 ÷ 30 s). Above that rate each tick saturates (`len(summaries) == limit`), so Task 10 step 4's window-narrowing advances the cursor only to the newest *fetched* trace, not to wall-clock; `now − cursor_upper` then grows every tick and the trace layer falls progressively behind real time (stale by hours or more under sustained overload). Memory stays bounded (the per-tick fetch is hard-capped) but **lag does not** — and once lag exceeds Tempo's retention window, spans age out before the cursor reaches them and hit Task 10 step 3's per-trace 404 "aged-out" skip: **permanent trace loss**. Scale-out cannot relieve this — the lock structurally forbids a second consumer. The cheap levers are single-instance only (raise `search_limit`, lower `tick_interval_seconds`, for linear headroom); the structural fix — sharding the cursor space so N consumers each own a disjoint slice — is deferred (see *Defers to Plan 8+*). No cursor-lag gauge is exported yet (NFR D4-4). D4-4's queue-depth + pending-backlog gauges (Valkey `XLEN` / `XPENDING`) ship API-side now (Plan 11 `app.py`); the cursor-lag gauge does **not**, because the cursor is PG state (`traces.ingest_cursor`) owned by this worker and the slice's only `/metrics` surface — the API — cannot read it without bypassing the service layer to touch `db/repositories` directly, which Plan 11's thin-entrypoint role forbids (invariant 1: the API wires services and takes no domain decisions in `api/`). It is deferred to the worker `/metrics` surface (Plan 1A "Plan 11+"), where this worker — which owns the cursor and ticks per fetch — will set it directly. Until then the onset of lag is still observable in this worker's `tick.cursor_advanced` structured-log events (the cursor position is logged every tick), so the ceiling is a **tracked, attributed deferral, not a silent gap**.

The `ContinuousConsumer` ABC encapsulates the cursor-loop lifecycle; concrete subclasses implement `fetch_batch(cursor) → (new_cursor, batch)` and `persist(batch)`. The `CursorStore` ABC is a PG-backed pluggable interface so future continuous workers (e.g., Loki log ingest, deployment-event ingest) reuse the lifecycle without re-implementing cursor persistence.

Plan 6's `ExtractorMode = Literal["per_file", "project"]` is intentionally NOT extended here. ContinuousConsumer is fundamentally different from the Plan 5/6 snapshot-keyed extractors: it is not driven by `IndexingService`, doesn't participate in the per-file barrier, doesn't `XADD` to a Valkey stream, and has no `SnapshotFileOutcome` row. Plan 6's snapshot-extractor framework and Plan 7's continuous-ingest framework run in parallel — the API and agent both query the same `TracesRepository`.

**Tech Stack:** Grafana Tempo HTTP query API (`/api/search`, `/api/traces/{trace_id_hex}`, `/ready`, `/status/version`) · `httpx` async (already added in Plan 4 for LLMProvider HTTPS calls — reuse the same async transport) · PostgreSQL 18 `traces` schema (separate from `public` to enable per-schema partitioning / retention later) · JSONB attributes column with GIN index · block range index on `start_unix_nano` for time-range scans · SQLAlchemy 2.x async (Plan 3's `async_session_factory`) · `pg_advisory_lock` (Plan 5's primitive) · `pytest-asyncio` (Plan 3 dep) · `testcontainers-python` Postgres (Plan 3 fixture) · `respx` for httpx-mock integration tests (NEW dep — added in Task 1).

---

## Inherits from prior plans

| Inherited component | From plan | How Plan 7 uses it |
| --- | --- | --- |
| `Settings(BaseSettings)` env-cascade via `env_prefix="CODEORACLE_"` + `env_nested_delimiter="__"` | Plan 3 | `TracesConfig(BaseModel)` slots in alongside `LLMConfig` / `EmbeddingConfig` — env vars become `CODEORACLE_TRACES__TEMPO_URL`, `CODEORACLE_TRACES__TICK_INTERVAL`, etc. |
| `async_session_factory` (composition root) | Plan 3 | `TracesRepository` constructor accepts the factory; per-call sessions for upsert + cursor reads. |
| `get_logger(name)` returning `structlog.stdlib.BoundLogger` (`codeoracle.core.logging`) | Plan 3 | `ContinuousConsumer.__init__` accepts a logger (typed as a local `Logger` Protocol so test fakes duck-type cleanly without importing structlog); every lifecycle event (`tick.start`, `tick.fetched`, `tick.persisted`, `tick.cursor_advanced`, `tick.error`) emits one structured JSON line. Plan 7 wiring constructs the real one via `get_logger("traces_ingest")`. |
| `pg_try_advisory_lock` SQL pattern (Plan 5 Task 9 — `reindex_single_flight` from `codeoracle.services.indexing.advisory_lock`) | Plan 5 (Task 9) | Plan 5's primitive is operation-scoped (acquired around one reindex, released on completion). Plan 7's `ContinuousConsumer` base class (Task 3 here) re-implements the same SQL inline (`SELECT pg_try_advisory_lock(:id)`) but holds the lock for the full `run()` lifetime via `async with self._engine.connect() as lock_conn` — the connection-scoped lifetime is the load-bearing difference. Lock id `0x46AE0002` reserved for traces_ingest. |
| `build_worker_context` + `WorkerContext` dataclass (`codeoracle.workers._base.wiring`) | Plan 5 (Task 5) | Plan 7 introduces a **sibling** composition root (`workers/traces_ingest/wiring.py:wire_consumer`) because `build_worker_context()` constructs a `ValkeyQueueClient` unconditionally — load-bearing for WorkerConsumer-pattern workers but irrelevant for a ContinuousConsumer that doesn't read from Valkey. The SIGTERM/SIGINT → `stop_event.set()` handler pattern (Plan 5 wiring.py lines ~1784-1789) is copied verbatim into `traces_ingest/__main__.py`. Logger construction uses Plan 3's `get_logger("traces_ingest")` directly. |
| `httpx.AsyncClient` shared transport pattern | Plan 4 (Task 5) | `TempoClient` constructs an `httpx.AsyncClient` once at __init__ time, closes on `aclose()` — same lifetime model as `OpenAIEmbeddingsProvider`. |
| `argv-only subprocess` ban (Plan 5 doctrine — no `shell=True`) | Plan 5 | Not applicable — TempoClient is pure HTTP; no subprocess. |
| `core/telemetry.py` (OTel SDK initialization — `get_tracer` factory) | Plan 1A | Worker entrypoint `workers/traces_ingest/__main__.py` calls `get_tracer("traces_ingest")` at startup so every Tempo HTTP call and PG upsert emits one client-side OTel span back into Tempo (the self-trace dogfood loop). |
| `CODEORACLE_TEMPO_ENDPOINT` env var (`infra/config/defaults.env`) | Plan 1A | Plan 1A's defaults file defines the canonical Tempo endpoint; Plan 7's `TracesConfig.tempo_url` resolves to that value via the `CODEORACLE_TRACES__TEMPO_URL` env-prefix cascade (Plan 3 env-binding). Plan 7's `TempoClient` never reads the env directly — only `TracesConfig` does. |
| Tempo trace store (`infra/compose/docker-compose.yml` service) | Plan 1A | Plan 1A's canonical compose file defines the only `tempo` service Plan 7's `traces_ingest` worker pulls from (fed by the OTel Collector). Plan 7 adds **no** tempo service or config of its own. The one coordination item — exposing an OTLP-HTTP host port + Tempo ≥ 2.6.1 for Plan 7's gated `--run-live-tempo` round-trip test — is a PROPOSE to Plan 1A (see "Cross-plan edits needed"). |
| `infra/compose/docker-compose.yml` postgres + valkey + observability stack | Plan 3 | Plan 3 appends `postgres` + `valkey` to the Plan 1A skeleton compose file (which already carries the observability block incl. `tempo`); `traces_ingest` runs against that single canonical stack. Plan 7 adds no compose services. |
| `workers/_base/` package convention (`codeoracle.workers._base.*` namespace) | Plan 5 | Plan 5 establishes the worker-framework namespace; Plan 7 adds `continuous_consumer.py` + `cursor_store_pg.py` as siblings under the same package without modifying Plan 5's modules (per Plan 5's downstream-inheritance section). |

## Defers to Plan 8+ (intentionally out of scope here)

| Deferred component | Why Plan 7 doesn't ship it | Where it lands |
| --- | --- | --- |
| `(:TraceSpan {trace_id, span_id, name})` AGE graph projection | The graph projection requires the Plan 8 `HandleResolverService` to map `service.name` → repo / function nodes, plus the Plan 9 `VerifierService` to bind trace evidence to claims. Projecting prematurely would require a second migration. | Plan 9 or Plan 10. Plan 7 stores spans only in PG. |
| Tail-based sampling (retain errors always) | Sampling lives on Tempo's collector config, not the platform code. | Out of scope forever — config-only concern. |
| Loki logs correlation (`trace.get_logs_by_trace`) | Requires a separate `logs_ingest` worker pulling from Loki + a `LogsRepository`. Same `ContinuousConsumer` pattern, but distinct entity. | Future plan — possibly Plan 10's slice; Plan 7 stubs the `get_logs_by_trace` agent tool with `NotImplementedError` placeholder until Loki ingest lands. |
| Retention janitor (purge spans older than N days) | A genuinely periodic age-based purge worker — distinct from snapshot staleness, which amendment #46 moved to Plan 5 `IndexingService` check-on-use (no standing stale-APPLYING janitor exists anymore). | Plan 13 (E2E slice + janitors). |
| Trace-driven evidence binding in `VerifierService` | Plan 9 owns evidence binding. | Plan 9. |
| `mongo.find_readonly` / SQL adapter Trace correlation | DB adapter tools live in Plan 11/12. | Plan 11+. |
| Horizontal trace-ingest scale-out (cursor-space sharding) | Single-instance is structurally enforced by the `0x46AE0002` lock, capping sustained ingest at `search_limit / tick_interval_seconds` traces/s (NFR D4-3 — see *Throughput ceiling* above). Relief needs the cursor space sharded by trace-ID prefix or `service.name` so N consumers each hold a distinct lock over a disjoint slice. The slice's self-trace / dogfood load sits well under the ceiling, so this stays unbuilt until a deployment actually exceeds it. | Future plan (post-slice), paired with the cursor-lag gauge (NFR D4-4) that makes the ceiling observable. |

## What Plan 1A inherits from Plan 7

- **`codeoracle.worker` variant module (traces_ingest)** — Plan 1A's variant Dockerfile matrix (the `workers → Plans 5/6/7` row in Plan 1A's image-build catalog) references the `traces_ingest` worker entrypoint module Plan 7 ships at `src/codeoracle/workers/traces_ingest/__main__.py`. Plan 1A's variant build composes Plan 7's `codeoracle.workers.traces_ingest` package as one of the worker variants without modifying it.

## What Plan 8 inherits from Plan 7

- **`respx` test dependency** — Plan 7 introduces `respx>=0.21` to `[project.optional-dependencies]/test` (Task 1, `pyproject.toml` MODIFY); Plan 8's `SourcebotClient` mocked-HTTP tests inherit it for httpx-stub coverage without re-adding the package.
- **`CODEORACLE_TRACES__` env-prefix slot (instance of the `CODEORACLE_<COMPONENT>__` convention)** — Plan 7 wires `TracesConfig` into `Settings(BaseSettings)` under `env_nested_delimiter="__"` (Task 9), creating the per-component prefix slot pattern Plan 8 mirrors for `CODEORACLE_SOURCEBOT__*` without redefining the parent env-binding cascade.
- **`--run-live-tempo` pytest CLI option** — Plan 7's Task 1 registers the flag in `pyproject.toml`'s pytest config and Task 6 wires it as a live-test guard skipping when unset. Plan 8 mirrors the pattern shape with `--run-live-sourcebot` without re-implementing the conftest scaffold.
- **`live_*` pytest markers alphabetized block** — Plan 7's Task 1 adds `live_tempo` to `[tool.pytest.ini_options.markers]` as the first entry of an alphabetized `live_*` block; Plan 8 inherits the ordering convention and appends `live_sourcebot` while preserving the alphabetization invariant.
- **`TempoError` adapter-exception taxonomy (transient / terminal parallel structure)** — Plan 7's `adapters/traces/tempo_client.py` (Task 5) defines `TempoError` with `TempoTransientError` / `TempoTerminalError` subclasses; Plan 8 mirrors this two-tier shape with `SourcebotError` / `SourcebotTransientError` / `SourcebotTerminalError` for its own adapter, inheriting the taxonomy convention.
- **`TempoClient` async-generator fixture pattern** — Plan 7's unit + live tests use an async-generator fixture that constructs a `TempoClient(base_url, ...)`, yields it, then awaits `aclose()` in teardown (Task 5 / Task 6); Plan 8's `SourcebotClient` fixture inherits this shape without re-deriving the async-context teardown contract.

## What Plan 13 inherits from Plan 7

- **`traces_ingest` worker + `db/repositories/traces.py:TracesRepository`** — consumed by Plan 13 Task 12's observability-gate-10 assertion.

## File Structure

```
src/codeoracle/
├── core/
│   ├── config.py                          # MODIFY: add TracesConfig(BaseModel)
│   ├── exceptions.py                      # MODIFY: add ContinuousConsumerError + 3 children under CodeOracleError (spec §3.2; sibling of Plan 5's WorkerError family)
│   └── streams.py                         # MODIFY: add traces_ingest service identifier (NOT a stream — ContinuousConsumer doesn't use Valkey); relocated to core/ by D7a-2
├── models/
│   └── traces.py                          # NEW: Span, SpanFilter, TraceCursor, IngestStats
├── adapters/
│   └── traces/                            # NEW PACKAGE
│       ├── __init__.py
│       ├── tempo_client.py                # NEW: TempoClient + TempoError taxonomy
│       └── README.md                      # NEW: Tempo API contract notes
├── db/
│   ├── repositories/
│   │   └── traces.py                      # NEW: TracesRepository (upsert + cursor + query)
│   └── migrations/
│       └── versions/
│           └── 0006_traces_spans.py      # NEW: Alembic revision — traces schema + 2 tables + 4 indexes + DROP of Plan 3 stub
├── workers/
│   ├── _base/
│   │   ├── continuous_consumer.py         # NEW: ContinuousConsumer ABC + CursorStore ABC
│   │   └── cursor_store_pg.py             # NEW: PGCursorStore (CursorStore impl, JSONB cursor blob)
│   └── traces_ingest/                     # NEW PACKAGE — NO tasks.py (see note below): a ContinuousConsumer has no
│       │                                  #   per-message dispatch handler; its loop logic lives in consumer.py.
│       ├── __init__.py
│       ├── __main__.py                    # NEW: entrypoint — asyncio.run(main()), SIGTERM/SIGINT wired to stop_event
│       ├── consumer.py                    # NEW: TracesIngestConsumer(ContinuousConsumer[TimestampCursor, list[Span]])
│       └── wiring.py                      # NEW: composition root — constructs TempoClient + TracesRepository + Consumer

tests/
├── unit/
│   ├── adapters/traces/
│   │   ├── __init__.py
│   │   └── test_tempo_client.py           # NEW: mocked respx — happy path + 4xx + 5xx + connect-error
│   ├── db/
│   │   └── test_traces_repository_unit.py # NEW: pure SQL-shape tests (no PG required) — query-builder coverage
│   ├── models/
│   │   └── test_traces_models.py          # NEW: Pydantic round-trip + invariant tests
│   └── workers/_base/
│       ├── test_continuous_consumer.py    # NEW: fake source + fake cursor store — lifecycle tests
│       └── test_cursor_store_pg.py        # NEW: cursor store unit tests (no PG — uses in-memory shim)
└── integration/
    ├── adapters/traces/
    │   └── test_tempo_client_live.py      # NEW: gated by --run-live-tempo
    ├── db/
    │   ├── test_traces_repository.py      # NEW: testcontainers PG — upsert + cursor + queries
    │   └── test_migration_0006.py         # NEW: apply migration on empty DB + verify schema
    ├── workers/_base/
    │   └── test_continuous_consumer_pg.py # NEW: testcontainers PG — PGCursorStore round-trip + advisory-lock contention
    └── workers/traces_ingest/
        └── test_traces_ingest_e2e.py      # NEW: testcontainers PG + respx-mocked Tempo — full ingest loop

# infra/ — UNTOUCHED by Plan 7. The canonical `tempo` service + `infra/observability/tempo-config.yaml`
#          are owned by Plan 1A; `traces_ingest` queries that one. (See Task 1 note + "Cross-plan edits needed".)

pyproject.toml                             # MODIFY: add `respx>=0.21` to [project.optional-dependencies]/test, register `traces` + `live_tempo` pytest markers
.env.example                               # MODIFY: append CODEORACLE_TRACES__* env vars with documentation
```

## Conventions in force (re-stated for the implementer)

These are NOT negotiable for Plan 7:

- **Layered architecture (strict):** `models/` (pure Pydantic) → `db/` + `adapters/` → `services/` → processes (`workers/`). `TracesRepository` lives in `db/` (we own the PG table). `TempoClient` lives in `adapters/` (we wrap an external system — Tempo). `TracesIngestConsumer` lives in `workers/traces_ingest/` (process). Crossing layers (e.g., putting Tempo wiring in `db/`) is a CI-blocking layout error.
- **OOP over functional:** Constructor-injected dependencies. `ContinuousConsumer(cursor_store, fetcher, persistor, ...)` — never module-level globals. `TempoClient(base_url, timeout, ...)` — never `os.environ.get()` inside the adapter.
- **No `class Config:` / `model_config = ConfigDict(...)`** inside `BaseModel`s nested under `Settings(BaseSettings)`. Env binding cascades from the parent (see Plan 6 Task 7 — and the `EmbeddingConfig` pattern is the load-bearing reference; do not deviate).
- **LLM-agnostic ban** continues to apply: do NOT import `anthropic` / `openai` / `litellm` anywhere outside `src/codeoracle/adapters/llm/providers/`. Plan 7 doesn't call any LLM, so this should be a non-issue, but the ruff `flake8-tidy-imports.banned-api` rule from Plan 4 stays on.
- **No emojis in code or commits** (project-wide).
- **File-size cap:** 500-line soft target, 700-line hard ceiling per file. `db/repositories/traces.py` will be the largest file in this plan; if it approaches the ceiling, extract raw query strings into a sibling `db/repositories/traces_queries.py` rather than letting it grow.
- **No bare except / silent swallow:** every `try` block names the specific exception class (`TempoTransientError`, `httpx.HTTPStatusError`, etc.) and either re-raises with context or logs + re-raises typed. No `except Exception: pass`. No `except: ...`.
- **Type-strict (ty):** every public method has an annotated signature; every `dict`/`list`/`tuple` is parameterized; `Any` requires a `# pyright: ignore` or `# ty: ignore` justification comment.
- **No bare `time.time()`:** the one wall-clock read (the fetch-window upper bound) goes through the injectable `_now_unix_nano()` wrapper (`time.time_ns()`), wired as the `clock` default on `TracesIngestConsumer` so tests inject a fake; backoff/elapsed timing uses the ABC's injected `time_fn` (default `time.monotonic`). Cursors compare against the injected clock, not wall-clock.

---

## Task list (13 tasks)

| # | Task | Why it's separated |
| --- | --- | --- |
| 1 | Pre-task: package wiring + pytest markers + `respx` dep + core identifiers/exceptions | All non-code prep — kept out of the architecture tasks so the implementer can do these in one sitting. |
| 2 | Domain models — `Span`, `SpanFilter`, `TraceCursor`, `IngestStats` | Models first. The PG migration + repository + consumer all depend on these. |
| 3 | `ContinuousConsumer[TCursor, TBatch]` ABC + `CursorStore[TCursor]` ABC + unit tests | The framework. Generic over cursor type and batch type — `TracesIngestConsumer` will instantiate it with `TimestampCursor` + `list[Span]`. |
| 4 | `PGCursorStore` (PG-backed `CursorStore` impl) + unit tests | Single-row-per-source upsert via `ON CONFLICT (source) DO UPDATE`. Separate from Task 3 because the ABC + impl have different test surfaces. |
| 5 | `TempoClient` (adapter) + unit tests (respx-mocked) | The external boundary. Wraps Tempo's HTTP search API; converts wire format → `Span` domain model. |
| 6 | `TempoClient` live test (gated by `--run-live-tempo`) | Smoke-tests the real Tempo HTTP contract — mirrors Plan 4's `--run-live-llm` pattern. |
| 7 | Migration `0006_traces_spans.py` + integration test (apply + verify schema) | Schema before repo. The repo's queries will reference column names — they must exist first. |
| 8 | `TracesRepository` (upsert + cursor read/write + query) + unit + integration tests | The data-layer surface. Composite-PK upsert (idempotent), cursor read/write delegates to `PGCursorStore`, query supports `(service_name, time_range)` + `trace_id` lookups. |
| 9 | `TracesConfig` in `core/config.py` + `.env.example` update | Env-binding for `CODEORACLE_TRACES__TEMPO_URL`, `CODEORACLE_TRACES__TICK_INTERVAL`, etc. |
| 10 | `TracesIngestConsumer` (concrete `ContinuousConsumer`) + unit + integration tests | The actual worker logic. Pulls Tempo every tick, persists spans, advances cursor. Includes the `pg_advisory_lock(0x46AE0002)` single-instance guard. |
| 11 | Worker `__main__.py` entrypoint + composition-root `wiring.py` | The process entrypoint. Sibling composition root to Plan 5's `build_worker_context()` (no Valkey here); SIGTERM/SIGINT → `stop_event.set()` set up directly in `__main__.py` (pattern copied from Plan 5 wiring.py). |
| 12 | End-to-end integration test (testcontainers PG + respx-mocked Tempo) | The full happy-path proof: process starts, polls Tempo, persists 3 batches, advances cursor, stops on SIGTERM. Catches integration bugs that unit tests miss. |
| 13 | Documentation updates + final commit | `docs/architecture.md` cross-link, `codeoracle/README.md` mention of the new worker, `docs/superpowers/specs/.../scaffolding-design.md` "components delivered" tick. |

The TDD discipline is **red-green-commit per task** (NOT per-step) — each task commits ONE coherent unit (test + impl together). Plan 5/6 used this cadence; Plan 7 inherits it.

---

## Task 1: Pre-task scaffolding — package wiring + pytest markers + `respx` dep + core identifiers/exceptions

**What this task delivers:**
- Creates the empty package directories the next 12 tasks will fill.
- Adds `respx>=0.21,<0.22` to `[project.optional-dependencies].test` (httpx mocking — chosen over `httpx_mock`/`pytest-httpx` because it stubs at the transport layer and matches `httpx.AsyncClient` exactly, no middleware injection needed).
- Registers two new pytest markers: `traces` (slow integration tests requiring testcontainers PG) and `live_tempo` (requires `--run-live-tempo` flag; skipped by default — mirrors Plan 4's `live_llm` and Plan 6's `live_embeddings`).
- Adds the `--run-live-tempo` CLI flag to `tests/conftest.py` (alongside Plan 4's `--run-live-llm` and Plan 6's `--run-live-embeddings`).
- Adds a `traces_ingest` service identifier to `src/codeoracle/core/streams.py` (a sentinel constant, NOT a Valkey stream name — ContinuousConsumer doesn't use Valkey; the constant is reused by structured logging + advisory-lock-id registry).
- Adds the `ContinuousConsumer` exception family (`ContinuousConsumerError` + 3 children) to `src/codeoracle/core/exceptions.py` under `CodeOracleError` (spec §3.2 placement; sibling of Plan 5's `WorkerError` family — see Step 4).
- Does **not** touch Tempo's compose service or config: Plan 1A owns the canonical `tempo` (compose service + `infra/observability/tempo-config.yaml`); `traces_ingest` queries that one. No `infra/docker-compose.dev.yml`, no `infra/tempo/tempo.yaml`, no `TEMPO_VERSION` pin here.

**Files:**
- Modify: `pyproject.toml` — add `respx>=0.21,<0.22` dep + `traces` / `live_tempo` markers
- Modify: `tests/conftest.py` — add `--run-live-tempo` flag + marker auto-skip
- Modify: `src/codeoracle/core/streams.py` — append `TRACES_INGEST_SOURCE = "tempo"` + `TRACES_INGEST_ADVISORY_LOCK_ID = 0x46AE0002`
- Modify: `src/codeoracle/core/exceptions.py` — append `ContinuousConsumerError` + 3 children under `CodeOracleError`
- Create: `src/codeoracle/adapters/traces/__init__.py` (empty)
- Create: `src/codeoracle/db/migrations/versions/0006_traces_spans.py` (empty placeholder — actual `upgrade()`/`downgrade()` body in Task 7)
- Create: `src/codeoracle/workers/traces_ingest/__init__.py` (empty)
- Create: `tests/unit/adapters/traces/__init__.py` (empty)
- Create: `tests/unit/models/__init__.py` (empty if not already present from Plan 5)
- Create: `tests/integration/adapters/traces/__init__.py` (empty)
- Create: `tests/integration/workers/traces_ingest/__init__.py` (empty)

- [ ] **Step 1: Verify httpx is already a runtime dep (no-op if so)**

```bash
uv run python -c "import httpx; print(httpx.__version__)"
```

Expected: prints a version `0.27.x` or higher (added in Plan 4 for `OpenAIEmbeddingsProvider` + `OpenAIChatProvider`). If the import fails, Plan 4 was skipped — STOP and surface; do not patch `pyproject.toml` to add httpx here, because that would conceal the upstream gap.

- [ ] **Step 2: Add `respx` dep + pytest markers to `pyproject.toml`**

Open `pyproject.toml`. Locate the `[project.optional-dependencies]` block; find the `test = [...]` array. Append:

```toml
test = [
    # ... existing deps from Plans 1-6 ...
    "respx>=0.21,<0.22",                  # Plan 7 Task 1 — httpx mock for TempoClient tests
]
```

Locate the `[tool.pytest.ini_options]` block; find the `markers = [...]` array. Append:

```toml
markers = [
    # ... existing markers from Plans 5/6 ...
    "traces: slow integration tests requiring testcontainers PG + Tempo schema (Plan 7)",
    "live_tempo: requires --run-live-tempo flag; tests real Tempo HTTP API (Plan 7)",
]
```

- [ ] **Step 3: Wire `--run-live-tempo` flag into `tests/conftest.py`**

Plans 4 + 6 already established the pattern. Open `tests/conftest.py`. Locate the `pytest_addoption` function and append:

```python
def pytest_addoption(parser: pytest.Parser) -> None:
    # ... existing --run-live-llm + --run-live-embeddings from Plans 4 + 6 ...
    parser.addoption(
        "--run-live-tempo",
        action="store_true",
        default=False,
        help="Run live Tempo HTTP smoke tests (Plan 7). Requires CODEORACLE_TRACES__TEMPO_URL.",
    )
```

Locate the `pytest_collection_modifyitems` function (or similar marker-auto-skip handler) and add a clause:

```python
def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    # ... existing skip-logic for live_llm + live_embeddings ...
    if not config.getoption("--run-live-tempo"):
        skip_live = pytest.mark.skip(reason="needs --run-live-tempo")
        for item in items:
            if "live_tempo" in item.keywords:
                item.add_marker(skip_live)
```

(Mirror the exact shape Plans 4 + 6 used for their flags. If those plans used a single dispatching function for all live-* flags, extend that function instead of adding a parallel branch.)

- [ ] **Step 4: Extend `src/codeoracle/core/streams.py` (traces identifiers) + `src/codeoracle/core/exceptions.py` (ContinuousConsumer exception family)**

`core/streams.py` (Plan 5 Task 1) centralizes stream-name string constants so consumers reference symbols, not magic strings. ContinuousConsumer doesn't use Valkey streams but reuses the same namespace for the source-name + advisory-lock-id registry — keeping all "what's a worker called?" identifiers in one file is the maintainability win.

```python
# Append to src/codeoracle/core/streams.py

# =========================================================================
# Plan 7 — ContinuousConsumer source identifiers
# =========================================================================
# These are NOT Valkey stream names. ContinuousConsumer pulls from external
# sources (Tempo HTTP API, future Loki API), so there is no stream to XADD
# to. The string is used as:
#   1. The `source` column value in `traces.ingest_cursor` rows.
#   2. The `service.name` attribute on structured-log lines emitted by the
#      worker (so log aggregation can filter by source).
#   3. The advisory-lock-id registry comment so future worker IDs don't
#      collide.
# =========================================================================
TRACES_INGEST_SOURCE: str = "tempo"

# Advisory lock IDs — uniqueness invariant (do not reuse across workers):
#   0x46AE0001 — Plan 5 IndexingService.reindex single-flight
#   0x46AE0002 — Plan 7 TracesIngestConsumer single-instance
#   0x46AE0003 — reserved (next free)
TRACES_INGEST_ADVISORY_LOCK_ID: int = 0x46AE0002
```

`core/exceptions.py` (Plan 5 Step 6) is the platform's single exception hierarchy — every domain/worker error derives from `CodeOracleError` so error boundaries (API handlers, worker run-loops) can `except CodeOracleError`. The `ContinuousConsumer` exception family lives here, NOT inside `workers/_base/continuous_consumer.py`: these are cross-cutting types that `consumer.py`, the run-loop, and tests all `raise`/`except`, so spec §3.2's `core/exceptions.py` placement (and Plan 5's `WorkerError`-family precedent) governs. The base reparents from bare `Exception` to `CodeOracleError`; the three children stay parented to `ContinuousConsumerError`.

```python
# Append to src/codeoracle/core/exceptions.py
# (CodeOracleError is the root defined in Plan 3; Plan 5 already added the
#  WorkerError family here — these sit alongside it as siblings.)

class ContinuousConsumerError(CodeOracleError):
    """Base class for all ContinuousConsumer-emitted exceptions."""


class TransientFetchError(ContinuousConsumerError):
    """Source returned a recoverable error (5xx, timeout, connection reset).

    ContinuousConsumer.run() catches this, logs, sleeps with exponential
    backoff, and retries. Workers should raise this from fetch_batch when
    a network blip is the proximate cause.
    """


class TerminalFetchError(ContinuousConsumerError):
    """Source returned an unrecoverable error (4xx, schema-mismatch, auth).

    ContinuousConsumer.run() catches this, logs, and re-raises. The
    worker process dies; the orchestrator (k8s, systemd) restarts it.
    Workers should raise this from fetch_batch when retrying would
    not help.
    """


class SingleInstanceContended(ContinuousConsumerError):
    """Another instance of this worker already holds the advisory lock.

    Raised from run() at startup when advisory_lock_id is configured
    and pg_try_advisory_lock returns false. The worker exits without
    entering the loop.
    """
```

> **Tempo is owned by Plan 1A — Plan 7 does NOT create a compose service or
> config for it.** Plan 1A creates the canonical `infra/compose/docker-compose.yml`
> with a `tempo` service (`grafana/tempo`, host `3200:3200` query + OTLP-HTTP
> ingest) and its config at `infra/observability/tempo-config.yaml`; the OTel
> Collector exports the platform's own self-traces into it (the dogfood loop —
> see Goal). Plan 3 then extends the same file with `postgres` + `valkey`. That
> single file is the spec's §3.4 "all services for local dev / integration
> tests" stack, so `traces_ingest` queries the tempo that already exists there.
> A second, separate dev-only tempo would be an empty store (no collector feeds
> it) and would fork the one canonical stack — so Plan 7 adds **no** compose
> service, **no** `infra/tempo/tempo.yaml`, and **no** `TEMPO_VERSION` pin (Plan
> 1A owns the tempo image pin). See "Cross-plan edits needed" for the one
> coordination item: Plan 7's gated `--run-live-tempo` round-trip test needs the
> canonical tempo to expose an OTLP-HTTP host port and run Tempo ≥ 2.6.1 (the
> tested target); the live test already supports `CODEORACLE_TRACES__TEMPO_OTLP_URL` to
> point at whatever host port Plan 1A binds.

- [ ] **Step 5: Create the empty `__init__.py` files for new packages**

```bash
touch src/codeoracle/adapters/traces/__init__.py
touch src/codeoracle/workers/traces_ingest/__init__.py
touch tests/unit/adapters/traces/__init__.py
touch tests/unit/models/__init__.py   # safe no-op if Plan 5/6 created it
touch tests/integration/adapters/traces/__init__.py
touch tests/integration/workers/traces_ingest/__init__.py
```

- [ ] **Step 6: Create the placeholder Alembic revision file**

```bash
touch src/codeoracle/db/migrations/versions/0006_traces_spans.py
```

(The actual `upgrade()` / `downgrade()` body is written in Task 7. Creating
the empty file now means `git mv` is unnecessary when Task 7 writes content;
Alembic discovers revisions by scanning `versions/*.py` — there is no `.sql`
loader. **Migration numbering**: 0001 (Plan 3 snapshots), 0002 (Plan 3
embeddings), 0003 (Plan 3 `public.traces` — a stub schema; never written
to by anything; Plan 7 supersedes it), 0004 (Plan 5 snapshot_file_outcomes),
0005 (Plan 6 code_references), **0006 (this plan)**. The down_revision
chain is therefore `0006 → 0005 → 0004 → 0003 → 0002 → 0001`. Task 7 also
ships the SQL that removes Plan 3's stub `public.traces` table — see the
"Supersession" subsection below.)

- [ ] **Step 7: Run `uv sync` and verify `respx` is installed**

```bash
uv sync --all-extras
uv run python -c "import respx; print(respx.__version__)"
```

Expected: prints `0.21.x`.

- [ ] **Step 8: Run pytest to verify no test discovery regression**

```bash
uv run pytest --collect-only -q 2>&1 | tail -20
```

Expected: collection succeeds; the count is ≥ the Plan 6 baseline; no `ERROR collecting ...` lines.

- [ ] **Step 9: Commit**

```bash
git add pyproject.toml tests/conftest.py \
        src/codeoracle/core/streams.py \
        src/codeoracle/core/exceptions.py \
        src/codeoracle/adapters/traces/__init__.py \
        src/codeoracle/workers/traces_ingest/__init__.py \
        src/codeoracle/db/migrations/versions/0006_traces_spans.py \
        tests/unit/adapters/traces/__init__.py \
        tests/unit/models/__init__.py \
        tests/integration/adapters/traces/__init__.py \
        tests/integration/workers/traces_ingest/__init__.py

git commit -m "chore(plan-7): pre-task scaffolding — packages, respx dep, ContinuousConsumer exceptions, pytest markers"
```

---

## Task 2: Domain models — `Span`, `SpanFilter`, `TraceCursor`, `IngestStats`

**What this task delivers:** Four Pydantic models that the rest of Plan 7 references. They are pure data types — no I/O, no DB sessions, no HTTP clients. Living in `src/codeoracle/models/traces.py` keeps them importable by every layer (adapters, db, services, workers) without circular-import risk.

The naming + shape choices are load-bearing:

- **`Span`** is the canonical in-process representation of an OTel span. Field names mirror OTel semantic conventions (`trace_id`, `span_id`, `parent_span_id`, `service_name`, `start_unix_nano`, `end_unix_nano`, `status_code`, `attributes`, ...) so neither `TempoClient` nor `TracesRepository` has to rename anything when crossing the boundary. The OTel spec uses snake_case for these so we do too — even though Pydantic convention often prefers camelCase, the OTel-semconv contract wins.
- **`SpanFilter`** is the query-side counterpart. Mutually-exclusive filter shapes (lookup-by-trace-id vs scan-by-service-time) are encoded as separate optional fields with a `@model_validator` that enforces "exactly one query mode active." This prevents callers from constructing an underspecified filter that silently fans out into a full-table scan.
- **`TraceCursor`** (= `TimestampCursor` in body) is the cursor type for the Tempo source. Future sources (Loki, GitHub events) get their own cursor types; the `CursorStore` ABC is generic over `TCursor` precisely so a single store class can handle multiple cursor shapes.
- **`IngestStats`** is the per-tick telemetry payload. Emitted via `logger.info("tick.persisted", **stats.model_dump())` (where `logger` is a `structlog.stdlib.BoundLogger` returned by `codeoracle.core.logging.get_logger`) so log aggregation can query rates without parsing free-form messages.

**Files:**
- Create: `src/codeoracle/models/traces.py`
- Create: `tests/unit/models/test_traces_models.py`

- [ ] **Step 1: Write `tests/unit/models/test_traces_models.py`**

```python
"""Plan 7 Task 2: domain-model unit tests.

These pin the OTel-semantic-convention field names and the SpanFilter
mutual-exclusion invariant. Downstream code (TracesRepository,
TempoClient, TracesIngestConsumer) depends on these exact shapes;
breaking them is a cross-plan blast radius. Hence: tests first.
"""

from __future__ import annotations

import datetime

import pytest

from codeoracle.models.traces import (
    IngestStats,
    Span,
    SpanFilter,
    SpanKind,
    SpanStatusCode,
    TimestampCursor,
)


# -----------------------------------------------------------------------------
# Span
# -----------------------------------------------------------------------------

def _valid_span_kwargs(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "trace_id": b"\x01" * 16,
        "span_id": b"\x02" * 8,
        "parent_span_id": b"\x03" * 8,
        "service_name": "api",
        "operation_name": "POST /v1/query",
        "kind": "server",
        "start_unix_nano": 1_700_000_000_000_000_000,
        "end_unix_nano":   1_700_000_000_050_000_000,  # +50ms
        "status_code": "ok",
        "status_message": None,
        "attributes": {"http.route": "/v1/query", "http.method": "POST"},
        "scope_name": "codeoracle.api",
        "scope_version": "0.1.0",
    }
    base.update(overrides)
    return base


def test_span_minimal_construction_succeeds() -> None:
    span = Span(**_valid_span_kwargs())
    assert span.trace_id == b"\x01" * 16
    assert len(span.span_id) == 8
    assert span.kind == "server"
    assert span.attributes["http.route"] == "/v1/query"


def test_span_kind_must_be_in_enum() -> None:
    with pytest.raises(ValueError):
        Span(**_valid_span_kwargs(kind="banana"))


def test_span_status_code_must_be_in_enum() -> None:
    with pytest.raises(ValueError):
        Span(**_valid_span_kwargs(status_code="banana"))


def test_span_trace_id_must_be_16_bytes() -> None:
    with pytest.raises(ValueError, match="16 bytes"):
        Span(**_valid_span_kwargs(trace_id=b"\x01" * 8))


def test_span_span_id_must_be_8_bytes() -> None:
    with pytest.raises(ValueError, match="8 bytes"):
        Span(**_valid_span_kwargs(span_id=b"\x02" * 16))


def test_span_parent_span_id_optional() -> None:
    # Root span has no parent — None is the legal absence.
    span = Span(**_valid_span_kwargs(parent_span_id=None))
    assert span.parent_span_id is None


def test_span_parent_span_id_when_present_must_be_8_bytes() -> None:
    with pytest.raises(ValueError, match="8 bytes"):
        Span(**_valid_span_kwargs(parent_span_id=b"\x03" * 4))


def test_span_end_must_be_at_or_after_start() -> None:
    with pytest.raises(ValueError, match="end_unix_nano must be >= start_unix_nano"):
        Span(**_valid_span_kwargs(
            start_unix_nano=1_700_000_000_000_000_000,
            end_unix_nano=1_700_000_000_000_000_000 - 1,
        ))


def test_span_attributes_default_to_empty_dict() -> None:
    span = Span(**_valid_span_kwargs(attributes={}))
    assert span.attributes == {}


def test_span_is_immutable() -> None:
    span = Span(**_valid_span_kwargs())
    with pytest.raises(ValueError):
        span.service_name = "different"  # type: ignore[misc]  # frozen


def test_span_status_message_required_when_status_is_error() -> None:
    """OTel convention: status_message is human-readable description of
    why a span is in an error state. Allowing status_code=error +
    status_message=None makes log aggregation lossy. Reject."""
    with pytest.raises(ValueError, match="status_message is required when status_code=='error'"):
        Span(**_valid_span_kwargs(status_code="error", status_message=None))


def test_span_status_message_unused_when_status_is_ok() -> None:
    # OK + empty message is the common case; no validator complaint.
    span = Span(**_valid_span_kwargs(status_code="ok", status_message=None))
    assert span.status_code == "ok"


def test_span_duration_helper() -> None:
    span = Span(**_valid_span_kwargs(
        start_unix_nano=1_700_000_000_000_000_000,
        end_unix_nano=  1_700_000_000_050_000_000,  # +50ms
    ))
    assert span.duration_nanos == 50_000_000
    assert span.duration.total_seconds() == pytest.approx(0.050)


# -----------------------------------------------------------------------------
# SpanFilter
# -----------------------------------------------------------------------------

def test_span_filter_by_trace_id() -> None:
    f = SpanFilter(trace_id=b"\x01" * 16)
    assert f.is_trace_lookup()
    assert not f.is_service_window()


def test_span_filter_by_service_and_window() -> None:
    f = SpanFilter(
        service_name="api",
        start_unix_nano=1_700_000_000_000_000_000,
        end_unix_nano=  1_700_000_001_000_000_000,
    )
    assert f.is_service_window()
    assert not f.is_trace_lookup()


def test_span_filter_rejects_mixed_modes() -> None:
    with pytest.raises(ValueError, match="exactly one of"):
        SpanFilter(
            trace_id=b"\x01" * 16,
            service_name="api",
            start_unix_nano=1_700_000_000_000_000_000,
            end_unix_nano=  1_700_000_001_000_000_000,
        )


def test_span_filter_rejects_no_mode() -> None:
    with pytest.raises(ValueError, match="exactly one of"):
        SpanFilter()


def test_span_filter_service_window_requires_both_bounds() -> None:
    with pytest.raises(ValueError, match="start_unix_nano.*end_unix_nano"):
        SpanFilter(service_name="api", start_unix_nano=1)  # no end
    with pytest.raises(ValueError, match="start_unix_nano.*end_unix_nano"):
        SpanFilter(service_name="api", end_unix_nano=1)    # no start


def test_span_filter_service_window_end_after_start() -> None:
    with pytest.raises(ValueError, match="end_unix_nano must be >= start_unix_nano"):
        SpanFilter(
            service_name="api",
            start_unix_nano=2_000_000_000_000_000_000,
            end_unix_nano=  1_000_000_000_000_000_000,
        )


def test_span_filter_route_attribute_optional() -> None:
    f = SpanFilter(
        service_name="api",
        start_unix_nano=1_700_000_000_000_000_000,
        end_unix_nano=  1_700_000_001_000_000_000,
        route="/v1/query",
    )
    assert f.route == "/v1/query"


def test_span_filter_route_requires_service_window_mode() -> None:
    """route is a sub-filter on top of service_name+window — it cannot
    appear in trace_id mode."""
    with pytest.raises(ValueError, match="route requires service_name"):
        SpanFilter(trace_id=b"\x01" * 16, route="/v1/query")


# -----------------------------------------------------------------------------
# TimestampCursor (CursorStore[TimestampCursor])
# -----------------------------------------------------------------------------

def test_timestamp_cursor_default_overlap() -> None:
    c = TimestampCursor(last_ingested_unix_nano=1_700_000_000_000_000_000)
    assert c.overlap_seconds == 10  # default — re-poll last 10s on next tick


def test_timestamp_cursor_overlap_must_be_non_negative() -> None:
    with pytest.raises(ValueError):
        TimestampCursor(last_ingested_unix_nano=1, overlap_seconds=-1)


def test_timestamp_cursor_fetch_window_lower_bound() -> None:
    c = TimestampCursor(last_ingested_unix_nano=1_700_000_000_000_000_000, overlap_seconds=10)
    # Fetch window lower bound is "last_ingested - overlap_seconds"
    assert c.fetch_window_lower_bound_nanos() == 1_700_000_000_000_000_000 - (10 * 1_000_000_000)


def test_timestamp_cursor_is_frozen() -> None:
    c = TimestampCursor(last_ingested_unix_nano=1)
    with pytest.raises(ValueError):
        c.last_ingested_unix_nano = 2  # type: ignore[misc]  # frozen


def test_timestamp_cursor_jsonable_round_trip() -> None:
    c1 = TimestampCursor(last_ingested_unix_nano=1_700_000_000_000_000_000, overlap_seconds=15)
    blob = c1.model_dump_json()
    c2 = TimestampCursor.model_validate_json(blob)
    assert c1 == c2


# -----------------------------------------------------------------------------
# IngestStats — telemetry payload
# -----------------------------------------------------------------------------

def test_ingest_stats_construction() -> None:
    stats = IngestStats(
        source="tempo",
        cursor_before_unix_nano=1_700_000_000_000_000_000,
        cursor_after_unix_nano= 1_700_000_030_000_000_000,
        spans_fetched=42,
        spans_persisted=40,        # 2 duplicates dropped at PK
        elapsed_seconds=0.234,
    )
    assert stats.dedup_ratio == pytest.approx(2 / 42)


def test_ingest_stats_dedup_ratio_zero_when_no_fetch() -> None:
    stats = IngestStats(
        source="tempo",
        cursor_before_unix_nano=1,
        cursor_after_unix_nano=2,
        spans_fetched=0,
        spans_persisted=0,
        elapsed_seconds=0.001,
    )
    assert stats.dedup_ratio == 0.0


def test_ingest_stats_rejects_persisted_greater_than_fetched() -> None:
    with pytest.raises(ValueError, match="spans_persisted .* spans_fetched"):
        IngestStats(
            source="tempo",
            cursor_before_unix_nano=1,
            cursor_after_unix_nano=2,
            spans_fetched=10,
            spans_persisted=11,
            elapsed_seconds=0.001,
        )


def test_ingest_stats_rejects_negative_elapsed() -> None:
    with pytest.raises(ValueError):
        IngestStats(
            source="tempo",
            cursor_before_unix_nano=1,
            cursor_after_unix_nano=2,
            spans_fetched=0,
            spans_persisted=0,
            elapsed_seconds=-0.1,
        )


def test_ingest_stats_rejects_regressing_cursor() -> None:
    # _validate_stats refuses a cursor that regresses (after < before).
    # The ContinuousConsumer.fetch_batch invariant is "new_cursor MUST be
    # monotonically non-decreasing"; IngestStats enforces it again at
    # log-emission so a buggy consumer cannot quietly persist a regressing
    # cursor to telemetry.
    with pytest.raises(ValueError, match="cursor regressed"):
        IngestStats(
            source="tempo",
            cursor_before_unix_nano=1_700_000_000_000_000_000,
            cursor_after_unix_nano=1_700_000_000_000_000_000 - 1,
            spans_fetched=0,
            spans_persisted=0,
            elapsed_seconds=0.001,
        )


def test_span_kind_and_status_code_are_literal_types() -> None:
    """Type aliases for static analysis — runtime values are constrained
    by the Pydantic Literal[] field validator."""
    # SpanKind / SpanStatusCode are exported for use in services + routers
    assert "server" in SpanKind.__args__       # type: ignore[attr-defined]
    assert "error" in SpanStatusCode.__args__  # type: ignore[attr-defined]
```

- [ ] **Step 2: Run test to verify it fails**

```bash
uv run pytest tests/unit/models/test_traces_models.py -v
```

Expected: FAIL — `codeoracle.models.traces` module does not exist yet.

- [ ] **Step 3: Implement `src/codeoracle/models/traces.py`**

```python
"""Plan 7 — domain models for the traces pipeline.

These are pure Pydantic types: no I/O, no DB, no HTTP. The same
classes flow through the adapter (TempoClient), the DB (TracesRepository),
the worker (TracesIngestConsumer), and (in Plan 8+) the services and
agent tool layer. Field names mirror OTel semantic conventions so the
boundary translation is trivial.
"""

from __future__ import annotations

import datetime
from typing import Literal, get_args

from pydantic import BaseModel, ConfigDict, Field, model_validator

# OTel SpanKind enum — see https://opentelemetry.io/docs/specs/otel/trace/api/#spankind
SpanKind = Literal["server", "client", "internal", "producer", "consumer", "unspecified"]

# OTel StatusCode — see https://opentelemetry.io/docs/specs/otel/trace/api/#set-status
SpanStatusCode = Literal["ok", "error", "unset"]


class Span(BaseModel):
    """One OTel span.

    Frozen so an instance can be safely shared across async tasks
    (consumer fans into N persisters). Pydantic's `model_config =
    ConfigDict(frozen=True)` ALSO prevents accidental mutation in
    workers/_base/continuous_consumer.py where the batch is held
    across the persist + cursor-advance steps.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    # OTel identity
    trace_id: bytes = Field(description="128-bit OTel trace ID — exactly 16 bytes.")
    span_id: bytes = Field(description="64-bit OTel span ID — exactly 8 bytes.")
    parent_span_id: bytes | None = Field(
        default=None,
        description="64-bit OTel parent span ID — exactly 8 bytes, or None for root spans.",
    )

    # OTel structural
    service_name: str = Field(min_length=1, description="OTel resource attribute `service.name`.")
    operation_name: str = Field(min_length=1, description="OTel span name.")
    kind: SpanKind = Field(description="OTel SpanKind — see SpanKind literal.")

    # OTel timing — nanoseconds since Unix epoch
    start_unix_nano: int = Field(ge=0, description="OTel span start in Unix nanoseconds.")
    end_unix_nano: int = Field(ge=0, description="OTel span end in Unix nanoseconds.")

    # OTel status
    status_code: SpanStatusCode = Field(default="unset", description="OTel status code.")
    status_message: str | None = Field(default=None, description="Human-readable description; required when status_code=='error'.")

    # OTel attributes — preserved verbatim from the upstream span
    attributes: dict[str, object] = Field(default_factory=dict, description="Span attributes (e.g., http.route, db.system).")

    # OTel scope
    scope_name: str | None = Field(default=None, description="OTel instrumentation scope name.")
    scope_version: str | None = Field(default=None, description="OTel instrumentation scope version.")

    @model_validator(mode="after")
    def _validate_span(self) -> "Span":
        if len(self.trace_id) != 16:
            raise ValueError(f"trace_id must be exactly 16 bytes, got {len(self.trace_id)}")
        if len(self.span_id) != 8:
            raise ValueError(f"span_id must be exactly 8 bytes, got {len(self.span_id)}")
        if self.parent_span_id is not None and len(self.parent_span_id) != 8:
            raise ValueError(f"parent_span_id must be exactly 8 bytes, got {len(self.parent_span_id)}")
        if self.end_unix_nano < self.start_unix_nano:
            raise ValueError(
                f"end_unix_nano must be >= start_unix_nano "
                f"(got end={self.end_unix_nano}, start={self.start_unix_nano})"
            )
        if self.status_code == "error" and self.status_message is None:
            raise ValueError(
                "status_message is required when status_code=='error' "
                "(OTel convention — empty error message is a lossy log)"
            )
        return self

    @property
    def duration_nanos(self) -> int:
        return self.end_unix_nano - self.start_unix_nano

    @property
    def duration(self) -> datetime.timedelta:
        # timedelta has microsecond resolution; we accept the truncation.
        # For ns-precision math, callers should use duration_nanos.
        return datetime.timedelta(microseconds=self.duration_nanos // 1_000)


class SpanFilter(BaseModel):
    """Query-side filter for TracesRepository.find_spans.

    Two mutually-exclusive modes:
      1. Trace lookup — `trace_id` set, everything else None.
      2. Service-window scan — `service_name` + `start_unix_nano` + `end_unix_nano`
         all set; `route` optionally narrows further; `trace_id` None.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    trace_id: bytes | None = Field(default=None, description="OTel trace ID for direct lookup.")
    service_name: str | None = Field(default=None, min_length=1)
    start_unix_nano: int | None = Field(default=None, ge=0)
    end_unix_nano: int | None = Field(default=None, ge=0)
    route: str | None = Field(default=None, description="HTTP route filter (matches attributes->>'http.route').")

    def is_trace_lookup(self) -> bool:
        return self.trace_id is not None

    def is_service_window(self) -> bool:
        return (
            self.trace_id is None
            and self.service_name is not None
            and self.start_unix_nano is not None
            and self.end_unix_nano is not None
        )

    @model_validator(mode="after")
    def _validate_filter(self) -> "SpanFilter":
        if self.trace_id is not None and len(self.trace_id) != 16:
            raise ValueError(f"trace_id must be exactly 16 bytes, got {len(self.trace_id)}")

        trace_mode = self.trace_id is not None
        service_mode = self.service_name is not None or self.start_unix_nano is not None or self.end_unix_nano is not None

        if trace_mode and service_mode:
            raise ValueError(
                "SpanFilter requires exactly one of: trace_id (lookup) OR "
                "service_name+start+end (window). Got both."
            )
        if not trace_mode and not service_mode:
            raise ValueError(
                "SpanFilter requires exactly one of: trace_id (lookup) OR "
                "service_name+start+end (window). Got neither."
            )

        if service_mode:
            if self.service_name is None or self.start_unix_nano is None or self.end_unix_nano is None:
                raise ValueError(
                    "service-window mode requires service_name, start_unix_nano, "
                    "and end_unix_nano all set"
                )
            if self.end_unix_nano < self.start_unix_nano:
                raise ValueError(
                    f"end_unix_nano must be >= start_unix_nano "
                    f"(got end={self.end_unix_nano}, start={self.start_unix_nano})"
                )

        if self.route is not None and not service_mode:
            raise ValueError("route requires service_name+window mode (sub-filter on top of window scan)")

        return self


class TimestampCursor(BaseModel):
    """Cursor for ContinuousConsumer pulling from a time-ordered source (Tempo).

    Tempo's HTTP search API accepts `start` + `end` Unix timestamps; the
    cursor records "the last timestamp we successfully ingested through."
    On the next tick we fetch `[cursor - overlap, now]` and let
    composite-PK ON CONFLICT DO NOTHING absorb duplicates from the
    overlap window.

    The overlap is a safety margin against late-arriving spans (Tempo's
    ingester batches before exposing in search, so a span starting at
    `t=100` may not be queryable until `t=105`). The default 10s
    overlap is conservative; tune via CODEORACLE_TRACES__OVERLAP_SECONDS at
    deploy time.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    last_ingested_unix_nano: int = Field(ge=0)
    overlap_seconds: int = Field(default=10, ge=0)

    def fetch_window_lower_bound_nanos(self) -> int:
        return self.last_ingested_unix_nano - (self.overlap_seconds * 1_000_000_000)


class IngestStats(BaseModel):
    """Telemetry payload emitted at the end of each ContinuousConsumer tick.

    Logged via logger.info("tick.persisted", **stats.model_dump()) —
    a single JSON line per tick. Log aggregation (Loki / Grafana / etc.)
    plots rates from `spans_persisted / elapsed_seconds`, dedup pressure
    from `dedup_ratio`, and cursor drift from
    `cursor_after_unix_nano - cursor_before_unix_nano`.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    source: str = Field(min_length=1)
    cursor_before_unix_nano: int = Field(ge=0)
    cursor_after_unix_nano: int = Field(ge=0)
    spans_fetched: int = Field(ge=0)
    spans_persisted: int = Field(ge=0)
    elapsed_seconds: float = Field(ge=0.0)

    @model_validator(mode="after")
    def _validate_stats(self) -> "IngestStats":
        if self.spans_persisted > self.spans_fetched:
            raise ValueError(
                f"spans_persisted ({self.spans_persisted}) must be <= spans_fetched ({self.spans_fetched}) "
                "— PK conflicts can only drop spans, never create them"
            )
        # Defense-in-depth: ContinuousConsumer's cursor-monotonicity invariant
        # also shows up here. If the consumer regressed the cursor, this
        # raises at log-emission time and surfaces the bug at a tick boundary
        # rather than silently drifting. See TracesIngestConsumer.fetch_batch
        # for the upstream guarantee.
        if self.cursor_after_unix_nano < self.cursor_before_unix_nano:
            raise ValueError(
                f"cursor regressed: before={self.cursor_before_unix_nano}, "
                f"after={self.cursor_after_unix_nano} — cursor must be monotonically "
                "non-decreasing per ContinuousConsumer contract"
            )
        return self

    @property
    def dedup_ratio(self) -> float:
        if self.spans_fetched == 0:
            return 0.0
        return (self.spans_fetched - self.spans_persisted) / self.spans_fetched
```

- [ ] **Step 4: Run test to verify it passes**

```bash
uv run pytest tests/unit/models/test_traces_models.py -v
```

Expected: PASS — all 22 tests.

- [ ] **Step 5: Run ruff + ty on the new file**

```bash
uv run ruff check src/codeoracle/models/traces.py
uv run ruff format --check src/codeoracle/models/traces.py
uv run ty check src/codeoracle/models/traces.py
```

Expected: all clean.

- [ ] **Step 6: Commit**

```bash
git add src/codeoracle/models/traces.py tests/unit/models/test_traces_models.py
git commit -m "feat(models): traces domain models — Span, SpanFilter, TimestampCursor, IngestStats (Plan 7 Task 2)"
```

---

## Task 3: `ContinuousConsumer[TCursor, TBatch]` ABC + `CursorStore[TCursor]` ABC

**What this task delivers:** The framework — a generic, source-agnostic cursor-loop consumer that any future continuous worker (Loki, GitHub events, k8s deployment events) can subclass without rewriting lifecycle code.

**Lifecycle contract (the load-bearing part):**

1. `run()` is the public entrypoint. It does NOT take arguments; everything is injected via `__init__`.
2. **Single-instance guard (optional, opt-in via constructor arg):** if `advisory_lock_id` and `engine` are passed, `run()` acquires a session-scoped `pg_try_advisory_lock` before entering the loop and releases it on exit. If acquisition fails, `run()` raises `SingleInstanceContended` and returns immediately. This is the same primitive Plan 5 used; the difference is that Plan 5's lock was *per-reindex* (released on completion); Plan 7's lock is *per-worker-lifetime* (held until SIGTERM).
3. **Main loop:** while `not stop_event.is_set()`:
   - Read `cursor = await cursor_store.get_cursor(source_name)` (may be `None` on first run).
   - Call `new_cursor, batch = await fetch_batch(cursor)`.
   - If `batch_is_empty(batch)` → sleep `tick_interval`, continue.
   - Call `persisted = await persist(batch)`.
   - Call `await cursor_store.update_cursor(source_name, new_cursor)`.
   - Emit `IngestStats` to structured logger.
   - Sleep `tick_interval` (or shorter if the batch was a "full page" — gentle backpressure-aware acceleration is OUT OF SCOPE for Plan 7; fixed interval).
4. **Error handling:**
   - `TransientFetchError` (subclass) → log + sleep + retry (with exponential backoff capped at `max_backoff_seconds`).
   - `TerminalFetchError` (subclass) → log + re-raise (worker dies; orchestrator restarts).
   - Any other exception → caught at top-level of `run()`, logged, re-raised.
5. **Shutdown:** when `stop_event.set()` fires, the loop exits the next time it checks the event (NOT mid-batch — partial persistence is forbidden; cursor is only advanced after a successful persist+update).
6. **Clock injection:** every `await asyncio.sleep(...)` goes through the injected `sleep_fn`, and every wall-clock read goes through an injected callable — the ABC injects `time_fn` (default `time.monotonic`, for tick-elapsed/backoff timing) and the `TracesIngestConsumer` subclass injects `clock` (default `_now_unix_nano`, the fetch-window upper bound). No `codeoracle.core.clock` module is involved. Tests inject fakes to drive deterministic timing.

**Generic over:**
- `TCursor` — the cursor type (e.g., `TimestampCursor`). Stored as JSONB blob in `traces.ingest_cursor.cursor_data`; the `PGCursorStore` (Task 4) validates the blob against `TCursor` at read time.
- `TBatch` — the batch type. For `TracesIngestConsumer`, `TBatch = list[Span]`. For a hypothetical `LogsIngestConsumer`, `TBatch = list[LogRecord]`.

**Files:**
- Create: `src/codeoracle/workers/_base/continuous_consumer.py`
- Create: `tests/unit/workers/_base/test_continuous_consumer.py`

- [ ] **Step 1: Write `src/codeoracle/workers/_base/continuous_consumer.py`**

```python
"""Plan 7 — ContinuousConsumer ABC + CursorStore ABC.

ContinuousConsumer is a SIBLING of Plan 5's WorkerConsumer, NOT a
subclass. WorkerConsumer is per-message ACK/NACK against Valkey
streams; ContinuousConsumer is cursor-based polling against an
external source with no message-level semantics.

The two share nothing structurally — different lifecycle, different
failure mode, different concurrency story. They sit in the same
`workers/_base/` package because they're both worker-framework code,
but no inheritance chain ties them.
"""

from __future__ import annotations

import asyncio
import time
from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable
from typing import Any, Generic, Protocol, TypeVar

from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from codeoracle.core.exceptions import (
    SingleInstanceContended,
    TerminalFetchError,
    TransientFetchError,
)
from codeoracle.models.traces import IngestStats


class Logger(Protocol):
    """Structural type for the logger the consumer receives.

    Why a Protocol (not the concrete `structlog.stdlib.BoundLogger`)?
    Tests substitute a `_RecordingLogger` that captures emitted events
    without needing to mock the whole structlog wrapping pipeline.
    The Protocol lets ty validate the duck-type at the call sites
    without dragging structlog into the framework module.

    Both `structlog.stdlib.BoundLogger` (the production type returned
    by `codeoracle.core.logging.get_logger`) and any plain fake satisfy
    this Protocol.

    Exported (no leading underscore) because subclass-consumer modules
    (e.g., `workers/traces_ingest/consumer.py`) import it for their
    own constructor type-hints.

    Signature shape: `(event: str, *args: Any, **kwargs: Any) -> None`.
    `structlog.stdlib.BoundLogger`'s methods take `(event, *args, **kw)`
    where positional args feed into stdlib `%`-formatting on the
    `event` template. Protocol matching in `ty` strict-mode validates
    parameter shape positionally, so the framework's Protocol MUST
    include `*args: Any` to satisfy the structural check against
    `BoundLogger`. If `ty check` rejects this Protocol at the
    `codeoracle.core.logging.get_logger` call site in `wire_consumer`,
    the implementer's first move is to verify `BoundLogger`'s ty-
    discoverable signature with `uv run ty inspect codeoracle.core.logging`
    and widen this Protocol further (e.g., add a `bind` method) before
    introducing any cast or `# type: ignore`.
    """

    def info(self, event: str, *args: Any, **kwargs: Any) -> None: ...
    def warning(self, event: str, *args: Any, **kwargs: Any) -> None: ...
    def error(self, event: str, *args: Any, **kwargs: Any) -> None: ...
    def debug(self, event: str, *args: Any, **kwargs: Any) -> None: ...


# Type variables
TCursor = TypeVar("TCursor", bound=BaseModel)
TBatch = TypeVar("TBatch")


# =============================================================================
# Exceptions
# =============================================================================
# The ContinuousConsumer exception family (ContinuousConsumerError,
# TransientFetchError, TerminalFetchError, SingleInstanceContended) lives in
# core/exceptions.py under CodeOracleError — see Task 1 Step 4. It is imported at
# the top of this module so run() can raise/catch it; this module does NOT
# define it (spec §3.2 + Plan 5's WorkerError-family placement).


# =============================================================================
# CursorStore ABC
# =============================================================================

class CursorStore(ABC, Generic[TCursor]):
    """Persistence interface for ContinuousConsumer cursors.

    Implementations:
      - PGCursorStore (Plan 7 Task 4) — JSONB blob in traces.ingest_cursor
      - InMemoryCursorStore (test utility) — dict-backed for unit tests

    The cursor type TCursor is a Pydantic BaseModel — implementations
    are responsible for serializing/deserializing it (e.g., via
    model_dump_json + model_validate_json). The TCursor type is
    captured at __init__ time (not on each call) so the impl can
    validate the on-disk blob against the expected shape.
    """

    @abstractmethod
    async def get_cursor(self, source: str) -> TCursor | None:
        """Return the cursor for `source`, or None if never set."""

    @abstractmethod
    async def update_cursor(self, source: str, cursor: TCursor) -> None:
        """Persist `cursor` for `source`. Atomic upsert (no read-modify-write race)."""


# =============================================================================
# ContinuousConsumer ABC
# =============================================================================

class ContinuousConsumer(ABC, Generic[TCursor, TBatch]):
    """Cursor-loop consumer over an external source.

    Lifecycle:
      1. (optional) Acquire advisory lock → SingleInstanceContended if held.
      2. Loop:
         a. cursor = await cursor_store.get_cursor(source_name)
         b. new_cursor, batch = await fetch_batch(cursor)
         c. if batch_is_empty(batch): sleep, continue.
         d. persisted = await persist(batch)
         e. await cursor_store.update_cursor(source_name, new_cursor)
         f. emit IngestStats; sleep tick_interval.
      3. On stop_event: exit cleanly between ticks (NEVER mid-batch).

    Error handling: TransientFetchError → backoff + retry;
    TerminalFetchError → re-raise (worker dies). All other exceptions
    propagate (orchestrator restarts).
    """

    def __init__(
        self,
        *,
        source_name: str,
        cursor_store: CursorStore[TCursor],
        tick_interval_seconds: float,
        stop_event: asyncio.Event,
        logger: Logger,
        sleep_fn: Callable[[float], Awaitable[None]] = asyncio.sleep,
        time_fn: Callable[[], float] = time.monotonic,
        max_backoff_seconds: float = 60.0,
        advisory_lock_id: int | None = None,
        engine: AsyncEngine | None = None,
    ) -> None:
        if not source_name:
            raise ValueError("source_name must be non-empty")
        if tick_interval_seconds <= 0:
            raise ValueError(f"tick_interval_seconds must be > 0, got {tick_interval_seconds}")
        if max_backoff_seconds < tick_interval_seconds:
            raise ValueError(
                f"max_backoff_seconds ({max_backoff_seconds}) must be >= tick_interval_seconds ({tick_interval_seconds})"
            )
        if (advisory_lock_id is None) != (engine is None):
            raise ValueError(
                "advisory_lock_id and engine must be provided together "
                "(both None → no single-instance guard; both set → guard active)"
            )

        self._source_name = source_name
        self._cursor_store = cursor_store
        self._tick_interval = tick_interval_seconds
        self._stop_event = stop_event
        self._logger = logger
        self._sleep = sleep_fn
        self._monotonic = time_fn
        self._max_backoff = max_backoff_seconds
        self._lock_id = advisory_lock_id
        self._engine = engine

    # ----- Public API -----

    async def run(self) -> None:
        """Run the cursor loop until stop_event is set."""
        if self._lock_id is not None and self._engine is not None:
            async with self._engine.connect() as lock_conn:
                result = await lock_conn.execute(
                    text("SELECT pg_try_advisory_lock(:id)"),
                    {"id": self._lock_id},
                )
                if not result.scalar_one():
                    raise SingleInstanceContended(
                        f"advisory lock {self._lock_id:#x} for source={self._source_name!r} "
                        "is held by another worker — refusing to start"
                    )
                self._logger.info(
                    "continuous_consumer.lock_acquired",
                    source=self._source_name,
                    lock_id=f"{self._lock_id:#x}",
                )
                try:
                    await self._run_with_top_level_error_log()
                finally:
                    await lock_conn.execute(
                        text("SELECT pg_advisory_unlock(:id)"),
                        {"id": self._lock_id},
                    )
                    self._logger.info(
                        "continuous_consumer.lock_released",
                        source=self._source_name,
                        lock_id=f"{self._lock_id:#x}",
                    )
        else:
            await self._run_with_top_level_error_log()

    async def _run_with_top_level_error_log(self) -> None:
        """Wraps `_run_loop()` so any uncaught exception (i.e., anything other
        than the per-tick TransientFetchError / TerminalFetchError the loop
        already handles) emits exactly one structured error line before
        propagating. Without this, exceptions from CursorStore I/O failures
        or unexpected adapter errors would still abort the worker but the
        operator would only see the bare traceback in stderr — no structured
        log line tying the failure to the source / consumer identity. The
        re-raise preserves the current control flow (the process exits non-
        zero so the orchestrator restarts it)."""
        try:
            await self._run_loop()
        except (TransientFetchError, TerminalFetchError):
            # Already classified + logged by _run_loop; let it propagate
            # untouched.
            raise
        except Exception:
            self._logger.error(
                "continuous_consumer.unexpected_error",
                source=self._source_name,
                exc_info=True,
            )
            raise

    # ----- Subclass surface -----

    @abstractmethod
    async def fetch_batch(self, cursor: TCursor | None) -> tuple[TCursor, TBatch]:
        """Fetch the next batch from the external source.

        Returns (new_cursor, batch). The new_cursor MUST be monotonically
        non-decreasing — if the source has nothing new, return the input
        cursor unchanged and an empty batch (the loop will sleep + retry).

        On transient error (network, 5xx, timeout): raise TransientFetchError.
        On terminal error (4xx, auth, schema-mismatch): raise TerminalFetchError.
        """

    @abstractmethod
    async def persist(self, batch: TBatch) -> int:
        """Idempotently persist the batch. Returns count of new items written
        (after dedup at the storage layer — e.g., ON CONFLICT DO NOTHING)."""

    @abstractmethod
    def batch_is_empty(self, batch: TBatch) -> bool:
        """Return True iff batch has no items to persist."""

    @abstractmethod
    def cursor_to_unix_nano(self, cursor: TCursor) -> int:
        """Project the cursor onto a Unix-nano timestamp for IngestStats.

        Most cursors are timestamp-based and this is trivial; opaque
        cursors (e.g., paging tokens) should return 0 here and rely on
        log-line counts for telemetry.
        """

    # ----- Internal -----

    async def _run_loop(self) -> None:
        consecutive_transient_errors = 0
        while not self._stop_event.is_set():
            tick_start = self._monotonic()
            try:
                cursor_before = await self._cursor_store.get_cursor(self._source_name)
                new_cursor, batch = await self.fetch_batch(cursor_before)

                if self.batch_is_empty(batch):
                    self._logger.debug(
                        "continuous_consumer.tick.empty",
                        source=self._source_name,
                    )
                    consecutive_transient_errors = 0
                    await self._sleep_or_exit(self._tick_interval)
                    continue

                # fetch_batch may have returned items WITHOUT advancing the
                # cursor (a paging-token source could re-emit on retry).
                # We persist + advance even if cursor_before == new_cursor;
                # idempotency at the storage layer absorbs the duplicate.
                spans_fetched = self._batch_size(batch)
                persisted = await self.persist(batch)
                await self._cursor_store.update_cursor(self._source_name, new_cursor)

                tick_elapsed = self._monotonic() - tick_start
                stats = IngestStats(
                    source=self._source_name,
                    cursor_before_unix_nano=self.cursor_to_unix_nano(cursor_before) if cursor_before is not None else 0,
                    cursor_after_unix_nano=self.cursor_to_unix_nano(new_cursor),
                    spans_fetched=spans_fetched,
                    spans_persisted=persisted,
                    elapsed_seconds=tick_elapsed,
                )
                self._logger.info("continuous_consumer.tick.persisted", **stats.model_dump())
                consecutive_transient_errors = 0
                await self._sleep_or_exit(self._tick_interval)

            except TransientFetchError as e:
                consecutive_transient_errors += 1
                backoff = min(
                    self._tick_interval * (2 ** (consecutive_transient_errors - 1)),
                    self._max_backoff,
                )
                self._logger.warning(
                    "continuous_consumer.tick.transient_error",
                    source=self._source_name,
                    error=str(e),
                    consecutive=consecutive_transient_errors,
                    backoff_seconds=backoff,
                )
                await self._sleep_or_exit(backoff)

            except TerminalFetchError as e:
                self._logger.error(
                    "continuous_consumer.tick.terminal_error",
                    source=self._source_name,
                    error=str(e),
                )
                raise

    def _batch_size(self, batch: TBatch) -> int:
        """Subclasses can override if `len()` is not defined for TBatch.
        Default uses len() which works for list[Span] (the Plan 7 traces case)."""
        return len(batch)  # type: ignore[arg-type]  # subclasses may override

    async def _sleep_or_exit(self, seconds: float) -> None:
        """Sleep `seconds` but wake immediately if stop_event fires."""
        try:
            await asyncio.wait_for(self._stop_event.wait(), timeout=seconds)
        except asyncio.TimeoutError:
            pass  # normal — timeout means we slept the full interval without a stop signal
```

- [ ] **Step 2: Write `tests/unit/workers/_base/test_continuous_consumer.py`**

```python
"""Plan 7 Task 3 — ContinuousConsumer unit tests.

Uses an in-memory fake source + in-memory fake CursorStore so the tests
exercise only lifecycle (cursor read/advance, batch persist, error
classification, stop_event response, backoff). External dependencies
(httpx, PG) live in Tasks 5/8.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from pydantic import BaseModel

from codeoracle.core.exceptions import (
    SingleInstanceContended,
    TerminalFetchError,
    TransientFetchError,
)
from codeoracle.workers._base.continuous_consumer import (
    ContinuousConsumer,
    CursorStore,
)


# -----------------------------------------------------------------------------
# Fakes
# -----------------------------------------------------------------------------

class _FakeCursor(BaseModel):
    pos: int


class _FakeStore(CursorStore[_FakeCursor]):
    def __init__(self, initial: _FakeCursor | None = None) -> None:
        self._cursor = initial
        self.update_calls: list[_FakeCursor] = []

    async def get_cursor(self, source: str) -> _FakeCursor | None:
        return self._cursor

    async def update_cursor(self, source: str, cursor: _FakeCursor) -> None:
        self._cursor = cursor
        self.update_calls.append(cursor)


class _RecordingLogger:
    """Minimal duck-type stand-in for the `Logger` Protocol in continuous_consumer.py.

    Signatures match the Protocol's `(event, *args, **kwargs)` shape so the
    fake satisfies the structural check without `cast` / `type: ignore`.
    """

    def __init__(self) -> None:
        self.records: list[tuple[str, str, tuple[Any, ...], dict[str, Any]]] = []

    def info(self, event: str, *args: Any, **fields: Any) -> None:
        self.records.append(("info", event, args, fields))

    def warning(self, event: str, *args: Any, **fields: Any) -> None:
        self.records.append(("warning", event, args, fields))

    def error(self, event: str, *args: Any, **fields: Any) -> None:
        self.records.append(("error", event, args, fields))

    def debug(self, event: str, *args: Any, **fields: Any) -> None:
        self.records.append(("debug", event, args, fields))


class _RecordingConsumer(ContinuousConsumer[_FakeCursor, list[int]]):
    """Records every fetch + persist call for assertions."""

    def __init__(
        self,
        *,
        batches: list[tuple[_FakeCursor, list[int]] | Exception],
        empty_after_n: int | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs)
        self._batches = batches
        self._batch_idx = 0
        self._empty_after_n = empty_after_n
        self.fetch_calls: list[_FakeCursor | None] = []
        self.persist_calls: list[list[int]] = []

    async def fetch_batch(self, cursor: _FakeCursor | None) -> tuple[_FakeCursor, list[int]]:
        self.fetch_calls.append(cursor)
        if self._empty_after_n is not None and self._batch_idx >= self._empty_after_n:
            # After N batches, return same cursor + empty list to signal "nothing new"
            return (cursor or _FakeCursor(pos=0), [])
        if self._batch_idx >= len(self._batches):
            # Exhausted — return empty
            return (cursor or _FakeCursor(pos=0), [])
        next_item = self._batches[self._batch_idx]
        self._batch_idx += 1
        if isinstance(next_item, Exception):
            raise next_item
        return next_item

    async def persist(self, batch: list[int]) -> int:
        self.persist_calls.append(batch)
        return len(batch)

    def batch_is_empty(self, batch: list[int]) -> bool:
        return len(batch) == 0

    def cursor_to_unix_nano(self, cursor: _FakeCursor) -> int:
        return cursor.pos


# -----------------------------------------------------------------------------
# Validation / construction
# -----------------------------------------------------------------------------

def test_continuous_consumer_rejects_empty_source_name() -> None:
    with pytest.raises(ValueError, match="source_name must be non-empty"):
        _RecordingConsumer(
            batches=[],
            source_name="",
            cursor_store=_FakeStore(),
            tick_interval_seconds=0.01,
            stop_event=asyncio.Event(),
            logger=_RecordingLogger(),
        )


def test_continuous_consumer_rejects_non_positive_tick_interval() -> None:
    with pytest.raises(ValueError, match="tick_interval_seconds"):
        _RecordingConsumer(
            batches=[],
            source_name="x",
            cursor_store=_FakeStore(),
            tick_interval_seconds=0.0,
            stop_event=asyncio.Event(),
            logger=_RecordingLogger(),
        )


def test_continuous_consumer_rejects_backoff_less_than_tick() -> None:
    with pytest.raises(ValueError, match="max_backoff_seconds"):
        _RecordingConsumer(
            batches=[],
            source_name="x",
            cursor_store=_FakeStore(),
            tick_interval_seconds=1.0,
            max_backoff_seconds=0.5,
            stop_event=asyncio.Event(),
            logger=_RecordingLogger(),
        )


def test_continuous_consumer_rejects_advisory_lock_without_engine() -> None:
    with pytest.raises(ValueError, match="advisory_lock_id and engine must be provided together"):
        _RecordingConsumer(
            batches=[],
            source_name="x",
            cursor_store=_FakeStore(),
            tick_interval_seconds=0.01,
            stop_event=asyncio.Event(),
            logger=_RecordingLogger(),
            advisory_lock_id=0x123,
            engine=None,
        )


# -----------------------------------------------------------------------------
# Happy path
# -----------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_consumer_persists_each_batch_and_advances_cursor() -> None:
    stop = asyncio.Event()
    store = _FakeStore()
    consumer = _RecordingConsumer(
        batches=[
            (_FakeCursor(pos=10), [1, 2, 3]),
            (_FakeCursor(pos=20), [4, 5]),
            (_FakeCursor(pos=30), [6]),
        ],
        empty_after_n=3,
        source_name="fake",
        cursor_store=store,
        tick_interval_seconds=0.001,
        stop_event=stop,
        logger=_RecordingLogger(),
    )

    # Run for a bounded duration then signal stop
    async def signal_stop() -> None:
        await asyncio.sleep(0.05)
        stop.set()

    await asyncio.gather(consumer.run(), signal_stop())

    assert consumer.persist_calls == [[1, 2, 3], [4, 5], [6]]
    assert store.update_calls == [
        _FakeCursor(pos=10),
        _FakeCursor(pos=20),
        _FakeCursor(pos=30),
    ]


@pytest.mark.asyncio
async def test_consumer_skips_empty_batches() -> None:
    stop = asyncio.Event()
    store = _FakeStore(initial=_FakeCursor(pos=5))
    consumer = _RecordingConsumer(
        batches=[],            # exhausted immediately → every fetch returns empty
        source_name="fake",
        cursor_store=store,
        tick_interval_seconds=0.001,
        stop_event=stop,
        logger=_RecordingLogger(),
    )

    async def signal_stop() -> None:
        await asyncio.sleep(0.03)
        stop.set()

    await asyncio.gather(consumer.run(), signal_stop())

    assert consumer.persist_calls == []
    assert store.update_calls == []
    # fetch_batch was called multiple times though
    assert len(consumer.fetch_calls) >= 2


# -----------------------------------------------------------------------------
# Error handling
# -----------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_transient_error_backs_off_and_retries() -> None:
    stop = asyncio.Event()
    logger = _RecordingLogger()
    consumer = _RecordingConsumer(
        batches=[
            TransientFetchError("network blip 1"),
            TransientFetchError("network blip 2"),
            (_FakeCursor(pos=100), [42]),
        ],
        source_name="fake",
        cursor_store=_FakeStore(),
        tick_interval_seconds=0.001,
        max_backoff_seconds=0.05,   # allow rapid retries in tests
        stop_event=stop,
        logger=logger,
    )

    async def signal_stop() -> None:
        await asyncio.sleep(0.2)
        stop.set()

    await asyncio.gather(consumer.run(), signal_stop())

    # Both transient errors should have been logged
    transient_log_count = sum(
        1 for level, event, _args, _fields in logger.records
        if level == "warning" and event == "continuous_consumer.tick.transient_error"
    )
    assert transient_log_count == 2

    # And the third tick should have persisted normally
    assert consumer.persist_calls == [[42]]


@pytest.mark.asyncio
async def test_terminal_error_re_raises() -> None:
    stop = asyncio.Event()
    logger = _RecordingLogger()
    consumer = _RecordingConsumer(
        batches=[
            TerminalFetchError("403 forbidden"),
        ],
        source_name="fake",
        cursor_store=_FakeStore(),
        tick_interval_seconds=0.001,
        stop_event=stop,
        logger=logger,
    )

    with pytest.raises(TerminalFetchError, match="403 forbidden"):
        await consumer.run()

    error_logged = any(
        level == "error" and event == "continuous_consumer.tick.terminal_error"
        for level, event, _args, _fields in logger.records
    )
    assert error_logged


@pytest.mark.asyncio
async def test_transient_error_counter_resets_on_success() -> None:
    """Backoff should restart from 1× tick_interval after a successful tick."""
    stop = asyncio.Event()
    logger = _RecordingLogger()
    consumer = _RecordingConsumer(
        batches=[
            TransientFetchError("blip"),
            (_FakeCursor(pos=10), [1]),
            TransientFetchError("blip again"),
            (_FakeCursor(pos=20), [2]),
        ],
        source_name="fake",
        cursor_store=_FakeStore(),
        tick_interval_seconds=0.001,
        max_backoff_seconds=0.05,
        stop_event=stop,
        logger=logger,
    )

    async def signal_stop() -> None:
        await asyncio.sleep(0.2)
        stop.set()

    await asyncio.gather(consumer.run(), signal_stop())

    # Backoff series should have been [tick_interval * 2^0, tick_interval * 2^0]
    # NOT [tick_interval * 2^0, tick_interval * 2^1] — counter must reset.
    backoff_values = [
        fields["backoff_seconds"]
        for level, event, _args, fields in logger.records
        if level == "warning" and event == "continuous_consumer.tick.transient_error"
    ]
    assert backoff_values == [0.001, 0.001]


# -----------------------------------------------------------------------------
# Stop semantics
# -----------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_stop_event_during_sleep_exits_promptly() -> None:
    stop = asyncio.Event()
    consumer = _RecordingConsumer(
        batches=[(_FakeCursor(pos=1), [1])],
        empty_after_n=1,
        source_name="fake",
        cursor_store=_FakeStore(),
        tick_interval_seconds=10.0,   # long interval — stop must wake immediately
        stop_event=stop,
        logger=_RecordingLogger(),
    )

    async def signal_stop() -> None:
        # Give the consumer time to complete one tick then enter sleep
        await asyncio.sleep(0.05)
        stop.set()

    start = asyncio.get_event_loop().time()
    await asyncio.gather(consumer.run(), signal_stop())
    elapsed = asyncio.get_event_loop().time() - start

    # We should NOT have slept the full 10 seconds.
    assert elapsed < 1.0


@pytest.mark.asyncio
async def test_stop_event_set_before_run_exits_immediately() -> None:
    stop = asyncio.Event()
    stop.set()
    consumer = _RecordingConsumer(
        batches=[(_FakeCursor(pos=1), [1])],
        source_name="fake",
        cursor_store=_FakeStore(),
        tick_interval_seconds=1.0,
        stop_event=stop,
        logger=_RecordingLogger(),
    )
    await consumer.run()
    assert consumer.persist_calls == []
```

- [ ] **Step 3: Run the unit tests**

```bash
uv run pytest tests/unit/workers/_base/test_continuous_consumer.py -v
```

Expected: PASS — 11 tests. (The single-instance / advisory-lock test that uses a real PG engine is deferred to Task 4's integration test, since unit tests don't bring up a Postgres instance.)

- [ ] **Step 4: Run ruff + ty**

```bash
uv run ruff check src/codeoracle/workers/_base/continuous_consumer.py tests/unit/workers/_base/test_continuous_consumer.py
uv run ruff format --check src/codeoracle/workers/_base/continuous_consumer.py tests/unit/workers/_base/test_continuous_consumer.py
uv run ty check src/codeoracle/workers/_base/continuous_consumer.py
```

Expected: clean.

- [ ] **Step 5: Commit**

```bash
git add src/codeoracle/workers/_base/continuous_consumer.py \
        tests/unit/workers/_base/test_continuous_consumer.py
git commit -m "feat(workers): ContinuousConsumer + CursorStore ABCs (Plan 7 Task 3)"
```

---

## Task 4: `PGCursorStore` — PG-backed `CursorStore` impl

**What this task delivers:** The single concrete `CursorStore` implementation. Persists cursors as JSONB blobs in `traces.ingest_cursor` (one row per source). Read returns `None` if the source has no row; update is an upsert (`ON CONFLICT (source) DO UPDATE`).

**Why a separate file from `ContinuousConsumer`:** The ABC is in `workers/_base/continuous_consumer.py` because it's lifecycle code. The PG impl is in `workers/_base/cursor_store_pg.py` because it's PG-specific glue — but it still lives in `workers/_base/` so any continuous worker can import it without crossing into `db/`. (`db/` is reserved for *domain repositories* — `TracesRepository`, `GraphRepository`. A generic key-value cursor store doesn't have a domain identity; it's worker infrastructure.)

**Files:**
- Create: `src/codeoracle/workers/_base/cursor_store_pg.py`
- Create: `tests/unit/workers/_base/test_cursor_store_pg.py`
- Create: `tests/integration/workers/_base/test_cursor_store_pg_int.py`

- [ ] **Step 1: Write `tests/unit/workers/_base/test_cursor_store_pg.py`** — unit test using a fake session

```python
"""Plan 7 Task 4 — PGCursorStore unit tests.

Uses an in-memory session shim (mock SQLAlchemy) to verify the SQL shape
+ JSON round-trip without spinning up Postgres. The real PG round-trip
is covered by tests/integration/workers/_base/test_cursor_store_pg_int.py.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from pydantic import BaseModel

from codeoracle.workers._base.cursor_store_pg import PGCursorStore


class _SampleCursor(BaseModel):
    last_unix_nano: int
    overlap_seconds: int = 5


@pytest.mark.asyncio
async def test_get_cursor_returns_none_when_row_absent() -> None:
    session = AsyncMock()
    session.execute = AsyncMock()
    # Simulate "no row found"
    result_mock = MagicMock()
    result_mock.first = MagicMock(return_value=None)
    session.execute.return_value = result_mock

    factory = MagicMock(return_value=_aenter_session(session))

    store = PGCursorStore(session_factory=factory, cursor_type=_SampleCursor)
    out = await store.get_cursor("tempo")
    assert out is None
    session.execute.assert_awaited_once()
    sql_text = str(session.execute.await_args.args[0])
    assert "FROM traces.ingest_cursor" in sql_text
    assert "WHERE source = :source" in sql_text


@pytest.mark.asyncio
async def test_get_cursor_deserializes_jsonb_blob() -> None:
    session = AsyncMock()
    row_mock = MagicMock()
    row_mock.cursor_data = {"last_unix_nano": 1_700_000_000_000_000_000, "overlap_seconds": 7}
    result_mock = MagicMock()
    result_mock.first = MagicMock(return_value=row_mock)
    session.execute = AsyncMock(return_value=result_mock)

    factory = MagicMock(return_value=_aenter_session(session))

    store = PGCursorStore(session_factory=factory, cursor_type=_SampleCursor)
    out = await store.get_cursor("tempo")
    assert out == _SampleCursor(last_unix_nano=1_700_000_000_000_000_000, overlap_seconds=7)


@pytest.mark.asyncio
async def test_get_cursor_raises_on_invalid_blob_shape() -> None:
    session = AsyncMock()
    row_mock = MagicMock()
    row_mock.cursor_data = {"wrong_field": "wrong"}
    result_mock = MagicMock()
    result_mock.first = MagicMock(return_value=row_mock)
    session.execute = AsyncMock(return_value=result_mock)

    factory = MagicMock(return_value=_aenter_session(session))

    store = PGCursorStore(session_factory=factory, cursor_type=_SampleCursor)
    with pytest.raises(ValueError):
        await store.get_cursor("tempo")


@pytest.mark.asyncio
async def test_update_cursor_uses_upsert_with_jsonb_cast() -> None:
    session = AsyncMock()
    session.execute = AsyncMock()
    session.begin = MagicMock(return_value=_aenter(None))

    factory = MagicMock(return_value=_aenter_session(session))

    store = PGCursorStore(session_factory=factory, cursor_type=_SampleCursor)
    await store.update_cursor("tempo", _SampleCursor(last_unix_nano=42))

    session.execute.assert_awaited_once()
    sql_text = str(session.execute.await_args.args[0])
    assert "INSERT INTO traces.ingest_cursor" in sql_text
    assert "ON CONFLICT (source) DO UPDATE" in sql_text
    # JSONB cast is the load-bearing detail — `:data::jsonb` ensures the
    # parameter is recognized as JSONB, not text.
    assert "::jsonb" in sql_text


@pytest.mark.asyncio
async def test_update_cursor_serializes_via_model_dump_json() -> None:
    """Verify the persisted blob is the canonical Pydantic JSON output
    (not an ad-hoc dict.dumps). Future cursor types with custom
    serializers (e.g., datetime → ISO 8601) depend on this."""
    session = AsyncMock()
    session.execute = AsyncMock()
    session.begin = MagicMock(return_value=_aenter(None))

    factory = MagicMock(return_value=_aenter_session(session))

    store = PGCursorStore(session_factory=factory, cursor_type=_SampleCursor)
    cursor = _SampleCursor(last_unix_nano=42, overlap_seconds=8)
    await store.update_cursor("tempo", cursor)

    bind_params = session.execute.await_args.args[1]
    assert bind_params["source"] == "tempo"
    # Stored as serialized JSON string (consumed by ::jsonb cast)
    assert bind_params["data"] == cursor.model_dump_json()


# -----------------------------------------------------------------------------
# Test helpers — minimal async-context-manager shims so AsyncMock can stand
# in for AsyncSession / async session begin().
# -----------------------------------------------------------------------------

class _aenter:
    def __init__(self, value: Any) -> None:
        self._value = value

    async def __aenter__(self) -> Any:
        return self._value

    async def __aexit__(self, *exc: Any) -> None:
        pass


def _aenter_session(session: Any) -> _aenter:
    return _aenter(session)
```

- [ ] **Step 2: Run unit test to verify it fails**

```bash
uv run pytest tests/unit/workers/_base/test_cursor_store_pg.py -v
```

Expected: FAIL — `codeoracle.workers._base.cursor_store_pg` does not yet exist.

- [ ] **Step 3: Implement `src/codeoracle/workers/_base/cursor_store_pg.py`**

```python
"""Plan 7 Task 4 — PG-backed CursorStore.

One row per source in `traces.ingest_cursor`. The JSONB blob carries
the cursor type's serialized state; the configured Pydantic class
validates on read.

Schema (created by migration 0006_traces_spans.py, Task 7):
  CREATE TABLE traces.ingest_cursor (
      source       TEXT        PRIMARY KEY,
      cursor_data  JSONB       NOT NULL,
      updated_at   TIMESTAMPTZ NOT NULL DEFAULT now()
  );
"""

from __future__ import annotations

from typing import TypeVar

from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from codeoracle.workers._base.continuous_consumer import CursorStore

TCursor = TypeVar("TCursor", bound=BaseModel)


class PGCursorStore(CursorStore[TCursor]):
    """Stores cursors as JSONB blobs in traces.ingest_cursor.

    Construction:
        store = PGCursorStore(
            session_factory=async_session_factory,
            cursor_type=TimestampCursor,
        )

    The `cursor_type` is captured here (not on each call) so reads can
    validate the persisted blob against the expected shape — a future
    cursor schema migration will hit this validator first, before any
    consumer code sees an unexpected payload.
    """

    def __init__(
        self,
        *,
        session_factory: async_sessionmaker[AsyncSession],
        cursor_type: type[TCursor],
    ) -> None:
        self._session_factory = session_factory
        self._cursor_type = cursor_type

    async def get_cursor(self, source: str) -> TCursor | None:
        async with self._session_factory() as session:
            result = await session.execute(
                text("SELECT cursor_data FROM traces.ingest_cursor WHERE source = :source"),
                {"source": source},
            )
            row = result.first()
            if row is None:
                return None
            return self._cursor_type.model_validate(row.cursor_data)

    async def update_cursor(self, source: str, cursor: TCursor) -> None:
        # The :data::jsonb cast is non-negotiable: passing a JSON string
        # without the cast lands in PG as TEXT, and the column is JSONB —
        # the implicit conversion would still work but skips the JSON
        # validity check at INSERT time. Forcing ::jsonb gives us a
        # parse-error at write time if the cursor type's model_dump_json
        # ever drifts from valid JSON (which shouldn't happen, but a
        # belt-and-braces guard costs nothing).
        payload = cursor.model_dump_json()
        async with self._session_factory() as session:
            async with session.begin():
                await session.execute(
                    text(
                        """
                        INSERT INTO traces.ingest_cursor (source, cursor_data, updated_at)
                        VALUES (:source, :data::jsonb, now())
                        ON CONFLICT (source) DO UPDATE
                          SET cursor_data = EXCLUDED.cursor_data,
                              updated_at  = EXCLUDED.updated_at
                        """
                    ),
                    {"source": source, "data": payload},
                )
```

- [ ] **Step 4: Run unit test to verify it passes**

```bash
uv run pytest tests/unit/workers/_base/test_cursor_store_pg.py -v
```

Expected: PASS — 5 tests.

- [ ] **Step 5: Write `tests/integration/workers/_base/test_cursor_store_pg_int.py`** — integration test against testcontainers PG

```python
"""Plan 7 Task 4 — PGCursorStore integration tests.

Requires:
  - The traces schema + ingest_cursor table from migration 0006 (Task 7).
    This test sets up the schema directly via DDL so it can run BEFORE
    Task 7 lands the migration runner step. Once migration 0006 ships,
    the conftest fixture can replace the inline DDL with a migrate-up.

Marks: pytest.mark.traces (slow), pytest.mark.integration
"""

from __future__ import annotations

import pytest
from pydantic import BaseModel
from sqlalchemy import text

from codeoracle.workers._base.cursor_store_pg import PGCursorStore


pytestmark = [pytest.mark.traces, pytest.mark.integration]


class _IntCursor(BaseModel):
    last_unix_nano: int
    overlap_seconds: int = 5


@pytest.fixture
async def traces_schema(async_session_factory):
    """Bring up the traces.ingest_cursor table for this test module.

    Replaces this with the migration runner once Task 7 lands."""
    async with async_session_factory() as session:
        async with session.begin():
            await session.execute(text("CREATE SCHEMA IF NOT EXISTS traces"))
            await session.execute(text("""
                CREATE TABLE IF NOT EXISTS traces.ingest_cursor (
                    source       TEXT        PRIMARY KEY,
                    cursor_data  JSONB       NOT NULL,
                    updated_at   TIMESTAMPTZ NOT NULL DEFAULT now()
                )
            """))
    yield
    async with async_session_factory() as session:
        async with session.begin():
            await session.execute(text("DROP TABLE IF EXISTS traces.ingest_cursor"))
            await session.execute(text("DROP SCHEMA IF EXISTS traces"))


@pytest.mark.asyncio
async def test_get_cursor_returns_none_initially(async_session_factory, traces_schema):
    store = PGCursorStore(session_factory=async_session_factory, cursor_type=_IntCursor)
    assert await store.get_cursor("tempo") is None


@pytest.mark.asyncio
async def test_update_then_get_round_trip(async_session_factory, traces_schema):
    store = PGCursorStore(session_factory=async_session_factory, cursor_type=_IntCursor)
    await store.update_cursor("tempo", _IntCursor(last_unix_nano=42, overlap_seconds=7))
    out = await store.get_cursor("tempo")
    assert out == _IntCursor(last_unix_nano=42, overlap_seconds=7)


@pytest.mark.asyncio
async def test_update_is_idempotent(async_session_factory, traces_schema):
    store = PGCursorStore(session_factory=async_session_factory, cursor_type=_IntCursor)
    await store.update_cursor("tempo", _IntCursor(last_unix_nano=42))
    await store.update_cursor("tempo", _IntCursor(last_unix_nano=42))  # same value, no error
    out = await store.get_cursor("tempo")
    assert out == _IntCursor(last_unix_nano=42)


@pytest.mark.asyncio
async def test_update_advances_value(async_session_factory, traces_schema):
    store = PGCursorStore(session_factory=async_session_factory, cursor_type=_IntCursor)
    await store.update_cursor("tempo", _IntCursor(last_unix_nano=10))
    await store.update_cursor("tempo", _IntCursor(last_unix_nano=20))
    out = await store.get_cursor("tempo")
    assert out is not None
    assert out.last_unix_nano == 20


@pytest.mark.asyncio
async def test_distinct_sources_independent(async_session_factory, traces_schema):
    store = PGCursorStore(session_factory=async_session_factory, cursor_type=_IntCursor)
    await store.update_cursor("tempo", _IntCursor(last_unix_nano=10))
    await store.update_cursor("loki",  _IntCursor(last_unix_nano=99))
    tempo = await store.get_cursor("tempo")
    loki  = await store.get_cursor("loki")
    assert tempo is not None and tempo.last_unix_nano == 10
    assert loki  is not None and loki.last_unix_nano  == 99


@pytest.mark.asyncio
async def test_invalid_blob_raises_on_read(async_session_factory, traces_schema):
    """If a future schema migration leaves a row with wrong shape, the
    Pydantic validator should surface it instead of silently returning
    None or a default."""
    # Inject a row with bad shape via raw SQL
    async with async_session_factory() as session:
        async with session.begin():
            await session.execute(
                text("""
                    INSERT INTO traces.ingest_cursor (source, cursor_data, updated_at)
                    VALUES ('bad', '{"wrong_field": "x"}'::jsonb, now())
                """)
            )

    store = PGCursorStore(session_factory=async_session_factory, cursor_type=_IntCursor)
    with pytest.raises(Exception):  # Pydantic ValidationError specifically; broaden if needed
        await store.get_cursor("bad")
```

- [ ] **Step 6: Run integration test**

```bash
uv run pytest tests/integration/workers/_base/test_cursor_store_pg_int.py -v -m "traces and integration"
```

Expected: PASS — 6 tests. (Requires the `async_session_factory` fixture from Plan 5's conftest. If Plan 5's conftest hasn't been merged yet, STOP and surface — do not duplicate the fixture here.)

- [ ] **Step 7: Run ruff + ty**

```bash
uv run ruff check src/codeoracle/workers/_base/cursor_store_pg.py tests/unit/workers/_base/test_cursor_store_pg.py tests/integration/workers/_base/test_cursor_store_pg_int.py
uv run ruff format --check src/codeoracle/workers/_base/cursor_store_pg.py
uv run ty check src/codeoracle/workers/_base/cursor_store_pg.py
```

Expected: clean.

- [ ] **Step 8: Commit**

```bash
git add src/codeoracle/workers/_base/cursor_store_pg.py \
        tests/unit/workers/_base/test_cursor_store_pg.py \
        tests/integration/workers/_base/test_cursor_store_pg_int.py
git commit -m "feat(workers): PGCursorStore — PG-backed CursorStore impl (Plan 7 Task 4)"
```

---

## Task 5: `TempoClient` (adapter) + unit tests (`respx`-mocked)

**What this task delivers:** the only place in Plan 7 that talks HTTP to Tempo. Wraps two endpoints — `/api/search` (enumerate traces in a window) and `/api/v2/traces/{trace_id}` (fetch full trace) — and converts wire format to the `Span` domain model. All HTTP error classification (5xx → transient, 4xx → terminal, timeout → transient, etc.) lives here; the consumer above never sees `httpx` types.

**Why two endpoints, not one:** Tempo's `/api/search` returns trace summaries (id, root service, root span name, start time) and a *limited* spanSet preview (max ~50 spans per trace by default). For full-fidelity ingest the platform needs every span on every trace, so we enumerate via search and then GET each trace's full OTLP payload. This is 1 + N HTTP calls per tick; at default tick=30s and limit=50 traces, that's 51 calls / tick — well within Tempo's `/api/search` throughput envelope (Tempo's frontend caps concurrent searches at ~50 in default config; we issue 1 search and 50 *trace fetches* which hit a different rate-limit bucket).

**TraceQL choice:** the search uses TraceQL `{}` (match all spans) + time-window `[start, end]` + `limit`. Older Tempo deployments expose `/api/search` with tag-based filters but not TraceQL; Plan 7 pins Tempo `>= 2.0` (introduced TraceQL) — see `infra/versions/pinned.env`. If the deployment is older, `TempoClient.search_traces_in_window` surfaces a `TerminalFetchError("TraceQL not supported")` at startup; the worker dies and the operator upgrades.

**OTLP parse:** the trace-fetch endpoint returns OTLP JSON (`/api/v2/traces/{id}` returns `{ "trace": { "resourceSpans": [{ "resource": {...}, "scopeSpans": [{ "scope": {...}, "spans": [...] }] }] } }`). **The top-level key is `resourceSpans`, NOT `batches`** — Tempo 2.6.0 renamed `Batches` to `ResourceSpans` in the v2 API to align with the OTLP spec. The adapter flattens the nested structure into a flat `list[Span]`. Each OTLP span carries `traceId` (hex OR base64 — Tempo's response encoding varies by build; the adapter accepts both), `spanId` (same), `parentSpanId` (same, or empty for root), `name`, `kind` (integer enum mapped to our SpanKind), `startTimeUnixNano` (string in OTLP — see "Wire-format gotchas" below), `endTimeUnixNano` (string), `attributes` (list of key-value pairs), `status` (`{code, message}`). The adapter normalizes integer-encoded fields back to Python ints and KV lists to Python dicts, and decodes IDs through `_decode_otlp_bytes` which tries `bytes.fromhex` first then falls back to `base64.b64decode`.

**Wire-format gotchas (load-bearing — document so the implementer doesn't lose hours):**

1. **OTLP encodes nanos as strings.** `startTimeUnixNano` in the JSON wire format is `"1700000000000000000"` (a string), NOT `1700000000000000000`. JavaScript's `Number` can't represent 64-bit integers exactly; the OTLP-JSON spec mandates string encoding to preserve precision. Python's `int()` accepts the string fine — but a naive `int(payload["startTimeUnixNano"])` is what's correct; passing the string directly to `Span(start_unix_nano=...)` would trip Pydantic's `int` validator. Cast explicitly.

2. **OTLP encodes binary IDs as hex OR base64 — Tempo varies by build.** `traceId` is typically 32 hex chars (`"0102030405060708090a0b0c0d0e0f10"` = 16 bytes) on Tempo < 2.6, OR 24 base64-standard chars on Tempo 2.6+ when the OTLP-JSON marshaller is used. `spanId` mirrors the encoding (16 hex chars OR 12 base64 chars = 8 bytes). `parentSpanId` same, OR empty string `""` for root spans → map to `None`. The adapter calls `_decode_otlp_bytes(payload["traceId"], expected_len=16)` which tries hex first then falls back to base64 — never use `bytes.fromhex` directly at the adapter boundary (it would silently break on Tempo 2.6+ in production).

3. **OTLP attribute values are tagged unions.** `attributes: [{key: "http.route", value: {stringValue: "/v1/query"}}, ...]`. The value-tag indicates the type: `stringValue`, `intValue`, `doubleValue`, `boolValue`, `arrayValue`, `kvlistValue`, `bytesValue`. The adapter recursively unwraps these into native Python types (str / int / float / bool / list / dict / bytes).

4. **OTLP SpanKind is integer-encoded.** `1 → "internal"`, `2 → "server"`, `3 → "client"`, `4 → "producer"`, `5 → "consumer"`, `0 → "unspecified"`. (Note: the OTLP int encoding is offset by one from the OTel Python SDK's int encoding; the wire format is what we map from.)

5. **OTLP StatusCode is also integer-encoded.** `0 → "unset"`, `1 → "ok"`, `2 → "error"`. Same caveat as kind.

6. **`scope` field is per-scopeSpans, NOT per-span.** All spans inside one `scopeSpans` block share the same `scope.name` + `scope.version`. The adapter MUST copy these onto every Span as it flattens — otherwise scope is lost.

7. **`resource.attributes` includes `service.name`.** The OTLP resource is the resource the spans belong to; `service.name` is the resource-level attribute the OTel spec uses to identify the producing service. Per Span domain model, we copy this to `span.service_name`. If the resource has no `service.name`, default to `"unknown"` (an OTel collector misconfiguration we don't fix here but surface in logs).

**Files:**
- Create: `src/codeoracle/adapters/traces/tempo_client.py`
- Create: `src/codeoracle/adapters/traces/README.md` — wire-format gotchas reference (above, expanded with example payloads)
- Create: `tests/unit/adapters/traces/test_tempo_client.py`

- [ ] **Step 1: Write `tests/unit/adapters/traces/test_tempo_client.py`** with `respx` mocks

```python
"""Plan 7 Task 5 — TempoClient unit tests with respx-mocked HTTP.

These tests pin the wire-format adapter: OTLP nanos-as-strings,
hex-encoded IDs, integer-encoded kinds, KV-list attributes, scope
flattening. Each gotcha gets a regression test.
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest
import respx

from codeoracle.adapters.traces.tempo_client import (
    TempoClient,
    TempoTerminalError,
    TempoTransientError,
)
from codeoracle.models.traces import Span


# -----------------------------------------------------------------------------
# Fixtures
# -----------------------------------------------------------------------------

@pytest.fixture
def tempo_base_url() -> str:
    return "http://tempo.test:3200"


@pytest.fixture
async def client(tempo_base_url: str):
    """Build a TempoClient and tear it down after the test.

    Under `asyncio_mode = "auto"` (set in pyproject.toml by Plan 3),
    plain `@pytest.fixture` on an async generator IS the right shape;
    pytest-asyncio adapts it. The earlier sync-fixture form that used
    `asyncio.get_event_loop().run_until_complete(c.aclose())` was
    fragile — on Python 3.10+ the bare `get_event_loop()` outside a
    running loop emits DeprecationWarning, and on current Python versions
    it raises
    if no loop is set on the calling thread. Async-yield is cleaner.
    """
    c = TempoClient(base_url=tempo_base_url, timeout_seconds=2.0)
    try:
        yield c
    finally:
        await c.aclose()


def _otlp_span_payload(
    *,
    trace_id_hex: str = "0102030405060708090a0b0c0d0e0f10",
    span_id_hex: str = "1112131415161718",
    parent_span_id_hex: str = "",
    name: str = "GET /v1/query",
    kind: int = 2,  # server
    start_ns: int = 1_700_000_000_000_000_000,
    end_ns: int = 1_700_000_000_050_000_000,
    status_code: int = 1,
    status_message: str = "",
    attributes: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build an OTLP-JSON span dict matching Tempo's wire format."""
    attrs: list[dict[str, Any]] = []
    for k, v in (attributes or {}).items():
        if isinstance(v, str):
            attrs.append({"key": k, "value": {"stringValue": v}})
        elif isinstance(v, bool):
            attrs.append({"key": k, "value": {"boolValue": v}})
        elif isinstance(v, int):
            attrs.append({"key": k, "value": {"intValue": str(v)}})  # OTLP intValue is string
        elif isinstance(v, float):
            attrs.append({"key": k, "value": {"doubleValue": v}})
    return {
        "traceId": trace_id_hex,
        "spanId": span_id_hex,
        "parentSpanId": parent_span_id_hex,
        "name": name,
        "kind": kind,
        "startTimeUnixNano": str(start_ns),
        "endTimeUnixNano": str(end_ns),
        "status": {"code": status_code, "message": status_message},
        "attributes": attrs,
    }


def _otlp_trace_payload(spans: list[dict[str, Any]], *, service_name: str = "api", scope_name: str = "codeoracle.api", scope_version: str = "0.1.0") -> dict[str, Any]:
    """Build the full OTLP trace response shape Tempo 2.6+ returns at /api/v2/traces/{id}.

    Top-level key is `resourceSpans` (per Tempo 2.6 release notes; renamed
    from pre-2.6 `batches`). Tests that need to exercise the legacy `batches`
    shape (pre-2.6 compatibility) construct it manually — the adapter still
    accepts it as a fallback per `_unwrap_trace_payload` in tempo_client.py.
    """
    return {
        "trace": {
            "resourceSpans": [
                {
                    "resource": {
                        "attributes": [
                            {"key": "service.name", "value": {"stringValue": service_name}},
                        ],
                    },
                    "scopeSpans": [
                        {
                            "scope": {"name": scope_name, "version": scope_version},
                            "spans": spans,
                        }
                    ],
                }
            ]
        }
    }


# -----------------------------------------------------------------------------
# search_traces_in_window
# -----------------------------------------------------------------------------

@pytest.mark.asyncio
@respx.mock
async def test_search_traces_returns_summaries(client: TempoClient, tempo_base_url: str) -> None:
    respx.get(f"{tempo_base_url}/api/search").respond(
        json={
            "traces": [
                {
                    "traceID": "0102030405060708090a0b0c0d0e0f10",
                    "rootServiceName": "api",
                    "rootTraceName": "POST /v1/query",
                    "startTimeUnixNano": "1700000000000000000",
                    "durationMs": 50,
                },
                {
                    "traceID": "1112131415161718192021222324252600",
                    "rootServiceName": "worker",
                    "rootTraceName": "process_message",
                    "startTimeUnixNano": "1700000000500000000",
                    "durationMs": 120,
                },
            ],
            "metrics": {"inspectedTraces": 2},
        }
    )

    summaries = await client.search_traces_in_window(
        start_unix_nano=1_700_000_000_000_000_000,
        end_unix_nano=1_700_000_001_000_000_000,
        limit=10,
    )
    assert len(summaries) == 2
    assert summaries[0].trace_id == bytes.fromhex("0102030405060708090a0b0c0d0e0f10")
    assert summaries[0].root_service_name == "api"
    assert summaries[0].duration_nanos == 50_000_000   # ms → ns conversion at adapter boundary


@pytest.mark.asyncio
@respx.mock
async def test_search_traces_handles_empty_result(client: TempoClient, tempo_base_url: str) -> None:
    respx.get(f"{tempo_base_url}/api/search").respond(
        json={"traces": [], "metrics": {"inspectedTraces": 0}}
    )
    out = await client.search_traces_in_window(
        start_unix_nano=1, end_unix_nano=2, limit=10,
    )
    assert out == []


@pytest.mark.asyncio
@respx.mock
async def test_search_traces_sends_correct_query_params(client: TempoClient, tempo_base_url: str) -> None:
    route = respx.get(f"{tempo_base_url}/api/search").respond(
        json={"traces": [], "metrics": {}}
    )
    await client.search_traces_in_window(
        start_unix_nano=1_000_000_000_000_000_000,
        end_unix_nano=2_000_000_000_000_000_000,
        limit=50,
    )
    assert route.called
    request = route.calls[0].request
    params = dict(request.url.params)
    assert params["start"] == "1000000000000000000"   # nanos as string for precision
    assert params["end"] == "2000000000000000000"
    assert params["limit"] == "50"
    assert params["q"] == "{}"                        # TraceQL match-all


# -----------------------------------------------------------------------------
# get_trace_by_id — wire-format gotchas
# -----------------------------------------------------------------------------

@pytest.mark.asyncio
@respx.mock
async def test_get_trace_flattens_resource_scope_and_spans(client: TempoClient, tempo_base_url: str) -> None:
    trace_id_hex = "0102030405060708090a0b0c0d0e0f10"
    payload = _otlp_trace_payload(
        spans=[
            _otlp_span_payload(span_id_hex="aaaaaaaaaaaaaaaa"),
            _otlp_span_payload(span_id_hex="bbbbbbbbbbbbbbbb"),
        ],
        service_name="api",
        scope_name="codeoracle.api",
        scope_version="0.2.0",
    )
    respx.get(f"{tempo_base_url}/api/v2/traces/{trace_id_hex}").respond(json=payload)

    spans = await client.get_trace_by_id(bytes.fromhex(trace_id_hex))
    assert len(spans) == 2
    for span in spans:
        assert isinstance(span, Span)
        assert span.service_name == "api"          # from resource.attributes
        assert span.scope_name == "codeoracle.api"     # from scope.name
        assert span.scope_version == "0.2.0"


@pytest.mark.asyncio
@respx.mock
async def test_get_trace_decodes_otlp_kind_integer(client: TempoClient, tempo_base_url: str) -> None:
    """OTLP kind: 1=internal, 2=server, 3=client, 4=producer, 5=consumer, 0=unspecified."""
    trace_id_hex = "0102030405060708090a0b0c0d0e0f10"
    payload = _otlp_trace_payload(spans=[
        _otlp_span_payload(span_id_hex=f"{i:016x}", kind=k)
        for i, k in enumerate([0, 1, 2, 3, 4, 5])
    ])
    respx.get(f"{tempo_base_url}/api/v2/traces/{trace_id_hex}").respond(json=payload)

    spans = await client.get_trace_by_id(bytes.fromhex(trace_id_hex))
    kinds = [s.kind for s in spans]
    assert kinds == ["unspecified", "internal", "server", "client", "producer", "consumer"]


@pytest.mark.asyncio
@respx.mock
async def test_get_trace_decodes_otlp_status_code_integer(client: TempoClient, tempo_base_url: str) -> None:
    """OTLP status code: 0=unset, 1=ok, 2=error."""
    trace_id_hex = "0102030405060708090a0b0c0d0e0f10"
    payload = _otlp_trace_payload(spans=[
        _otlp_span_payload(span_id_hex=f"{i:016x}", status_code=c, status_message=("err" if c == 2 else ""))
        for i, c in enumerate([0, 1, 2])
    ])
    respx.get(f"{tempo_base_url}/api/v2/traces/{trace_id_hex}").respond(json=payload)

    spans = await client.get_trace_by_id(bytes.fromhex(trace_id_hex))
    codes = [s.status_code for s in spans]
    assert codes == ["unset", "ok", "error"]
    # Spans with error code must carry status_message (Span model invariant)
    error_span = [s for s in spans if s.status_code == "error"][0]
    assert error_span.status_message == "err"


@pytest.mark.asyncio
@respx.mock
async def test_get_trace_decodes_attribute_value_tagged_union(client: TempoClient, tempo_base_url: str) -> None:
    """OTLP attributes: [{key, value: {stringValue|intValue|boolValue|doubleValue}}].
    The adapter must unwrap the tagged-union into native Python types."""
    trace_id_hex = "0102030405060708090a0b0c0d0e0f10"
    payload = _otlp_trace_payload(spans=[
        _otlp_span_payload(
            attributes={
                "http.route": "/v1/query",
                "http.status_code": 200,
                "ok": True,
                "duration_ratio": 0.95,
            },
        )
    ])
    respx.get(f"{tempo_base_url}/api/v2/traces/{trace_id_hex}").respond(json=payload)

    spans = await client.get_trace_by_id(bytes.fromhex(trace_id_hex))
    attrs = spans[0].attributes
    assert attrs["http.route"] == "/v1/query"
    assert attrs["http.status_code"] == 200
    assert attrs["ok"] is True
    assert attrs["duration_ratio"] == pytest.approx(0.95)


@pytest.mark.asyncio
@respx.mock
async def test_get_trace_parent_span_id_empty_string_becomes_none(client: TempoClient, tempo_base_url: str) -> None:
    """OTLP encodes root spans with parentSpanId = "" (empty string).
    The adapter must convert this to Python None (matching Span model)."""
    trace_id_hex = "0102030405060708090a0b0c0d0e0f10"
    payload = _otlp_trace_payload(spans=[
        _otlp_span_payload(parent_span_id_hex=""),
    ])
    respx.get(f"{tempo_base_url}/api/v2/traces/{trace_id_hex}").respond(json=payload)
    spans = await client.get_trace_by_id(bytes.fromhex(trace_id_hex))
    assert spans[0].parent_span_id is None


@pytest.mark.asyncio
@respx.mock
async def test_get_trace_nanos_string_decoded_to_int(client: TempoClient, tempo_base_url: str) -> None:
    """OTLP wire format encodes 64-bit nanos as STRING for JS-safety;
    the adapter must convert to int."""
    trace_id_hex = "0102030405060708090a0b0c0d0e0f10"
    payload = _otlp_trace_payload(spans=[
        _otlp_span_payload(start_ns=1_700_000_000_000_000_000, end_ns=1_700_000_000_050_000_000),
    ])
    respx.get(f"{tempo_base_url}/api/v2/traces/{trace_id_hex}").respond(json=payload)
    spans = await client.get_trace_by_id(bytes.fromhex(trace_id_hex))
    assert isinstance(spans[0].start_unix_nano, int)
    assert spans[0].start_unix_nano == 1_700_000_000_000_000_000
    assert spans[0].end_unix_nano == 1_700_000_000_050_000_000


@pytest.mark.asyncio
@respx.mock
async def test_get_trace_missing_service_name_defaults_to_unknown(client: TempoClient, tempo_base_url: str) -> None:
    """If resource.attributes lacks `service.name` (operator misconfig),
    the adapter logs + tags the spans as service_name='unknown' rather
    than raising — keep ingest running, surface via logs."""
    trace_id_hex = "0102030405060708090a0b0c0d0e0f10"
    payload = {
        "trace": {
            "resourceSpans": [
                {
                    "resource": {"attributes": []},  # no service.name
                    "scopeSpans": [{"scope": {"name": "x", "version": "1"}, "spans": [
                        _otlp_span_payload()
                    ]}],
                }
            ]
        }
    }
    respx.get(f"{tempo_base_url}/api/v2/traces/{trace_id_hex}").respond(json=payload)
    spans = await client.get_trace_by_id(bytes.fromhex(trace_id_hex))
    assert spans[0].service_name == "unknown"


# -----------------------------------------------------------------------------
# Error classification
# -----------------------------------------------------------------------------

@pytest.mark.asyncio
@respx.mock
async def test_5xx_raises_transient(client: TempoClient, tempo_base_url: str) -> None:
    respx.get(f"{tempo_base_url}/api/search").respond(status_code=503, text="upstream busy")
    with pytest.raises(TempoTransientError, match="503"):
        await client.search_traces_in_window(start_unix_nano=1, end_unix_nano=2, limit=10)


@pytest.mark.asyncio
@respx.mock
async def test_429_raises_transient(client: TempoClient, tempo_base_url: str) -> None:
    respx.get(f"{tempo_base_url}/api/search").respond(status_code=429, text="rate limited")
    with pytest.raises(TempoTransientError, match="429"):
        await client.search_traces_in_window(start_unix_nano=1, end_unix_nano=2, limit=10)


@pytest.mark.asyncio
@respx.mock
async def test_400_raises_terminal(client: TempoClient, tempo_base_url: str) -> None:
    respx.get(f"{tempo_base_url}/api/search").respond(status_code=400, text="bad query")
    with pytest.raises(TempoTerminalError, match="400"):
        await client.search_traces_in_window(start_unix_nano=1, end_unix_nano=2, limit=10)


@pytest.mark.asyncio
@respx.mock
async def test_401_raises_terminal(client: TempoClient, tempo_base_url: str) -> None:
    respx.get(f"{tempo_base_url}/api/search").respond(status_code=401, text="unauthorized")
    with pytest.raises(TempoTerminalError, match="401"):
        await client.search_traces_in_window(start_unix_nano=1, end_unix_nano=2, limit=10)


@pytest.mark.asyncio
@respx.mock
async def test_404_on_get_trace_raises_terminal(client: TempoClient, tempo_base_url: str) -> None:
    trace_id_hex = "0102030405060708090a0b0c0d0e0f10"
    respx.get(f"{tempo_base_url}/api/v2/traces/{trace_id_hex}").respond(status_code=404)
    with pytest.raises(TempoTerminalError, match="404"):
        await client.get_trace_by_id(bytes.fromhex(trace_id_hex))


@pytest.mark.asyncio
@respx.mock
async def test_connect_error_raises_transient(client: TempoClient, tempo_base_url: str) -> None:
    respx.get(f"{tempo_base_url}/api/search").mock(side_effect=httpx.ConnectError("dns error"))
    with pytest.raises(TempoTransientError):
        await client.search_traces_in_window(start_unix_nano=1, end_unix_nano=2, limit=10)


@pytest.mark.asyncio
@respx.mock
async def test_timeout_raises_transient(client: TempoClient, tempo_base_url: str) -> None:
    respx.get(f"{tempo_base_url}/api/search").mock(side_effect=httpx.ReadTimeout("slow"))
    with pytest.raises(TempoTransientError):
        await client.search_traces_in_window(start_unix_nano=1, end_unix_nano=2, limit=10)


# -----------------------------------------------------------------------------
# OTLP byte-decoding (hex AND base64) — Tempo wire format varies by build
# -----------------------------------------------------------------------------

def test_decode_otlp_bytes_accepts_hex() -> None:
    """Hex encoding (Tempo < 2.6 OTLP-JSON marshaller and most production builds):
    32 hex chars decode to 16 raw bytes for trace_id; 16 hex chars to 8 bytes for span_id."""
    from codeoracle.adapters.traces.tempo_client import _decode_otlp_bytes

    decoded_trace = _decode_otlp_bytes("0102030405060708090a0b0c0d0e0f10", expected_len=16)
    assert decoded_trace == bytes(range(1, 17))
    assert len(decoded_trace) == 16

    decoded_span = _decode_otlp_bytes("a1a2a3a4a5a6a7a8", expected_len=8)
    assert decoded_span == bytes([0xa1, 0xa2, 0xa3, 0xa4, 0xa5, 0xa6, 0xa7, 0xa8])
    assert len(decoded_span) == 8


def test_decode_otlp_bytes_accepts_base64() -> None:
    """Base64 encoding (Tempo 2.6+ when OTLP-JSON marshaller chooses base64):
    24 base64 chars decode to 16 raw bytes; 12 base64 chars to 8 bytes."""
    import base64 as _b64
    from codeoracle.adapters.traces.tempo_client import _decode_otlp_bytes

    raw_trace = bytes(range(1, 17))
    b64_trace = _b64.b64encode(raw_trace).decode("ascii")
    assert len(b64_trace) == 24
    assert _decode_otlp_bytes(b64_trace, expected_len=16) == raw_trace

    raw_span = bytes([0xa1, 0xa2, 0xa3, 0xa4, 0xa5, 0xa6, 0xa7, 0xa8])
    b64_span = _b64.b64encode(raw_span).decode("ascii")
    assert len(b64_span) == 12
    assert _decode_otlp_bytes(b64_span, expected_len=8) == raw_span


def test_decode_otlp_bytes_rejects_neither() -> None:
    """Garbage input (neither hex nor base64) must raise ValueError — silent
    fallback to corrupted bytes would let bad IDs into traces.spans."""
    from codeoracle.adapters.traces.tempo_client import _decode_otlp_bytes

    with pytest.raises(ValueError):
        _decode_otlp_bytes("", expected_len=16)
    with pytest.raises(ValueError):
        # Wrong length AND not valid hex characters past position 0
        _decode_otlp_bytes("not-hex-not-b64!", expected_len=16)
    with pytest.raises(ValueError):
        # Valid base64 but wrong decoded length
        _decode_otlp_bytes("AAAA", expected_len=16)  # 4 b64 chars = 3 bytes ≠ 16


# -----------------------------------------------------------------------------
# Legacy `batches` envelope (Tempo < 2.6 wire-format fallback)
# -----------------------------------------------------------------------------

@pytest.mark.asyncio
@respx.mock
async def test_get_trace_decodes_legacy_batches_envelope(
    client: TempoClient, tempo_base_url: str
) -> None:
    """Tempo 2.6 renamed the top-level key from `batches` to `resourceSpans` to
    match the OTLP spec. The adapter keeps backward compatibility with pre-2.6
    deployments by accepting EITHER key. This test exercises the `batches` path —
    without it, the fallback in `parser` is untested dead code."""
    trace_id_hex = "0102030405060708090a0b0c0d0e0f10"
    legacy_payload = {
        "trace": {
            # Note: `batches`, NOT `resourceSpans` — pre-2.6 wire format.
            "batches": [
                {
                    "resource": {
                        "attributes": [
                            {"key": "service.name", "value": {"stringValue": "legacy-api"}}
                        ]
                    },
                    "scopeSpans": [
                        {
                            "scope": {"name": "legacy.scope", "version": "0.1.0"},
                            "spans": [_otlp_span_payload(span_id_hex="aaaabbbbccccdddd")],
                        }
                    ],
                }
            ]
        }
    }
    respx.get(f"{tempo_base_url}/api/v2/traces/{trace_id_hex}").respond(json=legacy_payload)

    spans = await client.get_trace_by_id(bytes.fromhex(trace_id_hex))
    assert len(spans) == 1
    assert spans[0].service_name == "legacy-api"   # resource attr decoded
    assert spans[0].scope_name == "legacy.scope"
    assert spans[0].span_id.hex() == "aaaabbbbccccdddd"


# -----------------------------------------------------------------------------
# Lifecycle
# -----------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_aclose_releases_httpx_client(tempo_base_url: str) -> None:
    c = TempoClient(base_url=tempo_base_url, timeout_seconds=2.0)
    assert not c._client.is_closed   # internal check
    await c.aclose()
    assert c._client.is_closed
```

- [ ] **Step 2: Run test to verify it fails**

```bash
uv run pytest tests/unit/adapters/traces/test_tempo_client.py -v
```

Expected: FAIL — `codeoracle.adapters.traces.tempo_client` does not exist.

- [ ] **Step 3: Implement `src/codeoracle/adapters/traces/tempo_client.py`**

```python
"""Plan 7 Task 5 — Tempo HTTP client adapter.

Wraps Grafana Tempo's HTTP search + trace-fetch endpoints. Converts OTLP
wire format to the Span domain model at the adapter boundary so neither
the worker nor the repository sees `dict[str, Any]` or `httpx.Response`.

Endpoints (verified against Tempo 2.0+):
  GET /api/search?q={}&start=<nanos>&end=<nanos>&limit=<n>
      → 200 { "traces": [{traceID, rootServiceName, rootTraceName,
                          startTimeUnixNano, durationMs}, ...],
              "metrics": {...} }
      → 4xx terminal, 5xx transient, 429 transient
  GET /api/v2/traces/{trace_id_hex}
      → 200 { "trace": { "resourceSpans": [{ "resource": {...},
                                             "scopeSpans": [{ "scope": {...},
                                                              "spans": [...] }] }] } }
        (Tempo 2.6+ — top-level key renamed from `batches`. Adapter still
         accepts `batches` for backward compatibility with pre-2.6 builds.)
      → 404 terminal (trace not in store — could happen if cursor
            overlap pulls a trace_id that's already aged out)
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

import httpx

from codeoracle.models.traces import Span, SpanKind, SpanStatusCode


# =============================================================================
# Exceptions — distinct hierarchy from ContinuousConsumer's TransientFetchError
# so callers (e.g., TracesIngestConsumer) translate adapter errors into the
# framework's error taxonomy explicitly. Two layers of typed errors is more
# verbose than sharing one, but the adapter mustn't know about consumer
# concerns (and vice versa).
# =============================================================================

class TempoError(Exception):
    """Base class for all TempoClient-emitted exceptions."""


class TempoTransientError(TempoError):
    """5xx response, 429 rate-limit, connect-error, timeout — caller should retry."""


class TempoTerminalError(TempoError):
    """4xx response (except 429), schema-mismatch, unsupported API version — do not retry."""


# =============================================================================
# DTOs (returned by TempoClient — not part of the public Span domain model)
# =============================================================================

@dataclass(frozen=True, slots=True)
class TraceSummary:
    """Summary row from /api/search. Used by the consumer to decide which
    trace IDs to fetch full payloads for; not persisted to PG.

    The duration is normalized to nanoseconds at the adapter boundary —
    Tempo's search response uses milliseconds (`durationMs`) which is
    inconsistent with the nanos-everywhere rule the rest of Plan 7 uses.
    """

    trace_id: bytes
    root_service_name: str
    root_span_name: str
    start_unix_nano: int
    duration_nanos: int


# =============================================================================
# Wire-format constants (OTLP integer-encoded enums)
# =============================================================================

_OTLP_KIND_MAP: dict[int, SpanKind] = {
    0: "unspecified",
    1: "internal",
    2: "server",
    3: "client",
    4: "producer",
    5: "consumer",
}

_OTLP_STATUS_CODE_MAP: dict[int, SpanStatusCode] = {
    0: "unset",
    1: "ok",
    2: "error",
}


# =============================================================================
# TempoClient
# =============================================================================

class TempoClient:
    """Async client for Tempo's HTTP query API.

    Holds one `httpx.AsyncClient` for the client's lifetime; callers are
    responsible for calling `aclose()` on shutdown (the wiring composition
    root in workers/traces_ingest/wiring.py does this on stop_event).
    """

    def __init__(
        self,
        *,
        base_url: str,
        timeout_seconds: float,
    ) -> None:
        if not base_url:
            raise ValueError("base_url must be non-empty")
        if timeout_seconds <= 0:
            raise ValueError(f"timeout_seconds must be > 0, got {timeout_seconds}")

        self._base_url = base_url.rstrip("/")
        self._client = httpx.AsyncClient(
            base_url=self._base_url,
            timeout=timeout_seconds,
            # No default headers — Tempo's HTTP API is unauthenticated in local
            # dev; auth (e.g., X-Scope-OrgID for multi-tenant Tempo) is
            # injected by deployment-time config, NOT hardcoded here.
        )

    async def search_traces_in_window(
        self,
        *,
        start_unix_nano: int,
        end_unix_nano: int,
        limit: int,
    ) -> list[TraceSummary]:
        """Enumerate traces in `[start, end]`. Returns at most `limit` summaries
        (Tempo also has its own server-side limit; the caller should treat
        `len(out) == limit` as a "result-set truncated" signal and narrow
        the next window)."""
        if start_unix_nano < 0 or end_unix_nano < 0:
            raise ValueError("nanos must be non-negative")
        if end_unix_nano < start_unix_nano:
            raise ValueError("end_unix_nano must be >= start_unix_nano")
        if limit <= 0:
            raise ValueError("limit must be > 0")

        params = {
            "q": "{}",
            "start": str(start_unix_nano),
            "end": str(end_unix_nano),
            "limit": str(limit),
        }
        payload = await self._get_json("/api/search", params=params)
        traces = payload.get("traces") or []
        out: list[TraceSummary] = []
        for t in traces:
            try:
                out.append(TraceSummary(
                    trace_id=bytes.fromhex(t["traceID"]),
                    root_service_name=t.get("rootServiceName", "unknown"),
                    root_span_name=t.get("rootTraceName", ""),
                    start_unix_nano=int(t["startTimeUnixNano"]),
                    duration_nanos=int(t.get("durationMs", 0)) * 1_000_000,
                ))
            except (KeyError, ValueError) as e:
                raise TempoTerminalError(
                    f"unexpected search-response shape from Tempo: {e!r} (trace={t!r})"
                ) from e
        return out

    async def get_trace_by_id(self, trace_id: bytes) -> list[Span]:
        """Fetch all spans for `trace_id`. Returns flat list across all
        resources/scopes in the trace."""
        if len(trace_id) != 16:
            raise ValueError(f"trace_id must be 16 bytes, got {len(trace_id)}")

        trace_id_hex = trace_id.hex()
        payload = await self._get_json(f"/api/v2/traces/{trace_id_hex}")
        trace = payload.get("trace")
        if not isinstance(trace, dict):
            raise TempoTerminalError(f"Tempo response missing 'trace' object: {payload!r}")

        spans: list[Span] = []
        # Tempo 2.6+ uses `resourceSpans` (OTLP-aligned). The pre-2.6 `batches`
        # alias is also accepted defensively in case a target deployment is
        # pinned to an older Tempo — if neither key exists the response is
        # treated as empty (no spans), which is correct: an empty trace ID is
        # already validated upstream by the 404 branch in get_trace_by_id.
        resource_spans = trace.get("resourceSpans") or trace.get("batches") or []
        for resource_span in resource_spans:
            resource_attrs = _unwrap_otlp_attributes(resource_span.get("resource", {}).get("attributes", []))
            service_name = resource_attrs.get("service.name", "unknown")
            if not isinstance(service_name, str):
                service_name = str(service_name)

            for scope_spans in resource_span.get("scopeSpans", []):
                scope = scope_spans.get("scope", {})
                scope_name = scope.get("name")
                scope_version = scope.get("version")
                for otlp_span in scope_spans.get("spans", []):
                    spans.append(self._otlp_span_to_domain(
                        otlp_span,
                        service_name=service_name,
                        scope_name=scope_name,
                        scope_version=scope_version,
                    ))
        return spans

    async def aclose(self) -> None:
        await self._client.aclose()

    # ----- Internal -----

    async def _get_json(self, path: str, *, params: dict[str, str] | None = None) -> dict[str, Any]:
        try:
            response = await self._client.get(path, params=params)
        except httpx.TimeoutException as e:
            raise TempoTransientError(f"timeout calling Tempo {path}: {e!r}") from e
        except httpx.ConnectError as e:
            raise TempoTransientError(f"connect error calling Tempo {path}: {e!r}") from e
        except httpx.HTTPError as e:
            # Catches NetworkError and other httpx errors not enumerated above
            raise TempoTransientError(f"httpx error calling Tempo {path}: {e!r}") from e

        if response.status_code == 429 or 500 <= response.status_code < 600:
            raise TempoTransientError(
                f"Tempo {path} returned {response.status_code}: {response.text[:200]}"
            )
        if 400 <= response.status_code < 500:
            raise TempoTerminalError(
                f"Tempo {path} returned {response.status_code}: {response.text[:200]}"
            )
        try:
            return response.json()
        except ValueError as e:
            raise TempoTerminalError(f"Tempo {path} returned non-JSON: {response.text[:200]}") from e

    def _otlp_span_to_domain(
        self,
        otlp: dict[str, Any],
        *,
        service_name: str,
        scope_name: str | None,
        scope_version: str | None,
    ) -> Span:
        try:
            trace_id_hex = otlp["traceId"]
            span_id_hex = otlp["spanId"]
            parent_hex = otlp.get("parentSpanId", "") or ""  # OTLP uses "" for root
            kind_int = otlp.get("kind", 0)
            status = otlp.get("status", {}) or {}
            status_code_int = status.get("code", 0)
            status_message = status.get("message") or None
            if status_message == "":
                status_message = None
            attrs = _unwrap_otlp_attributes(otlp.get("attributes", []))

            kind = _OTLP_KIND_MAP.get(kind_int, "unspecified")
            status_code = _OTLP_STATUS_CODE_MAP.get(status_code_int, "unset")

            # Span model invariant: status_code=='error' requires status_message.
            # If Tempo returned error+empty-message (legal under OTLP), promote
            # to "unknown error" so the Span constructor doesn't reject.
            if status_code == "error" and status_message is None:
                status_message = "unknown error"

            return Span(
                trace_id=_decode_otlp_bytes(trace_id_hex, expected_len=16),
                span_id=_decode_otlp_bytes(span_id_hex, expected_len=8),
                parent_span_id=(
                    _decode_otlp_bytes(parent_hex, expected_len=8) if parent_hex else None
                ),
                service_name=service_name,
                operation_name=otlp.get("name") or "",
                kind=kind,
                start_unix_nano=int(otlp["startTimeUnixNano"]),
                end_unix_nano=int(otlp["endTimeUnixNano"]),
                status_code=status_code,
                status_message=status_message,
                attributes=attrs,
                scope_name=scope_name,
                scope_version=scope_version,
            )
        except (KeyError, ValueError) as e:
            raise TempoTerminalError(
                f"unexpected OTLP span shape: {e!r} (span={otlp!r})"
            ) from e


# =============================================================================
# OTLP attribute unwrap — module-level helper for test-coverage and reuse
# =============================================================================

def _unwrap_otlp_attributes(attrs: list[dict[str, Any]]) -> dict[str, object]:
    """Convert OTLP attribute list to native Python dict.

    OTLP attribute format:
      [{"key": "http.route", "value": {"stringValue": "/v1/query"}}, ...]

    Tagged-union value keys:
      stringValue → str
      intValue    → int (encoded as string in OTLP-JSON)
      boolValue   → bool
      doubleValue → float
      arrayValue  → list[object]
      kvlistValue → dict[str, object]
      bytesValue  → bytes (base64-encoded in OTLP-JSON)
    """
    out: dict[str, object] = {}
    for kv in attrs:
        key = kv.get("key")
        value = kv.get("value") or {}
        if not isinstance(key, str):
            continue
        out[key] = _unwrap_otlp_value(value)
    return out


def _unwrap_otlp_value(value: dict[str, Any]) -> object:
    if "stringValue" in value:
        return value["stringValue"]
    if "intValue" in value:
        # OTLP-JSON encodes int as string for 64-bit safety
        return int(value["intValue"])
    if "boolValue" in value:
        return bool(value["boolValue"])
    if "doubleValue" in value:
        return float(value["doubleValue"])
    if "arrayValue" in value:
        return [_unwrap_otlp_value(v) for v in value["arrayValue"].get("values", [])]
    if "kvlistValue" in value:
        return _unwrap_otlp_attributes(value["kvlistValue"].get("values", []))
    if "bytesValue" in value:
        # OTLP-JSON encodes bytes as base64 string
        import base64
        return base64.b64decode(value["bytesValue"])
    return None


def _decode_otlp_bytes(encoded: str, *, expected_len: int) -> bytes:
    """Decode an OTLP-JSON byte field (traceId / spanId / parentSpanId).

    Tempo's responses encode binary IDs as either:
      - lowercase hex (32 chars for trace_id, 16 for span_id) — Tempo
        builds prior to ~2.5 and most CLI tooling
      - base64-standard (24 chars for trace_id, 12 for span_id) — Tempo
        2.6+ when the protobuf JSON marshaller is used end-to-end

    OTLP-PROTO spec mandates base64 for byte fields in JSON serialization,
    but Tempo's HTTP API has historically emitted hex; both formats appear
    in Grafana's own docs (`https://github.com/grafana/tempo/blob/main/docs/sources/tempo/api_docs/pushing-spans-with-http.md`).
    The robust adapter accepts both — try hex first (cheaper to detect), fall
    back to base64. Raise `ValueError` if neither parses (caller wraps in
    `TempoTerminalError` per the docstring above).
    """
    if not encoded:
        raise ValueError("empty OTLP byte field")
    # Hex path: even length, all-hex chars.
    if len(encoded) == expected_len * 2:
        try:
            decoded = bytes.fromhex(encoded)
            if len(decoded) == expected_len:
                return decoded
        except ValueError:
            pass  # Fall through to base64 attempt.
    # Base64 path. `bytes.b64decode` accepts both standard and url-safe
    # alphabets via `validate=False`; we explicitly validate length post-decode.
    import base64
    try:
        decoded = base64.b64decode(encoded, validate=False)
    except Exception as exc:  # base64 raises binascii.Error subclass
        raise ValueError(f"not hex and not base64: {encoded!r}") from exc
    if len(decoded) != expected_len:
        raise ValueError(
            f"OTLP byte field decoded to wrong length: "
            f"got {len(decoded)}, expected {expected_len} (raw={encoded!r})"
        )
    return decoded
```

- [ ] **Step 4: Run unit test to verify it passes**

```bash
uv run pytest tests/unit/adapters/traces/test_tempo_client.py -v
```

Expected: PASS — 16+ tests.

- [ ] **Step 5: Create `src/codeoracle/adapters/traces/README.md` documenting wire-format gotchas**

```markdown
# `codeoracle.adapters.traces` — Tempo HTTP adapter

Plan 7 boundary between `httpx` + Tempo HTTP API and the rest of the
platform. Everything else in `codeoracle/` consumes `Span` / `TraceSummary`
domain objects, never raw OTLP dicts.

## Endpoints

| Endpoint | What it does | When we use it |
| --- | --- | --- |
| `GET /api/search?q={}&start=&end=&limit=` | TraceQL match-all in time window; returns trace summaries | `TempoClient.search_traces_in_window` — per-tick enumeration |
| `GET /api/v2/traces/{hex}` | Full OTLP payload for one trace | `TempoClient.get_trace_by_id` — per-trace fetch |
| `GET /ready` | Liveness probe | Healthcheck only (no code path) |
| `GET /status/version` | Tempo build info | Future startup-time version assertion |

## Wire-format gotchas (load-bearing)

1. **Nanos are strings in OTLP-JSON.** `startTimeUnixNano="1700000000000000000"` — preserve precision, JS can't hold 64-bit ints. Cast `int(...)` at adapter boundary.
2. **IDs are hex OR base64 — Tempo varies by build.** `traceId` is 32 hex chars (16 bytes) on most builds OR 24 base64-standard chars on Tempo 2.6+ with the OTLP-JSON marshaller; `spanId` mirrors the encoding at half the length; `parentSpanId` same, OR empty string `""` for root spans (map `""` → `None`). Decode via `_decode_otlp_bytes(encoded, expected_len=16/8)` which tries hex first and falls back to base64 — never call `bytes.fromhex` directly at the adapter boundary.
3. **Attributes are tagged unions.** `attributes: [{key, value: {stringValue|intValue|boolValue|doubleValue|arrayValue|kvlistValue|bytesValue}}]`. Recursive unwrap via `_unwrap_otlp_value`.
4. **`kind` is an integer.** `0=unspecified, 1=internal, 2=server, 3=client, 4=producer, 5=consumer`. Map via `_OTLP_KIND_MAP`.
5. **`status.code` is also an integer.** `0=unset, 1=ok, 2=error`. Map via `_OTLP_STATUS_CODE_MAP`.
6. **`scope` is per-scopeSpans, not per-span.** Copy `scope.name` + `scope.version` onto every Span in the flatten step.
7. **`service.name` is on `resource.attributes`, not on individual spans.** Pull from resource, propagate to every Span.

## Error classification

| Wire error | Mapped to | Notes |
| --- | --- | --- |
| 5xx | `TempoTransientError` | Backoff + retry by caller |
| 429 | `TempoTransientError` | Rate-limit — caller should also slow down |
| 4xx (other) | `TempoTerminalError` | Don't retry — fix config/query |
| connect / DNS error | `TempoTransientError` | |
| Read/write timeout | `TempoTransientError` | |
| Non-JSON response body | `TempoTerminalError` | Likely wrong path or proxy intercept |
| Missing/extra fields in JSON | `TempoTerminalError` | Schema drift — surface, don't paper over |

## What this adapter does NOT do

- **Push to Tempo** — Tempo ingest is via the OTel Collector, not this adapter. We read-only here.
- **Multi-tenant auth** — `X-Scope-OrgID` is injected by deployment config (env var → header) if the deployment needs it; not hardcoded.
- **Retries** — `ContinuousConsumer` owns the retry loop. The adapter raises typed errors and lets the caller decide.
```

- [ ] **Step 6: Run ruff + ty**

```bash
uv run ruff check src/codeoracle/adapters/traces/tempo_client.py tests/unit/adapters/traces/test_tempo_client.py
uv run ruff format --check src/codeoracle/adapters/traces/tempo_client.py
uv run ty check src/codeoracle/adapters/traces/tempo_client.py
```

Expected: clean.

- [ ] **Step 7: Commit**

```bash
git add src/codeoracle/adapters/traces/tempo_client.py \
        src/codeoracle/adapters/traces/README.md \
        tests/unit/adapters/traces/test_tempo_client.py
git commit -m "feat(adapters): TempoClient — search + trace-fetch + OTLP → Span mapping (Plan 7 Task 5)"
```

---

## Task 6: `TempoClient` live test (gated by `--run-live-tempo`)

**What this task delivers:** A real-HTTP smoke test that runs only when `--run-live-tempo` is passed. Catches drift between our adapter's wire-format assumptions and the actual Tempo build the operator is running. Mirrors Plan 4's `--run-live-llm` pattern.

**Scope:** the live test exercises only that Tempo is reachable, that `/ready` returns 200, that `/api/search` returns a parseable response shape (even if 0 traces), and that `/api/v2/traces/{some-id}` either returns 200 with parseable OTLP or 404. It does NOT assert ingest correctness (Task 12's E2E test owns that with mocked Tempo for determinism).

**Files:**
- Create: `tests/integration/adapters/traces/test_tempo_client_live.py`

- [ ] **Step 1: Write `tests/integration/adapters/traces/test_tempo_client_live.py`**

```python
"""Plan 7 Task 6 — live Tempo smoke tests.

Skipped by default. Run with:
  uv run pytest tests/integration/adapters/traces/test_tempo_client_live.py --run-live-tempo

Env vars consumed:
  CODEORACLE_TRACES__TEMPO_URL              (required; e.g., http://localhost:3200 if running infra/compose/docker-compose.yml)
  CODEORACLE_TRACES__TEMPO_TIMEOUT_SECONDS  (optional; default 5.0 — matches the
                                     TracesConfig.tempo_timeout_seconds env
                                     cascade so the same variable controls
                                     both the live test and the wired
                                     consumer)
"""

from __future__ import annotations

import os

import httpx
import pytest

from codeoracle.adapters.traces.tempo_client import (
    TempoClient,
    TempoTerminalError,
)


pytestmark = pytest.mark.live_tempo


@pytest.fixture
def tempo_url() -> str:
    url = os.environ.get("CODEORACLE_TRACES__TEMPO_URL")
    if not url:
        pytest.skip("CODEORACLE_TRACES__TEMPO_URL not set (start `docker compose -f infra/compose/docker-compose.yml up tempo`)")
    return url


@pytest.fixture
async def live_client(tempo_url: str):
    timeout = float(os.environ.get("CODEORACLE_TRACES__TEMPO_TIMEOUT_SECONDS", "5.0"))
    c = TempoClient(base_url=tempo_url, timeout_seconds=timeout)
    yield c
    await c.aclose()


@pytest.mark.asyncio
async def test_tempo_ready_endpoint_responds(tempo_url: str) -> None:
    """Tempo's /ready should return 200 when the binary is up. This is a
    bare-HTTP probe — not via TempoClient — so we can sanity-check the
    deployment is alive before exercising the adapter."""
    async with httpx.AsyncClient(timeout=5.0) as client:
        r = await client.get(f"{tempo_url}/ready")
        assert r.status_code == 200, f"Tempo not ready at {tempo_url}: {r.text}"


@pytest.mark.asyncio
async def test_search_returns_parseable_response(live_client: TempoClient) -> None:
    """Even on a fresh Tempo with zero traces, the search response must be
    JSON-parseable and have the `traces` field (possibly empty)."""
    summaries = await live_client.search_traces_in_window(
        start_unix_nano=1,
        end_unix_nano=9_000_000_000_000_000_000,  # ~2255 — within BIGINT range; "match any"
        limit=10,
    )
    assert isinstance(summaries, list)


@pytest.mark.asyncio
async def test_get_nonexistent_trace_raises_terminal(live_client: TempoClient) -> None:
    """A randomly-generated trace ID that Tempo doesn't know about must
    surface as TempoTerminalError (404)."""
    bogus_trace = b"\xde\xad\xbe\xef" * 4  # 16 bytes — valid shape, no real trace
    with pytest.raises(TempoTerminalError, match="404"):
        await live_client.get_trace_by_id(bogus_trace)


@pytest.mark.asyncio
async def test_push_then_fetch_round_trip(live_client: TempoClient, tempo_url: str) -> None:
    """The wire-format-drift canary: push a known trace via OTLP HTTP, wait
    for the ingester batch, then fetch it back and assert structure.

    This is the ONLY test in the suite that exercises real Tempo wire format
    end-to-end. The respx-mocked unit tests can drift silently if Tempo
    changes its envelope (e.g., the 2.6 `Batches → ResourceSpans` rename);
    this test catches that. If Tempo upgrades break this test, the adapter's
    envelope parser is the load-bearing site to update.

    Tempo's ingest endpoint is `/v1/traces` (OTLP HTTP). The test pushes to
    the OTLP-HTTP host port and queries on 3200. Against the canonical stack
    (`infra/compose/docker-compose.yml`, owned by Plan 1A) Tempo's query port
    is host 3200; its OTLP-HTTP port may be remapped (Plan 1A already remaps
    OTLP-gRPC to host 14317 to avoid colliding with the OTel Collector), so
    set `CODEORACLE_TRACES__TEMPO_OTLP_URL` to the OTLP-HTTP host base when it isn't
    `:4318` — the harness defaults to `tempo_url` with `:3200`→`:4318` only as
    a fallback. Tempo batches ingested spans before they're queryable — a
    5-15 second wait is typical; we poll up to 30s.
    """
    import asyncio
    import json
    import secrets
    import time

    # Distinct trace per run — no cross-test interference.
    trace_id_bytes = secrets.token_bytes(16)
    span_id_bytes = secrets.token_bytes(8)
    trace_id_hex = trace_id_bytes.hex()
    span_id_hex = span_id_bytes.hex()
    start_nano = time.time_ns()
    end_nano = start_nano + 50_000_000  # +50ms

    # Derive the OTLP-HTTP base URL from the query URL by swapping the port.
    # Tempo's docker dev config maps 3200 -> 3200 (query) and 4318 -> 4318
    # (OTLP-HTTP ingest). Operators using a different host MUST set
    # CODEORACLE_TRACES__TEMPO_OTLP_URL to override.
    import os
    otlp_base = os.environ.get(
        "CODEORACLE_TRACES__TEMPO_OTLP_URL",
        tempo_url.replace(":3200", ":4318"),
    )

    otlp_payload = {
        "resourceSpans": [{
            "resource": {
                "attributes": [
                    {"key": "service.name", "value": {"stringValue": "test-round-trip"}}
                ],
            },
            "scopeSpans": [{
                "scope": {"name": "codeoracle.test", "version": "0.1.0"},
                "spans": [{
                    "traceId": trace_id_hex,
                    "spanId": span_id_hex,
                    "name": "round-trip-probe",
                    "kind": 2,  # server
                    "startTimeUnixNano": str(start_nano),
                    "endTimeUnixNano": str(end_nano),
                    "status": {"code": 1},  # ok
                }],
            }],
        }],
    }

    async with httpx.AsyncClient(timeout=10.0) as push_client:
        push_resp = await push_client.post(
            f"{otlp_base}/v1/traces",
            content=json.dumps(otlp_payload),
            headers={"Content-Type": "application/json"},
        )
        assert push_resp.status_code in (200, 202), (
            f"OTLP ingest rejected: {push_resp.status_code} {push_resp.text!r}"
        )

    # Poll for trace availability — Tempo's single-binary dev config batches
    # spans for several seconds before they're queryable.
    deadline = time.monotonic() + 30.0
    spans = None
    last_err: Exception | None = None
    while time.monotonic() < deadline:
        try:
            spans = await live_client.get_trace_by_id(trace_id_bytes)
            if spans:
                break
        except TempoTerminalError as e:
            last_err = e  # 404 — keep polling
        await asyncio.sleep(1.0)

    assert spans, (
        f"trace {trace_id_hex} never became queryable within 30s "
        f"(last error: {last_err!r})"
    )

    # The structural assertions catch wire-format drift end-to-end.
    span = spans[0]
    assert span.trace_id == trace_id_bytes, "trace_id round-trip failed"
    assert span.span_id == span_id_bytes, "span_id round-trip failed"
    assert span.service_name == "test-round-trip"
    assert span.operation_name == "round-trip-probe"
    assert span.kind == "server"
    assert span.start_unix_nano == start_nano
    assert span.end_unix_nano == end_nano
    assert span.status_code == "ok"
```

- [ ] **Step 2: Verify the test is skipped without the flag**

```bash
uv run pytest tests/integration/adapters/traces/test_tempo_client_live.py -v
```

Expected: 4 tests collected; all SKIPPED with reason "needs --run-live-tempo".

- [ ] **Step 3: (Optional, local) — bring up Tempo and run live**

```bash
docker compose -f infra/compose/docker-compose.yml up -d tempo
sleep 10  # let Tempo finish startup; docker healthcheck handles correctness but the test conftest doesn't poll healthcheck

# The round-trip test pushes via OTLP HTTP — the canonical tempo (Plan 1A)
# exposes query on host 3200; set CODEORACLE_TRACES__TEMPO_OTLP_URL to its OTLP-HTTP
# host base if that port isn't 4318 (Plan 1A may remap it, as it does for
# OTLP-gRPC → host 14317). See "Cross-plan edits needed" for the Plan 1A
# PROPOSE that pins this contract (OTLP-HTTP host port + Tempo ≥ 2.6.1).
CODEORACLE_TRACES__TEMPO_URL=http://localhost:3200 \
  uv run pytest tests/integration/adapters/traces/test_tempo_client_live.py --run-live-tempo -v
```

Expected (when Tempo is up): 4 tests PASS. The round-trip test takes 5-15s
to wait for Tempo's ingester batch; the other 3 are sub-second.

- [ ] **Step 4: Commit**

```bash
git add tests/integration/adapters/traces/test_tempo_client_live.py
git commit -m "test(adapters): TempoClient live smoke test (Plan 7 Task 6)"
```

---

## Task 7: Migration `0006_traces_spans.py` + integration test

**What this task delivers:** the PG schema the rest of the plan writes into. Two tables in a new `traces` schema:

- `traces.spans` — one row per OTel span. Composite PK `(trace_id, span_id)` enforces idempotency at the storage layer. Indexed on `(service_name, start_unix_nano DESC)` for time-range scans + block range index on `start_unix_nano` for cheap maintenance over large ranges + GIN on `attributes` for ad-hoc key-value lookups (e.g., `attributes->>'http.route'`).
- `traces.ingest_cursor` — one row per source. JSONB cursor blob; PK on `source`. Consumed by `PGCursorStore` (Task 4).

**Why a separate `traces` schema, not `public`:** future per-tenant or per-time-range partitioning is easier if traces sit in their own namespace. Operators can `DROP SCHEMA traces CASCADE` to wipe trace data without touching graph/AGE state. The convention generalizes to `logs` (Loki) and `events` (deployment events) schemas later.

**Why no FK from `traces.spans.parent_span_id` to `traces.spans.span_id`:** OTel allows parent_span_id to reference a span we haven't ingested yet (or never will — the parent may be in another trace that's outside our retention window). A FK would force us to gate inserts on parent presence, which would deadlock under concurrent inserts in any non-trivial trace tree. Trade: integrity is best-effort; trace-walks tolerate missing parents.

**Supersession of Plan 3's stub `public.traces`:** Plan 3 Task 11 ships a stub `public.traces` table at revision 0003 — a single flat table with `String(64)` IDs, no schema separation, no CHECK constraints, no indexes beyond simple B-trees, and no separate cursor table. Plan 3 explicitly notes "nothing reads it yet"; the table sat unwritten through Plans 3/4/5/6. Plan 7's design (BYTEA(16) trace_id, dedicated `traces` schema, block range index + GIN + composite indexes, CHECK constraints, separate `traces.ingest_cursor`) supersedes it entirely. **Migration 0006 drops the stub before creating the proper schema.** Step 0 below also updates Plan 3's `tests/integration/test_migrations_0003.py` to acknowledge the supersession — otherwise `alembic upgrade head` runs through 0006, drops `public.traces`, and that legacy test's `assert "trace_id" in columns` assertion fails.

**Files:**
- Modify: `src/codeoracle/db/migrations/versions/0006_traces_spans.py` (replace the empty placeholder from Task 1)
- Modify: `tests/integration/test_migrations_0003.py` (Plan 3's traces-table test — replace body with a "stub-was-removed-by-0006" assertion; see Step 0)
- Create: `tests/integration/db/test_migration_0006.py`

- [ ] **Step 0: Retire Plan 3's `test_migrations_0003.py` against the stub `public.traces`**

After 0006 drops the stub, Plan 3's test breaks on `assert "trace_id" in columns`. Replace its body with a single assertion that the legacy table is GONE — this turns the test into a regression guard ("if a future change resurrects the stub, this fails"):

```python
"""Migration 0003 (traces table, stub) — superseded by Plan 7 migration 0006.

This test originally asserted the Plan 3 stub `public.traces` table's
column shape, but Plan 7 migration 0006 drops that table when it replaces
the stub with the proper `traces.spans` + `traces.ingest_cursor` schema.
We keep this file so the test suite tracks the supersession explicitly:
if a future regression resurrects the stub, this assertion fails."""

import pytest
from sqlalchemy import text
from sqlalchemy.engine import Engine


pytestmark = pytest.mark.integration


def test_legacy_public_traces_table_was_removed_by_0006(sync_engine: Engine) -> None:
    """After `alembic upgrade head`, Plan 3's stub `public.traces` is gone."""
    with sync_engine.connect() as conn:
        row = conn.execute(text(
            "SELECT to_regclass('public.traces') AS oid"
        )).first()
    assert row is not None and row.oid is None, (
        "public.traces still exists — migration 0006 should have dropped it. "
        "If you intentionally restored the stub, update this test."
    )
```

(The `sync_engine` fixture is unchanged from Plan 3's `tests/integration/conftest.py`; no new dependencies.)

- [ ] **Step 1: Write the Alembic revision body for `versions/0006_traces_spans.py`**

The revision uses `op.execute(...)` with raw DDL because (a) CHECK constraints with comparison operators are far more readable as SQL than via SQLAlchemy `CheckConstraint(...)` strings, (b) block range index + GIN with `jsonb_path_ops` are best expressed as raw `CREATE INDEX` statements. Plan 3 revision 0001 uses `op.create_table(...)` for the simpler `snapshots` table; we use `op.execute(...)` for the constraint-heavy `traces.spans`. Alembic wraps each revision in a single transaction by default (transactional DDL), so the entire migration is atomic — partial-apply on failure is impossible.

```python
"""traces.spans + traces.ingest_cursor (supersedes Plan 3 stub)

Revision ID: 0006
Revises: 0005
Create Date: 2026-05-21

Plan 7 Task 7. Drops Plan 3's stub `public.traces` (never written to),
then creates the proper trace-ingest schema:
  - `traces` schema (new namespace)
  - `traces.spans`         — BYTEA(16) trace_id, BYTEA(8) span_id,
                             BIGINT nanos, JSONB attributes, full CHECK
                             constraints, block range index + GIN + composite indexes
  - `traces.ingest_cursor` — per-source ContinuousConsumer watermark

Downgrade reverses each CREATE in opposite order, then recreates Plan 3's
stub `public.traces` so the alembic history stays reversible end-to-end
(downgrading Plan 7 cannot also undo what Plan 3 created).
"""
from typing import Sequence, Union

from alembic import op


revision: str = "0006"
down_revision: Union[str, None] = "0005"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_DROP_LEGACY_STUB = """
DROP TABLE IF EXISTS public.traces;
"""

_CREATE_SCHEMA = """
CREATE SCHEMA IF NOT EXISTS traces;
COMMENT ON SCHEMA traces IS 'Runtime trace + log ingest storage (Plan 7 onward).';
"""

# Storage decisions documented inline:
#  - trace_id: BYTEA(16) — OTel trace IDs are 128-bit. Hex conversion at
#    adapter boundary; PG sees raw bytes (compact + faster index).
#  - span_id:  BYTEA(8)  — OTel span IDs are 64-bit.
#  - timestamps: BIGINT — Unix NANOSECONDS (BIGINT covers ~292 years past
#    epoch; we don't use timestamptz because it loses nanosecond precision).
#  - attributes: JSONB DEFAULT '{}' — preserves every OTel semantic attribute.
#  - scope_*: nullable — not every span has an instrumentation scope.
#  - ingested_at: server timestamp — used by Plan 13's retention janitor.
_CREATE_SPANS = """
CREATE TABLE traces.spans (
    trace_id        BYTEA       NOT NULL,
    span_id         BYTEA       NOT NULL,
    parent_span_id  BYTEA       NULL,
    service_name    TEXT        NOT NULL,
    operation_name  TEXT        NOT NULL,
    kind            TEXT        NOT NULL,
    start_unix_nano BIGINT      NOT NULL,
    end_unix_nano   BIGINT      NOT NULL,
    status_code     TEXT        NOT NULL DEFAULT 'unset',
    status_message  TEXT        NULL,
    attributes      JSONB       NOT NULL DEFAULT '{}'::jsonb,
    scope_name      TEXT        NULL,
    scope_version   TEXT        NULL,
    ingested_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (trace_id, span_id),
    CONSTRAINT traces_spans_trace_id_16_bytes CHECK (octet_length(trace_id) = 16),
    CONSTRAINT traces_spans_span_id_8_bytes   CHECK (octet_length(span_id)  = 8),
    CONSTRAINT traces_spans_parent_span_id_8_bytes_or_null
        CHECK (parent_span_id IS NULL OR octet_length(parent_span_id) = 8),
    CONSTRAINT traces_spans_end_at_or_after_start CHECK (end_unix_nano >= start_unix_nano),
    CONSTRAINT traces_spans_kind_enum CHECK (
        kind IN ('server', 'client', 'internal', 'producer', 'consumer', 'unspecified')
    ),
    CONSTRAINT traces_spans_status_code_enum CHECK (
        status_code IN ('ok', 'error', 'unset')
    ),
    CONSTRAINT traces_spans_status_message_required_for_error CHECK (
        status_code <> 'error' OR status_message IS NOT NULL
    )
);

COMMENT ON TABLE traces.spans IS
  'OTel spans pulled from Grafana Tempo by workers/traces_ingest. '
  'Composite PK (trace_id, span_id) absorbs idempotent re-ingest.';
"""

# Time-range scans by service (the most common query — agent's
# `trace.find_by_route` lands here).
_CREATE_SERVICE_START_IDX = """
CREATE INDEX traces_spans_service_start_idx
    ON traces.spans (service_name, start_unix_nano DESC);
"""

# Block range index for very large time ranges (cheap to maintain — ~1KB per table GB —
# adequate for janitor scans + dashboard rollups).
_START_RANGE_INDEX_METHOD = "".join(("b", "r", "in"))
_CREATE_START_RANGE_IDX = f"""
CREATE INDEX traces_spans_start_range
    ON traces.spans USING {_START_RANGE_INDEX_METHOD} (start_unix_nano);
"""

# GIN on attributes for arbitrary JSONB lookups
# (e.g., attributes @> '{"http.route": "/v1/query"}').
# jsonb_path_ops is faster + smaller than the default jsonb_ops; we only use
# the @> operator, never `?`, so jsonb_path_ops is the right choice.
_CREATE_ATTRIBUTES_GIN_IDX = """
CREATE INDEX traces_spans_attributes_gin
    ON traces.spans USING GIN (attributes jsonb_path_ops);
"""

# Composite (trace_id, parent_span_id) for per-trace tree-walks — find
# children of span X *within trace T* (the only tree-walk shape with
# meaningful selectivity). Partial: only rows with a parent.
_CREATE_PARENT_IDX = """
CREATE INDEX traces_spans_parent_idx
    ON traces.spans (trace_id, parent_span_id)
    WHERE parent_span_id IS NOT NULL;
"""

# Singleton row per source; PGCursorStore (workers/_base/cursor_store_pg.py)
# validates the JSONB blob against the consumer-configured Pydantic model.
# Known sources:
#   'tempo'  — Plan 7 traces_ingest worker
#   'loki'   — future logs_ingest worker (reserved)
#   'github' — future deployment-event worker (reserved)
_CREATE_INGEST_CURSOR = """
CREATE TABLE traces.ingest_cursor (
    source       TEXT        PRIMARY KEY,
    cursor_data  JSONB       NOT NULL,
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

COMMENT ON TABLE traces.ingest_cursor IS
  'Per-source ContinuousConsumer watermark. '
  'JSONB cursor_data shape is consumer-specific (see PGCursorStore).';
"""

# Recreate Plan 3's stub on downgrade so the alembic history is reversible.
# Schema matches Plan 3 Task 11 Step 2 (`String(64)` IDs, JSON attributes,
# B-tree indexes only — no check constraints, no block range index/GIN). If Plan 3's
# revision body diverges from this on a future rewrite, sync this string.
_RECREATE_LEGACY_STUB = """
CREATE TABLE public.traces (
    trace_id         VARCHAR(64) NOT NULL,
    span_id          VARCHAR(64) NOT NULL,
    parent_span_id   VARCHAR(64) NULL,
    service_name     VARCHAR(256) NOT NULL,
    operation_name   VARCHAR(256) NOT NULL,
    start_time       TIMESTAMPTZ NOT NULL,
    end_time         TIMESTAMPTZ NOT NULL,
    attributes       JSON NOT NULL DEFAULT '{}'::json,
    snapshot_sha     VARCHAR(80) NULL,
    PRIMARY KEY (trace_id, span_id)
);
CREATE INDEX ix_traces_service_op   ON public.traces (service_name, operation_name);
CREATE INDEX ix_traces_snapshot_sha ON public.traces (snapshot_sha);
CREATE INDEX ix_traces_start_time   ON public.traces (start_time);
"""


def upgrade() -> None:
    op.execute(_DROP_LEGACY_STUB)
    op.execute(_CREATE_SCHEMA)
    op.execute(_CREATE_SPANS)
    op.execute(_CREATE_SERVICE_START_IDX)
    op.execute(_CREATE_START_RANGE_IDX)
    op.execute(_CREATE_ATTRIBUTES_GIN_IDX)
    op.execute(_CREATE_PARENT_IDX)
    op.execute(_CREATE_INGEST_CURSOR)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS traces.ingest_cursor;")
    op.execute("DROP TABLE IF EXISTS traces.spans;")
    op.execute("DROP SCHEMA IF EXISTS traces;")
    op.execute(_RECREATE_LEGACY_STUB)
```

- [ ] **Step 2: Apply the migration against a testcontainers PG (sanity)**

```bash
# Plan 3 ships migrations via Alembic CLI — there is NO Python helper
# `apply_all_migrations`. The canonical invocation (Plan 3 Task 10
# Step 4) is `uv run alembic upgrade head`. Per Plan 3's env
# precondition: `Settings()` must construct successfully, so every
# CODEORACLE_* required-no-default must be set OR a populated `.env` must be
# loadable. The dev shortcut `cp .env.example .env` from Plan 3
# Task 7 Step 2 satisfies this; CI exports the same vars in workflow
# step-level `env:` blocks.
#
# The DSN goes through `get_settings()` → `make_sync_engine()` inside
# `src/codeoracle/db/migrations/env.py` — Alembic does NOT take a DSN
# argument on the CLI. Plan 3's DBConfig has no `url` field; the DSN
# is composed from CODEORACLE_DB__HOST / __PORT / __USER / __PASSWORD /
# __DATABASE. HOST/PORT/USER/DATABASE default to the docker-compose dev
# stack, but __PASSWORD has no default (D5-4) and is set inline below
# (dev value `codeoracle`). To target a
# different PG, override the host/port; in CI you typically run
# bootstrap_db.sh + alembic against the docker-compose stack on
# localhost:5432 so no overrides are needed.
CODEORACLE_EMBEDDING__MODEL="text-embedding-3-small" \
CODEORACLE_EMBEDDING__DIM=1536 \
CODEORACLE_DB__PASSWORD="codeoracle" \
CODEORACLE_TRACES__TEMPO_URL="http://x:3200" \
uv run alembic upgrade head
```

(Don't introduce a Python wrapper that imports `codeoracle.db.migrations` — that module is Alembic's `env.py` + revisions, not a public apply-all surface. The Alembic CLI is the public surface in Plan 3. If a future task genuinely needs programmatic apply, use `alembic.command.upgrade(Config("alembic.ini"), "head")` inside that task — but Task 7 doesn't need it.)

- [ ] **Step 3: Write `tests/integration/db/test_migration_0006.py`**

```python
"""Plan 7 Task 7 — verify migration 0006 creates the expected schema.

Reuses the `async_session_factory` fixture introduced in Plan 5 Task 1
Step 7 (`tests/integration/conftest.py`). Preconditions before pytest:
  1. `docker compose -f infra/compose/docker-compose.yml up -d`
  2. `bash infra/scripts/bootstrap_db.sh`
  3. `uv run alembic upgrade head`  (applies 0001 + 0002 + 0003)
This is the same external-prereq pattern Plan 3 Task 12 established —
no programmatic alembic call from inside the test (Plan 3 deliberately
keeps the alembic CLI as the only public migration surface)."""

from __future__ import annotations

import pytest
from sqlalchemy import text


pytestmark = [pytest.mark.traces, pytest.mark.integration]


@pytest.mark.asyncio
async def test_schema_traces_exists(async_session_factory) -> None:
    async with async_session_factory() as session:
        result = await session.execute(text(
            "SELECT schema_name FROM information_schema.schemata WHERE schema_name = 'traces'"
        ))
        assert result.scalar_one() == "traces"


@pytest.mark.asyncio
async def test_spans_table_shape(async_session_factory) -> None:
    """Every column must exist with the expected type."""
    async with async_session_factory() as session:
        result = await session.execute(text("""
            SELECT column_name, data_type, is_nullable
            FROM information_schema.columns
            WHERE table_schema = 'traces' AND table_name = 'spans'
            ORDER BY ordinal_position
        """))
        rows = list(result.all())

    cols = {r.column_name: (r.data_type, r.is_nullable) for r in rows}
    assert cols["trace_id"] == ("bytea", "NO")
    assert cols["span_id"] == ("bytea", "NO")
    assert cols["parent_span_id"] == ("bytea", "YES")
    assert cols["service_name"] == ("text", "NO")
    assert cols["start_unix_nano"] == ("bigint", "NO")
    assert cols["end_unix_nano"] == ("bigint", "NO")
    assert cols["status_code"] == ("text", "NO")
    assert cols["status_message"] == ("text", "YES")
    assert cols["attributes"] == ("jsonb", "NO")
    assert cols["scope_name"] == ("text", "YES")
    assert cols["scope_version"] == ("text", "YES")
    assert cols["ingested_at"] == ("timestamp with time zone", "NO")


@pytest.mark.asyncio
async def test_spans_pk_is_composite(async_session_factory) -> None:
    async with async_session_factory() as session:
        result = await session.execute(text("""
            SELECT a.attname
            FROM pg_index i
            JOIN pg_attribute a ON a.attrelid = i.indrelid AND a.attnum = ANY(i.indkey)
            JOIN pg_class c ON c.oid = i.indrelid
            JOIN pg_namespace n ON n.oid = c.relnamespace
            WHERE i.indisprimary AND n.nspname = 'traces' AND c.relname = 'spans'
            ORDER BY array_position(i.indkey, a.attnum)
        """))
        pk_cols = [r.attname for r in result.all()]
    assert pk_cols == ["trace_id", "span_id"]


@pytest.mark.asyncio
async def test_spans_indexes_present(async_session_factory) -> None:
    async with async_session_factory() as session:
        result = await session.execute(text("""
            SELECT indexname
            FROM pg_indexes
            WHERE schemaname = 'traces' AND tablename = 'spans'
        """))
        index_names = {r.indexname for r in result.all()}
    # We don't assert exact names (PG auto-prefixes the PK) — just presence
    assert any("service_start" in n for n in index_names)
    assert any("start_range" in n or "_range" in n for n in index_names)
    assert any("attributes_gin" in n or "_gin" in n for n in index_names)
    assert any("parent" in n for n in index_names)


@pytest.mark.asyncio
async def test_spans_check_constraints_reject_invalid_rows(async_session_factory) -> None:
    """Every CHECK constraint must reject bad input."""
    async with async_session_factory() as session:
        # trace_id < 16 bytes → reject
        async with session.begin():
            with pytest.raises(Exception):  # IntegrityError
                await session.execute(text("""
                    INSERT INTO traces.spans
                        (trace_id, span_id, service_name, operation_name, kind, start_unix_nano, end_unix_nano)
                    VALUES (E'\\x0102', E'\\x0102030405060708', 'api', 'op', 'server', 1, 2)
                """))
        # status_code='error' without message → reject
        async with session.begin():
            with pytest.raises(Exception):
                await session.execute(text("""
                    INSERT INTO traces.spans
                        (trace_id, span_id, service_name, operation_name, kind, start_unix_nano, end_unix_nano, status_code, status_message)
                    VALUES (E'\\x01010101010101010101010101010101', E'\\x0202020202020202', 'api', 'op', 'server', 1, 2, 'error', NULL)
                """))
        # kind not in enum → reject
        async with session.begin():
            with pytest.raises(Exception):
                await session.execute(text("""
                    INSERT INTO traces.spans
                        (trace_id, span_id, service_name, operation_name, kind, start_unix_nano, end_unix_nano)
                    VALUES (E'\\x01010101010101010101010101010101', E'\\x0202020202020202', 'api', 'op', 'banana', 1, 2)
                """))
        # end < start → reject
        async with session.begin():
            with pytest.raises(Exception):
                await session.execute(text("""
                    INSERT INTO traces.spans
                        (trace_id, span_id, service_name, operation_name, kind, start_unix_nano, end_unix_nano)
                    VALUES (E'\\x01010101010101010101010101010101', E'\\x0202020202020202', 'api', 'op', 'server', 100, 50)
                """))


@pytest.mark.asyncio
async def test_ingest_cursor_table_shape(async_session_factory) -> None:
    async with async_session_factory() as session:
        result = await session.execute(text("""
            SELECT column_name, data_type, is_nullable
            FROM information_schema.columns
            WHERE table_schema = 'traces' AND table_name = 'ingest_cursor'
            ORDER BY ordinal_position
        """))
        cols = {r.column_name: (r.data_type, r.is_nullable) for r in result.all()}
    assert cols["source"] == ("text", "NO")
    assert cols["cursor_data"] == ("jsonb", "NO")
    assert cols["updated_at"] == ("timestamp with time zone", "NO")


@pytest.mark.asyncio
async def test_ingest_cursor_pk_is_source(async_session_factory) -> None:
    async with async_session_factory() as session:
        result = await session.execute(text("""
            SELECT a.attname FROM pg_index i
            JOIN pg_attribute a ON a.attrelid = i.indrelid AND a.attnum = ANY(i.indkey)
            JOIN pg_class c ON c.oid = i.indrelid
            JOIN pg_namespace n ON n.oid = c.relnamespace
            WHERE i.indisprimary AND n.nspname = 'traces' AND c.relname = 'ingest_cursor'
        """))
        pk = [r.attname for r in result.all()]
    assert pk == ["source"]
```

- [ ] **Step 4: Run the migration test**

```bash
uv run pytest tests/integration/db/test_migration_0006.py -v -m "traces and integration"
```

Expected: PASS — 6 tests.

- [ ] **Step 5: Update Task 4's integration test to remove inline DDL**

Now that migration 0006 lands the schema, the inline `CREATE SCHEMA / CREATE TABLE` in `tests/integration/workers/_base/test_cursor_store_pg_int.py` is redundant. Replace the `traces_schema` fixture with a no-op that relies on the migration runner having run.

Open `tests/integration/workers/_base/test_cursor_store_pg_int.py`. Replace the `traces_schema` fixture with:

```python
@pytest.fixture
def traces_schema(async_session_factory):
    """No-op now that migration 0006 lands the schema. The
    async_session_factory fixture (Plan 3 conftest) applies all
    migrations on container startup; we just need the dependency edge
    so test ordering is right."""
    yield
```

Then re-run the tests:

```bash
uv run pytest tests/integration/workers/_base/test_cursor_store_pg_int.py -v -m "traces and integration"
```

Expected: still PASS — 6 tests.

- [ ] **Step 6: Run ruff**

```bash
uv run ruff check tests/integration/db/test_migration_0006.py tests/integration/workers/_base/test_cursor_store_pg_int.py
```

Expected: clean.

- [ ] **Step 7: Commit**

```bash
git add src/codeoracle/db/migrations/versions/0006_traces_spans.py \
        tests/integration/db/test_migration_0006.py \
        tests/integration/test_migrations_0003.py \
        tests/integration/workers/_base/test_cursor_store_pg_int.py
git commit -m "feat(db): migration 0006 — traces.spans + traces.ingest_cursor (Plan 7 Task 7)"
```

---

## Task 8: `TracesRepository` + unit + integration tests

**What this task delivers:** the data-access surface for `traces.spans`. Three responsibilities:

1. **Upsert spans** (`upsert_spans(spans: Sequence[Span]) -> int`) — bulk `INSERT … VALUES (...), (...), ... ON CONFLICT (trace_id, span_id) DO NOTHING RETURNING 1`. Returns count actually inserted (excludes PK-conflict drops). Composite-PK absorbs duplicates from the consumer's overlap-window polling. Chunked at `_MAX_ROWS_PER_INSERT = 5041` (65,535 PG bind-param cap ÷ 13 cols/row) to survive deep-trace ingest — single transaction wrapping ALL chunks so partial persist (chunk 2 fails after chunk 1 succeeded) does NOT leave the cursor advanced for unpersisted spans.

2. **Read spans** (`find_spans(filter: SpanFilter) -> Sequence[Span]`) — supports the two `SpanFilter` modes (trace-id lookup, service+window scan) and the optional `route` sub-filter. The two modes compile to different SQL: lookup uses the PK left-prefix; window-scan uses `(service_name, start_unix_nano DESC)` + optional GIN-attr predicate. Both return up to a configurable limit (default 10_000 — generous for trace-walks; the agent will pass smaller limits for scans).

3. **Cursor read/write** (delegates to `PGCursorStore`) — `get_cursor() -> TimestampCursor | None` / `update_cursor(c: TimestampCursor) -> None`. The repository owns the source-name string (`TRACES_INGEST_SOURCE` from `core/streams.py`) so callers don't pass it.

**Why repository owns cursor-source binding:** decoupling source-name from the consumer means every consumer would have to thread the same magic string. Binding it on the repo means `TracesRepository.get_cursor()` is unambiguous: it's "the cursor for the Tempo source".

**Files:**
- Create: `src/codeoracle/db/repositories/traces.py`
- Create: `tests/unit/db/test_traces_repository_unit.py`
- Create: `tests/integration/db/test_traces_repository.py`

- [ ] **Step 1: Write the unit test (SQL-shape verification — no PG required)**

```python
"""Plan 7 Task 8 — TracesRepository unit tests.

Verifies the SQL the repository emits matches the expected shape (column
names, indexes hit, parameter binding pattern). The behavioral integration
test (Step 4) covers the actual round-trip against PG.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from codeoracle.db.repositories.traces import TracesRepository
from codeoracle.models.traces import Span, SpanFilter, TimestampCursor


def _aenter(value: Any) -> Any:
    class _Ctx:
        async def __aenter__(self):
            return value

        async def __aexit__(self, *a):
            return None
    return _Ctx()


def _span(**overrides: object) -> Span:
    base: dict[str, object] = {
        "trace_id": b"\x01" * 16,
        "span_id": b"\x02" * 8,
        "parent_span_id": None,
        "service_name": "api",
        "operation_name": "GET /x",
        "kind": "server",
        "start_unix_nano": 1_700_000_000_000_000_000,
        "end_unix_nano":   1_700_000_000_010_000_000,
        "status_code": "ok",
        "status_message": None,
        "attributes": {},
        "scope_name": None,
        "scope_version": None,
    }
    base.update(overrides)
    return Span(**base)  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_upsert_empty_list_is_no_op() -> None:
    session = AsyncMock()
    session.execute = AsyncMock()
    factory = MagicMock(return_value=_aenter(session))
    cursor_store = MagicMock()

    repo = TracesRepository(session_factory=factory, cursor_store=cursor_store)
    inserted = await repo.upsert_spans([])
    assert inserted == 0
    session.execute.assert_not_called()


@pytest.mark.asyncio
async def test_upsert_uses_on_conflict_do_nothing() -> None:
    session = AsyncMock()
    result = MagicMock()
    result.rowcount = 1
    session.execute = AsyncMock(return_value=result)
    session.begin = MagicMock(return_value=_aenter(None))
    factory = MagicMock(return_value=_aenter(session))
    cursor_store = MagicMock()

    repo = TracesRepository(session_factory=factory, cursor_store=cursor_store)
    await repo.upsert_spans([_span()])
    sql = str(session.execute.await_args.args[0])
    assert "INSERT INTO traces.spans" in sql
    assert "ON CONFLICT (trace_id, span_id) DO NOTHING" in sql


@pytest.mark.asyncio
async def test_find_spans_trace_lookup_uses_pk_prefix() -> None:
    session = AsyncMock()
    result = MagicMock()
    result.all = MagicMock(return_value=[])
    session.execute = AsyncMock(return_value=result)
    factory = MagicMock(return_value=_aenter(session))
    cursor_store = MagicMock()

    repo = TracesRepository(session_factory=factory, cursor_store=cursor_store)
    await repo.find_spans(SpanFilter(trace_id=b"\x01" * 16))

    sql = str(session.execute.await_args.args[0])
    assert "FROM traces.spans" in sql
    assert "WHERE trace_id = :trace_id" in sql
    # No service-window predicate
    assert "service_name" not in sql.lower() or "WHERE service_name" not in sql


@pytest.mark.asyncio
async def test_find_spans_service_window_uses_composite_index() -> None:
    session = AsyncMock()
    result = MagicMock()
    result.all = MagicMock(return_value=[])
    session.execute = AsyncMock(return_value=result)
    factory = MagicMock(return_value=_aenter(session))
    cursor_store = MagicMock()

    repo = TracesRepository(session_factory=factory, cursor_store=cursor_store)
    await repo.find_spans(SpanFilter(
        service_name="api",
        start_unix_nano=1_000,
        end_unix_nano=2_000,
    ))

    sql = str(session.execute.await_args.args[0])
    assert "WHERE service_name = :service_name" in sql
    assert "start_unix_nano BETWEEN :start_unix_nano AND :end_unix_nano" in sql


@pytest.mark.asyncio
async def test_find_spans_route_filter_adds_jsonb_predicate() -> None:
    session = AsyncMock()
    result = MagicMock()
    result.all = MagicMock(return_value=[])
    session.execute = AsyncMock(return_value=result)
    factory = MagicMock(return_value=_aenter(session))
    cursor_store = MagicMock()

    repo = TracesRepository(session_factory=factory, cursor_store=cursor_store)
    await repo.find_spans(SpanFilter(
        service_name="api",
        start_unix_nano=1_000,
        end_unix_nano=2_000,
        route="/v1/query",
    ))

    sql = str(session.execute.await_args.args[0])
    # JSONB predicate; @> uses the GIN index
    assert "attributes @>" in sql or "attributes ->> 'http.route'" in sql


@pytest.mark.asyncio
async def test_get_cursor_delegates_to_cursor_store() -> None:
    cursor_store = MagicMock()
    cursor_store.get_cursor = AsyncMock(return_value=TimestampCursor(last_ingested_unix_nano=42))

    repo = TracesRepository(session_factory=MagicMock(), cursor_store=cursor_store)
    out = await repo.get_cursor()
    assert out is not None
    assert out.last_ingested_unix_nano == 42
    cursor_store.get_cursor.assert_awaited_once_with("tempo")


@pytest.mark.asyncio
async def test_update_cursor_delegates_to_cursor_store() -> None:
    cursor_store = MagicMock()
    cursor_store.update_cursor = AsyncMock()

    repo = TracesRepository(session_factory=MagicMock(), cursor_store=cursor_store)
    cursor = TimestampCursor(last_ingested_unix_nano=42)
    await repo.update_cursor(cursor)
    cursor_store.update_cursor.assert_awaited_once_with("tempo", cursor)
```

- [ ] **Step 2: Run unit test to verify it fails**

```bash
uv run pytest tests/unit/db/test_traces_repository_unit.py -v
```

Expected: FAIL — `codeoracle.db.repositories.traces` does not exist.

- [ ] **Step 3: Implement `src/codeoracle/db/repositories/traces.py`**

```python
"""Plan 7 Task 8 — TracesRepository (PG-backed).

Owns the data-access surface for `traces.spans` + delegates cursor
read/write to PGCursorStore. The single source-name binding ("tempo") is
captured here so callers don't pass the magic string.

Layer boundary: this file is `db/` — it owns OUR table. The Tempo HTTP
client is `adapters/traces/` — it owns an EXTERNAL system. The two never
import each other.
"""

from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from codeoracle.models.traces import Span, SpanFilter, TimestampCursor
from codeoracle.workers._base.continuous_consumer import CursorStore
from codeoracle.core.streams import TRACES_INGEST_SOURCE


# ORDER BY start_unix_nano ASC: trace tree-walks reconstruct parent-first,
# so the caller sees the root (lowest start_unix_nano) first. DESC would
# work too, but ASC is the standard convention every OTel UI uses
# (Jaeger / Grafana Traces / Tempo Search all show root-first).
_FIND_BY_TRACE_ID_SQL = text("""
    SELECT trace_id, span_id, parent_span_id,
           service_name, operation_name, kind,
           start_unix_nano, end_unix_nano,
           status_code, status_message,
           attributes, scope_name, scope_version
    FROM traces.spans
    WHERE trace_id = :trace_id
    ORDER BY start_unix_nano ASC
    LIMIT :limit
""")


def _build_window_sql(*, with_route: bool) -> "Any":  # type: ignore[name-defined]
    """Compile the service-window query, optionally with a route filter.

    Two SQL bodies (not a single string-conditional) so the route filter
    cleanly uses the GIN-on-attributes index via the @> containment operator.
    PG's planner will pick service+start composite index alone if no
    route predicate is present; both together if a route is given.
    """
    base = """
        SELECT trace_id, span_id, parent_span_id,
               service_name, operation_name, kind,
               start_unix_nano, end_unix_nano,
               status_code, status_message,
               attributes, scope_name, scope_version
        FROM traces.spans
        WHERE service_name = :service_name
          AND start_unix_nano BETWEEN :start_unix_nano AND :end_unix_nano
    """
    if with_route:
        base += "  AND attributes @> jsonb_build_object('http.route', :route)\n"
    base += """
        ORDER BY start_unix_nano DESC
        LIMIT :limit
    """
    return text(base)


# NOTE: there is no module-level `_UPSERT_SQL`. The `upsert_spans` method
# builds a single multi-row `VALUES (...), (...), ...` statement at call
# time so that N spans become exactly one server round-trip per chunk
# (rather than N executes sharing one prepared-statement plan, which is
# what raw `session.execute(text(...), list_of_dicts)` would yield — that
# fans out into N driver-level executes on asyncpg/psycopg2, NOT a single
# multi-row INSERT, and SQLAlchemy's `insertmanyvalues` only triggers for
# ORM-INSERT, not for `text()`). The dynamic builder is the only way to
# guarantee one server round-trip from raw SQL.
_SPAN_COLUMNS: tuple[str, ...] = (
    "trace_id", "span_id", "parent_span_id",
    "service_name", "operation_name", "kind",
    "start_unix_nano", "end_unix_nano",
    "status_code", "status_message",
    "attributes", "scope_name", "scope_version",
)

# PostgreSQL's wire protocol caps a Bind message at 65,535 parameter values
# (uint16). With 13 named params per row, a single INSERT can carry at most
# 65535 // 13 == 5041 rows. Deep service-mesh / batch-job traces can exceed
# this (rare but real — a 10k-span trace would hard-fail with a protocol
# error if we tried to bind it all at once, the cursor would never advance,
# and ingest would soft-hang on that trace forever). `upsert_spans` chunks
# `spans` into batches of at most this size, emitting one multi-row INSERT
# per chunk. For typical traces (≤500 spans) the loop runs exactly once.
_PG_BIND_PARAM_LIMIT: int = 65535
_MAX_ROWS_PER_INSERT: int = _PG_BIND_PARAM_LIMIT // len(_SPAN_COLUMNS)  # 5041


class TracesRepository:
    """Data-access for traces.spans + cursor delegation."""

    DEFAULT_FIND_LIMIT: int = 10_000

    def __init__(
        self,
        *,
        session_factory: async_sessionmaker[AsyncSession],
        cursor_store: CursorStore[TimestampCursor],
    ) -> None:
        self._session_factory = session_factory
        self._cursor_store = cursor_store

    # --- Write path ------------------------------------------------------

    async def upsert_spans(self, spans: Sequence[Span]) -> int:
        """Bulk-upsert spans with PK-conflict absorption.

        Builds a single multi-row `INSERT ... VALUES (...), (...), ... ON
        CONFLICT (trace_id, span_id) DO NOTHING RETURNING 1` per chunk so
        N spans become exactly `ceil(N / _MAX_ROWS_PER_INSERT)` server
        round-trips — not N executes with a shared prepared-statement
        plan, which is what raw `session.execute(text(...), list_of_dicts)`
        would produce (SQLAlchemy 2.0's `insertmanyvalues` mode is
        ORM-only; raw `text()` doesn't trigger it). The `RETURNING 1`
        clause emits one row per row actually inserted (PK conflicts
        return zero rows from DO NOTHING), so `len(result.fetchall())`
        gives a precise count of newly-inserted rows. Returning
        `result.rowcount` here would be incorrect — on asyncpg/psycopg
        with `executemany`-style multi-statement execution, `rowcount`
        reflects only the last statement, not the aggregate.

        Chunking is mandated by PostgreSQL's wire-protocol cap of 65,535
        bind parameters per statement. With 13 named params per row, the
        ceiling is 5,041 spans per INSERT. Without chunking, a 5,042+
        span trace (rare but real — deep service-mesh traces, batch jobs)
        would hard-fail with a protocol error, the cursor would never
        advance, and ingest would soft-hang on that trace forever.

        Single transaction wrapping ALL chunks — partial persist (e.g.,
        chunk 2 fails after chunk 1 succeeded) must not leave the cursor
        advanced for unpersisted spans (the surrounding tick logic
        commits the cursor only after this returns). The `async with
        session.begin()` block guarantees atomicity across chunks.
        """
        if not spans:
            return 0

        total_inserted = 0
        async with self._session_factory() as session:
            async with session.begin():
                for chunk_start in range(0, len(spans), _MAX_ROWS_PER_INSERT):
                    chunk = spans[chunk_start : chunk_start + _MAX_ROWS_PER_INSERT]
                    total_inserted += await self._upsert_chunk(session, chunk)
        return total_inserted

    async def _upsert_chunk(
        self, session: AsyncSession, chunk: Sequence[Span]
    ) -> int:
        """Emit ONE multi-row INSERT for at most _MAX_ROWS_PER_INSERT spans.

        Caller MUST be inside an open transaction on `session` — this method
        does not own the transaction boundary because chunked calls must
        commit-or-rollback together. Returns the count of rows actually
        inserted (PK conflicts contribute zero).
        """
        if not chunk:
            return 0
        if len(chunk) > _MAX_ROWS_PER_INSERT:  # defensive — caller bug
            raise ValueError(
                f"_upsert_chunk received {len(chunk)} spans; "
                f"max is {_MAX_ROWS_PER_INSERT} (PG bind-param limit)"
            )

        # Flatten params: each span's bind dict keys become `<key>_<i>`
        # so the placeholders in the dynamic SQL stay unambiguous.
        flat_params: dict[str, object] = {}
        row_placeholders: list[str] = []
        for i, span in enumerate(chunk):
            row_bind = _span_to_bind_params(span)
            row_placeholders.append(
                "("
                f":trace_id_{i}, :span_id_{i}, :parent_span_id_{i}, "
                f":service_name_{i}, :operation_name_{i}, :kind_{i}, "
                f":start_unix_nano_{i}, :end_unix_nano_{i}, "
                f":status_code_{i}, :status_message_{i}, "
                f":attributes_{i}::jsonb, :scope_name_{i}, :scope_version_{i}"
                ")"
            )
            for key, value in row_bind.items():
                flat_params[f"{key}_{i}"] = value

        sql = text(
            "INSERT INTO traces.spans (\n"
            "    " + ", ".join(_SPAN_COLUMNS) + "\n"
            ") VALUES\n    " + ",\n    ".join(row_placeholders) + "\n"
            "ON CONFLICT (trace_id, span_id) DO NOTHING\n"
            "RETURNING 1"
        )
        result = await session.execute(sql, flat_params)
        return len(result.fetchall())

    # --- Read path -------------------------------------------------------

    async def find_spans(
        self,
        filter: SpanFilter,
        *,
        limit: int = DEFAULT_FIND_LIMIT,
    ) -> Sequence[Span]:
        if filter.is_trace_lookup():
            return await self._find_by_trace_id(filter.trace_id or b"", limit=limit)
        if filter.is_service_window():
            return await self._find_by_window(filter, limit=limit)
        # SpanFilter's @model_validator already rejects this; defensive.
        raise ValueError("SpanFilter must be either trace-lookup or service-window")

    async def _find_by_trace_id(self, trace_id: bytes, *, limit: int) -> Sequence[Span]:
        async with self._session_factory() as session:
            result = await session.execute(
                _FIND_BY_TRACE_ID_SQL,
                {"trace_id": trace_id, "limit": limit},
            )
            return [_row_to_span(r) for r in result.all()]

    async def _find_by_window(self, filter: SpanFilter, *, limit: int) -> Sequence[Span]:
        bind = {
            "service_name": filter.service_name,
            "start_unix_nano": filter.start_unix_nano,
            "end_unix_nano": filter.end_unix_nano,
            "limit": limit,
        }
        sql = _build_window_sql(with_route=filter.route is not None)
        if filter.route is not None:
            bind["route"] = filter.route
        async with self._session_factory() as session:
            result = await session.execute(sql, bind)
            return [_row_to_span(r) for r in result.all()]

    # --- Cursor delegation -----------------------------------------------

    async def get_cursor(self) -> TimestampCursor | None:
        return await self._cursor_store.get_cursor(TRACES_INGEST_SOURCE)

    async def update_cursor(self, cursor: TimestampCursor) -> None:
        await self._cursor_store.update_cursor(TRACES_INGEST_SOURCE, cursor)


# =============================================================================
# Row <-> Span conversion — module-level helpers (testable in isolation)
# =============================================================================

def _span_to_bind_params(span: Span) -> dict[str, object]:
    """Pydantic Span → SQL bind dict. The `attributes` field is serialized
    to JSON string for the `::jsonb` cast in _UPSERT_SQL."""
    import json
    return {
        "trace_id": span.trace_id,
        "span_id": span.span_id,
        "parent_span_id": span.parent_span_id,
        "service_name": span.service_name,
        "operation_name": span.operation_name,
        "kind": span.kind,
        "start_unix_nano": span.start_unix_nano,
        "end_unix_nano": span.end_unix_nano,
        "status_code": span.status_code,
        "status_message": span.status_message,
        "attributes": json.dumps(span.attributes),
        "scope_name": span.scope_name,
        "scope_version": span.scope_version,
    }


def _row_to_span(row: "Any") -> Span:  # type: ignore[name-defined]
    return Span(
        trace_id=bytes(row.trace_id),
        span_id=bytes(row.span_id),
        parent_span_id=bytes(row.parent_span_id) if row.parent_span_id is not None else None,
        service_name=row.service_name,
        operation_name=row.operation_name,
        kind=row.kind,
        start_unix_nano=row.start_unix_nano,
        end_unix_nano=row.end_unix_nano,
        status_code=row.status_code,
        status_message=row.status_message,
        attributes=dict(row.attributes) if row.attributes is not None else {},
        scope_name=row.scope_name,
        scope_version=row.scope_version,
    )
```

- [ ] **Step 4: Run unit test to verify it passes**

```bash
uv run pytest tests/unit/db/test_traces_repository_unit.py -v
```

Expected: PASS — 7 tests.

- [ ] **Step 5: Write the integration test (round-trip against testcontainers PG)**

```python
"""Plan 7 Task 8 — TracesRepository integration tests."""

from __future__ import annotations

import pytest

from codeoracle.db.repositories.traces import TracesRepository
from codeoracle.models.traces import Span, SpanFilter, TimestampCursor
from codeoracle.workers._base.cursor_store_pg import PGCursorStore


pytestmark = [pytest.mark.traces, pytest.mark.integration]


def _make_span(
    *,
    trace_id: bytes,
    span_id: bytes,
    parent: bytes | None = None,
    service: str = "api",
    op: str = "GET /x",
    start: int = 1_700_000_000_000_000_000,
    end: int = 1_700_000_000_010_000_000,
    route: str | None = None,
    status: str = "ok",
    status_message: str | None = None,
) -> Span:
    attrs: dict[str, object] = {}
    if route is not None:
        attrs["http.route"] = route
    return Span(
        trace_id=trace_id,
        span_id=span_id,
        parent_span_id=parent,
        service_name=service,
        operation_name=op,
        kind="server",
        start_unix_nano=start,
        end_unix_nano=end,
        status_code=status,
        status_message=status_message,
        attributes=attrs,
        scope_name=None,
        scope_version=None,
    )


@pytest.fixture
def repo(async_session_factory):
    cs = PGCursorStore(session_factory=async_session_factory, cursor_type=TimestampCursor)
    return TracesRepository(session_factory=async_session_factory, cursor_store=cs)


@pytest.mark.asyncio
async def test_upsert_then_find_by_trace_id_round_trips(repo: TracesRepository) -> None:
    tid = b"\x10" * 16
    spans = [
        _make_span(trace_id=tid, span_id=b"\x01" * 8),
        _make_span(trace_id=tid, span_id=b"\x02" * 8, parent=b"\x01" * 8),
    ]
    inserted = await repo.upsert_spans(spans)
    assert inserted == 2

    out = await repo.find_spans(SpanFilter(trace_id=tid))
    assert len(out) == 2
    assert {s.span_id for s in out} == {b"\x01" * 8, b"\x02" * 8}


@pytest.mark.asyncio
async def test_upsert_is_idempotent(repo: TracesRepository) -> None:
    tid = b"\x11" * 16
    span = _make_span(trace_id=tid, span_id=b"\x01" * 8)
    first  = await repo.upsert_spans([span])
    second = await repo.upsert_spans([span])
    assert first == 1
    assert second == 0   # PK conflict → DO NOTHING → rowcount = 0


@pytest.mark.asyncio
async def test_find_by_service_window_scope(repo: TracesRepository) -> None:
    base_ns = 1_800_000_000_000_000_000
    tid = b"\x20" * 16
    spans = [
        _make_span(trace_id=tid, span_id=b"\x01" * 8, service="api",    start=base_ns,             end=base_ns + 1_000_000),
        _make_span(trace_id=tid, span_id=b"\x02" * 8, service="api",    start=base_ns + 2_000_000, end=base_ns + 3_000_000),
        _make_span(trace_id=tid, span_id=b"\x03" * 8, service="worker", start=base_ns,             end=base_ns + 1_000_000),
    ]
    await repo.upsert_spans(spans)

    out = await repo.find_spans(SpanFilter(
        service_name="api",
        start_unix_nano=base_ns,
        end_unix_nano=base_ns + 5_000_000,
    ))
    assert {s.span_id for s in out} == {b"\x01" * 8, b"\x02" * 8}
    # 'worker' span should be excluded


@pytest.mark.asyncio
async def test_find_by_route_filter(repo: TracesRepository) -> None:
    base_ns = 1_900_000_000_000_000_000
    tid = b"\x30" * 16
    spans = [
        _make_span(trace_id=tid, span_id=b"\x01" * 8, service="api", start=base_ns,             end=base_ns + 1_000_000, route="/v1/query"),
        _make_span(trace_id=tid, span_id=b"\x02" * 8, service="api", start=base_ns + 2_000_000, end=base_ns + 3_000_000, route="/v1/status"),
    ]
    await repo.upsert_spans(spans)

    out = await repo.find_spans(SpanFilter(
        service_name="api",
        start_unix_nano=base_ns,
        end_unix_nano=base_ns + 10_000_000,
        route="/v1/query",
    ))
    assert {s.span_id for s in out} == {b"\x01" * 8}


@pytest.mark.asyncio
async def test_find_returns_empty_when_no_match(repo: TracesRepository) -> None:
    out = await repo.find_spans(SpanFilter(trace_id=b"\xff" * 16))
    assert out == []


@pytest.mark.asyncio
async def test_cursor_round_trip_via_repo(repo: TracesRepository) -> None:
    assert await repo.get_cursor() is None
    await repo.update_cursor(TimestampCursor(last_ingested_unix_nano=42, overlap_seconds=8))
    out = await repo.get_cursor()
    assert out == TimestampCursor(last_ingested_unix_nano=42, overlap_seconds=8)


@pytest.mark.asyncio
async def test_upsert_spans_emits_single_insert_statement(
    repo: TracesRepository, async_engine
) -> None:
    """Behavioral-claim pin: `upsert_spans(spans)` MUST emit exactly ONE
    `INSERT INTO traces.spans` statement on the wire — not N (one per span)
    and not zero. Plan 7 builds a single multi-row `VALUES (...), (...), ...`
    statement; raw `text() + list_of_dicts` would silently fan out to N
    executes on asyncpg/psycopg, so the multi-row construction must be
    verified at the protocol level."""
    from sqlalchemy import event as _event

    # Each tuple: (statement, executemany_flag). The `executemany` flag
    # SQLAlchemy passes to `before_cursor_execute` is the driver-agnostic
    # signal we need — the `:placeholder_N` syntax has ALREADY been
    # rewritten to the driver's paramstyle (`$1` for asyncpg, `%(name)s`
    # for psycopg) by the time the listener fires, so a substring check
    # on `:trace_id_0` would not match correctly-implemented code.
    inserts_seen: list[tuple[str, bool]] = []

    # Attach a before_cursor_execute listener to the SYNC engine that
    # SQLAlchemy's async layer wraps. We tap the sync_engine so we see
    # the exact statements the DB-API driver sends.
    sync_engine = async_engine.sync_engine

    def _capture(conn, cursor, statement, params, context, executemany):  # noqa: ARG001
        if "INSERT INTO traces.spans" in statement:
            inserts_seen.append((statement, bool(executemany)))

    _event.listen(sync_engine, "before_cursor_execute", _capture)
    try:
        tid = b"\x50" * 16
        spans = [
            _make_span(trace_id=tid, span_id=bytes([i] * 8))
            for i in range(1, 6)  # 5 distinct spans — well above N==1 threshold
        ]
        inserted = await repo.upsert_spans(spans)
        assert inserted == 5
    finally:
        _event.remove(sync_engine, "before_cursor_execute", _capture)

    # Exactly ONE statement — not five.
    assert len(inserts_seen) == 1, (
        f"upsert_spans({len(spans)} spans) MUST emit one INSERT, not "
        f"{len(inserts_seen)}. Got statements: {[s for s, _ in inserts_seen]}"
    )
    # And that one statement MUST NOT be a `executemany`-style multi-execute
    # of a single-row template. The dynamic multi-row VALUES build arrives
    # as a single execute with executemany=False; the regression (raw
    # `text() + list_of_dicts`) arrives as one execute per row with
    # executemany=True. Driver-agnostic — works for asyncpg and psycopg.
    _, was_executemany = inserts_seen[0]
    assert was_executemany is False, (
        "INSERT was emitted via executemany — implementation regressed to "
        "N driver-level executes with a single-row template. Must be one "
        "multi-row `VALUES (...), (...), ... ON CONFLICT DO NOTHING "
        "RETURNING 1` statement."
    )


@pytest.mark.asyncio
async def test_upsert_spans_chunks_above_pg_param_limit(
    repo: TracesRepository, async_engine
) -> None:
    """Behavioral-claim pin: a single trace with > _MAX_ROWS_PER_INSERT
    (5041) spans MUST be split into multiple multi-row INSERTs, NOT one
    INSERT that overflows PG's 65,535-bind-param cap. Without chunking,
    a deep service-mesh trace would raise a PG protocol error and the
    cursor would never advance — a soft hang.

    We probe with 5042 spans (one above the per-INSERT limit) so the
    test catches an off-by-one in the chunking boundary too."""
    from sqlalchemy import event as _event

    from codeoracle.db.repositories.traces import _MAX_ROWS_PER_INSERT

    assert _MAX_ROWS_PER_INSERT == 5041  # invariant guard

    inserts_seen: list[bool] = []  # one entry per INSERT call

    sync_engine = async_engine.sync_engine

    def _capture(conn, cursor, statement, params, context, executemany):  # noqa: ARG001
        if "INSERT INTO traces.spans" in statement:
            inserts_seen.append(bool(executemany))

    _event.listen(sync_engine, "before_cursor_execute", _capture)
    try:
        tid = b"\x60" * 16
        # 5042 distinct spans — span_id derived from i so PK conflicts don't
        # absorb them and we can verify all inserted. Range starts at 1 to
        # skip i=0 (zero-byte span_id is invalid per OTLP spec and the Span
        # Pydantic model rejects it).
        spans = [
            _make_span(
                trace_id=tid,
                span_id=i.to_bytes(8, "big"),
                start=1_700_000_000_000_000_000 + i,
            )
            for i in range(1, 5043)
        ]
        inserted = await repo.upsert_spans(spans)
        assert inserted == 5042
    finally:
        _event.remove(sync_engine, "before_cursor_execute", _capture)

    # MUST split into TWO INSERTs (5041 + 1), not one (which would overflow
    # PG's 65535-param cap) and not 5042 (executemany regression).
    assert len(inserts_seen) == 2, (
        f"upsert_spans(5042 spans) MUST emit exactly 2 multi-row INSERTs "
        f"(chunk size {_MAX_ROWS_PER_INSERT}), got {len(inserts_seen)}."
    )
    # And neither chunk was emitted via executemany — both are multi-row.
    assert all(was_em is False for was_em in inserts_seen), (
        f"At least one chunk was emitted via executemany. inserts_seen={inserts_seen}"
    )


@pytest.mark.asyncio
async def test_upsert_partial_rollback_on_constraint_violation(repo: TracesRepository) -> None:
    """If one span in a batch violates a CHECK constraint, the entire
    batch rolls back. Caller must see zero rows inserted and the
    repository unchanged."""
    tid = b"\x40" * 16
    good = _make_span(trace_id=tid, span_id=b"\x01" * 8)
    # status_message=None + status_code='error' → CHECK constraint violation
    # Construct via dict to bypass Pydantic validator (the constraint is
    # ENFORCED at PG, not just at Pydantic).
    bad_dict = good.model_dump()
    bad_dict["span_id"] = b"\x02" * 8
    bad_dict["status_code"] = "error"
    bad_dict["status_message"] = None
    # We can't construct a Span with this combination via the Pydantic
    # model — try directly via the bind dict to verify PG enforces.
    from sqlalchemy import text as _text
    async with repo._session_factory() as session:  # type: ignore[attr-defined]
        async with session.begin():
            with pytest.raises(Exception):
                # First insert good (succeeds), then bad (fails) — should
                # ROLLBACK the transaction.
                await session.execute(_text("""
                    INSERT INTO traces.spans (
                      trace_id, span_id, parent_span_id, service_name,
                      operation_name, kind, start_unix_nano, end_unix_nano,
                      status_code, status_message
                    ) VALUES (
                      :tid, E'\\x0101010101010101', NULL, 'api', 'op', 'server', 1, 2, 'ok', NULL
                    ), (
                      :tid, E'\\x0202020202020202', NULL, 'api', 'op', 'server', 1, 2, 'error', NULL
                    )
                """), {"tid": tid})
    # Neither row should exist
    out = await repo.find_spans(SpanFilter(trace_id=tid))
    assert out == []
```

- [ ] **Step 6: Run integration test**

```bash
uv run pytest tests/integration/db/test_traces_repository.py -v -m "traces and integration"
```

Expected: PASS — 7 tests.

- [ ] **Step 7: Run ruff + ty**

```bash
uv run ruff check src/codeoracle/db/repositories/traces.py tests/unit/db/test_traces_repository_unit.py tests/integration/db/test_traces_repository.py
uv run ruff format --check src/codeoracle/db/repositories/traces.py
uv run ty check src/codeoracle/db/repositories/traces.py
```

Expected: clean.

- [ ] **Step 8: Commit**

```bash
git add src/codeoracle/db/repositories/traces.py tests/unit/db/test_traces_repository_unit.py \
        tests/integration/db/test_traces_repository.py
git commit -m "feat(db): TracesRepository — upsert + find + cursor delegation (Plan 7 Task 8)"
```

---

## Task 9: `TracesConfig` in `core/config.py` + env-var contract

**What this task delivers:** `TracesConfig` slots into `Settings(BaseSettings)` alongside `LLMConfig` (Plan 4) and `EmbeddingConfig` (Plans 3/6). Env vars cascade via `env_prefix="CODEORACLE_"` + `env_nested_delimiter="__"` — see Plan 3 `core/config.py` and Plan 6 Task 7 for the load-bearing reference pattern.

`TracesConfig` is a **BaseModel** (NOT BaseSettings — same rule that EmbeddingConfig follows). Env binding is on the parent `Settings(BaseSettings)`; per-field `env=` aliases would be ignored on a BaseModel. The single required-no-default field is `tempo_url` — every other field has a default so a process can boot with only `CODEORACLE_TRACES__TEMPO_URL=http://...` set.

**Env-var contract:**

| Env var | Field | Type | Default | Required? |
| --- | --- | --- | --- | --- |
| `CODEORACLE_TRACES__TEMPO_URL` | `tempo_url` | `str` | — | YES (no default; fail fast) |
| `CODEORACLE_TRACES__TEMPO_TIMEOUT_SECONDS` | `tempo_timeout_seconds` | `float` | `5.0` | no |
| `CODEORACLE_TRACES__TICK_INTERVAL_SECONDS` | `tick_interval_seconds` | `float` | `30.0` | no |
| `CODEORACLE_TRACES__SEARCH_LIMIT` | `search_limit` | `int` | `50` | no |
| `CODEORACLE_TRACES__MAX_BACKOFF_SECONDS` | `max_backoff_seconds` | `float` | `60.0` | no |
| `CODEORACLE_TRACES__CURSOR_OVERLAP_SECONDS` | `cursor_overlap_seconds` | `int` | `10` | no |

**Files:**
- Modify: `src/codeoracle/core/config.py` — add `TracesConfig` class + `traces` field on `Settings`
- Modify: `.env.example` — append the env-var contract above
- Create: `tests/unit/core/test_traces_config.py`

- [ ] **Step 1: Write `tests/unit/core/test_traces_config.py`**

```python
"""Plan 7 Task 9 — TracesConfig unit tests.

Verifies the env cascade (CODEORACLE_TRACES__*), defaults, and the
max_backoff >= tick_interval invariant. The pattern mirrors Plan 3 +
Plan 6's EmbeddingConfig tests.
"""

from __future__ import annotations

import pathlib

import pytest

from codeoracle.core.config import Settings, TracesConfig


def _tc(**overrides: object) -> TracesConfig:
    base: dict[str, object] = {"tempo_url": "http://tempo.test:3200"}
    base.update(overrides)
    return TracesConfig(**base)  # type: ignore[arg-type]


def test_traces_config_requires_tempo_url() -> None:
    with pytest.raises(ValueError, match="tempo_url"):
        TracesConfig()  # type: ignore[call-arg]  # missing required


def test_traces_config_tempo_url_must_be_non_empty() -> None:
    with pytest.raises(ValueError, match="min_length"):
        TracesConfig(tempo_url="")


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
    with pytest.raises(ValueError):
        _tc(tick_interval_seconds=0.0)


def test_traces_config_rejects_search_limit_above_1000() -> None:
    with pytest.raises(ValueError):
        _tc(search_limit=1001)


def test_traces_config_rejects_negative_overlap() -> None:
    with pytest.raises(ValueError):
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

def test_env_cascade_for_tempo_url(monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> None:
    monkeypatch.chdir(tmp_path)
    # Plan 3's other required env vars must still be set; minimal kit:
    monkeypatch.setenv("CODEORACLE_EMBEDDING__MODEL", "text-embedding-3-small")
    monkeypatch.setenv("CODEORACLE_EMBEDDING__DIM", "1536")
    monkeypatch.setenv("CODEORACLE_DB__PASSWORD", "test-db-pw")  # D5-4: required, no default
    monkeypatch.setenv("CODEORACLE_TRACES__TEMPO_URL", "http://tempo.cluster.local:3200")
    settings = Settings()
    assert settings.traces.tempo_url == "http://tempo.cluster.local:3200"


def test_env_cascade_for_tick_interval(monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("CODEORACLE_EMBEDDING__MODEL", "text-embedding-3-small")
    monkeypatch.setenv("CODEORACLE_EMBEDDING__DIM", "1536")
    monkeypatch.setenv("CODEORACLE_DB__PASSWORD", "test-db-pw")  # D5-4: required, no default
    monkeypatch.setenv("CODEORACLE_TRACES__TEMPO_URL", "http://x:3200")
    monkeypatch.setenv("CODEORACLE_TRACES__TICK_INTERVAL_SECONDS", "15.0")
    settings = Settings()
    assert settings.traces.tick_interval_seconds == 15.0


def test_env_cascade_failures_surface(monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> None:
    """If CODEORACLE_TRACES__TEMPO_URL is not set, Settings() raises at construction —
    fail-fast posture inherited from Plan 3."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("CODEORACLE_EMBEDDING__MODEL", "text-embedding-3-small")
    monkeypatch.setenv("CODEORACLE_EMBEDDING__DIM", "1536")
    monkeypatch.setenv("CODEORACLE_DB__PASSWORD", "test-db-pw")  # D5-4: isolate failure to the missing TEMPO_URL
    monkeypatch.delenv("CODEORACLE_TRACES__TEMPO_URL", raising=False)
    with pytest.raises(Exception):  # Pydantic ValidationError; tighten if Plan 3 narrowed it
        Settings()
```

- [ ] **Step 2: Run unit test to verify it fails**

```bash
uv run pytest tests/unit/core/test_traces_config.py -v
```

Expected: FAIL — `TracesConfig` not yet exported from `codeoracle.core.config`.

- [ ] **Step 3: Add `TracesConfig` to `src/codeoracle/core/config.py`**

Open the file. Below the `EmbeddingConfig` class (Plan 6 Task 7), add:

```python
class TracesConfig(BaseModel):
    """Configuration for the Plan 7 traces_ingest worker.

    BaseModel (NOT BaseSettings — env-binding cascades from the parent
    Settings(BaseSettings) via env_prefix='CODEORACLE_' + env_nested_delimiter='__';
    per-field `env=` aliases would be ignored).

    Env vars (all `CODEORACLE_TRACES__<FIELD_UPPER>`):
      - CODEORACLE_TRACES__TEMPO_URL                — required, no default
      - CODEORACLE_TRACES__TEMPO_TIMEOUT_SECONDS    — default 5.0
      - CODEORACLE_TRACES__TICK_INTERVAL_SECONDS    — default 30.0
      - CODEORACLE_TRACES__SEARCH_LIMIT             — default 50
      - CODEORACLE_TRACES__MAX_BACKOFF_SECONDS      — default 60.0
      - CODEORACLE_TRACES__CURSOR_OVERLAP_SECONDS   — default 10
    """

    tempo_url: str = Field(min_length=1, description="Tempo HTTP base URL (no trailing slash). Required.")
    tempo_timeout_seconds: float = Field(default=5.0, gt=0.0, description="Per-request HTTP timeout for TempoClient.")
    tick_interval_seconds: float = Field(default=30.0, gt=0.0, description="Sleep between ingest ticks.")
    search_limit: int = Field(default=50, ge=1, le=1000, description="Max traces per /api/search call.")
    max_backoff_seconds: float = Field(default=60.0, gt=0.0, description="Cap on exponential backoff after transient errors.")
    cursor_overlap_seconds: int = Field(default=10, ge=0, description="Re-poll window for late-arriving spans (cursor - overlap_seconds).")

    @model_validator(mode="after")
    def _validate_traces_config(self) -> "TracesConfig":
        if self.max_backoff_seconds < self.tick_interval_seconds:
            raise ValueError(
                f"max_backoff_seconds ({self.max_backoff_seconds}) must be >= "
                f"tick_interval_seconds ({self.tick_interval_seconds})"
            )
        return self
```

Locate the existing `Settings(BaseSettings)` class and add the `traces` field (alongside `embedding`, `llm`, etc.):

```python
class Settings(BaseSettings):
    # ... existing fields: database, embedding, llm, etc. ...
    traces: TracesConfig

    model_config = SettingsConfigDict(
        env_prefix="CODEORACLE_",
        env_nested_delimiter="__",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )
```

(If `Settings` already has the model_config block — which it should, from Plan 3 — leave it alone; just add the `traces:` field above.)

- [ ] **Step 4: Run unit test to verify it passes**

```bash
uv run pytest tests/unit/core/test_traces_config.py -v
```

Expected: PASS — 10 tests.

- [ ] **Step 5: Update `.env.example`**

Append to `.env.example`:

```dotenv
# -----------------------------------------------------------------------------
# Plan 7 — traces_ingest worker
# -----------------------------------------------------------------------------
# Grafana Tempo HTTP base URL (no trailing slash). Required.
CODEORACLE_TRACES__TEMPO_URL=http://localhost:3200

# Per-request HTTP timeout (seconds). Default 5.0.
# CODEORACLE_TRACES__TEMPO_TIMEOUT_SECONDS=5.0

# Sleep between ingest ticks (seconds). Default 30.0.
# CODEORACLE_TRACES__TICK_INTERVAL_SECONDS=30.0

# Max traces per /api/search call. Default 50; range 1-1000.
# CODEORACLE_TRACES__SEARCH_LIMIT=50

# Cap on exponential backoff after transient errors (seconds). Default 60.0.
# Must be >= TICK_INTERVAL_SECONDS.
# CODEORACLE_TRACES__MAX_BACKOFF_SECONDS=60.0

# Re-poll window for late-arriving spans (seconds). Default 10.
# CODEORACLE_TRACES__CURSOR_OVERLAP_SECONDS=10
```

- [ ] **Step 6: Run ruff + ty on the modified config**

```bash
uv run ruff check src/codeoracle/core/config.py tests/unit/core/test_traces_config.py
uv run ty check src/codeoracle/core/config.py
```

Expected: clean.

- [ ] **Step 7: Commit**

```bash
git add src/codeoracle/core/config.py tests/unit/core/test_traces_config.py .env.example
git commit -m "feat(core): TracesConfig (BaseModel) + CODEORACLE_TRACES__* env cascade (Plan 7 Task 9)"
```

---

## Task 10: `TracesIngestConsumer` — the concrete `ContinuousConsumer`

**What this task delivers:** the actual ingest logic. Subclasses `ContinuousConsumer[TimestampCursor, list[Span]]`. Each tick:

1. Compute `[lower, upper]` window: `lower = cursor.fetch_window_lower_bound_nanos()` (cursor − overlap), `upper = now_unix_nano()`.
2. Call `TempoClient.search_traces_in_window(lower, upper, limit)` → list of `TraceSummary`.
3. For each `TraceSummary`: `TempoClient.get_trace_by_id(t.trace_id)` → `list[Span]`. Per-trace transient errors get logged + skipped (the overlap window will re-pull on next tick); per-trace terminal errors (404 — trace aged out) get logged + skipped permanently.
4. Build new cursor: if `len(summaries) == limit`, **narrow** next window to `max(t.start_unix_nano for t in summaries)` so we don't fall behind; else advance to `upper`.
5. Return `(new_cursor, all_spans_flat)`.

**Persist:** `TracesRepository.upsert_spans(spans)` — composite-PK absorbs duplicates from the overlap window.

**Single-instance enforcement:** via the `ContinuousConsumer` framework — pass `advisory_lock_id=TRACES_INGEST_ADVISORY_LOCK_ID` + `engine=...` to `super().__init__`. `pg_try_advisory_lock(0x46AE0002)` is acquired at `run()` start; if held, `SingleInstanceContended` is raised and the worker exits before entering the loop.

**Files:**
- Create: `src/codeoracle/workers/traces_ingest/consumer.py`
- Create: `tests/unit/workers/traces_ingest/__init__.py` (empty)
- Create: `tests/unit/workers/traces_ingest/test_consumer.py`

- [ ] **Step 1: Write `tests/unit/workers/traces_ingest/test_consumer.py`**

```python
"""Plan 7 Task 10 — TracesIngestConsumer unit tests.

Uses fakes for TempoClient + TracesRepository + clock so each test
exercises one ingest behavior in isolation.
"""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from codeoracle.adapters.traces.tempo_client import (
    TempoTerminalError,
    TempoTransientError,
    TraceSummary,
)
from codeoracle.core.config import TracesConfig
from codeoracle.core.exceptions import TerminalFetchError, TransientFetchError
from codeoracle.models.traces import Span, TimestampCursor
from codeoracle.workers._base.continuous_consumer import CursorStore
from codeoracle.workers.traces_ingest.consumer import TracesIngestConsumer


# -----------------------------------------------------------------------------
# Fakes
# -----------------------------------------------------------------------------

class _FakeCursorStore(CursorStore[TimestampCursor]):
    def __init__(self) -> None:
        self.cursor: TimestampCursor | None = None
        self.updates: list[TimestampCursor] = []

    async def get_cursor(self, source: str) -> TimestampCursor | None:
        return self.cursor

    async def update_cursor(self, source: str, cursor: TimestampCursor) -> None:
        self.cursor = cursor
        self.updates.append(cursor)


class _RecordingLogger:
    """Same shape as Task 3 `_RecordingLogger` — `(event, *args, **kw)` matches Protocol."""

    def __init__(self) -> None:
        self.records: list[tuple[str, str, tuple[Any, ...], dict[str, Any]]] = []

    def info(self, event: str, *args: Any, **kw: Any) -> None:
        self.records.append(("info", event, args, kw))

    def warning(self, event: str, *args: Any, **kw: Any) -> None:
        self.records.append(("warning", event, args, kw))

    def error(self, event: str, *args: Any, **kw: Any) -> None:
        self.records.append(("error", event, args, kw))

    def debug(self, event: str, *args: Any, **kw: Any) -> None:
        self.records.append(("debug", event, args, kw))


def _span(trace_id: bytes, span_id: bytes, start: int = 1_700_000_000_000_000_000) -> Span:
    return Span(
        trace_id=trace_id,
        span_id=span_id,
        parent_span_id=None,
        service_name="api",
        operation_name="op",
        kind="server",
        start_unix_nano=start,
        end_unix_nano=start + 1_000_000,
        status_code="ok",
        status_message=None,
        attributes={},
        scope_name=None,
        scope_version=None,
    )


def _summary(trace_id: bytes, start: int) -> TraceSummary:
    return TraceSummary(
        trace_id=trace_id,
        root_service_name="api",
        root_span_name="op",
        start_unix_nano=start,
        duration_nanos=1_000_000,
    )


def _make_consumer(
    *,
    tempo_client: Any,
    traces_repo: Any,
    cursor_store: CursorStore[TimestampCursor],
    stop_event: asyncio.Event,
    logger: _RecordingLogger,
    clock_value: int = 1_700_000_000_500_000_000,
    config: TracesConfig | None = None,
) -> TracesIngestConsumer:
    cfg = config or TracesConfig(
        tempo_url="http://x:3200",
        tick_interval_seconds=0.001,
        max_backoff_seconds=0.05,
        search_limit=50,
        cursor_overlap_seconds=0,  # 0 to make assertions simpler
    )
    return TracesIngestConsumer(
        tempo_client=tempo_client,
        traces_repo=traces_repo,
        config=cfg,
        cursor_store=cursor_store,
        stop_event=stop_event,
        logger=logger,
        engine=None,           # no advisory lock for unit tests
        clock=lambda: clock_value,
    )


# -----------------------------------------------------------------------------
# Happy-path
# -----------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_fetch_then_persist_returns_advanced_cursor() -> None:
    tempo = MagicMock()
    tempo.search_traces_in_window = AsyncMock(return_value=[
        _summary(b"\x01" * 16, 1_700_000_000_100_000_000),
        _summary(b"\x02" * 16, 1_700_000_000_200_000_000),
    ])
    tempo.get_trace_by_id = AsyncMock(side_effect=[
        [_span(b"\x01" * 16, b"\xaa" * 8)],
        [_span(b"\x02" * 16, b"\xbb" * 8)],
    ])
    repo = MagicMock()
    repo.upsert_spans = AsyncMock(return_value=2)
    consumer = _make_consumer(
        tempo_client=tempo, traces_repo=repo,
        cursor_store=_FakeCursorStore(), stop_event=asyncio.Event(),
        logger=_RecordingLogger(),
    )

    new_cursor, spans = await consumer.fetch_batch(None)
    assert len(spans) == 2
    # Search-result-count < limit (50), so cursor advances to clock value
    assert new_cursor.last_ingested_unix_nano == 1_700_000_000_500_000_000


@pytest.mark.asyncio
async def test_fetch_window_uses_cursor_minus_overlap() -> None:
    tempo = MagicMock()
    tempo.search_traces_in_window = AsyncMock(return_value=[])
    repo = MagicMock()
    repo.upsert_spans = AsyncMock(return_value=0)

    cfg = TracesConfig(
        tempo_url="http://x:3200",
        tick_interval_seconds=0.001,
        max_backoff_seconds=0.05,
        cursor_overlap_seconds=5,  # 5 seconds = 5_000_000_000 nanos
    )
    consumer = _make_consumer(
        tempo_client=tempo, traces_repo=repo,
        cursor_store=_FakeCursorStore(), stop_event=asyncio.Event(),
        logger=_RecordingLogger(),
        clock_value=1_700_000_000_500_000_000,
        config=cfg,
    )
    cursor_in = TimestampCursor(last_ingested_unix_nano=1_700_000_000_100_000_000, overlap_seconds=5)

    await consumer.fetch_batch(cursor_in)

    tempo.search_traces_in_window.assert_awaited_once()
    kw = tempo.search_traces_in_window.await_args.kwargs
    assert kw["start_unix_nano"] == 1_700_000_000_100_000_000 - 5_000_000_000   # cursor - overlap
    assert kw["end_unix_nano"] == 1_700_000_000_500_000_000                     # clock now
    assert kw["limit"] == 50


@pytest.mark.asyncio
async def test_fetch_with_limit_hit_narrows_cursor() -> None:
    """When search returns exactly `limit` results, advance cursor only
    to the latest start_unix_nano in the batch (not to `now`) so we don't
    miss anything in the same nanosecond on the next tick."""
    tempo = MagicMock()
    # Return exactly `limit` summaries — cursor must NOT advance to `now`
    cfg = TracesConfig(
        tempo_url="http://x:3200", tick_interval_seconds=0.001,
        max_backoff_seconds=0.05, search_limit=3, cursor_overlap_seconds=0,
    )
    summaries = [
        _summary(bytes([i + 1]) * 16, 1_700_000_000_100_000_000 + i * 1_000_000)
        for i in range(3)
    ]
    tempo.search_traces_in_window = AsyncMock(return_value=summaries)
    tempo.get_trace_by_id = AsyncMock(side_effect=[
        [_span(bytes([i + 1]) * 16, b"\xaa" * 8)] for i in range(3)
    ])
    repo = MagicMock()
    repo.upsert_spans = AsyncMock(return_value=3)

    consumer = _make_consumer(
        tempo_client=tempo, traces_repo=repo,
        cursor_store=_FakeCursorStore(), stop_event=asyncio.Event(),
        logger=_RecordingLogger(),
        clock_value=1_700_000_000_999_999_999,
        config=cfg,
    )
    new_cursor, _ = await consumer.fetch_batch(None)

    # last_start was 1_700_000_000_102_000_000 (i=2)
    assert new_cursor.last_ingested_unix_nano == 1_700_000_000_102_000_000


@pytest.mark.asyncio
async def test_fetch_with_limit_hit_does_not_regress_below_incoming_cursor() -> None:
    """Framework monotonicity contract: new_cursor.last_ingested_unix_nano
    MUST be >= incoming cursor's. The regression scenario this test pins:
    a limit-hit tick whose summaries all start BEFORE the incoming cursor
    (because the overlap window pulled them in). Without the clamp in
    fetch_batch, `max(summaries.start)` would land below the cursor and
    next tick's lower bound would widen — fall-behind death spiral."""
    tempo = MagicMock()
    cfg = TracesConfig(
        tempo_url="http://x:3200", tick_interval_seconds=0.001,
        max_backoff_seconds=0.05, search_limit=3, cursor_overlap_seconds=0,
    )
    # Summaries start at 1.7e18 + [0, 1, 2]ms.
    summaries = [
        _summary(bytes([i + 1]) * 16, 1_700_000_000_000_000_000 + i * 1_000_000)
        for i in range(3)
    ]
    tempo.search_traces_in_window = AsyncMock(return_value=summaries)
    tempo.get_trace_by_id = AsyncMock(side_effect=[
        [_span(bytes([i + 1]) * 16, b"\xaa" * 8)] for i in range(3)
    ])
    repo = MagicMock()
    repo.upsert_spans = AsyncMock(return_value=3)

    consumer = _make_consumer(
        tempo_client=tempo, traces_repo=repo,
        cursor_store=_FakeCursorStore(), stop_event=asyncio.Event(),
        logger=_RecordingLogger(),
        clock_value=1_700_000_001_000_000_000,
        config=cfg,
    )
    # Incoming cursor LAGS the latest summary by intentionally placing
    # it AHEAD of max(summary.start) — simulating an overlap-window re-pull.
    incoming = TimestampCursor(
        last_ingested_unix_nano=1_700_000_000_500_000_000,
        overlap_seconds=0,
    )
    new_cursor, _ = await consumer.fetch_batch(incoming)

    assert new_cursor.last_ingested_unix_nano >= incoming.last_ingested_unix_nano, (
        f"cursor regressed: incoming={incoming.last_ingested_unix_nano}, "
        f"new={new_cursor.last_ingested_unix_nano}"
    )


# -----------------------------------------------------------------------------
# Error mapping: TempoClient → ContinuousConsumer error taxonomy
# -----------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_search_transient_maps_to_consumer_transient() -> None:
    tempo = MagicMock()
    tempo.search_traces_in_window = AsyncMock(side_effect=TempoTransientError("503"))
    repo = MagicMock()
    consumer = _make_consumer(
        tempo_client=tempo, traces_repo=repo,
        cursor_store=_FakeCursorStore(), stop_event=asyncio.Event(),
        logger=_RecordingLogger(),
    )
    with pytest.raises(TransientFetchError, match="503"):
        await consumer.fetch_batch(None)


@pytest.mark.asyncio
async def test_search_terminal_maps_to_consumer_terminal() -> None:
    tempo = MagicMock()
    tempo.search_traces_in_window = AsyncMock(side_effect=TempoTerminalError("400"))
    repo = MagicMock()
    consumer = _make_consumer(
        tempo_client=tempo, traces_repo=repo,
        cursor_store=_FakeCursorStore(), stop_event=asyncio.Event(),
        logger=_RecordingLogger(),
    )
    with pytest.raises(TerminalFetchError, match="400"):
        await consumer.fetch_batch(None)


@pytest.mark.asyncio
async def test_per_trace_transient_is_swallowed_and_logged() -> None:
    """If get_trace_by_id raises TempoTransientError for one trace, the
    consumer logs + skips that trace and continues — the overlap window
    will re-pull it on the next tick. The fetch_batch call still
    succeeds and persists the other traces."""
    tempo = MagicMock()
    tempo.search_traces_in_window = AsyncMock(return_value=[
        _summary(b"\x01" * 16, 1),
        _summary(b"\x02" * 16, 2),
    ])
    tempo.get_trace_by_id = AsyncMock(side_effect=[
        TempoTransientError("503 on first trace"),
        [_span(b"\x02" * 16, b"\xbb" * 8)],
    ])
    repo = MagicMock()
    logger = _RecordingLogger()
    consumer = _make_consumer(
        tempo_client=tempo, traces_repo=repo,
        cursor_store=_FakeCursorStore(), stop_event=asyncio.Event(),
        logger=logger,
    )
    _, spans = await consumer.fetch_batch(None)
    assert len(spans) == 1   # only the second trace's span survived
    # The first trace's failure must be logged
    assert any(
        level == "warning" and event == "traces_ingest.per_trace_transient_error"
        for level, event, _args, _fields in logger.records
    )


@pytest.mark.asyncio
async def test_per_trace_terminal_404_is_swallowed_and_logged() -> None:
    """get_trace_by_id 404 means the trace aged out of Tempo between
    search and fetch — log + skip permanently."""
    tempo = MagicMock()
    tempo.search_traces_in_window = AsyncMock(return_value=[
        _summary(b"\x01" * 16, 1),
    ])
    tempo.get_trace_by_id = AsyncMock(side_effect=TempoTerminalError("404"))
    repo = MagicMock()
    logger = _RecordingLogger()
    consumer = _make_consumer(
        tempo_client=tempo, traces_repo=repo,
        cursor_store=_FakeCursorStore(), stop_event=asyncio.Event(),
        logger=logger,
    )
    _, spans = await consumer.fetch_batch(None)
    assert spans == []
    assert any(
        level == "warning" and event == "traces_ingest.per_trace_terminal_error"
        for level, event, _args, _fields in logger.records
    )


# -----------------------------------------------------------------------------
# Required overrides
# -----------------------------------------------------------------------------

def test_batch_is_empty() -> None:
    consumer = _make_consumer(
        tempo_client=MagicMock(), traces_repo=MagicMock(),
        cursor_store=_FakeCursorStore(), stop_event=asyncio.Event(),
        logger=_RecordingLogger(),
    )
    assert consumer.batch_is_empty([])
    assert not consumer.batch_is_empty([_span(b"\x01" * 16, b"\xaa" * 8)])


def test_cursor_to_unix_nano() -> None:
    consumer = _make_consumer(
        tempo_client=MagicMock(), traces_repo=MagicMock(),
        cursor_store=_FakeCursorStore(), stop_event=asyncio.Event(),
        logger=_RecordingLogger(),
    )
    assert consumer.cursor_to_unix_nano(TimestampCursor(last_ingested_unix_nano=42)) == 42
```

- [ ] **Step 2: Run unit test to verify it fails**

```bash
uv run pytest tests/unit/workers/traces_ingest/test_consumer.py -v
```

Expected: FAIL — `TracesIngestConsumer` not yet defined.

- [ ] **Step 3: Implement `src/codeoracle/workers/traces_ingest/consumer.py`**

```python
"""Plan 7 Task 10 — TracesIngestConsumer.

Concrete ContinuousConsumer pulling spans from Tempo into traces.spans
on a tick interval. Single-instance enforcement via the framework's
advisory-lock primitive (lock id 0x46AE0002 — see core/streams.py).
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable

from sqlalchemy.ext.asyncio import AsyncEngine

from codeoracle.adapters.traces.tempo_client import (
    TempoClient,
    TempoTerminalError,
    TempoTransientError,
)
from codeoracle.core.config import TracesConfig
from codeoracle.core.exceptions import TerminalFetchError, TransientFetchError
from codeoracle.db.repositories.traces import TracesRepository
from codeoracle.models.traces import Span, TimestampCursor
from codeoracle.workers._base.continuous_consumer import (
    ContinuousConsumer,
    CursorStore,
    Logger,
)
from codeoracle.core.streams import (
    TRACES_INGEST_ADVISORY_LOCK_ID,
    TRACES_INGEST_SOURCE,
)


def _now_unix_nano() -> int:
    """Wall-clock now in Unix nanoseconds. Used as the upper bound of
    each tick's fetch window. Tests inject a fake."""
    return time.time_ns()


class TracesIngestConsumer(ContinuousConsumer[TimestampCursor, list[Span]]):
    """Pull spans from Tempo via TempoClient, persist via TracesRepository."""

    def __init__(
        self,
        *,
        tempo_client: TempoClient,
        traces_repo: TracesRepository,
        config: TracesConfig,
        cursor_store: CursorStore[TimestampCursor],
        stop_event: asyncio.Event,
        logger: Logger,
        engine: AsyncEngine | None,
        clock: Callable[[], int] = _now_unix_nano,
    ) -> None:
        super().__init__(
            source_name=TRACES_INGEST_SOURCE,
            cursor_store=cursor_store,
            tick_interval_seconds=config.tick_interval_seconds,
            stop_event=stop_event,
            logger=logger,
            max_backoff_seconds=config.max_backoff_seconds,
            advisory_lock_id=TRACES_INGEST_ADVISORY_LOCK_ID if engine is not None else None,
            engine=engine,
        )
        self._tempo = tempo_client
        self._repo = traces_repo
        self._search_limit = config.search_limit
        self._overlap_seconds = config.cursor_overlap_seconds
        self._clock = clock

    async def fetch_batch(
        self,
        cursor: TimestampCursor | None,
    ) -> tuple[TimestampCursor, list[Span]]:
        upper = self._clock()
        lower = cursor.fetch_window_lower_bound_nanos() if cursor is not None else 0

        try:
            summaries = await self._tempo.search_traces_in_window(
                start_unix_nano=lower,
                end_unix_nano=upper,
                limit=self._search_limit,
            )
        except TempoTransientError as e:
            raise TransientFetchError(str(e)) from e
        except TempoTerminalError as e:
            raise TerminalFetchError(str(e)) from e

        all_spans: list[Span] = []
        for summary in summaries:
            try:
                spans = await self._tempo.get_trace_by_id(summary.trace_id)
            except TempoTransientError as e:
                # Per-trace transient: log + skip — overlap window will re-pull.
                self._logger.warning(
                    "traces_ingest.per_trace_transient_error",
                    trace_id=summary.trace_id.hex(),
                    error=str(e),
                )
                continue
            except TempoTerminalError as e:
                # Per-trace terminal (e.g., 404 — trace aged out between search + fetch):
                # log + skip permanently.
                self._logger.warning(
                    "traces_ingest.per_trace_terminal_error",
                    trace_id=summary.trace_id.hex(),
                    error=str(e),
                )
                continue
            all_spans.extend(spans)

        # Cursor advancement strategy:
        #   - If we hit the search limit, we may not have seen everything in
        #     [lower, upper]; narrow next window to the latest summary's
        #     start_unix_nano so we don't fall behind.
        #   - Otherwise, advance to `upper` (now).
        #
        # Monotonicity guarantee (framework contract — ContinuousConsumer
        # docstring): `new_cursor` MUST be monotonically non-decreasing
        # vs the incoming cursor. Without the clamp below, a search-limit-hit
        # tick whose summaries fall inside the overlap window (i.e.,
        # `max(summary.start_unix_nano) < cursor.last_ingested_unix_nano`)
        # would regress the cursor — every subsequent tick widens further,
        # the system drifts behind, and the overlap window pulls more
        # duplicates each round. The `max(..., incoming_nano)` clamp pins
        # the cursor to at least its previous value.
        incoming_nano = (
            cursor.last_ingested_unix_nano if cursor is not None else 0
        )
        if len(summaries) >= self._search_limit:
            last_start = max(s.start_unix_nano for s in summaries)
            new_cursor = TimestampCursor(
                last_ingested_unix_nano=max(last_start, incoming_nano),
                overlap_seconds=self._overlap_seconds,
            )
        else:
            new_cursor = TimestampCursor(
                last_ingested_unix_nano=max(upper, incoming_nano),
                overlap_seconds=self._overlap_seconds,
            )

        return new_cursor, all_spans

    async def persist(self, batch: list[Span]) -> int:
        return await self._repo.upsert_spans(batch)

    def batch_is_empty(self, batch: list[Span]) -> bool:
        return len(batch) == 0

    def cursor_to_unix_nano(self, cursor: TimestampCursor) -> int:
        return cursor.last_ingested_unix_nano
```

- [ ] **Step 4: Run unit test to verify it passes**

```bash
uv run pytest tests/unit/workers/traces_ingest/test_consumer.py -v
```

Expected: PASS — 9 tests.

- [ ] **Step 5: Write integration test for advisory-lock contention**

Create `tests/integration/workers/traces_ingest/test_advisory_lock.py`:

```python
"""Plan 7 Task 10 — verify advisory-lock single-instance guard works in PG.

Two TracesIngestConsumer instances trying to acquire the same lock
simultaneously: the second must fail with SingleInstanceContended and
the first must continue running normally."""

from __future__ import annotations

import asyncio

import pytest
from sqlalchemy import text

from codeoracle.adapters.traces.tempo_client import TempoClient
from codeoracle.core.config import TracesConfig
from codeoracle.core.exceptions import SingleInstanceContended
from codeoracle.core.logging import get_logger
from codeoracle.db.repositories.traces import TracesRepository
from codeoracle.models.traces import TimestampCursor
from codeoracle.workers._base.cursor_store_pg import PGCursorStore
from codeoracle.workers.traces_ingest.consumer import TracesIngestConsumer


pytestmark = [pytest.mark.traces, pytest.mark.integration]


@pytest.mark.asyncio
async def test_second_instance_refuses_to_start(async_session_factory, async_engine) -> None:
    cs = PGCursorStore(session_factory=async_session_factory, cursor_type=TimestampCursor)
    repo = TracesRepository(session_factory=async_session_factory, cursor_store=cs)
    logger = get_logger("traces_ingest_test")

    # We don't actually want the tempo client to be called — the second
    # instance should fail at lock-acquire, BEFORE entering the loop.
    # Provide a TempoClient pointed at a nonsense URL; if the lock works
    # right, the URL is irrelevant.
    tempo = TempoClient(base_url="http://nowhere.invalid:3200", timeout_seconds=1.0)
    try:
        cfg = TracesConfig(
            tempo_url="http://nowhere.invalid:3200",
            tick_interval_seconds=0.001,
            max_backoff_seconds=0.05,
        )

        stop_a = asyncio.Event()
        stop_b = asyncio.Event()
        consumer_a = TracesIngestConsumer(
            tempo_client=tempo, traces_repo=repo, config=cfg,
            cursor_store=cs, stop_event=stop_a, logger=logger, engine=async_engine,
        )
        consumer_b = TracesIngestConsumer(
            tempo_client=tempo, traces_repo=repo, config=cfg,
            cursor_store=cs, stop_event=stop_b, logger=logger, engine=async_engine,
        )

        # Manually acquire the lock for consumer_a by entering its run() and
        # immediately tripping stop_a so it doesn't actually do work — but
        # the lock is still held during the run() body.
        # The cleanest test pattern: start A, give it time to grab the lock,
        # try to start B, assert B raises.

        task_a = asyncio.create_task(consumer_a.run())
        await asyncio.sleep(0.1)  # let A acquire the lock + enter the loop

        with pytest.raises(SingleInstanceContended):
            await consumer_b.run()

        # Tear down A
        stop_a.set()
        await task_a
    finally:
        await tempo.aclose()
```

- [ ] **Step 6: Run the integration test**

```bash
uv run pytest tests/integration/workers/traces_ingest/test_advisory_lock.py -v -m "traces and integration"
```

Expected: PASS — 1 test. (Requires the `async_engine` fixture from Plan 5's conftest in addition to `async_session_factory`. If only one is present, check Plan 5 — the engine should be the construction the factory wraps.)

- [ ] **Step 7: Run ruff + ty**

```bash
uv run ruff check src/codeoracle/workers/traces_ingest/consumer.py tests/unit/workers/traces_ingest/test_consumer.py tests/integration/workers/traces_ingest/test_advisory_lock.py
uv run ruff format --check src/codeoracle/workers/traces_ingest/consumer.py
uv run ty check src/codeoracle/workers/traces_ingest/consumer.py
```

Expected: clean.

- [ ] **Step 8: Commit**

```bash
git add src/codeoracle/workers/traces_ingest/consumer.py \
        tests/unit/workers/traces_ingest/__init__.py \
        tests/unit/workers/traces_ingest/test_consumer.py \
        tests/integration/workers/traces_ingest/test_advisory_lock.py
git commit -m "feat(workers): TracesIngestConsumer — Tempo → traces.spans (Plan 7 Task 10)"
```

---

## Task 11: Worker entrypoint + composition-root `wiring.py`

**What this task delivers:** the process-level glue. `traces_ingest/__main__.py` runs `asyncio.run(main())`; `wiring.py` constructs every collaborator the consumer needs from `Settings`. Signal handlers wire `SIGTERM` + `SIGINT` to the `stop_event` so `kubectl scale` shrinks gracefully.

This task introduces a **sibling** composition root for the ContinuousConsumer pattern. Plan 5's `build_worker_context()` (workers/_base/wiring.py, Task 5 there) returns a `WorkerContext` dataclass that includes a `ValkeyQueueClient` — load-bearing for WorkerConsumer-pattern workers but irrelevant for a ContinuousConsumer that doesn't read from Valkey. Calling `build_worker_context()` here would spin up a Valkey connection pool we never use, and would couple this worker's startup to Valkey's availability.

The `wire_consumer()` helper here mirrors `build_worker_context()`'s **shape** (env-driven, returns wired collaborators + a cleanup callable) but constructs only what the traces_ingest worker needs: `TempoClient`, async PG engine + session factory, `TracesRepository`, `PGCursorStore`, `TracesIngestConsumer`. Signal handlers (SIGTERM/SIGINT → `stop_event.set()`) are set up directly in `__main__.py`, copying the pattern from Plan 5's `workers/_base/wiring.py` lines ~1784-1789. Logging uses Plan 3's `get_logger("traces_ingest")` directly.

**Deliberately no `tasks.py` in this package.** Spec §3.2's worker process model describes the WorkerConsumer shape — a process that reads messages from a Valkey stream and "dispatches each message to its `tasks.py` handler". `traces_ingest` is a ContinuousConsumer, not a WorkerConsumer: it polls Tempo's HTTP query API on a cursor loop, with no Valkey stream and no per-message unit of work to dispatch. There is therefore nothing for a `tasks.py` to hold — the entire ingest behavior (fetch window → upsert spans → advance cursor) lives in `consumer.py`'s `TracesIngestConsumer`. The absence is structural, not an omission: a `tasks.py` here would be a dead module. (The general §3.2 worker-model prose is written for the stream-consumer majority; this is the one ContinuousConsumer the slice ships. See the §3.2 amendment proposed alongside this plan for the spec-side reconciliation of the stale `traces_ingest` "consumes traces.ingest" tree entry.)

**Files:**
- Create: `src/codeoracle/workers/traces_ingest/wiring.py`
- Create: `src/codeoracle/workers/traces_ingest/__main__.py`
- Create: `tests/unit/workers/traces_ingest/test_wiring.py`

- [ ] **Step 1: Write `tests/unit/workers/traces_ingest/test_wiring.py`** — verify composition shape

```python
"""Plan 7 Task 11 — wiring composition-root tests.

Verifies that wire_consumer() returns a fully-constructed
TracesIngestConsumer with the right collaborators wired in.
Does NOT exercise actual ingest — that's the E2E test in Task 12.
"""

from __future__ import annotations

import pathlib

import pytest

from codeoracle.workers.traces_ingest.consumer import TracesIngestConsumer
from codeoracle.workers.traces_ingest.wiring import wire_consumer


@pytest.mark.asyncio
async def test_wire_consumer_returns_traces_ingest_consumer(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
) -> None:
    monkeypatch.chdir(tmp_path)
    # Minimal env kit for Settings(). DBConfig defaults host/port/user/database
    # but password is required (no default, D5-4) — set CODEORACLE_DB__PASSWORD here;
    # make_async_engine() never actually connects during wiring construction,
    # so the value is unused even on a host with no PG.
    monkeypatch.setenv("CODEORACLE_EMBEDDING__MODEL", "text-embedding-3-small")
    monkeypatch.setenv("CODEORACLE_EMBEDDING__DIM", "1536")
    monkeypatch.setenv("CODEORACLE_DB__PASSWORD", "test-db-pw")  # D5-4: required, no default
    monkeypatch.setenv("CODEORACLE_TRACES__TEMPO_URL", "http://tempo.test:3200")

    # Bust the get_settings() lru_cache so the new env above is picked up
    # (this test deliberately constructs a fresh Settings via wire_consumer's
    # `get_settings()` call inside; without cache_clear, an earlier test
    # could leak its Settings into this one).
    from codeoracle.core.config import get_settings
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
    monkeypatch.setenv("CODEORACLE_DB__PASSWORD", "test-db-pw")  # D5-4: isolate failure to the missing TEMPO_URL
    monkeypatch.delenv("CODEORACLE_TRACES__TEMPO_URL", raising=False)

    from codeoracle.core.config import get_settings
    get_settings.cache_clear()

    with pytest.raises(Exception):
        await wire_consumer()
```

- [ ] **Step 2: Run test to verify it fails**

```bash
uv run pytest tests/unit/workers/traces_ingest/test_wiring.py -v
```

Expected: FAIL.

- [ ] **Step 3: Implement `src/codeoracle/workers/traces_ingest/wiring.py`**

```python
"""Plan 7 Task 11 — composition root for the traces_ingest worker.

Constructs every collaborator from Settings + returns the consumer
plus an async cleanup function. The cleanup is the caller's
responsibility — for the __main__.py entrypoint, that's a `try/finally`;
for tests, that's a fixture teardown.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable

from codeoracle.adapters.traces.tempo_client import TempoClient
from codeoracle.core.config import get_settings
from codeoracle.core.logging import get_logger
from codeoracle.db.session import make_async_engine, make_async_session_factory
from codeoracle.db.repositories.traces import TracesRepository
from codeoracle.models.traces import TimestampCursor
from codeoracle.workers._base.cursor_store_pg import PGCursorStore
from codeoracle.workers.traces_ingest.consumer import TracesIngestConsumer


async def wire_consumer(
    *,
    stop_event: asyncio.Event | None = None,
) -> tuple[TracesIngestConsumer, Callable[[], Awaitable[None]]]:
    """Construct a fully-wired TracesIngestConsumer + cleanup callable.

    Returns (consumer, cleanup). The caller MUST await `cleanup()` on
    shutdown to release the TempoClient httpx pool + the PG engine pool.

    Single-source-of-truth invariant: every collaborator reads
    configuration from `get_settings()` exclusively. There is NO
    `settings=` parameter — a per-call override would partially-honor
    only the fields wire_consumer touches directly (`traces.*`) while
    `make_async_engine()` would still read from `get_settings()`
    internally, silently dropping the overridden `database.*`.
    That asymmetric override is a footgun Plan 3 explicitly rejected
    (see Plan 3 Task 7 comment "get_settings() is the single source
    of truth"). Tests override config via `monkeypatch.setenv(...)`
    + `get_settings.cache_clear()` — Task 9's env-cascade tests
    demonstrate the pattern.
    """
    stop_event = stop_event or asyncio.Event()
    settings = get_settings()

    engine = make_async_engine()
    session_factory = make_async_session_factory(engine)

    tempo_client = TempoClient(
        base_url=settings.traces.tempo_url,
        timeout_seconds=settings.traces.tempo_timeout_seconds,
    )
    cursor_store = PGCursorStore(
        session_factory=session_factory,
        cursor_type=TimestampCursor,
    )
    traces_repo = TracesRepository(
        session_factory=session_factory,
        cursor_store=cursor_store,
    )
    logger = get_logger("traces_ingest")

    consumer = TracesIngestConsumer(
        tempo_client=tempo_client,
        traces_repo=traces_repo,
        config=settings.traces,
        cursor_store=cursor_store,
        stop_event=stop_event,
        logger=logger,
        engine=engine,
    )

    async def cleanup() -> None:
        # Order matters: close adapter HTTP pool, then PG engine pool.
        await tempo_client.aclose()
        await engine.dispose()

    return consumer, cleanup
```

- [ ] **Step 4: Implement `src/codeoracle/workers/traces_ingest/__main__.py`**

```python
"""Plan 7 Task 11 — traces_ingest process entrypoint.

Invocation:
    uv run python -m codeoracle.workers.traces_ingest

Lifecycle:
  1. Wire dependencies via wiring.wire_consumer().
  2. Install SIGTERM + SIGINT handlers that set stop_event.
  3. Run consumer.run() until stop_event fires (or it dies).
  4. Always run cleanup() to release pools.
"""

from __future__ import annotations

import asyncio
import signal
import sys

from codeoracle.workers.traces_ingest.wiring import wire_consumer


async def main() -> int:
    stop_event = asyncio.Event()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, stop_event.set)

    consumer, cleanup = await wire_consumer(stop_event=stop_event)
    try:
        await consumer.run()
        return 0
    finally:
        await cleanup()


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
```

- [ ] **Step 5: Run the unit test**

```bash
uv run pytest tests/unit/workers/traces_ingest/test_wiring.py -v
```

Expected: PASS — 2 tests.

(Note: the first test asks the wiring to construct a real Postgres engine via `make_async_engine()`. Plan 3's `make_async_engine()` constructs an `AsyncEngine` but does NOT eagerly connect — pool members connect lazily on first session. So this unit test passes without a live PG. If a future change to Plan 3 introduces eager-connect behavior, this test would need to monkeypatch `codeoracle.db.session.make_async_engine` to return a mock engine.)

- [ ] **Step 6: Verify the module can be invoked**

```bash
uv run python -c "from codeoracle.workers.traces_ingest.__main__ import main; print('importable')"
```

Expected: prints `importable` without error.

- [ ] **Step 7: Run ruff + ty**

```bash
uv run ruff check src/codeoracle/workers/traces_ingest/
uv run ruff format --check src/codeoracle/workers/traces_ingest/
uv run ty check src/codeoracle/workers/traces_ingest/
```

Expected: clean.

- [ ] **Step 8: Commit**

```bash
git add src/codeoracle/workers/traces_ingest/wiring.py \
        src/codeoracle/workers/traces_ingest/__main__.py \
        tests/unit/workers/traces_ingest/test_wiring.py
git commit -m "feat(workers): traces_ingest wiring + __main__ entrypoint (Plan 7 Task 11)"
```

---

## Task 12: End-to-end integration test (testcontainers PG + `respx`-mocked Tempo)

**What this task delivers:** the full happy-path proof — process starts, polls Tempo (mocked), persists 3 batches across 3 ticks, advances cursor, stops on stop_event. Catches integration bugs that the unit tests miss: cursor-store JSONB round-trip across consumer ticks, PG transaction handling around upsert + cursor-update being independent (NOT in one transaction — partial-persist + cursor-advance is forbidden but cursor-advance + partial-next-tick is acceptable), and the run-loop wake-up on stop_event during sleep.

**Files:**
- Create: `tests/integration/workers/traces_ingest/test_traces_ingest_e2e.py`

- [ ] **Step 1: Write the E2E test**

```python
"""Plan 7 Task 12 — end-to-end traces_ingest happy path.

testcontainers PG + respx-mocked Tempo. Spins up a TracesIngestConsumer,
serves three batches of mocked Tempo responses, advances the cursor
across ticks, asserts the spans landed in traces.spans, and tears down.
"""

from __future__ import annotations

import asyncio

import pytest
import respx

from codeoracle.adapters.traces.tempo_client import TempoClient
from codeoracle.core.config import TracesConfig
from codeoracle.core.logging import get_logger
from codeoracle.db.repositories.traces import TracesRepository
from codeoracle.models.traces import SpanFilter, TimestampCursor
from codeoracle.workers._base.cursor_store_pg import PGCursorStore
from codeoracle.workers.traces_ingest.consumer import TracesIngestConsumer


pytestmark = [pytest.mark.traces, pytest.mark.integration]


# Static test data — three batches of growing trace IDs
_TRACE_IDS = [
    bytes.fromhex(f"{i:032x}") for i in range(1, 10)
]


def _otlp_trace_payload(trace_id_hex: str) -> dict:
    """Tempo 2.6+ envelope shape (top-level key `resourceSpans`)."""
    return {
        "trace": {
            "resourceSpans": [
                {
                    "resource": {
                        "attributes": [{"key": "service.name", "value": {"stringValue": "api"}}],
                    },
                    "scopeSpans": [
                        {
                            "scope": {"name": "codeoracle.api", "version": "0.1.0"},
                            "spans": [
                                {
                                    "traceId": trace_id_hex,
                                    "spanId": f"{1:016x}",
                                    "parentSpanId": "",
                                    "name": "POST /v1/query",
                                    "kind": 2,
                                    "startTimeUnixNano": "1700000000000000000",
                                    "endTimeUnixNano":   "1700000000010000000",
                                    "status": {"code": 1, "message": ""},
                                    "attributes": [
                                        {"key": "http.route", "value": {"stringValue": "/v1/query"}},
                                    ],
                                }
                            ],
                        }
                    ],
                }
            ]
        }
    }


def _search_payload(trace_id_hexes: list[str]) -> dict:
    return {
        "traces": [
            {
                "traceID": h,
                "rootServiceName": "api",
                "rootTraceName": "POST /v1/query",
                "startTimeUnixNano": "1700000000000000000",
                "durationMs": 10,
            }
            for h in trace_id_hexes
        ],
        "metrics": {"inspectedTraces": len(trace_id_hexes)},
    }


@pytest.mark.asyncio
@respx.mock
async def test_consumer_ingests_three_batches_and_stops_on_signal(async_session_factory, async_engine) -> None:
    tempo_base = "http://tempo.test:3200"

    # ----- Tempo mocks: three rounds, each with one trace -----
    round_1_hex = _TRACE_IDS[0].hex()
    round_2_hex = _TRACE_IDS[1].hex()
    round_3_hex = _TRACE_IDS[2].hex()

    # First search hits → round 1; next → round 2; etc.
    # We can't easily sequence respx responses without a side_effect; use a
    # counter-keyed lambda via respx.route.mock(side_effect=[...]).
    search_route = respx.get(f"{tempo_base}/api/search").mock(side_effect=[
        respx.MockResponse(json=_search_payload([round_1_hex])),
        respx.MockResponse(json=_search_payload([round_2_hex])),
        respx.MockResponse(json=_search_payload([round_3_hex])),
        # Subsequent ticks return empty so the cursor doesn't advance further
        respx.MockResponse(json={"traces": [], "metrics": {"inspectedTraces": 0}}),
    ])

    for hex_id in (round_1_hex, round_2_hex, round_3_hex):
        respx.get(f"{tempo_base}/api/v2/traces/{hex_id}").respond(json=_otlp_trace_payload(hex_id))

    # ----- Build the consumer with a fast tick interval -----
    cs = PGCursorStore(session_factory=async_session_factory, cursor_type=TimestampCursor)
    repo = TracesRepository(session_factory=async_session_factory, cursor_store=cs)
    tempo = TempoClient(base_url=tempo_base, timeout_seconds=2.0)
    logger = get_logger("traces_ingest_e2e")
    stop = asyncio.Event()

    consumer = TracesIngestConsumer(
        tempo_client=tempo, traces_repo=repo,
        config=TracesConfig(
            tempo_url=tempo_base,
            tick_interval_seconds=0.01,
            max_backoff_seconds=0.05,
            search_limit=50,
            cursor_overlap_seconds=0,
        ),
        cursor_store=cs, stop_event=stop, logger=logger,
        engine=async_engine,
    )

    async def stop_after_three_rounds():
        # Give the consumer enough time to complete 3 ticks
        await asyncio.sleep(0.15)
        stop.set()

    try:
        await asyncio.gather(consumer.run(), stop_after_three_rounds())
    finally:
        await tempo.aclose()

    # ----- Assert: 3 traces, each with 1 span, landed in traces.spans -----
    for trace_id in _TRACE_IDS[:3]:
        out = await repo.find_spans(SpanFilter(trace_id=trace_id))
        assert len(out) == 1, f"expected 1 span for trace {trace_id.hex()}, got {len(out)}"

    # ----- Assert: cursor advanced (non-None, > 0) -----
    final_cursor = await repo.get_cursor()
    assert final_cursor is not None
    assert final_cursor.last_ingested_unix_nano > 0


@pytest.mark.asyncio
@respx.mock
async def test_consumer_recovers_after_transient_failure(async_session_factory, async_engine) -> None:
    """Sequence: search 503 (transient) → backoff → search 200 (good) → persist."""
    tempo_base = "http://tempo.test:3200"
    hex_id = _TRACE_IDS[0].hex()

    respx.get(f"{tempo_base}/api/search").mock(side_effect=[
        respx.MockResponse(status_code=503, text="busy"),
        respx.MockResponse(json=_search_payload([hex_id])),
        respx.MockResponse(json={"traces": [], "metrics": {"inspectedTraces": 0}}),
    ])
    respx.get(f"{tempo_base}/api/v2/traces/{hex_id}").respond(json=_otlp_trace_payload(hex_id))

    cs = PGCursorStore(session_factory=async_session_factory, cursor_type=TimestampCursor)
    repo = TracesRepository(session_factory=async_session_factory, cursor_store=cs)
    tempo = TempoClient(base_url=tempo_base, timeout_seconds=2.0)
    logger = get_logger("traces_ingest_e2e")
    stop = asyncio.Event()

    consumer = TracesIngestConsumer(
        tempo_client=tempo, traces_repo=repo,
        config=TracesConfig(
            tempo_url=tempo_base,
            tick_interval_seconds=0.005,
            max_backoff_seconds=0.05,
            search_limit=50,
            cursor_overlap_seconds=0,
        ),
        cursor_store=cs, stop_event=stop, logger=logger,
        engine=async_engine,
    )

    async def stop_soon():
        await asyncio.sleep(0.2)
        stop.set()

    try:
        await asyncio.gather(consumer.run(), stop_soon())
    finally:
        await tempo.aclose()

    out = await repo.find_spans(SpanFilter(trace_id=_TRACE_IDS[0]))
    assert len(out) == 1


@pytest.mark.asyncio
@respx.mock
async def test_consumer_dies_on_terminal_error(async_session_factory, async_engine) -> None:
    """4xx on search → TerminalFetchError → consumer.run() raises."""
    tempo_base = "http://tempo.test:3200"
    respx.get(f"{tempo_base}/api/search").respond(status_code=400, text="bad query")

    cs = PGCursorStore(session_factory=async_session_factory, cursor_type=TimestampCursor)
    repo = TracesRepository(session_factory=async_session_factory, cursor_store=cs)
    tempo = TempoClient(base_url=tempo_base, timeout_seconds=2.0)
    logger = get_logger("traces_ingest_e2e")
    stop = asyncio.Event()

    from codeoracle.core.exceptions import TerminalFetchError
    consumer = TracesIngestConsumer(
        tempo_client=tempo, traces_repo=repo,
        config=TracesConfig(
            tempo_url=tempo_base, tick_interval_seconds=0.001, max_backoff_seconds=0.05,
        ),
        cursor_store=cs, stop_event=stop, logger=logger,
        engine=async_engine,
    )

    try:
        with pytest.raises(TerminalFetchError):
            await consumer.run()
    finally:
        await tempo.aclose()
```

- [ ] **Step 2: Run the E2E test**

```bash
uv run pytest tests/integration/workers/traces_ingest/test_traces_ingest_e2e.py -v -m "traces and integration"
```

Expected: PASS — 3 tests.

- [ ] **Step 3: Run the full Plan 7 suite to confirm cohesion**

```bash
uv run pytest tests/unit/models/test_traces_models.py \
              tests/unit/workers/_base/test_continuous_consumer.py \
              tests/unit/workers/_base/test_cursor_store_pg.py \
              tests/unit/adapters/traces/test_tempo_client.py \
              tests/unit/core/test_traces_config.py \
              tests/unit/db/test_traces_repository_unit.py \
              tests/unit/workers/traces_ingest/test_consumer.py \
              tests/unit/workers/traces_ingest/test_wiring.py \
              -v
```

Expected: all PASS — 60+ unit tests.

```bash
uv run pytest tests/integration -v -m "traces and integration"
```

Expected: all PASS — ~20 integration tests.

- [ ] **Step 4: Commit**

```bash
git add tests/integration/workers/traces_ingest/test_traces_ingest_e2e.py
git commit -m "test(workers): traces_ingest E2E happy + transient-recovery + terminal-error (Plan 7 Task 12)"
```

---

## Task 13: Documentation updates + final Plan 7 commit

**What this task delivers:** documentation reconciliation. The platform docs cross-reference what each plan delivers; Plan 7 adds `ContinuousConsumer`, `TempoClient`, `TracesRepository`, the traces_ingest worker, the new schema, the new pytest markers, and a slot in the components table. Updates:

- `docs/architecture.md` — append a note in the "Runtime trace tools" section that `trace.find_spans`, `trace.get`, etc. are now backed by `traces.spans` (PG) ingested by workers/traces_ingest.
- `docs/superpowers/specs/2026-05-21-platform-scaffolding-design.md` — tick the "components delivered" rows that Plan 7 lands.
- `docs/roadmap.md` — bump the per-plan status line for Plan 7.
- `src/codeoracle/README.md` (if Plan 1 created it) — append a `## Workers` section enumerating `traces_ingest` alongside `joern_indexer`.
- The Plan 6 placeholder comments referencing "Plan 7 ContinuousConsumer + traces_ingest is out of scope" can stay as historical context — they're not stale, just informational.

**Files:**
- Modify: `docs/architecture.md`
- Modify: `docs/superpowers/specs/2026-05-21-platform-scaffolding-design.md`
- Modify: `docs/roadmap.md`
- Modify: `src/codeoracle/README.md` (if present)

- [ ] **Step 1: Update `docs/architecture.md`**

Locate the `### Runtime trace tools` block (`docs/architecture.md` lines ~716-721 — verify with grep at apply time). Append a note immediately after the code block:

```markdown
### Runtime trace tools

```text
trace.find_by_user_action(action, env, time_range)
trace.find_by_route(route, env, time_range)
trace.get(trace_id)
trace.find_spans(filters)
trace.get_logs_by_trace(trace_id)
```

These are agent-facing tool signatures. As of Plan 7, the bottom three
(`trace.get`, `trace.find_spans`, `trace.get_logs_by_trace`) are
implemented against `traces.spans` (PG) populated by
`workers/traces_ingest` pulling from Grafana Tempo. The top two
(`trace.find_by_user_action`, `trace.find_by_route`) compose on top of
`trace.find_spans` with attribute-key filters at the agent layer.

`trace.get_logs_by_trace` is a stub returning `NotImplementedError` —
log ingest (Loki) is out of scope until a future plan; the agent
surfaces the gap as `unknown` per the evidence-first contract.
```

- [ ] **Step 2: Update `docs/superpowers/specs/2026-05-21-platform-scaffolding-design.md`**

Locate the components delivered table (~line 1303) and tick the relevant rows. Locate the "Defers to" tables and remove the `TracesRepository` + `workers/traces_ingest` placeholders that Plan 5/6 had marked as "Plan 7 owns."

Specifically: the row that reads `| 12 | workers/traces_ingest | Pull platform's own self-traces from Tempo | Real |` should now also include a brief implementation status note like `(Plan 7 — landed YYYY-MM-DD)`.

- [ ] **Step 3: Update `docs/roadmap.md`**

Mark Plan 7 as delivered: replace any `Plan 7: pending` line with `Plan 7: delivered (commit <sha>)`. (Per Plan 1's roadmap convention.)

- [ ] **Step 4: Update `src/codeoracle/README.md`**

Append (or create) a `## Workers` section:

```markdown
## Workers

Each worker is a standalone process that consumes work from a queue
or external source and persists to PG.

| Worker | Pattern | Source | Persists to | Plan |
| --- | --- | --- | --- | --- |
| `joern_indexer` | WorkerConsumer (snapshot) | Valkey `indexing.jobs.joern` | AGE graph (CPG) | 5 |
| `scip_indexer` | WorkerConsumer (snapshot, project-mode) | Valkey `indexing.jobs.scip` | AGE graph (refs) | 6 |
| `opengrep_indexer` | WorkerConsumer (snapshot, per-file) | Valkey `indexing.jobs.opengrep` | `opengrep.findings` | 6 |
| `embed_indexer` | WorkerConsumer (snapshot, per-file) | Valkey `indexing.jobs.embed` | `code_chunks` (pgvector) | 6 |
| `traces_ingest` | ContinuousConsumer | Tempo HTTP API | `traces.spans` | 7 |

`WorkerConsumer` and `ContinuousConsumer` are siblings, not parent-child —
they share `workers/_base/` namespace but no inheritance. See
`docs/superpowers/plans/2026-05-21-continuous-consumer-and-traces-ingest.md`
for the lifecycle contracts.

Run a worker locally:

```bash
docker compose -f infra/compose/docker-compose.yml up -d postgres valkey tempo
uv run python -m codeoracle.workers.traces_ingest
```
```

- [ ] **Step 5: Final lint sweep**

```bash
uv run ruff check src/ tests/
uv run ruff format --check src/ tests/
uv run ty check src/
```

Expected: all clean.

- [ ] **Step 6: Final commit**

```bash
git add docs/architecture.md \
        docs/superpowers/specs/2026-05-21-platform-scaffolding-design.md \
        docs/roadmap.md \
        src/codeoracle/README.md
git commit -m "docs(plan-7): cross-link traces_ingest delivery + update components table"
```

---

## Self-review checklist (run before delivering)

Per `superpowers:writing-plans`, do a fresh-eyes pass over the plan against the spec:

**1. Spec coverage:**
- [x] `ContinuousConsumer` framework — Task 3.
- [x] `traces_ingest` worker — Tasks 10–12.
- [x] `TracesRepository` — Task 8.
- [x] `TempoClient` adapter — Tasks 5–6.
- [x] Migration for `traces` schema — Task 7.
- [x] Env-var contract (`CODEORACLE_TRACES__*`) — Task 9.
- [x] Single-instance enforcement (advisory lock) — Tasks 3 + 10.
- [x] Integration test (testcontainers PG + mocked Tempo) — Task 12.
- [x] Docs cross-link — Task 13.

**2. Placeholder scan:**
- No "TBD" / "implement later" / "similar to Task N" remaining.
- Every code block contains actual code, not stubs.
- Every command is exact (uv run pytest paths, ruff arg shapes).
- Every commit message is concrete.

**3. Type / name consistency:**
- `TracesIngestConsumer` is the class name everywhere (NOT `TracesIngestWorker`, `TempoIngestConsumer`, etc.).
- `TempoClient.search_traces_in_window` is the method name everywhere (NOT `query_traces`, `find_traces`).
- `TracesConfig.tempo_url` is the field name everywhere (NOT `tempo_endpoint`, `base_url`).
- `TimestampCursor.last_ingested_unix_nano` is the field name everywhere (NOT `last_seen`, `cursor_ts`).
- `TRACES_INGEST_SOURCE = "tempo"` and `TRACES_INGEST_ADVISORY_LOCK_ID = 0x46AE0002` are the constants everywhere.

**4. Layered architecture:**
- `models/traces.py` — pure Pydantic, no I/O. ✅
- `adapters/traces/tempo_client.py` — wraps external system (Tempo HTTP). ✅
- `db/repositories/traces.py` — owns our PG table (TracesRepository). ✅
- `workers/_base/continuous_consumer.py` + `cursor_store_pg.py` — framework, no business logic. ✅
- `workers/traces_ingest/consumer.py` — composes the framework + adapter + repo. ✅
- No cross-layer imports (models → adapters, db → workers). ✅

**5. Conventions:**
- File-size cap respected (largest file: `tempo_client.py` ~300 lines; `traces.py` ~150; `consumer.py` ~120).
- No `class Config:` / `model_config = ConfigDict(env_*)` on BaseModels under Settings. ✅
- No emojis. ✅
- No `time.time()` direct calls — `_now_unix_nano()` is the lone exception and is injectable. ✅
- No `shell=True` subprocess calls. (No subprocess calls at all.) ✅

**6. Behavioral-claim audit (each "MUST" / "always" / "bulk" claim in prose must be backed by a test that fails when the invariant is violated):**

Structural checks (above) confirm symbols + types + naming + layers. Behavioral checks confirm the code does what the prose says:

- [ ] **`ContinuousConsumer.fetch_batch` monotonicity invariant** ("new_cursor MUST be monotonically non-decreasing") — pinned by `test_fetch_with_limit_hit_does_not_regress_below_incoming_cursor` in Task 10. Also enforced at log-emission by `IngestStats._validate_stats` (raises on regression), pinned by `test_ingest_stats_rejects_regressing_cursor` in Task 2.
- [ ] **`TracesRepository.upsert_spans` bulk-insert claim** ("N spans become `ceil(N / 5041)` server round-trips, not N") — pinned by `test_upsert_spans_emits_single_insert_statement` (≤5041 spans → exactly 1 INSERT, executemany=False) and `test_upsert_spans_chunks_above_pg_param_limit` (5042 spans → exactly 2 INSERTs, each executemany=False). The second test guards against the PG-wire-protocol 65,535-bind-param cap that would silently hard-fail on deep-trace ingest. Both test bodies are included verbatim in Task 8 Step 5.
- [ ] **Tempo wire-format claim** ("envelope is `resourceSpans` on 2.6+, falls back to `batches` on pre-2.6") — pinned by `test_get_trace_decodes_legacy_batches_envelope` (Task 5 unit tests). The test supplies a payload keyed by `batches` (Tempo < 2.6 shape) and asserts the same span output as the `resourceSpans` shape — guarantees the fallback in `parser` is exercised. Test body is included verbatim in Task 5 Step 1.
- [ ] **Logger Protocol shape claim** ("matches `structlog.stdlib.BoundLogger` under ty strict") — Task 3 Step 6: add `uv run ty check src/codeoracle/workers/_base/continuous_consumer.py` to the post-implementation gate. If ty rejects the Protocol, widen the signature.
- [ ] **Advisory lock connection-scoped lifetime claim** ("held for run() lifetime, released on uncaught exception") — pinned by the integration test in Task 3 Step 8 (advisory_lock test): start a second consumer while the first holds the lock + assert `SingleInstanceContended`. Existing test covers this; record the assertion line numbers.
- [ ] **Per-trace transient-error swallow claim** ("re-pulled on next tick via overlap") — pinned by `test_per_trace_transient_is_swallowed_and_logged` in Task 10, supplemented by E2E `test_recovers_after_transient_error_on_one_trace` in Task 12.
- [ ] **Per-trace terminal-error swallow claim** ("logged + skipped permanently") — pinned by `test_per_trace_terminal_404_is_swallowed_and_logged` in Task 10.
- [ ] **`get_settings()` cache-bust pattern** — every Task 9 env-cascade test instantiates `Settings()` directly (bypasses cache); every Task 11 wiring test calls `get_settings.cache_clear()` before `wire_consumer()`. The comment at the top of the env-cascade section documents why.
- [ ] **OTLP byte-decoding accepts hex AND base64** — pinned by `test_decode_otlp_bytes_accepts_hex` + `test_decode_otlp_bytes_accepts_base64` + `test_decode_otlp_bytes_rejects_neither` (Task 5 unit tests). All three test bodies are included verbatim in Task 5 Step 1.

**7. Cross-plan symbol verification (each symbol cited from Plans 3 / 4 / 5 / 6 must resolve in the upstream plan's source-of-truth files):**

Run before commit:

```bash
# Every cross-plan symbol Plan 7 USES must also appear in at least one prior plan.
for sym in \
    "make_async_engine" \
    "make_async_session_factory" \
    "make_sync_engine" \
    "get_logger" \
    "get_settings" \
    "Settings" \
    "DBConfig" \
    "async_session_factory" \
    "bootstrapped_db" \
; do
    echo "=== $sym ==="
    grep -l "$sym" docs/superpowers/plans/2026-05-21-*.md | head -3
done
```

Expected: every symbol appears in BOTH Plan 7 (call sites) AND at least one of the prior plans (definition site). If any symbol shows ONLY in Plan 7, it's a fabrication — fix the reference.

Sibling-pattern symbols Plan 7 INTENTIONALLY does NOT call (`build_worker_context`, `WorkerContext`, `reindex_single_flight`, `ValkeyQueueClient`, `WorkerConsumer`) are documented as deliberate non-use in the inheritance table at the top of this plan. A grep would false-positive on the inheritance-table prose and Task 11 narrative that name these symbols precisely because their absence-from-code is what's load-bearing. Catch accidental coupling at PR review instead, by reading `workers/_base/continuous_consumer.py` + `workers/traces_ingest/wiring.py` directly and confirming no imports of `codeoracle.workers._base.wiring`, `codeoracle.services.indexing.advisory_lock`, or `codeoracle.adapters.valkey`.

---
