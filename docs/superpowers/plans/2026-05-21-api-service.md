# Plan 11 — API Service Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the user-facing HTTP API for the CodeOracle platform — a FastAPI process exposing `POST /v1/query` (delegates retrieval to the agent process over HTTP), `POST /v1/reindex` + `GET /v1/reindex/{snapshot_sha}` (async-job pattern amended 2026-05-26: POST enqueues via `IndexingService.enqueue_reindex` returning 202 + PENDING `Snapshot`; GET polls the snapshot lifecycle via `IndexingService.get_snapshot`; the `ReindexOrchestratorConsumer` worker — Plan 5 amendment `40885bf` — holds the advisory lock out-of-band), `GET /v1/health` (db-ping liveness), and `GET /metrics` (Prometheus scrape) — plus the `AgentClient` httpx wrapper the API uses to call the agent process.

**Architecture:** The API process is a thin HTTP entrypoint that wires existing services together (spec §3.3 layer purity rule 6: processes are thin entrypoints; no business logic). It composes `IndexingService` directly (matching Plan 5's existing inline construction pattern; no `build_indexing_service` composer exists in Plan 5) and `AgentClient` (the only new domain artifact this plan introduces). The lifespan owns engine + session-factory + Valkey-client + agent-client construction + shutdown drain; per-request dependencies are pulled from `app.state` via FastAPI's `Depends()`. The `AgentClient` lives in `adapters/agent/` because the agent is an EXTERNAL HTTP process from the API's point of view (matching the strict-layer-purity precedent set by `adapters/sourcebot/client.py`).

**Tech Stack:** Python 3.14 · FastAPI 0.115 (HTTP routing + lifespan) · uvicorn 0.32 (ASGI server) · httpx 0.27 (AgentClient, runtime — Plan 10 currently pins it as test-only; Plan 11 promotes it to a runtime dep) · prometheus-client 0.21 (`/metrics` ASGI app) · Pydantic v2 (request/response validation + drift detection via `extra="forbid"`) · structlog 24 (structured-log middleware — already configured by Plan 3 via `configure_logging`) · opentelemetry-api + opentelemetry-instrumentation-fastapi (W3C traceparent extraction inbound; manual propagation outbound to agent) · pytest + pytest-asyncio (`asyncio_mode = "auto"`) · respx (test only — httpx mocking) · ruff + ty · uv

---

## Invariants

The following invariants are load-bearing across the plan. They are restated in tasks where they apply but are catalogued here so any reader has a single reference.

1. **Process role — thin entrypoint.** The API process wires services together. It validates request bodies, calls one service, maps service exceptions to HTTP status codes, and returns the service's response. No domain decisions live in `api/`. Spec §3.3 rule 6.

2. **AgentClient lives in `adapters/agent/`.** From the API process's point of view the agent is an external HTTP process; the adapter wraps that boundary. Matches the layer-purity precedent set by `adapters/sourcebot/client.py` (wraps the external Sourcebot daemon). The `AgentClient` class never reaches into the agent's internals; it speaks only the published `POST /v1/query` wire contract (Plan 10 invariant 11).

3. **No envelope on /v1/query or /v1/reindex.** `POST /v1/query` returns `QueryResult` raw (the Plan 3 model with `extra="forbid"`). `POST /v1/reindex` returns `Snapshot` raw (the Plan 3 model). No `{data: …, meta: …}` wrapper. Plan 10 invariant 11 immutability extends across the API↔user boundary: the same Pydantic class is the wire contract on both hops (user → API → agent).

4. **Lock-contention is invisible to clients; retries are idempotent (amended 2026-05-26).** Post-amendment, `pg_advisory_lock(0x46AE0001)` is acquired and held by the `ReindexOrchestratorConsumer` worker process — the API process never calls `pg_try_advisory_lock`. `POST /v1/reindex` therefore cannot raise `AdvisoryLockTimeoutError` and never returns HTTP 409. `IndexingService.enqueue_reindex` is idempotent on `SnapshotConflictError`: if the recomputed `new_snapshot_sha` already has a PENDING/APPLYING row (same tree state, retry from client), the existing row is returned (HTTP 202, same shape — no duplicate XADD). All other `CodeOracleError` family exceptions propagate as `HTTP 500`. (Slice+1 may surface a queue-depth signal as `HTTP 429 Too Many Requests` if backpressure becomes operationally meaningful; explicit non-goal in slice scope.)

5. **Agent-unreachable is HTTP 503.** `AgentClient` raises `AgentUnreachableError` for transport-layer failure (DNS, connection refused, TLS, total timeout, `httpx.RequestError` family). `POST /v1/query` maps this to `HTTP 503 Service Unavailable`. Non-2xx responses from the agent raise `AgentResponseError` and map to `HTTP 502 Bad Gateway`. The API never retries on the user's behalf — clients retry their own requests (slice scope; retry policy lands in Phase 7).

6. **`init_telemetry()` is the SDK boot.** Plan 11's `__main__.py` calls Plan 1A's zero-arg `init_telemetry()` before constructing the FastAPI app (same pattern Plan 10's `agent/__main__.py` uses). `TelemetryConfig` is its own `BaseSettings(env_prefix="CODEORACLE_OTEL_")` (Plan 1A L456) — not nested under `Settings`. FastAPI's OTel server instrumentor (`FastAPIInstrumentor.instrument_app(app)`) extracts incoming `traceparent` and sets the active span context. Outbound `httpx` calls in `AgentClient` propagate the traceparent header manually (`opentelemetry.propagate.inject(headers)`).

7. **No inbound auth in slice.** Per spec §8.6 + Plan 10 invariant 9, no auth gate sits in front of `/v1/query`, `/v1/reindex`, `/v1/health`, or `/metrics`. CORS, rate-limiting, and inbound user→API auth all land in Phase 7. The API → agent hop uses `settings.agent.shared_token` if set (Plan 10 Deviation 9 reactivation trigger); slice default is `None`, no header sent.

8. **`extra="forbid"` everywhere.** `ReindexRequest` (this plan's only new request model) uses `model_config = ConfigDict(extra="forbid")`. Plan 3's `Query` and `QueryResult` already do. Wire-contract drift surfaces at the boundary instead of being silently absorbed.

9. **`Settings.repo_root` is top-level, not nested under `api`.** The API process passes `settings.repo_root` to `IndexingService(repo_root=...)`. Workers (Plan 5/6/7) and the MCP process (Plan 12) will read the same field. Promoting it to `ApiConfig.repo_root` would mean every other consumer either reaches into the API namespace or redeclares its own field — both wrong. Plan 11 Task 3 amends Plan 3's `Settings` class (mirroring how Plan 10 amended `agent/dependencies.py` to append `build_agent_dependencies`).

10. **Shutdown drain order.** Lifespan exit drains `agent_client.aclose()` → `valkey_client.aclose()` → `engine.dispose()`, each in its own `try` / `except` so a failure earlier in the chain does not prevent later resources from being drained. Matches Plan 10's drain pattern (sourcebot → llm → engine, each with try / except).

11. **Reindex is async — POST returns 202 + `Snapshot(status=PENDING)`; GET `/v1/reindex/{snapshot_sha}` polls (amended 2026-05-26).** The route does NOT block on the reindex; it calls `IndexingService.enqueue_reindex` (Plan 5 amendment `40885bf`), which persists a PENDING `Snapshot` row + XADDs a request to `STREAM_REINDEX_REQUESTS`, and returns the row immediately (<50ms wall). The `ReindexOrchestratorConsumer` (Plan 5 amendment, separate worker process) holds the advisory lock and drives the reindex out-of-band; client polls `GET /v1/reindex/{snapshot_sha}` until `status` transitions out of PENDING/APPLYING. `uvicorn --timeout-keep-alive` returns to the framework default (5s); no special sizing needed since the request never blocks on indexing work. Driver: user P0 — sync HTTP reindex over large production repositories would tie up uvicorn workers for minutes and time out under any realistic tenant; see Deviation 6 below for the full pivot rationale.

12. **`IndexingService` is constructed inline in the lifespan.** Plan 5 does not provide a `build_indexing_service` composer — every `IndexingService(...)` construction site spells out the keyword args inline. Plan 11 follows the same pattern in `app.py` lifespan; introducing a composer that wraps Plan 5's existing inline construction shape would invent an abstraction Plan 5 hasn't asked for.

13. **`AgentDependencies` is NOT imported by Plan 11.** Plan 10 owns `AgentDependencies` construction (via `build_agent_dependencies` in `agent/dependencies.py`). Plan 11 talks to the agent only over HTTP — the type stays inside the agent process. This is the layer-purity reason `AgentClient` lives in `adapters/agent/` and not in `services/`.

---

## File structure

The plan creates the API process under `src/codeoracle/api/` and the AgentClient adapter under `src/codeoracle/adapters/agent/`. It modifies three pre-existing files (`core/config.py`, `models/__init__.py`, `pyproject.toml`).

```
src/codeoracle/
├── adapters/
│   └── agent/                                # NEW (Task 1) — wraps the external agent HTTP process
│       ├── __init__.py                        (NEW — empty, marks package)
│       ├── client.py                          (NEW — AgentClient httpx wrapper)
│       └── errors.py                          (NEW — AgentUnreachableError + AgentResponseError)
├── api/                                       # NEW (Task 0 layout, Tasks 4-10 contents) — API process
│   ├── __init__.py                            (NEW — empty)
│   ├── __main__.py                            (NEW — uvicorn entrypoint; Task 10)
│   ├── app.py                                 (NEW — FastAPI app factory + lifespan; Task 9)
│   ├── deps.py                                (NEW — FastAPI Depends() helpers; Task 4)
│   ├── middleware.py                          (NEW — structured-log + trace_id middleware; Task 8)
│   └── routes/                                # NEW package
│       ├── __init__.py                        (NEW — empty)
│       ├── health.py                          (NEW — /v1/health; Task 5)
│       ├── query.py                           (NEW — /v1/query; Task 6)
│       └── reindex.py                         (NEW — /v1/reindex; Task 7)
├── core/
│   └── config.py                              (M — Task 3 appends `repo_root: str = "."` to Settings)
├── models/
│   ├── __init__.py                            (M — Task 2 re-exports ReindexRequest)
│   └── reindex.py                             (NEW — ReindexRequest Pydantic model; Task 2)

tests/
├── unit/
│   ├── adapters/
│   │   └── agent/                             # NEW
│   │       ├── __init__.py                    (NEW — empty)
│   │       └── test_client.py                 (NEW — AgentClient unit tests with respx; Task 1)
│   ├── api/                                   # NEW
│   │   ├── __init__.py                        (NEW — empty)
│   │   ├── test_deps.py                       (NEW — Depends() helper unit tests; Task 4)
│   │   ├── test_middleware.py                 (NEW — trace_id-binding middleware tests; Task 8)
│   │   └── test_routes.py                     (NEW — route-handler unit tests with TestClient; Tasks 5-7)
│   ├── core/
│   │   └── test_config.py                     (M — Task 3 appends test_settings_repo_root_*)
│   └── models/
│       └── test_reindex.py                    (NEW — ReindexRequest validation tests; Task 2)
└── integration/
    └── api/                                   # NEW
        ├── __init__.py                        (NEW — empty)
        ├── conftest.py                        (NEW — fixtures: live FastAPI app, stub agent server, real DB; Task 11)
        ├── test_health_endpoint.py            (NEW — Task 11)
        ├── test_query_endpoint.py             (NEW — Task 11)
        ├── test_reindex_endpoint.py           (NEW — Task 11)
        └── test_metrics_endpoint.py           (NEW — Task 11)

pyproject.toml                                  (M — Task 12 adds prometheus-client + promotes httpx to runtime + `codeoracle-api` script entry + respx test-only)
```

**Total: 22 new files, 3 modified.**

---

## Task list

| # | What it delivers | Key files |
|---|---|---|
| 0 | Package layout — empty `__init__.py` files for all new packages | `src/codeoracle/api/__init__.py`, `src/codeoracle/api/routes/__init__.py`, `src/codeoracle/adapters/agent/__init__.py`, `tests/unit/api/__init__.py`, `tests/unit/adapters/agent/__init__.py`, `tests/integration/api/__init__.py` |
| 1 | `AgentClient` httpx wrapper + error types — wraps `POST /v1/query` against the agent process; raises `AgentUnreachableError` (transport failure) or `AgentResponseError` (non-2xx); propagates W3C traceparent outbound; `aclose()` lifecycle | `adapters/agent/client.py`, `adapters/agent/errors.py`, `tests/unit/adapters/agent/test_client.py` |
| 2 | `ReindexRequest` Pydantic model — `{base_sha: str \| None = None}` with `extra="forbid"` | `models/reindex.py`, `models/__init__.py` (M), `tests/unit/models/test_reindex.py` |
| 3 | `Settings.repo_root` top-level field — defaults to `"."`; consumed by Plan 11 + future workers / MCP | `core/config.py` (M), `tests/unit/core/test_config.py` (M) |
| 4 | `deps.py` — FastAPI `Depends()` helpers reading from `app.state` (`get_settings`, `get_engine`, `get_session_factory`, `get_agent_client`, `get_indexing_service`) | `api/deps.py`, `tests/unit/api/test_deps.py` |
| 5 | `/v1/health` route — db-ping liveness; returns `HealthResponse` (Plan 10 model) | `api/routes/health.py`, `tests/unit/api/test_routes.py` (creates) |
| 6 | `/v1/query` route — accepts `Query`, calls `AgentClient.run`, returns `QueryResult`; 503 on `AgentUnreachableError`, 502 on `AgentResponseError` | `api/routes/query.py`, `tests/unit/api/test_routes.py` (M) |
| 7 | `/v1/reindex` POST + GET routes (async-job, amended 2026-05-26) — POST accepts `ReindexRequest`, calls `IndexingService.enqueue_reindex`, returns 202 + PENDING `Snapshot`; GET `{snapshot_sha}` calls `IndexingService.get_snapshot`, returns 200 + `Snapshot` or 404. No 409 (lock lives in worker). | `api/routes/reindex.py`, `tests/unit/api/test_routes.py` (M) |
| 8 | Structured-log middleware — binds OTel `trace_id` + request method/path to structlog contextvars per request | `api/middleware.py`, `tests/unit/api/test_middleware.py` |
| 9 | `app.py` — FastAPI app factory + lifespan composing engine + session_factory + Valkey + IndexingService + AgentClient; mounts `/metrics`; adds OTel instrumentor + structured-log middleware; includes routers | `api/app.py` |
| 10 | `__main__.py` — uvicorn entrypoint reading `settings.api.host/port` (amended 2026-05-26: uses framework default `timeout_keep_alive`; reindex is async so no long-running request to size against); calls `init_telemetry()` + `configure_logging()` | `api/__main__.py` |
| 11 | Integration tests pinning §8.5 acceptance gates landing in Plan 11 + invariants — health, query (success + 503), reindex (POST 202 + poll-to-APPLIED via in-process `ReindexOrchestratorConsumer` + idempotent retry; amended 2026-05-26), metrics | `tests/integration/api/conftest.py`, `tests/integration/api/test_{health,query,reindex,metrics}_endpoint.py` |
| 12 | `pyproject.toml` — promote `httpx` to runtime, add `prometheus-client`, add `respx` test-only, register `codeoracle-api` script entry point | `pyproject.toml` (M) |
| 13 | Cross-plan dependencies + §6 EDIT table + Plan 12 / 13 hand-off + closure | (this file — narrative-only) |

Tasks 0 → 1 → 2 → 3 form the pre-route prerequisite chain. Tasks 4–8 are independent once 0–3 land (can be parallelised by an executor). Task 9 depends on 1, 4, 5, 6, 7, 8. Task 10 depends on 9. Task 11 depends on 10. Task 12 is a pyproject-only change that can run anywhere before Task 11's integration tests need the deps installed.

---

## Cross-plan dependencies

### Imports from earlier plans

| Plan | Artifact | Used in Plan 11 by |
|---|---|---|
| **Plan 1** | `ruff` + `ty` + `pytest` quality gates; `requires-python = ">=3.14"` | All tasks — every code change runs under the same quality gates |
| **Plan 1A** | `infra/observability/*` (Prometheus scrape config for `/metrics`); `core/telemetry.py:init_telemetry()` (zero-arg, reads `CODEORACLE_OTEL_*` env). (Plan 1A's `defaults.env` uses the `CODEORACLE_*` non-Pydantic env scheme — Plan 11's `CODEORACLE_API__*` keys are Pydantic-bound via `ApiConfig` (Plan 3) and have safe in-code defaults; no `defaults.env` modification is required for Plan 11 to boot.) | Task 10 (`__main__.py` calls `init_telemetry()`), Task 9 (lifespan registers `FastAPIInstrumentor`), Task 8 (structured-log middleware binds OTel trace_id into structlog contextvars) |
| **Plan 2** | `infra/scripts/preflight.sh` smoke runner — Plan 11 adds an `/v1/health` smoke step to the runner; the addition lands in Task 11 of this plan via an EDIT to `infra/scripts/preflight.sh` (M) | Task 11 — adds one `curl -fsS http://127.0.0.1:8080/v1/health` line to the preflight smoke order, gated on the API process being up |
| **Plan 3** | `core/config.py:Settings` (with `ApiConfig` nested model from the 2026-05-26 amendment + `DBConfig` + `EmbeddingConfig` + `AgentConfig`); `core/config.py:get_settings()` LRU singleton; `core/logging.py:configure_logging(settings)` (Plan 3 Task 3 — installs the structlog renderer + JSON pipeline); `core/exceptions.py:AdvisoryLockTimeoutError` + `CodeOracleError` family; `models/query.py:Query` + `QueryResult` + `QueryStatus`; `models/snapshot.py:Snapshot` + `SnapshotStatus` + `ExtractorOutcome` (test-only import — Task 11 integration tests construct `Snapshot` fixtures with `extractor_results={"joern": ExtractorOutcome.SUCCESS}`); `db/engine.py:create_engine()` async engine + session_factory factory | Task 3 appends `repo_root: str = "."` to `Settings` (the only new field — `ApiConfig` already landed); Task 4 builds `Depends()` helpers wrapping `get_settings()`; Task 6 / Task 7 take `Query` / `ReindexRequest` and return `QueryResult` / `Snapshot`; Task 9 lifespan calls `create_engine(settings.db)`; Task 10 (`__main__.py`) calls `configure_logging(settings)` before the first log line; Task 11 imports `ExtractorOutcome` for `Snapshot` test fixtures |
| **Plan 4** | `adapters/llm/__init__.py` + `LLMProvider` ABC + concrete providers — NOT consumed in Plan 11 (the agent process owns LLM calls); listed only to document that Plan 11 does NOT introduce a second LLM-call path | (Negative dep — referenced in invariant 13) |
| **Plan 5** | (pre-amendment) `services/indexing/service.py:IndexingService`; `services/indexing/advisory_lock.py:reindex_single_flight` (called internally by `IndexingService.reindex`); `core/exceptions.py:AdvisoryLockTimeoutError` (raised when lock acquisition exceeds `advisory_lock_timeout_seconds` — Plan 5 amendment 2026-05-26 `40885bf` moves this raise-site into the WORKER process; the API no longer touches the lock); `adapters/valkey/client.py:ValkeyQueueClient` (constructor arg to `IndexingService`); `adapters/valkey/config.py:ValkeyConfig` (standalone `BaseSettings` — Plan 5 L441 — constructed zero-arg per Plan 5's precedent at L2132 and L7016, requires `# type: ignore[call-arg]` because `ValkeyConfig.url` is a required field populated from `CODEORACLE_VALKEY__URL` at runtime — ty cannot see the env source). **(Plan 5 amendment 2026-05-26 `40885bf`):** `IndexingService.enqueue_reindex(self, *, base_sha: str \| None) -> Snapshot` (async-job entrypoint — persists PENDING Snapshot row, XADDs to `STREAM_REINDEX_REQUESTS`, returns the row; idempotent on `SnapshotConflictError`); `IndexingService.get_snapshot(self, snapshot_sha: str) -> Snapshot \| None` (polling-endpoint backend wrapping `SnapshotRepository.get_by_sha`); `STREAM_REINDEX_REQUESTS` constant (`codeoracle.core.streams` — Plan 11 does not import directly; only `IndexingService.enqueue_reindex` does the XADD); `ReindexOrchestratorConsumer` (`codeoracle.workers.reindex_orchestrator.consumer` — separate worker process launched via `python -m codeoracle.workers.reindex_orchestrator`; holds `pg_advisory_lock(0x46AE0001)` on its OWN connection — not on any API connection). | Task 7 — POST handler calls `indexing_service.enqueue_reindex(base_sha=req.base_sha)` returning 202 + PENDING Snapshot; GET handler calls `indexing_service.get_snapshot(snapshot_sha)` returning Snapshot or 404. Task 9 lifespan constructs `IndexingService` inline (matching Plan 5's 5 existing construction sites, no composer needed) and constructs `ValkeyQueueClient(ValkeyConfig())` zero-arg. Task 11 integration test pins POST → 202+PENDING → poll GET → APPLIED round-trip (running `ReindexOrchestratorConsumer` in-process as a background asyncio task — mirrors Plan 5 Task 12 fixture pattern), and idempotent retry. **The advisory-lock interaction now lives entirely in the worker (Plan 5 amendment), NOT in the API** — no `AdvisoryLockTimeoutError` raise path on the request thread; the lock-contention 409 + Retry-After mapping that pre-amendment Plan 11 owned has been deleted (see invariant 4 amended, Deviation 6 amended). |
| **Plan 8** | (None imported by Plan 11 — `HandleResolverService` + `SourcebotClient` live inside the agent process; Plan 11 talks only to the agent's `/v1/query`) | — |
| **Plan 9** | (None — `VerifierService` + `TrustMatrixService` live inside the agent process) | — |
| **Plan 10** | `models/health.py:HealthResponse` (Plan 10 invariant — Task 11 of Plan 10 creates the file; Plan 11 inherits as the `/v1/health` response model); the agent's published `POST /v1/query` wire contract (request body `Query`, response body `QueryResult`; both Plan 3 models — Plan 10 invariant 11); the agent's `GET /v1/health` is reused by integration tests indirectly but NOT by Plan 11's own `/v1/health` (which db-pings the API's own DB connection) | Task 1 (`AgentClient.run(query)` constructs the `POST /v1/query` request body from `Query` and parses the response into `QueryResult`); Task 5 (`/v1/health` returns `HealthResponse`); Task 11 (the stub-agent fixture serves the same wire contract) |
| **specs/2026-05-21-platform-scaffolding-design.md** | §3.2 file tree (pins `api/{__main__.py, app.py, deps.py, routes/{query.py, reindex.py, health.py}}`); §3.3 rule 6 (processes are thin entrypoints — no business logic in `api/`); §4.1 / §4.2 (API → agent over REST; API owns the user-facing entrypoint); §8.5 acceptance-gate row for `test_reindex_endpoint_produces_new_snapshot returning {snapshot_sha: <new>}`; §8.5 acceptance-gate rows for query / health smoke; §8.6 (multi-tenancy / auth deferred to Phase 7 — confirms no inbound-auth gate in slice) | Task 0 + Task 9 file layout matches §3.2; invariants 1 + 7 honor §3.3 rule 6 + §8.6; Task 11 integration tests pin §8.5 rows |

### Provides to later plans

| Consumer | Artifact | Used by |
|---|---|---|
| **Plan 12 (MCP)** | `adapters/agent/client.py:AgentClient` + `adapters/agent/errors.py` — the MCP `explain_symbol` tool invokes the same agent process and reuses the same client; the streamable-HTTP transport may also reuse the AgentClient for parity with the API path | Plan 12 MCP server `__main__.py` (composition root) + the `explain_symbol` tool handler |
| **Plan 12 (MCP)** | `models/reindex.py:ReindexRequest` — the MCP `reindex` tool reuses the request shape if it exposes reindex (Plan 12 will decide scope) | Plan 12 MCP server `tools.py` (if reindex exposed) |
| **Plan 12 (MCP)** | `core/config.py:Settings.repo_root` (this plan's Task 3 addition) — the MCP process also constructs `IndexingService` if it exposes reindex | Plan 12 MCP server composition root |
| **Plan 13 (E2E + slice acceptance)** | The full `codeoracle-api` process — `api/__main__.py` (uvicorn entrypoint) + `GET /v1/health` — which Plan 13 spins up alongside the agent and MCP processes for the cross-process slice acceptance gates (the live POST /v1/query → graph-query → verifier → response path); invariants 3 (QueryResult parity) / 4 (idempotent retry — Plan 11 amendment `fc0894a`) / 5 (503 on agent unreachable) are verified at integration scale by Tasks 3-7 + 9 | Plan 13 E2E suite |
| **Plan 13 (E2E + slice acceptance)** | The `/v1/reindex` endpoint contract — POST returns **202 + `Snapshot(status=PENDING)`**, client polls **`GET /v1/reindex/{snapshot_sha}`** until status is terminal (APPLIED/FAILED) (async pivot, amendment `fc0894a`); Plan 13's `BL-RX01` (or whichever gate ID is assigned in §8.5) verifies a full reindex produces a new snapshot and the next `/v1/query` reads from it | Plan 13 E2E suite |
| **Phase 7 (auth, rate-limit, CORS — Slice+1)** | The API process surface — every Phase 7 inbound-auth amendment will be a Plan 11 file modification (mostly `app.py` middleware insertion + per-route `Security` dep). Slice scope leaves the surface open; Phase 7 wraps it | Phase 7 plan(s) |

### Reciprocal-row catalogue (for D2-style mutual-consistency audits)

Per the D2 audit pattern, every "Plan 11 imports from Plan X" row above MUST be matched by a "Plan X provides to Plan 11" row inside Plan X's own cross-plan-deps section. The reciprocity table to enforce in Phase G (Task #41):

| Plan to amend | Section | Add row |
|---|---|---|
| Plan 1A | "Provides to later plans" / "What Plans X-Y inherit from Plan 1A" | "Plan 11 — `init_telemetry()`. FastAPI server-side OTel instrumentor registered by Plan 11 Task 9; Plan 1A's Prometheus scrape job points at `:8080/metrics` exposed by Plan 11 Task 9" |
| Plan 2 | "Provides to later plans" | "Plan 11 — `infra/scripts/preflight.sh` gains a `/v1/health` smoke line via Plan 11 Task 11 EDIT (M)" |
| Plan 3 | "Provides to later plans" / "What Plans 8 – 12 inherit from Plan 3" → "Plan 11" bullet | Already extended for `ApiConfig` (2026-05-26 amendment `8a4021f`). Plan 11 Task 3 adds another modification: `Settings.repo_root: str = "."` — extend the existing bullet to include the new field name + env var `CODEORACLE_REPO_ROOT`. The bullet also covers `core/logging.py:configure_logging(settings)` (Plan 3 Task 3 — Plan 11 Task 10 calls it before any log line; NOT Plan 1A, which provides `telemetry.py`/`init_telemetry()` only) |
| Plan 5 | "Provides to later plans" — Plan 11 entry | "Plan 11 (amended 2026-05-26) — `IndexingService.enqueue_reindex(base_sha=…)` + `IndexingService.get_snapshot(snapshot_sha)` consumers (per Plan 5 amendment `40885bf`); constructs `IndexingService` inline in `api/app.py` lifespan (matches Plan 5's 5 existing construction sites — no composer needed); also constructs `ValkeyQueueClient(ValkeyConfig())` zero-arg, importing `ValkeyConfig` from `adapters/valkey/config.py` (the standalone BaseSettings, NOT a nested field on `Settings`). The advisory lock + `AdvisoryLockTimeoutError` raise-site now lives in `ReindexOrchestratorConsumer` (Plan 5 amendment `40885bf` Task 13a) — the API never catches that exception. Plan 11 integration test (Task 11) runs the consumer in-process as a background asyncio task to drive POST→202→poll→APPLIED round-trip." |
| Plan 10 | "Provides to later plans" — Plan 11 entry | "Plan 11 — consumes the published `POST /v1/query` wire contract (`Query` → `QueryResult`); imports `models/health.py:HealthResponse` for its own `/v1/health` route" |

(Plan 11 author: leave the Phase G task — #41 — to apply these reciprocal rows across the corpus. This plan's own Cross-plan dependencies section is canonical here.)

---

## Task 0: Package layout

**Files:**
- Create: `src/codeoracle/api/__init__.py`
- Create: `src/codeoracle/api/routes/__init__.py`
- Create: `src/codeoracle/adapters/agent/__init__.py`
- Create: `tests/unit/api/__init__.py`
- Create: `tests/unit/adapters/agent/__init__.py`
- Create: `tests/integration/api/__init__.py`

The six new packages all need empty `__init__.py` files so pytest discovers tests and import paths resolve cleanly. Doing this upfront in a single task avoids "ModuleNotFoundError" friction during later tasks.

- [ ] **Step 1: Create the six empty `__init__.py` files**

Each file is a single blank line — the Python convention for a package marker.

```bash
touch src/codeoracle/api/__init__.py
mkdir -p src/codeoracle/api/routes && touch src/codeoracle/api/routes/__init__.py
mkdir -p src/codeoracle/adapters/agent && touch src/codeoracle/adapters/agent/__init__.py
mkdir -p tests/unit/api && touch tests/unit/api/__init__.py
mkdir -p tests/unit/adapters/agent && touch tests/unit/adapters/agent/__init__.py
mkdir -p tests/integration/api && touch tests/integration/api/__init__.py
```

- [ ] **Step 2: Verify the import surfaces resolve**

```bash
uv run python -c "import codeoracle.api; import codeoracle.api.routes; import codeoracle.adapters.agent; print('ok')"
```

Expected: `ok` on stdout, exit 0. If any of the three imports fail, fix the missing directory before continuing.

- [ ] **Step 3: Commit**

```bash
git add src/codeoracle/api/__init__.py \
        src/codeoracle/api/routes/__init__.py \
        src/codeoracle/adapters/agent/__init__.py \
        tests/unit/api/__init__.py \
        tests/unit/adapters/agent/__init__.py \
        tests/integration/api/__init__.py
git commit -m "feat(api): package layout scaffolding (Task 0)"
```

---

## Task 1: AgentClient adapter + error types

**Files:**
- Create: `src/codeoracle/adapters/agent/errors.py`
- Create: `src/codeoracle/adapters/agent/client.py`
- Test: `tests/unit/adapters/agent/test_client.py`

`AgentClient` wraps the agent process's `POST /v1/query` HTTP endpoint. From the API's point of view the agent is an external process, so the client lives under `adapters/agent/` per strict-layer-purity (matches `adapters/sourcebot/client.py`). The client owns: one long-lived `httpx.AsyncClient` (constructed in `app.py` lifespan, drained on shutdown), explicit per-request timeout, no retries, manual OTel traceparent propagation, and translation of transport / HTTP errors into typed exceptions.

- [ ] **Step 1: Write the failing test — happy-path round trip**

Create `tests/unit/adapters/agent/test_client.py`:

```python
"""AgentClient unit tests — stub the agent HTTP endpoint with respx.

These tests pin the AgentClient wire contract: POST /v1/query body shape,
response parsing into QueryResult, and the error-classification boundary
(AgentUnreachableError vs AgentResponseError).
"""

from __future__ import annotations

import httpx
import pytest
import respx
from pydantic import SecretStr

from codeoracle.adapters.agent.client import AgentClient
from codeoracle.adapters.agent.errors import AgentResponseError, AgentUnreachableError
from codeoracle.models.evidence import (
    Citation,
    Claim,
    EvidenceHandle,
    HandleType,
    Verdict,
    VerdictStatus,
)
from codeoracle.models.query import Query, QueryResult, QueryStatus


@pytest.fixture
def query() -> Query:
    return Query(
        symbol="codeoracle.services.verifier.service.VerifierService.verify",
        snapshot_sha=None,
    )


@pytest.fixture
def query_result_payload() -> dict:
    """A PROVEN QueryResult serialised to JSON — the wire shape the agent
    returns. Built via the `QueryResult.proven` factory + `model_dump`
    so it is valid by construction against Plan 3's `extra="forbid"`
    contract (correct enum casing, >=1 citation on the Claim). The SHA
    lives in the citation handle (canonical QueryResult has no top-level
    snapshot_sha).
    """
    return QueryResult.proven(
        trace_id="00000000000000000000000000000001",
        claim=Claim(
            statement="It's defined in src/auth/hash.py:42 — see citation A.",
            citations=[
                Citation(
                    handle=EvidenceHandle(
                        type=HandleType.CODE_SPAN,
                        locator="file://src/codeoracle/core/security.py:1-10",
                        snapshot_sha="abc123def456",
                    ),
                    excerpt="def hash_password(raw: str) -> str:",
                    source_trust=9,
                )
            ],
            confidence=0.9,
        ),
        verdict=Verdict(
            status=VerdictStatus.PROVEN,
            reason="All citations resolved and match cached excerpts.",
            resolved_citations=[],
        ),
    ).model_dump(mode="json")


async def test_run_returns_query_result_on_2xx(query, query_result_payload):
    """Happy path: agent returns 200 with QueryResult JSON, AgentClient
    parses it back into the Pydantic model.
    """
    async with respx.mock(base_url="http://agent.test:8081") as mock:
        route = mock.post("/v1/query").mock(
            return_value=httpx.Response(200, json=query_result_payload)
        )
        client = AgentClient(
            base_url="http://agent.test:8081",
            request_timeout_seconds=5.0,
            shared_token=None,
        )
        try:
            result = await client.run(query)
        finally:
            await client.aclose()

    assert route.called
    assert isinstance(result, QueryResult)
    assert result.status == QueryStatus.COMPLETE
    assert result.claim is not None
    assert result.claim.citations[0].handle.snapshot_sha == "abc123def456"


async def test_run_sends_query_as_json_body(query, query_result_payload):
    """The POST body must be the Query model serialised — every field
    Plan 3's Query exposes, in the order Pydantic emits them. The agent
    side (Plan 10) validates with `extra="forbid"`, so an extra key would
    fail the request.
    """
    async with respx.mock(base_url="http://agent.test:8081") as mock:
        route = mock.post("/v1/query").mock(
            return_value=httpx.Response(200, json=query_result_payload)
        )
        client = AgentClient(
            base_url="http://agent.test:8081",
            request_timeout_seconds=5.0,
            shared_token=None,
        )
        try:
            await client.run(query)
        finally:
            await client.aclose()

    # respx records the sent request — assert body equality
    sent = route.calls[0].request
    assert sent.method == "POST"
    assert sent.url.path == "/v1/query"
    # Pydantic v2 model_dump_json round-trips through json.loads
    import json as _json
    assert _json.loads(sent.content.decode()) == query.model_dump(mode="json")


async def test_run_raises_agent_unreachable_on_connect_error(query):
    """Transport-layer failure (httpx.ConnectError, ReadTimeout, etc.)
    surfaces as AgentUnreachableError. The original exception is chained
    via __cause__ so observability tooling can still see the root cause.
    """
    async with respx.mock(base_url="http://agent.test:8081") as mock:
        mock.post("/v1/query").mock(side_effect=httpx.ConnectError("nope"))
        client = AgentClient(
            base_url="http://agent.test:8081",
            request_timeout_seconds=5.0,
            shared_token=None,
        )
        try:
            with pytest.raises(AgentUnreachableError) as excinfo:
                await client.run(query)
        finally:
            await client.aclose()

    assert isinstance(excinfo.value.__cause__, httpx.ConnectError)


async def test_run_raises_agent_unreachable_on_total_timeout(query):
    """A request that times out (httpx.ReadTimeout / TimeoutException)
    is unreachable, not a bad gateway. The API maps this to HTTP 503 —
    the same as a connection refusal.
    """
    async with respx.mock(base_url="http://agent.test:8081") as mock:
        mock.post("/v1/query").mock(side_effect=httpx.ReadTimeout("slow"))
        client = AgentClient(
            base_url="http://agent.test:8081",
            request_timeout_seconds=0.1,
            shared_token=None,
        )
        try:
            with pytest.raises(AgentUnreachableError):
                await client.run(query)
        finally:
            await client.aclose()


async def test_run_raises_agent_response_error_on_5xx(query):
    """Non-2xx response = the agent answered but with a failure. This is
    a bad-gateway situation, not unreachable. The API maps it to HTTP 502.
    """
    async with respx.mock(base_url="http://agent.test:8081") as mock:
        mock.post("/v1/query").mock(
            return_value=httpx.Response(500, text="boom")
        )
        client = AgentClient(
            base_url="http://agent.test:8081",
            request_timeout_seconds=5.0,
            shared_token=None,
        )
        try:
            with pytest.raises(AgentResponseError) as excinfo:
                await client.run(query)
        finally:
            await client.aclose()

    assert excinfo.value.status_code == 500
    assert "boom" in str(excinfo.value)


async def test_run_raises_agent_response_error_on_4xx(query):
    """4xx is also a response error — agent rejected the input. The API
    propagates 4xx as 502 (not as a passthrough) because the API has
    already validated the body; any 4xx the agent returns is a contract
    drift, not a client error.
    """
    async with respx.mock(base_url="http://agent.test:8081") as mock:
        mock.post("/v1/query").mock(
            return_value=httpx.Response(422, json={"detail": "drift"})
        )
        client = AgentClient(
            base_url="http://agent.test:8081",
            request_timeout_seconds=5.0,
            shared_token=None,
        )
        try:
            with pytest.raises(AgentResponseError) as excinfo:
                await client.run(query)
        finally:
            await client.aclose()

    assert excinfo.value.status_code == 422


async def test_run_propagates_traceparent_header(query, query_result_payload):
    """OTel traceparent must be injected into the outbound request via
    opentelemetry.propagate.inject(headers). Without the SDK installed
    in the test env the injector is a no-op — but the call site MUST be
    present so production runs (with init_telemetry()) emit it.

    To assert this without an SDK, we use the global propagator's API:
    the W3CTraceContextPropagator is registered by default, but it only
    writes a header if there is an active span context. We set one
    explicitly with NonRecordingSpan + a known SpanContext, then assert
    the header lands.
    """
    from opentelemetry import trace
    from opentelemetry.trace import (
        NonRecordingSpan,
        SpanContext,
        TraceFlags,
        set_span_in_context,
    )

    span_ctx = SpanContext(
        trace_id=0x1234567890ABCDEF1234567890ABCDEF,
        span_id=0xFEDCBA0987654321,
        is_remote=False,
        trace_flags=TraceFlags(0x01),
    )
    ctx = set_span_in_context(NonRecordingSpan(span_ctx))

    async with respx.mock(base_url="http://agent.test:8081") as mock:
        route = mock.post("/v1/query").mock(
            return_value=httpx.Response(200, json=query_result_payload)
        )
        client = AgentClient(
            base_url="http://agent.test:8081",
            request_timeout_seconds=5.0,
            shared_token=None,
        )
        try:
            token = trace.set_span_in_context  # silence ruff F841 — we use ctx via attach below
            from opentelemetry.context import attach, detach
            tok = attach(ctx)
            try:
                await client.run(query)
            finally:
                detach(tok)
        finally:
            await client.aclose()

    sent = route.calls[0].request
    # W3C traceparent format: "00-<32hex trace_id>-<16hex span_id>-<2hex flags>"
    traceparent = sent.headers.get("traceparent")
    assert traceparent is not None
    assert traceparent.startswith("00-")
    assert "1234567890abcdef1234567890abcdef" in traceparent.lower()


async def test_run_sends_shared_token_header_when_configured(query, query_result_payload):
    """If `settings.agent.shared_token` is set (Plan 10 Deviation 9
    reactivation trigger), the AgentClient sends it as
    `X-CodeOracle-Shared-Token`. Slice default is None (no header sent).
    """
    async with respx.mock(base_url="http://agent.test:8081") as mock:
        route = mock.post("/v1/query").mock(
            return_value=httpx.Response(200, json=query_result_payload)
        )
        client = AgentClient(
            base_url="http://agent.test:8081",
            request_timeout_seconds=5.0,
            shared_token=SecretStr("secret-multi-host-token"),
        )
        try:
            await client.run(query)
        finally:
            await client.aclose()

    sent = route.calls[0].request
    assert sent.headers.get("X-CodeOracle-Shared-Token") == "secret-multi-host-token"


async def test_run_omits_shared_token_header_when_none(query, query_result_payload):
    """Slice default: shared_token=None → no header (avoid sending an
    empty string which middleware might misread).
    """
    async with respx.mock(base_url="http://agent.test:8081") as mock:
        route = mock.post("/v1/query").mock(
            return_value=httpx.Response(200, json=query_result_payload)
        )
        client = AgentClient(
            base_url="http://agent.test:8081",
            request_timeout_seconds=5.0,
            shared_token=None,
        )
        try:
            await client.run(query)
        finally:
            await client.aclose()

    sent = route.calls[0].request
    assert "X-CodeOracle-Shared-Token" not in sent.headers


async def test_aclose_is_idempotent():
    """Calling aclose() twice must not raise — the lifespan shutdown
    might be invoked from multiple paths (KeyboardInterrupt + normal
    exit) and we cannot guarantee single-shot semantics from the caller.
    """
    client = AgentClient(
        base_url="http://agent.test:8081",
        request_timeout_seconds=5.0,
        shared_token=None,
    )
    await client.aclose()
    await client.aclose()  # must not raise


async def test_run_after_aclose_raises_runtime_error(query):
    """Using a closed client must fail loudly — silently re-opening would
    mask a lifespan bug. httpx.AsyncClient already raises RuntimeError
    when used after aclose(); AgentClient surfaces this as RuntimeError
    (NOT AgentUnreachableError — this is a programmer error, not a
    transport failure).
    """
    client = AgentClient(
        base_url="http://agent.test:8081",
        request_timeout_seconds=5.0,
        shared_token=None,
    )
    await client.aclose()
    with pytest.raises(RuntimeError):
        await client.run(query)
```

- [ ] **Step 2: Run the tests to verify they fail**

```bash
uv run pytest tests/unit/adapters/agent/test_client.py -v
```

Expected: collection error — `ModuleNotFoundError: No module named 'codeoracle.adapters.agent.client'`. Step 3 fixes this.

- [ ] **Step 3: Write `adapters/agent/errors.py`**

Create `src/codeoracle/adapters/agent/errors.py`:

```python
"""AgentClient error types.

The two leaves below are the entire taxonomy the API-side code matches on.
Anything else that escapes AgentClient (e.g. RuntimeError when the client
is used after aclose()) is a programmer error and is intentionally NOT
caught by the routes — those bubble as HTTP 500.
"""

from __future__ import annotations

from codeoracle.core.exceptions import CodeOracleError


class AgentClientError(CodeOracleError):
    """Base for all AgentClient-raised errors. Do not raise this directly;
    use one of the two concrete subclasses so routes can branch on the
    transport-vs-response distinction.
    """


class AgentUnreachableError(AgentClientError):
    """The agent could not be reached: transport failure (connect refused,
    DNS, TLS) or total timeout. The route layer maps this to HTTP 503
    Service Unavailable. The underlying httpx exception is chained via
    `__cause__` so OTel + structlog still surface the root cause.
    """


class AgentResponseError(AgentClientError):
    """The agent responded but with a non-2xx status. The route layer maps
    this to HTTP 502 Bad Gateway — the request reached the agent but the
    agent's reply violated the contract.

    Attributes:
        status_code: HTTP status the agent returned (non-2xx).
        body_excerpt: First 512 bytes of the response body, decoded as
            utf-8 with errors='replace'. Logged but never returned to the
            HTTP client — leaking agent-internals to users is a Phase 7
            concern (auth), not a slice concern.
    """

    def __init__(self, *, status_code: int, body_excerpt: str) -> None:
        self.status_code = status_code
        self.body_excerpt = body_excerpt
        super().__init__(
            f"agent returned HTTP {status_code}: {body_excerpt!r}"
        )
```

- [ ] **Step 4: Write `adapters/agent/client.py`**

Create `src/codeoracle/adapters/agent/client.py`:

```python
"""HTTP client wrapping the agent process's POST /v1/query endpoint.

This adapter is the only place in the API process that imports httpx.
Per layer-purity rule 6 (spec §3.3), the API process is a thin
entrypoint — wrapping the external agent process's HTTP interface here
keeps `services/` and `routes/` ignorant of transport mechanics.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx
from opentelemetry import propagate
from pydantic import SecretStr

from codeoracle.adapters.agent.errors import (
    AgentResponseError,
    AgentUnreachableError,
)
from codeoracle.models.query import Query, QueryResult

logger = logging.getLogger(__name__)

_QUERY_PATH = "/v1/query"
_BODY_EXCERPT_BYTES = 512


class AgentClient:
    """Async HTTP client for the agent's POST /v1/query.

    One instance lives for the whole API process lifetime (constructed in
    `api/app.py` lifespan, stored on `app.state`, drained on shutdown).
    The class owns a single `httpx.AsyncClient`; per-request, it injects
    the W3C traceparent header (so the agent's FastAPI OTel instrumentor
    picks up the same trace) and the slice-scope shared-token header
    (only when configured — slice default omits it).
    """

    def __init__(
        self,
        *,
        base_url: str,
        request_timeout_seconds: float,
        shared_token: SecretStr | None,
    ) -> None:
        """Construct the AgentClient.

        Args:
            base_url: The agent's URL — `settings.api.agent_base_url`
                (default `http://127.0.0.1:8081` for single-host slice;
                override per Plan 10 Deviation 9 for multi-host).
            request_timeout_seconds: Wall-clock cap for the whole
                request (`settings.api.agent_request_timeout_seconds`,
                default 35.0). Connect-vs-read split stays inside
                httpx; this is the outer cap.
            shared_token: Optional per-process shared secret (SecretStr)
                for the slice+1 reactivation of agent auth. Held wrapped;
                unwrapped via get_secret_value() only at the header write.
                Slice default is `None` — no header sent.
        """
        self._base_url = base_url.rstrip("/")
        self._shared_token = shared_token
        self._client = httpx.AsyncClient(
            base_url=self._base_url,
            timeout=httpx.Timeout(request_timeout_seconds),
        )

    async def run(self, query: Query) -> QueryResult:
        """Send the query to the agent and parse the response.

        Returns:
            The parsed `QueryResult` (Plan 3 model with `extra="forbid"`).

        Raises:
            AgentUnreachableError: transport-layer failure (connect,
                DNS, TLS, total timeout). API routes map this to HTTP 503.
            AgentResponseError: non-2xx HTTP response. API routes map
                this to HTTP 502.
            RuntimeError: the client has been closed via `aclose()`.
                This is a programmer error (lifespan misuse); routes do
                NOT catch it.
        """
        headers: dict[str, str] = {}
        propagate.inject(headers)
        if self._shared_token is not None:
            headers["X-CodeOracle-Shared-Token"] = self._shared_token.get_secret_value()

        body = query.model_dump(mode="json")
        try:
            response = await self._client.post(
                _QUERY_PATH,
                json=body,
                headers=headers,
            )
        except httpx.RequestError as exc:
            # Transport-layer failure — connect refused, DNS, TLS,
            # total timeout. Anything that prevented us from getting a
            # response goes through here. `RequestError` is the parent
            # of `ConnectError`, `ReadTimeout`, `RemoteProtocolError`,
            # etc.
            logger.warning(
                "agent_unreachable",
                extra={"base_url": self._base_url, "error": str(exc)},
            )
            raise AgentUnreachableError(
                f"agent at {self._base_url} unreachable: {exc}"
            ) from exc

        if not (200 <= response.status_code < 300):
            body_excerpt = response.text[:_BODY_EXCERPT_BYTES]
            logger.warning(
                "agent_response_error",
                extra={
                    "status_code": response.status_code,
                    "base_url": self._base_url,
                },
            )
            raise AgentResponseError(
                status_code=response.status_code,
                body_excerpt=body_excerpt,
            )

        # Pydantic v2 model_validate raises ValidationError if the agent
        # sent an unrecognized shape — let it propagate (becomes HTTP 500
        # at the route layer; debuggable via structured log). This is
        # rare and indicates a Plan 3 / Plan 10 wire-contract drift, not
        # something the slice should silently absorb.
        return QueryResult.model_validate(response.json())

    async def aclose(self) -> None:
        """Drain the underlying httpx.AsyncClient. Idempotent (httpx's
        own aclose is idempotent — but documenting the contract here
        anchors the call sites in `api/app.py` lifespan).
        """
        await self._client.aclose()
```

- [ ] **Step 5: Run the tests to verify they pass**

```bash
uv run pytest tests/unit/adapters/agent/test_client.py -v
```

Expected: 10 tests pass. If `test_run_propagates_traceparent_header` fails because the W3C propagator is not the default in the test env, install / configure it via `opentelemetry.propagate.set_global_textmap(...)` in a `conftest.py` — but Plan 1A's default propagator is already W3C, so this should not be needed.

- [ ] **Step 6: Run the lint + type gate against the new files**

```bash
uv run ruff check src/codeoracle/adapters/agent/ tests/unit/adapters/agent/
uv run ruff format --check src/codeoracle/adapters/agent/ tests/unit/adapters/agent/
uv run ty check src/codeoracle/adapters/agent/
```

Expected: all three commands exit 0. If `ty` flags the `opentelemetry.propagate.inject(headers)` signature mismatch (the function accepts `MutableMapping[str, str]`), upgrade the local typing on `headers` from `dict[str, str]` to `MutableMapping[str, str]` — but `dict[str, str]` already satisfies the protocol, so this should be clean.

- [ ] **Step 7: Commit**

```bash
git add src/codeoracle/adapters/agent/errors.py \
        src/codeoracle/adapters/agent/client.py \
        tests/unit/adapters/agent/test_client.py
git commit -m "feat(api): AgentClient + error types (Task 1)"
```

---

## Task 2: ReindexRequest model

**Files:**
- Create: `src/codeoracle/models/reindex.py`
- Modify: `src/codeoracle/models/__init__.py`
- Test: `tests/unit/models/test_reindex.py`

`ReindexRequest` is the request body for `POST /v1/reindex`. It has a single optional field: `base_sha: str | None = None`. `None` means "full reindex" (Plan 5 CLI `--full` flag semantics). Like every Plan 3 model it carries `extra="forbid"` so unknown keys surface as 422 rather than being silently ignored.

- [ ] **Step 1: Write the failing test**

Create `tests/unit/models/test_reindex.py`:

```python
"""ReindexRequest model tests — pin the wire shape so the route handler
can rely on `model_validate` rejecting drift.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from codeoracle.models.reindex import ReindexRequest


def test_default_base_sha_is_none():
    """Empty body = full reindex (base_sha=None). Mirrors Plan 5 CLI
    `--full` (which sets `base_sha=None` before calling
    `IndexingService.reindex`).
    """
    req = ReindexRequest()
    assert req.base_sha is None


def test_base_sha_accepts_hex_string():
    """A SHA1 / SHA256 / arbitrary string is accepted — the model does
    NOT validate the format. Git SHA validation lives inside
    `IndexingService.reindex` (Plan 5's `DeltaComputer` resolves it via
    `git rev-parse`). Keeping the request shape permissive avoids
    duplicating SHA validation in two places.
    """
    req = ReindexRequest(base_sha="abc123def456")
    assert req.base_sha == "abc123def456"


def test_extra_field_rejected():
    """`extra="forbid"` — any unknown key triggers ValidationError so
    wire drift is loud. A user typo (`base_shaa`) or a contract change
    in a future plan that adds a field without updating Plan 11 surfaces
    here, not silently."""
    with pytest.raises(ValidationError) as excinfo:
        ReindexRequest.model_validate({"base_sha": "abc", "force": True})
    # Pydantic v2 errors include the extra-key location
    assert any("force" in str(err) for err in excinfo.value.errors())


def test_base_sha_empty_string_accepted_as_string():
    """Empty string is NOT coerced to None — it stays as "" and is
    forwarded to `IndexingService.reindex(base_sha="")`. Plan 5's
    `DeltaComputer.compute(base_sha="")` will fail at git resolution,
    which surfaces as HTTP 500 via CodeOracleError. The model does not
    second-guess the user's intent here.
    """
    req = ReindexRequest(base_sha="")
    assert req.base_sha == ""


def test_base_sha_explicit_null():
    """JSON `null` deserialises to None — same as omitting the field."""
    req = ReindexRequest.model_validate({"base_sha": None})
    assert req.base_sha is None


def test_model_dump_round_trip():
    """model_dump → model_validate must be lossless. Used by clients
    that re-emit the request (e.g., the MCP tool wrapper in Plan 12).
    """
    original = ReindexRequest(base_sha="deadbeef")
    payload = original.model_dump()
    restored = ReindexRequest.model_validate(payload)
    assert restored == original
```

- [ ] **Step 2: Run the test to verify it fails**

```bash
uv run pytest tests/unit/models/test_reindex.py -v
```

Expected: `ModuleNotFoundError: No module named 'codeoracle.models.reindex'`.

- [ ] **Step 3: Write `models/reindex.py`**

Create `src/codeoracle/models/reindex.py`:

```python
"""ReindexRequest — POST /v1/reindex body model (Plan 11).

A reindex is parameterised by a single optional `base_sha`:
  - `None` (default) → full reindex (Plan 5 CLI `--full` equivalent).
  - non-None hex string → incremental reindex from that SHA.

The model does NOT validate the SHA format — Plan 5's DeltaComputer
resolves it via `git rev-parse`, and any drift surfaces as an
IndexingService error (HTTP 500). Duplicating validation here would
create two places to keep in sync.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class ReindexRequest(BaseModel):
    """POST /v1/reindex request body.

    Wire contract: `extra="forbid"`. Unknown keys raise 422.
    """

    model_config = ConfigDict(extra="forbid")

    base_sha: str | None = None
```

- [ ] **Step 4: Re-export from the models package**

Edit `src/codeoracle/models/__init__.py` — append the re-export, preserving every existing line. The pattern matches Plan 3's existing re-exports (`from codeoracle.models.query import Query  # noqa: F401`, etc.):

```python
from codeoracle.models.reindex import ReindexRequest  # noqa: F401
```

- [ ] **Step 5: Run the test to verify it passes**

```bash
uv run pytest tests/unit/models/test_reindex.py -v
```

Expected: 6 tests pass.

- [ ] **Step 6: Run the lint + type gate**

```bash
uv run ruff check src/codeoracle/models/reindex.py tests/unit/models/test_reindex.py
uv run ty check src/codeoracle/models/reindex.py
```

Expected: both exit 0.

- [ ] **Step 7: Commit**

```bash
git add src/codeoracle/models/reindex.py \
        src/codeoracle/models/__init__.py \
        tests/unit/models/test_reindex.py
git commit -m "feat(api): ReindexRequest model (Task 2)"
```

---

## Task 3: `Settings.repo_root` top-level field

**Files:**
- Modify: `src/codeoracle/core/config.py`
- Modify: `tests/unit/core/test_config.py`

The API process (and any future process that constructs `IndexingService`) needs to know which repository to operate on. Plan 5's CLI takes `--repo-root` with default `.`; production processes need a config field. Plan 11 introduces `Settings.repo_root: str = "."` at the **top level** of `Settings` (not nested under `ApiConfig`) because workers (Plan 5/6/7) and the MCP server (Plan 12) all consume the same value.

This is a Plan 3 file modification owned by Plan 11 — same precedent as Plan 10 appending `build_agent_dependencies` to `agent/dependencies.py`. Plan 3's existing tests stay green; Task 3 adds two new tests covering the default + env override.

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/core/test_config.py` (after `test_api_config_env_override`):

```python
def test_settings_repo_root_default(monkeypatch, tmp_path):
    """`Settings.repo_root` defaults to "." — relative to the process's
    CWD at construction time. The lifespan resolves it to an absolute
    path via Path("...").resolve() before passing to IndexingService;
    the field itself stays as the raw string the env / .env supplied.
    """
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("CODEORACLE_EMBEDDING__MODEL", "text-embedding-3-small")
    monkeypatch.setenv("CODEORACLE_EMBEDDING__DIM", "1536")
    monkeypatch.setenv("CODEORACLE_DB__PASSWORD", "test-db-pw")  # D5-4: required, no default
    s = Settings()
    assert s.repo_root == "."


def test_settings_repo_root_env_override(monkeypatch, tmp_path):
    """`CODEORACLE_REPO_ROOT` overrides the default. The env var is top-level
    (no nested-delimiter `__`) because `repo_root` is a top-level field.
    Multi-host deployments set this to the container's bind mount
    (e.g., `/workspace`).
    """
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("CODEORACLE_EMBEDDING__MODEL", "text-embedding-3-small")
    monkeypatch.setenv("CODEORACLE_EMBEDDING__DIM", "1536")
    monkeypatch.setenv("CODEORACLE_DB__PASSWORD", "test-db-pw")  # D5-4: required, no default
    monkeypatch.setenv("CODEORACLE_REPO_ROOT", "/workspace")
    s = Settings()
    assert s.repo_root == "/workspace"
```

Also update the Step 7 expected count from "14 tests pass (10 in `test_config.py` + 4 in `test_exceptions.py`)" to "16 tests pass (12 in `test_config.py` + 4 in `test_exceptions.py`)".

- [ ] **Step 2: Run the tests to verify they fail**

```bash
uv run pytest tests/unit/core/test_config.py -v
```

Expected: `test_settings_repo_root_default` and `test_settings_repo_root_env_override` fail with `AttributeError: 'Settings' object has no attribute 'repo_root'` (or `AssertionError` if Pydantic accepts an unknown attribute access depending on its `extra` config — Plan 3's Settings uses `extra="ignore"`, so the attribute access fails with AttributeError).

- [ ] **Step 3: Modify `src/codeoracle/core/config.py` — append `repo_root` to Settings**

The current `Settings` class (after the 2026-05-26 ApiConfig amendment) looks like:

```python
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
```

Add ONE new field after `api: ApiConfig = ...`:

```python
    repo_root: str = Field(
        default=".",
        description=(
            "Filesystem path the platform indexes from. Passed to "
            "`IndexingService(repo_root=...)` (Plan 5) by the API "
            "(Plan 11), MCP (Plan 12), and workers (Plan 5/6/7) "
            "composition roots. Default `.` resolves to the process's "
            "CWD at construction time (matches Plan 5's CLI default). "
            "Env var: `CODEORACLE_REPO_ROOT` (top-level — no nested delimiter)."
        ),
    )
```

- [ ] **Step 4: Run the tests to verify they pass**

```bash
uv run pytest tests/unit/core/test_config.py -v
```

Expected: 12 tests pass in `test_config.py` (the 10 from the 2026-05-26 amendment plus the 2 new).

- [ ] **Step 5: Run the full unit-core suite (covers test_exceptions.py too)**

```bash
uv run pytest tests/unit/core/ -v
```

Expected: 16 tests pass (12 in `test_config.py` + 4 in `test_exceptions.py`).

- [ ] **Step 6: Run the lint + type gate**

```bash
uv run ruff check src/codeoracle/core/config.py tests/unit/core/test_config.py
uv run ty check src/codeoracle/core/config.py
```

Expected: both exit 0.

- [ ] **Step 7: Commit**

```bash
git add src/codeoracle/core/config.py tests/unit/core/test_config.py
git commit -m "feat(api): Settings.repo_root top-level field (Task 3)"
```

---

## Task 4: `deps.py` — FastAPI Depends() helpers

**Files:**
- Create: `src/codeoracle/api/deps.py`
- Test: `tests/unit/api/test_deps.py`

`api/deps.py` exposes the FastAPI `Depends()` helpers that route handlers consume. Each helper pulls a single resource off `app.state` (where the lifespan stored it in Task 9). The helpers are pure attribute reads — the lifespan owns construction lifetimes, never the request.

This file is named `deps.py` per spec §3.2 (NOT `dependencies.py`). The agent process uses `agent/dependencies.py` because it ALSO holds the `AgentDependencies` frozen dataclass. The API has no equivalent dataclass — only the per-route DI helpers — so the shorter name `deps.py` is accurate and matches the spec.

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/api/test_deps.py`:

```python
"""Unit tests for api/deps.py — FastAPI Depends() helpers.

These tests use a stub FastAPI app populated with known `app.state`
attributes; they verify each helper reads from the expected slot and
raises a clear error when the slot is empty (i.e., lifespan misuse).
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI, Request

from codeoracle.api.deps import (
    get_agent_client,
    get_engine,
    get_indexing_service,
    get_session_factory,
    get_settings_dep,
)


@pytest.fixture
def app_with_state() -> FastAPI:
    """A FastAPI app whose state holds the resources the lifespan would
    have created. Each test patches the relevant slot.
    """
    app = FastAPI()
    app.state.settings = SimpleNamespace(api=SimpleNamespace(host="127.0.0.1"))
    app.state.engine = MagicMock(name="engine")
    app.state.session_factory = MagicMock(name="session_factory")
    app.state.agent_client = MagicMock(name="agent_client")
    app.state.indexing_service = MagicMock(name="indexing_service")
    return app


def _build_request(app: FastAPI) -> Request:
    """Build a Request with the given app — enough surface for the
    helpers, which only read `request.app.state`.
    """
    scope = {"type": "http", "app": app}
    return Request(scope=scope)


def test_get_settings_dep_returns_app_state_settings(app_with_state):
    request = _build_request(app_with_state)
    assert get_settings_dep(request) is app_with_state.state.settings


def test_get_engine_returns_app_state_engine(app_with_state):
    request = _build_request(app_with_state)
    assert get_engine(request) is app_with_state.state.engine


def test_get_session_factory_returns_app_state_session_factory(app_with_state):
    request = _build_request(app_with_state)
    assert get_session_factory(request) is app_with_state.state.session_factory


def test_get_agent_client_returns_app_state_agent_client(app_with_state):
    request = _build_request(app_with_state)
    assert get_agent_client(request) is app_with_state.state.agent_client


def test_get_indexing_service_returns_app_state_indexing_service(app_with_state):
    request = _build_request(app_with_state)
    assert get_indexing_service(request) is app_with_state.state.indexing_service


def test_helpers_raise_runtime_error_when_state_unset():
    """If a helper is called before the lifespan ran (or after a wrong
    lifespan branch), the missing-attribute access is a programmer
    error. The helper raises RuntimeError with a clear message naming
    the missing slot — silent None passthrough would surface as a
    confusing AttributeError deep inside a route handler.
    """
    empty_app = FastAPI()
    request = _build_request(empty_app)
    with pytest.raises(RuntimeError, match="agent_client"):
        get_agent_client(request)
    with pytest.raises(RuntimeError, match="indexing_service"):
        get_indexing_service(request)
    with pytest.raises(RuntimeError, match="engine"):
        get_engine(request)
    with pytest.raises(RuntimeError, match="session_factory"):
        get_session_factory(request)
    with pytest.raises(RuntimeError, match="settings"):
        get_settings_dep(request)
```

- [ ] **Step 2: Run the tests to verify they fail**

```bash
uv run pytest tests/unit/api/test_deps.py -v
```

Expected: `ModuleNotFoundError: No module named 'codeoracle.api.deps'`.

- [ ] **Step 3: Write `api/deps.py`**

Create `src/codeoracle/api/deps.py`:

```python
"""FastAPI Depends() helpers for the API process.

Each helper reads a single resource off `request.app.state`, where the
lifespan (`api/app.py`) stored it at process startup. Helpers raise
RuntimeError with a clear name if the slot is missing — that means the
lifespan didn't run (test misconfiguration) or didn't write the slot
(implementation bug); both are programmer errors that should fail
loudly, not silently return None.

Route handlers consume these via FastAPI `Depends()`:

    from fastapi import Depends
    from codeoracle.api.deps import get_agent_client
    from codeoracle.adapters.agent.client import AgentClient

    @router.post("/v1/query")
    async def query_endpoint(
        body: Query,
        agent_client: AgentClient = Depends(get_agent_client),
    ) -> QueryResult:
        return await agent_client.run(body)
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from fastapi import Request

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker

    from codeoracle.adapters.agent.client import AgentClient
    from codeoracle.core.config import Settings
    from codeoracle.services.indexing.service import IndexingService


def _require(request: Request, slot: str) -> object:
    """Pull `request.app.state.<slot>` or raise with a clear message."""
    value = getattr(request.app.state, slot, None)
    if value is None:
        raise RuntimeError(
            f"api.deps: request.app.state.{slot} is None — the lifespan "
            f"either did not run or did not initialise this slot."
        )
    return value


def get_settings_dep(request: Request) -> "Settings":
    """The process-wide `Settings` singleton (stored at lifespan start)."""
    return _require(request, "settings")  # type: ignore[return-value]


def get_engine(request: Request) -> "AsyncEngine":
    """The SQLAlchemy AsyncEngine. Per-request session lifetimes are
    managed by the route — see `get_session_factory` instead.
    """
    return _require(request, "engine")  # type: ignore[return-value]


def get_session_factory(request: Request) -> "async_sessionmaker":
    """The `async_sessionmaker` bound to `engine`. Routes that need a
    DB session open one via `async with session_factory() as session:`
    so the lifetime matches one HTTP request.
    """
    return _require(request, "session_factory")  # type: ignore[return-value]


def get_agent_client(request: Request) -> "AgentClient":
    """The long-lived AgentClient — wraps the agent process's POST /v1/query.

    The route layer is responsible for catching `AgentUnreachableError`
    (→ HTTP 503) and `AgentResponseError` (→ HTTP 502); see
    `routes/query.py`.
    """
    return _require(request, "agent_client")  # type: ignore[return-value]


def get_indexing_service(request: Request) -> "IndexingService":
    """The IndexingService instance constructed in the lifespan
    (matching Plan 5's inline-construction pattern).
    """
    return _require(request, "indexing_service")  # type: ignore[return-value]
```

The `TYPE_CHECKING` block keeps the imports lazy — the deps module is loaded at import time, but the type names only matter for static analysis. This avoids a circular import if `services/indexing/service.py` ever imports anything that traces back to the API package.

- [ ] **Step 4: Run the tests to verify they pass**

```bash
uv run pytest tests/unit/api/test_deps.py -v
```

Expected: 6 tests pass.

- [ ] **Step 5: Run the lint + type gate**

```bash
uv run ruff check src/codeoracle/api/deps.py tests/unit/api/test_deps.py
uv run ty check src/codeoracle/api/deps.py
```

Expected: both exit 0.

- [ ] **Step 6: Commit**

```bash
git add src/codeoracle/api/deps.py tests/unit/api/test_deps.py
git commit -m "feat(api): FastAPI Depends() helpers (Task 4)"
```

---

## Task 5: `/v1/health` route — db-ping liveness

**Files:**
- Create: `src/codeoracle/api/routes/health.py`
- Test: `tests/unit/api/test_routes.py`

`/v1/health` is the API process's own liveness probe. It opens a session and runs `SELECT 1` to confirm the DB is reachable. The response is `HealthResponse` (Plan 10's model — `status: Literal["ok", "degraded"]`, `db: Literal["ok", "fail"]`).

The slice-scope contract is intentionally minimal: ok if DB pings, degraded if it doesn't. Plan 12 + the workers will add their own liveness probes; cross-process readiness (the K8s `/v1/ready` style) lands in Phase 9. This route is `/v1/health` only — no `/v1/ready`.

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/api/test_routes.py`:

```python
"""Unit tests for api/routes/*.py — route handlers tested with
FastAPI's TestClient against a stub-state app.

Each test builds a FastAPI app, mounts the route module under test,
populates `app.state` with mocks, and exercises the route via
TestClient. These are route-handler contract tests — integration tests
(real DB, real agent process) live in `tests/integration/api/`.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


# ---- /v1/health ------------------------------------------------------

def _build_app_with_session_factory(session_factory) -> FastAPI:
    """Mount the health router on a FastAPI app with the given
    session_factory in app.state.
    """
    from codeoracle.api.routes.health import router as health_router

    app = FastAPI()
    app.state.session_factory = session_factory
    app.state.settings = SimpleNamespace()
    app.state.engine = MagicMock()
    app.state.agent_client = MagicMock()
    app.state.indexing_service = MagicMock()
    app.include_router(health_router, prefix="/v1")
    return app


def test_health_returns_ok_when_db_pings(monkeypatch):
    """Happy path — session.execute(SELECT 1) returns a result whose
    scalar_one() is 1 → status=ok, db=ok.
    """
    mock_session = AsyncMock()
    mock_result = MagicMock()
    mock_result.scalar_one.return_value = 1
    mock_session.execute = AsyncMock(return_value=mock_result)

    class _SessionFactory:
        def __call__(self):
            return _SessionCtx(mock_session)

    class _SessionCtx:
        def __init__(self, session):
            self._session = session
        async def __aenter__(self):
            return self._session
        async def __aexit__(self, *a):
            return None

    app = _build_app_with_session_factory(_SessionFactory())
    client = TestClient(app)
    r = client.get("/v1/health")
    assert r.status_code == 200
    body = r.json()
    assert body == {"status": "ok", "db": "ok"}


def test_health_returns_degraded_when_db_raises():
    """If the DB ping raises (engine down, connection refused),
    response is degraded + db=fail. Status code stays 200 — health
    probes use the body, not the status, so a degraded API can still
    answer the probe.
    """
    mock_session = AsyncMock()
    mock_session.execute = AsyncMock(side_effect=RuntimeError("db down"))

    class _SessionFactory:
        def __call__(self):
            return _SessionCtx(mock_session)

    class _SessionCtx:
        def __init__(self, session):
            self._session = session
        async def __aenter__(self):
            return self._session
        async def __aexit__(self, *a):
            return None

    app = _build_app_with_session_factory(_SessionFactory())
    client = TestClient(app)
    r = client.get("/v1/health")
    assert r.status_code == 200
    body = r.json()
    assert body == {"status": "degraded", "db": "fail"}
```

- [ ] **Step 2: Run the test to verify it fails**

```bash
uv run pytest tests/unit/api/test_routes.py::test_health_returns_ok_when_db_pings tests/unit/api/test_routes.py::test_health_returns_degraded_when_db_raises -v
```

Expected: `ModuleNotFoundError: No module named 'codeoracle.api.routes.health'`.

- [ ] **Step 3: Write `api/routes/health.py`**

Create `src/codeoracle/api/routes/health.py`:

```python
"""GET /v1/health — db-ping liveness for the API process.

Slice scope intent (Plan 11 invariant 7 + spec §8.6): one HTTP probe
endpoint, no auth gate. Cross-process readiness (`/v1/ready`-style
gate that waits for the agent + workers + Valkey + DB all to be
reachable) lands in Phase 9; the slice has only this single liveness
endpoint per process.

The response body is `HealthResponse` (Plan 10 model):
  - status: Literal["ok", "degraded"]
  - db:     Literal["ok", "fail"]

Status stays HTTP 200 regardless of db health — health-probe
consumers read the body, not the status. A 5xx here would tell K8s /
external monitors that the API itself is broken (route-routing layer
failed), which is a different signal than "DB is down".
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker

from codeoracle.api.deps import get_session_factory
from codeoracle.models.health import HealthResponse

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/health", response_model=HealthResponse)
async def health(
    session_factory: async_sessionmaker = Depends(get_session_factory),
) -> HealthResponse:
    """Liveness — open a session and run `SELECT 1`."""
    try:
        async with session_factory() as session:
            result = await session.execute(text("SELECT 1"))
            value = result.scalar_one()
            if value != 1:
                logger.warning("health_db_ping_unexpected", extra={"value": value})
                return HealthResponse(status="degraded", db="fail")
        return HealthResponse(status="ok", db="ok")
    except Exception as exc:  # noqa: BLE001 — health probe must not crash the process
        logger.warning("health_db_ping_failed", extra={"error": str(exc)})
        return HealthResponse(status="degraded", db="fail")
```

The broad `except Exception` is deliberate and the `noqa: BLE001` documents that — a health probe that raises through to the ASGI layer turns into HTTP 500, which the probe consumer reads as "API is broken at routing layer". Catching here lets the body carry the precise signal (`db: "fail"`).

- [ ] **Step 4: Run the tests to verify they pass**

```bash
uv run pytest tests/unit/api/test_routes.py -v
```

Expected: 2 tests pass (health-related).

- [ ] **Step 5: Run the lint + type gate**

```bash
uv run ruff check src/codeoracle/api/routes/health.py
uv run ty check src/codeoracle/api/routes/health.py
```

Expected: both exit 0.

- [ ] **Step 6: Commit**

```bash
git add src/codeoracle/api/routes/health.py tests/unit/api/test_routes.py
git commit -m "feat(api): /v1/health route (Task 5)"
```

---

## Task 6: `/v1/query` route — agent delegation

**Files:**
- Create: `src/codeoracle/api/routes/query.py`
- Modify: `tests/unit/api/test_routes.py` (append query-route tests)

The `/v1/query` handler accepts a `Query` body, forwards it to the agent via `AgentClient.run`, and returns the raw `QueryResult` (no envelope — invariant 3). The two `AgentClient` error types map to HTTP 503 / 502 (invariant 5).

- [ ] **Step 1: Append failing tests to `tests/unit/api/test_routes.py`**

Append after the health tests:

```python
# ---- /v1/query -------------------------------------------------------

def _build_app_with_agent_client(agent_client) -> FastAPI:
    """Mount the query router on a FastAPI app with the given
    agent_client in app.state.
    """
    from codeoracle.api.routes.query import router as query_router

    app = FastAPI()
    app.state.agent_client = agent_client
    app.state.settings = SimpleNamespace()
    app.state.engine = MagicMock()
    app.state.session_factory = MagicMock()
    app.state.indexing_service = MagicMock()
    app.include_router(query_router, prefix="/v1")
    return app


def test_query_returns_query_result_raw(monkeypatch):
    """Happy path: AgentClient.run returns QueryResult; the route
    returns it as the response body (no envelope per invariant 3).
    """
    from codeoracle.models.evidence import (
        Citation,
        Claim,
        EvidenceHandle,
        HandleType,
        Verdict,
        VerdictStatus,
    )
    from codeoracle.models.query import QueryResult
    agent_client = MagicMock()
    agent_client.run = AsyncMock(
        return_value=QueryResult.proven(
            trace_id="00000000000000000000000000000001",
            claim=Claim(
                statement="The function is at hash.py:42.",
                citations=[
                    Citation(
                        handle=EvidenceHandle(
                            type=HandleType.CODE_SPAN,
                            locator="file://src/codeoracle/core/security.py:1-10",
                            snapshot_sha="abc123",
                        ),
                        excerpt="def hash_password(raw: str) -> str:",
                        source_trust=9,
                    )
                ],
                confidence=0.9,
            ),
            verdict=Verdict(
                status=VerdictStatus.PROVEN,
                reason="All citations resolved and match cached excerpts.",
                resolved_citations=[],
            ),
        )
    )

    app = _build_app_with_agent_client(agent_client)
    client = TestClient(app)
    r = client.post(
        "/v1/query",
        json={"symbol": "codeoracle.core.security.hash_password", "snapshot_sha": None},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "COMPLETE"
    assert body["claim"]["statement"] == "The function is at hash.py:42."
    assert body["claim"]["citations"][0]["handle"]["snapshot_sha"] == "abc123"
    # No envelope keys
    assert "data" not in body
    assert "meta" not in body


def test_query_returns_503_on_agent_unreachable():
    """AgentUnreachableError → HTTP 503 + a plain JSON error body
    (no internals leaked).
    """
    from codeoracle.adapters.agent.errors import AgentUnreachableError
    agent_client = MagicMock()
    agent_client.run = AsyncMock(
        side_effect=AgentUnreachableError("agent at http://… unreachable")
    )

    app = _build_app_with_agent_client(agent_client)
    client = TestClient(app)
    r = client.post(
        "/v1/query",
        json={"symbol": "codeoracle.core.security.hash_password", "snapshot_sha": None},
    )
    assert r.status_code == 503
    body = r.json()
    assert body == {"detail": "agent unreachable"}


def test_query_returns_502_on_agent_response_error():
    """AgentResponseError → HTTP 502 + plain JSON body."""
    from codeoracle.adapters.agent.errors import AgentResponseError
    agent_client = MagicMock()
    agent_client.run = AsyncMock(
        side_effect=AgentResponseError(status_code=500, body_excerpt="boom")
    )

    app = _build_app_with_agent_client(agent_client)
    client = TestClient(app)
    r = client.post(
        "/v1/query",
        json={"symbol": "codeoracle.core.security.hash_password", "snapshot_sha": None},
    )
    assert r.status_code == 502
    body = r.json()
    assert body == {"detail": "agent response error"}


def test_query_rejects_unknown_field_as_422():
    """Plan 3's Query uses `extra="forbid"` (per the 2026-05-26
    amendment). An unknown key surfaces as HTTP 422 — the route
    handler never sees the body.
    """
    agent_client = MagicMock()
    agent_client.run = AsyncMock()

    app = _build_app_with_agent_client(agent_client)
    client = TestClient(app)
    r = client.post(
        "/v1/query",
        json={"symbol": "codeoracle.core.security.hash_password", "snapshot_sha": None, "extra_key": True},
    )
    assert r.status_code == 422
    agent_client.run.assert_not_called()


def test_query_rejects_missing_symbol_as_422():
    """Required field `symbol` missing → 422."""
    agent_client = MagicMock()
    agent_client.run = AsyncMock()

    app = _build_app_with_agent_client(agent_client)
    client = TestClient(app)
    r = client.post("/v1/query", json={"snapshot_sha": None})
    assert r.status_code == 422
    agent_client.run.assert_not_called()
```

- [ ] **Step 2: Run the test to verify it fails**

```bash
uv run pytest tests/unit/api/test_routes.py -v -k query
```

Expected: `ModuleNotFoundError: No module named 'codeoracle.api.routes.query'`.

- [ ] **Step 3: Write `api/routes/query.py`**

Create `src/codeoracle/api/routes/query.py`:

```python
"""POST /v1/query — delegate to the agent process.

The handler is intentionally thin: validate body → forward to agent →
return raw response. Per invariant 3 (no envelope), the response body
IS the QueryResult, byte-for-byte.

Error mapping (invariant 5):
  - AgentUnreachableError → HTTP 503 (transport failure — agent down)
  - AgentResponseError     → HTTP 502 (agent answered with non-2xx)

Anything else propagates and becomes HTTP 500 (uncaught exceptions are
logged via structlog + reported via OTel; this is desirable — a wire
contract drift surfaces as a 500 we can debug, not as a silent
fallback).
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, status

from codeoracle.adapters.agent.client import AgentClient
from codeoracle.adapters.agent.errors import (
    AgentResponseError,
    AgentUnreachableError,
)
from codeoracle.api.deps import get_agent_client
from codeoracle.models.query import Query, QueryResult

logger = logging.getLogger(__name__)

router = APIRouter()


@router.post("/query", response_model=QueryResult)
async def query_endpoint(
    body: Query,
    agent_client: AgentClient = Depends(get_agent_client),
) -> QueryResult:
    try:
        return await agent_client.run(body)
    except AgentUnreachableError:
        logger.warning("query_agent_unreachable")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="agent unreachable",
        )
    except AgentResponseError as exc:
        logger.warning(
            "query_agent_response_error",
            extra={"status_code": exc.status_code},
        )
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="agent response error",
        )
```

- [ ] **Step 4: Run the tests to verify they pass**

```bash
uv run pytest tests/unit/api/test_routes.py -v -k query
```

Expected: 5 tests pass (query-related).

- [ ] **Step 5: Run the lint + type gate**

```bash
uv run ruff check src/codeoracle/api/routes/query.py
uv run ty check src/codeoracle/api/routes/query.py
```

Expected: both exit 0.

- [ ] **Step 6: Commit**

```bash
git add src/codeoracle/api/routes/query.py tests/unit/api/test_routes.py
git commit -m "feat(api): /v1/query route with agent delegation (Task 6)"
```

---

## Task 7: `/v1/reindex` POST (enqueue) + GET (poll) — IndexingService async-job delegation

**Files:**
- Create: `src/codeoracle/api/routes/reindex.py`
- Modify: `tests/unit/api/test_routes.py` (append reindex-route tests)

Amended 2026-05-26 (invariants 4 + 11, Deviation 6): `POST /v1/reindex` is now an async-job enqueue — it calls `IndexingService.enqueue_reindex(base_sha=...)` (Plan 5 amendment `40885bf`), receives a PENDING `Snapshot` row, and returns it with `HTTP 202 Accepted` (no envelope; invariant 3). A second route `GET /v1/reindex/{snapshot_sha}` polls a snapshot's lifecycle: 200 + Snapshot if found, 404 if unknown. The handler classifies no domain exceptions — `IndexingService.enqueue_reindex` is idempotent on `SnapshotConflictError` (returns the existing row on retry), and the advisory lock lives entirely in the `ReindexOrchestratorConsumer` worker process (it never reaches the API). All other `CodeOracleError` family exceptions propagate to HTTP 500.

- [ ] **Step 1: Append failing tests to `tests/unit/api/test_routes.py`**

Append after the query tests:

```python
# ---- /v1/reindex -----------------------------------------------------

def _build_app_with_indexing_service(indexing_service, settings=None) -> FastAPI:
    """Mount the reindex router on a FastAPI app with the given
    indexing_service in app.state. `settings` populates app.state.settings
    (defaults to a stub; lock_acquisition_timeout_seconds is no longer
    consumed by this route post-2026-05-26 amendment but the stub keeps
    the shape for Settings consistency).
    """
    from codeoracle.api.routes.reindex import router as reindex_router

    app = FastAPI()
    app.state.indexing_service = indexing_service
    app.state.settings = settings or SimpleNamespace(
        api=SimpleNamespace(lock_acquisition_timeout_seconds=5.0)
    )
    app.state.engine = MagicMock()
    app.state.session_factory = MagicMock()
    app.state.agent_client = MagicMock()
    app.include_router(reindex_router, prefix="/v1")
    return app


def test_reindex_returns_202_with_pending_snapshot():
    """POST returns 202 + PENDING Snapshot raw (invariant 3 — no envelope).
    enqueue_reindex is called exactly once with the requested base_sha.
    """
    from codeoracle.models.snapshot import Snapshot, SnapshotStatus
    indexing_service = MagicMock()
    indexing_service.enqueue_reindex = AsyncMock(
        return_value=Snapshot(
            sha="pending_sha_abc",
            parent_sha=None,
            status=SnapshotStatus.PENDING,
            extractor_results={},
            applied_at=None,
            error=None,
        )
    )

    app = _build_app_with_indexing_service(indexing_service)
    client = TestClient(app)
    r = client.post("/v1/reindex", json={"base_sha": None})
    assert r.status_code == 202
    body = r.json()
    assert body["sha"] == "pending_sha_abc"
    assert body["status"] == "pending"
    # No envelope keys (invariant 3)
    assert "data" not in body
    indexing_service.enqueue_reindex.assert_awaited_once_with(base_sha=None)


def test_reindex_full_when_empty_body():
    """Empty body = base_sha=None = full reindex enqueue."""
    from codeoracle.models.snapshot import Snapshot, SnapshotStatus
    indexing_service = MagicMock()
    indexing_service.enqueue_reindex = AsyncMock(
        return_value=Snapshot(
            sha="full_pending",
            parent_sha=None,
            status=SnapshotStatus.PENDING,
            extractor_results={},
            applied_at=None,
            error=None,
        )
    )

    app = _build_app_with_indexing_service(indexing_service)
    client = TestClient(app)
    r = client.post("/v1/reindex", json={})
    assert r.status_code == 202
    indexing_service.enqueue_reindex.assert_awaited_once_with(base_sha=None)


def test_reindex_with_base_sha():
    """Explicit base_sha → incremental enqueue."""
    from codeoracle.models.snapshot import Snapshot, SnapshotStatus
    indexing_service = MagicMock()
    indexing_service.enqueue_reindex = AsyncMock(
        return_value=Snapshot(
            sha="incr_pending",
            parent_sha="base123",
            status=SnapshotStatus.PENDING,
            extractor_results={},
            applied_at=None,
            error=None,
        )
    )

    app = _build_app_with_indexing_service(indexing_service)
    client = TestClient(app)
    r = client.post("/v1/reindex", json={"base_sha": "base123"})
    assert r.status_code == 202
    indexing_service.enqueue_reindex.assert_awaited_once_with(base_sha="base123")


def test_reindex_idempotent_returns_existing_snapshot():
    """Two POSTs with the same tree state return the same snapshot_sha.
    Idempotence lives inside IndexingService.enqueue_reindex (the second
    call hits the SnapshotConflictError branch and returns the existing
    row); the route is unaware and returns 202 both times.
    """
    from codeoracle.models.snapshot import Snapshot, SnapshotStatus
    existing_snap = Snapshot(
        sha="same_sha_xyz",
        parent_sha=None,
        status=SnapshotStatus.PENDING,
        extractor_results={},
        applied_at=None,
        error=None,
    )
    indexing_service = MagicMock()
    indexing_service.enqueue_reindex = AsyncMock(
        side_effect=[existing_snap, existing_snap]
    )

    app = _build_app_with_indexing_service(indexing_service)
    client = TestClient(app)
    r1 = client.post("/v1/reindex", json={"base_sha": None})
    r2 = client.post("/v1/reindex", json={"base_sha": None})
    assert r1.status_code == 202
    assert r2.status_code == 202
    assert r1.json()["sha"] == r2.json()["sha"] == "same_sha_xyz"
    assert indexing_service.enqueue_reindex.await_count == 2


def test_reindex_rejects_unknown_field_as_422():
    """ReindexRequest extra="forbid" — unknown key → 422."""
    indexing_service = MagicMock()
    indexing_service.enqueue_reindex = AsyncMock()

    app = _build_app_with_indexing_service(indexing_service)
    client = TestClient(app)
    r = client.post("/v1/reindex", json={"base_sha": None, "force": True})
    assert r.status_code == 422
    indexing_service.enqueue_reindex.assert_not_called()


def test_get_reindex_returns_snapshot():
    """GET /v1/reindex/{sha} returns the Snapshot row raw with 200."""
    from datetime import datetime, timezone
    from codeoracle.models.snapshot import ExtractorOutcome, Snapshot, SnapshotStatus
    indexing_service = MagicMock()
    indexing_service.get_snapshot = AsyncMock(
        return_value=Snapshot(
            sha="lookup_sha",
            parent_sha=None,
            status=SnapshotStatus.APPLIED,
            extractor_results={"joern": ExtractorOutcome.SUCCESS},
            applied_at=datetime(2026, 5, 26, 12, 0, 0, tzinfo=timezone.utc),
            error=None,
        )
    )

    app = _build_app_with_indexing_service(indexing_service)
    client = TestClient(app)
    r = client.get("/v1/reindex/lookup_sha")
    assert r.status_code == 200
    body = r.json()
    assert body["sha"] == "lookup_sha"
    assert body["status"] == "applied"
    indexing_service.get_snapshot.assert_awaited_once_with("lookup_sha")


def test_get_reindex_404_on_unknown_sha():
    """GET /v1/reindex/{unknown_sha} → 404 + detail."""
    indexing_service = MagicMock()
    indexing_service.get_snapshot = AsyncMock(return_value=None)

    app = _build_app_with_indexing_service(indexing_service)
    client = TestClient(app)
    r = client.get("/v1/reindex/missing_sha")
    assert r.status_code == 404
    assert r.json() == {"detail": "no snapshot with sha='missing_sha'"}


def test_get_reindex_carries_pending_status():
    """Snapshot.status=PENDING serialises as the lowercase string the
    polling client distinguishes.
    """
    from codeoracle.models.snapshot import Snapshot, SnapshotStatus
    indexing_service = MagicMock()
    indexing_service.get_snapshot = AsyncMock(
        return_value=Snapshot(
            sha="poll_sha",
            parent_sha=None,
            status=SnapshotStatus.PENDING,
            extractor_results={},
            applied_at=None,
            error=None,
        )
    )

    app = _build_app_with_indexing_service(indexing_service)
    client = TestClient(app)
    r = client.get("/v1/reindex/poll_sha")
    assert r.status_code == 200
    assert r.json()["status"] == "pending"


def test_get_reindex_carries_applying_status():
    """Snapshot.status=APPLYING serialises during the worker reindex."""
    from codeoracle.models.snapshot import Snapshot, SnapshotStatus
    indexing_service = MagicMock()
    indexing_service.get_snapshot = AsyncMock(
        return_value=Snapshot(
            sha="poll_sha",
            parent_sha=None,
            status=SnapshotStatus.APPLYING,
            extractor_results={},
            applied_at=None,
            error=None,
        )
    )

    app = _build_app_with_indexing_service(indexing_service)
    client = TestClient(app)
    r = client.get("/v1/reindex/poll_sha")
    assert r.status_code == 200
    assert r.json()["status"] == "applying"


def test_get_reindex_carries_failed_status_with_error():
    """Snapshot.status=FAILED with error message serialises raw."""
    from codeoracle.models.snapshot import Snapshot, SnapshotStatus
    indexing_service = MagicMock()
    indexing_service.get_snapshot = AsyncMock(
        return_value=Snapshot(
            sha="poll_sha",
            parent_sha=None,
            status=SnapshotStatus.FAILED,
            extractor_results={},
            applied_at=None,
            error="joern extractor crashed",
        )
    )

    app = _build_app_with_indexing_service(indexing_service)
    client = TestClient(app)
    r = client.get("/v1/reindex/poll_sha")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "failed"
    assert body["error"] == "joern extractor crashed"
```

- [ ] **Step 2: Run the test to verify it fails**

```bash
uv run pytest tests/unit/api/test_routes.py -v -k reindex
```

Expected: `ModuleNotFoundError: No module named 'codeoracle.api.routes.reindex'`.

- [ ] **Step 3: Write `api/routes/reindex.py`**

Create `src/codeoracle/api/routes/reindex.py`:

```python
"""Reindex routes (amended 2026-05-26 — sync→async pivot).

POST /v1/reindex — enqueue an async reindex; return the PENDING Snapshot
raw (invariant 3) with HTTP 202. The handler does NOT block on the
reindex itself; it delegates to IndexingService.enqueue_reindex which
persists a PENDING Snapshot row + XADDs to STREAM_REINDEX_REQUESTS, and
returns the row immediately. The ReindexOrchestratorConsumer worker
process (Plan 5 amendment 40885bf) drives the actual reindex out-of-band.

GET /v1/reindex/{snapshot_sha} — poll a snapshot's lifecycle. Returns the
Snapshot row raw (200) or 404 if the sha is not found. Clients poll until
status is APPLIED or FAILED — the Snapshot model's `status` field carries
the lifecycle (Plan 3 SnapshotStatus enum: PENDING|APPLYING|APPLIED|FAILED).

Lock-contention is invisible at this boundary (invariant 4 amended): the
advisory lock lives in the worker process; the API never raises
AdvisoryLockTimeoutError. enqueue_reindex is idempotent on
SnapshotConflictError (returns the existing row); all other CodeOracleError
family exceptions propagate as HTTP 500.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, status

from codeoracle.api.deps import get_indexing_service
from codeoracle.models.reindex import ReindexRequest
from codeoracle.models.snapshot import Snapshot
from codeoracle.services.indexing.service import IndexingService

logger = logging.getLogger(__name__)

router = APIRouter()


@router.post(
    "/reindex",
    response_model=Snapshot,
    status_code=status.HTTP_202_ACCEPTED,
)
async def reindex_endpoint(
    body: ReindexRequest,
    indexing_service: IndexingService = Depends(get_indexing_service),
) -> Snapshot:
    """Enqueue an async reindex; return the PENDING Snapshot row."""
    snap = await indexing_service.enqueue_reindex(base_sha=body.base_sha)
    logger.info(
        "reindex_enqueued",
        extra={"snapshot_sha": snap.sha, "status": snap.status.value},
    )
    return snap


@router.get(
    "/reindex/{snapshot_sha}",
    response_model=Snapshot,
)
async def reindex_status_endpoint(
    snapshot_sha: str,
    indexing_service: IndexingService = Depends(get_indexing_service),
) -> Snapshot:
    """Poll a reindex's lifecycle. Returns the Snapshot row raw, or 404
    if the sha is not found.
    """
    snap = await indexing_service.get_snapshot(snapshot_sha)
    if snap is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"no snapshot with sha={snapshot_sha!r}",
        )
    return snap
```

`indexing_service.get_snapshot(snapshot_sha) -> Snapshot | None` is a thin facade Plan 5 amendment `40885bf` adds alongside `enqueue_reindex`; it wraps `SnapshotRepository.get_by_sha`. The route does not take a `Request` arg because `FastAPIInstrumentor.instrument_app(app)` (Task 9) handles incoming-traceparent extraction at the ASGI layer, before the handler runs; the handler does not need to touch the request object.

- [ ] **Step 4: Run the tests to verify they pass**

```bash
uv run pytest tests/unit/api/test_routes.py -v -k reindex
```

Expected: 10 tests pass (5 POST + 5 GET / status).

- [ ] **Step 5: Run the lint + type gate**

```bash
uv run ruff check src/codeoracle/api/routes/reindex.py
uv run ty check src/codeoracle/api/routes/reindex.py
```

Expected: both exit 0.

- [ ] **Step 6: Run the full route-test file to confirm nothing earlier regressed**

```bash
uv run pytest tests/unit/api/test_routes.py -v
```

Expected: 17 tests pass (2 health + 5 query + 10 reindex).

- [ ] **Step 7: Commit**

```bash
git add src/codeoracle/api/routes/reindex.py tests/unit/api/test_routes.py
git commit -m "feat(api): /v1/reindex POST+poll async-job routes (Task 7)"
```

---

## Task 8: Structured-log middleware

**Files:**
- Create: `src/codeoracle/api/middleware.py`
- Test: `tests/unit/api/test_middleware.py`

A FastAPI middleware that, per request, binds the OTel active-span's `trace_id` (32 hex chars, W3C format) + the request's HTTP method and path into the structlog contextvars. After the response, it unbinds. This means every log line emitted during a request (route handler logs, service logs, repository logs) carries the same `trace_id` field — searchable in Loki, correlatable with the OTel traces in Tempo.

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/api/test_middleware.py`:

```python
"""Unit tests for api/middleware.py — structured-log middleware.

The middleware binds OTel trace_id + http.method + http.path into
structlog contextvars per request. Tests verify (a) bind happens
before downstream handlers see the request, (b) unbind happens after
the response is returned, (c) trace_id is the 32-hex W3C format.
"""

from __future__ import annotations

import structlog
from fastapi import FastAPI
from fastapi.testclient import TestClient
from opentelemetry import trace
from opentelemetry.trace import (
    NonRecordingSpan,
    SpanContext,
    TraceFlags,
    set_span_in_context,
)
from opentelemetry.context import attach, detach

from codeoracle.api.middleware import StructuredLogMiddleware


def _build_app_with_middleware() -> FastAPI:
    """Build a minimal FastAPI app with the middleware mounted and a
    test endpoint that returns the current structlog contextvars.
    """
    app = FastAPI()
    app.add_middleware(StructuredLogMiddleware)

    @app.get("/_probe")
    async def probe():
        ctx = structlog.contextvars.get_contextvars()
        return {
            "trace_id": ctx.get("trace_id"),
            "http.method": ctx.get("http.method"),
            "http.path": ctx.get("http.path"),
        }

    return app


def test_middleware_binds_method_and_path():
    """Without any OTel span active, trace_id is None but method + path
    bind.
    """
    app = _build_app_with_middleware()
    client = TestClient(app)
    r = client.get("/_probe")
    assert r.status_code == 200
    body = r.json()
    assert body["http.method"] == "GET"
    assert body["http.path"] == "/_probe"
    # No active span → trace_id is None (we don't fabricate one)
    assert body["trace_id"] is None


def test_middleware_binds_trace_id_when_active_span():
    """With a parent span active in the request scope, the middleware
    extracts trace_id as 32-hex W3C and binds it.
    """
    span_ctx = SpanContext(
        trace_id=0xABCDEF0123456789ABCDEF0123456789,
        span_id=0x1111222233334444,
        is_remote=True,
        trace_flags=TraceFlags(0x01),
    )
    ctx = set_span_in_context(NonRecordingSpan(span_ctx))
    tok = attach(ctx)
    try:
        app = _build_app_with_middleware()
        client = TestClient(app)
        r = client.get("/_probe")
    finally:
        detach(tok)

    assert r.status_code == 200
    body = r.json()
    assert body["trace_id"] == "abcdef0123456789abcdef0123456789"


def test_middleware_unbinds_after_response():
    """After the request completes, the contextvars are clean for the
    NEXT request — no leakage between handlers.
    """
    app = _build_app_with_middleware()
    client = TestClient(app)

    # First request seeds the contextvars
    client.get("/_probe")

    # Inspect contextvars OUTSIDE a request — the middleware's unbind
    # should have cleared trace_id / method / path.
    ctx = structlog.contextvars.get_contextvars()
    assert ctx.get("trace_id") is None
    assert ctx.get("http.method") is None
    assert ctx.get("http.path") is None
```

- [ ] **Step 2: Run the tests to verify they fail**

```bash
uv run pytest tests/unit/api/test_middleware.py -v
```

Expected: `ModuleNotFoundError: No module named 'codeoracle.api.middleware'`.

- [ ] **Step 3: Write `api/middleware.py`**

Create `src/codeoracle/api/middleware.py`:

```python
"""Structured-log middleware — bind OTel trace_id + request scope into
structlog contextvars per request.

The middleware uses `structlog.contextvars.bind_contextvars` /
`unbind_contextvars` so any log line emitted while the request is
in-flight (route handler, service, repo) carries the same `trace_id`
field automatically. Pair this with Plan 3's `configure_logging` —
which installs the structlog renderer + the JSON pipeline — and every
log record gains the trace context without the call site mentioning it.
"""

from __future__ import annotations

import structlog
from opentelemetry import trace
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response


_INVALID_TRACE_ID = 0  # OpenTelemetry sentinel — INVALID_TRACE_ID


class StructuredLogMiddleware(BaseHTTPMiddleware):
    """Per-request structlog contextvars binding.

    Bound keys:
      - trace_id: 32-hex W3C trace ID, or None if no active span.
      - http.method: e.g. "POST".
      - http.path: e.g. "/v1/query".
    """

    async def dispatch(self, request: Request, call_next) -> Response:
        span = trace.get_current_span()
        span_ctx = span.get_span_context()
        trace_id: str | None = (
            f"{span_ctx.trace_id:032x}"
            if span_ctx.trace_id != _INVALID_TRACE_ID
            else None
        )

        structlog.contextvars.bind_contextvars(
            trace_id=trace_id,
            **{
                "http.method": request.method,
                "http.path": request.url.path,
            },
        )
        try:
            return await call_next(request)
        finally:
            # Surgical unbind by explicit key name — preserves any other
            # contextvars bound elsewhere in the request chain (route
            # handlers, services). Cheaper than `clear_contextvars()`.
            structlog.contextvars.unbind_contextvars(
                "trace_id", "http.method", "http.path"
            )
```

We discard the `bind_contextvars` return value (it varies across structlog versions — dict in some, `None` in others) and unbind by explicit key name in the `finally` block. That is more surgical than `clear_contextvars()` (which would also wipe contextvars bound by upstream middleware or by `__main__.py`).

- [ ] **Step 4: Run the tests to verify they pass**

```bash
uv run pytest tests/unit/api/test_middleware.py -v
```

Expected: 3 tests pass.

- [ ] **Step 5: Run the lint + type gate**

```bash
uv run ruff check src/codeoracle/api/middleware.py tests/unit/api/test_middleware.py
uv run ty check src/codeoracle/api/middleware.py
```

Expected: both exit 0.

- [ ] **Step 6: Commit**

```bash
git add src/codeoracle/api/middleware.py tests/unit/api/test_middleware.py
git commit -m "feat(api): structured-log middleware binds OTel trace_id (Task 8)"
```

---

## Task 9: `app.py` — FastAPI app factory + lifespan

**Files:**
- Create: `src/codeoracle/api/app.py`

This is the composition root. The `lifespan` async context manager builds the engine, session factory, Valkey client, IndexingService, and AgentClient at process startup; drains them in reverse order at shutdown. The app factory registers the OTel server instrumentor, the structured-log middleware, the `/metrics` ASGI app, and the three route routers.

There's no unit test for `app.py` itself — its behaviour is exercised by Task 11's integration tests against a live HTTP surface. Unit-testing the lifespan in isolation would require either heavy mocking (which would just re-verify the wiring sequence) or building a parallel stub-app pattern that diverges from the real composition. Integration tests against a real DB + stub agent server are the right scope.

- [ ] **Step 1: Write `api/app.py`**

Create `src/codeoracle/api/app.py`:

```python
"""FastAPI app factory + lifespan for the API process.

Composition root (spec §3.3 rule 6 — processes are thin entrypoints,
no business logic). The lifespan owns:
  - SQLAlchemy AsyncEngine + async_sessionmaker
  - ValkeyQueueClient
  - IndexingService (Plan 5 — inline construction; matches Plan 5's
    5 existing construction sites, no composer needed per invariant 12)
  - AgentClient (Plan 11 Task 1 — wraps the agent's POST /v1/query)

Lifespan exit drains in reverse order (invariant 10):
  agent_client.aclose() → valkey_client.aclose() → engine.dispose()
Each in its own try/except so a failure earlier in the chain does
not block later cleanup.

The app factory registers:
  - FastAPIInstrumentor (server-side OTel — extracts incoming
    traceparent, sets active span context per request)
  - StructuredLogMiddleware (binds trace_id into structlog contextvars)
  - /metrics ASGI app (prometheus_client.make_asgi_app)
  - /v1/health, /v1/query, /v1/reindex routers
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from prometheus_client import REGISTRY, Gauge, make_asgi_app
from prometheus_client.core import GaugeMetricFamily
from prometheus_client.registry import Collector
from sqlalchemy.ext.asyncio import AsyncEngine

from codeoracle.adapters.agent.client import AgentClient
from codeoracle.adapters.valkey.client import ValkeyQueueClient
from codeoracle.adapters.valkey.config import ValkeyConfig
from codeoracle.api.middleware import StructuredLogMiddleware
from codeoracle.api.routes.health import router as health_router
from codeoracle.api.routes.query import router as query_router
from codeoracle.api.routes.reindex import router as reindex_router
from codeoracle.core import streams
from codeoracle.core.config import Settings, get_settings
from codeoracle.db.session import make_async_engine, make_async_session_factory
from codeoracle.services.indexing.service import IndexingService

logger = logging.getLogger(__name__)


class ConnectionPoolCollector(Collector):
    """On-scrape SQLAlchemy pool-saturation gauges for the API engine (NFR D0-3).

    The default /metrics counters (process CPU / FDs / GC) say nothing about DB
    connection-pool headroom — the failure mode that yields `FATAL: too many
    clients` under reindex fan-out + query load (Plan 3 connection-budget note).
    This reads the live pool counters at ``collect()`` time — synchronous
    QueuePool methods, so every scrape is exact with no background refresh — and
    labels them by ``process`` for a future multi-surface exposition.

    Scope: the API is the slice's only ``/metrics`` surface (MCP ships none by
    design — Plan 12 L206; worker ``/metrics`` is deferred — Plan 1A). When the
    workers gain an exposition surface, promote this to ``core/`` and register
    it per worker engine — the worker pools are the bulk of the budget (D0-2).
    """

    def __init__(self, engine: AsyncEngine, *, process: str) -> None:
        # AsyncEngine wraps a sync Engine whose `.pool` carries the live
        # checkout counters; AsyncAdaptedQueuePool exposes QueuePool's sync
        # introspection methods unchanged.
        self._pool = engine.sync_engine.pool
        self._process = process

    def collect(self) -> Iterator[GaugeMetricFamily]:
        for name, doc, value in (
            (
                "codeoracle_db_pool_checked_out_connections",
                "Connections currently checked out of the pool (in active use).",
                self._pool.checkedout(),
            ),
            (
                "codeoracle_db_pool_checked_in_connections",
                "Idle connections currently available in the pool.",
                self._pool.checkedin(),
            ),
            (
                "codeoracle_db_pool_overflow_connections",
                "Overflow connections open beyond pool_size (rises toward "
                "max_overflow as the pool saturates).",
                self._pool.overflow(),
            ),
        ):
            gauge = GaugeMetricFamily(name, doc, labels=["process"])
            gauge.add_metric([self._process], float(value))
            yield gauge


# --- NFR D4-4 — Valkey queue-depth + pending-backlog gauges ------------------
# D0-3's pool collector reads sync QueuePool methods on scrape. Stream depth
# (XLEN) and consumer-group pending-list size (XPENDING) are ASYNC reads on the
# Valkey client and so cannot run inside a sync ``Collector.collect()`` — they
# are refreshed by a background task instead (started in the lifespan, cancelled
# on shutdown). These module-level Gauges are created once per process, so
# re-creating the app in one process (the function-scoped ``api_app`` fixture)
# never raises a "Duplicated timeseries" clash — which is why, unlike D0-3's
# per-lifespan collector, they need no unregister.
#
# The ledger's third D4-4 gauge — trace-ingest cursor-lag (``now − cursor_upper``)
# — is intentionally NOT exported here: the cursor is PG state owned by the
# traces_ingest worker (Plan 7), and surfacing it API-side would force ``api/``
# to bypass the service layer into ``db/repositories`` — violating its
# thin-entrypoint role (Plan 11 invariant 1). It rides the deferred worker
# ``/metrics`` surface (Plan 1A "Plan 11+"); Plan 7 L9 tracks that one
# remaining piece.
VALKEY_STREAM_LENGTH = Gauge(
    "codeoracle_valkey_stream_length",
    "Current entry count (XLEN) of a Valkey worker stream — rises when "
    "producers outrun consumers (NFR D4-2 backpressure visibility).",
    ["stream"],
)
VALKEY_PENDING_ENTRIES = Gauge(
    "codeoracle_valkey_pending_entries",
    "Size of a consumer group's pending-entries list (XPENDING) — rises when "
    "in-flight messages are not being ACKed/reclaimed (NFR D4-1 visibility).",
    ["stream", "group"],
)

# (stream, consumer-group) pairs the refresher observes. The names come from
# the ``core.streams`` registry (imported, never hardcoded as literals); only
# the pairing is expressed here, mirroring each WorkerConsumer's (stream,
# group) ClassVar binding (Plan 5/6). Lifting the pairing into ``core.streams``
# as a shared mapping is a tracked follow-up; for the slice's fixed five-stream
# set the local tuple is the proportionate, low-risk form.
_MONITORED_QUEUES: tuple[tuple[str, str], ...] = (
    (streams.STREAM_INDEXING_JOERN, streams.GROUP_JOERN_INDEXER),
    (streams.STREAM_INDEXING_SCIP, streams.GROUP_SCIP_INDEXER),
    (streams.STREAM_INDEXING_OPENGREP, streams.GROUP_OPENGREP_RUNNER),
    (streams.STREAM_INDEXING_EMBED, streams.GROUP_EMBED_INDEXER),
    (streams.STREAM_REINDEX_REQUESTS, streams.GROUP_REINDEX_ORCHESTRATOR),
)

# Refresh cadence — aligned to Prometheus' default 15s scrape_interval (Plan 1A)
# so each scrape sees data at most one interval stale. The API has no worker
# poll loop to piggyback on (the ledger's "per poll/tick"), so a fixed-interval
# refresher is the API-side equivalent.
_QUEUE_METRICS_REFRESH_SECONDS: float = 15.0


async def _refresh_queue_gauges(queue_client: ValkeyQueueClient) -> None:
    """One refresh pass — set every monitored queue's length + pending gauge.

    Factored out of the loop so tests can drive a single deterministic pass
    without timing. Both reads are side-effect-free (XLEN of a missing stream
    and XPENDING of a not-yet-created group both resolve to 0), so this is safe
    to call before any worker has bootstrapped its group.
    """
    for stream, group in _MONITORED_QUEUES:
        VALKEY_STREAM_LENGTH.labels(stream=stream).set(
            await queue_client.stream_length(stream)
        )
        VALKEY_PENDING_ENTRIES.labels(stream=stream, group=group).set(
            await queue_client.pending_count(stream, group)
        )


async def _run_queue_metrics_refresher(queue_client: ValkeyQueueClient) -> None:
    """Background loop refreshing the queue gauges until cancelled.

    A per-cycle failure (e.g. a transient Valkey outage) is logged and
    swallowed so the gauges degrade to stale-not-crashed and the next cycle
    recovers; ``CancelledError`` (shutdown) propagates out of the ``sleep`` and
    breaks the loop.
    """
    while True:
        try:
            await _refresh_queue_gauges(queue_client)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "queue_metrics_refresh_failed", extra={"error": str(exc)}
            )
        await asyncio.sleep(_QUEUE_METRICS_REFRESH_SECONDS)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Process startup + shutdown.

    Startup order is dependency-driven:
      1. Settings (already constructed by caller; loaded into app.state)
      2. AsyncEngine + session_factory (the DB layer)
      3. ValkeyQueueClient (Plan 5 dep — IndexingService needs the queue)
      4. IndexingService (composes engine + session_factory + queue +
         settings.repo_root + settings.api.lock_acquisition_timeout_seconds)
      5. AgentClient (the API → agent HTTP wrapper)

    Shutdown drains in reverse order (invariant 10). Each step is
    wrapped in try/except so a partial failure doesn't block the
    remaining drains.
    """
    settings: Settings = app.state.settings
    logger.info("api_lifespan_startup_begin")
    # NFR D4-4 — declared before any startup step so the shutdown path can
    # cancel it even if startup fails before the task is created.
    queue_metrics_task: asyncio.Task[None] | None = None

    # 1. Engine + session_factory ---------------------------------
    # Build via Plan 3's make_async_engine() (db/session.py), never
    # create_async_engine directly: importing that module registers the AGE
    # connect listener so this process's connections can run agtype queries
    # (D2-7), and pool sizing flows from CODEORACLE_DB__POOL_SIZE / __MAX_OVERFLOW /
    # __POOL_TIMEOUT through DBConfig (D2-3) instead of being hard-pinned here.
    # The factory is zero-arg and reads get_settings(), NOT the injected
    # app.state.settings — the platform-wide composition-root convention (cf.
    # Plan 10's agent app, L3545). In production the two are the same Settings;
    # the integration fixture makes them agree by pointing both at the
    # testcontainer through CODEORACLE_DB__* env (see tests/integration/api/conftest.py).
    engine = make_async_engine()
    session_factory = make_async_session_factory(engine)

    # NFR D0-3 — expose this engine's pool-saturation gauges at /metrics.
    # Registered against the default REGISTRY that make_asgi_app() serves;
    # unregistered on shutdown so re-creating the app in one process (the
    # function-scoped `api_app` integration fixture) never raises a
    # "Duplicated timeseries" clash on the second construction.
    pool_collector = ConnectionPoolCollector(engine, process="api")
    REGISTRY.register(pool_collector)

    # 2. Valkey ---------------------------------------------------
    # ValkeyConfig is a standalone BaseSettings (Plan 5 L441) — NOT
    # nested under Settings. It reads CODEORACLE_VALKEY__URL etc. from the
    # environment directly. Zero-arg construction matches Plan 5's
    # precedent at L2132 (`valkey_config or ValkeyConfig()`) and
    # L7016 (`ValkeyQueueClient(ValkeyConfig())`). `type: ignore[call-arg]`
    # is required because ValkeyConfig.url is a required field with
    # no Python default (Plan 5 L444 class body — "All fields are
    # required (no defaults) so misconfiguration fails fast at process
    # start") — ty sees the missing constructor arg; BaseSettings
    # populates it from env at runtime.
    valkey_client = ValkeyQueueClient(ValkeyConfig())  # type: ignore[call-arg]
    await valkey_client.start()

    # NFR D4-4 — refresh the Valkey queue-depth / pending-backlog gauges in the
    # background (XLEN/XPENDING are async, so they can't ride D0-3's sync
    # on-scrape collector). Cancelled first on shutdown, before the client it
    # reads is closed.
    queue_metrics_task = asyncio.create_task(
        _run_queue_metrics_refresher(valkey_client)
    )

    # 3. IndexingService (inline — invariant 12) ------------------
    indexing_service = IndexingService(
        engine=engine,
        db_session_factory=session_factory,
        queue_client=valkey_client,
        repo_root=settings.repo_root,
        extractor_kinds=("joern",),  # Plan 6 extends — slice ships joern only
        advisory_lock_timeout_seconds=settings.api.lock_acquisition_timeout_seconds,
    )

    # 4. AgentClient ----------------------------------------------
    agent_client = AgentClient(
        base_url=settings.api.agent_base_url,
        request_timeout_seconds=settings.api.agent_request_timeout_seconds,
        shared_token=settings.agent.shared_token,
    )

    # Stash on app.state for the deps.py helpers to read.
    app.state.engine = engine
    app.state.session_factory = session_factory
    app.state.valkey_client = valkey_client
    app.state.indexing_service = indexing_service
    app.state.agent_client = agent_client

    logger.info("api_lifespan_startup_done")

    try:
        yield
    finally:
        logger.info("api_lifespan_shutdown_begin")
        # NFR D4-4 — stop the queue-metrics refresher FIRST, before the Valkey
        # client it reads is drained below. Guarded because startup may have
        # failed before the task was created.
        if queue_metrics_task is not None:
            queue_metrics_task.cancel()
            try:
                await queue_metrics_task
            except asyncio.CancelledError:
                pass
        # Stop exposing the pool gauges before draining the engine (NFR D0-3)
        # so the next app built in this process can re-register cleanly.
        try:
            REGISTRY.unregister(pool_collector)
        except KeyError:
            pass  # never registered / already removed — idempotent shutdown
        # Drain in reverse order — each in its own try/except so an
        # earlier failure doesn't block later cleanup (invariant 10).
        try:
            await agent_client.aclose()
        except Exception as exc:  # noqa: BLE001
            logger.warning("api_shutdown_agent_aclose_failed", extra={"error": str(exc)})
        try:
            await valkey_client.aclose()
        except Exception as exc:  # noqa: BLE001
            logger.warning("api_shutdown_valkey_aclose_failed", extra={"error": str(exc)})
        try:
            await engine.dispose()
        except Exception as exc:  # noqa: BLE001
            logger.warning("api_shutdown_engine_dispose_failed", extra={"error": str(exc)})
        logger.info("api_lifespan_shutdown_done")


def create_app(settings: Settings | None = None) -> FastAPI:
    """Build the FastAPI app. Caller is responsible for `init_telemetry()`
    and `configure_logging(settings)` BEFORE invoking create_app — see
    `__main__.py` (Task 10).

    Args:
        settings: Override the process-wide Settings (test only). When
            None, reads `get_settings()` (the LRU-cached singleton).
    """
    s = settings or get_settings()

    app = FastAPI(
        title="CodeOracle API",
        version="0.1.0-slice",
        lifespan=lifespan,
    )
    app.state.settings = s

    # OTel server instrumentation — extracts incoming traceparent and
    # sets the active span context per request. Plan 1A's init_telemetry()
    # must have installed the TracerProvider already; if it hasn't, this
    # call is a no-op and trace_ids are 0 (the middleware handles that).
    FastAPIInstrumentor.instrument_app(app)

    # Structured-log middleware AFTER the OTel instrumentor so the
    # active span is populated before we read it.
    app.add_middleware(StructuredLogMiddleware)

    # Mount /metrics — prometheus_client exposes its own ASGI app.
    # Plan 1A's prometheus scrape job points at this path.
    app.mount("/metrics", make_asgi_app())

    # Route routers
    app.include_router(health_router, prefix="/v1")
    app.include_router(query_router, prefix="/v1")
    app.include_router(reindex_router, prefix="/v1")

    return app
```

There is NO module-level `app = create_app()` — production startup goes through `__main__.py` (Task 10), which calls `init_telemetry()` and `configure_logging(settings)` BEFORE `create_app(settings)`. Skipping that ordering would lose OTel server instrumentation and structured-log formatting on early request traffic. Test code calls `create_app(test_settings)` directly with an explicit fixture-built Settings.

- [ ] **Step 2: Sanity-check the app loads (no test — just import)**

```bash
uv run python -c "from codeoracle.api.app import create_app; app = create_app(); print('app loaded:', app.title)"
```

Expected: `app loaded: CodeOracle API` (assuming all the Plan 3 + Plan 5 + Plan 10 prereqs are present in the environment — DB connectable, env vars set). If Settings construction fails because `CODEORACLE_EMBEDDING__*` are missing, set them in `.env` per Plan 3 Step 1 of Task 3.

- [ ] **Step 3: Run the lint + type gate**

```bash
uv run ruff check src/codeoracle/api/app.py
uv run ty check src/codeoracle/api/app.py
```

Expected: both exit 0.

- [ ] **Step 4: Commit**

```bash
git add src/codeoracle/api/app.py
git commit -m "feat(api): FastAPI app factory + lifespan (Task 9)"
```

---

## Task 10: `__main__.py` — uvicorn entrypoint

**Files:**
- Create: `src/codeoracle/api/__main__.py`

The entrypoint Plan 1A's Dockerfile invokes for the API variant (`python -m codeoracle.api`). It must, in this order: (1) call `configure_logging(settings)` so structlog renders correctly before the first log line, (2) call `init_telemetry()` so the OTel SDK installs the TracerProvider, (3) build the app via `create_app(settings)`, (4) launch uvicorn at `settings.api.host:port` using the framework default `timeout_keep_alive` (invariant 11 amended 2026-05-26 — the reindex route is now async and returns in <50ms; no long-running request to size against).

- [ ] **Step 1: Write `api/__main__.py`**

Create `src/codeoracle/api/__main__.py`:

```python
"""API process entrypoint — `python -m codeoracle.api`.

Plan 1A's Dockerfile entrypoint shim invokes this module for the API
variant (`api -> Plan 11` per Plan 1A's variant table). At slice scope
the API runs as a single uvicorn process (workers=1) bound to the
host/port from `settings.api`.

Startup order is invariant — every Plan 1A-rooted process module uses
this exact sequence:
  1. configure_logging(settings) — install structlog renderer
  2. init_telemetry()             — install OTel TracerProvider + exporter
  3. create_app(settings)         — build FastAPI app + lifespan
  4. uvicorn.run(app, host, port)

Amended 2026-05-26: reindex is async (invariant 11 amended); the route
returns in <50ms after enqueueing, so uvicorn's default
`timeout_keep_alive` (5s) is sufficient. The pre-amendment sizing
(`reindex_timeout_seconds + 30s headroom`) is no longer needed.
"""

from __future__ import annotations

import uvicorn

from codeoracle.api.app import create_app
from codeoracle.core.config import get_settings
from codeoracle.core.logging import configure_logging
from codeoracle.core.telemetry import init_telemetry


def main() -> None:
    settings = get_settings()
    configure_logging(settings)
    init_telemetry()
    app = create_app(settings)

    uvicorn.run(
        app,
        host=settings.api.host,
        port=settings.api.port,
        # log_config=None disables uvicorn's own logging setup so
        # configure_logging() is the single source of truth for log
        # formatting (Plan 3's structlog pipeline).
        log_config=None,
    )


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Sanity-check the module imports**

```bash
uv run python -c "from codeoracle.api import __main__; print('main loads')"
```

Expected: `main loads`.

- [ ] **Step 3: Run the lint + type gate**

```bash
uv run ruff check src/codeoracle/api/__main__.py
uv run ty check src/codeoracle/api/__main__.py
```

Expected: both exit 0.

- [ ] **Step 4: Commit**

```bash
git add src/codeoracle/api/__main__.py
git commit -m "feat(api): uvicorn entrypoint (Task 10)"
```

---

## Task 11: Integration tests + preflight smoke

**Files:**
- Create: `tests/integration/api/conftest.py`
- Create: `tests/integration/api/test_health_endpoint.py`
- Create: `tests/integration/api/test_query_endpoint.py`
- Create: `tests/integration/api/test_reindex_endpoint.py`
- Create: `tests/integration/api/test_metrics_endpoint.py`
- Modify: `infra/scripts/preflight.sh` (append a `/v1/health` smoke step)

Integration tests run the full API process against a real Postgres + Valkey (testcontainers) and a stub agent server (httpx mock-transport ASGI app). They pin the §8.5 acceptance gates landing in Plan 11.

- [ ] **Step 1: Write `tests/integration/api/conftest.py`**

Create `tests/integration/api/conftest.py`:

```python
"""Integration test fixtures for the API process.

Pattern reuses Plan 5's `tests/integration/conftest.py` (real
PostgresContainer + Valkey RedisContainer), augmented with a
stub-agent FastAPI server that the AgentClient calls. The stub agent
runs in the SAME process as the test, on a different uvicorn port,
because spinning a real Plan 10 agent process would pull in LLM + DB
+ vector + sourcebot — too much for a Plan 11 contract test.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
import uvicorn
from fastapi import FastAPI

from codeoracle.api.app import create_app
from codeoracle.core.config import Settings, get_settings


@pytest.fixture(scope="session")
def stub_agent_app() -> FastAPI:
    """A minimal FastAPI app that mimics Plan 10's POST /v1/query +
    GET /v1/health. Returns a canned QueryResult; tests verify the
    AgentClient → API round-trip not the LLM behind it.
    """
    from codeoracle.models.evidence import (
        Citation,
        Claim,
        EvidenceHandle,
        HandleType,
        Verdict,
        VerdictStatus,
    )
    from codeoracle.models.health import HealthResponse
    from codeoracle.models.query import Query, QueryResult

    app = FastAPI(title="Stub Agent")

    @app.post("/v1/query", response_model=QueryResult)
    async def stub_query(body: Query) -> QueryResult:
        return QueryResult.proven(
            trace_id="00000000000000000000000000000001",
            claim=Claim(
                statement=f"stub answer for: {body.symbol}",
                citations=[
                    Citation(
                        handle=EvidenceHandle(
                            type=HandleType.CODE_SPAN,
                            locator="file://src/codeoracle/core/security.py:1-10",
                            snapshot_sha=body.snapshot_sha or "stub_sha",
                        ),
                        excerpt="def hash_password(raw: str) -> str:",
                        source_trust=9,
                    )
                ],
                confidence=0.9,
            ),
            verdict=Verdict(
                status=VerdictStatus.PROVEN,
                reason="All citations resolved and match cached excerpts.",
                resolved_citations=[],
            ),
        )

    @app.get("/v1/health", response_model=HealthResponse)
    async def stub_health() -> HealthResponse:
        return HealthResponse(status="ok", db="ok")

    return app


@pytest.fixture(scope="session")
def stub_agent_port() -> int:
    """An unused port for the stub agent. The session-scope ensures
    one stub agent per test session.
    """
    import socket

    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest_asyncio.fixture(scope="session")
async def stub_agent_server(stub_agent_app, stub_agent_port) -> AsyncIterator[None]:
    """Run the stub-agent uvicorn server in a background task for the
    whole session.
    """
    config = uvicorn.Config(
        stub_agent_app,
        host="127.0.0.1",
        port=stub_agent_port,
        log_level="warning",
    )
    server = uvicorn.Server(config)
    task = asyncio.create_task(server.serve())
    # Wait for the server to be ready
    for _ in range(50):
        if server.started:
            break
        await asyncio.sleep(0.05)
    yield
    server.should_exit = True
    await task


@pytest_asyncio.fixture
async def api_settings(
    stub_agent_port,
    async_engine,
    monkeypatch,
) -> Settings:
    """Build a Settings instance pointing at the stub agent + the
    testcontainer DB. `async_engine` is Plan 3's existing session-scoped
    integration fixture (an AsyncEngine bound to the bootstrapped test DB);
    depending on it guarantees the schema exists and the container is
    reachable before the API lifespan opens its own pool, and exposes the
    live DB URL components via `async_engine.url`.

    Uses `monkeypatch.setenv` (auto-reverts after the test exits)
    so test-process environment is not permanently mutated.
    """
    monkeypatch.setenv("CODEORACLE_EMBEDDING__MODEL", "text-embedding-3-small")
    monkeypatch.setenv("CODEORACLE_EMBEDDING__DIM", "1536")
    monkeypatch.setenv("CODEORACLE_API__AGENT_BASE_URL", f"http://127.0.0.1:{stub_agent_port}")
    monkeypatch.setenv("CODEORACLE_API__AGENT_REQUEST_TIMEOUT_SECONDS", "5.0")
    monkeypatch.setenv("CODEORACLE_API__LOCK_ACQUISITION_TIMEOUT_SECONDS", "5.0")
    # Point Settings.db at the same DB Plan 3's `async_engine` fixture
    # bootstrapped, by threading its URL components into the CODEORACLE_DB__* fields.
    # CODEORACLE_DB__ASYNC_URL is NOT a settable field — `async_url()` is a computed
    # method, so setting it is silently ignored; the URL must flow through the
    # host/port/user/password/database fields async_url() reads back. The
    # lifespan builds its engine via make_async_engine() → get_settings(), so
    # clear the cached singleton to force a fresh read of this env (Plan 3
    # conftest L605/607/2723 also bust it; this explicit clear matches the
    # corpus after-setenv convention). `.password` attribute access returns the
    # real secret — only str(url)/repr mask it.
    db_url = async_engine.url
    monkeypatch.setenv("CODEORACLE_DB__HOST", db_url.host or "127.0.0.1")
    monkeypatch.setenv("CODEORACLE_DB__PORT", str(db_url.port or 5432))
    monkeypatch.setenv("CODEORACLE_DB__USER", db_url.username or "codeoracle")
    monkeypatch.setenv("CODEORACLE_DB__PASSWORD", db_url.password or "codeoracle")
    monkeypatch.setenv("CODEORACLE_DB__DATABASE", db_url.database or "codeoracle")
    get_settings.cache_clear()
    return Settings()


@pytest_asyncio.fixture
async def api_app(api_settings, stub_agent_server):
    """Build the API app with the stub-agent-pointing settings. The
    lifespan runs against the testcontainer DB + a real Valkey.
    """
    app = create_app(api_settings)
    async with app.router.lifespan_context(app):
        yield app
```

The integration suite depends on Plan 3's existing `tests/integration/conftest.py` providing the `async_engine` fixture (session-scoped `AsyncEngine` over the bootstrapped test DB; `api_settings` reads `async_engine.url` for the DB components) and a Valkey container. If those don't exist yet (Plan 5 added Valkey), reuse Plan 5's pattern verbatim.

- [ ] **Step 2: Write `tests/integration/api/test_health_endpoint.py`**

Create:

```python
"""§8.5 acceptance gate — /v1/health (db-ping liveness).

Spec §8.5 row: BL-HC01 (or whichever ID is assigned during Task #18 — D4
audit) — "API /v1/health returns ok with db=ok when DB is reachable".
"""

from __future__ import annotations

import httpx
import pytest


@pytest.mark.integration
async def test_health_returns_ok_with_real_db(api_app):
    """Full process: live DB ping returns 200 + {status: ok, db: ok}."""
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=api_app),
        base_url="http://test",
    ) as c:
        r = await c.get("/v1/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok", "db": "ok"}
```

- [ ] **Step 3: Write `tests/integration/api/test_query_endpoint.py`**

Create:

```python
"""§8.5 acceptance gate — /v1/query (agent delegation).

Spec §8.5 row: BL-QY01 (D4-audit-assigned) — "POST /v1/query returns
QueryResult by delegating to the agent". Plus invariant-5 503/502
mapping tests.
"""

from __future__ import annotations

import httpx
import pytest


@pytest.mark.integration
async def test_query_round_trip_to_stub_agent(api_app):
    """The API forwards the request to the stub agent and returns the
    stub's canned answer raw (no envelope).
    """
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=api_app),
        base_url="http://test",
    ) as c:
        r = await c.post(
            "/v1/query",
            json={"symbol": "codeoracle.core.security.hash_password", "snapshot_sha": None},
        )
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "COMPLETE"
    assert (
        "stub answer for: codeoracle.core.security.hash_password"
        in body["claim"]["statement"]
    )
    assert body["claim"]["citations"][0]["handle"]["snapshot_sha"] == "stub_sha"
    # No envelope keys (invariant 3)
    assert "data" not in body


@pytest.mark.integration
async def test_query_503_when_agent_port_changes(api_app, monkeypatch):
    """Point the AgentClient at a port nothing listens on → 503.
    Validates invariant 5 (transport-layer failure → 503).
    """
    # Mutate app.state.agent_client to point at a closed port. This
    # bypasses the lifespan and tests the route's error mapping
    # directly against the real httpx transport.
    from codeoracle.adapters.agent.client import AgentClient

    api_app.state.agent_client = AgentClient(
        base_url="http://127.0.0.1:1",  # port 1 — almost always closed
        request_timeout_seconds=1.0,
        shared_token=None,
    )
    try:
        async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=api_app),
        base_url="http://test",
    ) as c:
            r = await c.post(
                "/v1/query",
                json={"symbol": "codeoracle.core.security.hash_password", "snapshot_sha": None},
            )
        assert r.status_code == 503
        assert r.json() == {"detail": "agent unreachable"}
    finally:
        await api_app.state.agent_client.aclose()
```

- [ ] **Step 4: Write `tests/integration/api/test_reindex_endpoint.py`**

Create:

```python
"""§8.5 acceptance gate — /v1/reindex (snapshot lifecycle).

Amended 2026-05-26: the route is async (POST 202 + Snapshot(PENDING);
poll GET /v1/reindex/{sha} until APPLIED). The integration test starts
a ReindexOrchestratorConsumer (Plan 5 Task 13a) in-process as a
background asyncio task — same fixture pattern Plan 5 Task 12 uses for
its joern_indexer integration test.

Spec §8.5 row: BL-RX01 (D4-audit-assigned) — amended to "POST /v1/reindex
returns 202 + PENDING Snapshot; polling GET /v1/reindex/{sha} transitions
to APPLIED within the cold-3K-LOC budget (3 min p95)." Idempotent retry
also covered.
"""

from __future__ import annotations

import asyncio
import time

import httpx
import pytest
import pytest_asyncio

from codeoracle.api.deps import get_indexing_service
from codeoracle.models.snapshot import Snapshot, SnapshotStatus
from codeoracle.services.indexing.service import IndexingService


_POLL_INTERVAL_SECONDS = 0.5
_POLL_BUDGET_SECONDS = 180.0


@pytest_asyncio.fixture
async def shared_indexing_service(api_app, tmp_git_repo) -> IndexingService:
    """Build a single IndexingService rooted at tmp_git_repo and install
    it as the API route's dependency_override. The SAME instance is
    consumed by the ReindexOrchestratorConsumer fixture below — both
    share the engine, queue_client, and repo_root so the producer's
    XADD is visible to the consumer's XREADGROUP.

    `tmp_git_repo` is Plan 5's session-scoped fixture (declared in
    `tests/integration/conftest.py` per Plan 5 Task 12 precedent);
    pytest auto-discovers it from `tests/integration/api/`.

    Override is cleared in teardown. Async fixture for consistency with
    `api_app` + `api_settings` (other async fixtures in conftest); makes
    it forward-compatible if setup or teardown ever needs to await.
    """
    settings = api_app.state.settings
    svc = IndexingService(
        engine=api_app.state.engine,
        db_session_factory=api_app.state.session_factory,
        queue_client=api_app.state.valkey_client,
        repo_root=str(tmp_git_repo),
        extractor_kinds=("joern",),
        advisory_lock_timeout_seconds=settings.api.lock_acquisition_timeout_seconds,
    )
    api_app.dependency_overrides[get_indexing_service] = lambda: svc
    yield svc
    api_app.dependency_overrides.pop(get_indexing_service, None)


@pytest_asyncio.fixture
async def reindex_consumer(
    api_app, shared_indexing_service, tmp_git_repo, monkeypatch,
):
    """Start a ReindexOrchestratorConsumer in-process for the duration
    of the test. The consumer shares the api_app's queue_client and the
    shared_indexing_service — so a POST /v1/reindex from the test client
    XADDs a message the consumer XREADGROUPs on its next poll.

    `RetryPolicy()` constructs zero-arg because Plan 5 Task 3
    (`src/codeoracle/workers/_base/message.py`) declares it as
    `@dataclass(frozen=True)` with its single field defaulted
    (`max_attempts=5`); if that default is removed later, this test
    will fail at construction with a clear TypeError, which is the right
    surface for the contract change.

    Teardown signals stop_event and awaits the consumer task to drain.
    """
    from codeoracle.workers.reindex_orchestrator.consumer import (
        ReindexOrchestratorConsumer,
    )
    from codeoracle.workers._base.message import RetryPolicy

    monkeypatch.chdir(tmp_git_repo)
    stop_event = asyncio.Event()
    consumer = ReindexOrchestratorConsumer(
        queue_client=api_app.state.valkey_client,
        retry_policy=RetryPolicy(),
        consumer_name="reindex-orchestrator-test",
        stop_event=stop_event,
        indexing_service=shared_indexing_service,
    )
    consumer_task = asyncio.create_task(consumer.run())
    try:
        yield consumer
    finally:
        stop_event.set()
        try:
            await asyncio.wait_for(consumer_task, timeout=5.0)
        except asyncio.TimeoutError:
            consumer_task.cancel()


async def _drive_reindex_to_applied(
    api_client: httpx.AsyncClient, *, base_sha: str | None,
) -> Snapshot:
    """POST /v1/reindex then poll GET until status terminal."""
    post = await api_client.post("/v1/reindex", json={"base_sha": base_sha})
    assert post.status_code == 202
    enqueued = Snapshot.model_validate(post.json())
    assert enqueued.status in {SnapshotStatus.PENDING, SnapshotStatus.APPLYING}
    snapshot_sha = enqueued.sha

    deadline = time.monotonic() + _POLL_BUDGET_SECONDS
    while time.monotonic() < deadline:
        get_response = await api_client.get(f"/v1/reindex/{snapshot_sha}")
        assert get_response.status_code == 200
        current = Snapshot.model_validate(get_response.json())
        if current.status in {SnapshotStatus.APPLIED, SnapshotStatus.FAILED}:
            return current
        await asyncio.sleep(_POLL_INTERVAL_SECONDS)
    raise AssertionError(
        f"Reindex {snapshot_sha!r} did not converge within {_POLL_BUDGET_SECONDS}s"
    )


@pytest.mark.integration
async def test_reindex_endpoint_enqueues_and_polls_to_applied(
    api_app, tmp_git_repo, reindex_consumer,
):
    """POST returns 202 + PENDING Snapshot; polling reaches APPLIED.

    `tmp_git_repo` is Plan 5's fixture — a tiny git repo with files Joern
    can parse. The `reindex_consumer` fixture starts the worker in-process.
    """
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=api_app),
        base_url="http://test",
    ) as c:
        final = await _drive_reindex_to_applied(c, base_sha=None)

    assert final.status == SnapshotStatus.APPLIED, (
        f"Expected APPLIED, got {final.status!r} with error={final.error!r}"
    )
    # Spec §8.5 contract: response.sha is the new snapshot.
    assert final.sha
    assert final.error is None


@pytest.mark.integration
async def test_reindex_endpoint_idempotent_on_duplicate_post(
    api_app, tmp_git_repo, shared_indexing_service,
):
    """Two POSTs with the same tree state return the same snapshot_sha.
    Does NOT require a running consumer — idempotence is producer-side
    (Plan 5 IndexingService.enqueue_reindex catches SnapshotConflictError
    and returns the existing PENDING row).
    """
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=api_app),
        base_url="http://test",
    ) as c:
        r1 = await c.post("/v1/reindex", json={"base_sha": None})
        r2 = await c.post("/v1/reindex", json={"base_sha": None})
    assert r1.status_code == 202
    assert r2.status_code == 202
    assert r1.json()["sha"] == r2.json()["sha"]
```

- [ ] **Step 5: Write `tests/integration/api/test_metrics_endpoint.py`**

Create:

```python
"""/metrics endpoint exposes Prometheus counters.

Spec §8.5 — Plan 1A's prometheus scrape job pings /metrics; Plan 11
ships the endpoint mounted via prometheus_client.make_asgi_app().
"""

from __future__ import annotations

import httpx
import pytest

from codeoracle.api.app import _refresh_queue_gauges
from codeoracle.core import streams


@pytest.mark.integration
async def test_metrics_endpoint_returns_text(api_app):
    """/metrics returns text/plain; version=0.0.4 (Prometheus exposition
    format) with at least one default counter line.
    """
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=api_app),
        base_url="http://test",
    ) as c:
        r = await c.get("/metrics")
    assert r.status_code == 200
    assert "text/plain" in r.headers.get("content-type", "")
    # Process-level counters that prometheus_client emits by default
    # (process_start_time_seconds, process_cpu_seconds_total, etc.)
    assert "process_" in r.text
    # NFR D0-3 — the connection-pool saturation gauges registered for the API
    # engine in the lifespan are exposed alongside the default counters.
    assert "codeoracle_db_pool_checked_out_connections" in r.text
    assert 'process="api"' in r.text


@pytest.mark.integration
async def test_metrics_endpoint_exposes_queue_gauges(api_app):
    """NFR D4-4 — the Valkey queue-depth + pending-backlog gauges appear at
    /metrics after a refresh.

    The gauges are populated by a background refresher (async XLEN/XPENDING
    can't ride D0-3's sync on-scrape collector), so we drive one deterministic
    pass directly rather than racing the lifespan's first tick. The refresh
    runs against the live Valkey testcontainer with empty streams/groups (no
    worker ran), so it exercises the missing-stream→0 (XLEN) and NOGROUP→0
    (XPENDING) paths and every gauge resolves to 0 without error.
    """
    await _refresh_queue_gauges(api_app.state.valkey_client)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=api_app),
        base_url="http://test",
    ) as c:
        r = await c.get("/metrics")
    assert r.status_code == 200
    # Both gauge families are exposed, labelled per stream / per (stream, group).
    assert "codeoracle_valkey_stream_length" in r.text
    assert "codeoracle_valkey_pending_entries" in r.text
    assert f'stream="{streams.STREAM_INDEXING_JOERN}"' in r.text
    assert f'group="{streams.GROUP_REINDEX_ORCHESTRATOR}"' in r.text
    # The refresh must be side-effect-free: `pending_count` maps NOGROUP to 0
    # rather than bootstrapping the group (which `_ensure_group` would record in
    # `_ensured_groups`). No read/reclaim ran in this test, so an empty bootstrap
    # set proves the metrics scrape created no consumer group as a side effect.
    assert not api_app.state.valkey_client._ensured_groups
```

- [ ] **Step 6: Modify `infra/scripts/preflight.sh` — append /v1/health smoke**

Plan 2's `preflight.sh` runs after the compose stack is up; Plan 11 adds an API smoke step to it. Append (after the existing OTel collector / Tempo / Prometheus smoke lines):

```bash
# Plan 11: API /v1/health smoke
echo "[preflight] api /v1/health"
curl -fsS --max-time 5 "http://127.0.0.1:${CODEORACLE_API_PORT:-8080}/v1/health" \
    | grep -q '"status":"ok"' \
    || { echo "  api /v1/health FAIL"; exit 1; }
echo "  api /v1/health PASS"
```

The script reads `CODEORACLE_API_PORT` to keep its config-independence (mirrors Plan 2's other smoke lines).

- [ ] **Step 7: Run the integration suite**

```bash
uv run pytest tests/integration/api/ -v -m integration
```

Expected: 7 tests pass (1 health + 2 query + 2 reindex + 2 metrics). The reindex tests require Plan 5's `tmp_git_repo` fixture — if missing, mark `@pytest.mark.skip("requires Plan 5 fixture")` and surface as a Phase-K open finding.

- [ ] **Step 8: Run the preflight smoke locally**

```bash
docker compose -f infra/compose/docker-compose.yml up -d
# Wait for services
sleep 10
# Start the API in another terminal: uv run python -m codeoracle.api
# Then:
bash infra/scripts/preflight.sh
```

Expected: every smoke step (Plan 1A's + Plan 11's new one) reports PASS.

- [ ] **Step 9: Commit**

```bash
git add tests/integration/api/conftest.py \
        tests/integration/api/test_health_endpoint.py \
        tests/integration/api/test_query_endpoint.py \
        tests/integration/api/test_reindex_endpoint.py \
        tests/integration/api/test_metrics_endpoint.py \
        infra/scripts/preflight.sh
git commit -m "test(api): integration suite + preflight smoke (Task 11)"
```

---

## Task 12: `pyproject.toml` — deps + script entry

**Files:**
- Modify: `pyproject.toml`

Promote `httpx` from test-only (Plan 10) to a runtime dep (Plan 11 needs it for `AgentClient`). Add `prometheus-client` (NEW). Add `respx` (test-only). Register the `codeoracle-api` script entry point.

- [ ] **Step 1: Modify `[project.dependencies]`**

Locate the current `[project.dependencies]` block (Plan 10 has fastapi + uvicorn + opentelemetry-instrumentation-fastapi; httpx is in `[dependency-groups].test`). Append the two new runtime deps and move httpx to runtime:

```toml
[project.dependencies]
# … existing entries from Plan 1 + Plan 1A + Plan 3 + Plan 4 + Plan 5 + Plan 8 + Plan 10 …
httpx = "0.27.*"           # Plan 11 — promoted from test-only (Plan 10) to runtime (AgentClient)
prometheus-client = "0.21.*"  # Plan 11 — /metrics endpoint exposition
```

And in `[dependency-groups].test`, add `respx`:

```toml
[dependency-groups]
test = [
    # … existing test deps from Plan 10 …
    "respx==0.21.*",       # Plan 11 — httpx mocking for AgentClient unit tests
]
```

- [ ] **Step 2: Add the script entry point**

In `[project.scripts]`:

```toml
[project.scripts]
# … existing entries (codeoracle-agent from Plan 10, codeoracle from Plan 5, …) …
codeoracle-api = "codeoracle.api.__main__:main"
```

- [ ] **Step 3: Re-lock**

```bash
uv lock --upgrade-package httpx --upgrade-package prometheus-client --upgrade-package respx
uv sync --all-groups
```

Expected: `uv lock` updates `uv.lock` with the resolved versions. `uv sync` installs them into the project venv.

- [ ] **Step 4: Verify the script entry resolves**

```bash
uv run codeoracle-api --help 2>&1 | head -5
```

Expected: `codeoracle-api` runs (and probably hangs trying to bind 8080 — Ctrl-C is the expected exit). If it errors with `ModuleNotFoundError`, the entry-point path is wrong; double-check `codeoracle.api.__main__:main`.

- [ ] **Step 5: Commit**

```bash
git add pyproject.toml uv.lock
git commit -m "build(api): runtime deps + codeoracle-api script entry (Task 12)"
```

---

## Task 13: Closure — handoff documentation

**Files:**
- (this file — narrative-only edits in §Cross-plan dependencies above)

No code or test artifacts in this task — Plan 11's drafting is complete once Tasks 0–12 are committed. The Cross-plan dependencies section already documents the inbound + outbound contracts. This closure step is the explicit hand-off list for the orchestrator's Phase G (Task #41) cross-plan-deps update sweep.

- [ ] **Step 1: Verify the §Cross-plan dependencies section above is current**

  Re-read this plan's "Cross-plan dependencies" section. Confirm:
  - Every "Plan 11 imports from Plan X" row above corresponds to a real artifact in Plan X.
  - The reciprocal-row catalogue lists every Plan X that needs a "Provides to Plan 11" row added.

- [ ] **Step 2: Phase G (Task #41) checklist**

  The orchestrator's Task #41 ("Cross-plan-deps update across all 14 plans") will:
  1. Add a "Plan 11 — …" row to Plan 1A's "Provides to later plans" section.
  2. Add a "Plan 11 — …" row to Plan 2's "Provides to later plans" section.
  3. Extend the existing "Plan 11" bullet in Plan 3's "What Plans 8 – 12 inherit from Plan 3" with the `Settings.repo_root` addition + env var `CODEORACLE_REPO_ROOT`.
  4. Add a "Plan 11 — …" row to Plan 5's "Provides to later plans" section.
  5. Add a "Plan 11 — …" row to Plan 10's "Provides to later plans" section.

  Each row text is pre-drafted in the "Reciprocal-row catalogue" table above — Phase G is mechanical application.

- [ ] **Step 3: Phase I (Task #42) full-corpus crosscheck**

  Task #42's D2 dimension will verify the reciprocity. D4 (acceptance-gate coverage) will check that §8.5's BL-HC01 / BL-QY01 / BL-RX01 (or whatever IDs land during the D4 audit) map to Plan 11's Task 11 integration tests.

---

## §6 EDIT table — pre-pivot misattributions + Plan-3 / Plan-5 modifications

Plan 11's drafting touches two earlier plans' files (Plan 3's `core/config.py` via Task 3; Plan 5's `IndexingService` consumed inline). Neither is a doc EDIT — they're code modifications declared in Plan 11's task table. This §6 section is therefore empty except for the audit-friendly explicit declaration:

| Finding ID | Owned by | Disposition |
|---|---|---|
| Plan-11-amendment-2026-05-26 | Plan 11 | **Reindex sync→async-job pivot.** POST `/v1/reindex` returns 202 + PENDING `Snapshot` instead of blocking until APPLIED; new GET `/v1/reindex/{snapshot_sha}` polling endpoint exposes the snapshot lifecycle. Invariant 11 reshaped (uvicorn returns to default `timeout_keep_alive`; no `reindex_timeout_seconds` sizing). Invariant 4 reshaped (advisory lock moves to the `ReindexOrchestratorConsumer` worker process; API no longer raises `AdvisoryLockTimeoutError` and never returns 409). Deviation 6 replaced (sync rationale removed; async-job rationale recorded). Task 7 fully rewritten — POST handler delegates to `IndexingService.enqueue_reindex`, GET handler delegates to `IndexingService.get_snapshot`. Task 10 uvicorn entrypoint simplified — `math.ceil(reindex_timeout_seconds) + 30` math removed. Task 11 integration test rewritten — POST 202 + poll-to-APPLIED round-trip via in-process `ReindexOrchestratorConsumer` fixture; idempotent retry test added; lock-contention 409 test dropped (no longer reachable). **Driver:** user-flagged P0 — the "small repo so sync is fine" assumption violates the platform mandate to operate on large production repositories, including repositories larger than the slice corpus. **Companion amendments (DAG order):** Plan 3 `7a82190` (`IndexingConfig` nested Settings — `repo_root` + `extractor_kinds`); Plan 5 `40885bf` (`ReindexOrchestratorConsumer` + `IndexingService.enqueue_reindex`/`get_snapshot` + `STREAM_REINDEX_REQUESTS` + `WorkerContext.engine` + `pre_allocated_snapshot_sha` on `IndexingService.reindex`); Plan 13 (gate 5 + Task 8 helpers + §8.7 ceremony scripts — applied in this amendment sweep). |
| Plan-11-2026-05-23-baseline | Plan 11 | Plan 11 (pre-amendment) introduces no new D-x.y / P0-N / G-NN / M-x.y findings against earlier plans. Both file modifications (Plan 3 `Settings.repo_root` + `ApiConfig` from `8a4021f`, Plan 5 inline-construction site #6) are pre-routed by this plan's own task list. |

---

## Deviations from spec / earlier plans

1. **`deps.py` (this plan) vs `dependencies.py` (Plan 10 — `agent/dependencies.py`).** The two file names look like inconsistency. Reason: the agent's `dependencies.py` holds BOTH the `AgentDependencies` frozen dataclass AND the `build_agent_dependencies` composer — the file is named after the type it owns. The API process has no equivalent dataclass (only the per-route DI helpers), so `deps.py` per spec §3.2 is accurate. **Not a drift; documented to forestall the D2 audit's "naming mismatch" flag.**

2. **`Settings.repo_root` top-level, not nested under `api`.** Promoting it to `ApiConfig.repo_root` would force workers (Plan 5/6/7) and MCP (Plan 12) to either reach into the API namespace or redeclare. Invariant 9 covers this; Plan 3's Settings amendment is documented in Task 3.

3. **No `/v1/ready` in slice.** K8s-style readiness probes that gate on cross-process state (agent reachable, Valkey up, all workers healthy) land in Phase 9. Slice has `/v1/health` only — local liveness, one process at a time. Spec §8.5 does not mandate `/v1/ready` for slice.

4. **`httpx` runtime promotion.** Plan 10 pins httpx as test-only. Plan 11 needs it at runtime for AgentClient. This is a transparent dep tier change; the runtime version (0.27.*) is pinned to match Plan 10's test version.

5. **No CORS / no rate-limit / no inbound auth in slice.** Per spec §8.6 + invariant 7. Phase 7 plan(s) will add the middleware.

6. **Reindex is asynchronous: POST → 202 + `snapshot_sha`; GET → poll (amended 2026-05-26).** The initial Plan 11 draft (commit `10ffcec` 2026-05-23) chose a synchronous reindex route on the assumption that slice repo size made sync acceptable. User flagged this as a P0 gap on 2026-05-26: CodeOracle's whole purpose is to operate on large real-world production repositories, including codebases far larger than the slice corpus — sync over HTTP times out at 300s and ties up uvicorn workers for the entire reindex duration under any realistic tenant. The amendment shifts the route to the worker-orchestrator pattern Plan 5 (DECIDE-D advisory-lock single-flight) and Plan 7 (`TracesIngestConsumer` on sibling lock `0x46AE0002`) already establish. POST `/v1/reindex` is now a thin enqueue: persist PENDING `Snapshot` row, XADD to `STREAM_REINDEX_REQUESTS`, return 202 + the row. A new `ReindexOrchestratorConsumer` (Plan 5 amendment `40885bf` Task 13a) dequeues, acquires lock `0x46AE0001` in the worker process, drives `IndexingService.reindex(pre_allocated_snapshot_sha=…)`, transitions the row to APPLIED|FAILED. Clients poll `GET /v1/reindex/{snapshot_sha}` until the row exits PENDING/APPLYING. Survives API restarts (lock is on the worker conn), scales across API replicas (workers single-flight; API replicas are stateless w.r.t. the lock), doesn't tie up uvicorn workers — production-grade by CodeOracle's mandate. The sync `IndexingService.reindex()` method REMAINS as-is for the Plan 5 CLI (Task 14) and the vertical integration test (Task 15), which both want blocking semantics in their own contexts.

---

## Self-review checklist

Before declaring drafting complete, the author has verified:

- [x] Spec §3.2 file tree (`api/{__main__.py, app.py, deps.py, routes/{query.py, reindex.py, health.py}}`) matches Task 0 + Task 9's file map.
- [x] Spec §3.3 rule 6 (processes are thin entrypoints) honored by invariant 1.
- [x] Spec §4.1 / §4.2 (API → agent over REST) implemented by AgentClient (Task 1) + /v1/query (Task 6).
- [x] Spec §8.5 acceptance-gate rows for /v1/query, /v1/reindex, /v1/health all have Task 11 integration tests.
- [x] Spec §8.6 (auth deferred to Phase 7) honored by invariant 7.
- [x] Plan 3's `ApiConfig` (from 2026-05-26 amendment `8a4021f`) fields all consumed: `host` (Task 10), `port` (Task 10), `agent_base_url` (Task 9 AgentClient), `agent_request_timeout_seconds` (Task 9), `lock_acquisition_timeout_seconds` (Task 9 IndexingService construction; the worker-side IndexingService reads this via Plan 5 amendment `40885bf` Task 13a). `reindex_timeout_seconds` is NO LONGER consumed by Plan 11 post-amendment 2026-05-26 — the route is async and uvicorn's default `timeout_keep_alive` (5s) is sufficient. The field stays on `ApiConfig` as a documented dead-letter pending a Slice+1 rename / migration to `IndexingConfig`; integration test does NOT set `CODEORACLE_API__REINDEX_TIMEOUT_SECONDS` (deleted from the `api_settings` fixture per advisor polish 2026-05-26), so the field's `60.0` default is what Settings carries.
- [x] Plan 5's `IndexingService.enqueue_reindex(*, base_sha: str | None) -> Snapshot` + `get_snapshot(snapshot_sha) -> Snapshot | None` signatures (from Plan 5 amendment `40885bf`) match Task 7's invocations.
- [x] Plan 5's `AdvisoryLockTimeoutError` (from `core/exceptions.py`) is NO LONGER caught by Task 7 — the raise-site moved to the `ReindexOrchestratorConsumer` worker (Plan 5 amendment `40885bf`); the API's request thread cannot reach it.
- [x] Plan 5's `IndexingService.__init__` keyword args (engine, db_session_factory, queue_client, repo_root, extractor_kinds, advisory_lock_timeout_seconds) all set in Task 9 lifespan.
- [x] Plan 10's `HealthResponse` model is the response type for Task 5's `/v1/health`.
- [x] Plan 10's invariant 11 (Query/QueryResult are wire contract; `extra="forbid"` enforced) honored by AgentClient (Task 1) — sends `Query` raw, parses `QueryResult` raw.
- [x] Plan 10's invariant 9 (no auth in slice; `shared_token` for Slice+1) honored by AgentClient — sends header only when configured.
- [x] Plan 1A's `init_telemetry()` (zero-arg) called in `__main__.py` (Task 10) before app construction.
- [x] Plan 3's `configure_logging(settings)` called in `__main__.py` (Task 10) before any log line.
- [x] No placeholder strings ("TBD", "TODO", "implement later", "fill in details", "add appropriate error handling", "similar to Task N").
- [x] Every step's code block contains the actual code an engineer types, not a description.
- [x] Type / method-signature consistency across tasks: `AgentClient.run(query)` in Task 1 matches its call in Task 6; `IndexingService.enqueue_reindex(base_sha=...)` + `IndexingService.get_snapshot(snapshot_sha)` in Task 7 match Plan 5 amendment `40885bf`'s signatures.
- [x] No Greek letters anywhere in this plan (per global feedback memory).
- [x] No emojis (per global instructions).

---

## Execution handoff

Plan complete and saved to `docs/superpowers/plans/2026-05-21-api-service.md`. Two execution options:

**1. Subagent-Driven (recommended)** — Orchestrator dispatches a fresh subagent per task, reviews between tasks, fast iteration. Subagents inherit the platform implementation scope and safety constraints from the parent dispatch template.

**2. Inline Execution** — Execute tasks in this session using `executing-plans` skill, batch execution with checkpoints for review. Reasonable for the small (~12) task count, but the Plan-5 + Plan-10 + Plan-1A cross-dependency surface makes subagent-driven safer.

When execution begins, Tasks 0 → 1 → 2 → 3 form the strict prerequisite chain (each depends on the previous). Tasks 4 → 5 → 6 → 7 → 8 are largely independent once the chain lands. Task 9 fans them in; Task 10 boots; Task 11 verifies; Task 12 ships the deps. Task 13 is a self-audit step.
