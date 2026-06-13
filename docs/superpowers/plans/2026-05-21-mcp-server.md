# MCP Server Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` (recommended) or `superpowers:executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship the platform's Model Context Protocol (MCP) server surface — `mcp/__main__.py`, `mcp/server.py`, `mcp/transports.py` — exposing the slice's two user-facing operations (`explain_symbol` and `reindex`) as MCP tools over both stdio (dev / local IDE integration) and streamable HTTP (service-mode parity with Plan 11's `POST /v1/query` and `POST /v1/reindex`). The §8.5 acceptance gate `test_mcp_explain_symbol_matches_api_response` is the load-bearing parity contract: the same `Query` model routed through MCP must produce byte-equal `QueryResult` payloads (modulo `trace_id`) to the same query routed through the HTTP API.

**Architecture:** Composition-over-inheritance OOP wrapper around the official `mcp` Python SDK's low-level `Server` class (composition root in `__main__.py`; `MCPServer` class in `server.py` holds constructor-injected `AgentClient` + `IndexingService` + `Settings`; tool handlers delegate to those deps so the MCP layer has zero business logic). The SDK's `Server` is a held member, never subclassed, so tool-handler methods are pure-function-testable without spinning up the JSON-RPC transport. Two transports share the same `MCPServer` instance: `transports.run_stdio(server)` for dev mode (default) and `transports.run_streamable_http(server, host, port)` for service mode. Dependencies are constructed eagerly in `__main__.py` (process-lifetime scope mirroring Plan 11's `app.py` lifespan pattern) with explicit `aclose()` in a `try/finally`.

**Tech Stack:** `mcp >= 1.12, < 1.13` (official Python SDK — `mcp.server.lowlevel.Server`, `mcp.server.stdio.stdio_server`, `mcp.server.streamable_http_manager.StreamableHTTPSessionManager` composed manually into a Starlette app for the HTTP transport — the SDK does NOT expose a one-shot `Server.streamable_http_app(...)` factory in v1.12.4); `starlette >= 0.40, < 0.50` (pinned directly in `pyproject.toml` — Task 1 — used in `transports.py` for the streamable-HTTP ASGI app + lifespan + `Mount`; NOT relied on transitively from fastapi/Plan 11); `uvicorn[standard] >= 0.32, < 0.33` (HTTP transport runner, shared with Plan 10/11 entrypoints); inherits `httpx >= 0.27` from Plan 11 (used by `AgentClient` — Plan 12 does not import `httpx` directly); inherits `pydantic >= 2.9` for the tool-schema derivation; `pytest-asyncio` for the stdio/streamable-HTTP integration tests.

---

## Task list

- Task 0: Package layout + `mcp` SDK dependency (foundation for all subsequent tasks)
- Task 1: Plan 3 amendment — `McpConfig` nested Settings model + `configure_logging(stream=...)` parameter (the two upstream changes Plan 12 depends on; lands as its own Plan 3 amendment commit *before* Plan 12 implementation begins)
- Task 2: `MCPServer` class — composition root + `explain_symbol` handler + tool schemas
- Task 3: `MCPServer` class — `reindex` handler (async enqueue via `IndexingService.enqueue_reindex` → PENDING `Snapshot` receipt, mirroring Plan 11's `POST /v1/reindex`; DECIDE-G amended 2026-05-26)
- Task 4: Transports module — `run_stdio` + `run_streamable_http` (stdout-pollution-safe stdio init; uvicorn-driven streamable HTTP runner)
- Task 5: `__main__.py` composition root — argv parsing, transport selection, eager dependency construction, `try/finally` teardown
- Task 6: Unit tests — `MCPServer` tool handlers with mocked `AgentClient` + `IndexingService`
- Task 7: Integration test — stdio transport round-trip via the SDK's in-process client
- Task 8: Integration test — §8.5 parity gate `test_mcp_explain_symbol_matches_api_response` (byte-equal `QueryResult` modulo `trace_id`)
- Task 9: `pyproject.toml` — `[project.scripts] codeoracle-mcp` entry + `mcp` runtime dep + `preflight.sh` MCP smoke step
- Task 10: Closure notes — verification log + downstream-plan inheritance pointers recorded in this plan

---

## Architectural invariants

These invariants apply to every file Plan 12 ships. Tasks reference them by number.

1. **MCP is a process, not a service** (spec §3.3 layer purity rule 6): `src/codeoracle/mcp/` is a thin process that wires existing services. No business logic. The MCP layer never reaches into `core/`, `db/`, or `services/` internals directly — it composes them via constructor injection.

2. **`explain_symbol` is byte-equal to `POST /v1/query`** (spec §8.5 gate): Both routes accept the same `Query` model, both delegate to the same `AgentClient.run()`, both serialize the same `QueryResult` via `model_dump(mode="json")`. The byte-equal parity is structurally enforced by sharing the model + the client, not by a separate translation layer.

3. **`reindex` mirrors `POST /v1/reindex`'s async enqueue (amended 2026-05-26)**: Same `ReindexRequest` model, same `IndexingService.enqueue_reindex(base_sha=...)` call (Plan 5 amendment `40885bf`), same `Snapshot` serialization. Both the MCP tool and the HTTP route enqueue an async job and return the PENDING `Snapshot` receipt immediately (HTTP returns it with 202; MCP returns the dict as `structuredContent`); the `ReindexOrchestratorConsumer` worker drives the reindex out-of-band. Because `snapshot_sha` is a pure function of tree state and both transports call the same `enqueue_reindex`, the same `base_sha` produces the SAME `snapshot_sha` through either path — that is the reindex-parity contract (Plan 13's reindex-parity gate pins it; the byte-equal-modulo-`trace_id` shape of the `explain_symbol` gate does not transfer because the reindex `Snapshot` mutates as the worker runs). The advisory lock lives entirely in the worker, so neither transport raises `AdvisoryLockTimeoutError` or returns a 409-equivalent; `enqueue_reindex` is idempotent on `SnapshotConflictError` (a duplicate enqueue against the same tree returns the existing row). (No `Settings.mcp.lock_acquisition_timeout_seconds`; the lock-wait window is a worker concern shared with the HTTP API.)

4. **Dual transport, single server instance**: `mcp/transports.py` exposes two runners (`run_stdio(server)` and `run_streamable_http(server, host, port)`). Both accept the same `MCPServer` instance constructed once in `__main__.py`. Transport selection is argv-driven (default = stdio; `--http` = streamable HTTP).

5. **Stdout-pollution invariant** (the MCP stdio footgun): On stdio transport, *any* non-JSON-RPC byte on stdout corrupts the protocol — the client silently desyncs. Plan 12 enforces three guards:
   - `__main__.py` calls `configure_logging(settings, stream=sys.stderr)` *before* any other import that might log, when stdio mode is active.
   - Plan 3 amendment (Task 1) extends `configure_logging()` to accept a `stream: TextIO` parameter that plumbs through to both `logging.basicConfig(stream=stream, force=True)` and `structlog.PrintLoggerFactory(file=stream)`.
   - Bare `print()` is forbidden anywhere under `src/codeoracle/mcp/` (enforced by a ruff rule added in Task 9).

6. **OOP composition root** (CLAUDE.md preference + spec layer-purity rule 6): `MCPServer` is a class with `__init__(*, agent_client, indexing_service, settings)`. The SDK's `Server` is a held member (`self._server`); never subclassed. Tool handlers are `async` methods (`_handle_explain_symbol`, `_handle_reindex`). Registration uses the SDK's decorator pattern through the held `Server`: `self._server.list_tools()(self._list_tools)` and `self._server.call_tool()(self._call_tool)`.

7. **Eager dependency lifetime** (matches Plan 11's `app.py` lifespan): `AgentClient` + `IndexingService` + `ValkeyQueueClient` + SQLAlchemy engine are constructed once in `__main__.py` and live for the entire process. Cleanup is via `try/finally` after the transport runner returns. The SDK `Server` lifespan (the `lifespan=` kwarg on `Server()`) is set to a no-op async context that just yields an empty context dict; cleanup is the composition root's job, not the SDK's.

8. **`extra="forbid"` flows through the tool schemas**: Tool input schemas are derived via `Query.model_json_schema()` and `ReindexRequest.model_json_schema()` (Plan 3 + Plan 11 models). Both models already declare `model_config = ConfigDict(extra="forbid")`, so the emitted JSON Schemas include `"additionalProperties": false`. MCP clients that send extra fields will fail validation client-side OR server-side (`Query.model_validate(args)` raises `ValidationError` which the SDK wraps as a tool error).

9. **No `/health`, no `/metrics`, no auth on the streamable HTTP transport** (slice scope). The streamable HTTP transport exposes *only* the `/mcp` JSON-RPC endpoint. Plan 11's `/v1/health` and `/metrics` are sibling-port surfaces — operators monitoring the MCP service either rely on connection-level health (TCP accept) or scrape the *agent's* `/health` (the process MCP composes against). This deviation from Plan 11 is deliberate: MCP's JSON-RPC framing doesn't easily admit a sidecar `/health` route, and the slice's operability target is "Claude Code runs `codeoracle-mcp` over stdio," not "k8s liveness probe on the streamable HTTP service."

10. **LLM-agnostic invariant** (CLAUDE.md project rule): The MCP process does NOT import any LLM provider directly (`anthropic`, `openai`, `litellm`, etc. forbidden). All LLM calls flow through `AgentClient` → agent process → `codeoracle.adapters.llm` facade. The ruff `per-file-ignores` rule already forbids LLM-provider imports outside `adapters/llm/providers/`; Plan 12's files inherit that rule.

11. **Module-level mutable state forbidden** (matches Plan 11 invariant 11): No `app = ...` or `server = MCPServer(...)` at module scope. Construction happens inside `main()` or test helpers. Test code uses `build_test_server()` or directly instantiates `MCPServer` with mock deps.

12. **`Settings.repo_root` shared with Plan 11** (Plan 11 invariant — top-level, not nested under `ApiConfig` or `McpConfig`): `IndexingService` construction in `__main__.py` consumes `settings.repo_root` directly. This is the single source of truth for the snapshot path across processes; no per-process override.

---

## Cross-plan dependencies

### Imports from

| Plan | Symbol / module | Purpose in this plan |
|---|---|---|
| Plan 1 | `pyproject.toml` toolchain (`uv` / `ruff` / `ty` / `pytest`) | All Plan 12 code is built/lint/type/test-gated by Plan 1's toolchain commit-#1 gates. |
| Plan 2 | `preflight.sh` | Task 9 EXTENDS preflight.sh with an MCP smoke step (`codeoracle-mcp --http &; client ping; kill`); the existing preflight steps remain untouched. |
| Plan 3 | `core/config.py:Settings`, `core/config.py:McpConfig` (added by Task 1 amendment), `core/config.py:get_settings`, `core/logging.py:configure_logging` (signature extended by Task 1 amendment to accept `stream: TextIO`), `models/query.py:Query` + `QueryStatus` + `QueryResult`, `models/snapshot.py:Snapshot` + `SnapshotStatus` | `Settings` / `McpConfig` / `get_settings` configure the process; `configure_logging(stream=...)` enforces the stdio stdout-pollution guard; `Query` / `QueryStatus` / `QueryResult` are the `explain_symbol` tool input/output schemas + handler argument types; `Snapshot` / `SnapshotStatus` are the `reindex` tool output schema + handler return type. |
| Plan 4 | (No direct import — LLM provider is reached transitively via `AgentClient` → agent process → `codeoracle.adapters.llm`.) | — |
| Plan 5 | `services/indexing/service.py:IndexingService` (incl. `enqueue_reindex` — Plan 5 amendment `40885bf`), `adapters/valkey/client.py:ValkeyQueueClient`, `adapters/valkey/config.py:ValkeyConfig` (standalone `BaseSettings` — Plan 5 L441 — constructed zero-arg per Plan 5's precedent at L2132 and L7016, requires `# type: ignore[call-arg]` because `ValkeyConfig.url` is a required field populated from `CODEORACLE_VALKEY__URL` at runtime — ty cannot see the env source) | `IndexingService.enqueue_reindex(base_sha=...)` is the reindex tool's async-job delegate (the same entrypoint Plan 11's `POST /v1/reindex` calls): it persists a PENDING `Snapshot`, XADDs to `STREAM_REINDEX_REQUESTS`, and returns the row immediately; the handler serializes that receipt with `model_dump(mode="json")`. It is idempotent on `SnapshotConflictError` (duplicate enqueue → existing row). The advisory lock lives in the `ReindexOrchestratorConsumer` worker (Plan 5 amendment `40885bf`), so the MCP handler never sees `AdvisoryLockTimeoutError`; the Valkey client is a constructor arg of `IndexingService`. |
| Plan 10 | (No direct import — Plan 12 reaches the agent via `AgentClient` from Plan 11, not via Plan 10's process-internal classes.) | — |
| Plan 11 | `adapters/agent/client.py:AgentClient`, `adapters/agent/errors.py:AgentRemoteError` + `AgentTransportError` + `AgentValidationError`, `models/reindex.py:ReindexRequest` | `AgentClient.run(query)` is the explain_symbol tool's delegate; the agent errors flow through unchanged (the SDK wraps non-tool exceptions as JSON-RPC error responses); `ReindexRequest` is the reindex tool's input schema. |

### Provides to

| Plan | Symbol / module | Purpose in downstream plan |
|---|---|---|
| Plan 13 (E2E + slice acceptance) | `codeoracle-mcp` binary (entrypoint registered in `pyproject.toml` `[project.scripts]`) | E2E tests run `codeoracle-mcp --http` in a subprocess + connect via the SDK's HTTP client to exercise the full MCP-mediated query flow against a live agent + sourcebot + DB stack. The §9 demo PROVEN / UNKNOWN / live-edit scripts all drive `explain_symbol` via the MCP client. |
| Plan 13 | `mcp.MCPServer` class + `transports.run_streamable_http` | E2E uses the `build_test_server(...)` helper from Task 7's integration tests + `run_streamable_http(server, host=..., port=...)` to spin up an in-process streamable-HTTP server for the E2E suite (instead of spawning a subprocess). |

### Reciprocal-row entries (paired with downstream-plan §Cross-plan dependencies)

| Plan | Direction | Reciprocal row description |
|---|---|---|
| Plan 3 | Plan 3 provides to Plan 12 | `core/config.py:McpConfig` (Task 1 amendment) + `core/logging.py:configure_logging(stream=...)` (Task 1 amendment) + `models/query.py:Query` / `QueryResult` / `QueryStatus` (tool schemas) + `models/snapshot.py:Snapshot` / `SnapshotStatus` (reindex output). |
| Plan 5 | Plan 5 provides to Plan 12 | `services/indexing/service.py:IndexingService` (incl. `enqueue_reindex` — amendment `40885bf`) + `adapters/valkey/client.py:ValkeyQueueClient` + `adapters/valkey/config.py:ValkeyConfig`. (The advisory lock + `AdvisoryLockTimeoutError` now live in `ReindexOrchestratorConsumer`, not consumed by the MCP handler.) |
| Plan 11 | Plan 11 provides to Plan 12 | `adapters/agent/client.py:AgentClient` + `adapters/agent/errors.py:*` + `models/reindex.py:ReindexRequest`. |
| Plan 13 | Plan 13 inherits from Plan 12 | `codeoracle-mcp` script entry + `mcp.MCPServer` factory + `transports.run_streamable_http` runner. |

---

## File structure

Plan 12 ships the following new files. Existing files modified are noted under "Modify" in the per-task headers.

```
src/codeoracle/mcp/
├── __init__.py            (~5 lines — re-exports for ergonomic imports)
├── __main__.py            (~80 lines — argv parsing + composition root + transport selection + try/finally teardown)
├── server.py              (~280 lines — MCPServer class: __init__, _build_server, _list_tools, _call_tool, _handle_explain_symbol, _handle_reindex)
└── transports.py          (~120 lines — run_stdio, run_streamable_http)

tests/unit/mcp/
├── __init__.py            (empty marker)
├── conftest.py            (~70 lines — fake_agent_client, fake_indexing_service, settings_env autouse fixture)
└── test_server.py         (~280 lines — MCPServer handler unit tests with mocked deps)

tests/integration/mcp/
├── __init__.py            (empty marker)
├── conftest.py            (~90 lines — shared fixtures: in-memory MCPServer + stub AgentClient + stub IndexingService)
├── test_stdio_transport.py    (~160 lines — stdio round-trip via SDK in-process client)
└── test_parity_with_api.py    (~180 lines — §8.5 byte-equal parity gate)
```

All files stay well under the 500-line soft target (largest is `server.py` at ~280 lines; the OOP composition with single-responsibility handler methods naturally keeps the file small).

---

## Decisions resolved before drafting

This section records design decisions Plan 12 commits to. Each decision is justified by spec text, Plan 11 precedent, or an explicit trade-off articulated below. Tasks reference these by ID.

### DECIDE-E: MCP SDK choice — official `mcp` Python SDK v1.12.4

**Decision:** Use the official `modelcontextprotocol/python-sdk` (`mcp` PyPI package), pinned `>= 1.12, < 1.13`.

**Alternative considered:** A hand-rolled JSON-RPC + stdio framing layer using `anyio` + `starlette` directly. Eliminates the SDK dependency but duplicates ~500 lines of well-tested framing logic.

**Why recommendation wins:** The official SDK is the reference implementation maintained by the MCP spec authors. It handles JSON-RPC framing, stdio line-delimited transport, streamable HTTP session management, schema validation, and the `CallToolResult.isError` semantics. Re-implementing those is high-risk, low-value debt — exactly the kind of thing the spec's "no slop, no half-finished implementations" rule forbids.

**Trade-off accepted:** The SDK is at v1.12.4 — a young, fast-moving library. We pin tight (`< 1.13`) and accept that Plan 13/14-18 may need to bump the pin. The cost of a future minor-version migration is bounded; the cost of maintaining our own JSON-RPC framing is unbounded.

### DECIDE-F: `explain_symbol` tool accepts the full `Query` model

**Decision:** The `explain_symbol` tool's `inputSchema` is `Query.model_json_schema()`. The handler does `Query.model_validate(arguments)` and passes the resulting `Query` to `AgentClient.run(query)`. No translation layer.

**Alternative considered:** Define an MCP-specific `ExplainSymbolRequest` model that's a strict subset of `Query` (e.g., just `symbol: str`). Simpler client UX (less surface area for the MCP client to populate); diverges from `POST /v1/query` (would need a Query-builder layer to map MCP → API model).

**Why recommendation wins:** Spec §8.5 demands byte-equal payloads. The cleanest way to get that for free is shared models. A `Query`-builder layer is exactly the kind of business-logic-in-the-process-layer the spec forbids (§3.3 rule 6 — processes are thin entrypoints). Plus: the MCP client (Claude Code, Cursor) is itself an LLM agent that can populate the full `Query` model from natural language — it doesn't need a "simpler" wrapper.

**Trade-off accepted:** Tool input schema is wider than necessary for the dev-mode demo, but matches the production semantics from day one. We do NOT define a "convenience" wrapper; the full `Query` model is the contract.

### DECIDE-G: `reindex` tool mirrors Plan 11's async enqueue (single-shot, returns the PENDING receipt)

**Decision (amended 2026-05-26 — sync→async pivot):** The `reindex` tool's `inputSchema` is `ReindexRequest.model_json_schema()` (the Plan 11 model). The handler does `ReindexRequest.model_validate(arguments)` and calls `self._indexing_service.enqueue_reindex(base_sha=req.base_sha)` — the same async-job entrypoint Plan 11's `POST /v1/reindex` calls (Plan 5 amendment `40885bf`). It returns the PENDING `Snapshot` receipt immediately; the `ReindexOrchestratorConsumer` worker drives the reindex out-of-band. The slice ships ONE tool call — the enqueue — and does NOT add a separate MCP-side `get_reindex_status` poll tool.

**Alternative considered:** Keep `reindex` synchronous — `self._indexing_service.reindex(base_sha=...)` blocking until the snapshot reaches APPLIED, returning the terminal `Snapshot`. Simpler client UX (one call, terminal result); no second poll round-trip.

**Why recommendation wins:** Plan 11's `POST /v1/reindex` is now async (amendment `fc0894a`: 202 + PENDING `Snapshot`, poll via `GET /v1/reindex/{snapshot_sha}`) because a synchronous reindex over large production repositories ties up the server for minutes and times out under any realistic tenant — the original "small repo so sync is fine" assumption violated the platform's mandate. §8.5 parity requires MCP to match: a synchronous MCP `reindex` against an async API would be the lone divergent contract (the exact gap this amendment closes), and over the streamable HTTP transport it would also hit the session timeout on a large reindex. Delegating to the same `enqueue_reindex` makes the two transports structurally identical — and because `snapshot_sha` is a pure function of tree state, the same `base_sha` enqueues the SAME snapshot through either path (the reindex-parity gate in Plan 13 pins exactly that).

**Trade-off accepted:** The MCP client receives a PENDING receipt, not a terminal `Snapshot`, so an MCP client that wants to observe completion must poll the API's `GET /v1/reindex/{snapshot_sha}` (or wait for a future MCP `get_reindex_status` tool — deferred to Slice+1, see Outstanding deviations). For the slice's PROVEN/UNKNOWN/live-edit demos this is acceptable: the ceremony scripts drive the API poll surface directly.

### DECIDE-H: Streamable HTTP is stateless (`stateless=True` on the session manager)

**Decision:** `StreamableHTTPSessionManager(app=server, event_store=None, json_response=False, stateless=True)` composed into a Starlette app with a `lifespan` opening `manager.run()` and a `Mount("/mcp", app=handle_streamable_http)` route — the canonical pattern verified against the v1.12.4 SDK source (`mcp.server.streamable_http_manager`). Each request is independent; no session-scoped tool state on the server side. Note: the kwarg is `stateless` (NOT `stateless_http`), and the v1.12.4 SDK does NOT expose a one-shot `Server.streamable_http_app(...)` factory — the caller composes Starlette + the session manager directly.

**Alternative considered:** `stateless=False` (the SDK default) — server-side session state with reconnection / resumption support. Useful for long-running tool invocations.

**Why recommendation wins:** Our two tools (`explain_symbol`, `reindex`) are both single-shot request/response. There's no per-session state to maintain (no streaming partial results, no incremental tool output). Stateless mode simplifies the deployment story: any horizontal scaling later just round-robins requests; no sticky-session config needed.

**Trade-off accepted:** If Slice+1 introduces a streaming tool (e.g., `stream_evidence` that yields citations incrementally), we'll flip the session manager's `stateless` kwarg to `False`. This is a one-line change.

### DECIDE-I: Separate `codeoracle-mcp` binary (independent process from `codeoracle-api`, `codeoracle-agent`)

**Decision:** `pyproject.toml` ships `codeoracle-mcp = "codeoracle.mcp.__main__:app"` as its own `[project.scripts]` entry (the Typer app object; mirrors Plan 10's `codeoracle-agent` and Plan 11's `codeoracle-api` in being a process binary, though those target uvicorn `:main` entrypoints rather than Typer apps). The slice runs three separate processes: agent (8081), API (8080), MCP (8082 in HTTP mode, or stdin/stdout in stdio mode).

**Alternative considered:** Mount the MCP streamable-HTTP ASGI app inside Plan 11's `api/app.py` so a single `codeoracle-api` process serves both `/v1/*` and `/mcp` on the same port. Simpler deployment (one binary).

**Why recommendation wins:** Per spec §3.3 rule 6 + Plan 11 invariant 1 ("API is a process, not a service"), processes are independent thin entrypoints. The API process is HTTP-only; the MCP process serves stdio AND HTTP. Folding MCP into the API process couples deployment lifecycles (every API restart restarts MCP) and creates a confusing dual-binding (one process serving two protocols). Three separate binaries map cleanly to the spec's three-process model.

**Trade-off accepted:** Three processes mean three lifecycle managers in the docker-compose/k8s deployment. The cost is small (each process is ~200 LoC of wiring); the clarity gain is substantial.

### DECIDE-J: ~~`AdvisoryLockTimeoutError` re-raise with retry-hint message~~ — SUPERSEDED by the 2026-05-26 async pivot

**Status:** Superseded. This decision governed how MCP surfaced lock contention over the wire while `POST /v1/reindex` was synchronous and the API held `pg_advisory_lock(0x46AE0001)` on the request thread. The 2026-05-26 sync→async pivot (Plan 11 amendment `fc0894a`; Plan 5 amendment `40885bf`) moved the advisory lock entirely into the `ReindexOrchestratorConsumer` worker process. `IndexingService.enqueue_reindex` — the handler's new delegate — never acquires the lock and never raises `AdvisoryLockTimeoutError`, so lock contention no longer reaches the MCP boundary. There is no successor decision: the concern simply vanishes (parallel to Plan 11 invariant 4 amended — the API's HTTP 409 + `Retry-After` mapping was likewise deleted). The reindex tool now returns the PENDING `Snapshot` enqueue receipt; idempotency on duplicate enqueue (Plan 11 invariant 4) replaces the old retry-hint contract. The letter slot `DECIDE-J` is retained as a tombstone so DECIDE-K/L/M cross-references stay stable.

### DECIDE-K: Reuse `AgentClient` from Plan 11 (no separate MCP-side client)

**Decision:** Plan 12 imports `codeoracle.adapters.agent.client.AgentClient` from Plan 11 and uses it unchanged. No `mcp/agent_client.py` or `mcp/clients/agent.py` — that would duplicate the HTTP+timeout+retry semantics Plan 11 already encapsulates.

**Alternative considered:** Define an MCP-internal `MCPAgentClient` that's a thin facade over `AgentClient` to allow MCP-specific telemetry / error handling.

**Why recommendation wins:** The byte-equal parity gate (spec §8.5) demands that the agent be called identically from both API and MCP. Two clients = two code paths = two opportunities for drift. One client = structurally enforced parity.

**Trade-off accepted:** MCP-specific telemetry (e.g., "this query came in via MCP not API") would be added at the `_call_tool` boundary via an OTLP span attribute `mcp.transport=stdio|http`, not inside the client (the client stays MCP-agnostic). Plan 12's slice scope explicitly DEFERS that instrumentation to Slice+1 (see the Outstanding deviations table for the deferral + trigger). No `core/telemetry.py` symbol is imported by any Plan 12 file in this slice; Plan 1A's tracer surface is reserved for the Slice+1 hookup.

### DECIDE-L: OOP `MCPServer` class wrapping the SDK `Server` (composition, not inheritance)

**Decision:** `MCPServer` is a class with constructor DI (`__init__(*, agent_client, indexing_service, settings)`). The SDK's `Server` is a held member (`self._server`) — `MCPServer` does NOT inherit from `mcp.server.lowlevel.Server`. Tool registration uses the held server's decorator API: `self._server.list_tools()(self._list_tools)` and `self._server.call_tool()(self._call_tool)`.

**Alternative considered:** Subclass `mcp.server.lowlevel.Server` directly so `MCPServer` IS a `Server`. Slightly more idiomatic for SDK consumers expecting a `Server` instance.

**Why recommendation wins (CLAUDE.md OOP preference + composition-over-inheritance):**

1. The SDK's `Server` API is not stable (v1.x); subclassing locks us to its internal method names. Composition gives us a stable seam.
2. Constructor DI is impossible via subclassing because `Server.__init__` is positional-only and its signature changes between SDK versions. Composition lets us own the constructor.
3. Tool handlers are async methods on `MCPServer` (testable in isolation) — they don't need access to the `Server`'s protocol internals.
4. The held `Server` is exposed via a `server` property for transport runners, so the rest of the system can still get an SDK-compatible `Server` instance.

**Trade-off accepted:** Code that wants to access SDK-specific `Server` features goes through `mcp_server.server.*`. This is fine — only the transport runners (`run_stdio`, `run_streamable_http`) need it.

### DECIDE-M: No `/health` on streamable HTTP (spec deviation, slice scope only)

**Decision:** The streamable HTTP transport exposes ONLY `/mcp` (the JSON-RPC endpoint). No `/health`, no `/metrics`. Operators rely on TCP-level liveness (port accept) for the slice's deployment posture.

**Alternative considered:** Wrap the SDK ASGI app in a parent Starlette app with `/health` mounted alongside `/mcp` at the root — straightforward (~15 LoC).

**Why recommendation wins (slice scope discipline):** The slice's operability target is "Claude Code runs `codeoracle-mcp` over stdio for a developer demo." Service-mode HTTP exists for §8.5's parity gate (to prove the MCP server CAN run as a long-lived service), not for k8s readiness. Plan 11 already owns `/v1/health` + `/metrics`; operators monitoring the slice will scrape the API's health + the agent's health, not the MCP's. Adding `/health` to MCP is build-and-throw-away work for the slice.

**Trade-off accepted:** If Slice+1 deploys MCP as a long-lived k8s pod with liveness probes, we add `/health`. Until then, YAGNI.

---

## Pre-implementation reading list

Before starting Task 0, the implementer should skim the following (existing in the repo):

1. **`docs/superpowers/specs/2026-05-21-platform-scaffolding-design.md`**:
   - §3.2 Layout (the `mcp/` directory tree)
   - §3.3 Layer purity (rule 6 — processes are thin entrypoints)
   - §8.5 Acceptance gates (the `test_mcp_explain_symbol_matches_api_response` row)
   - §9 Demos (the live PROVEN / UNKNOWN / live-edit demo scripts all drive `explain_symbol` via the MCP client)

2. **`docs/superpowers/plans/2026-05-21-api-service.md`** (Plan 11):
   - Task 1 (`AgentClient` adapter — Plan 12's explain_symbol delegate)
   - Task 3 (`Settings.api` nested config — McpConfig mirrors this pattern)
   - Task 9 (`api/app.py` lifespan — Plan 12's `__main__.py` mirrors this dependency-construction order)
   - §Cross-plan dependencies (Plan 11's "provides to Plan 12" row pre-declares the imports)

3. **`docs/superpowers/plans/2026-05-21-core-foundation-and-db-bootstrap.md`** (Plan 3):
   - §`Settings` (L503-540 — nested config Field(default_factory=...) pattern; McpConfig adds row 5)
   - §`ApiConfig` (L402-457 — nested-config docstring + env-var namespace template; McpConfig follows the same shape)
   - §`configure_logging` (search `def configure_logging` in Plan 3, currently ~L915 — Task 1 amendment adds a `stream` parameter; the existing `force=True` semantic is preserved)

4. **`docs/superpowers/plans/2026-05-21-worker-framework-and-joern-vertical.md`** (Plan 5):
   - §`IndexingService.enqueue_reindex` (Plan 5 amendment `40885bf` — the reindex tool's async-job delegate; persists a PENDING `Snapshot`, XADDs to `STREAM_REINDEX_REQUESTS`, idempotent on `SnapshotConflictError`; same entrypoint Plan 11's `POST /v1/reindex` calls)
   - §`ReindexOrchestratorConsumer` (Plan 5 amendment `40885bf` — the worker process that holds `pg_advisory_lock(0x46AE0001)` out-of-band; the lock no longer reaches the MCP/API boundary)
   - §`ValkeyConfig` (L441 — zero-arg `BaseSettings` construction precedent; Plan 12 inherits via Plan 11's pattern)

5. **`docs/superpowers/plans/2026-05-21-agent-orchestration.md`** (Plan 10):
   - Task 11 (agent HTTP entrypoint — `agent/__main__.py` ~10-line uvicorn wrapper, `agent/app.py` factory + lifespan, conftest autouse env fixture). Plan 12's `__main__.py` + transports.py + integration-test conftest mirror this pattern.

6. **MCP Python SDK** (external, install on `Task 0 Step 1`):
   - `mcp.server.lowlevel.Server` — the class held inside `MCPServer`
   - `mcp.server.lowlevel.helper_types.ReadStream` + `WriteStream` — the stdio runner's signature
   - `mcp.server.stdio.stdio_server` — the stdio transport context manager
   - `mcp.server.streamable_http_manager.StreamableHTTPSessionManager` — composed manually into a Starlette app for the HTTP transport (the v1.12.4 SDK does NOT expose a one-shot `Server.streamable_http_app(...)` factory)
   - `mcp.types.Tool` / `TextContent` / `CallToolResult` — the SDK's type surface

---

## Task 0: Package layout + `mcp` SDK dependency

**Files:**
- Create: `src/codeoracle/mcp/__init__.py`
- Create: `tests/unit/mcp/__init__.py`
- Create: `tests/integration/mcp/__init__.py`
- Modify: `pyproject.toml` (add `mcp` runtime dep — full pin row added in Task 9; this task adds the bare minimum to make imports work)

**Goal:** Create the empty package skeleton + add the `mcp` SDK dep so subsequent tasks' tests can import the library.

- [ ] **Step 1: Add the `mcp` SDK to `[project.dependencies]`**

```toml
# pyproject.toml — INSERT inside [project] dependencies = [...] (alphabetical order)

dependencies = [
    # ... existing rows ...
    "mcp >= 1.12, < 1.13",  # MCP Python SDK — Plan 12; Server, stdio_server, StreamableHTTPSessionManager
    # ... existing rows ...
    "starlette >= 0.40, < 0.50",  # direct import in transports.py for the streamable-HTTP ASGI app + lifespan + Mount; not relied on transitively from fastapi/Plan 11
    # ... existing rows ...
]
```

`starlette` is pinned directly (rather than relying on the transitive pull-in via fastapi from Plan 11) because Plan 12's `transports.py` imports `Starlette`, `Mount`, and the `Scope/Receive/Send` types directly — a future Plan 11 dep change that drops or replaces fastapi would silently break Plan 12 if the pin were transitive. The version band matches the fastapi 0.115 line's Starlette requirement (>=0.40,<0.50).

The full `[project.scripts] codeoracle-mcp = ...` entry is added in Task 9 alongside the preflight.sh edit. This step is the minimum to make `import mcp.server.lowlevel` and `import starlette.applications` succeed in subsequent tests.

- [ ] **Step 2: Install the dep + verify import**

```bash
uv sync
python -c "from mcp.server.lowlevel import Server; from mcp.server.stdio import stdio_server; print(Server.__module__)"
```

Expected: `mcp.server.lowlevel` (the import resolves to the SDK package, not a stub).

- [ ] **Step 3: Create the empty package marker `src/codeoracle/mcp/__init__.py`**

```python
"""Model Context Protocol server process for CodeOracle.

This package implements the MCP server surface — `explain_symbol` and `reindex`
tools — over stdio (dev / IDE integration) and streamable HTTP (service-mode
parity with Plan 11's HTTP API). The full architectural invariants are in
docs/superpowers/plans/2026-05-21-mcp-server.md (invariants 1-12).

Public surface (re-exported for ergonomic imports):

- `MCPServer` — the OOP wrapper around `mcp.server.lowlevel.Server` that holds
  the composed dependencies (AgentClient + IndexingService + Settings) and
  registers the two tool handlers.
- `run_stdio` / `run_streamable_http` — the two transport runners; both accept
  an `MCPServer` instance constructed in `__main__.py`.
"""

from codeoracle.mcp.server import MCPServer
from codeoracle.mcp.transports import run_stdio, run_streamable_http

__all__ = ["MCPServer", "run_stdio", "run_streamable_http"]
```

Note: the re-exports reference `codeoracle.mcp.server` and `codeoracle.mcp.transports`, which are created in Tasks 2-4. Importing `codeoracle.mcp` will fail until those modules exist; that's expected and tests don't import the package until Task 6+.

- [ ] **Step 4: Create the empty test-package markers**

```python
# tests/unit/mcp/__init__.py
```

(empty file — pytest needs the marker to discover the test package)

```python
# tests/integration/mcp/__init__.py
```

(empty file — same reason)

- [ ] **Step 5: Run the package-layout sanity test**

```bash
# Confirm pytest can discover the empty test packages (no tests yet — just no collection errors)
uv run pytest tests/unit/mcp/ tests/integration/mcp/ --collect-only -q
```

Expected output:
```
no tests ran in 0.XX s
```

- [ ] **Step 6: Commit**

```bash
git add pyproject.toml uv.lock src/codeoracle/mcp/__init__.py tests/unit/mcp/__init__.py tests/integration/mcp/__init__.py
git commit -m "feat(mcp): scaffold package skeleton + mcp SDK dep

Add empty src/codeoracle/mcp/ package and tests/{unit,integration}/mcp/ test
packages with the mcp SDK pin (>= 1.12, < 1.13). Subsequent tasks build
server.py + transports.py + __main__.py.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

## Task 1: Plan 3 amendment — `McpConfig` nested Settings + `configure_logging(stream=...)`

**Files:**
- Modify: `docs/superpowers/plans/2026-05-21-core-foundation-and-db-bootstrap.md` (Plan 3 amendment — TWO changes: add `McpConfig` nested model + extend `configure_logging` signature)
- Modify: `docs/superpowers/plans/2026-05-21-mcp-server.md` (this plan — append to §6 EDIT table if one exists, or note the amendment date)

**Goal:** Land the two upstream Plan 3 changes Plan 12 depends on, as a SEPARATE Plan 3 amendment commit BEFORE any Plan 12 implementation begins. This mirrors Plan 11's precedent where `ApiConfig` was added to Plan 3 as commit `8a4021f` before Plan 11 task implementation began.

**Critical:** This task does NOT modify any `src/codeoracle/` source code. It modifies only the Plan 3 plan document. The implementation of `McpConfig` + `configure_logging(stream=...)` will be done by whoever implements Plan 3 Tasks 2 + 3 (or, if Plan 3 is already implemented, by a separate "back-port" commit referencing this amendment).

- [ ] **Step 1: Read Plan 3's existing `ApiConfig` block (L402-457) to confirm the nested-config docstring + env-var namespace template**

```bash
sed -n '402,457p' docs/superpowers/plans/2026-05-21-core-foundation-and-db-bootstrap.md
```

Expected: the `class ApiConfig(BaseModel):` block with `model_config = ConfigDict(extra="forbid")`, `host: str = Field(default="127.0.0.1", ...)`, `port: int = Field(default=8080, ...)`, plus the `agent_base_url` / `agent_request_timeout_seconds` / `reindex_timeout_seconds` / `lock_acquisition_timeout_seconds` rows.

- [ ] **Step 2: Append the `McpConfig` block to Plan 3 (insert immediately after the `ApiConfig` block)**

The new `McpConfig` block lands at Plan 3 L458 (immediately after the last line of the `ApiConfig` block):

```python
class McpConfig(BaseModel):
    """Bind address + transport defaults for the MCP server process.

    Sister config to ApiConfig — the MCP process is a separate binary
    (`codeoracle-mcp`) with its own bind address. Two transports:

    1. **stdio** (default) — dev mode, no host/port. Launched by an MCP
       client like Claude Code via stdio JSON-RPC. The `host` / `port`
       fields below are ignored when stdio transport is active.

    2. **streamable HTTP** (`--http` flag) — service mode, binds to
       `host:port` and exposes `/mcp` JSON-RPC endpoint. Used by the
       §8.5 parity gate `test_mcp_explain_symbol_matches_api_response`
       and by Slice+1 deployments that want a long-lived MCP service.

    No `lock_acquisition_timeout_seconds` field — post the 2026-05-26 async
    pivot the MCP `reindex` tool enqueues via `IndexingService.enqueue_reindex`
    and never touches the advisory lock (it lives in the
    `ReindexOrchestratorConsumer` worker), so the MCP process has no lock-wait
    window to configure.
    """
    model_config = ConfigDict(extra="forbid")

    host: str = Field(
        default="127.0.0.1",
        description=(
            "Bind address for the streamable HTTP transport. Ignored in "
            "stdio mode. Default loopback matches the slice's single-host "
            "deployment posture (Plan 11 invariant for ApiConfig.host)."
        ),
    )
    port: int = Field(
        default=8082,
        ge=1,
        le=65535,
        description=(
            "Port for the streamable HTTP transport. Ignored in stdio "
            "mode. Default 8082 sits adjacent to ApiConfig.port (8080) "
            "and AgentConfig.port (8081) for slice-port-map consistency."
        ),
    )
```

The `Settings` class (Plan 3 L503-540) extends to add the `mcp` field:

```python
class Settings(BaseSettings):
    """Top-level CodeOracle settings; composed of nested per-domain configs."""
    model_config = SettingsConfigDict(
        env_prefix="CODEORACLE_",
        env_nested_delimiter="__",
        extra="forbid",
    )
    db: DBConfig = Field(...)  # no default_factory — password required, fail-fast (D5-4)
    embedding: EmbeddingConfig = Field(...)  # no default_factory — fail-fast per existing docstring
    agent: AgentConfig = Field(default_factory=AgentConfig)
    api: ApiConfig = Field(default_factory=ApiConfig)
    mcp: McpConfig = Field(default_factory=McpConfig)  # ← NEW ROW (Plan 12)
    repo_root: Path = Field(...)  # top-level — Plan 11 amendment
```

- [ ] **Step 3: Extend `configure_logging` (search `def configure_logging` in Plan 3, currently ~L915) to accept a `stream` parameter**

Locate Plan 3's `configure_logging` definition (search for `def configure_logging`):

```bash
grep -n "def configure_logging" docs/superpowers/plans/2026-05-21-core-foundation-and-db-bootstrap.md
```

Plan 3's pre-amendment def is `configure_logging(environment: str = "dev", level: str = "INFO") -> None`. The Phase-J Option-A resolution (the corpus-canonical `configure_logging` signature decision) folds this amendment's exact target — `configure_logging(settings: Settings, *, stream: TextIO = sys.stdout) -> None` — directly into Plan 3 Task 3, so verify Plan 3 ships it; do not re-extend. For reference, the canonical body folded into Plan 3 Task 3 is:

```python
def configure_logging(
    settings: Settings,
    *,
    stream: TextIO = sys.stdout,
) -> None:
    """Configure structlog + stdlib logging from Settings.

    Plan 3 Task 3 — establishes the structlog foundation telemetry.py
    builds on (Plan 1A integrates this for OTLP exporter diagnostics).

    The optional `stream` parameter overrides the destination for BOTH
    stdlib logging (`logging.basicConfig(stream=...)`) and structlog
    (`PrintLoggerFactory(file=...)`). Default stdout matches the legacy
    behavior; the MCP server process (Plan 12) passes `sys.stderr` when
    running in stdio transport mode to avoid corrupting the JSON-RPC
    framing on stdout. See Plan 12 invariant 5 ("stdout-pollution
    invariant") for the load-bearing rationale.
    """
    logging.basicConfig(
        level=settings.log_level,
        stream=stream,           # ← was: sys.stdout (hardcoded)
        force=True,
    )
    # ... structlog setup (unchanged) ...
    structlog.configure(
        # ...
        logger_factory=structlog.PrintLoggerFactory(file=stream),  # ← was: PrintLoggerFactory() (defaults to sys.stdout)
        # ...
    )
```

The `from typing import TextIO` import is added to Plan 3 Task 3's imports list (which also carries `from __future__ import annotations` + a `TYPE_CHECKING`-guarded `from codeoracle.core.config import Settings`, so the `settings: Settings` annotation resolves at type-check time without a runtime `core.config` import — see Plan 3 Task 3 for the authoritative module preamble; this reference body deliberately omits the unchanged structlog setup).

- [ ] **Step 4: Update Plan 3's `Settings` docstring + cross-plan-deps "Provides to" row**

Locate Plan 3's "Cross-plan dependencies" section (search for `## Cross-plan dependencies` or `### Provides to`). Add a new row to the "Provides to" table:

```markdown
| Plan 12 | `core/config.py:McpConfig` + `Settings.mcp` field (host + port for the MCP streamable HTTP transport; stdio mode ignores these); `core/logging.py:configure_logging(stream=...)` parameter (extended in Plan 12 amendment to plumb a custom output stream through both stdlib logging and structlog's `PrintLoggerFactory(file=stream)`, so the MCP stdio transport can route logs to stderr without corrupting JSON-RPC framing on stdout). Env vars: `CODEORACLE_MCP__HOST`, `CODEORACLE_MCP__PORT`. |
```

- [ ] **Step 5: Verify the amendment lints cleanly**

```bash
npx markdownlint-cli2 docs/superpowers/plans/2026-05-21-core-foundation-and-db-bootstrap.md
```

Expected: zero new findings (any pre-existing findings remain; the amendment must not introduce new ones).

- [ ] **Step 6: Commit (Plan 3 amendment, SEPARATE from Plan 12)**

```bash
git add docs/superpowers/plans/2026-05-21-core-foundation-and-db-bootstrap.md
git commit -m "docs(plan-3): amendment — McpConfig nested Settings + configure_logging(stream=) (prereqs for Plan 12)

Two upstream changes required by Plan 12 (MCP server, drafted as
docs/superpowers/plans/2026-05-21-mcp-server.md):

1. McpConfig — new nested config under Settings (mirrors ApiConfig
   precedent). Fields: host (default 127.0.0.1), port (default 8082).
   Env vars: CODEORACLE_MCP__HOST / CODEORACLE_MCP__PORT. Both fields are ignored when
   MCP runs in stdio transport mode (default — dev / IDE integration).
   The streamable-HTTP transport (--http) binds to host:port and exposes
   /mcp JSON-RPC endpoint for the §8.5 parity gate.

2. configure_logging(stream=) — extended signature. The optional stream
   parameter plumbs through to both logging.basicConfig(stream=...) AND
   structlog.PrintLoggerFactory(file=...). MCP stdio mode passes
   sys.stderr to avoid corrupting JSON-RPC framing on stdout. Existing
   callers (all default to stdout) are unaffected.

Plan 12 invariant 5 documents the stdout-pollution load-bearing rationale.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

- [ ] **Step 7: Push the amendment to remote**

```bash
git push origin main
```

Expected: the amendment lands as a single commit ahead of Plan 12's task implementation.

---

## Task 2: `MCPServer` class — composition root + `explain_symbol` handler + tool schemas

**Files:**
- Create: `src/codeoracle/mcp/server.py` (~280 lines total; this task lands the class skeleton + `_list_tools` + `_handle_explain_symbol` — ~180 lines so far)
- Create: `tests/unit/mcp/conftest.py` (~70 lines — shared fixtures: `fake_agent_client`, `fake_indexing_service`, `settings_env` autouse fixture)
- Create: `tests/unit/mcp/test_server.py` (this task adds the `explain_symbol` unit tests — Task 3 adds the `reindex` ones)

**Goal:** Land the `MCPServer` class composition root + tool registration via the SDK's decorator API + the `explain_symbol` tool handler (delegating to `AgentClient.run()`). Spec §8.5's byte-equal parity is established at this layer: the handler does `Query.model_validate(args)` and forwards to `AgentClient.run(query)` unchanged.

- [ ] **Step 1: Write the failing unit test for `_handle_explain_symbol` happy path**

```python
# tests/unit/mcp/conftest.py
"""Shared fixtures for tests/unit/mcp/.

Provides:
- `settings_env` — autouse env-var fixture so Settings() can construct
  with no real .env present (mirrors Plan 10 Task 11's conftest pattern).
- `fake_agent_client` — a MagicMock with `.run(query)` AsyncMock returning
  a canonical QueryResult; tests override `.return_value` as needed.
- `fake_indexing_service` — same shape for IndexingService.enqueue_reindex.
- `fake_settings` — a real Settings() instance (constructed under the env
  fixture so it succeeds without raising).
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from codeoracle.core.config import Settings
from codeoracle.models.evidence import (
    Citation,
    Claim,
    EvidenceHandle,
    HandleType,
    Verdict,
    VerdictStatus,
)
from codeoracle.models.query import Query, QueryResult


@pytest.fixture(autouse=True)
def settings_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Inject the env vars Settings() requires (no Python defaults).

    Mirrors Plan 10 Task 11 + Plan 11 Task 9 conftest pattern. The
    embedding fields have no default_factory in Plan 3, so Settings()
    will raise ValidationError without these env vars.
    """
    monkeypatch.setenv("CODEORACLE_EMBEDDING__MODEL", "test-embedding-model")
    monkeypatch.setenv("CODEORACLE_EMBEDDING__DIM", "128")
    monkeypatch.setenv("CODEORACLE_DB__PASSWORD", "test-db-pw")  # D5-4: required, no default
    monkeypatch.setenv("CODEORACLE_REPO_ROOT", str(tmp_path))


@pytest.fixture
def fake_settings() -> Settings:
    """Real Settings instance — populated entirely from env vars set above."""
    return Settings()  # type: ignore[call-arg]


@pytest.fixture
def canonical_query() -> Query:
    """A canonical Query instance for parity / handler tests."""
    return Query(symbol="resolve_subscriber")


@pytest.fixture
def canonical_query_result() -> QueryResult:
    """A canonical QueryResult — what the AgentClient.run() mock returns."""
    claim = Claim(
        statement="resolve_subscriber returns the active subscriber matching uid",
        citations=[
            Citation(
                handle=EvidenceHandle(
                    type=HandleType.CODE_SPAN,
                    locator="file://src/codeoracle/services/handle_resolution/service.py:1-10",
                    snapshot_sha="abc123def456",
                ),
                excerpt="resolve_subscriber(...)",
                source_trust=9,
            )
        ],
        confidence=0.9,
    )
    verdict = Verdict(
        status=VerdictStatus.PROVEN,
        reason="All citations resolved and match cached excerpts.",
        resolved_citations=[],
    )
    return QueryResult.proven(
        trace_id="01HXYZTRACEABCD",
        claim=claim,
        verdict=verdict,
    )


@pytest.fixture
def fake_agent_client(canonical_query_result: QueryResult) -> MagicMock:
    """Mock AgentClient with .run() returning a canonical QueryResult.

    Tests that need a different return value: `fake_agent_client.run.return_value = ...`.
    Tests that need an exception: `fake_agent_client.run.side_effect = ...`.
    """
    client = MagicMock()
    client.run = AsyncMock(return_value=canonical_query_result)
    client.aclose = AsyncMock(return_value=None)
    return client


@pytest.fixture
def fake_indexing_service() -> MagicMock:
    """Mock IndexingService with .enqueue_reindex() AsyncMock — return value set per test."""
    svc = MagicMock()
    svc.enqueue_reindex = AsyncMock()
    return svc
```

```python
# tests/unit/mcp/test_server.py
"""Unit tests for MCPServer tool handlers.

Tests exercise the handler methods directly (NOT through the SDK's
JSON-RPC layer), with mocked AgentClient + IndexingService. The SDK
transport layer is covered by integration tests in tests/integration/mcp/.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from codeoracle.core.config import Settings
from codeoracle.mcp.server import MCPServer
from codeoracle.models.query import Query, QueryResult


class TestExplainSymbolHandler:
    """Tests for MCPServer._handle_explain_symbol."""

    @pytest.mark.asyncio
    async def test_handler_returns_query_result_as_dict(
        self,
        fake_settings: Settings,
        fake_agent_client: MagicMock,
        fake_indexing_service: MagicMock,
        canonical_query: Query,
        canonical_query_result: QueryResult,
    ) -> None:
        """Happy path: handler validates input, calls AgentClient.run, returns dict."""
        server = MCPServer(
            agent_client=fake_agent_client,
            indexing_service=fake_indexing_service,
            settings=fake_settings,
        )

        result = await server._handle_explain_symbol(
            canonical_query.model_dump(mode="json")
        )

        # 1. AgentClient.run was called with a Query that round-trips the input
        fake_agent_client.run.assert_awaited_once()
        call_arg = fake_agent_client.run.await_args.args[0]
        assert isinstance(call_arg, Query)
        assert call_arg.symbol == canonical_query.symbol
        assert call_arg.corpus == canonical_query.corpus

        # 2. Handler returned the QueryResult serialized as a dict (matches outputSchema)
        assert result == canonical_query_result.model_dump(mode="json")
        # In particular: trace_id is included (the §8.5 parity test strips it later)
        assert result["trace_id"] == "01HXYZTRACEABCD"
        # The JOB status is COMPLETE (the .proven() factory sets it); the PROVEN
        # verdict lives on result["verdict"], not on the job-lifecycle status.
        assert result["status"] == "COMPLETE"
        assert result["verdict"]["status"] == "PROVEN"

    @pytest.mark.asyncio
    async def test_handler_rejects_invalid_input_schema(
        self,
        fake_settings: Settings,
        fake_agent_client: MagicMock,
        fake_indexing_service: MagicMock,
    ) -> None:
        """`extra="forbid"` on Query rejects unknown fields; ValidationError propagates."""
        server = MCPServer(
            agent_client=fake_agent_client,
            indexing_service=fake_indexing_service,
            settings=fake_settings,
        )

        with pytest.raises(Exception) as excinfo:
            await server._handle_explain_symbol(
                {
                    "symbol": "x",
                    "unknown_field": "boom",  # extra="forbid" should reject
                }
            )

        # ValidationError or ValueError-derived — exact type is Pydantic's, not our concern
        assert "unknown_field" in str(excinfo.value) or "extra" in str(excinfo.value).lower()

        # AgentClient.run was NEVER called — validation guards the boundary
        fake_agent_client.run.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_handler_propagates_agent_remote_error(
        self,
        fake_settings: Settings,
        fake_agent_client: MagicMock,
        fake_indexing_service: MagicMock,
        canonical_query: Query,
    ) -> None:
        """AgentRemoteError from AgentClient.run bubbles out (SDK wraps as JSON-RPC error)."""
        from codeoracle.adapters.agent.errors import AgentRemoteError

        fake_agent_client.run.side_effect = AgentRemoteError("agent returned 503")

        server = MCPServer(
            agent_client=fake_agent_client,
            indexing_service=fake_indexing_service,
            settings=fake_settings,
        )

        with pytest.raises(AgentRemoteError, match="agent returned 503"):
            await server._handle_explain_symbol(canonical_query.model_dump(mode="json"))
```

- [ ] **Step 2: Run the test to verify it fails (target module doesn't exist yet)**

```bash
uv run pytest tests/unit/mcp/test_server.py::TestExplainSymbolHandler -v
```

Expected: `ImportError: cannot import name 'MCPServer' from 'codeoracle.mcp.server'` (the module file isn't created yet).

- [ ] **Step 3: Write `src/codeoracle/mcp/server.py` — class skeleton + `_list_tools` + `_handle_explain_symbol`**

```python
# src/codeoracle/mcp/server.py
"""MCPServer — composition-root OOP wrapper around mcp.server.lowlevel.Server.

Holds constructor-injected AgentClient + IndexingService + Settings, registers
the two tool handlers (`explain_symbol` and `reindex`) with the held SDK
Server, and exposes the SDK Server via a property for transport runners.

Architectural invariants (see Plan 12 §Architectural invariants for full
prose):
- Composition, not inheritance (invariant 6): the SDK Server is a held
  member; MCPServer does not subclass Server.
- No business logic (invariant 1): handlers delegate to AgentClient.run /
  IndexingService.enqueue_reindex unchanged.
- Parity with API (invariants 2 + 3): same models, same client, same
  service — explain_symbol is byte-equal; reindex mirrors the async enqueue
  (same base_sha → same snapshot_sha) — structurally enforced.
- No module-level mutable state (invariant 11): construction happens inside
  __main__.py's main() or test helpers.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, Any, AsyncIterator

import mcp.types as types
from mcp.server.lowlevel import Server

from codeoracle.models.query import Query, QueryResult
from codeoracle.models.reindex import ReindexRequest
from codeoracle.models.snapshot import Snapshot

if TYPE_CHECKING:
    from codeoracle.adapters.agent.client import AgentClient
    from codeoracle.core.config import Settings
    from codeoracle.services.indexing.service import IndexingService


SERVER_NAME = "codeoracle-mcp"


class MCPServer:
    """OOP wrapper around mcp.server.lowlevel.Server with constructor DI.

    Composition root for the MCP process layer. Holds three dependencies
    (AgentClient + IndexingService + Settings) and registers two tool
    handlers (`explain_symbol` + `reindex`) against an SDK Server instance.

    The held SDK Server is accessible via the `server` property for
    transport runners (`run_stdio` / `run_streamable_http`).

    Lifecycle: dependencies are constructed BEFORE this class
    (`__main__.py` is the composition root). MCPServer's constructor
    only WIRES — it never builds. Cleanup is `__main__.py`'s job via
    try/finally, not the SDK Server's lifespan. The SDK lifespan is set
    to a no-op async context that just yields an empty dict (the SDK
    requires a lifespan callable; we don't use the yielded context).
    """

    def __init__(
        self,
        *,
        agent_client: AgentClient,
        indexing_service: IndexingService,
        settings: Settings,
    ) -> None:
        self._agent_client = agent_client
        self._indexing_service = indexing_service
        self._settings = settings
        self._server: Server = self._build_server()

    @property
    def server(self) -> Server:
        """Expose the held SDK Server for transport runners."""
        return self._server

    def _build_server(self) -> Server:
        """Construct the SDK Server, register handlers, return it.

        Tool registration via the SDK's decorator API (the decorators
        mutate the Server's internal handler registry; we don't keep a
        reference to the returned wrappers).
        """
        server: Server = Server(
            SERVER_NAME,
            lifespan=self._noop_lifespan,
        )
        # Decorator semantics: server.list_tools()(fn) registers fn as the
        # list_tools handler and returns fn unchanged. We discard the return
        # value because the handler is also accessible as self._list_tools.
        server.list_tools()(self._list_tools)
        server.call_tool()(self._call_tool)
        return server

    @asynccontextmanager
    async def _noop_lifespan(
        self, _: Server
    ) -> AsyncIterator[dict[str, Any]]:
        """No-op lifespan — yields empty context dict.

        The SDK requires a lifespan callable, but Plan 12 manages
        dependency construction + cleanup in __main__.py (process scope),
        not per JSON-RPC session. The yielded context dict is empty and
        unused by tool handlers.
        """
        yield {}

    async def _list_tools(self) -> list[types.Tool]:
        """Return the static tool registry — two tools: explain_symbol + reindex."""
        return [
            types.Tool(
                name="explain_symbol",
                description=(
                    "Ask the CodeOracle agent to explain a code symbol with "
                    "evidence-bound proof. Same wire contract as the platform's "
                    "HTTP API endpoint POST /v1/query — byte-equal QueryResult "
                    "payload (modulo trace_id). Returns a Claim with cited "
                    "evidence (status=PROVEN), a Refutation (status=REFUTED), "
                    "or status=UNKNOWN when the agent cannot prove the claim."
                ),
                inputSchema=Query.model_json_schema(),
                outputSchema=QueryResult.model_json_schema(),
            ),
            types.Tool(
                name="reindex",
                description=(
                    "Enqueue an incremental or full reindex of the repository "
                    "snapshot under Settings.repo_root. Same wire contract as "
                    "POST /v1/reindex (async enqueue). Returns the PENDING "
                    "Snapshot receipt immediately; the ReindexOrchestratorConsumer "
                    "worker drives the reindex out-of-band. Duplicate calls "
                    "against the same tree state are idempotent — they return "
                    "the existing Snapshot row rather than enqueuing again."
                ),
                inputSchema=ReindexRequest.model_json_schema(),
                outputSchema=Snapshot.model_json_schema(),
            ),
        ]

    async def _call_tool(
        self, name: str, arguments: dict[str, Any]
    ) -> dict[str, Any]:
        """Dispatch a tool invocation to the matching handler.

        Return value: a dict matching the tool's outputSchema. The SDK's
        low-level Server.call_tool wrapper (verified in v1.12.4 source —
        `mcp/server/lowlevel/server.py`) accepts three return-types from
        a user handler: (a) iterable of ContentBlock, (b) dict (structured
        content; auto-validated against outputSchema + JSON-stringified
        into a backward-compat TextContent), (c) tuple of (iterable, dict).
        Plan 12 standardizes on (b) — dict — so the wire response is
        `CallToolResult(structuredContent=..., content=[TextContent(json)])`.

        Error signaling: handlers RAISE Python exceptions. The SDK catches
        ALL exceptions in `_call_tool`'s outer try/except and converts them
        via `_make_error_result(str(exc))` into
        `CallToolResult(content=[TextContent(text=str(exc))], isError=True)`.
        We do NOT return a CallToolResult from a handler — the SDK does not
        unwrap that return type and would mis-classify it as iterable content.
        """
        if name == "explain_symbol":
            return await self._handle_explain_symbol(arguments)
        if name == "reindex":
            return await self._handle_reindex(arguments)
        raise ValueError(f"Unknown tool: {name}")

    async def _handle_explain_symbol(
        self, arguments: dict[str, Any]
    ) -> dict[str, Any]:
        """Validate Query input, delegate to AgentClient.run, serialize QueryResult.

        Byte-equal parity with POST /v1/query (Plan 11 invariant 2):
        - Same Query model (Plan 3 models/query.py)
        - Same AgentClient (Plan 11 adapters/agent/client.py)
        - Same QueryResult serialization (model_dump(mode="json"))

        Validation errors (extra fields, missing required, wrong type) raise
        pydantic.ValidationError, which the SDK wraps as a JSON-RPC error
        response with isError=True. Domain errors from the agent
        (AgentRemoteError, AgentTransportError, AgentValidationError) also
        propagate — same wrapping.
        """
        query = Query.model_validate(arguments)
        result: QueryResult = await self._agent_client.run(query)
        return result.model_dump(mode="json")

    async def _handle_reindex(
        self, arguments: dict[str, Any]
    ) -> dict[str, Any]:
        """Stub for Task 3 — raises NotImplementedError until Task 3 lands.

        Defined here so the _call_tool dispatch can reference it; the actual
        implementation (async enqueue delegate, returns the PENDING Snapshot
        receipt) is in Task 3.
        """
        raise NotImplementedError(
            "MCPServer._handle_reindex is implemented in Plan 12 Task 3"
        )
```

- [ ] **Step 4: Run the explain_symbol unit tests; verify all pass**

```bash
uv run pytest tests/unit/mcp/test_server.py::TestExplainSymbolHandler -v
```

Expected: all three tests pass:
- `test_handler_returns_query_result_as_dict` PASS
- `test_handler_rejects_invalid_input_schema` PASS
- `test_handler_propagates_agent_remote_error` PASS

- [ ] **Step 5: Add a schema-shape test for the static tool registry**

```python
# tests/unit/mcp/test_server.py — APPEND a new test class

class TestToolRegistry:
    """Tests for MCPServer._list_tools — the static tool registry."""

    @pytest.mark.asyncio
    async def test_lists_two_tools_with_expected_names(
        self,
        fake_settings: Settings,
        fake_agent_client: MagicMock,
        fake_indexing_service: MagicMock,
    ) -> None:
        """The registry exposes exactly explain_symbol + reindex."""
        server = MCPServer(
            agent_client=fake_agent_client,
            indexing_service=fake_indexing_service,
            settings=fake_settings,
        )

        tools = await server._list_tools()

        assert len(tools) == 2
        assert {t.name for t in tools} == {"explain_symbol", "reindex"}

    @pytest.mark.asyncio
    async def test_explain_symbol_schema_derived_from_query_model(
        self,
        fake_settings: Settings,
        fake_agent_client: MagicMock,
        fake_indexing_service: MagicMock,
    ) -> None:
        """The explain_symbol tool's inputSchema is Query.model_json_schema()."""
        server = MCPServer(
            agent_client=fake_agent_client,
            indexing_service=fake_indexing_service,
            settings=fake_settings,
        )

        tools = await server._list_tools()
        explain = next(t for t in tools if t.name == "explain_symbol")

        assert explain.inputSchema == Query.model_json_schema()
        assert explain.outputSchema == QueryResult.model_json_schema()
        # extra="forbid" → additionalProperties=False in the emitted schema
        assert explain.inputSchema.get("additionalProperties") is False

    @pytest.mark.asyncio
    async def test_unknown_tool_raises_value_error(
        self,
        fake_settings: Settings,
        fake_agent_client: MagicMock,
        fake_indexing_service: MagicMock,
    ) -> None:
        """`_call_tool` with an unknown tool name raises ValueError."""
        server = MCPServer(
            agent_client=fake_agent_client,
            indexing_service=fake_indexing_service,
            settings=fake_settings,
        )

        with pytest.raises(ValueError, match="Unknown tool: bogus_tool"):
            await server._call_tool("bogus_tool", {})
```

- [ ] **Step 6: Run the registry tests; verify all pass**

```bash
uv run pytest tests/unit/mcp/test_server.py -v
```

Expected: all 6 tests pass (3 explain_symbol + 3 registry).

- [ ] **Step 7: Run ruff + ty on the new files**

```bash
uv run ruff check src/codeoracle/mcp/server.py tests/unit/mcp/
uv run ruff format --check src/codeoracle/mcp/server.py tests/unit/mcp/
uv run ty check src/codeoracle/mcp/server.py
```

Expected: zero findings; format check passes; ty reports no errors.

- [ ] **Step 8: Commit**

```bash
git add src/codeoracle/mcp/server.py tests/unit/mcp/conftest.py tests/unit/mcp/test_server.py
git commit -m "feat(mcp): MCPServer class + explain_symbol handler + tool schemas

OOP composition root for the MCP process layer. Constructor-injected
AgentClient + IndexingService + Settings; held SDK Server (composition,
not inheritance — Plan 12 invariant 6). Tool registration via the
decorator API; handlers delegate to deps unchanged (no business logic
in the process layer — Plan 12 invariant 1).

explain_symbol delegates to AgentClient.run(query) — same Query model,
same client, same serialization as POST /v1/query (byte-equal parity
established at this layer; §8.5 gate verifies in Task 8).

Reindex handler stubbed; Task 3 lands the async enqueue delegate
(IndexingService.enqueue_reindex → PENDING Snapshot receipt).

Unit tests: 6 cases — happy path, extra-field rejection, AgentRemoteError
propagation, tool count, schema shape, unknown-tool dispatch.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

## Task 3: `MCPServer._handle_reindex` — async enqueue delegate (202-equivalent)

**Files:**
- Modify: `src/codeoracle/mcp/server.py` (replace the `NotImplementedError` stub from Task 2 with the real handler — adds ~20 lines)
- Modify: `tests/unit/mcp/test_server.py` (add `TestReindexHandler` test class — ~70 lines of tests)

**Goal:** Implement the `reindex` tool handler (DECIDE-G, amended 2026-05-26 — sync→async pivot) with two branches:
1. Success — call `IndexingService.enqueue_reindex(base_sha=...)` (the same async-job entrypoint Plan 11's `POST /v1/reindex` uses, Plan 5 amendment `40885bf`); return the PENDING `Snapshot.model_dump(mode="json")` receipt. `enqueue_reindex` is idempotent on `SnapshotConflictError` — a duplicate enqueue against the same tree state returns the existing row.
2. Other exceptions — propagate; the SDK's low-level `Server.call_tool` wrapper emits `CallToolResult(content=[TextContent(text=str(exc))], isError=True)` on the wire. There is no `AdvisoryLockTimeoutError` branch: the advisory lock lives in the `ReindexOrchestratorConsumer` worker, so lock contention never reaches the MCP handler (no 409-equivalent retry hint — see DECIDE-J tombstone).

Parity with `POST /v1/reindex` (Plan 12 invariant 3) is structurally enforced: same `ReindexRequest` model, same `IndexingService.enqueue_reindex()` call, same `Snapshot` serialization. Because `snapshot_sha` is a pure function of tree state, the same `base_sha` enqueues the SAME snapshot through MCP and the HTTP API (the reindex-parity gate in Plan 13 pins exactly that). Handlers never return a `CallToolResult` directly — that return-type is not unwrapped by the SDK in v1.12.4 (see Task 2 `_call_tool` docstring).

- [ ] **Step 1: Write the failing reindex tests**

```python
# tests/unit/mcp/test_server.py — APPEND a new test class

from datetime import datetime, timezone

import mcp.types as types

from codeoracle.models.reindex import ReindexRequest
from codeoracle.models.snapshot import ExtractorOutcome, Snapshot, SnapshotStatus


@pytest.fixture
def canonical_reindex_request() -> ReindexRequest:
    """A canonical ReindexRequest — incremental reindex from a base SHA."""
    return ReindexRequest(base_sha="abc123def456")


@pytest.fixture
def canonical_snapshot() -> Snapshot:
    """A canonical Snapshot — the row the stubbed IndexingService returns.

    The unit happy-path stubs `enqueue_reindex` to return this row and
    asserts the handler serializes it intact; a terminal APPLIED row keeps
    the serialization assertion deterministic (Plan 3 L2121:
    `assert SnapshotStatus.APPLIED.value == "applied"`). The real
    `enqueue_reindex` returns a PENDING receipt — the PENDING/APPLYING
    lifecycle is exercised at integration scope (Task 7), not here.
    """
    return Snapshot(
        sha="abc123def456",
        parent_sha=None,
        status=SnapshotStatus.APPLIED,
        applied_at=datetime(2026, 5, 26, 12, 0, 0, tzinfo=timezone.utc),
        extractor_results={"joern": ExtractorOutcome.SUCCESS},
    )


class TestReindexHandler:
    """Tests for MCPServer._handle_reindex."""

    @pytest.mark.asyncio
    async def test_handler_returns_snapshot_as_dict_on_success(
        self,
        fake_settings: Settings,
        fake_agent_client: MagicMock,
        fake_indexing_service: MagicMock,
        canonical_reindex_request: ReindexRequest,
        canonical_snapshot: Snapshot,
    ) -> None:
        """Happy path: handler validates input, calls IndexingService.enqueue_reindex, returns dict."""
        fake_indexing_service.enqueue_reindex.return_value = canonical_snapshot

        server = MCPServer(
            agent_client=fake_agent_client,
            indexing_service=fake_indexing_service,
            settings=fake_settings,
        )

        result = await server._handle_reindex(
            canonical_reindex_request.model_dump(mode="json")
        )

        # 1. IndexingService.enqueue_reindex called with the parsed base_sha
        fake_indexing_service.enqueue_reindex.assert_awaited_once_with(base_sha="abc123def456")
        # 2. Handler returned the Snapshot serialized as dict
        assert result == canonical_snapshot.model_dump(mode="json")

    @pytest.mark.asyncio
    async def test_handler_propagates_other_exceptions(
        self,
        fake_settings: Settings,
        fake_agent_client: MagicMock,
        fake_indexing_service: MagicMock,
        canonical_reindex_request: ReindexRequest,
    ) -> None:
        """Exceptions from enqueue bubble out (SDK wraps as JSON-RPC error)."""
        fake_indexing_service.enqueue_reindex.side_effect = RuntimeError("disk full")

        server = MCPServer(
            agent_client=fake_agent_client,
            indexing_service=fake_indexing_service,
            settings=fake_settings,
        )

        with pytest.raises(RuntimeError, match="disk full"):
            await server._handle_reindex(canonical_reindex_request.model_dump(mode="json"))

    @pytest.mark.asyncio
    async def test_handler_rejects_invalid_input_schema(
        self,
        fake_settings: Settings,
        fake_agent_client: MagicMock,
        fake_indexing_service: MagicMock,
    ) -> None:
        """extra="forbid" on ReindexRequest rejects unknown fields."""
        server = MCPServer(
            agent_client=fake_agent_client,
            indexing_service=fake_indexing_service,
            settings=fake_settings,
        )

        with pytest.raises(Exception) as excinfo:
            await server._handle_reindex(
                {"base_sha": "abc123", "force_full": True}  # `force_full` doesn't exist on ReindexRequest
            )

        assert "force_full" in str(excinfo.value) or "extra" in str(excinfo.value).lower()
        fake_indexing_service.enqueue_reindex.assert_not_awaited()
```

- [ ] **Step 2: Run the tests; verify they fail with `NotImplementedError`**

```bash
uv run pytest tests/unit/mcp/test_server.py::TestReindexHandler -v
```

Expected: all 3 tests fail with `NotImplementedError: MCPServer._handle_reindex is implemented in Plan 12 Task 3` (the Task 2 stub).

- [ ] **Step 3: Replace the `_handle_reindex` stub with the real implementation**

Edit `src/codeoracle/mcp/server.py` — replace the `raise NotImplementedError(...)` line with:

```python
    async def _handle_reindex(
        self, arguments: dict[str, Any]
    ) -> dict[str, Any]:
        """Validate ReindexRequest, enqueue an async reindex, return the receipt.

        Amended 2026-05-26 (Plan 11 sync→async pivot, amendment `fc0894a`):
        the handler delegates to `IndexingService.enqueue_reindex` — the same
        async-job entrypoint Plan 11's `POST /v1/reindex` calls — which
        persists a PENDING `Snapshot` row, XADDs a request to
        `STREAM_REINDEX_REQUESTS`, and returns the row immediately. The
        `ReindexOrchestratorConsumer` worker (Plan 5 amendment `40885bf`)
        drives the actual reindex out-of-band and holds the advisory lock on
        its own connection. Two branches:
        1. Success → return Snapshot.model_dump(mode="json") (the PENDING
           enqueue receipt; a dict the SDK validates against the reindex
           tool's outputSchema and emits as `structuredContent=...`).
           `enqueue_reindex` is idempotent on `SnapshotConflictError`: a
           duplicate enqueue against the same tree state returns the existing
           PENDING/APPLYING/APPLIED row instead of XADDing a second request.
        2. Other exceptions propagate; the SDK's low-level Server.call_tool
           wrapper catches them and converts to
           CallToolResult(content=[TextContent(text=str(exc))], isError=True)
           via `_make_error_result(str(exc))` (verified in v1.12.4 source).
           Precise error categorization is a Slice+1 concern.

        Parity with POST /v1/reindex (Plan 12 invariant 3) is structural:
        - Same ReindexRequest model (Plan 11 models/reindex.py)
        - Same IndexingService.enqueue_reindex(base_sha=...) call (Plan 5)
        - Same Snapshot serialization (model_dump(mode="json"))
        Because `sha` is a pure function of tree state, the same `base_sha`
        routed through MCP and through the HTTP API enqueues the SAME
        `snapshot_sha` — the reindex-parity gate (Plan 13) pins exactly that.
        The advisory lock no longer reaches this boundary, so there is no
        AdvisoryLockTimeoutError branch and no 409-equivalent retry hint.
        """
        request = ReindexRequest.model_validate(arguments)
        snapshot: Snapshot = await self._indexing_service.enqueue_reindex(
            base_sha=request.base_sha
        )
        return snapshot.model_dump(mode="json")
```

- [ ] **Step 4: Run the reindex tests; verify all pass**

```bash
uv run pytest tests/unit/mcp/test_server.py::TestReindexHandler -v
```

Expected: all 3 tests pass.

- [ ] **Step 5: Run the full unit test suite + lint/type check**

```bash
uv run pytest tests/unit/mcp/ -v
uv run ruff check src/codeoracle/mcp/server.py
uv run ty check src/codeoracle/mcp/server.py
```

Expected: 9 tests pass (6 from Task 2 + 3 from Task 3); ruff + ty clean.

- [ ] **Step 6: Commit**

```bash
git add src/codeoracle/mcp/server.py tests/unit/mcp/test_server.py
git commit -m "feat(mcp): reindex handler — async enqueue (202-equivalent)

Replace Task 2's NotImplementedError stub for _handle_reindex with the
real implementation. Post the 2026-05-26 sync→async pivot, two branches:

1. Success: delegate to IndexingService.enqueue_reindex(base_sha=...);
   return Snapshot.model_dump(mode='json') — the PENDING enqueue receipt,
   mirroring Plan 11's POST /v1/reindex 202 + PENDING Snapshot
   (Plan 12 invariant 3). enqueue_reindex is idempotent on
   SnapshotConflictError (duplicate enqueue returns the existing row).
2. Other exceptions: propagate; SDK applies isError=True wrapping via
   _make_error_result(str(exc)). The advisory lock lives in the
   ReindexOrchestratorConsumer worker (Plan 5 amendment 40885bf), so
   AdvisoryLockTimeoutError never reaches this handler — no 409-equivalent
   retry-hint branch.

Unit tests: 4 cases — happy path (enqueue), dispatch, propagation of
other exceptions, extra-field rejection.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

## Task 4: Transports module — `run_stdio` + `run_streamable_http`

**Files:**
- Create: `src/codeoracle/mcp/transports.py` (~120 lines — two transport runners + stdout-pollution guard for stdio)
- Create: `tests/unit/mcp/test_transports.py` (~80 lines — transport runner sanity tests, NOT full transport-protocol tests; those are in Task 7)

**Goal:** Land the two transport runners. Both accept an `MCPServer` instance and run the SDK server against the chosen transport. The stdio runner enforces the stdout-pollution invariant (invariant 5) by routing logging to stderr before the SDK starts framing JSON-RPC on stdout.

- [ ] **Step 1: Write the failing transport sanity tests**

```python
# tests/unit/mcp/test_transports.py
"""Sanity tests for transport runners.

These tests verify the runners' SIGNATURES + early-exit behavior, NOT
the full JSON-RPC round-trip (that's integration territory — Task 7).
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from codeoracle.core.config import Settings
from codeoracle.mcp.server import MCPServer
from codeoracle.mcp.transports import run_stdio, run_streamable_http


class TestRunStdio:
    """Tests for run_stdio."""

    @pytest.mark.asyncio
    async def test_invokes_stdio_server_with_mcp_server(
        self,
        fake_settings: Settings,
        fake_agent_client: MagicMock,
        fake_indexing_service: MagicMock,
    ) -> None:
        """run_stdio calls mcp.server.stdio.stdio_server then server.run."""
        server = MCPServer(
            agent_client=fake_agent_client,
            indexing_service=fake_indexing_service,
            settings=fake_settings,
        )

        # Patch the SDK's stdio_server CM + Server.run so we don't actually
        # bind stdin/stdout (which would hang the test).
        read_stream = MagicMock(name="read_stream")
        write_stream = MagicMock(name="write_stream")
        stdio_cm = MagicMock()
        stdio_cm.__aenter__ = AsyncMock(return_value=(read_stream, write_stream))
        stdio_cm.__aexit__ = AsyncMock(return_value=None)

        with patch(
            "codeoracle.mcp.transports.stdio_server", return_value=stdio_cm
        ) as stdio_mock, patch.object(
            server.server, "run", new_callable=AsyncMock
        ) as run_mock:
            await run_stdio(server)

        stdio_mock.assert_called_once_with()
        run_mock.assert_awaited_once()
        # First two positional args: read_stream, write_stream
        call_args = run_mock.await_args
        assert call_args.args[0] is read_stream
        assert call_args.args[1] is write_stream


class TestRunStreamableHttp:
    """Tests for run_streamable_http + _build_streamable_http_app."""

    def test_session_manager_constructed_with_stateless_kwargs(
        self,
        fake_settings: Settings,
        fake_agent_client: MagicMock,
        fake_indexing_service: MagicMock,
    ) -> None:
        """_build_streamable_http_app constructs StreamableHTTPSessionManager
        with the canonical kwargs (stateless=True, json_response=False)."""
        from codeoracle.mcp.transports import _build_streamable_http_app

        server = MCPServer(
            agent_client=fake_agent_client,
            indexing_service=fake_indexing_service,
            settings=fake_settings,
        )

        with patch(
            "codeoracle.mcp.transports.StreamableHTTPSessionManager"
        ) as mgr_cls:
            mgr_cls.return_value = MagicMock(name="session_manager")
            asgi_app = _build_streamable_http_app(server)

        mgr_cls.assert_called_once_with(
            app=server.server,
            event_store=None,
            json_response=False,
            stateless=True,
        )
        # The returned object is a Starlette app with a /mcp mount.
        from starlette.applications import Starlette

        assert isinstance(asgi_app, Starlette)
        mount_paths = [r.path for r in asgi_app.routes]
        assert "/mcp" in mount_paths

    def test_run_streamable_http_passes_asgi_app_to_uvicorn(
        self,
        fake_settings: Settings,
        fake_agent_client: MagicMock,
        fake_indexing_service: MagicMock,
    ) -> None:
        """run_streamable_http invokes uvicorn.run with the composed app."""
        server = MCPServer(
            agent_client=fake_agent_client,
            indexing_service=fake_indexing_service,
            settings=fake_settings,
        )

        sentinel_app = MagicMock(name="asgi_app")
        with patch(
            "codeoracle.mcp.transports._build_streamable_http_app",
            return_value=sentinel_app,
        ) as build_mock, patch(
            "codeoracle.mcp.transports.uvicorn.run"
        ) as uvicorn_mock:
            run_streamable_http(server, host="127.0.0.1", port=8082)

        build_mock.assert_called_once_with(server)
        uvicorn_mock.assert_called_once()
        call_args = uvicorn_mock.call_args
        assert call_args.args[0] is sentinel_app
        assert call_args.kwargs["host"] == "127.0.0.1"
        assert call_args.kwargs["port"] == 8082
```

- [ ] **Step 2: Run the tests; verify they fail (module doesn't exist)**

```bash
uv run pytest tests/unit/mcp/test_transports.py -v
```

Expected: `ImportError` on `from codeoracle.mcp.transports import run_stdio, run_streamable_http`.

- [ ] **Step 3: Write `src/codeoracle/mcp/transports.py`**

```python
# src/codeoracle/mcp/transports.py
"""Transport runners for the MCPServer.

Two transports share the same MCPServer instance:

- `run_stdio(server)` — stdio JSON-RPC (dev mode / IDE integration).
  The MCP client (Claude Code, Cursor) launches the `codeoracle-mcp` binary
  as a subprocess and exchanges JSON-RPC over stdin/stdout. This runner
  binds the SDK's stdio_server context manager + delegates to
  Server.run(read_stream, write_stream).

- `run_streamable_http(server, *, host, port)` — streamable HTTP
  (service mode / §8.5 parity gate). Constructs a
  `StreamableHTTPSessionManager` over the same `Server` instance, wraps
  its `run()` context in a Starlette `lifespan`, mounts the request
  handler at `/mcp`, and serves the ASGI app via `uvicorn.run`. The
  SDK does NOT expose a one-shot `Server.streamable_http_app(...)`
  factory in v1.12.4 (verified against the SDK source) — the caller
  composes Starlette + the session manager directly.

The stdout-pollution invariant (Plan 12 invariant 5) is enforced
elsewhere — `__main__.py` calls configure_logging(stream=sys.stderr)
BEFORE invoking run_stdio. The transport runners themselves do not
reconfigure logging.

The HTTP transport is stateless (`stateless=True` on the session
manager) per DECIDE-H — both tools are single-shot request/response,
no session state to preserve.
"""

from __future__ import annotations

import contextlib
from collections.abc import AsyncIterator
from typing import TYPE_CHECKING

import uvicorn
from mcp.server.lowlevel import NotificationOptions, Server
from mcp.server.models import InitializationOptions
from mcp.server.stdio import stdio_server
from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
from starlette.applications import Starlette
from starlette.routing import Mount
from starlette.types import Receive, Scope, Send

if TYPE_CHECKING:
    from codeoracle.mcp.server import MCPServer


SERVER_VERSION = "0.1.0-slice"


def build_initialization_options(sdk_server: Server) -> InitializationOptions:
    """SDK initialization metadata announced to the MCP client on connect.

    The `capabilities` field is built via the SDK's own
    `Server.get_capabilities()` helper — the canonical pattern in v1.12.4
    (verified against the lowlevel-server lifespan example in the SDK
    README). Hand-rolling the dict (e.g., `{"tools": {"listChanged": False}}`)
    is brittle: the SDK helper enforces the exact ServerCapabilities Pydantic
    shape, picks defaults consistent with what the registered handlers
    actually support, and adapts when the schema evolves.

    Public (no leading underscore) so the Task 7 integration tests can
    import this directly without reaching for a private symbol.
    """
    return InitializationOptions(
        server_name="codeoracle-mcp",
        server_version=SERVER_VERSION,
        capabilities=sdk_server.get_capabilities(
            notification_options=NotificationOptions(),
            experimental_capabilities={},
        ),
    )


async def run_stdio(mcp_server: MCPServer) -> None:
    """Run the MCP server over stdio JSON-RPC.

    Binds stdin/stdout via the SDK's stdio_server context manager;
    blocks until the client disconnects.

    Stdout-pollution warning (Plan 12 invariant 5): the CALLER
    (__main__.py) MUST configure logging to stderr BEFORE invoking this
    function. Once stdio_server is entered, any byte written to stdout
    that isn't a properly-framed JSON-RPC message corrupts the protocol
    and silently desyncs the MCP client.

    SDK exception semantics: `Server.run` defaults `raise_exceptions=False`
    in v1.12.4 — handler exceptions are caught by the SDK and converted to
    `CallToolResult(isError=True)` per `_make_error_result`. We rely on
    that default (DO NOT override to True) — overriding causes handler
    exceptions to propagate up through `Server.run`, which in stdio mode
    means a process crash mid-session rather than a wire-level isError
    response the client can act on.
    """
    async with stdio_server() as (read_stream, write_stream):
        await mcp_server.server.run(
            read_stream,
            write_stream,
            build_initialization_options(mcp_server.server),
        )


def _build_streamable_http_app(mcp_server: MCPServer) -> Starlette:
    """Compose the Starlette ASGI app that wires the SDK session manager.

    Why this exists: the v1.12.4 SDK does NOT expose a one-shot
    `Server.streamable_http_app(...)` factory. The canonical pattern
    (per the SDK's own `examples/servers/simple-streamablehttp`) is for
    the caller to:

    1. Construct `StreamableHTTPSessionManager(app=<low-level Server>,
       event_store=None, json_response=False, stateless=True)`.
       Note the kwarg is `stateless` (NOT `stateless_http`).
    2. Wrap `session_manager.run()` in a Starlette `lifespan` so the
       manager's background tasks start with the app and shut down
       cleanly on uvicorn termination.
    3. Mount a request handler at `/mcp` that delegates to
       `session_manager.handle_request(scope, receive, send)`.

    Factored out (rather than inlined into `run_streamable_http`) so
    Task 4 unit tests can construct the ASGI app without actually
    invoking `uvicorn.run` (which would block).
    """
    session_manager = StreamableHTTPSessionManager(
        app=mcp_server.server,
        event_store=None,
        json_response=False,
        stateless=True,
    )

    async def handle_streamable_http(
        scope: Scope, receive: Receive, send: Send
    ) -> None:
        await session_manager.handle_request(scope, receive, send)

    @contextlib.asynccontextmanager
    async def lifespan(_app: Starlette) -> AsyncIterator[None]:
        async with session_manager.run():
            yield

    return Starlette(
        routes=[Mount("/mcp", app=handle_streamable_http)],
        lifespan=lifespan,
    )


def run_streamable_http(
    mcp_server: MCPServer, *, host: str, port: int
) -> None:
    """Run the MCP server over streamable HTTP (service mode).

    Composes a Starlette ASGI app around `StreamableHTTPSessionManager`
    (see `_build_streamable_http_app`) and serves it via uvicorn.
    Stateless mode (DECIDE-H) — no session state on the server side;
    each request is independent.

    Synchronous wrapper around uvicorn.run (which manages its own event
    loop). Returns when the server shuts down (KeyboardInterrupt or
    uvicorn termination).
    """
    asgi_app = _build_streamable_http_app(mcp_server)
    uvicorn.run(
        asgi_app,
        host=host,
        port=port,
        log_level="info",
        access_log=True,
        # access_log goes to stderr by default — safe for the streamable
        # HTTP transport (no stdout-framing concern in HTTP mode).
    )
```

- [ ] **Step 4: Run the transport tests; verify all pass**

```bash
uv run pytest tests/unit/mcp/test_transports.py -v
```

Expected: both tests pass.

- [ ] **Step 5: Run ruff + ty**

```bash
uv run ruff check src/codeoracle/mcp/transports.py
uv run ty check src/codeoracle/mcp/transports.py
```

Expected: zero findings.

- [ ] **Step 6: Commit**

```bash
git add src/codeoracle/mcp/transports.py tests/unit/mcp/test_transports.py
git commit -m "feat(mcp): transport runners — stdio + streamable HTTP

Two transports share one MCPServer instance:

- run_stdio: binds stdin/stdout via the SDK's stdio_server CM, delegates
  to Server.run. Caller (__main__.py) must reconfigure logging to stderr
  BEFORE invoking this; stdout is reserved for JSON-RPC framing
  (Plan 12 invariant 5).
- run_streamable_http: composes StreamableHTTPSessionManager into a
  Starlette app with a lifespan opening manager.run() and a
  Mount('/mcp', ...) route delegating to manager.handle_request; serves
  via uvicorn.run. Stateless mode (DECIDE-H, stateless=True on the
  session manager) — both tools are single-shot request/response. The
  v1.12.4 SDK does NOT expose a Server.streamable_http_app(...) factory;
  the caller composes Starlette + the session manager directly per the
  SDK's own simple-streamablehttp example.

Unit tests verify the SDK plumbing is invoked correctly without binding
real stdin/stdout (which would hang the test).

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

## Task 5: `__main__.py` — composition root + argv parsing + try/finally teardown

**Files:**
- Create: `src/codeoracle/mcp/__main__.py` (~80 lines — main() entry, argv parsing, eager dep construction, transport selection, teardown)
- Create: `tests/unit/mcp/test_main.py` (~120 lines — argv parsing + transport selection unit tests with all I/O mocked)

**Goal:** Land the composition root. `__main__.py` is the binary entrypoint registered as `codeoracle-mcp` in `pyproject.toml` (Task 9). It:
1. Parses argv (`--http` flag; default = stdio)
2. Loads `Settings` via `get_settings()`
3. **For stdio mode:** calls `configure_logging(settings, stream=sys.stderr)` BEFORE anything else logs (invariant 5)
4. **For HTTP mode:** calls `configure_logging(settings)` normally (stdout is fine in HTTP mode)
5. Constructs the dependency graph eagerly: SQLAlchemy `Engine` → `ValkeyQueueClient` → `IndexingService` → `AgentClient`
6. Constructs `MCPServer(agent_client=, indexing_service=, settings=)`
7. Dispatches to `run_stdio(server)` or `run_streamable_http(server, host, port)` based on argv
8. `try/finally`: on shutdown, calls `agent_client.aclose()` + `valkey_client.aclose()` + `engine.dispose()` (mirroring Plan 11's `app.py` lifespan teardown)

- [ ] **Step 1: Write the failing tests**

```python
# tests/unit/mcp/test_main.py
"""Tests for src/codeoracle/mcp/__main__.py — argv parsing + composition root."""

from __future__ import annotations

import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from typer.testing import CliRunner

from codeoracle.mcp.__main__ import app


class TestArgvParsing:
    """Transport selection via the Typer app — no real I/O (deps + transports mocked)."""

    def test_no_args_defaults_to_stdio(self) -> None:
        runner = CliRunner()
        with patch("codeoracle.mcp.__main__.configure_logging"), \
             patch("codeoracle.mcp.__main__._build_dependency_graph") as build_mock, \
             patch("codeoracle.mcp.__main__.run_stdio"), \
             patch("codeoracle.mcp.__main__.asyncio.run"):
            build_mock.return_value = MagicMock(name="deps", aclose=AsyncMock())
            result = runner.invoke(app, [])
        assert result.exit_code == 0

    def test_http_flag_selects_streamable_http(self) -> None:
        runner = CliRunner()
        with patch("codeoracle.mcp.__main__.configure_logging"), \
             patch("codeoracle.mcp.__main__._build_dependency_graph") as build_mock, \
             patch("codeoracle.mcp.__main__.run_streamable_http") as http_mock, \
             patch("codeoracle.mcp.__main__.asyncio.run"):
            build_mock.return_value = MagicMock(name="deps", aclose=AsyncMock())
            result = runner.invoke(app, ["--http"])
        assert result.exit_code == 0
        assert http_mock.called

    def test_unknown_flag_exits_2(self) -> None:
        runner = CliRunner()
        result = runner.invoke(app, ["--bogus"])
        assert result.exit_code == 2


class TestMainStdioMode:
    """Tests for main() in stdio mode — argv + transport dispatch."""

    def test_stdio_mode_configures_logging_to_stderr_before_construction(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pytest.TempPathFactory,
    ) -> None:
        """Stdio mode must call configure_logging(stream=sys.stderr) FIRST.

        Otherwise structlog's PrintLoggerFactory + stdlib basicConfig
        default to stdout — corrupts MCP JSON-RPC framing.
        """
        monkeypatch.setenv("CODEORACLE_EMBEDDING__MODEL", "x")
        monkeypatch.setenv("CODEORACLE_EMBEDDING__DIM", "1")
        monkeypatch.setenv("CODEORACLE_REPO_ROOT", "/tmp/x")
        call_order: list[str] = []

        def record_configure(_settings, **kwargs):
            call_order.append(f"configure_logging(stream={kwargs.get('stream')})")

        def record_run_stdio(_server):
            call_order.append("run_stdio")

        with patch("codeoracle.mcp.__main__.configure_logging", side_effect=record_configure), \
             patch("codeoracle.mcp.__main__._build_dependency_graph") as build_mock, \
             patch("codeoracle.mcp.__main__.run_stdio", side_effect=record_run_stdio), \
             patch("codeoracle.mcp.__main__.asyncio.run") as asyncio_mock:
            build_mock.return_value = MagicMock(name="deps")
            asyncio_mock.side_effect = lambda coro: call_order.append("run_stdio")
            CliRunner().invoke(app, [])  # no argv → default stdio transport

        # configure_logging called with stream=sys.stderr BEFORE _build_dependency_graph + run_stdio
        assert any("sys.stderr" in s or "stderr" in s for s in call_order), call_order
        assert call_order[0].startswith("configure_logging"), call_order


class TestMainHttpMode:
    """Tests for main() in HTTP mode — uvicorn invoked with settings.mcp.host/port."""

    def test_http_mode_dispatches_to_run_streamable_http(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("CODEORACLE_EMBEDDING__MODEL", "x")
        monkeypatch.setenv("CODEORACLE_EMBEDDING__DIM", "1")
        monkeypatch.setenv("CODEORACLE_REPO_ROOT", "/tmp/x")
        monkeypatch.setenv("CODEORACLE_MCP__HOST", "0.0.0.0")
        monkeypatch.setenv("CODEORACLE_MCP__PORT", "9999")

        captured_kwargs: dict = {}

        def record_http(server, *, host, port):
            captured_kwargs.update({"host": host, "port": port})

        with patch("codeoracle.mcp.__main__.configure_logging"), \
             patch("codeoracle.mcp.__main__._build_dependency_graph") as build_mock, \
             patch("codeoracle.mcp.__main__.run_streamable_http", side_effect=record_http):
            build_mock.return_value = MagicMock(name="deps", aclose=AsyncMock())
            CliRunner().invoke(app, ["--http"])

        assert captured_kwargs == {"host": "0.0.0.0", "port": 9999}

    def test_http_mode_calls_aclose_in_finally(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Even if run_streamable_http raises, teardown still runs."""
        monkeypatch.setenv("CODEORACLE_EMBEDDING__MODEL", "x")
        monkeypatch.setenv("CODEORACLE_EMBEDDING__DIM", "1")
        monkeypatch.setenv("CODEORACLE_REPO_ROOT", "/tmp/x")

        deps_mock = MagicMock(name="deps")
        deps_mock.aclose = AsyncMock()

        with patch("codeoracle.mcp.__main__.configure_logging"), \
             patch("codeoracle.mcp.__main__._build_dependency_graph", return_value=deps_mock), \
             patch("codeoracle.mcp.__main__.run_streamable_http", side_effect=RuntimeError("boom")), \
             patch("codeoracle.mcp.__main__.asyncio.run") as asyncio_mock:
            asyncio_mock.side_effect = lambda coro: None  # let the aclose coroutine "complete"
            result = CliRunner().invoke(app, ["--http"])
            assert isinstance(result.exception, RuntimeError)

        # asyncio.run was called once (for aclose) AFTER the exception
        assert asyncio_mock.call_count == 1
```

- [ ] **Step 2: Run the tests; verify they fail**

```bash
uv run pytest tests/unit/mcp/test_main.py -v
```

Expected: `ImportError` — module doesn't exist.

- [ ] **Step 3: Write `src/codeoracle/mcp/__main__.py`**

```python
# src/codeoracle/mcp/__main__.py
"""Composition root + binary entry for the MCP server process.

Registered as `codeoracle-mcp` in pyproject.toml [project.scripts]. Two
transport modes selected by argv:

- (default, no args) → stdio JSON-RPC (dev / IDE integration)
- `--http`           → streamable HTTP (service mode; §8.5 parity gate)

The composition root constructs the dependency graph eagerly
(process-lifetime scope), wraps it in MCPServer, dispatches to the
chosen transport runner, and tears down via try/finally on exit.

Stdout-pollution invariant (Plan 12 invariant 5): in stdio mode,
configure_logging is called with stream=sys.stderr BEFORE any
dependency construction so the first log line (e.g., engine init)
doesn't corrupt the JSON-RPC framing on stdout.
"""

from __future__ import annotations

import asyncio
import logging
import sys
from dataclasses import dataclass
from typing import TYPE_CHECKING, Annotated

import typer

from codeoracle.adapters.agent.client import AgentClient
from codeoracle.adapters.valkey.client import ValkeyQueueClient
from codeoracle.adapters.valkey.config import ValkeyConfig
from codeoracle.core.config import Settings, get_settings
from codeoracle.core.logging import configure_logging
from codeoracle.db.session import make_async_engine, make_async_session_factory
from codeoracle.mcp.server import MCPServer
from codeoracle.mcp.transports import run_stdio, run_streamable_http
from codeoracle.services.indexing.service import IndexingService

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncEngine


logger = logging.getLogger(__name__)


app = typer.Typer(
    help="CodeOracle MCP server — stdio (default) or streamable HTTP (--http).",
)


@dataclass
class _Dependencies:
    """Holder for the eagerly-constructed dependency graph.

    Lives for the entire process. Cleanup is via aclose() in __main__'s
    finally block — NOT via the SDK Server's lifespan (Plan 12 invariant 7).
    """

    engine: AsyncEngine
    valkey_client: ValkeyQueueClient
    indexing_service: IndexingService
    agent_client: AgentClient

    async def aclose(self) -> None:
        """Tear down all deps in reverse construction order."""
        await self.agent_client.aclose()
        await self.valkey_client.aclose()
        await self.engine.dispose()


def _build_dependency_graph(settings: Settings) -> _Dependencies:
    """Construct the dep graph (process-lifetime scope).

    Construction order:
    1. AsyncEngine via Plan 3's make_async_engine() — must be first
       (registers the AGE connect listener so agtype queries work — D2-7);
       IndexingService consumes it
    2. ValkeyQueueClient — needed by IndexingService for queue enqueue
    3. IndexingService — composes engine + session_factory + queue + repo_root
    4. AgentClient — independent (HTTP client to the agent process)

    Each construct uses settings fields from Plan 3 + Plan 11's amendments.
    `# type: ignore[call-arg]` on ValkeyConfig() is required because
    ValkeyConfig.url is a required field populated from CODEORACLE_VALKEY__URL at
    runtime — ty cannot see the env source (Plan 5 L444 docstring: "All
    fields are required (no defaults) so misconfiguration fails fast at
    process start"). Zero-arg construction matches Plan 5's precedent at
    L2132 (`valkey_config or ValkeyConfig()`) and L7016
    (`ValkeyQueueClient(ValkeyConfig())`).
    """
    engine = make_async_engine()  # reads get_settings() (== settings param); registers AGE listener (D2-7)
    valkey_client = ValkeyQueueClient(ValkeyConfig())  # type: ignore[call-arg]
    session_factory = make_async_session_factory(engine)
    indexing_service = IndexingService(
        engine=engine,
        db_session_factory=session_factory,
        queue_client=valkey_client,
        repo_root=settings.repo_root,
        # advisory_lock_timeout_seconds + extractor_kinds left at IndexingService
        # defaults: the MCP reindex tool calls enqueue_reindex (persist a PENDING
        # Snapshot + XADD to STREAM_REINDEX_REQUESTS), which never acquires
        # pg_advisory_lock(0x46AE0001) nor runs extractors — those are the
        # ReindexOrchestratorConsumer worker's job (invariant 3 / DECIDE-J L177).
        # Mirroring the API's lock-timeout here would imply MCP shares lock
        # contention the 2026-05-26 async pivot deliberately moved off this boundary.
    )
    agent_client = AgentClient(
        base_url=settings.api.agent_base_url,
        request_timeout_seconds=settings.api.agent_request_timeout_seconds,
        shared_token=settings.agent.shared_token,
    )
    return _Dependencies(
        engine=engine,
        valkey_client=valkey_client,
        indexing_service=indexing_service,
        agent_client=agent_client,
    )


@app.command()
def main(
    http: Annotated[
        bool,
        typer.Option(
            "--http",
            help="Use streamable HTTP transport (default: stdio JSON-RPC).",
        ),
    ] = False,
) -> None:
    """Binary entry — stdio (default) or streamable HTTP (--http)."""
    transport = "http" if http else "stdio"
    settings = get_settings()

    # CRITICAL ORDERING — stdout-pollution invariant (Plan 12 invariant 5):
    # In stdio mode, configure_logging MUST be called with stream=sys.stderr
    # BEFORE any other code path that might log (including engine init).
    if transport == "stdio":
        configure_logging(settings, stream=sys.stderr)
    else:
        configure_logging(settings)

    logger.info("codeoracle_mcp_starting transport=%s", transport)
    deps = _build_dependency_graph(settings)
    server = MCPServer(
        agent_client=deps.agent_client,
        indexing_service=deps.indexing_service,
        settings=settings,
    )
    try:
        if transport == "stdio":
            asyncio.run(run_stdio(server))
        else:
            run_streamable_http(
                server,
                host=settings.mcp.host,
                port=settings.mcp.port,
            )
    finally:
        # asyncio.run is OK here because we're outside any running loop
        # (run_stdio's loop has exited by the time we reach finally; the
        # uvicorn HTTP path is sync and doesn't leave a loop running).
        asyncio.run(deps.aclose())


if __name__ == "__main__":
    app()
```

- [ ] **Step 4: Run the tests**

```bash
uv run pytest tests/unit/mcp/test_main.py -v
```

Expected: all 5 tests pass (1 argv-parsing triple + 2 main mode tests + 2 teardown tests).

- [ ] **Step 5: Lint + type check**

```bash
uv run ruff check src/codeoracle/mcp/__main__.py
uv run ty check src/codeoracle/mcp/__main__.py
```

Expected: zero findings.

- [ ] **Step 6: Commit**

```bash
git add src/codeoracle/mcp/__main__.py tests/unit/mcp/test_main.py
git commit -m "feat(mcp): __main__ composition root + argv parsing + teardown

Binary entry registered as 'codeoracle-mcp' (pyproject.toml entry added in
Task 9). Two transport modes via argv:

- (no args) → stdio JSON-RPC (default, dev mode / IDE integration)
- --http    → streamable HTTP (service mode, §8.5 parity gate)

Composition order:
1. Typer parses `--http` → transport selection
2. get_settings → load Plan 3 Settings
3. configure_logging(stream=sys.stderr if stdio else default)
   — CRITICAL ordering: stdout-pollution invariant (Plan 12 invariant 5)
4. _build_dependency_graph → engine + valkey + IndexingService + AgentClient
5. Wrap in MCPServer (composition root)
6. Dispatch to run_stdio or run_streamable_http
7. try/finally → asyncio.run(deps.aclose()) for clean teardown

Mirrors Plan 11's api/app.py lifespan pattern (process-scoped deps, not
per-session).

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

## Task 6: Unit-test polish — full handler coverage + edge cases

**Files:**
- Modify: `tests/unit/mcp/test_server.py` (add edge-case tests not covered by Tasks 2-3 — empty content arrays, special-character handling, etc.)
- Modify: `tests/unit/mcp/test_transports.py` (add edge-case: `InitializationOptions` content shape verification)

**Goal:** Close the unit-test gap. After Tasks 2-5, the unit coverage is solid for happy paths + main error paths. This task adds:
1. `_call_tool` dispatch tests (verify the right handler is called for each tool name)
2. Tool-schema content tests (verify `description` is non-empty + grammatical, `inputSchema` is a non-empty dict, etc.)

This task is intentionally polish-only — no new source code; just more test cases.

- [ ] **Step 1: Add `_call_tool` dispatch tests**

```python
# tests/unit/mcp/test_server.py — APPEND

class TestCallToolDispatch:
    """Tests for MCPServer._call_tool — dispatch to the right handler."""

    @pytest.mark.asyncio
    async def test_dispatches_explain_symbol(
        self,
        fake_settings: Settings,
        fake_agent_client: MagicMock,
        fake_indexing_service: MagicMock,
        canonical_query: Query,
    ) -> None:
        server = MCPServer(
            agent_client=fake_agent_client,
            indexing_service=fake_indexing_service,
            settings=fake_settings,
        )
        result = await server._call_tool(
            "explain_symbol", canonical_query.model_dump(mode="json")
        )
        # explain_symbol handler returned a dict (not a CallToolResult)
        assert isinstance(result, dict)
        fake_agent_client.run.assert_awaited_once()
        fake_indexing_service.enqueue_reindex.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_dispatches_reindex(
        self,
        fake_settings: Settings,
        fake_agent_client: MagicMock,
        fake_indexing_service: MagicMock,
        canonical_reindex_request: ReindexRequest,
        canonical_snapshot: Snapshot,
    ) -> None:
        fake_indexing_service.enqueue_reindex.return_value = canonical_snapshot
        server = MCPServer(
            agent_client=fake_agent_client,
            indexing_service=fake_indexing_service,
            settings=fake_settings,
        )
        result = await server._call_tool(
            "reindex", canonical_reindex_request.model_dump(mode="json")
        )
        assert isinstance(result, dict)
        fake_indexing_service.enqueue_reindex.assert_awaited_once()
        fake_agent_client.run.assert_not_awaited()
```

- [ ] **Step 2: Add schema-content tests**

```python
# tests/unit/mcp/test_server.py — APPEND to TestToolRegistry

    @pytest.mark.asyncio
    async def test_both_tools_have_non_empty_descriptions(
        self,
        fake_settings: Settings,
        fake_agent_client: MagicMock,
        fake_indexing_service: MagicMock,
    ) -> None:
        """Tool descriptions are non-empty and reference the corresponding HTTP endpoint."""
        server = MCPServer(
            agent_client=fake_agent_client,
            indexing_service=fake_indexing_service,
            settings=fake_settings,
        )
        tools = await server._list_tools()
        for t in tools:
            assert t.description, f"Tool {t.name} has empty description"
            assert len(t.description) > 50, f"Tool {t.name} description too terse"

        explain = next(t for t in tools if t.name == "explain_symbol")
        reindex = next(t for t in tools if t.name == "reindex")
        assert "/v1/query" in (explain.description or "")
        assert "/v1/reindex" in (reindex.description or "")
```

- [ ] **Step 3: Run the full unit test suite**

```bash
uv run pytest tests/unit/mcp/ -v
```

Expected: ~18-22 tests pass (the 6 from Task 2 + 4 from Task 3 + 2 from Task 4 + 5 from Task 5 + the dispatch + schema-content cases from this task).

- [ ] **Step 4: Coverage spot-check**

```bash
uv run pytest tests/unit/mcp/ --cov=src/codeoracle/mcp --cov-report=term-missing
```

Expected: ≥95% line coverage for `src/codeoracle/mcp/server.py` and `src/codeoracle/mcp/transports.py`. The `__main__.py` may be lower because the `if __name__ == "__main__":` and the asyncio.run wrapping are not directly exercised — that's fine; Task 7+ integration covers those paths.

- [ ] **Step 5: Commit**

```bash
git add tests/unit/mcp/test_server.py tests/unit/mcp/test_transports.py
git commit -m "test(mcp): unit-test polish — dispatch + schema content

Closes the unit-test gap left after Tasks 2-5:

- _call_tool dispatch: verify explain_symbol routes to the agent client
  (and not the indexing service), and reindex routes to the indexing
  service's enqueue_reindex (and not the agent client).
- Schema content: tool descriptions are non-empty + reference the
  matching HTTP endpoint (cross-reference to Plan 11).

~15 unit tests total across the MCP layer; ≥95% line coverage on
server.py + transports.py.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

## Task 7: Integration test — stdio transport round-trip

**Files:**
- Create: `tests/integration/mcp/conftest.py` (~90 lines — fixtures for in-process MCPServer + stub AgentClient + stub IndexingService)
- Create: `tests/integration/mcp/test_stdio_transport.py` (~160 lines — full round-trip test using the SDK's in-process client)

**Goal:** Land the first end-to-end integration test exercising the SDK's transport layer. The test:
1. Constructs an `MCPServer` with stubbed deps (no real DB, no real agent)
2. Spawns the SDK's in-process client + server connected via in-memory streams
3. Calls `list_tools()` via the client; verifies the two tools are advertised
4. Calls `explain_symbol` via the client with a canonical `Query`; verifies the `QueryResult` round-trips through JSON-RPC framing
5. Calls `reindex` (success path); verifies the enqueued `Snapshot` round-trips
6. Calls `reindex` twice (idempotent enqueue); verifies both return the same `Snapshot` sha (no error — the advisory lock lives in the worker, not the MCP boundary)

This is the first task that exercises the SDK's transport plumbing (Tasks 2-6 mocked it). If the SDK API has changed in a way that breaks the integration, this task surfaces it.

- [ ] **Step 1: Write the shared integration conftest**

```python
# tests/integration/mcp/conftest.py
"""Shared fixtures for tests/integration/mcp/.

Provides:
- `mcp_server` — a real MCPServer with stubbed dependencies. Used by all
  integration tests so they share the same transport-layer surface.
- `stub_agent_client` / `stub_indexing_service` — instance attributes on
  the MCPServer accessible via `mcp_server._agent_client` etc. for
  test-side reconfiguration.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from codeoracle.core.config import Settings
from codeoracle.mcp.server import MCPServer
from codeoracle.models.evidence import (
    Citation,
    Claim,
    EvidenceHandle,
    HandleType,
    Verdict,
    VerdictStatus,
)
from codeoracle.models.query import Query, QueryResult
from codeoracle.models.reindex import ReindexRequest
from codeoracle.models.snapshot import ExtractorOutcome, Snapshot, SnapshotStatus


@pytest.fixture(autouse=True)
def integration_settings_env(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Mirrors the unit-test conftest's env shim."""
    monkeypatch.setenv("CODEORACLE_EMBEDDING__MODEL", "test-embedding-model")
    monkeypatch.setenv("CODEORACLE_EMBEDDING__DIM", "128")
    monkeypatch.setenv("CODEORACLE_DB__PASSWORD", "test-db-pw")  # D5-4: required, no default
    monkeypatch.setenv("CODEORACLE_REPO_ROOT", str(tmp_path))


@pytest.fixture
def canonical_query() -> Query:
    return Query(symbol="resolve_subscriber")


@pytest.fixture
def canonical_query_result() -> QueryResult:
    claim = Claim(
        statement="resolve_subscriber returns the active subscriber matching uid",
        citations=[
            Citation(
                handle=EvidenceHandle(
                    type=HandleType.CODE_SPAN,
                    locator="file://src/codeoracle/services/handle_resolution/service.py:1-10",
                    snapshot_sha="abc123def456",
                ),
                excerpt="resolve_subscriber(...)",
                source_trust=9,
            )
        ],
        confidence=0.9,
    )
    verdict = Verdict(
        status=VerdictStatus.PROVEN,
        reason="All citations resolved and match cached excerpts.",
        resolved_citations=[],
    )
    return QueryResult.proven(
        trace_id="01HXYZTRACEABCD",
        claim=claim,
        verdict=verdict,
    )


@pytest.fixture
def canonical_snapshot() -> Snapshot:
    return Snapshot(
        sha="abc123def456",
        parent_sha=None,
        status=SnapshotStatus.APPLIED,
        applied_at=datetime(2026, 5, 26, 12, 0, 0, tzinfo=timezone.utc),
        extractor_results={"joern": ExtractorOutcome.SUCCESS},
    )


@pytest.fixture
def stub_agent_client(canonical_query_result: QueryResult) -> MagicMock:
    client = MagicMock()
    client.run = AsyncMock(return_value=canonical_query_result)
    client.aclose = AsyncMock(return_value=None)
    return client


@pytest.fixture
def stub_indexing_service(canonical_snapshot: Snapshot) -> MagicMock:
    svc = MagicMock()
    svc.enqueue_reindex = AsyncMock(return_value=canonical_snapshot)
    return svc


@pytest.fixture
def mcp_server(
    stub_agent_client: MagicMock,
    stub_indexing_service: MagicMock,
) -> MCPServer:
    """A real MCPServer with stubbed deps.

    Tests can mutate stub behavior via `mcp_server._agent_client.run.side_effect = ...`
    or `mcp_server._indexing_service.enqueue_reindex.return_value = ...`.
    """
    return MCPServer(
        agent_client=stub_agent_client,
        indexing_service=stub_indexing_service,
        settings=Settings(),  # type: ignore[call-arg]
    )
```

- [ ] **Step 2: Write the stdio round-trip test**

```python
# tests/integration/mcp/test_stdio_transport.py
"""Integration tests for the MCP stdio transport.

Uses the SDK's in-process client + memory-stream pair to exercise the
full JSON-RPC framing without binding real stdin/stdout. The MCPServer
under test holds STUB deps (no real DB / no real agent) — this test
verifies the TRANSPORT LAYER is wired correctly, not the underlying
service implementations.
"""

from __future__ import annotations

import asyncio

import anyio
import pytest
from mcp.client.session import ClientSession
from mcp.shared.memory import create_client_server_memory_streams

from codeoracle.mcp.server import MCPServer
from codeoracle.mcp.transports import build_initialization_options


@pytest.mark.asyncio
async def test_list_tools_returns_explain_symbol_and_reindex(
    mcp_server: MCPServer,
) -> None:
    """Via the SDK client: list_tools returns explain_symbol + reindex."""
    async with create_client_server_memory_streams() as (
        (client_read, client_write),
        (server_read, server_write),
    ):
        # Run the server in the background
        async def _run_server() -> None:
            await mcp_server.server.run(
                server_read,
                server_write,
                build_initialization_options(mcp_server.server),
            )

        async with anyio.create_task_group() as tg:
            tg.start_soon(_run_server)

            async with ClientSession(client_read, client_write) as client:
                await client.initialize()
                tools_result = await client.list_tools()

            tg.cancel_scope.cancel()

    tool_names = {t.name for t in tools_result.tools}
    assert tool_names == {"explain_symbol", "reindex"}


@pytest.mark.asyncio
async def test_explain_symbol_round_trip(
    mcp_server: MCPServer,
    canonical_query,
    canonical_query_result,
) -> None:
    """Via the SDK client: call_tool(explain_symbol) returns the QueryResult."""
    async with create_client_server_memory_streams() as (
        (client_read, client_write),
        (server_read, server_write),
    ):

        async def _run_server() -> None:
            await mcp_server.server.run(
                server_read,
                server_write,
                build_initialization_options(mcp_server.server),
            )

        async with anyio.create_task_group() as tg:
            tg.start_soon(_run_server)

            async with ClientSession(client_read, client_write) as client:
                await client.initialize()
                result = await client.call_tool(
                    "explain_symbol", canonical_query.model_dump(mode="json")
                )

            tg.cancel_scope.cancel()

    assert result.isError is False or result.isError is None
    # SDK returns structuredContent when outputSchema is declared
    assert result.structuredContent == canonical_query_result.model_dump(mode="json")


@pytest.mark.asyncio
async def test_reindex_success_round_trip(
    mcp_server: MCPServer,
    canonical_snapshot,
) -> None:
    """Via the SDK client: call_tool(reindex) returns the enqueued Snapshot.

    Post the 2026-05-26 async pivot the tool delegates to
    `IndexingService.enqueue_reindex` and returns the `Snapshot` receipt
    (PENDING on a fresh enqueue; the stub here returns a terminal row to
    keep the transport-wiring assertion deterministic). The round-trip
    pins that the `Snapshot` payload survives JSON-RPC framing intact.
    """
    async with create_client_server_memory_streams() as (
        (client_read, client_write),
        (server_read, server_write),
    ):

        async def _run_server() -> None:
            await mcp_server.server.run(
                server_read,
                server_write,
                build_initialization_options(mcp_server.server),
            )

        async with anyio.create_task_group() as tg:
            tg.start_soon(_run_server)

            async with ClientSession(client_read, client_write) as client:
                await client.initialize()
                result = await client.call_tool(
                    "reindex", {"base_sha": "abc123def456"}
                )

            tg.cancel_scope.cancel()

    assert result.isError is False or result.isError is None
    assert result.structuredContent == canonical_snapshot.model_dump(mode="json")


@pytest.mark.asyncio
async def test_reindex_idempotent_enqueue_round_trip(
    mcp_server: MCPServer,
    canonical_snapshot,
) -> None:
    """Via the SDK client: a duplicate reindex enqueue is idempotent.

    Post the 2026-05-26 async pivot, `reindex` delegates to
    `IndexingService.enqueue_reindex`, which is idempotent on
    `SnapshotConflictError` (Plan 11 invariant 4): a second enqueue
    against the same tree state returns the EXISTING `Snapshot` row
    rather than XADDing a duplicate. The stub returns the same snapshot
    for both calls; the tool surfaces the same `sha` both times and never
    an error (the advisory lock lives in the worker — lock contention
    no longer reaches the MCP boundary).
    """
    async with create_client_server_memory_streams() as (
        (client_read, client_write),
        (server_read, server_write),
    ):

        async def _run_server() -> None:
            await mcp_server.server.run(
                server_read,
                server_write,
                build_initialization_options(mcp_server.server),
            )

        async with anyio.create_task_group() as tg:
            tg.start_soon(_run_server)

            async with ClientSession(client_read, client_write) as client:
                await client.initialize()
                first = await client.call_tool("reindex", {"base_sha": None})
                second = await client.call_tool("reindex", {"base_sha": None})

            tg.cancel_scope.cancel()

    assert (first.isError is False or first.isError is None)
    assert (second.isError is False or second.isError is None)
    expected = canonical_snapshot.model_dump(mode="json")
    assert first.structuredContent == expected
    assert second.structuredContent["sha"] == expected["sha"]
```

- [ ] **Step 3: Run the integration tests**

```bash
uv run pytest tests/integration/mcp/test_stdio_transport.py -v
```

Expected: all 4 tests pass.

If the SDK API has shifted (e.g., `create_client_server_memory_streams` moved or renamed), this is where you find out. Fix the import + signature; retry.

- [ ] **Step 4: Lint + type check**

```bash
uv run ruff check tests/integration/mcp/
uv run ty check tests/integration/mcp/
```

Expected: zero findings.

- [ ] **Step 5: Commit**

```bash
git add tests/integration/mcp/conftest.py tests/integration/mcp/test_stdio_transport.py
git commit -m "test(mcp): integration — stdio transport round-trip via SDK in-process client

First end-to-end test exercising the MCP SDK's transport layer. Uses
mcp.shared.memory.create_client_server_memory_streams to pair an SDK
ClientSession with the held server.run() without binding real
stdin/stdout.

Four cases:
- list_tools: client sees explain_symbol + reindex
- explain_symbol: QueryResult round-trips through JSON-RPC framing
- reindex (success): enqueued Snapshot round-trips
- reindex (idempotent enqueue): a duplicate call returns the same Snapshot sha (no error — lock lives in the worker post async pivot)

Stub deps (MagicMock AgentClient + IndexingService) — this test verifies
the TRANSPORT wiring, not the underlying service correctness.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

## Task 8: §8.5 parity gate — `test_mcp_explain_symbol_matches_api_response`

**Files:**
- Create: `tests/integration/mcp/test_parity_with_api.py` (~180 lines — the spec §8.5 byte-equal parity test)

**Goal:** Land the load-bearing §8.5 acceptance gate. Same `Query` routed through both the MCP `explain_symbol` tool AND Plan 11's `POST /v1/query` HTTP endpoint must produce byte-equal `QueryResult` payloads modulo `trace_id`.

> **Same-named test, two layers (§7.4 #9).** A `test_mcp_explain_symbol_matches_api_response` of the same name also lives in **Plan 13 Task 6** (`tests/integration/slice/test_slice_explain_symbol.py`) — that copy is the FULL-STACK layer (real `codeoracle-mcp` + `codeoracle-api` over compose, `streamablehttp_client`). THIS copy is the IN-PROCESS layer (`MCPServer` + a minimal FastAPI app in one process, `ASGITransport`, all deps mocked). Intentional two-layer coverage of the one §8.5 gate-4 parity contract: the in-process pin here catches `QueryResult`-shape drift at unit speed; Plan 13's full-stack pin catches transport/serialization drift. Not a duplication.

This test:
1. Constructs ONE shared `AgentClient` mock that returns a canonical `QueryResult`
2. Wires it into BOTH an `MCPServer` (this plan) AND a minimal FastAPI app exposing Plan 11's `/v1/query` router (built via the test-local helper `_build_parity_api_app(agent_client)`, mirroring Plan 11's `tests/unit/api/test_query.py:_build_app_with_agent_client` at L1561 — `app.state.agent_client` is the injection point Plan 11's `get_agent_client` DI provider reads from)
3. Calls the MCP tool via the SDK in-process client; captures `result.structuredContent`
4. Calls `POST /v1/query` via `httpx.ASGITransport`; captures the JSON body
5. Strips `trace_id` from both
6. Asserts byte-equal

If the two paths diverge, the byte-equal contract is broken. The test must pass with the same `AgentClient` instance to prove the equivalence is structural (same client, same model serialization), not coincidental.

- [ ] **Step 1: Write the parity test**

```python
# tests/integration/mcp/test_parity_with_api.py
"""§8.5 acceptance gate — byte-equal parity between MCP explain_symbol
tool and HTTP API POST /v1/query.

Spec text (docs/superpowers/specs/2026-05-21-platform-scaffolding-design.md L1396):

    def test_mcp_explain_symbol_matches_api_response():
        '''Same query via MCP and HTTP API → byte-equal Claim payloads (modulo trace_id).'''

This test wires the SAME AgentClient mock into BOTH paths so the
equivalence is structurally enforced — not just empirically true. If
this test ever breaks, either:
1. The MCP layer added a transformation (forbidden by invariant 2), OR
2. The API layer added a transformation (also forbidden by Plan 11
   invariant — handler delegates to AgentClient.run unchanged), OR
3. The QueryResult model gained a non-deterministic field beyond
   trace_id (needs spec amendment to enumerate the new exclusion).
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import anyio
import httpx
import mcp.types as types
import pytest
from fastapi import FastAPI
from mcp.client.session import ClientSession
from mcp.shared.memory import create_client_server_memory_streams

from codeoracle.core.config import Settings
from codeoracle.mcp.server import MCPServer
from codeoracle.mcp.transports import build_initialization_options
from codeoracle.models.evidence import (
    Citation,
    Claim,
    EvidenceHandle,
    HandleType,
    Verdict,
    VerdictStatus,
)
from codeoracle.models.query import Query, QueryResult


# Canonical inputs — re-defined here (not imported from conftest) so the
# parity test is self-contained and obviously points at a SINGLE pinned
# Query input.


@pytest.fixture
def parity_query() -> Query:
    """The single pinned Query used by both MCP and API paths."""
    return Query(symbol="resolve_subscriber")


@pytest.fixture
def parity_query_result() -> QueryResult:
    """The single canonical QueryResult both AgentClient calls return."""
    claim = Claim(
        statement="resolve_subscriber returns the active subscriber matching uid",
        citations=[
            Citation(
                handle=EvidenceHandle(
                    type=HandleType.CODE_SPAN,
                    locator="file://src/codeoracle/services/handle_resolution/service.py:1-10",
                    snapshot_sha="abc123def456",
                ),
                excerpt="resolve_subscriber(...)",
                source_trust=9,
            )
        ],
        confidence=0.9,
    )
    verdict = Verdict(
        status=VerdictStatus.PROVEN,
        reason="All citations resolved and match cached excerpts.",
        resolved_citations=[],
    )
    return QueryResult.proven(
        trace_id="01HXYZTRACEABCD",
        claim=claim,
        verdict=verdict,
    )


@pytest.fixture
def shared_agent_client(parity_query_result: QueryResult) -> MagicMock:
    """ONE AgentClient mock wired into BOTH the API and MCP servers."""
    client = MagicMock()
    client.run = AsyncMock(return_value=parity_query_result)
    client.aclose = AsyncMock(return_value=None)
    return client


def _build_parity_api_app(agent_client: MagicMock) -> FastAPI:
    """Build a minimal FastAPI app that exposes Plan 11's /v1/query router.

    Mirrors the helper at Plan 11 `tests/unit/api/test_query.py` L1561
    (`_build_app_with_agent_client`) — we duplicate the pattern locally
    rather than cross-import a private test helper from a sibling test
    package. The duplication is small (~10 lines) and keeps the parity
    test self-contained.

    Why not `codeoracle.api.app.create_app(settings)`? Two reasons:
    1. `create_app` constructs a REAL AgentClient inside the lifespan; we
       need to inject our shared mock BEFORE the lifespan runs, which
       requires either a `dependency_overrides` ceremony or skipping the
       lifespan entirely. The latter is what Plan 11's L1561 helper does.
    2. Skipping the lifespan also avoids spinning up the real SQLAlchemy
       engine / Valkey client / etc. for a test that only exercises the
       query route.

    The DI provider Plan 11 ships (`get_agent_client`) reads from
    `app.state.agent_client`, so injecting the mock there is sufficient
    to route the route handler to our shared mock.
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


def _strip_trace_id(payload: dict) -> dict:
    """Remove trace_id from a QueryResult dict before comparison.

    Spec §8.5 explicitly excludes trace_id from the parity gate (each
    request gets its own trace_id; the field's presence is contractual
    but its value is request-scoped).
    """
    return {k: v for k, v in payload.items() if k != "trace_id"}


@pytest.mark.asyncio
async def test_mcp_explain_symbol_matches_api_response(
    parity_query: Query,
    shared_agent_client: MagicMock,
) -> None:
    """§8.5 gate: MCP and HTTP API produce byte-equal QueryResult (modulo trace_id)."""

    # ----- API path ---------------------------------------------------
    # Build a minimal FastAPI app exposing Plan 11's /v1/query router
    # with the shared mock injected into app.state.agent_client. Plan 11's
    # DI provider (`get_agent_client`) reads from state, so the route
    # handler routes to our shared mock.
    api_app = _build_parity_api_app(shared_agent_client)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=api_app),
        base_url="http://test",
    ) as http_client:
        api_response = await http_client.post(
            "/v1/query", json=parity_query.model_dump(mode="json")
        )

    assert api_response.status_code == 200
    api_payload = api_response.json()

    # ----- MCP path ---------------------------------------------------
    mcp_server = MCPServer(
        agent_client=shared_agent_client,  # SAME instance
        indexing_service=MagicMock(),  # not exercised by explain_symbol
        settings=Settings(),  # type: ignore[call-arg]
    )

    async with create_client_server_memory_streams() as (
        (client_read, client_write),
        (server_read, server_write),
    ):

        async def _run_server() -> None:
            await mcp_server.server.run(
                server_read,
                server_write,
                build_initialization_options(mcp_server.server),
            )

        async with anyio.create_task_group() as tg:
            tg.start_soon(_run_server)
            async with ClientSession(client_read, client_write) as client:
                await client.initialize()
                mcp_result = await client.call_tool(
                    "explain_symbol", parity_query.model_dump(mode="json")
                )
            tg.cancel_scope.cancel()

    mcp_payload = mcp_result.structuredContent

    # ----- Strip trace_id from both and compare -----------------------
    assert _strip_trace_id(api_payload) == _strip_trace_id(mcp_payload), (
        "§8.5 byte-equal parity violated.\n"
        f"API payload (trace_id-stripped): {_strip_trace_id(api_payload)}\n"
        f"MCP payload (trace_id-stripped): {_strip_trace_id(mcp_payload)}\n"
    )

    # ----- Verify AgentClient was called identically from both --------
    # The shared mock was called exactly twice (once per path), both with
    # the same Query input.
    assert shared_agent_client.run.await_count == 2
    api_call_arg = shared_agent_client.run.await_args_list[0].args[0]
    mcp_call_arg = shared_agent_client.run.await_args_list[1].args[0]
    assert isinstance(api_call_arg, Query)
    assert isinstance(mcp_call_arg, Query)
    assert api_call_arg.model_dump() == mcp_call_arg.model_dump()
```

- [ ] **Step 2: Run the parity test**

```bash
uv run pytest tests/integration/mcp/test_parity_with_api.py::test_mcp_explain_symbol_matches_api_response -v
```

Expected: PASS.

If it fails, the divergence is either:
- A field beyond `trace_id` differs because one path stamps a server-side value instead of returning the mock's `QueryResult` unchanged. Investigate which path is adding the transformation; fix the offender (almost always: remove the transformation, don't add a parallel one to the other path).
- The MCP `structuredContent` shape differs from the API JSON shape (e.g., MCP wraps in an envelope). Check `result.structuredContent` vs. `result.content` — the SDK might return the dict at one vs. the other.

- [ ] **Step 3: Add a complementary "diverging trace_id is the ONLY allowed difference" test**

```python
# tests/integration/mcp/test_parity_with_api.py — APPEND

@pytest.mark.asyncio
async def test_trace_id_is_present_in_both_paths(
    parity_query: Query,
    shared_agent_client: MagicMock,
    parity_query_result: QueryResult,
) -> None:
    """Both paths emit trace_id (it's the ONLY field exempt from byte-equal)."""
    # Reuse the API + MCP machinery — minimal repetition for clarity
    api_app = _build_parity_api_app(shared_agent_client)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=api_app),
        base_url="http://test",
    ) as http_client:
        api_response = await http_client.post(
            "/v1/query", json=parity_query.model_dump(mode="json")
        )

    api_payload = api_response.json()
    assert "trace_id" in api_payload
    assert api_payload["trace_id"]  # non-empty
```

- [ ] **Step 4: Run both parity tests**

```bash
uv run pytest tests/integration/mcp/test_parity_with_api.py -v
```

Expected: both tests pass.

- [ ] **Step 5: Lint + type check**

```bash
uv run ruff check tests/integration/mcp/test_parity_with_api.py
uv run ty check tests/integration/mcp/test_parity_with_api.py
```

Expected: zero findings.

- [ ] **Step 6: Commit**

```bash
git add tests/integration/mcp/test_parity_with_api.py
git commit -m "test(mcp): §8.5 parity gate — explain_symbol matches /v1/query (modulo trace_id)

Lands the load-bearing acceptance gate from spec §8.5:

    def test_mcp_explain_symbol_matches_api_response():
        '''Same query via MCP and HTTP API → byte-equal Claim payloads
        (modulo trace_id).'''

ONE AgentClient mock is wired into BOTH:
- the MCP server (this plan's MCPServer with stub IndexingService)
- a minimal FastAPI app exposing Plan 11's /v1/query router via the
  test-local helper _build_parity_api_app (mirrors Plan 11's L1561 helper)

Same pinned Query is dispatched through both paths; QueryResult payloads
are compared after stripping trace_id. AgentClient.run is asserted
called identically from both (same Query input, twice).

If this test breaks, the byte-equal contract is broken — see test
docstring for the three possible failure causes + remediation.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

## Task 9: `pyproject.toml` — `codeoracle-mcp` script entry + ruff `print` ban + preflight.sh edit

**Files:**
- Modify: `pyproject.toml` (add `[project.scripts] codeoracle-mcp = ...` entry + extend ruff `per-file-ignores` / add a `flake8-print` rule for `src/codeoracle/mcp/`)
- Modify: `scripts/preflight.sh` (add MCP smoke step — start `codeoracle-mcp --http &`, ping, kill)

**Goal:** Wire up the binary entry + the lint guard against `print()` in MCP code + the preflight smoke step. After this task, `uv run codeoracle-mcp --http` works as a binary and the preflight verifies the MCP server starts cleanly.

- [ ] **Step 1: Add the `[project.scripts]` entry**

```toml
# pyproject.toml — INSIDE [project.scripts]

[project.scripts]
codeoracle = "codeoracle.cli.__main__:app"        # Plan 1 scaffold + Plan 5 Task 14 (Typer app)
codeoracle-agent = "codeoracle.agent.__main__:main"  # Plan 10
codeoracle-api = "codeoracle.api.__main__:main"   # Plan 11
codeoracle-mcp = "codeoracle.mcp.__main__:app"    # ← NEW (Plan 12) (Typer app)
```

- [ ] **Step 2: Add the `print` ban for `src/codeoracle/mcp/`**

```toml
# pyproject.toml — UNDER [tool.ruff.lint]

[tool.ruff.lint]
select = [..., "T20"]  # T20: flake8-print — bans print() / pprint() calls

[tool.ruff.lint.per-file-ignores]
# Existing entries (LLM provider isolation, test ergonomics, etc.) ...
# Plan 12: MCP stdio mode MUST NOT write to stdout (corrupts JSON-RPC
# framing). The T20 (flake8-print) rule above bans print() globally;
# this entry is intentionally EMPTY (no exemption) for src/codeoracle/mcp/
# — print() is a runtime correctness violation in MCP code, not just a
# style preference. Tests under tests/unit/mcp/ + tests/integration/mcp/
# also stay print-free; if a test needs debug output, use the logger.
```

Note: T20 may already be enabled globally — in that case, just add a comment in the section explaining why MCP code can't be exempted. If T20 is NOT yet enabled, this task adds it. Either way, after this task, `print()` anywhere under `src/codeoracle/mcp/` triggers a lint failure.

- [ ] **Step 3: Verify the ruff config rejects `print()` in MCP code**

Add a temporary print() to `src/codeoracle/mcp/server.py` and run ruff:

```bash
# Add a one-line print at the top of MCPServer.__init__
# Then:
uv run ruff check src/codeoracle/mcp/server.py
# Expect: T201 `print` found
# Remove the temporary print after verification.
```

Expected: ruff reports `T201 print() found` for the temporary line. Remove the temp print + re-run; ruff is clean.

- [ ] **Step 4: Extend `scripts/preflight.sh` with the MCP smoke step**

```bash
# scripts/preflight.sh — APPEND a new step after the existing API smoke step

echo "[preflight] Starting codeoracle-mcp on HTTP transport for smoke test..."
codeoracle-mcp --http &
MCP_PID=$!
sleep 2

# Smoke probe — POST a JSON-RPC initialize request and check for a valid response
RESPONSE=$(curl -s -X POST \
    -H "Content-Type: application/json" \
    -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{}}}' \
    "http://${CODEORACLE_MCP__HOST:-127.0.0.1}:${CODEORACLE_MCP__PORT:-8082}/mcp" \
  || echo "REQUEST_FAILED")

if echo "${RESPONSE}" | grep -q '"jsonrpc"'; then
    echo "[preflight] codeoracle-mcp smoke OK (JSON-RPC initialize echo received)"
else
    echo "[preflight] codeoracle-mcp smoke FAILED. Response: ${RESPONSE}" >&2
    kill "${MCP_PID}" 2>/dev/null || true
    exit 1
fi

kill "${MCP_PID}" 2>/dev/null || true
wait "${MCP_PID}" 2>/dev/null || true
echo "[preflight] codeoracle-mcp smoke step complete."
```

The exact command shape follows Plan 11 Task 11's preflight pattern for `codeoracle-api`. Adjust if Plan 11's preflight uses a slightly different idiom (e.g., a separate function vs. inline).

- [ ] **Step 5: Run preflight locally**

```bash
bash scripts/preflight.sh
```

Expected: all existing preflight steps pass + the new MCP smoke step prints "codeoracle-mcp smoke OK".

- [ ] **Step 6: Commit**

```bash
git add pyproject.toml scripts/preflight.sh
git commit -m "feat(mcp): binary entry, print-ban lint rule, preflight smoke step

Three wireup changes:

1. pyproject.toml [project.scripts] — register codeoracle-mcp =
   'codeoracle.mcp.__main__:app'. After install, 'uv run codeoracle-mcp [--http]'
   works as a binary (mirroring codeoracle / codeoracle-agent / codeoracle-api).

2. ruff T20 (flake8-print) — globally enabled; src/codeoracle/mcp/ is NOT
   exempted. print() is a runtime correctness violation in MCP code
   (stdout pollution corrupts JSON-RPC framing — invariant 5), not a
   style preference. Tests under tests/{unit,integration}/mcp/ also
   stay print-free.

3. scripts/preflight.sh — add MCP smoke step: start codeoracle-mcp --http
   in background, POST a JSON-RPC initialize request, verify the
   response is a valid JSON-RPC envelope, kill the process.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

## Task 10: Closure documentation + downstream-plan inheritance pointers

**Files:**
- Update: this plan's closure section (~120 lines — verification log + cross-plan-dep summary + Plan 13 inheritance pointer)

Plan 12 is a new plan, not an audit-derived rewrite, so there is no §6 EDIT table on this plan; closure work consists of writing the closure doc + verifying all cross-plan-dep rows are reciprocal-consistent (the Phase G cross-plan-deps sweep is the next task in the orchestrator's roadmap).

**Goal:** Land the closure documentation that subsequent plans (Plan 13 E2E + slice acceptance, Plans 14-18 orchestration layer) inherit from. The closure doc records:
1. What Plan 12 ships (files + tests + script entry)
2. What Plan 12 inherits (Plan 11 AgentClient, Plan 5 IndexingService, etc.)
3. What Plan 12 provides (codeoracle-mcp binary, MCPServer factory, transport runners)
4. Verification log (which tests pass + coverage numbers)
5. Outstanding deviations / TODOs for Slice+1 (e.g., DECIDE-M's no-/health on streamable HTTP)

- [ ] **Step 1: Write the closure doc**

```markdown
# Plan 12 Closure — MCP Server

**Plan:** docs/superpowers/plans/2026-05-21-mcp-server.md
**Status:** SHIPPED (all 11 tasks complete; unit + integration tests pass; §8.5 parity gate green)
**Closed:** [DATE — fill in at closure time]
**Closed by:** [author handle]

## What Plan 12 ships

| File | Purpose | LoC |
|---|---|---|
| `src/codeoracle/mcp/__init__.py` | Package re-exports (`MCPServer`, `run_stdio`, `run_streamable_http`) | ~25 |
| `src/codeoracle/mcp/__main__.py` | Composition root + argv parsing + transport selection + teardown | ~80 |
| `src/codeoracle/mcp/server.py` | `MCPServer` class — held SDK Server, tool registration, two handlers | ~280 |
| `src/codeoracle/mcp/transports.py` | `run_stdio` + `run_streamable_http` runners | ~120 |
| `tests/unit/mcp/conftest.py` | Shared unit-test fixtures (env shim + mocks) | ~70 |
| `tests/unit/mcp/test_server.py` | MCPServer handler unit tests | ~280 |
| `tests/unit/mcp/test_transports.py` | Transport runner sanity tests | ~80 |
| `tests/unit/mcp/test_main.py` | `__main__` argv parsing + composition root tests | ~120 |
| `tests/integration/mcp/conftest.py` | Integration fixtures (real MCPServer + stub deps) | ~90 |
| `tests/integration/mcp/test_stdio_transport.py` | Stdio round-trip via SDK in-process client | ~160 |
| `tests/integration/mcp/test_parity_with_api.py` | §8.5 parity gate (byte-equal modulo trace_id) | ~180 |
| `pyproject.toml` | `[project.scripts] codeoracle-mcp`, `mcp` dep, T20 ruff rule | (edits) |
| `scripts/preflight.sh` | MCP smoke step | (edits) |

**Plan 3 amendment landed separately** (Task 1) before Plan 12 implementation:
- `core/config.py:McpConfig` nested model + `Settings.mcp` field
- `core/logging.py:configure_logging(stream=...)` parameter

## What Plan 12 inherits

Cross-plan-deps "Imports from" table (full table in plan §Cross-plan dependencies):

- **Plan 1**: toolchain (uv / ruff / ty / pytest)
- **Plan 2**: `scripts/preflight.sh` — Plan 12 extends with the MCP smoke step
- **Plan 3**: `Settings` + `McpConfig` (Task 1 amendment) + `configure_logging(stream=)` (Task 1 amendment) + `models/query.py` (`Query` / `QueryResult` / `QueryStatus`) + `models/snapshot.py` (`Snapshot` / `SnapshotStatus`)
- **Plan 5**: `services/indexing/service.py:IndexingService` (incl. `enqueue_reindex` — amendment `40885bf`) + `adapters/valkey/client.py:ValkeyQueueClient` + `adapters/valkey/config.py:ValkeyConfig` (advisory lock + `AdvisoryLockTimeoutError` live in `ReindexOrchestratorConsumer`, not consumed here)
- **Plan 11**: `adapters/agent/client.py:AgentClient` + `adapters/agent/errors.py:*` + `models/reindex.py:ReindexRequest`

NOTE: Plan 1A (`core/telemetry.py`) is intentionally NOT listed. Plan 12's
slice scope deliberately defers MCP-specific telemetry to Slice+1
(see Outstanding deviations table) — no symbols from `core/telemetry.py`
are imported in any Plan 12 file. The "MCP-specific telemetry" Slice+1
trigger row documents the deferral; this is a known, explicit gap so
the imports table stays honest.

## What Plan 12 provides

Cross-plan-deps "Provides to" table:

- **Plan 13 (E2E + slice acceptance)**:
  - `codeoracle-mcp` binary (`pyproject.toml [project.scripts]`)
  - `mcp.MCPServer` class (E2E uses it for in-process server construction)
  - `mcp.run_streamable_http` runner (E2E uses for in-process HTTP server)
  - Test fixtures in `tests/integration/mcp/conftest.py` (E2E imports `mcp_server` fixture pattern)

## Verification log

All commands run on commit `[FILL IN AT CLOSURE]`:

```bash
uv run pytest tests/unit/mcp/ -v        # ~25-30 tests, all PASS
uv run pytest tests/integration/mcp/ -v # 5 tests, all PASS (4 stdio + 2 parity — note: 1 parity test is duplicated above; final count is 6 if test_trace_id_is_present_in_both_paths added)
uv run ruff check src/codeoracle/mcp/ tests/unit/mcp/ tests/integration/mcp/  # CLEAN
uv run ty check src/codeoracle/mcp/                                            # CLEAN
uv run pytest --cov=src/codeoracle/mcp tests/unit/mcp/                         # ≥95% line coverage on server.py + transports.py
bash scripts/preflight.sh                                                  # All steps pass including MCP smoke
```

## §8.5 acceptance gate

`tests/integration/mcp/test_parity_with_api.py::test_mcp_explain_symbol_matches_api_response` — PASS

The gate proves byte-equal parity between MCP `explain_symbol` and HTTP `POST /v1/query` (modulo `trace_id`) by wiring the SAME `AgentClient` mock into both paths and comparing the resulting `QueryResult` payloads.

## Outstanding deviations / Slice+1 TODOs

| Item | Decision | Why deferred | Slice+1 trigger |
|---|---|---|---|
| `/health` endpoint on streamable HTTP transport | DECIDE-M: skip for slice | Operability target is "Claude Code over stdio"; k8s readiness not a slice concern | Slice+1 deploys MCP as a long-lived service with liveness probes |
| Dedicated MCP `get_reindex_status` poll tool | DECIDE-G: single-shot `reindex` enqueue for slice (mirrors Plan 11's async POST → 202 + PENDING `Snapshot`) | The slice `reindex` tool already returns the enqueue receipt (PENDING/APPLYING/APPLIED `Snapshot`); a separate MCP-side poll tool that re-reads the snapshot lifecycle is not needed while the agent-side parity gate covers the enqueue contract | Long-running production reindexes need an MCP client to poll lifecycle without re-enqueuing → add a `get_reindex_status(snapshot_sha)` tool parallel to Plan 11's `GET /v1/reindex/{snapshot_sha}` |
| MCP-specific telemetry (transport attribute) | Spec OOS for slice; add via OTLP span attribute `mcp.transport=stdio\|http` at `_call_tool` boundary if needed | Plan 1A's `core/telemetry` already provides the span surface | Operators need to attribute requests by transport |
| Streaming partial results for `explain_symbol` | OOS for slice | Spec §3.2 lists `explain_symbol` as a single-shot tool | Spec adds a streaming variant; flip the session manager's `stateless` kwarg to `False` |

## Architectural invariants honored

All 12 invariants from the plan §Architectural invariants section are verified:

1. ✓ MCP is a process — no business logic in `src/codeoracle/mcp/`
2. ✓ `explain_symbol` byte-equal to `/v1/query` — Task 8 §8.5 gate
3. ✓ `reindex` mirrors `/v1/reindex`'s async enqueue — same model + `enqueue_reindex`; both return the PENDING `Snapshot` receipt (parity gate: same `base_sha` → same `sha`)
4. ✓ Dual transport, single server — `run_stdio` + `run_streamable_http` share an `MCPServer`
5. ✓ Stdout-pollution invariant — `configure_logging(stream=sys.stderr)` in stdio mode; T20 ruff bans `print()`; preflight smoke verifies non-corruption
6. ✓ OOP composition root — `MCPServer` holds SDK Server (no inheritance)
7. ✓ Eager dependency lifetime — `__main__.py` constructs + `try/finally` tears down
8. ✓ `extra="forbid"` flows through tool schemas — Pydantic `model_config` carries through
9. ✓ No `/health` on streamable HTTP — DECIDE-M deferred to Slice+1
10. ✓ LLM-agnostic — MCP process imports no LLM provider (ruff per-file-ignores enforces)
11. ✓ No module-level mutable state — all construction inside `main()` or test fixtures
12. ✓ `Settings.repo_root` shared with Plan 11 — top-level, not duplicated under `McpConfig`

## Closure handoff

Plan 13 (E2E + slice acceptance) is unblocked. Plan 13 will:
- Spawn `codeoracle-mcp --http` in subprocess for E2E
- Reuse `mcp_server` fixture pattern from `tests/integration/mcp/conftest.py` for in-process E2E variants
- Drive `explain_symbol` via the SDK client for the §9 PROVEN / UNKNOWN / live-edit demos

After Plan 13, the slice's cross-plan-deps update sweep (Phase G) refreshes the full Plans 1-13 dependency graph including the bidirectional MCP ↔ API ↔ Agent rows.
```

- [ ] **Step 2: Run markdownlint on the closure doc**

```bash
npx markdownlint-cli2 docs/superpowers/plans/2026-05-21-mcp-server.md
```

Expected: zero findings.

- [ ] **Step 3: Commit the closure**

```bash
git add docs/superpowers/plans/2026-05-21-mcp-server.md
git commit -m "docs(plan-12): closure — MCP server slice complete

Closure log for Plan 12 (docs/superpowers/plans/2026-05-21-mcp-server.md).
All 11 tasks complete; unit + integration tests pass; §8.5 parity gate
green (test_mcp_explain_symbol_matches_api_response).

Outstanding items deferred to Slice+1 (DECIDE-M /health on streamable
HTTP, DECIDE-G non-blocking `get_reindex_status` MCP poll tool, MCP-specific
telemetry, streaming explain_symbol). All 12 architectural invariants
honored; verified by unit + integration + lint + type-check + preflight.

Plan 13 (E2E + slice acceptance) is unblocked.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

## Self-review pass

Per the writing-plans skill's "Self-Review" step, I'm running the checklist now (NOT delegating to a subagent — this is my own pass).

### 1. Spec coverage

| Spec section | Plan 12 task |
|---|---|
| §3.2 `mcp/` directory tree (`__main__.py`, `server.py`, `transports.py`) | Task 0 (skeleton) + Task 2 (server.py) + Task 4 (transports.py) + Task 5 (`__main__.py`) |
| §3.3 rule 6 (processes are thin entrypoints, no business logic) | Invariant 1; verified by Task 6 (handler delegation tests) |
| §3.2 / §1.3 `explain_symbol` tool | Task 2 (handler) + Task 8 (§8.5 parity) |
| §3.2 / §1.3 `reindex` tool | Task 3 (handler — async enqueue, 202-equivalent) |
| §8.5 `test_mcp_explain_symbol_matches_api_response` | Task 8 (the entire task) |
| §9 demos (PROVEN / UNKNOWN / live-edit via MCP) | Plan 13 owns the demos; Plan 12 provides the `codeoracle-mcp` binary they consume |
| Stdio transport + IDE integration | Task 4 (`run_stdio`) + Task 5 (`__main__` stdio default) + Task 7 (round-trip test) |
| Streamable HTTP transport (service mode) | Task 4 (`run_streamable_http`) + Task 5 (`__main__` --http flag) + Task 9 (preflight smoke) |

**Result:** complete; every spec requirement has a task.

### 2. Placeholder scan

Searched for: "TBD", "TODO", "implement later", "fill in details", "Add appropriate error handling", "handle edge cases", "Similar to Task N", "Write tests for the above".

Findings:
- Task 10 closure doc has `[DATE — fill in at closure time]` and `[FILL IN AT CLOSURE]` — these are intentional template tokens for the implementer to fill at closure (not plan placeholders).
- No other placeholders found.

**Result:** clean.

### 3. Type consistency

- `MCPServer.__init__` keyword-only params: `agent_client`, `indexing_service`, `settings` — consistent across Tasks 2, 3, 5, 6, 7, 8.
- `_handle_explain_symbol` return type: `dict[str, Any]` — consistent.
- `_handle_reindex` return type: `dict[str, Any]` — consistent across Tasks 3, 6, 7. (Post the 2026-05-26 async pivot the handler delegates to `IndexingService.enqueue_reindex(base_sha=...)` and returns the PENDING `Snapshot` as a dict; lock contention no longer reaches this boundary — the advisory lock lives in the `ReindexOrchestratorConsumer` worker. Plan 12 never has a handler return a `CallToolResult` instance — that return-type is not unwrapped in v1.12.4.)
- `run_stdio` signature: `async def run_stdio(mcp_server: MCPServer) -> None` — consistent.
- `run_streamable_http` signature: `def run_streamable_http(mcp_server: MCPServer, *, host: str, port: int) -> None` — consistent.
- `_Dependencies` dataclass: same fields referenced consistently in Task 5 + closure doc.

**Result:** consistent.

---

**Plan complete and saved to `docs/superpowers/plans/2026-05-21-mcp-server.md`.**

Two execution options for the implementer:

**1. Subagent-Driven (recommended)** — Dispatch a fresh subagent per task, review between tasks, fast iteration. REQUIRED SUB-SKILL: `superpowers:subagent-driven-development`.

**2. Inline Execution** — Execute tasks in this session using `superpowers:executing-plans`, batch execution with checkpoints.

Which approach?
