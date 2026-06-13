# Agent Orchestration (LangGraph) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the `codeoracle.agent` process — a LangGraph state machine that turns an `explain_symbol` `Query` into a verified `QueryResult` by orchestrating retrieval, LLM composition, trust scoring, and verification.

**Architecture:** A strictly sequential LangGraph `StateGraph` runs `intent → graph_query → embed_query → compose → score → verify`. The retrieval nodes (`graph_query`, `embed_query`) build every `EvidenceHandle` *and resolve it* through `HandleResolverService`, so each citation's excerpt is platform-owned and round-trips to PROVEN by construction — the compose node's LLM authors prose and *selects* evidence by index but never authors excerpt bytes. `embed_query` routes each `EmbeddingHit` to a citation kind by source-path extension via Plan 6's `is_doc_path()` predicate — doc files (`.md` / `.rst` / `.adoc` / `.markdown`) emit `HandleType.DOC_PARAGRAPH`, every other path emits `HandleType.CODE_SPAN` — so the routing reuses the same extension set `chunker_for` dispatched on at index time, no drift. Composition is per-query: `build_agent_graph(deps)` bakes the request's session-bound collaborators into the nodes via an explicit `async def _with_deps` wrapper in `graph.py` — *not* `functools.partial`, because an `async def` is unambiguously a coroutine function and LangGraph cannot mis-detect such a node as synchronous (see Task 9 for the full rationale); `run_agent_query(graph, query)` invokes the graph and assembles the terminal `QueryResult`.

**Process surface (Task 11):** the agent is launched as `codeoracle/agent/__main__.py` running `uvicorn` against a FastAPI app. The app exposes `POST /v1/query` (request body `Query`, response `QueryResult` — both Plan 3 models with `extra="forbid"` drift detection) and `GET /v1/health` (response `HealthResponse` — db-ping liveness). The agent process is its own composition root: a FastAPI `lifespan` async context manager builds the SQLAlchemy engine + session_factory + `LLMProvider` + `EmbeddingsProvider` + `SourcebotClient` (Plan 8 L787-788's aclose-on-stop contract) on the uvicorn event loop. Per request, the route handler opens a fresh `AsyncSession` from the factory and calls `build_agent_dependencies(session, sourcebot, llm, embeddings, settings)` to assemble `AgentDependencies` for `run_agent_query`. The FastAPI OTel server instrumentor extracts incoming W3C `traceparent` and sets it as the active span context; agent node spans inherit. Process shutdown drains `SourcebotClient.aclose()` then `LLMProvider.aclose()` then disposes the SQLAlchemy engine (per-resource try/except so partial-cleanup failures still progress).

**Tech Stack:** Python 3.14 · LangGraph 1.2 (`StateGraph`) · OpenTelemetry API (span instrumentation) · Pydantic v2 · pytest + pytest-asyncio (`asyncio_mode = "auto"`) · ruff + ty · uv · FastAPI 0.115 · uvicorn 0.32 · opentelemetry-instrumentation-fastapi · httpx (test only)

---

## Cross-plan dependencies

This plan is **Plan 10** of the platform build. It consumes the models, adapters, repositories, and services delivered by Plans 2–9 and provides the agent process that Plans 11 (API) and 12 (MCP) drive.

### Consumes

| Symbol | From | Kind | How Plan 10 uses it |
|---|---|---|---|
| `Query` | Plan 3 | Pydantic model | Input to `run_agent_query`; nodes read `query.symbol`, `query.snapshot_sha` |
| `QueryResult` | Plan 3 | Pydantic model | Output of `run_agent_query`; classmethods `.proven()`, `.failed()`, plus a direct constructor for the symbol-not-found case |
| `QueryStatus` | Plan 3 | enum | `QueryStatus.COMPLETE` on the inline symbol-not-found result |
| `DraftClaim` | Plan 3 | Pydantic model | Assembled by `compose_node` from platform-owned candidate citations |
| `Claim` | Plan 3 | Pydantic model | Produced by `score_node` via `TrustMatrixService` |
| `DraftCitation` | Plan 3 | Pydantic model | Built by retrieval nodes; carried in `graph_citations` / `embed_citations` |
| `Citation` | Plan 3 | Pydantic model | Lives inside a scored `Claim`; the agent never constructs it directly |
| `EvidenceHandle` | Plan 3 | Pydantic model (frozen) | Retrieval nodes construct `cypher://` and `file://` handles |
| `HandleType` | Plan 3 | enum | `CODE_SPAN`, `DOC_PARAGRAPH`, `GRAPH_QUERY` — the slice's emitted handle types |
| `Verdict` | Plan 3 | Pydantic model | `graph_query` builds the UNKNOWN verdict; `verify` returns `VerifierService`'s verdict verbatim |
| `VerdictStatus` | Plan 3 | enum | `VerdictStatus.UNKNOWN` for the symbol-not-found verdict |
| `Snapshot` | Plan 3 | Pydantic model | `intent` reads `.sha` off `SnapshotRepository.get_latest_applied()` / `.get_by_sha()`, and `.status` to gate a pinned SHA on APPLIED |
| `ChatMessage` | Plan 3 (+ Plan 4 `template_id`) | Pydantic model | `compose` builds the prompt; every message carries a `template_id` |
| `ChatResponse` | Plan 3 | Pydantic model | Return type of `LLMProvider.chat` |
| `ToolCall` | Plan 3 | Pydantic model | `compose` parses `response.tool_calls[0].arguments_json` |
| `ToolDefinition` | Plan 3 | Pydantic model | `SUBMIT_EXPLANATION_TOOL` |
| `TokenUsage` | Plan 3 | Pydantic model | Field of `ChatResponse` (used only by test fakes) |
| `CodeOracleError` | Plan 3 | exception base | `AgentError` extends it |
| `LLMProvider` | Plan 4 | ABC | `compose` calls `.chat(messages, tools=...)` |
| `EmbeddingsProvider` | Plan 6 | ABC | `embed_query` calls `.embed([symbol])` |
| `GraphRepository` | Plan 5 | repository | `graph_query` calls `.execute_cypher(stmt, params)` |
| `SnapshotRepository` | Plan 5 | repository | `intent` calls `.get_latest_applied()` (unpinned path) and `.get_by_sha()` (pinned path — then gates on `Snapshot.status is APPLIED`) |
| `EmbeddingsRepository` | Plan 6 | repository | `embed_query` calls `.knn(query_embedding=…, snapshot_sha=…, k=…)` (keyword-only per Plan 6 signature) |
| `EmbeddingHit` | Plan 6 | value type | `knn` results; supplies `source_path` (routed by `is_doc_path`) + line range for the `file://` locator |
| `is_doc_path` | Plan 6 (`codeoracle.services.embedding.chunker`) | predicate | `embed_query` routes each hit to `HandleType.DOC_PARAGRAPH` (doc extension) or `HandleType.CODE_SPAN` (everything else); reuses the same `_DOC_EXTENSIONS` set `chunker_for` dispatched on, so what got chunked as a doc cites as a doc |
| `HandleResolverService` | Plan 8 | service | `resolve_to_citation` calls `.resolve(handle)` |
| `VerifierService` | Plan 9 | service | `verify_node` calls `.verify(claim)` |
| `TrustMatrixService` | Plan 9 | service | `score_node` calls `.score_each(draft)` (synchronous) |
| all `models/` types | Plan 3 | Pydantic model bundle | Wholesale bundle reciprocal of Plan 3's wholesale `provides.45` umbrella; per-type rows above (`Query`, `QueryResult`, `Claim`, `Citation`, `DraftCitation`, `EvidenceHandle`, `HandleType`, `Verdict`, `VerdictStatus`, `Snapshot`, `ChatMessage`, `ChatResponse`, `ToolCall`, `ToolDefinition`, `TokenUsage`, `DraftClaim`, `QueryStatus`) are the fine-grained references |
| `Settings` (`core/config.py`) | Plan 3 | Pydantic settings model | Task 11's `__main__.py` constructs `Settings()` once at process start and reads `settings.agent.host` / `settings.agent.port` for uvicorn binding (per Plan 3 amendment `AgentConfig` addition). The FastAPI lifespan reads `settings.db.*` to construct the SQLAlchemy engine and `settings.embedding.*` to construct `EmbeddingsProvider` via `EmbeddingsProviderFactory.build(settings)`. `settings.agent.shared_token` (Plan 3 amendment, `str \| None` default `None`) is provisioned for Slice+1 auth reactivation (new Deviation 9). Agent NODE functions still never read `Settings` directly — DI keeps them pure; the agent PROCESS (Task 11) is the composition root that resolves `Settings` into concrete dependencies. |
| `configure_logging` / `get_logger` (`core/logging.py`) | Plan 3 | bootstrap function + factory | The composition root calls `configure_logging` once at process startup; each agent node acquires its logger via `get_logger(__name__)` — Plan 3's structured-logging contract carries through `@agent_node` span context |
| `Citation` (agent as `score_node` producer) | Plan 3 | Pydantic model — producer-role attribution | Reciprocal of Plan 3's `provides.48` per-edge attribution; the agent's `score_node` is the *producer* site that constructs scored `Citation` objects via `TrustMatrixService.score_each(draft)` — orthogonal to the type-import row above which covers the symbol itself |
| Graph FQN scheme (dotted Python form from `(file_path, name)`) | Plan 5 | naming convention | `is_safe_symbol_fqn(symbol) -> bool` validates `Query.symbol` against the SAFE_FQN_PATTERN derived from Plan 5's scheme before any Cypher interpolation (invariant 3) |
| `Snapshot.extractor_results` (diagnostic record — NOT a gate) | Plan 5 | Pydantic field — per-extractor outcome record | The agent does **not** read `extractor_results`. Queryability is enforced one layer down by the `graph_query` existence check: Plan 5's barrier marks a partial-failure reindex APPLIED (the failed extractor's coverage simply absent from an otherwise-populated graph), and `graph_query` then returns a terminal UNKNOWN for any symbol missing from that partial graph — never a false answer; an all-extractors-failed barrier resolves to FAILED (D-GATE Option B), so a contentless snapshot is never served APPLIED. `extractor_results` is Plan 5's CLI-surfaced diagnostic of how each extractor fared. The real intent↔snapshot dependency (`.sha` + `.status` APPLIED gate) is the `Snapshot` / `SnapshotRepository` rows above. |
| `(:TraceSpan {trace_id, span_id, name})` AGE projection | Plan 7 | DEFERRED contract — Slice+1 | DEFERRED to Slice+1 per Plan 10 Deviation 1 (`traces_query` node dropped — TRACE_SPAN is an explicit Slice+1 non-goal); reciprocal of Plan 7's `provides.3` DEFERRED-prefix forward-ref |
| Loki logs correlation (`trace.get_logs_by_trace`) | Plan 7 | DEFERRED agent-tool stub — Slice+1 | DEFERRED — Plan 10 does NOT ship a `get_logs_by_trace` tool in slice (six retrieval/composition nodes only); reciprocal of Plan 7's `provides.5` DEFERRED-prefix forward-ref (Plan 7's "Future plan — possibly Plan 10's slice" note remains non-committal) |
| Scalar-per-row Cypher result contract (single column named `result`) | Plan 8 | hard constraint on emitted Cypher | `graph_query` node MUST emit Cypher whose `RETURN` projects a single column named `result` with one scalar per row, so `stringify_cypher_rows` produces deterministic excerpts (reciprocal of Plan 8's `provides.7`) |
| `stringify_cypher_rows` excerpt-format contract | Plan 8 | hard constraint on agent-prompt design | The agent-prompt design inherits Plan 8's excerpt-format contract — the compose node's prompt references excerpts whose formatting comes from `stringify_cypher_rows`; reciprocal of Plan 8's `provides.8` |
| `HandleResolverService` stateless / no-caching invariant | Plan 8 | architectural invariant (drift visibility) | Plan 10 inherits the stateless / no-caching constraint per Plan 8's lock — the agent must NOT introduce multi-repo or per-snapshot caching at the resolver layer (drift-visibility guarantee preserved); reciprocal of Plan 8's `provides.14` |

### Provides

| Symbol | Kind | For whom |
|---|---|---|
| `run_agent_query(graph, query) -> QueryResult` | async function | Plan 13 slice acceptance harness (in-process fallback path); Task 11's FastAPI route handler (HTTP wrapper) — the direct Python-level entry point. Plans 11/12 consume the agent over HTTP via `POST /v1/query`; they do NOT import `run_agent_query`. |
| `build_agent_graph(deps) -> CompiledStateGraph` | factory function | Agent process's per-request route handler (Task 11) — called once per query inside the FastAPI request handler. Plan 13 slice harness may also call it directly in the in-process testing path. |
| `AgentDependencies` | frozen dataclass | Constructed inside the agent process by Task 11's route handler per request via `build_agent_dependencies(session, sourcebot, llm, embeddings, settings)` composer in `codeoracle/agent/dependencies.py`. The composer builds the 5-strategy `HandleResolverService` (Plan 8 L80-84 registry) + `VerifierService(resolver=resolver)` (Plan 9 L1411) + stateless `TrustMatrixService()` (Plan 9 L526). Per-request data (`query`) stays in the route handler and is passed to `run_agent_query(graph, query)` directly. Plan 11 never imports `AgentDependencies`; it owns the HTTP boundary only. |
| `POST /v1/query` (HTTP surface) | FastAPI route — request `Query` (Plan 3, raw `QueryResult` response — no envelope) | Plan 11 (api gateway's `httpx`-based `AgentClient`), Plan 12 (MCP server's `explain_symbol` tool — DECIDE-per-plan), Plan 13 (slice acceptance — HTTP path). |
| `GET /v1/health` (HTTP surface) | FastAPI route — response `HealthResponse` Pydantic model (new, in `codeoracle.models.health`) | Plan 1A (Docker `HEALTHCHECK` directive), Plan 11 (upstream health-aggregation when written), slice startup (readiness gate). |
| `FastAPI(lifespan=...)` (process-shutdown drain) | async context manager — startup builds process-scoped `SourcebotClient` + `LLMProvider` + `EmbeddingsProvider` + engine/session_factory; shutdown calls `await sourcebot.aclose()` then `await llm.aclose()` then `await engine.dispose()`, each in its own try/except so partial-cleanup failures still progress | Plan 11 (api process inherits the pattern verbatim when written), Plan 12 (MCP process inherits when written). The `LLMProvider.aclose()` ABC contract itself ships in Plan 4 amendment Commit 2; `SourcebotClient.aclose()` ships in Plan 8 L883 with the composition-root-registers-aclose contract recorded at Plan 8 L787-788; this row records Plan 10 introduces the shutdown-orchestration invariant by satisfying both contracts in one lifespan. |
| `AgentState` | `TypedDict` | The agent's nodes; Plan 13 may type-check isolated node tests |
| `AgentError` | exception (`CodeOracleError` subclass) | Raised inside nodes; trapped by `@agent_node`; Plan 11 need not catch it |
| `is_safe_symbol_fqn(symbol) -> bool` | predicate | The injection-safe-FQN guard; reusable by future intent types |
| six node functions (`intent_node`, …, `verify_node`) | async functions | Internal to the graph; individually unit-testable |
| `codeoracle.agent` (variant module) | Python package — agent process module | Plan 1A — the agent variant the operational Dockerfile matrix builds (`agent → Plan 10` per Plan 1A's variant table). Plan 1A's Dockerfile entrypoint shim invokes `python -m codeoracle.agent` for the agent variant. `__main__.py` calls `configure_logging(settings)` + `init_telemetry()` (zero-arg, reads `CODEORACLE_OTEL_*` env independently per Plan 1A L485-495) then `uvicorn.run(app, host=settings.agent.host, port=settings.agent.port)` blocking. Plan 1A's `defaults.env` (Plan 1A Task 3) gains `CODEORACLE_AGENT__HOST=127.0.0.1` and `CODEORACLE_AGENT__PORT=8081` via Task 11 (subset-check defaults.env test stays green). |
| LangGraph-agent reindex-tool integration into `services/indexing/orchestrator.py` | DEFERRED contract | Plan 5 — DEFERRED to Slice+1; the reindex agent tool is out of slice (Plan 10 ships six retrieval/composition nodes only, no operator tooling) — reciprocal of Plan 5's `imports.38` DEFERRED-prefix forward-ref |
| `@agent_node` exception-to-terminal-error policy | architectural invariant (Plan 10 invariants 6 + 10) | Plan 9 — Plan 9's Invariant 12 reasoning relies on this policy: node exceptions are trapped, converted to a terminal `error`, and surfaced via `QueryResult.failed`; the agent never propagates an exception |
| `resolve_to_citation(handle, resolver) -> DraftCitation` | async helper function (`agent/runtime.py`) | Plan 9 — the citation builder Plan 9 reasons about for Invariant 12; resolves a handle and pairs it with the resolver's verbatim output as the excerpt (invariant 1 made concrete) |
| Invariant 1 (LLM-cannot-author-`DraftCitation`) doctrine | architectural invariant (Plan 10 Invariant 1) | Plan 9 — Plan 9's Invariant 12 reasoning explicitly relies on this doctrine: the LLM never authors excerpt bytes; every `DraftCitation.excerpt` equals `HandleResolverService.resolve(handle)` verbatim, so re-resolution at verification yields byte-identical excerpts (no spurious REFUTED) |
| `codeoracle-agent` process binary + `agent/__main__.py` (amendment #45 — uvicorn entrypoint) + the LangGraph 6-node graph | process (PID 1 in compose `agent:` service) | Plan 13 (E2E) — `docker-compose.e2e.yml` invokes `python -m codeoracle.agent`; dispatch via Plan 1A entrypoint.sh + `environment.CODEORACLE_VARIANT: agent` per Plan 13 DECIDE-W; intent-classifier handle-type filter verified by Task 9. NOT consumed: Plan 10's private `_FakeLLM` (Plan 13 uses real `MockLLMProvider` per D-10.U3) |

---

## Plan-level invariants

These hold across every task. A change that violates one is a bug, not a refactor.

1. **Excerpts are platform-owned (resolve-at-retrieval).** Every `DraftCitation` the agent emits has `excerpt == HandleResolverService.resolve(handle)`. The LLM never authors excerpt bytes — only the `statement`, the `confidence`, and the *selection* of evidence by index. This is what makes the PROVEN path structural: re-resolving the same handle during verification yields a byte-identical excerpt, so a citation cannot be REFUTED for an LLM paraphrase.

2. **Deterministic Cypher ordering.** Every `cypher://` locator the agent constructs includes an explicit `ORDER BY` over deterministic columns. AGE / openCypher give *no* row-order guarantee without `ORDER BY`; without this an honest multi-row result stringifies differently at retrieval vs. verification → spurious REFUTED. The callers locator is additionally bounded by a `LIMIT` (NFR D3-3) so a high-fan-in symbol cannot produce an unbounded excerpt; that bound is re-run-safe precisely because the order is deterministic.

3. **FQN injection safety.** `Query.symbol` is validated against `SAFE_FQN_PATTERN` *before* any string-interpolation into a Cypher query. The `cypher://` locator carries *literal* Cypher (Plan 8's `GraphQueryResolver` re-runs it verbatim), so an unsanitised symbol is a Cypher-injection surface. An injection-unsafe symbol terminates the query in the `intent` node, before retrieval.

4. **Sequential execution.** Nodes run one at a time; there is no parallel fan-out. The per-query `AsyncSession` behind the repositories is *not* concurrency-safe — two retrieval nodes touching it concurrently would corrupt the session. (Retrieval latency is also immaterial against the LLM-dominated budget; the slice query budget is median <8 s, and the LLM call dominates.)

5. **`template_id` on every LLM message.** Every `ChatMessage` the agent constructs sets a non-`None` `template_id` (`COMPOSE_SYSTEM_TEMPLATE_ID` / `COMPOSE_USER_TEMPLATE_ID`). Plan 4's request-fingerprint hashes template ids, not raw bytes — a missing id silently degrades fingerprint stability.

6. **The agent never raises.** `run_agent_query` always returns a `QueryResult` — never propagates an exception. Node failures are trapped by `@agent_node` and converted to a terminal `error`; `run_agent_query` maps `error` to `QueryResult.failed` and wraps `graph.ainvoke` in a defence-in-depth `try`/`except`.

7. **Verdict is returned verbatim.** The agent never overrides, softens, or re-interprets the `Verdict` that `VerifierService.verify` produces. A REFUTED verdict reaches the caller as REFUTED.

8. **Handle-type closure.** Every citation in a composed `DraftClaim` has `handle.type ∈ state["plan_handle_types"]`. The `explain_symbol` plan is `[HandleType.CODE_SPAN, HandleType.DOC_PARAGRAPH, HandleType.GRAPH_QUERY]`; `compose_node` asserts the closure and `TRACE_SPAN` / `DB_RECORD` are structurally impossible to emit.

9. **Per-query composition.** `build_agent_graph` is called once per query. `AgentDependencies` carries that request's session-bound collaborators; the compiled graph belongs to exactly one query. (Compilation is graph-validation only — microseconds — so compile-per-query costs nothing against an 8 s query.)

10. **One short-circuit mechanism.** Control flow is a plain linear chain of `add_edge` calls. The *only* short-circuit mechanism is the `@agent_node` decorator: if a terminal `error` or `verdict` is already set, the decorated node returns `{}` without opening a span. No conditional edges, no in-node guard clauses — the decorator centralises it.

11. **HTTP contract immutability.** `Query` and `QueryResult` (Plan 3) are the agent's wire contract; both carry `model_config = ConfigDict(extra="forbid")` after Plan 3 amendment. Field additions are non-breaking only if optional with safe defaults. Field removals, type narrowings, or required-field additions are breaking changes that block Plan 11 deployment until its `AgentClient` is updated. Enforced by Task 11's integration test `test_query_rejects_extra_fields` (asserts 422 on unknown wire fields).

12. **Traceparent propagation.** Every incoming HTTP request's `traceparent` (or a generated root if absent) becomes the active OTel context for the entire request lifecycle. All agent node spans (`intent.parse`, `graph.query`, `embed.query`, `llm.compose`, `verifier.verify`, `resolve.<handle_type>`) MUST inherit; no node opens an isolated root span. Enforced by Task 11's integration test `test_traceparent_propagates_to_node_spans` — installs an in-memory span exporter via an OTel-internals monkeypatch and asserts ≥3 spans all sharing the request's trace_id (server span + 2 child spans opened by the test's `stub_runner_with_node_spans`).

---

## Deviations from the spec §4 agent sketch

The spec (`docs/superpowers/specs/2026-05-21-platform-scaffolding-design.md`) §4 sketches the agent. Plan 10 deviates as follows; each deviation is deliberate and, where it affects a downstream plan, names the reconciliation.

1. **`traces_query` node dropped.** Spec §4.2 implies a node querying trace spans, and §8.5 observability gate 9 lists a `traces.query` child span. `TRACE_SPAN` is an explicit Slice+1 non-goal (§8.6) and `TracesRepository` exposes no symbol→span query. The slice agent has **six** nodes. **Plan 13 reconciliation (blocking slice acceptance):** Plan 13 MUST drop `traces.query` from its gate-9 span-set assertion before gate 9 can pass — or restore a `traces_query` node here. This is a load-bearing cross-plan demand, not advisory: an unreconciled gate-9 assertion is a latent slice-acceptance failure that Plan 13's authoring task must verify explicitly (or call back to amend Plan 10) before the slice ships. See Plan 10 close-out for the long-form statement.

2. **`intent` is deterministic — no LLM call.** Spec §4.2 implies intent classification by an LLM. `Query.type` is `Literal["explain_symbol"]`, so the intent is type-guaranteed at construction. The `intent` node validates the symbol (FQN safety) and resolves the snapshot — no LLM call. The §8.5 test `test_intent_classifier_never_produces_unsupported_handle_types` remains meaningful: it pins that the codified `plan_handle_types` never contains `TRACE_SPAN` / `DB_RECORD`.

3. **No `QueryPlannerService`.** Spec §3.2 lists `services/query_planner.py`. With a single intent type a planner service is premature abstraction; planning logic is the `intent` node plus tested helpers. **Reactivation trigger:** reintroduce `QueryPlannerService` when a second intent type is added.

4. **One LLM model.** Spec §4.2 implies small/large model tiers. Spec §5's config defines one model; the agent uses the single injected `LLMProvider` for `compose`.

5. **Compose selects, the platform composes.** The LLM authors the `statement`, the `confidence`, and selects supporting/contradicting evidence *by index* via the `submit_explanation` tool. It does **not** author the `DraftClaim` object or any excerpt — `compose_node` assembles the `DraftClaim` from platform-owned candidate citations (invariant 1). The tool's `parameters_schema` is a flat, hand-authored JSON-Schema (no nested `$defs`) so the contract stays legible and provider-portable.

6. **Symbol self-location via embed-knn.** The slice locates the symbol's own definition through embed-knn proximity to the FQN string, not a precise FQN→line-range graph query — the `:Definition` line-range schema is underspecified across Plans 5/6. Reliability depends on FQN density at the definition site. **Future work:** a precise SCIP `:Definition` query once that schema is pinned.

7. **OTel SDK install splits across Plan 1A + Task 11.** The agent's nodes (Tasks 1–10) depend only on `opentelemetry-api` and emit spans via `get_tracer` — no SDK. The SDK bootstrap module (`TracerProvider`, OTLP exporter — contents of `core/telemetry.py`) is owned by **Plan 1A (Operational Infrastructure)**. SDK installation at process startup is **Task 11's** responsibility: `agent/__main__.py` calls Plan 1A's `init_telemetry()` (zero-arg, self-constructs `TelemetryConfig()` from `CODEORACLE_OTEL_*` env per Plan 1A L485-495) to install `TracerProvider` + exporter once. Unit tests for Tasks 1–10 still see no-op spans (no SDK installed in test env, by design — zero-overhead in unit tests). Task 11's integration test `test_traceparent_propagates_to_node_spans` installs an in-memory span exporter via `monkeypatch` of OTel's tracer-provider sentinel to assert traceparent propagation through node spans. Plan 11 (API) and Plan 12 (MCP) inherit the same `init_telemetry()` startup pattern when written.

8. **Single-shot compose.** `compose_node` makes one LLM call. A malformed or absent tool call is not retried — it becomes `QueryResult.failed`. Retry/backoff is post-slice hardening (it needs a retry budget and changes determinism).

9. **No auth at api↔agent HTTP boundary in slice — localhost-trust deferral.** The agent process binds to `127.0.0.1:8081` by default (`AgentConfig` host/port; configurable via `CODEORACLE_AGENT__HOST` / `CODEORACLE_AGENT__PORT`). No bearer-token check on `POST /v1/query`. Risk: any process on the same host can call the agent's query endpoint. Slice deployment model is all-on-one-host native Python (Plan 1A's `infra/compose/docker-compose.yml` contains only observability + DB services, not application processes — verified during the amendment); the trust surface ≈ the host's UNIX security boundary. `settings.agent.shared_token` field (Plan 3 amendment, `SecretStr | None` default `None`) is provisioned so Slice+1 can land the `verify_agent_token` FastAPI dependency without a schema migration. **Reactivation trigger:** multi-host slice deployment OR public network exposure (api/agent on separate hosts behind a shared network).

10. **`embed_query` retrieves `KNN_K=4` nearest chunks — reconciled with spec §4.2 in #44.** `embed_query.py` sets `KNN_K = 4`; the value keeps the compose prompt inside the LLM context budget while still surfacing the symbol's own definition plus its immediate neighbours. Spec §4.2 (L469) *originally* sketched `db.EmbeddingsRepository.knn(query_vec, k=5)`; the spec was the stale party (sketch value vs the tuned slice value) and was amended in #44 to `knn(query_vec, k=4)`, so spec and slice now agree. **Reactivation note:** `KNN_K` stays independently tunable for the compose-prompt budget; if it changes, re-measure the compose prompt size and re-sync spec §4.2. `spec-reconcile: applied (#44)`.

11. **OOP convention — the six nodes and `resolve_to_citation` are functions, not classes (a deliberate, reasoned exception to the CLAUDE.md "OOP over functional" rule, recorded here because silence is not an option).** The six nodes (`intent`/`graph_query`/`embed_query`/`compose`/`score`/`verify`) are `async def` + `@agent_node`; `resolve_to_citation` is a module-level `async def`. CLAUDE.md's default would make each a constructor-injected class (they compose collaborators and do I/O, so they are not "pure transformations / one-line helpers"). The exception is taken on four grounds, not the "the framework needs functions" one (a `NodeBase` ABC with `async __call__` is equally callable, so that argument is rejected):
    - **CLAUDE.md reserves ABCs for *swappable layers* ("ABCs + concrete implementations for swappable layers — LLM provider, DB adapters"); a node is not a swap point.** Each node has exactly one implementation, is composed once per query into the compiled graph (invariant 9), holds no state across calls, and is never substituted — tests inject fake *collaborators* via `make_fake_deps`, never a fake node. The two benefits the OOP rule exists to capture — state shared across multiple methods, and alternate implementations behind an interface — are both absent. A `NodeBase` ABC + one class per node would host a single `_run` method whose only instance state is the injected `deps`: ceremony without encapsulation, the "class that should be a function" inversion.
    - **`@agent_node` *is* the node abstraction — functionally equivalent to, and lighter than, a `NodeBase.__call__` template method.** It centralizes the whole cross-cutting surface in one place: invariant 10's single short-circuit (skip when a terminal `error`/`verdict` is set, opening no span), the gate-9 span, and invariant 6's exception-to-terminal-`error` trap. An ABC would centralize the identical three concerns in `__call__` and delegate the body to `self._run`; the decorator does the same while keeping each body a plain, directly-callable coroutine (the Task-3 tests invoke a decorated node as `node(state, deps)` with no class to construct). Dependency injection still happens — `graph.py`'s `_with_deps` wrapper bakes the request-scoped `AgentDependencies` into each node per query — it is closure-injection, the correct shape for a unit instantiated once per graph compile.
    - **The collaborators the nodes orchestrate are rule-honoring classes.** `VerifierService`, `HandleResolverService`, `TrustMatrixService`, and the three repositories are all constructor-injected classes; the OOP rule is satisfied where state and swappability actually live. The nodes are thin orchestration glue over that compliant layer.
    - **`resolve_to_citation` is justified on its own terms (the node-idiom argument above does not reach it — it is not a node).** It takes its one collaborator (`HandleResolverService`) as an explicit per-call argument, opens a `resolve.<type>` span, calls `.resolve`, and returns `DraftCitation(handle, excerpt)` — invariant 1 made concrete. It stores nothing, so wrapping it in its own class would add a stateless single-method object around an already-injected service for zero encapsulation gain. Nor does it belong *on* `HandleResolverService`: `DraftCitation` ships from `codeoracle/models/evidence.py`, so a Plan 8 service could legally construct it (no layering bar) — but the resolver's charter is handle resolution, and folding the agent's draft-assembly step (invariant 1) plus its `resolve.<type>` span onto a general-purpose service would couple it to an agent-specific concern. A tested, module-level async helper at the agent-runtime layer — bridging a lower-layer service call to a `models/`-layer type — is the correct shape.

    **Reactivation trigger:** if a second implementation of any node is ever needed (e.g., an alternate `compose` strategy selected at graph-build time), promote *that* node to a `NodeBase` ABC + concrete classes then — the swap point would finally exist. Likewise, if `resolve_to_citation` grows state or a second collaborator it must store, make it a class.

---

## Task index

```
src/codeoracle/
  core/exceptions.py        (M) + AgentError
  models/health.py          (Task 11) HealthStatus
  agent/
    __init__.py             (Task 2 stub, Task 9 re-exports)
    __main__.py             (Task 11) uvicorn entrypoint — `python -m codeoracle.agent`
    app.py                  (Task 11) FastAPI app + lifespan + build_agent_dependencies composer
    server.py               (Task 11) POST /v1/query + GET /v1/health route handlers
    state.py                AgentState TypedDict
    fqn.py                  SAFE_FQN_PATTERN, is_safe_symbol_fqn
    runtime.py              span names, get_tracer, agent_node, resolve_to_citation
    dependencies.py         AgentDependencies frozen dataclass
    graph.py                build_agent_graph, run_agent_query
    nodes/
      __init__.py
      intent.py             intent_node
      graph_query.py        graph_query_node + Cypher builders
      embed_query.py        embed_query_node
      compose.py            compose_node + submit_explanation tool + parser
      score.py              score_node
      verify.py             verify_node
tests/unit/agent/
  conftest.py               shared fakes + make_fake_deps / make_agent_state
  test_*.py                 one file per task
tests/integration/agent/
  conftest.py               (Task 11) FastAPI TestClient + traceparent fixture + _TRACER_PROVIDER monkeypatch
  test_agent_http_contract.py  (Task 11) 5 integration tests pinning Invariants 11 + 12
pyproject.toml              (M) + langgraph, opentelemetry-api, fastapi, uvicorn, opentelemetry-instrumentation-fastapi, httpx (test only)
```

| Task | Delivers | Key files |
|---|---|---|
| 1 | `langgraph` + `opentelemetry-api` dependencies; `AgentError` exception | `pyproject.toml`, `core/exceptions.py` |
| 2 | `AgentState` TypedDict; injection-safe-FQN guard | `agent/__init__.py`, `agent/state.py`, `agent/fqn.py` |
| 3 | `AgentDependencies` bundle; `@agent_node` runtime + spans; shared test fakes | `agent/dependencies.py`, `agent/runtime.py`, `tests/unit/agent/conftest.py` |
| 4 | `intent` node — query validation + snapshot resolution | `agent/nodes/__init__.py`, `agent/nodes/intent.py` |
| 5 | `graph_query` node — existence check + `GRAPH_QUERY` citation | `agent/nodes/graph_query.py` |
| 6 | `embed_query` node — knn retrieval + `DOC_PARAGRAPH` / `CODE_SPAN` citations | `agent/nodes/embed_query.py` |
| 7 | `compose` node — LLM evidence selection + `DraftClaim` assembly | `agent/nodes/compose.py` |
| 8 | `score` + `verify` nodes | `agent/nodes/score.py`, `agent/nodes/verify.py` |
| 9 | Graph assembly; `run_agent_query`; end-to-end tests; `__init__` re-exports | `agent/graph.py`, `agent/__init__.py` |
| 10 | Documentation, full-suite green, Plan 10 closure | `docs/`, closure |
| 11 | Agent HTTP entrypoint — FastAPI app + lifespan + uvicorn; W3C traceparent propagation; 5 integration tests pinning Invariants 11 + 12 | `agent/__main__.py`, `agent/app.py`, `agent/server.py`, `agent/dependencies.py` (M — appends `build_agent_dependencies` composer), `models/health.py`, `tests/integration/agent/conftest.py`, `tests/integration/agent/test_agent_http_contract.py`, `pyproject.toml` (M) |

Each task is test-first: write the failing test, run it to confirm the failure mode, write the minimal implementation, confirm green, lint + type-check, commit. Per-task commits use `feat(plan-10): Task N — <summary>`.

---

## Task 1: Dependencies + `AgentError`

**Files:**
- Modify: `pyproject.toml` — add `langgraph` and `opentelemetry-api` to `[project.dependencies]`
- Modify: `src/codeoracle/core/exceptions.py` — add `AgentError`
- Test: `tests/unit/agent/test_dependencies_available.py`, `tests/unit/agent/test_agent_error.py`

LangGraph 1.2 supplies `StateGraph` (the graph builder) and `START`/`END` sentinels. `opentelemetry-api` supplies `trace.get_tracer` and the span context manager — the *API only*, not the SDK. Tasks 1–10 wire only `opentelemetry-api`; SDK installation lands in Task 11 (the agent process startup, via Plan 1A's `init_telemetry()`) per amended Deviation 7.

- [ ] **Step 1: Write the dependency-availability test**

Create `tests/unit/agent/test_dependencies_available.py`:

```python
"""Plan 10 Task 1 — the new third-party dependencies are installed and importable."""


def test_langgraph_state_graph_importable() -> None:
    from langgraph.graph import END, START, StateGraph  # noqa: F401


def test_opentelemetry_api_importable() -> None:
    from opentelemetry import trace
    from opentelemetry.trace import Status, StatusCode  # noqa: F401

    # get_tracer must return a usable (no-op until an SDK provider is set) tracer.
    tracer = trace.get_tracer("codeoracle.agent.test")
    with tracer.start_as_current_span("smoke"):
        pass
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/unit/agent/test_dependencies_available.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'langgraph'`.

- [ ] **Step 3: Add the dependencies to `pyproject.toml`**

In `[project.dependencies]`, add the two pins (keep the array sorted if the existing array is sorted):

```toml
    "langgraph>=1.2,<2.0",
    "opentelemetry-api>=1.30,<2.0",
```

Then lock and sync:

```bash
uv lock
uv sync
```

`langgraph>=1.2,<2.0` — LangGraph follows semver; 1.2 is the first release with the stabilised `StateGraph` API used here, and the `<2.0` ceiling guards against a breaking major. `opentelemetry-api>=1.30,<2.0` — the API package is stable and SDK-independent; the floor is a recent known-good release.

- [ ] **Step 4: Run the test to verify it passes**

Run: `uv run pytest tests/unit/agent/test_dependencies_available.py -v`
Expected: PASS (2 tests).

- [ ] **Step 5: Write the `AgentError` test**

Create `tests/unit/agent/test_agent_error.py`:

```python
"""Plan 10 Task 1 — AgentError is an CodeOracleError carrying a plain message."""

from codeoracle.core.exceptions import AgentError, CodeOracleError


def test_agent_error_is_an_oracle_error() -> None:
    assert issubclass(AgentError, CodeOracleError)


def test_agent_error_carries_its_message() -> None:
    err = AgentError("no APPLIED snapshot exists")
    assert str(err) == "no APPLIED snapshot exists"
    assert isinstance(err, CodeOracleError)
```

- [ ] **Step 6: Run the test to verify it fails**

Run: `uv run pytest tests/unit/agent/test_agent_error.py -v`
Expected: FAIL — `ImportError: cannot import name 'AgentError'`.

- [ ] **Step 7: Add `AgentError` to `core/exceptions.py`**

Append to `src/codeoracle/core/exceptions.py` (after the existing `CodeOracleError` subclasses, keeping the file's ordering convention):

```python
class AgentError(CodeOracleError):
    """Raised inside an agent node when it detects an unrecoverable condition
    for the current query — an injection-unsafe symbol, no APPLIED snapshot,
    a malformed LLM tool call, or an unexpected collaborator failure.

    Agent nodes are wrapped by ``@agent_node`` (see ``codeoracle.agent.runtime``),
    which traps ``AgentError`` — and any other ``Exception`` — and converts it
    to the terminal ``error`` field of ``AgentState``. ``run_agent_query`` then
    maps a set ``error`` to ``QueryResult.failed``. This exception therefore
    never escapes the agent process (plan invariant 6).
    """
```

This is the only Plan 10 change to `core/exceptions.py`. `core/exceptions.py` belongs to Plan 2; adding a leaf subclass is additive and the established cross-plan pattern (Plans 8/9 added `ResolverError`, `UnsupportedHandleTypeError` the same way).

- [ ] **Step 8: Run the test to verify it passes**

Run: `uv run pytest tests/unit/agent/test_agent_error.py -v`
Expected: PASS (2 tests).

- [ ] **Step 9: Lint and type-check**

```bash
uv run ruff check src/codeoracle/core/exceptions.py tests/unit/agent/
uv run ruff format src/codeoracle/core/exceptions.py tests/unit/agent/
uv run ty check src/codeoracle/core/exceptions.py
```

Expected: all clean.

- [ ] **Step 10: Commit**

```bash
git add pyproject.toml uv.lock src/codeoracle/core/exceptions.py tests/unit/agent/test_dependencies_available.py tests/unit/agent/test_agent_error.py
git commit -m "feat(plan-10): Task 1 — langgraph + opentelemetry-api deps, AgentError"
```

---

## Task 2: `AgentState` + injection-safe-FQN guard

**Files:**
- Create: `src/codeoracle/agent/__init__.py`
- Create: `src/codeoracle/agent/state.py`
- Create: `src/codeoracle/agent/fqn.py`
- Test: `tests/unit/agent/test_state.py`, `tests/unit/agent/test_fqn.py`

`AgentState` is the integration substrate: every node reads and writes keys here. It is a plain `TypedDict` with **no reducers** because the graph is a strictly sequential chain — each key is written exactly once, by exactly one node. `run_agent_query` (Task 9) seeds every key before invocation, so a node may read any key unconditionally.

`fqn.py` is the home of invariant 3. `Query.symbol` is `Annotated[str, StringConstraints(min_length=1, max_length=512)]` — Pydantic enforces *length* but no character set, so a symbol like `x'}) DETACH DELETE (n) //` validates as a `Query` yet is a Cypher-injection payload. `is_safe_symbol_fqn` is the gate.

- [ ] **Step 1: Pre-flight import check**

Confirm the model package re-exports the symbols `state.py` needs:

```bash
uv run python -c "from codeoracle.models import Claim, DraftCitation, DraftClaim, HandleType, Query, Verdict; print('models OK')"
```

Expected: `models OK`. If an import fails, the symbol is in its source module (`codeoracle.models.evidence` for the evidence types, `codeoracle.models.query` for `Query`) — Plan 3's `models/__init__.py` re-exports all of them; use the path that resolves.

- [ ] **Step 2: Write the `AgentState` test**

Create `tests/unit/agent/test_state.py`:

```python
"""Plan 10 Task 2 — AgentState declares exactly the eleven pipeline keys."""

from codeoracle.agent.state import AgentState


def test_agent_state_declares_all_pipeline_keys() -> None:
    expected = {
        "query",
        "trace_id",
        "snapshot_sha",
        "plan_handle_types",
        "symbol_found",
        "graph_citations",
        "embed_citations",
        "draft_claim",
        "claim",
        "verdict",
        "error",
    }
    assert set(AgentState.__annotations__) == expected
```

- [ ] **Step 3: Run the test to verify it fails**

Run: `uv run pytest tests/unit/agent/test_state.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'codeoracle.agent'`.

- [ ] **Step 4: Create the agent package marker**

Create `src/codeoracle/agent/__init__.py`:

```python
"""CodeOracle agent process.

A LangGraph state machine that turns an ``explain_symbol`` query into a
verified ``QueryResult``. Task 9 adds the public re-exports
(``build_agent_graph``, ``run_agent_query``, ``AgentState``,
``AgentDependencies``) once those symbols exist.
"""
```

- [ ] **Step 5: Write `agent/state.py`**

Create `src/codeoracle/agent/state.py`:

```python
"""LangGraph state schema for the explain-symbol agent.

``AgentState`` is the substrate every node reads and writes. It is a plain
``TypedDict`` (no reducers): the graph is a strictly sequential chain, so each
key is written exactly once by exactly one node. ``run_agent_query`` seeds
every key before invocation, so nodes may read any key unconditionally.
"""

from typing import TypedDict

from codeoracle.models import (
    Claim,
    DraftCitation,
    DraftClaim,
    HandleType,
    Query,
    Verdict,
)


class AgentState(TypedDict):
    """The agent graph's shared state. Keys are grouped by the node that writes
    them; ``run_agent_query`` seeds defaults for every key before invocation."""

    # --- input: seeded by run_agent_query ---
    query: Query
    trace_id: str
    # --- written by intent_node ---
    snapshot_sha: str
    plan_handle_types: list[HandleType]
    # --- written by graph_query_node ---
    symbol_found: bool
    graph_citations: list[DraftCitation]
    # --- written by embed_query_node ---
    embed_citations: list[DraftCitation]
    # --- written by compose_node ---
    draft_claim: DraftClaim | None
    # --- written by score_node ---
    claim: Claim | None
    # --- written by verify_node (also by graph_query_node on symbol-not-found) ---
    verdict: Verdict | None
    # --- terminal short-circuit, written by @agent_node on any failure ---
    error: str | None
```

- [ ] **Step 6: Run the test to verify it passes**

Run: `uv run pytest tests/unit/agent/test_state.py -v`
Expected: PASS (1 test).

- [ ] **Step 7: Write the `fqn.py` test**

Create `tests/unit/agent/test_fqn.py`:

```python
"""Plan 10 Task 2 — the injection-safe-FQN guard (plan invariant 3)."""

import pytest

from codeoracle.agent.fqn import is_safe_symbol_fqn


@pytest.mark.parametrize(
    "symbol",
    [
        "codeoracle.services.verifier.service.VerifierService.verify",
        "codeoracle.agent.graph.run_agent_query",
        "module.func()",  # SCIP-style callable suffix
        "package.module.ClassName#",  # SCIP-style class suffix
        "_private._helper_fn",
        "single",
    ],
)
def test_accepts_well_formed_fqns(symbol: str) -> None:
    assert is_safe_symbol_fqn(symbol) is True


@pytest.mark.parametrize(
    "symbol",
    [
        "x'}) DETACH DELETE (n) //",  # the canonical Cypher-injection payload
        "a.b; DROP GRAPH",
        "name with spaces",
        "",  # empty — Pydantic allows min_length=1 but charset still required
        "a..b",  # consecutive dots
        ".leading.dot",
        "trailing.dot.",
        "kebab-case",
        "path/with/slashes",
        'has"a"quote',
        "1starts_with_digit",
    ],
)
def test_rejects_injection_unsafe_symbols(symbol: str) -> None:
    assert is_safe_symbol_fqn(symbol) is False
```

- [ ] **Step 8: Run the test to verify it fails**

Run: `uv run pytest tests/unit/agent/test_fqn.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'codeoracle.agent.fqn'`.

- [ ] **Step 9: Write `agent/fqn.py`**

Create `src/codeoracle/agent/fqn.py`:

```python
"""Injection-safe symbol-FQN validation (plan invariant 3).

``Query.symbol`` flows into *literal* openCypher embedded in ``cypher://``
evidence locators — Plan 8's ``GraphQueryResolver`` re-runs the literal query
verbatim. An unsanitised symbol is therefore a Cypher-injection surface.
``is_safe_symbol_fqn`` is the gate: ``intent_node`` calls it before any query
construction, and an unsafe symbol terminates the request before retrieval.
"""

import re

# A dotted identifier path: each segment starts with a letter or underscore and
# continues with word characters. An optional SCIP-style suffix marks a callable
# ``()`` or a class ``#``. Deliberately strict — letters, digits, underscores,
# dots, and the two suffixes only. No quotes, spaces, braces, or semicolons can
# survive this pattern, so nothing injectable reaches a Cypher string.
SAFE_FQN_PATTERN = re.compile(
    r"^[A-Za-z_][A-Za-z0-9_]*(\.[A-Za-z_][A-Za-z0-9_]*)*(\(\)|#)?$"
)


def is_safe_symbol_fqn(symbol: str) -> bool:
    """Return ``True`` iff ``symbol`` is a well-formed, injection-safe FQN."""
    return SAFE_FQN_PATTERN.fullmatch(symbol) is not None
```

- [ ] **Step 10: Run the test to verify it passes**

Run: `uv run pytest tests/unit/agent/test_fqn.py -v`
Expected: PASS (17 parametrised cases).

- [ ] **Step 11: Lint and type-check**

```bash
uv run ruff check src/codeoracle/agent/ tests/unit/agent/
uv run ruff format src/codeoracle/agent/ tests/unit/agent/
uv run ty check src/codeoracle/agent/
```

Expected: all clean.

- [ ] **Step 12: Commit**

```bash
git add src/codeoracle/agent/__init__.py src/codeoracle/agent/state.py src/codeoracle/agent/fqn.py tests/unit/agent/test_state.py tests/unit/agent/test_fqn.py
git commit -m "feat(plan-10): Task 2 — AgentState TypedDict + injection-safe-FQN guard"
```

---

## Task 3: `AgentDependencies` + `@agent_node` runtime + shared test fakes

**Files:**
- Create: `src/codeoracle/agent/dependencies.py`
- Create: `src/codeoracle/agent/runtime.py`
- Create: `tests/unit/agent/conftest.py`
- Test: `tests/unit/agent/test_dependencies.py`, `tests/unit/agent/test_runtime.py`

This task builds the agent's spine: the dependency bundle, the node-execution decorator that *is* invariant 10's single short-circuit mechanism, and the `resolve_to_citation` helper that *is* invariant 1. It also creates the shared test fakes — every later task's tests configure collaborators through `make_fake_deps`, never by constructing a fake class directly.

`@agent_node` does three things in one wrapper, because every node needs all three: (a) skip the node and return `{}` when a terminal `error`/`verdict` is already set — no span opened for a skipped node; (b) run the body inside an OpenTelemetry span (or no span when `span_name is None` — the `score` node has no gate-9 span); (c) trap any `Exception` into a terminal `error` so the node never propagates into LangGraph (invariant 6).

- [ ] **Step 1: Pre-flight import check**

Confirm every collaborator type `dependencies.py` imports resolves:

```bash
uv run python -c "
from codeoracle.adapters.llm.embeddings import EmbeddingsProvider
from codeoracle.adapters.llm.interfaces import LLMProvider
from codeoracle.db.repositories.embeddings import EmbeddingsRepository
from codeoracle.db.repositories.graph import GraphRepository
from codeoracle.db.repositories.snapshots import SnapshotRepository
from codeoracle.services.handle_resolution import HandleResolverService
from codeoracle.services.trust_matrix import TrustMatrixService
from codeoracle.services.verifier import VerifierService
print('agent dependencies OK')
"
```

Expected: `agent dependencies OK`. If a path differs, the type is wherever its owning plan placed it — `grep -rn "class <Name>" src/codeoracle/` and use the resolved path.

- [ ] **Step 2: Write the `AgentDependencies` test**

Create `tests/unit/agent/test_dependencies.py`:

```python
"""Plan 10 Task 3 — AgentDependencies is a frozen, fully-populated bundle."""

import dataclasses

import pytest

from codeoracle.agent.dependencies import AgentDependencies


def test_agent_dependencies_is_frozen(make_fake_deps) -> None:
    deps = make_fake_deps()
    with pytest.raises(dataclasses.FrozenInstanceError):
        deps.llm = None  # type: ignore[misc]


def test_agent_dependencies_exposes_every_collaborator(make_fake_deps) -> None:
    deps = make_fake_deps()
    for attr in (
        "graph_repo",
        "embeddings_repo",
        "snapshot_repo",
        "resolver",
        "verifier",
        "trust_matrix",
        "llm",
        "embeddings",
    ):
        assert getattr(deps, attr) is not None


def test_agent_dependencies_field_set_is_exactly_eight() -> None:
    fields = {f.name for f in dataclasses.fields(AgentDependencies)}
    assert fields == {
        "graph_repo",
        "embeddings_repo",
        "snapshot_repo",
        "resolver",
        "verifier",
        "trust_matrix",
        "llm",
        "embeddings",
    }
```

- [ ] **Step 3: Run the test to verify it fails**

Run: `uv run pytest tests/unit/agent/test_dependencies.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'codeoracle.agent.dependencies'` (and `make_fake_deps` fixture unknown — both fixed in this task).

- [ ] **Step 4: Write `agent/dependencies.py`**

Create `src/codeoracle/agent/dependencies.py`:

```python
"""The collaborator bundle injected into the agent graph.

``AgentDependencies`` is built once per query by the agent process's FastAPI
route handler (Task 11) via ``build_agent_dependencies(session, sourcebot,
llm, embeddings, settings)`` in ``codeoracle/agent/dependencies.py``. Per-request
data (the ``Query``) stays in the route-handler scope and is forwarded to
``run_agent_query(graph, query)`` directly — not to the composer. Tests use
``make_fake_deps``.
``build_agent_graph`` bakes one instance into the node closures, so a compiled
graph belongs to exactly one query (invariant 9).

The repositories are bound to a single request-scoped ``AsyncSession``. Because
the graph runs strictly sequentially (invariant 4) that session is never
touched concurrently — a parallel retrieval fan-out would corrupt it.
"""

from dataclasses import dataclass

from codeoracle.adapters.llm.embeddings import EmbeddingsProvider
from codeoracle.adapters.llm.interfaces import LLMProvider
from codeoracle.db.repositories.embeddings import EmbeddingsRepository
from codeoracle.db.repositories.graph import GraphRepository
from codeoracle.db.repositories.snapshots import SnapshotRepository
from codeoracle.services.handle_resolution import HandleResolverService
from codeoracle.services.trust_matrix import TrustMatrixService
from codeoracle.services.verifier import VerifierService


@dataclass(frozen=True, slots=True)
class AgentDependencies:
    """Everything the agent's nodes need, assembled once per query."""

    graph_repo: GraphRepository
    embeddings_repo: EmbeddingsRepository
    snapshot_repo: SnapshotRepository
    resolver: HandleResolverService
    verifier: VerifierService
    trust_matrix: TrustMatrixService
    llm: LLMProvider
    embeddings: EmbeddingsProvider
```

- [ ] **Step 5: Write the `@agent_node` runtime test**

Create `tests/unit/agent/test_runtime.py`:

```python
"""Plan 10 Task 3 — the @agent_node decorator + resolve_to_citation helper."""

import pytest

from codeoracle.agent.runtime import (
    agent_node,
    get_tracer,
    resolve_span_name,
    resolve_to_citation,
)
from codeoracle.agent.state import AgentState
from codeoracle.core.exceptions import AgentError
from codeoracle.models import EvidenceHandle, HandleType, VerdictStatus, Verdict


def _clean_state() -> dict[str, object]:
    """A state with no terminal flag set — the decorator should run the body."""
    return {"error": None, "verdict": None}


async def test_agent_node_runs_body_and_returns_update() -> None:
    @agent_node("test.span")
    async def node(state: AgentState, deps: object) -> dict[str, object]:
        return {"snapshot_sha": "abc1234"}

    result = await node(_clean_state(), None)  # type: ignore[arg-type]
    assert result == {"snapshot_sha": "abc1234"}


async def test_agent_node_traps_exception_into_error() -> None:
    @agent_node("test.span")
    async def node(state: AgentState, deps: object) -> dict[str, object]:
        raise AgentError("boom")

    result = await node(_clean_state(), None)  # type: ignore[arg-type]
    assert set(result) == {"error"}
    assert "boom" in str(result["error"])
    assert "node" in str(result["error"])  # the failing node is named


async def test_agent_node_traps_unexpected_exception_too() -> None:
    @agent_node("test.span")
    async def node(state: AgentState, deps: object) -> dict[str, object]:
        raise RuntimeError("collaborator blew up")

    result = await node(_clean_state(), None)  # type: ignore[arg-type]
    assert "collaborator blew up" in str(result["error"])


async def test_agent_node_short_circuits_when_error_set() -> None:
    ran = False

    @agent_node("test.span")
    async def node(state: AgentState, deps: object) -> dict[str, object]:
        nonlocal ran
        ran = True
        return {"snapshot_sha": "x"}

    result = await node({"error": "prior failure", "verdict": None}, None)  # type: ignore[arg-type]
    assert result == {}
    assert ran is False


async def test_agent_node_short_circuits_when_verdict_set() -> None:
    ran = False
    verdict = Verdict(status=VerdictStatus.UNKNOWN, reason="x", resolved_citations=[])

    @agent_node("test.span")
    async def node(state: AgentState, deps: object) -> dict[str, object]:
        nonlocal ran
        ran = True
        return {"snapshot_sha": "x"}

    result = await node({"error": None, "verdict": verdict}, None)  # type: ignore[arg-type]
    assert result == {}
    assert ran is False


async def test_agent_node_with_none_span_name_still_runs_and_traps() -> None:
    @agent_node(None)
    async def ok(state: AgentState, deps: object) -> dict[str, object]:
        return {"claim": None}

    @agent_node(None)
    async def boom(state: AgentState, deps: object) -> dict[str, object]:
        raise AgentError("no span but still trapped")

    assert await ok(_clean_state(), None) == {"claim": None}  # type: ignore[arg-type]
    trapped = await boom(_clean_state(), None)  # type: ignore[arg-type]
    assert "no span but still trapped" in str(trapped["error"])


def test_resolve_span_name() -> None:
    assert resolve_span_name("code_span") == "resolve.code_span"
    assert resolve_span_name("graph_query") == "resolve.graph_query"


def test_get_tracer_returns_a_tracer() -> None:
    tracer = get_tracer()
    with tracer.start_as_current_span("smoke"):
        pass  # no-op tracer until an SDK provider is installed — must not raise


async def test_resolve_to_citation_uses_resolver_output_as_excerpt(make_fake_deps) -> None:
    deps = make_fake_deps(resolve_content="VERBATIM")
    handle = EvidenceHandle(
        type=HandleType.CODE_SPAN,
        locator="file://src/codeoracle/x.py:1-3",
        snapshot_sha="abc1234",
    )
    citation = await resolve_to_citation(handle, deps.resolver)
    assert citation.handle == handle
    # the fake resolver returns "<content>::<locator>" — the excerpt is exactly that
    assert citation.excerpt == "VERBATIM::file://src/codeoracle/x.py:1-3"


async def test_resolve_to_citation_propagates_resolver_failure(make_fake_deps) -> None:
    """A resolve failure is recorded on the span and re-raised — the caller's
    @agent_node wrapper turns the propagated exception into a terminal error."""
    deps = make_fake_deps(resolve_raises=RuntimeError("sourcebot down"))
    handle = EvidenceHandle(
        type=HandleType.CODE_SPAN,
        locator="file://src/codeoracle/x.py:1-3",
        snapshot_sha="abc1234",
    )
    with pytest.raises(RuntimeError, match="sourcebot down"):
        await resolve_to_citation(handle, deps.resolver)
```

- [ ] **Step 6: Run the test to verify it fails**

Run: `uv run pytest tests/unit/agent/test_runtime.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'codeoracle.agent.runtime'`.

- [ ] **Step 7: Write `agent/runtime.py`**

Create `src/codeoracle/agent/runtime.py`:

```python
"""Node-execution runtime for the agent graph.

Three concerns live here because every node depends on all three:

* **Tracing** — ``get_tracer`` and the §8.5 gate-9 span-name constants.
* **The ``agent_node`` decorator** — invariant 10's *single* short-circuit and
  error mechanism. It skips a node (returning ``{}``, no span) when a terminal
  ``error``/``verdict`` is already set, wraps the body in a span, and traps any
  ``Exception`` into a terminal ``error``. The graph wiring stays a plain
  linear chain of ``add_edge`` calls.
* **``resolve_to_citation``** — invariant 1 made concrete: resolve a handle and
  pair it with its *verbatim* resolved excerpt, so the citation round-trips
  through ``VerifierService`` to PROVEN by construction.
"""

import functools
from collections.abc import Awaitable, Callable
from contextlib import nullcontext

from opentelemetry import trace
from opentelemetry.trace import Status, StatusCode, Tracer

from codeoracle.agent.dependencies import AgentDependencies
from codeoracle.agent.state import AgentState
from codeoracle.models import DraftCitation, EvidenceHandle
from codeoracle.services.handle_resolution import HandleResolverService

# --- §8.5 gate-9 span names -------------------------------------------------
SPAN_INTENT = "intent.parse"
SPAN_GRAPH_QUERY = "graph.query"
SPAN_EMBED_QUERY = "embed.query"
SPAN_COMPOSE = "llm.compose"
SPAN_VERIFY = "verifier.verify"

NodeUpdate = dict[str, object]
NodeFn = Callable[[AgentState, AgentDependencies], Awaitable[NodeUpdate]]


def get_tracer() -> Tracer:
    """Return the agent's OpenTelemetry tracer.

    No-op until a process installs an SDK ``TracerProvider`` (Plan 11 API /
    Plan 13 slice) — so spans are zero-overhead when the agent is unit-tested
    in isolation, and form the §8.5 gate-9 span tree once the SDK is wired.
    """
    return trace.get_tracer("codeoracle.agent")


def resolve_span_name(handle_type_value: str) -> str:
    """Span name for one handle resolution — ``resolve.<handle_type>``."""
    return f"resolve.{handle_type_value}"


def agent_node(span_name: str | None) -> Callable[[NodeFn], NodeFn]:
    """Wrap an agent node with short-circuit + span + exception trapping.

    * If ``state`` already carries a terminal ``error`` or ``verdict``, the
      node is skipped — returns ``{}``, opens no span.
    * Otherwise the body runs inside a span named ``span_name`` (or no span
      when ``span_name is None`` — the ``score`` node has no gate-9 span).
    * Any ``Exception`` is recorded on the span and converted to a terminal
      ``error`` update, prefixed with the node's name. The node never
      propagates an exception into LangGraph (invariant 6).
    """

    def decorator(fn: NodeFn) -> NodeFn:
        @functools.wraps(fn)
        async def wrapper(state: AgentState, deps: AgentDependencies) -> NodeUpdate:
            if state.get("error") is not None or state.get("verdict") is not None:
                return {}
            span_cm = (
                get_tracer().start_as_current_span(span_name)
                if span_name is not None
                else nullcontext()
            )
            with span_cm as span:
                try:
                    return await fn(state, deps)
                except Exception as exc:  # noqa: BLE001 — boundary trap (invariant 6)
                    if span is not None:
                        span.record_exception(exc)
                        span.set_status(Status(StatusCode.ERROR, str(exc)))
                    return {"error": f"{fn.__name__}: {exc}"}

        return wrapper

    return decorator


async def resolve_to_citation(
    handle: EvidenceHandle, resolver: HandleResolverService
) -> DraftCitation:
    """Resolve ``handle`` and pair it with its verbatim excerpt (invariant 1).

    The excerpt is exactly what ``HandleResolverService.resolve`` returns —
    never LLM-authored text — so re-resolving the same handle during
    verification yields a byte-identical excerpt and the citation cannot be
    REFUTED for a paraphrase. The resolution runs inside a
    ``resolve.<handle_type>`` span; a resolve failure is recorded on that span
    (so the trace does not show a failed resolve as successful) and re-raised
    for the caller's ``@agent_node`` wrapper to convert into a terminal error.
    """
    with get_tracer().start_as_current_span(resolve_span_name(handle.type.value)) as span:
        try:
            excerpt = await resolver.resolve(handle)
        except Exception as exc:
            span.record_exception(exc)
            span.set_status(Status(StatusCode.ERROR, str(exc)))
            raise
    return DraftCitation(handle=handle, excerpt=excerpt)
```

- [ ] **Step 8: Run the test to verify it passes**

Run: `uv run pytest tests/unit/agent/test_runtime.py tests/unit/agent/test_dependencies.py -v`
Expected: still FAIL — `make_fake_deps` fixture is undefined until Step 9. Tests not using `make_fake_deps` should pass; the rest error on the missing fixture.

- [ ] **Step 9: Write the shared test fakes (`conftest.py`)**

Create `tests/unit/agent/conftest.py`. Every fake mirrors exactly the methods the agent calls, with verified signatures. Tests configure collaborators through `make_fake_deps`'s keyword arguments — never by constructing a fake class — so the fake classes stay private to this module and test files need no cross-file imports.

```python
"""Shared fakes + factories for agent unit tests.

The fakes implement exactly the methods the agent calls, with the signatures
verified against Plans 4-9. Tests configure them through ``make_fake_deps`` /
``make_agent_state`` keyword arguments; the fake classes are private.
"""

import json
from collections.abc import Callable, Sequence
from typing import Any

import pytest

from codeoracle.agent.dependencies import AgentDependencies
from codeoracle.agent.state import AgentState
from codeoracle.models import (
    Citation,
    Claim,
    DraftClaim,
    EmbeddingHit,
    EvidenceHandle,
    HandleType,
    Query,
    Snapshot,
    SnapshotStatus,
    Verdict,
    VerdictStatus,
)
from codeoracle.models.llm import ChatResponse, TokenUsage, ToolCall

_DEFAULT_SHA = "abc1234"


# --- fake collaborators -----------------------------------------------------

class _FakeGraphRepo:
    """Fakes GraphRepository.execute_cypher. The agent calls execute_cypher
    exactly once per query — the symbol-existence check — so the fake returns
    the configured existence rows for any call and records what it was asked.
    (The callers query reaches the DB through the resolver, never this repo.)"""

    def __init__(self, *, exists_rows: list[Any]) -> None:
        self._exists_rows = exists_rows
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def execute_cypher(
        self, stmt: str, params: dict[str, Any] | None = None
    ) -> list[Any]:
        self.calls.append((stmt, params or {}))
        return self._exists_rows


class _FakeEmbeddingsRepo:
    def __init__(self, *, hits: Sequence[EmbeddingHit]) -> None:
        self._hits = list(hits)

    async def knn(
        self,
        *,
        query_embedding: list[float],
        snapshot_sha: str,
        k: int,
    ) -> list[EmbeddingHit]:
        # Keyword-only signature mirrors Plan 6's EmbeddingsRepository.knn
        # contract (P0-17). A positional call from the agent would TypeError
        # at unit-test time, failing the suite green before signature drift
        # could ship.
        return self._hits[:k]


class _FakeSnapshotRepo:
    def __init__(
        self, *, latest: Snapshot | None, pinned_status: SnapshotStatus | None
    ) -> None:
        self._latest = latest
        self._pinned_status = pinned_status

    async def get_latest_applied(self) -> Snapshot | None:
        return self._latest

    async def get_by_sha(self, sha: str) -> Snapshot | None:
        # Mirrors the real repo's WHERE sha == :sha: the returned snapshot carries
        # the queried sha. ``pinned_status`` None models "no such row" so tests
        # exercise the intent node's APPLIED gate (missing + non-APPLIED) on the
        # pinned path.
        if self._pinned_status is None:
            return None
        return Snapshot(sha=sha, status=self._pinned_status)


class _FakeResolver:
    """Fakes HandleResolverService.resolve. Returns ``<content>::<locator>`` so
    every citation gets a distinct, deterministic excerpt; or raises."""

    def __init__(self, *, content: str, raise_exc: Exception | None = None) -> None:
        self._content = content
        self._raise = raise_exc
        self.resolved: list[EvidenceHandle] = []

    async def resolve(self, handle: EvidenceHandle) -> str:
        if self._raise is not None:
            raise self._raise
        self.resolved.append(handle)
        return f"{self._content}::{handle.locator}"


class _FakeVerifier:
    def __init__(self, *, verdict: Verdict) -> None:
        self._verdict = verdict
        self.verified: list[Claim] = []

    async def verify(self, claim: Claim) -> Verdict:
        self.verified.append(claim)
        return self._verdict


class _FakeTrustMatrix:
    """Mirrors the real synchronous ``score_each(draft, *, source_age_days=0)``;
    assigns a constant trust of 7 to every citation."""

    def score_each(self, draft: DraftClaim, *, source_age_days: int = 0) -> Claim:
        return Claim(
            statement=draft.statement,
            citations=[
                Citation(handle=c.handle, excerpt=c.excerpt, source_trust=7)
                for c in draft.citations
            ],
            confidence=draft.confidence,
            contradictions=[
                Citation(handle=c.handle, excerpt=c.excerpt, source_trust=7)
                for c in draft.contradictions
            ],
        )


class _FakeLLM:
    def __init__(self, *, response: ChatResponse) -> None:
        self._response = response
        self.messages_seen: list[Any] = []

    async def chat(self, messages: list[Any], *, tools: Any = None, structured_output: Any = None) -> ChatResponse:
        self.messages_seen = list(messages)
        return self._response

    def count_tokens(self, text: str) -> int:
        return len(text) // 4


class _FakeEmbeddings:
    def __init__(self, *, vector: Sequence[float]) -> None:
        self._vector = list(vector)

    async def embed(self, texts: list[str]) -> list[list[float]]:
        return [list(self._vector) for _ in texts]


# --- builders ---------------------------------------------------------------

def default_tool_args(
    citation_indices: Sequence[int] = (0, 1, 2),
    contradiction_indices: Sequence[int] = (),
    *,
    statement: str = "The symbol resolves and checks each evidence citation.",
    confidence: float = 0.9,
) -> str:
    """A well-formed ``submit_explanation`` arguments JSON string."""
    return json.dumps(
        {
            "statement": statement,
            "confidence": confidence,
            "citation_indices": list(citation_indices),
            "contradiction_indices": list(contradiction_indices),
        }
    )


def chat_response(arguments_json: str, *, tool_name: str = "submit_explanation") -> ChatResponse:
    """A ChatResponse whose single tool call carries ``arguments_json``."""
    return ChatResponse(
        content="",
        usage=TokenUsage(input_tokens=20, output_tokens=20),
        finish_reason="tool_calls",
        tool_calls=[ToolCall(id="call_1", name=tool_name, arguments_json=arguments_json)],
    )


def chat_response_no_tool_call() -> ChatResponse:
    """A ChatResponse with prose only — no tool call (a compose failure case)."""
    return ChatResponse(
        content="I cannot determine this.",
        usage=TokenUsage(input_tokens=20, output_tokens=5),
        finish_reason="stop",
        tool_calls=[],
    )


def _default_hits() -> list[EmbeddingHit]:
    return [
        EmbeddingHit(
            snapshot_sha=_DEFAULT_SHA,
            source_path="src/codeoracle/services/verifier/service.py",
            chunk_start_line=10 + 20 * i,
            chunk_end_line=25 + 20 * i,
            content=f"chunk {i} body",
            distance=0.1 * (i + 1),
        )
        for i in range(3)
    ]


# --- fixtures ---------------------------------------------------------------

@pytest.fixture
def make_fake_deps() -> Callable[..., AgentDependencies]:
    """Return a factory producing an AgentDependencies of configurable fakes.

    Keyword arguments (all optional):
      symbol_exists      — graph existence check returns a row (default True)
      knn_hits           — explicit list[EmbeddingHit] (default 3 hits)
      resolve_content    — resolver excerpt prefix; resolve_raises — make it raise
      has_snapshot       — get_latest_applied returns a Snapshot (default True)
      latest_sha         — that snapshot's sha
      pinned_status      — status of the snapshot get_by_sha returns for a pinned
                           SHA (default APPLIED); None → get_by_sha returns None
      llm_response       — explicit ChatResponse; llm_tool_args — explicit args JSON
      verdict            — the verdict FakeVerifier.verify returns
    """

    def _make(
        *,
        symbol_exists: bool = True,
        knn_hits: list[EmbeddingHit] | None = None,
        resolve_content: str = "<resolved>",
        resolve_raises: Exception | None = None,
        has_snapshot: bool = True,
        latest_sha: str = _DEFAULT_SHA,
        pinned_status: SnapshotStatus | None = SnapshotStatus.APPLIED,
        llm_response: ChatResponse | None = None,
        llm_tool_args: str | None = None,
        verdict: Verdict | None = None,
        embed_vector: Sequence[float] = (0.1, 0.2, 0.3),
    ) -> AgentDependencies:
        if llm_response is None:
            args = llm_tool_args if llm_tool_args is not None else default_tool_args()
            llm_response = chat_response(args)
        if verdict is None:
            verdict = Verdict(
                status=VerdictStatus.PROVEN,
                reason="every citation resolved and matched",
                resolved_citations=[],
            )
        snapshot = Snapshot(sha=latest_sha) if has_snapshot else None
        return AgentDependencies(
            graph_repo=_FakeGraphRepo(  # type: ignore[arg-type]
                exists_rows=[{"fqn": "found"}] if symbol_exists else [],
            ),
            embeddings_repo=_FakeEmbeddingsRepo(hits=knn_hits if knn_hits is not None else _default_hits()),  # type: ignore[arg-type]
            snapshot_repo=_FakeSnapshotRepo(latest=snapshot, pinned_status=pinned_status),  # type: ignore[arg-type]
            resolver=_FakeResolver(content=resolve_content, raise_exc=resolve_raises),  # type: ignore[arg-type]
            verifier=_FakeVerifier(verdict=verdict),  # type: ignore[arg-type]
            trust_matrix=_FakeTrustMatrix(),  # type: ignore[arg-type]
            llm=_FakeLLM(response=llm_response),  # type: ignore[arg-type]
            embeddings=_FakeEmbeddings(vector=embed_vector),  # type: ignore[arg-type]
        )

    return _make


@pytest.fixture
def make_agent_state() -> Callable[..., AgentState]:
    """Return a factory producing an AgentState seeded like ``run_agent_query``
    does — every key present, overridable by keyword."""

    def _make(**overrides: Any) -> AgentState:
        state: dict[str, Any] = {
            "query": Query(symbol="codeoracle.services.verifier.service.VerifierService.verify"),
            "trace_id": "trace-test",
            "snapshot_sha": _DEFAULT_SHA,
            "plan_handle_types": [HandleType.CODE_SPAN, HandleType.DOC_PARAGRAPH, HandleType.GRAPH_QUERY],
            "symbol_found": False,
            "graph_citations": [],
            "embed_citations": [],
            "draft_claim": None,
            "claim": None,
            "verdict": None,
            "error": None,
        }
        state.update(overrides)
        return state  # type: ignore[return-value]

    return _make
```

The `# type: ignore[arg-type]` markers are deliberate: the fakes are *structural* stand-ins — they implement the called methods but do not subclass the real types. `ty` runs strict over `src/codeoracle/`; test fakes are not required to satisfy nominal typing.

**Why `_FakeLLM` here, not the spec-§5.7 `MockLLMProvider` (the unit-vs-integration split — D-10.U3).** Plan 10's unit layer drives the LLM through `_FakeLLM` — a minimal structural double returning one canned `ChatResponse` — *by design*: a unit test of a single node must be able to vary the tool-call shape (well-formed, malformed, absent, confidence out of range) directly in-test, which a fixture-file-keyed provider would force into YAML round-trips for no unit-level gain. The trade-off is that `_FakeLLM` exercises neither `template_id` nor Plan 4's `request_fingerprint` (it ignores the messages it is handed). That path is **not** left unexercised — it is covered one layer up, at the Plan 13 **integration** layer: Plan 13's slice tests run every process against the real `MockLLMProvider` (spec §5.7) configured via `CODEORACLE_LLM_PROVIDER=mock` + `CODEORACLE_LLM_MOCK__SCRIPTS_DIR=tests/fixtures/llm_scripts`, whose responses are keyed by Plan 4's `template_id` + `request_fingerprint` scheme, so cosmetic prompt-template drift fails slice CI (Plan 13 Invariant 2 / Task 9; cross-plan dep recorded in Plan 13's consumes-from-Plan-10 row). Unit layer: `_FakeLLM`, fingerprint-agnostic. Integration layer: real `MockLLMProvider`, fingerprint-exercised. Neither layer is redundant; together they close the LLM-path coverage.

- [ ] **Step 10: Run the tests to verify they pass**

Run: `uv run pytest tests/unit/agent/test_runtime.py tests/unit/agent/test_dependencies.py -v`
Expected: PASS (all tests — `make_fake_deps` is now defined).

- [ ] **Step 11: Lint and type-check**

```bash
uv run ruff check src/codeoracle/agent/ tests/unit/agent/
uv run ruff format src/codeoracle/agent/ tests/unit/agent/
uv run ty check src/codeoracle/agent/
```

Expected: all clean (`ty` targets `src/codeoracle/agent/` — the structural fakes in `conftest.py` are not strict-checked).

- [ ] **Step 12: Commit**

```bash
git add src/codeoracle/agent/dependencies.py src/codeoracle/agent/runtime.py tests/unit/agent/conftest.py tests/unit/agent/test_dependencies.py tests/unit/agent/test_runtime.py
git commit -m "feat(plan-10): Task 3 — AgentDependencies, @agent_node runtime, shared test fakes"
```

---

## Task 4: `intent` node — query validation + snapshot resolution

**Files:**
- Create: `src/codeoracle/agent/nodes/__init__.py`
- Create: `src/codeoracle/agent/nodes/intent.py`
- Test: `tests/unit/agent/test_intent_node.py`

The `intent` node is the first node and is **deterministic** — no LLM call (Deviation 2). It has two jobs: enforce invariant 3 (reject an injection-unsafe symbol before any Cypher is built) and resolve the corpus snapshot (the query's explicit pin, or the latest APPLIED snapshot — and either way the resolved snapshot must be APPLIED, so a pinned non-APPLIED SHA is rejected). It writes `snapshot_sha` and `plan_handle_types`.

- [ ] **Step 1: Pre-flight import check**

```bash
uv run python -c "from codeoracle.models import HandleType, Query; q = Query(symbol='a.b.c'); print(q.symbol, q.snapshot_sha)"
```

Expected: `a.b.c None` — confirms `Query.symbol` and the optional `Query.snapshot_sha` field.

- [ ] **Step 2: Write the `intent` node test**

Create `tests/unit/agent/test_intent_node.py`:

```python
"""Plan 10 Task 4 — the intent node: deterministic validation + snapshot resolution."""

from codeoracle.agent.nodes.intent import EXPLAIN_SYMBOL_HANDLE_TYPES, intent_node
from codeoracle.models import HandleType, Query, SnapshotStatus


async def test_intent_resolves_latest_snapshot_when_query_unpinned(
    make_fake_deps, make_agent_state
) -> None:
    deps = make_fake_deps(latest_sha="deadbeef")
    state = make_agent_state(query=Query(symbol="codeoracle.agent.graph.run_agent_query"))
    update = await intent_node(state, deps)
    assert update["snapshot_sha"] == "deadbeef"
    assert update["plan_handle_types"] == [HandleType.CODE_SPAN, HandleType.DOC_PARAGRAPH, HandleType.GRAPH_QUERY]


async def test_intent_honors_explicit_snapshot_pin(make_fake_deps, make_agent_state) -> None:
    # The pinned SHA is resolved via get_by_sha and must be APPLIED (default fake).
    deps = make_fake_deps(latest_sha="deadbeef")
    state = make_agent_state(query=Query(symbol="codeoracle.x.y", snapshot_sha="pinned99"))
    update = await intent_node(state, deps)
    assert update["snapshot_sha"] == "pinned99"  # the pin wins over the latest


async def test_intent_rejects_pinned_snapshot_that_is_not_applied(
    make_fake_deps, make_agent_state
) -> None:
    # A pin to a non-APPLIED SHA must NOT silently query a half-applied corpus.
    deps = make_fake_deps(pinned_status=SnapshotStatus.PENDING)
    state = make_agent_state(query=Query(symbol="codeoracle.x.y", snapshot_sha="pending99"))
    update = await intent_node(state, deps)
    assert "is not APPLIED" in str(update["error"])
    assert "snapshot_sha" not in update


async def test_intent_rejects_pinned_snapshot_that_is_missing(
    make_fake_deps, make_agent_state
) -> None:
    # A pin to a SHA with no snapshot row is rejected, not passed through.
    deps = make_fake_deps(pinned_status=None)
    state = make_agent_state(query=Query(symbol="codeoracle.x.y", snapshot_sha="ghost99"))
    update = await intent_node(state, deps)
    assert "is not APPLIED" in str(update["error"])
    assert "snapshot_sha" not in update


async def test_intent_errors_when_no_snapshot_exists(make_fake_deps, make_agent_state) -> None:
    deps = make_fake_deps(has_snapshot=False)
    state = make_agent_state(query=Query(symbol="codeoracle.x.y"))
    update = await intent_node(state, deps)
    assert "no APPLIED snapshot" in str(update["error"])


async def test_intent_rejects_injection_unsafe_symbol(make_fake_deps, make_agent_state) -> None:
    # Query accepts this (it is only length-bounded); the agent's FQN guard must not.
    deps = make_fake_deps()
    state = make_agent_state(query=Query(symbol="x'}) DETACH DELETE (n) //"))
    update = await intent_node(state, deps)
    assert "injection-safe" in str(update["error"])
    # the unsafe symbol never reaches snapshot resolution
    assert "snapshot_sha" not in update


async def test_intent_never_plans_stub_handle_types(make_fake_deps, make_agent_state) -> None:
    """§8.5 — test_intent_classifier_never_produces_unsupported_handle_types.

    For every symbol, the planned handle types exclude TRACE_SPAN / DB_RECORD.
    """
    deps = make_fake_deps()
    for symbol in ("codeoracle.a.b", "codeoracle.c.d.e", "module.func()", "pkg.Class#", "single"):
        update = await intent_node(make_agent_state(query=Query(symbol=symbol)), deps)
        planned = update["plan_handle_types"]
        assert HandleType.TRACE_SPAN not in planned  # type: ignore[operator]
        assert HandleType.DB_RECORD not in planned  # type: ignore[operator]


def test_explain_symbol_handle_types_constant() -> None:
    assert EXPLAIN_SYMBOL_HANDLE_TYPES == [HandleType.CODE_SPAN, HandleType.DOC_PARAGRAPH, HandleType.GRAPH_QUERY]
```

- [ ] **Step 3: Run the test to verify it fails**

Run: `uv run pytest tests/unit/agent/test_intent_node.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'codeoracle.agent.nodes'`.

- [ ] **Step 4: Create the nodes package marker**

Create `src/codeoracle/agent/nodes/__init__.py`:

```python
"""Agent graph nodes — one module per node in the explain-symbol pipeline."""
```

- [ ] **Step 5: Write `agent/nodes/intent.py`**

Create `src/codeoracle/agent/nodes/intent.py`:

```python
"""The intent node — deterministic query validation and snapshot resolution."""

from codeoracle.agent.dependencies import AgentDependencies
from codeoracle.agent.fqn import is_safe_symbol_fqn
from codeoracle.agent.runtime import SPAN_INTENT, NodeUpdate, agent_node
from codeoracle.agent.state import AgentState
from codeoracle.core.exceptions import AgentError
from codeoracle.models import HandleType, SnapshotStatus

# The handle types the explain_symbol plan emits (invariant 8). TRACE_SPAN and
# DB_RECORD are structurally absent — the §8.5 intent-classifier test pins this.
EXPLAIN_SYMBOL_HANDLE_TYPES = [HandleType.CODE_SPAN, HandleType.DOC_PARAGRAPH, HandleType.GRAPH_QUERY]


@agent_node(SPAN_INTENT)
async def intent_node(state: AgentState, deps: AgentDependencies) -> NodeUpdate:
    """Validate the query and resolve the corpus snapshot.

    Deterministic — no LLM call (Deviation 2). Two responsibilities:

    1. Reject an injection-unsafe symbol *before* any Cypher is built
       (invariant 3) — the symbol is interpolated into literal ``cypher://``
       queries downstream, so this is the injection gate.
    2. Resolve ``snapshot_sha``: the query's explicit pin if present, else the
       latest APPLIED snapshot. Either way the resolved snapshot MUST be
       APPLIED — a pinned SHA that is missing, PENDING, APPLYING, or FAILED is
       rejected, so the retrieval nodes never query a half-applied corpus. No
       APPLIED snapshot at all → the query cannot run.
    """
    query = state["query"]
    if not is_safe_symbol_fqn(query.symbol):
        raise AgentError(f"symbol is not an injection-safe FQN: {query.symbol!r}")

    if query.snapshot_sha is not None:
        snapshot = await deps.snapshot_repo.get_by_sha(query.snapshot_sha)
        if snapshot is None or snapshot.status is not SnapshotStatus.APPLIED:
            raise AgentError(
                f"pinned snapshot {query.snapshot_sha!r} is not APPLIED; "
                "cannot resolve a corpus"
            )
        snapshot_sha = snapshot.sha
    else:
        snapshot = await deps.snapshot_repo.get_latest_applied()
        if snapshot is None:
            raise AgentError("no APPLIED snapshot exists; cannot resolve a corpus")
        snapshot_sha = snapshot.sha

    return {
        "snapshot_sha": snapshot_sha,
        "plan_handle_types": list(EXPLAIN_SYMBOL_HANDLE_TYPES),
    }
```

`list(EXPLAIN_SYMBOL_HANDLE_TYPES)` returns a fresh copy so the state owns its own list rather than aliasing the module-level constant.

- [ ] **Step 6: Run the test to verify it passes**

Run: `uv run pytest tests/unit/agent/test_intent_node.py -v`
Expected: PASS (8 tests).

- [ ] **Step 7: Lint and type-check**

```bash
uv run ruff check src/codeoracle/agent/ tests/unit/agent/
uv run ruff format src/codeoracle/agent/ tests/unit/agent/
uv run ty check src/codeoracle/agent/
```

Expected: all clean.

- [ ] **Step 8: Commit**

```bash
git add src/codeoracle/agent/nodes/__init__.py src/codeoracle/agent/nodes/intent.py tests/unit/agent/test_intent_node.py
git commit -m "feat(plan-10): Task 4 — intent node (validation + snapshot resolution)"
```

---

## Task 5: `graph_query` node — existence check + `GRAPH_QUERY` citation

**Files:**
- Create: `src/codeoracle/agent/nodes/graph_query.py`
- Test: `tests/unit/agent/test_graph_query_node.py`

`graph_query` does two things. First, a **control-flow existence check**: a parameterised `execute_cypher` against `(:Symbol {fqn, snapshot_sha})` — the one schema fact stable across Plans 5/6. No rows → the terminal UNKNOWN verdict (the exact §8.7 reason string) and short-circuit. Second, when the symbol exists, it builds a `GRAPH_QUERY` **citation**: a `cypher://` handle whose literal query lists the symbol's callers, resolved through `HandleResolverService` for its excerpt.

Two distinct query paths, deliberately: the existence check is *parameterised* (a control query, never a citation, so no injection concern); the citation query is *literal* (the `cypher://` resolver re-runs the embedded string verbatim — invariant 3 is why `intent` already validated the FQN, and invariant 2 is why it carries an `ORDER BY`, with a bounding `LIMIT` per NFR D3-3).

- [ ] **Step 1: Pre-flight import check**

```bash
uv run python -c "
from codeoracle.db.repositories.graph import GraphRepository
from codeoracle.models import EvidenceHandle, HandleType, Verdict, VerdictStatus
import inspect
print('execute_cypher' in dir(GraphRepository))
print(inspect.signature(GraphRepository.execute_cypher))
"
```

Expected: `True` and a signature accepting a statement plus a params mapping. Confirm `EvidenceHandle.locator` accepts a `cypher://` string (its validator requires the substring `://`).

- [ ] **Step 2: Write the `graph_query` node test**

Create `tests/unit/agent/test_graph_query_node.py`:

```python
"""Plan 10 Task 5 — the graph_query node: existence check + GRAPH_QUERY citation."""

from codeoracle.agent.nodes.graph_query import build_callers_cypher, graph_query_node
from codeoracle.models import HandleType, Query, VerdictStatus


async def test_graph_query_emits_citation_when_symbol_found(
    make_fake_deps, make_agent_state
) -> None:
    deps = make_fake_deps(symbol_exists=True, resolve_content="CALLERS")
    update = await graph_query_node(make_agent_state(snapshot_sha="abc1234"), deps)
    assert update["symbol_found"] is True
    citations = update["graph_citations"]
    assert len(citations) == 1  # type: ignore[arg-type]
    assert citations[0].handle.type == HandleType.GRAPH_QUERY  # type: ignore[index]
    assert citations[0].handle.locator.startswith("cypher://")  # type: ignore[index]
    # invariant 1 — the excerpt is the resolver's output verbatim
    assert citations[0].excerpt.startswith("CALLERS::cypher://")  # type: ignore[index]


async def test_graph_query_sets_unknown_verdict_when_symbol_missing(
    make_fake_deps, make_agent_state
) -> None:
    deps = make_fake_deps(symbol_exists=False)
    update = await graph_query_node(make_agent_state(snapshot_sha="abc1234"), deps)
    assert update["symbol_found"] is False
    verdict = update["verdict"]
    assert verdict.status == VerdictStatus.UNKNOWN  # type: ignore[union-attr]
    # §8.7 ceremony point 4 — the exact reason-string format
    assert verdict.reason == "symbol not found in graph at snapshot abc1234"  # type: ignore[union-attr]
    assert "graph_citations" not in update  # nothing retrieved on the UNKNOWN path


def test_callers_cypher_is_deterministically_ordered() -> None:
    """invariant 2 — every cypher:// query the agent builds is deterministically
    ordered, and (NFR D3-3) bounded so a high-fan-in symbol cannot produce an
    unbounded excerpt."""
    cypher = build_callers_cypher("codeoracle.x.Y.z", "abc1234")
    assert "ORDER BY result" in cypher  # invariant 2 — stable re-run order
    assert cypher.rstrip().endswith("LIMIT 50")  # NFR D3-3 — bounded fan-out


def test_callers_cypher_interpolates_fqn_and_sha() -> None:
    cypher = build_callers_cypher("codeoracle.x.Y.z", "abc1234")
    assert "'codeoracle.x.Y.z'" in cypher
    assert "'abc1234'" in cypher
    # §3.7 topology — Symbol→Symbol :CALLS, with the caller resolved to its
    # File via :DEFINES; the impossible File→Symbol :CALLS and the out-of-scope
    # :USES / :IMPORTS edge tokens are gone (D-5.10/G15, Option B).
    assert ":CALLS]->(s:Symbol" in cypher
    assert "(f:File" in cypher and ":DEFINES]->(caller)" in cypher
    assert ":USES" not in cypher and ":IMPORTS" not in cypher


async def test_graph_query_existence_check_is_parameterised(
    make_fake_deps, make_agent_state
) -> None:
    """The control-flow existence query uses bound params — never interpolation."""
    deps = make_fake_deps(symbol_exists=True)
    state = make_agent_state(query=Query(symbol="codeoracle.x.y"), snapshot_sha="abc1234")
    await graph_query_node(state, deps)
    stmt, params = deps.graph_repo.calls[0]  # type: ignore[attr-defined]
    assert "$fqn" in stmt and "$sha" in stmt
    assert params == {"fqn": "codeoracle.x.y", "sha": "abc1234"}
```

- [ ] **Step 3: Run the test to verify it fails**

Run: `uv run pytest tests/unit/agent/test_graph_query_node.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'codeoracle.agent.nodes.graph_query'`.

- [ ] **Step 4: Write `agent/nodes/graph_query.py`**

Create `src/codeoracle/agent/nodes/graph_query.py`:

```python
"""The graph_query node — symbol existence check + GRAPH_QUERY citation."""

from codeoracle.agent.dependencies import AgentDependencies
from codeoracle.agent.runtime import (
    SPAN_GRAPH_QUERY,
    NodeUpdate,
    agent_node,
    resolve_to_citation,
)
from codeoracle.agent.state import AgentState
from codeoracle.models import EvidenceHandle, HandleType, Verdict, VerdictStatus

# Existence check — parameterised, run directly. A control query, never a
# citation, so bound parameters are correct and there is no injection concern.
# ``(:Symbol {fqn, snapshot_sha})`` is the one graph-schema fact stable across
# Plans 5 and 6. The btree property indexes that keep this an Index Scan rather
# than a per-query Seq Scan over the whole Symbol label (Symbol.fqn /
# Symbol.snapshot_sha) are created in Plan 3 ``bootstrap_db.sql`` — NFR D3-1.
_EXISTS_CYPHER = (
    "MATCH (s:Symbol {fqn: $fqn, snapshot_sha: $sha}) RETURN s.fqn AS fqn LIMIT 1"
)

# Callers-query fan-out bound (NFR D3-3). A high-fan-in symbol — a logger, a
# base class, a shared util — can be called from thousands of files; an
# unbounded callers result becomes one multi-KB GRAPH_QUERY excerpt that blows
# the compose-prompt budget and is fragile to byte-for-byte re-match at
# verification. 50 is a deliberate, tunable cap (cf. KNN_K); changing it is a
# contract change pinned by test_callers_cypher_is_deterministically_ordered.
_CALLERS_LIMIT = 50


def build_callers_cypher(fqn: str, sha: str) -> str:
    """Build the literal callers query embedded in the GRAPH_QUERY locator.

    ``fqn`` and ``sha`` are interpolated *literally* — Plan 8's ``cypher://``
    resolver re-runs the string verbatim with no parameters. The caller MUST
    have validated ``fqn`` with ``is_safe_symbol_fqn`` first (invariant 3);
    ``intent_node`` does.

    The query carries a deterministic ``ORDER BY result`` so the stringified
    rows are row-order-stable between retrieval and verification (invariant 2)
    — without it AGE/openCypher may return rows in a different order on the
    re-run and the citation would be spuriously REFUTED.

    It is then bounded by ``LIMIT _CALLERS_LIMIT`` (NFR D3-3). The bound is
    verification-safe because it lives *inside* the locator string: the
    verifier re-runs the identical bounded query, and the deterministic order
    makes the first ``_CALLERS_LIMIT`` rows the same set on both runs, so the
    excerpt byte-matches. The excerpt is a bounded view, honest by
    construction — every path shown is a real caller; exhaustiveness is never
    asserted.
    """
    # File path accessor is `file_path` (the parser's per-file partition key,
    # shared with replace_file_subgraph's DELETE) — File has no bare `path`
    # property; `AS result` is only the output-column alias.
    return (
        f"MATCH (caller:Symbol {{snapshot_sha: '{sha}'}})"
        f"-[:CALLS]->(s:Symbol {{fqn: '{fqn}', snapshot_sha: '{sha}'}}) "
        f"MATCH (f:File {{snapshot_sha: '{sha}'}})-[:DEFINES]->(caller) "
        f"RETURN DISTINCT f.file_path AS result "
        f"ORDER BY result "
        f"LIMIT {_CALLERS_LIMIT}"
    )


@agent_node(SPAN_GRAPH_QUERY)
async def graph_query_node(state: AgentState, deps: AgentDependencies) -> NodeUpdate:
    """Check the symbol exists in the graph; if so, emit a GRAPH_QUERY citation.

    Symbol absent → set the terminal UNKNOWN verdict (the §8.7 reason string)
    and short-circuit: every downstream node is skipped by ``@agent_node``.
    Symbol present → build a ``cypher://`` handle for the callers query and
    resolve it; the resolved, stringified result is the citation's excerpt
    (invariant 1).
    """
    fqn = state["query"].symbol
    sha = state["snapshot_sha"]

    rows = await deps.graph_repo.execute_cypher(_EXISTS_CYPHER, {"fqn": fqn, "sha": sha})
    if not rows:
        return {
            "symbol_found": False,
            "verdict": Verdict(
                status=VerdictStatus.UNKNOWN,
                reason=f"symbol not found in graph at snapshot {sha}",
                resolved_citations=[],
            ),
        }

    handle = EvidenceHandle(
        type=HandleType.GRAPH_QUERY,
        locator=f"cypher://{build_callers_cypher(fqn, sha)}",
        snapshot_sha=sha,
    )
    citation = await resolve_to_citation(handle, deps.resolver)
    return {"symbol_found": True, "graph_citations": [citation]}
```

- [ ] **Step 5: Run the test to verify it passes**

Run: `uv run pytest tests/unit/agent/test_graph_query_node.py -v`
Expected: PASS (5 tests).

- [ ] **Step 6: Lint and type-check**

```bash
uv run ruff check src/codeoracle/agent/ tests/unit/agent/
uv run ruff format src/codeoracle/agent/ tests/unit/agent/
uv run ty check src/codeoracle/agent/
```

Expected: all clean.

- [ ] **Step 7: Commit**

```bash
git add src/codeoracle/agent/nodes/graph_query.py tests/unit/agent/test_graph_query_node.py
git commit -m "feat(plan-10): Task 5 — graph_query node (existence check + GRAPH_QUERY citation)"
```

---

## Task 6: `embed_query` node — knn retrieval + `DOC_PARAGRAPH` / `CODE_SPAN` citations

**Files:**
- Create: `src/codeoracle/agent/nodes/embed_query.py`
- Test: `tests/unit/agent/test_embed_query_node.py`

`embed_query` embeds the symbol FQN, knn-searches the snapshot's chunk embeddings, and turns each hit into a resolved citation — `HandleType.DOC_PARAGRAPH` for doc paths (`.md` / `.rst` / `.adoc` / `.markdown`, classified by Plan 6's `is_doc_path`) and `HandleType.CODE_SPAN` for everything else, so the citation kind matches the chunker family that wrote the row at index time (Plan 6 Architectural Invariant 5). This is also how the symbol's *own definition* reaches the evidence set — by embedding proximity to the FQN string, not a precise FQN→line-range query (Deviation 6). Each hit's `file://path:start-end` handle is resolved so its excerpt is platform-owned (invariant 1).

This node never short-circuits and never sets a verdict: zero hits simply yields an empty `embed_citations` list (the `graph_query` node has already contributed a citation when the symbol was found, so `compose` still has candidates).

- [ ] **Step 1: Pre-flight import check**

```bash
uv run python -c "
from codeoracle.adapters.llm.embeddings import EmbeddingsProvider
from codeoracle.db.repositories.embeddings import EmbeddingsRepository
from codeoracle.models import EmbeddingHit
from codeoracle.services.embedding.chunker import is_doc_path
import inspect
h = EmbeddingHit(snapshot_sha='abc1234', source_path='src/x.py', chunk_start_line=1, chunk_end_line=9, content='c', distance=0.1)
print(h.source_path, h.chunk_start_line, h.chunk_end_line)
print('doc:', is_doc_path('docs/intro.md'), 'code:', is_doc_path('src/x.py'))
print('knn signature:', inspect.signature(EmbeddingsRepository.knn))
"
```

Expected:
- `src/x.py 1 9` — confirms `EmbeddingHit`'s field names.
- `doc: True code: False` — confirms the cross-plan `is_doc_path` predicate is importable and classifies `.md` vs `.py` correctly.
- `knn signature: (self, *, query_embedding: list[float], snapshot_sha: str, k: int)` — confirms the keyword-only contract Plan 6 ships, so the call site below MUST pass each parameter by keyword.

If any check fails, `grep -rn "class EmbeddingHit\|^def is_doc_path\|^def knn\|async def knn" src/codeoracle/` and adjust this task's code to match what Plan 6 actually shipped.

- [ ] **Step 2: Write the `embed_query` node test**

Create `tests/unit/agent/test_embed_query_node.py`:

```python
"""Plan 10 Task 6 — the embed_query node: knn retrieval → DOC_PARAGRAPH / CODE_SPAN citations."""

import pytest

from codeoracle.agent.nodes.embed_query import KNN_K, embed_query_node
from codeoracle.models import EmbeddingHit, HandleType


async def test_embed_query_builds_code_span_citations(make_fake_deps, make_agent_state) -> None:
    deps = make_fake_deps(resolve_content="SRC")
    update = await embed_query_node(make_agent_state(snapshot_sha="abc1234"), deps)
    citations = update["embed_citations"]
    assert len(citations) == 3  # the default fake supplies 3 hits (all .py paths)  # type: ignore[arg-type]
    for c in citations:  # type: ignore[union-attr]
        assert c.handle.type == HandleType.CODE_SPAN
        assert c.handle.locator.startswith("file://")
        assert c.excerpt.startswith("SRC::file://")  # invariant 1


async def test_embed_query_locator_encodes_the_hit_line_range(
    make_fake_deps, make_agent_state
) -> None:
    hit = EmbeddingHit(
        snapshot_sha="abc1234",
        source_path="src/codeoracle/services/verifier/service.py",
        chunk_start_line=42,
        chunk_end_line=58,
        content="...",
        distance=0.05,
    )
    deps = make_fake_deps(knn_hits=[hit])
    update = await embed_query_node(make_agent_state(snapshot_sha="abc1234"), deps)
    locator = update["embed_citations"][0].handle.locator  # type: ignore[index]
    assert locator == "file://src/codeoracle/services/verifier/service.py:42-58"


async def test_embed_query_locator_round_trips_through_plan8_parser(
    make_fake_deps, make_agent_state
) -> None:
    """The ``file://`` locator ``embed_query`` builds from an ``EmbeddingHit``
    is parsed back by Plan 8's REAL ``parse_file_locator`` into the exact
    ``source_path`` + line range the hit carried (D-10.U9).

    This pins the cross-plan locator-format contract end-to-end: Plan 6 WRITES
    ``EmbeddingHit.source_path`` as a repo-root-relative POSIX path (the embed
    worker sets ``source_path = message.file_path``, a path relative to
    ``repo_root``), ``embed_query`` (the READER) interpolates
    it into ``file://{source_path}:{start}-{end}``, and Plan 8's
    ``CodeSpanResolver`` / ``DocParagraphResolver`` resolve it via the shared
    ``parse_file_locator``. Driving the parser with the locator ``embed_query``
    *actually emits* (not a hand-built literal) is what catches format drift —
    a relative-vs-absolute change, a stray snapshot prefix, or a separator swap
    would make the round-trip lose the path or the range and silently miss
    CODE_SPAN resolution at query time.

    The ``+wt<12hex>`` dirty-tree suffix lives on ``snapshot_sha`` (stripped at
    the resolver layer by ``head_sha_only``, Plan 8 Invariant 10), never on the
    locator path — so it is correctly out of scope for this path-format contract.
    """
    from codeoracle.services.handle_resolution.strategies.base import parse_file_locator

    hit = EmbeddingHit(
        snapshot_sha="abc1234",
        source_path="src/codeoracle/services/verifier/service.py",
        chunk_start_line=42,
        chunk_end_line=58,
        content="...",
        distance=0.05,
    )
    deps = make_fake_deps(knn_hits=[hit])
    update = await embed_query_node(make_agent_state(snapshot_sha="abc1234"), deps)
    locator = update["embed_citations"][0].handle.locator  # type: ignore[index]

    path, (start, end) = parse_file_locator(locator)
    # Plan 8 preserves the path verbatim (no normalization) and the range is
    # 1-indexed inclusive — both sides must agree on the relative-POSIX form.
    assert path == hit.source_path
    assert (start, end) == (hit.chunk_start_line, hit.chunk_end_line)


async def test_embed_query_caps_results_at_knn_k(make_fake_deps, make_agent_state) -> None:
    many = [
        EmbeddingHit(
            snapshot_sha="abc1234",
            source_path=f"src/f{i}.py",
            chunk_start_line=1,
            chunk_end_line=9,
            content="x",
            distance=0.1 * i,
        )
        for i in range(KNN_K + 6)
    ]
    deps = make_fake_deps(knn_hits=many)
    update = await embed_query_node(make_agent_state(snapshot_sha="abc1234"), deps)
    assert len(update["embed_citations"]) == KNN_K  # type: ignore[arg-type]


async def test_embed_query_handles_zero_hits(make_fake_deps, make_agent_state) -> None:
    deps = make_fake_deps(knn_hits=[])
    update = await embed_query_node(make_agent_state(snapshot_sha="abc1234"), deps)
    assert update["embed_citations"] == []


# --- DOC_PARAGRAPH / CODE_SPAN routing (Plan 6 `is_doc_path`) ----------------


@pytest.mark.parametrize(
    "source_path,expected_type",
    [
        ("docs/architecture.md", HandleType.DOC_PARAGRAPH),
        ("docs/specs/intro.rst", HandleType.DOC_PARAGRAPH),
        ("api.adoc", HandleType.DOC_PARAGRAPH),
        ("README.markdown", HandleType.DOC_PARAGRAPH),
        ("CHANGELOG.MD", HandleType.DOC_PARAGRAPH),  # case-insensitive
        ("src/codeoracle/agent/nodes/embed_query.py", HandleType.CODE_SPAN),
        ("types.pyi", HandleType.CODE_SPAN),
        ("notes.txt", HandleType.CODE_SPAN),  # unknown extension → conservative CODE_SPAN
        ("LICENSE", HandleType.CODE_SPAN),    # no extension → CODE_SPAN
        ("Dockerfile", HandleType.CODE_SPAN),
    ],
)
async def test_embed_query_routes_handle_type_by_source_path_extension(
    make_fake_deps, make_agent_state, source_path: str, expected_type: HandleType
) -> None:
    """`embed_query` routes each EmbeddingHit to DOC_PARAGRAPH (doc extension)
    or CODE_SPAN (everything else) via Plan 6's `is_doc_path`. This pins the
    cross-plan routing contract — every chunk written by ParagraphChunker
    must cite as DOC_PARAGRAPH and every chunk written by LineChunker must
    cite as CODE_SPAN (Plan 6 Architectural Invariant 5)."""
    hit = EmbeddingHit(
        snapshot_sha="abc1234",
        source_path=source_path,
        chunk_start_line=1,
        chunk_end_line=12,
        content="...",
        distance=0.1,
    )
    deps = make_fake_deps(knn_hits=[hit])
    update = await embed_query_node(make_agent_state(snapshot_sha="abc1234"), deps)
    citation = update["embed_citations"][0]  # type: ignore[index]
    assert citation.handle.type is expected_type
    # Locator shape is identical for both kinds (Plan 8: same parse_file_locator).
    assert citation.handle.locator == f"file://{source_path}:1-12"


async def test_embed_query_emits_both_handle_types_in_one_batch(
    make_fake_deps, make_agent_state
) -> None:
    """A mixed-extension knn result yields a citation list with BOTH
    DOC_PARAGRAPH and CODE_SPAN entries, in the same order as the hits.
    Catches a regression that would route every hit to one kind regardless
    of source_path (e.g., an accidental hard-coded CODE_SPAN)."""
    hits = [
        EmbeddingHit(
            snapshot_sha="abc1234",
            source_path="docs/architecture.md",
            chunk_start_line=1, chunk_end_line=5, content="md1", distance=0.10,
        ),
        EmbeddingHit(
            snapshot_sha="abc1234",
            source_path="src/codeoracle/agent/runtime.py",
            chunk_start_line=10, chunk_end_line=25, content="py1", distance=0.15,
        ),
        EmbeddingHit(
            snapshot_sha="abc1234",
            source_path="docs/specs/scaffolding.rst",
            chunk_start_line=1, chunk_end_line=8, content="rst1", distance=0.20,
        ),
        EmbeddingHit(
            snapshot_sha="abc1234",
            source_path="src/codeoracle/services/verifier/service.py",
            chunk_start_line=40, chunk_end_line=58, content="py2", distance=0.25,
        ),
    ]
    deps = make_fake_deps(knn_hits=hits)
    update = await embed_query_node(make_agent_state(snapshot_sha="abc1234"), deps)
    citations = update["embed_citations"]  # type: ignore[index]
    assert [c.handle.type for c in citations] == [
        HandleType.DOC_PARAGRAPH,
        HandleType.CODE_SPAN,
        HandleType.DOC_PARAGRAPH,
        HandleType.CODE_SPAN,
    ]
```

- [ ] **Step 3: Run the test to verify it fails**

Run: `uv run pytest tests/unit/agent/test_embed_query_node.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'codeoracle.agent.nodes.embed_query'`.

- [ ] **Step 4: Write `agent/nodes/embed_query.py`**

Create `src/codeoracle/agent/nodes/embed_query.py`:

```python
"""The embed_query node — knn retrieval → DOC_PARAGRAPH / CODE_SPAN citations."""

from codeoracle.agent.dependencies import AgentDependencies
from codeoracle.agent.runtime import (
    SPAN_EMBED_QUERY,
    NodeUpdate,
    agent_node,
    resolve_to_citation,
)
from codeoracle.agent.state import AgentState
from codeoracle.models import DraftCitation, EvidenceHandle, HandleType
from codeoracle.services.embedding.chunker import is_doc_path

# Number of nearest chunks to retrieve. Small enough to keep the compose prompt
# inside the LLM context budget, large enough to surface the symbol's own
# definition plus its immediate neighbours. Spec §4.2's *original* sketch wrote
# k=5; it was reconciled to k=4 in #44, so spec and slice now agree (Deviation
# 10). KNN_K stays independently tunable for the prompt budget — a slice-tuning
# call for prompt size.
KNN_K = 4


@agent_node(SPAN_EMBED_QUERY)
async def embed_query_node(state: AgentState, deps: AgentDependencies) -> NodeUpdate:
    """Embed the symbol FQN, knn-search the snapshot's chunk embeddings, and
    turn each hit into a resolved citation — DOC_PARAGRAPH for doc paths,
    CODE_SPAN for everything else.

    Routing reuses Plan 6's `is_doc_path` predicate, which consults the same
    ``_DOC_EXTENSIONS`` set ``chunker_for`` used at index time, so a chunk
    written by ``ParagraphChunker`` is cited as DOC_PARAGRAPH and a chunk
    written by ``LineChunker`` is cited as CODE_SPAN — no drift between
    "what got chunked as a doc" and "what gets cited as a doc."

    The symbol's own definition is located by embedding proximity to the FQN
    string (Deviation 6). Each hit becomes a ``file://path:start-end`` handle
    that is resolved so the excerpt is platform-owned (invariant 1). Both
    DOC_PARAGRAPH and CODE_SPAN locators use the same ``file://`` shape
    (Plan 8: ``DocParagraphResolver`` and ``CodeSpanResolver`` share the
    ``parse_file_locator`` parser — the handle type differentiates trust
    score, not the wire path). Zero hits yields an empty list — not a failure.
    """
    sha = state["snapshot_sha"]
    vectors = await deps.embeddings.embed([state["query"].symbol])
    hits = await deps.embeddings_repo.knn(
        query_embedding=vectors[0],
        snapshot_sha=sha,
        k=KNN_K,
    )

    citations: list[DraftCitation] = []
    for hit in hits:
        handle_type = (
            HandleType.DOC_PARAGRAPH if is_doc_path(hit.source_path) else HandleType.CODE_SPAN
        )
        handle = EvidenceHandle(
            type=handle_type,
            locator=f"file://{hit.source_path}:{hit.chunk_start_line}-{hit.chunk_end_line}",
            snapshot_sha=sha,
        )
        citations.append(await resolve_to_citation(handle, deps.resolver))
    return {"embed_citations": citations}
```

- [ ] **Step 5: Run the test to verify it passes**

Run: `uv run pytest tests/unit/agent/test_embed_query_node.py -v`
Expected: PASS (16 tests — 5 original (incl. the Plan 8 locator round-trip, D-10.U9) + 10 parametrized routing cases + 1 mixed-batch test).

- [ ] **Step 6: Lint and type-check**

```bash
uv run ruff check src/codeoracle/agent/ tests/unit/agent/
uv run ruff format src/codeoracle/agent/ tests/unit/agent/
uv run ty check src/codeoracle/agent/
```

Expected: all clean.

- [ ] **Step 7: Commit**

```bash
git add src/codeoracle/agent/nodes/embed_query.py tests/unit/agent/test_embed_query_node.py
git commit -m "feat(plan-10): Task 6 — embed_query node (knn retrieval + CODE_SPAN citations)"
```

---

## Task 7: `compose` node — LLM evidence selection + `DraftClaim` assembly

**Files:**
- Create: `src/codeoracle/agent/nodes/compose.py`
- Test: `tests/unit/agent/test_compose_node.py`

This is the only node that calls the LLM, and the only node where the design has real subtlety. The LLM authors the explanation `statement`, a calibrated `confidence`, and *selects* supporting / contradicting evidence **by index** — it never authors a citation excerpt (invariant 1). `compose_node` builds the candidate list, calls the LLM with the `submit_explanation` tool, parses the tool call, and assembles the `DraftClaim` entirely from the platform-owned candidate citations.

Three design decisions, each load-bearing:

- **Why selection-by-index, not LLM-authored citations.** If the LLM authored excerpt text it would paraphrase; `VerifierService` re-resolves each handle and compares — a paraphrase mismatches → REFUTED on an honest query. By having the LLM pick indices into platform-resolved candidates, every excerpt is verbatim and the PROVEN path is structural.
- **Why a flat, hand-authored tool schema.** `DraftClaim.model_json_schema()` emits nested `$defs` (for `DraftCitation`, `EvidenceHandle`) that not every provider's tool-calling accepts cleanly. The `submit_explanation` schema is four scalar/array fields — no `$defs`, provider-portable, and the LLM never has to construct a citation object.
- **Why candidate order is fixed.** The tool call references candidates by index, so `graph_citations` then `embed_citations` is a pinned ordering — index 0 is always the `GRAPH_QUERY` citation.

- [ ] **Step 1: Pre-flight import check**

```bash
uv run python -c "
from codeoracle.models.llm import ChatMessage, ChatResponse, ToolCall, ToolDefinition
m = ChatMessage(role='system', content='x', template_id='t')
print(m.template_id)
tc = ToolCall(id='c1', name='submit_explanation', arguments_json='{}')
print(tc.arguments_json)
import inspect
from codeoracle.adapters.llm.interfaces import LLMProvider
print(inspect.signature(LLMProvider.chat))
"
```

Expected: `t`, `{}`, and a `chat` signature `(self, messages, *, tools=None, structured_output=None)`. Confirms `ChatMessage.template_id`, `ToolCall.arguments_json`, and that `chat` takes keyword-only `tools`.

- [ ] **Step 2: Write the `compose` node test**

Create `tests/unit/agent/test_compose_node.py`:

```python
"""Plan 10 Task 7 — the compose node: LLM evidence selection + DraftClaim assembly."""

import json

import pytest

from codeoracle.agent.nodes.compose import (
    COMPOSE_SYSTEM_TEMPLATE_ID,
    COMPOSE_USER_TEMPLATE_ID,
    SUBMIT_EXPLANATION_TOOL,
    build_compose_messages,
    compose_node,
    parse_submit_explanation,
)
from codeoracle.core.exceptions import AgentError
from codeoracle.models import DraftCitation, EvidenceHandle, HandleType
from codeoracle.models.llm import ChatResponse, TokenUsage, ToolCall


def _response(
    arguments_json: str,
    *,
    tool_name: str = "submit_explanation",
    tool_calls: list[ToolCall] | None = None,
) -> ChatResponse:
    calls = (
        tool_calls
        if tool_calls is not None
        else [ToolCall(id="c1", name=tool_name, arguments_json=arguments_json)]
    )
    return ChatResponse(
        content="",
        usage=TokenUsage(input_tokens=1, output_tokens=1),
        finish_reason="tool_calls",
        tool_calls=calls,
    )


def _args(
    citation_indices: list[int] | None = None,
    contradiction_indices: list[int] | None = None,
    *,
    statement: str = "It does X.",
    confidence: float = 0.9,
) -> str:
    return json.dumps(
        {
            "statement": statement,
            "confidence": confidence,
            "citation_indices": citation_indices if citation_indices is not None else [0, 1],
            "contradiction_indices": contradiction_indices or [],
        }
    )


def _citation(handle_type: HandleType, locator: str) -> DraftCitation:
    return DraftCitation(
        handle=EvidenceHandle(type=handle_type, locator=locator, snapshot_sha="abc1234"),
        excerpt="platform-owned excerpt",
    )


# --- the submit_explanation tool schema ---

def test_submit_explanation_tool_schema_is_flat() -> None:
    schema = SUBMIT_EXPLANATION_TOOL.parameters_schema
    assert "$defs" not in schema  # flat, hand-authored — no nested Pydantic refs
    assert set(schema["properties"]) == {  # type: ignore[index,arg-type]
        "statement",
        "confidence",
        "citation_indices",
        "contradiction_indices",
    }
    assert SUBMIT_EXPLANATION_TOOL.name == "submit_explanation"


# --- build_compose_messages ---

def test_compose_messages_carry_template_ids() -> None:
    """invariant 5 — every ChatMessage the agent builds has a non-None template_id."""
    msgs = build_compose_messages(
        "codeoracle.x.y", [_citation(HandleType.CODE_SPAN, "file://a.py:1-2")]
    )
    assert [m.template_id for m in msgs] == [
        COMPOSE_SYSTEM_TEMPLATE_ID,
        COMPOSE_USER_TEMPLATE_ID,
    ]
    assert all(m.template_id is not None for m in msgs)


def test_compose_messages_index_every_candidate() -> None:
    candidates = [
        _citation(HandleType.GRAPH_QUERY, "cypher://MATCH (n) RETURN n"),
        _citation(HandleType.CODE_SPAN, "file://a.py:1-2"),
    ]
    user = build_compose_messages("codeoracle.x.y", candidates)[1].content
    assert "[0]" in user and "[1]" in user
    assert "codeoracle.x.y" in user


# --- parse_submit_explanation ---

def test_parse_accepts_a_well_formed_tool_call() -> None:
    statement, confidence, cites, contras = parse_submit_explanation(
        _response(_args(citation_indices=[0, 2], contradiction_indices=[1])),
        candidate_count=3,
    )
    assert statement == "It does X."
    assert confidence == 0.9
    assert cites == [0, 2]
    assert contras == [1]


def test_parse_dedupes_indices_preserving_order() -> None:
    _, _, cites, _ = parse_submit_explanation(
        _response(_args(citation_indices=[2, 0, 2, 0])), candidate_count=3
    )
    assert cites == [2, 0]


def test_parse_rejects_missing_tool_call() -> None:
    with pytest.raises(AgentError, match="did not call submit_explanation"):
        parse_submit_explanation(_response("", tool_calls=[]), candidate_count=3)


def test_parse_rejects_wrong_tool_name() -> None:
    with pytest.raises(AgentError, match="unexpected tool"):
        parse_submit_explanation(
            _response(_args(), tool_name="something_else"), candidate_count=3
        )


def test_parse_rejects_invalid_json() -> None:
    with pytest.raises(AgentError, match="not valid JSON"):
        parse_submit_explanation(_response("{not json"), candidate_count=3)


def test_parse_rejects_empty_statement() -> None:
    with pytest.raises(AgentError, match="non-empty 'statement'"):
        parse_submit_explanation(_response(_args(statement="   ")), candidate_count=3)


def test_parse_rejects_confidence_out_of_range() -> None:
    with pytest.raises(AgentError, match="outside"):
        parse_submit_explanation(_response(_args(confidence=1.5)), candidate_count=3)


def test_parse_rejects_out_of_range_index() -> None:
    with pytest.raises(AgentError, match="out-of-range"):
        parse_submit_explanation(
            _response(_args(citation_indices=[0, 9])), candidate_count=3
        )


def test_parse_rejects_empty_citation_indices() -> None:
    with pytest.raises(AgentError, match="at least one"):
        parse_submit_explanation(
            _response(_args(citation_indices=[])), candidate_count=3
        )


# --- compose_node ---

async def test_compose_assembles_draft_claim_from_candidates(
    make_fake_deps, make_agent_state
) -> None:
    gc = _citation(HandleType.GRAPH_QUERY, "cypher://MATCH (n) RETURN n")
    ec = _citation(HandleType.CODE_SPAN, "file://a.py:1-9")
    deps = make_fake_deps(llm_tool_args=_args(citation_indices=[0, 1]))
    state = make_agent_state(graph_citations=[gc], embed_citations=[ec])
    update = await compose_node(state, deps)
    draft = update["draft_claim"]
    assert draft.statement == "It does X."  # type: ignore[union-attr]
    assert len(draft.citations) == 2  # type: ignore[union-attr]
    # invariant 1 — every excerpt is platform-owned, never LLM-authored
    assert all(c.excerpt == "platform-owned excerpt" for c in draft.citations)  # type: ignore[union-attr]


async def test_compose_candidate_order_is_graph_then_embed(
    make_fake_deps, make_agent_state
) -> None:
    gc = _citation(HandleType.GRAPH_QUERY, "cypher://MATCH (n) RETURN n")
    ec = _citation(HandleType.CODE_SPAN, "file://e.py:1-9")
    deps = make_fake_deps(llm_tool_args=_args(citation_indices=[0]))
    state = make_agent_state(graph_citations=[gc], embed_citations=[ec])
    update = await compose_node(state, deps)
    # index 0 is always the graph citation
    assert update["draft_claim"].citations[0].handle.type == HandleType.GRAPH_QUERY  # type: ignore[union-attr]


async def test_compose_fails_when_llm_makes_no_tool_call(
    make_fake_deps, make_agent_state
) -> None:
    no_call = ChatResponse(
        content="cannot help",
        usage=TokenUsage(input_tokens=1, output_tokens=1),
        finish_reason="stop",
        tool_calls=[],
    )
    deps = make_fake_deps(llm_response=no_call)
    state = make_agent_state(
        graph_citations=[_citation(HandleType.GRAPH_QUERY, "cypher://MATCH (n) RETURN n")]
    )
    update = await compose_node(state, deps)
    assert "did not call submit_explanation" in str(update["error"])


async def test_compose_fails_when_no_candidates(make_fake_deps, make_agent_state) -> None:
    deps = make_fake_deps()
    state = make_agent_state(graph_citations=[], embed_citations=[])
    update = await compose_node(state, deps)
    assert "no evidence candidates" in str(update["error"])
```

- [ ] **Step 3: Run the test to verify it fails**

Run: `uv run pytest tests/unit/agent/test_compose_node.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'codeoracle.agent.nodes.compose'`.

- [ ] **Step 4: Write `agent/nodes/compose.py`**

Create `src/codeoracle/agent/nodes/compose.py`:

```python
"""The compose node — LLM evidence selection and DraftClaim assembly.

The LLM authors the explanation prose and *selects* which retrieved evidence
supports or contradicts it; it never authors citation excerpts (invariant 1).
``compose_node`` builds the candidate list, asks the LLM to call
``submit_explanation`` with a statement, a confidence, and evidence indices,
then assembles the ``DraftClaim`` from the platform-owned candidates.
"""

import json

from codeoracle.agent.dependencies import AgentDependencies
from codeoracle.agent.runtime import SPAN_COMPOSE, NodeUpdate, agent_node
from codeoracle.agent.state import AgentState
from codeoracle.core.exceptions import AgentError
from codeoracle.models import DraftCitation, DraftClaim
from codeoracle.models.llm import ChatMessage, ChatResponse, ToolDefinition

# Template ids — one per role. Plan 4's request fingerprint hashes template ids,
# not raw bytes, so a cosmetic prompt edit must not shatter the fingerprint
# (invariant 5). The user message's content varies per query; its template_id
# does not — that is the point.
COMPOSE_SYSTEM_TEMPLATE_ID = "agent.compose.system.v1"
COMPOSE_USER_TEMPLATE_ID = "agent.compose.user.v1"

_SYSTEM_PROMPT = """You are CodeOracle's explanation composer.

You are given a symbol and a numbered list of evidence items already retrieved \
and verified by the platform. Your job:

1. Write a precise, self-contained `statement` explaining what the symbol is \
and what the evidence shows about it. Describe only what the evidence \
supports — never speculate beyond it.
2. Give a calibrated `confidence` in [0, 1]: how fully the cited evidence \
supports your statement.
3. Select, by index, the evidence items that SUPPORT the statement \
(`citation_indices`, at least one) and any that CONTRADICT it \
(`contradiction_indices`).

You MUST respond by calling the `submit_explanation` tool. Do not quote or \
paraphrase evidence text into the statement — the platform owns the evidence \
excerpts; you only reference items by index."""

# A flat, hand-authored JSON-Schema — no nested `$defs`. The LLM never has to
# construct a citation object; it only writes prose and picks indices.
SUBMIT_EXPLANATION_TOOL = ToolDefinition(
    name="submit_explanation",
    description=(
        "Submit the final explanation of the symbol, citing the supporting "
        "and contradicting evidence by its list index."
    ),
    parameters_schema={
        "type": "object",
        "properties": {
            "statement": {
                "type": "string",
                "description": "A precise, self-contained explanation of the symbol.",
            },
            "confidence": {
                "type": "number",
                "minimum": 0.0,
                "maximum": 1.0,
                "description": "Calibrated confidence that the evidence supports the statement.",
            },
            "citation_indices": {
                "type": "array",
                "items": {"type": "integer"},
                "description": "Indices of evidence items that support the statement (at least one).",
            },
            "contradiction_indices": {
                "type": "array",
                "items": {"type": "integer"},
                "description": "Indices of evidence items that contradict the statement, if any.",
            },
        },
        "required": ["statement", "confidence", "citation_indices"],
    },
)


def build_compose_messages(
    symbol: str, candidates: list[DraftCitation]
) -> list[ChatMessage]:
    """Build the compose prompt: a system message and a user message listing
    every candidate citation with its index, handle type, and excerpt.

    Both messages carry a ``template_id`` (invariant 5).
    """
    lines = [f"Symbol to explain: {symbol}", "", "Evidence candidates:"]
    for index, citation in enumerate(candidates):
        lines.append(f"\n[{index}] ({citation.handle.type.value}) {citation.handle.locator}")
        lines.append(citation.excerpt)
    return [
        ChatMessage(
            role="system",
            content=_SYSTEM_PROMPT,
            template_id=COMPOSE_SYSTEM_TEMPLATE_ID,
        ),
        ChatMessage(
            role="user",
            content="\n".join(lines),
            template_id=COMPOSE_USER_TEMPLATE_ID,
        ),
    ]


def _validate_index_list(name: str, value: object, candidate_count: int) -> list[int]:
    """Validate a JSON value is a list of in-range integers; return it deduped.

    ``bool`` is rejected explicitly — ``bool`` is a subclass of ``int`` in
    Python and JSON ``true``/``false`` parse to ``bool``, but a boolean is not
    a valid evidence index.
    """
    if not isinstance(value, list):
        raise AgentError(f"tool call field {name!r} must be a list")
    indices: list[int] = []
    for item in value:
        if isinstance(item, bool) or not isinstance(item, int):
            raise AgentError(f"tool call field {name!r} must contain only integers")
        if item < 0 or item >= candidate_count:
            raise AgentError(
                f"tool call field {name!r} has out-of-range index {item} "
                f"(candidate count is {candidate_count})"
            )
        indices.append(item)
    return list(dict.fromkeys(indices))  # dedupe, preserve first-seen order


def parse_submit_explanation(
    response: ChatResponse, candidate_count: int
) -> tuple[str, float, list[int], list[int]]:
    """Parse and validate the LLM's ``submit_explanation`` tool call.

    Returns ``(statement, confidence, citation_indices, contradiction_indices)``.
    Raises ``AgentError`` on any malformed response — compose is single-shot
    (Deviation 8), so a malformed call ends the query as FAILED. A truncated
    response is caught here too: a half-written tool call fails JSON parsing.
    """
    if not response.tool_calls:
        raise AgentError("LLM did not call submit_explanation")
    call = response.tool_calls[0]
    if call.name != "submit_explanation":
        raise AgentError(f"LLM called unexpected tool {call.name!r}")
    try:
        args = json.loads(call.arguments_json)
    except json.JSONDecodeError as exc:
        raise AgentError(f"tool call arguments are not valid JSON: {exc}") from exc
    if not isinstance(args, dict):
        raise AgentError("tool call arguments are not a JSON object")

    statement = args.get("statement")
    if not isinstance(statement, str) or not statement.strip():
        raise AgentError("tool call is missing a non-empty 'statement'")

    confidence = args.get("confidence")
    if isinstance(confidence, bool) or not isinstance(confidence, (int, float)):
        raise AgentError("tool call 'confidence' must be a number")
    if not 0.0 <= float(confidence) <= 1.0:
        raise AgentError(f"tool call 'confidence' {confidence} is outside [0, 1]")

    citation_indices = _validate_index_list(
        "citation_indices", args.get("citation_indices"), candidate_count
    )
    if not citation_indices:
        raise AgentError("tool call 'citation_indices' must select at least one item")
    contradiction_indices = _validate_index_list(
        "contradiction_indices", args.get("contradiction_indices", []), candidate_count
    )
    return statement, float(confidence), citation_indices, contradiction_indices


@agent_node(SPAN_COMPOSE)
async def compose_node(state: AgentState, deps: AgentDependencies) -> NodeUpdate:
    """Ask the LLM to select evidence; assemble the DraftClaim from candidates.

    Candidate order is fixed — ``graph_citations`` then ``embed_citations`` —
    because the LLM's tool call references candidates by index. The LLM never
    authors excerpts; ``compose_node`` builds the ``DraftClaim`` entirely from
    the platform-owned candidate citations (invariant 1) and asserts every
    cited handle type is in the query plan (invariant 8).
    """
    candidates = list(state["graph_citations"]) + list(state["embed_citations"])
    if not candidates:
        raise AgentError("no evidence candidates were retrieved")

    messages = build_compose_messages(state["query"].symbol, candidates)
    response = await deps.llm.chat(messages, tools=[SUBMIT_EXPLANATION_TOOL])
    statement, confidence, cite_idx, contra_idx = parse_submit_explanation(
        response, len(candidates)
    )

    draft = DraftClaim(
        statement=statement,
        citations=[candidates[i] for i in cite_idx],
        confidence=confidence,
        contradictions=[candidates[i] for i in contra_idx],
    )

    plan = set(state["plan_handle_types"])
    for citation in draft.citations:
        if citation.handle.type not in plan:
            raise AgentError(
                f"composed citation type {citation.handle.type.value!r} is not in "
                f"the query plan {sorted(t.value for t in plan)}"
            )
    return {"draft_claim": draft}
```

- [ ] **Step 5: Run the test to verify it passes**

Run: `uv run pytest tests/unit/agent/test_compose_node.py -v`
Expected: PASS (16 tests).

- [ ] **Step 6: Lint and type-check**

```bash
uv run ruff check src/codeoracle/agent/ tests/unit/agent/
uv run ruff format src/codeoracle/agent/ tests/unit/agent/
uv run ty check src/codeoracle/agent/
```

Expected: all clean.

- [ ] **Step 7: Commit**

```bash
git add src/codeoracle/agent/nodes/compose.py tests/unit/agent/test_compose_node.py
git commit -m "feat(plan-10): Task 7 — compose node (LLM evidence selection + DraftClaim assembly)"
```

---

## Task 8: `score` + `verify` nodes

**Files:**
- Create: `src/codeoracle/agent/nodes/score.py`
- Create: `src/codeoracle/agent/nodes/verify.py`
- Test: `tests/unit/agent/test_score_node.py`, `tests/unit/agent/test_verify_node.py`

Two thin nodes — each delegates to a Plan 9 service and writes one state key. They are deliberately thin: the trust algorithm and the verification algorithm are Plan 9's, fully tested there; these nodes only adapt them to the graph.

`score` is the `DraftClaim → Claim` boundary — `TrustMatrixService.score_each` is the *only* producer of a `Claim` in non-test code, and it is **synchronous** (pure computation: trust ranks, no I/O). `score` has no §8.5 gate-9 span, so `@agent_node` is given `None`. `verify` returns `VerifierService`'s `Verdict` verbatim — invariant 7: the agent never softens a REFUTED or upgrades an UNKNOWN.

- [ ] **Step 1: Pre-flight import check**

```bash
uv run python -c "
import inspect
from codeoracle.services.trust_matrix import TrustMatrixService
from codeoracle.services.verifier import VerifierService
print('score_each:', inspect.signature(TrustMatrixService.score_each))
print('score_each async:', inspect.iscoroutinefunction(TrustMatrixService.score_each))
print('verify:', inspect.signature(VerifierService.verify))
print('verify async:', inspect.iscoroutinefunction(VerifierService.verify))
"
```

Expected: `score_each` takes `(self, draft, *, source_age_days=0)` and is **not** a coroutine (synchronous — call it without `await`); `verify` takes `(self, claim)` and **is** a coroutine (call it with `await`).

- [ ] **Step 2: Write the `score` node test**

Create `tests/unit/agent/test_score_node.py`:

```python
"""Plan 10 Task 8 — the score node: DraftClaim → Claim via the trust matrix."""

from codeoracle.agent.nodes.score import score_node
from codeoracle.models import DraftCitation, DraftClaim, EvidenceHandle, HandleType


def _draft() -> DraftClaim:
    cite = DraftCitation(
        handle=EvidenceHandle(
            type=HandleType.CODE_SPAN, locator="file://a.py:1-9", snapshot_sha="abc1234"
        ),
        excerpt="def f(): ...",
    )
    return DraftClaim(
        statement="It does X.", citations=[cite], confidence=0.8, contradictions=[]
    )


async def test_score_produces_a_claim(make_fake_deps, make_agent_state) -> None:
    deps = make_fake_deps()
    update = await score_node(make_agent_state(draft_claim=_draft()), deps)
    claim = update["claim"]
    assert claim.statement == "It does X."  # type: ignore[union-attr]
    # the trust matrix stamps each citation with a source_trust the LLM cannot forge
    assert all(c.source_trust >= 0 for c in claim.citations)  # type: ignore[union-attr]


async def test_score_errors_without_a_draft(make_fake_deps, make_agent_state) -> None:
    deps = make_fake_deps()
    update = await score_node(make_agent_state(draft_claim=None), deps)
    assert "no draft claim" in str(update["error"])
```

- [ ] **Step 3: Run the test to verify it fails**

Run: `uv run pytest tests/unit/agent/test_score_node.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'codeoracle.agent.nodes.score'`.

- [ ] **Step 4: Write `agent/nodes/score.py`**

Create `src/codeoracle/agent/nodes/score.py`:

```python
"""The score node — DraftClaim -> Claim via the trust matrix."""

from codeoracle.agent.dependencies import AgentDependencies
from codeoracle.agent.runtime import NodeUpdate, agent_node
from codeoracle.agent.state import AgentState
from codeoracle.core.exceptions import AgentError


@agent_node(None)
async def score_node(state: AgentState, deps: AgentDependencies) -> NodeUpdate:
    """Score the draft claim into a trust-weighted Claim.

    ``TrustMatrixService.score_each`` is the only DraftClaim -> Claim producer
    in non-test code — it stamps each citation with a ``source_trust`` the LLM
    cannot forge. The call is synchronous (pure computation, no I/O), so it is
    not awaited. This node has no §8.5 gate-9 span — ``@agent_node`` is given
    ``None`` — but still gets the short-circuit and exception-trapping wrapper.
    """
    draft = state["draft_claim"]
    if draft is None:
        raise AgentError("score node reached with no draft claim")
    return {"claim": deps.trust_matrix.score_each(draft)}
```

- [ ] **Step 5: Run the test to verify it passes**

Run: `uv run pytest tests/unit/agent/test_score_node.py -v`
Expected: PASS (2 tests).

- [ ] **Step 6: Write the `verify` node test**

Create `tests/unit/agent/test_verify_node.py`:

```python
"""Plan 10 Task 8 — the verify node: Claim -> Verdict via the verifier."""

from codeoracle.agent.nodes.verify import verify_node
from codeoracle.models import (
    Citation,
    Claim,
    EvidenceHandle,
    HandleType,
    Verdict,
    VerdictStatus,
)


def _claim() -> Claim:
    cite = Citation(
        handle=EvidenceHandle(
            type=HandleType.CODE_SPAN, locator="file://a.py:1-9", snapshot_sha="abc1234"
        ),
        excerpt="def f(): ...",
        source_trust=7,
    )
    return Claim(
        statement="It does X.", citations=[cite], confidence=0.8, contradictions=[]
    )


async def test_verify_returns_the_verifier_verdict_verbatim(
    make_fake_deps, make_agent_state
) -> None:
    refuted = Verdict(
        status=VerdictStatus.REFUTED, reason="excerpt mismatch", resolved_citations=[]
    )
    deps = make_fake_deps(verdict=refuted)
    update = await verify_node(make_agent_state(claim=_claim()), deps)
    # invariant 7 — the agent never softens a REFUTED verdict
    assert update["verdict"].status == VerdictStatus.REFUTED  # type: ignore[union-attr]
    assert update["verdict"].reason == "excerpt mismatch"  # type: ignore[union-attr]


async def test_verify_errors_without_a_claim(make_fake_deps, make_agent_state) -> None:
    deps = make_fake_deps()
    update = await verify_node(make_agent_state(claim=None), deps)
    assert "no scored claim" in str(update["error"])
```

- [ ] **Step 7: Run the test to verify it fails**

Run: `uv run pytest tests/unit/agent/test_verify_node.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'codeoracle.agent.nodes.verify'`.

- [ ] **Step 8: Write `agent/nodes/verify.py`**

Create `src/codeoracle/agent/nodes/verify.py`:

```python
"""The verify node — Claim -> Verdict via the verifier service."""

from codeoracle.agent.dependencies import AgentDependencies
from codeoracle.agent.runtime import SPAN_VERIFY, NodeUpdate, agent_node
from codeoracle.agent.state import AgentState
from codeoracle.core.exceptions import AgentError


@agent_node(SPAN_VERIFY)
async def verify_node(state: AgentState, deps: AgentDependencies) -> NodeUpdate:
    """Verify the scored claim, producing the terminal Verdict.

    The agent returns ``VerifierService``'s verdict verbatim (invariant 7) — it
    never softens a REFUTED or upgrades an UNKNOWN. ``VerifierService``
    re-resolves every citation and compares against its recorded excerpt;
    because the agent populated those excerpts by resolution at retrieval time
    (invariant 1), an untampered claim verifies as PROVEN.
    """
    claim = state["claim"]
    if claim is None:
        raise AgentError("verify node reached with no scored claim")
    return {"verdict": await deps.verifier.verify(claim)}
```

- [ ] **Step 9: Run the test to verify it passes**

Run: `uv run pytest tests/unit/agent/test_verify_node.py -v`
Expected: PASS (2 tests).

- [ ] **Step 10: Lint and type-check**

```bash
uv run ruff check src/codeoracle/agent/ tests/unit/agent/
uv run ruff format src/codeoracle/agent/ tests/unit/agent/
uv run ty check src/codeoracle/agent/
```

Expected: all clean.

- [ ] **Step 11: Commit**

```bash
git add src/codeoracle/agent/nodes/score.py src/codeoracle/agent/nodes/verify.py tests/unit/agent/test_score_node.py tests/unit/agent/test_verify_node.py
git commit -m "feat(plan-10): Task 8 — score + verify nodes"
```

---

## Task 9: Graph assembly + `run_agent_query`

**Files:**
- Create: `src/codeoracle/agent/graph.py`
- Modify: `src/codeoracle/agent/__init__.py` — add the public re-exports
- Test: `tests/unit/agent/test_graph.py`

This task assembles the six nodes into a LangGraph `StateGraph` and provides `run_agent_query` — the agent's entry point. `build_agent_graph` is the per-query factory: it binds `deps` into every node and compiles a linear chain. `run_agent_query` seeds the initial `AgentState`, invokes the graph, and maps the final state to one of three `QueryResult` terminals.

`deps` is bound into nodes by an explicit `async def` closure (`_with_deps`), **not** `functools.partial` — an `async def` is unambiguously a coroutine function, so LangGraph cannot mis-detect a node as synchronous. The end-to-end tests here are the agent's real proof: they run all six nodes against fully-faked collaborators.

- [ ] **Step 1: Pre-flight import check**

```bash
uv run python -c "
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
print('langgraph graph API OK')
from codeoracle.models import Query, QueryResult, QueryStatus
r = QueryResult.failed('t', 'e'); print(r.status)
"
```

Expected: `langgraph graph API OK` and a `QueryStatus`. If `CompiledStateGraph` is not at `langgraph.graph.state`, find it with `uv run python -c "import langgraph.graph as g; print([n for n in dir(g) if 'Compiled' in n])"` and adjust the import.

- [ ] **Step 2: Write the end-to-end graph test**

Create `tests/unit/agent/test_graph.py`:

```python
"""Plan 10 Task 9 — end-to-end: build_agent_graph + run_agent_query.

Every test drives the whole six-node graph against fully-faked collaborators —
except ``test_low_confidence_surfaces_unknown_through_the_real_floor``, which
swaps in the REAL ``VerifierService`` (over the fake resolver) so the agent-level
confidence-floor path is pinned against Plan 9's actual floor, not a fake's copy
of it (D-10.U8).
"""

from codeoracle.agent.graph import build_agent_graph, run_agent_query
from codeoracle.models import Query, QueryStatus, VerdictStatus
from codeoracle.models.llm import ChatResponse, TokenUsage


async def test_proven_path_returns_complete_with_a_verified_claim(make_fake_deps) -> None:
    deps = make_fake_deps()  # defaults: symbol exists, 3 knn hits, PROVEN verdict
    graph = build_agent_graph(deps)
    result = await run_agent_query(graph, Query(symbol="codeoracle.services.x.X.method"))
    assert result.status == QueryStatus.COMPLETE
    assert result.verdict is not None
    assert result.verdict.status == VerdictStatus.PROVEN
    assert result.claim is not None
    # §8.5 — PROVEN with >= 3 citations spanning >= 2 handle types
    assert len(result.claim.citations) >= 3
    assert len({c.handle.type for c in result.claim.citations}) >= 2
    assert result.error is None
    assert result.trace_id  # a non-empty trace id


async def test_missing_symbol_returns_complete_with_unknown_verdict(make_fake_deps) -> None:
    deps = make_fake_deps(symbol_exists=False)
    result = await run_agent_query(build_agent_graph(deps), Query(symbol="codeoracle.deleted.symbol"))
    assert result.status == QueryStatus.COMPLETE
    assert result.verdict is not None
    assert result.verdict.status == VerdictStatus.UNKNOWN
    # §8.7 ceremony point 4 — the exact reason string
    assert result.verdict.reason == "symbol not found in graph at snapshot abc1234"
    assert result.claim is None  # no claim on the UNKNOWN path
    assert result.error is None


async def test_refuted_verdict_passes_through_verbatim(make_fake_deps) -> None:
    from codeoracle.models import Verdict

    refuted = Verdict(status=VerdictStatus.REFUTED, reason="excerpt tampered", resolved_citations=[])
    deps = make_fake_deps(verdict=refuted)
    result = await run_agent_query(build_agent_graph(deps), Query(symbol="codeoracle.x.y"))
    # invariant 7 — the agent reports REFUTED as REFUTED; the query still COMPLETEd
    assert result.status == QueryStatus.COMPLETE
    assert result.verdict is not None
    assert result.verdict.status == VerdictStatus.REFUTED


async def test_low_confidence_surfaces_unknown_through_the_real_floor(make_fake_deps) -> None:
    """An LLM ``confidence`` below ``PROVEN_CONFIDENCE_FLOOR`` makes the agent
    surface UNKNOWN — pinned end-to-end against the REAL ``VerifierService``,
    not a floor-aware fake (D-10.U8).

    The floor lives in Plan 9 (``VerifierService.verify`` abstains below it) and
    is unit-tested there; this test pins the *agent-level* path the slice
    actually runs — compose authors a low confidence, ``score`` still builds the
    Claim, and ``verify`` returns UNKNOWN. Wiring the real verifier (over the
    fake resolver) and importing the real ``PROVEN_CONFIDENCE_FLOOR`` constant
    means a future retune of the floor cannot leave this test green against a
    stale literal (the D-10.U6 failure mode). The confidence is driven below the
    floor by feeding compose a ``submit_explanation`` tool call whose
    ``confidence`` is derived from the imported constant — no node is modified.

    Verdict precedence note: this is the ``test_refuted_verdict_passes_through_verbatim``
    shape, NOT the symbol-not-found shape. Because the full pipeline runs, the
    Claim IS present (``score`` produced it) and ``run_agent_query`` routes
    through ``QueryResult.proven`` — which forwards a non-PROVEN verdict
    verbatim. So ``claim`` is non-``None`` here; only the verdict is UNKNOWN.
    """
    import dataclasses
    import json

    from codeoracle.models.llm import ToolCall
    from codeoracle.services.verifier.service import (
        PROVEN_CONFIDENCE_FLOOR,
        VerifierService,
    )

    # A well-formed submit_explanation tool call whose confidence is driven
    # below the real floor (no magic number — derived from the imported
    # constant, so a future retune cannot leave a stale literal green).
    low_conf_args = json.dumps(
        {
            "statement": "The symbol resolves but the evidence is weak.",
            "confidence": PROVEN_CONFIDENCE_FLOOR - 0.2,
            "citation_indices": [0, 1, 2],
            "contradiction_indices": [],
        }
    )
    low_conf_response = ChatResponse(
        content="",
        usage=TokenUsage(input_tokens=20, output_tokens=20),
        finish_reason="tool_calls",
        tool_calls=[ToolCall(id="call_1", name="submit_explanation", arguments_json=low_conf_args)],
    )
    base = make_fake_deps(llm_response=low_conf_response)
    # Replace ONLY the verifier with the real service (frozen+slots dataclass →
    # dataclasses.replace), keeping the fake resolver the real service resolves
    # through. Every other collaborator stays a fake.
    deps = dataclasses.replace(base, verifier=VerifierService(resolver=base.resolver))
    result = await run_agent_query(build_agent_graph(deps), Query(symbol="codeoracle.x.y"))
    assert result.status == QueryStatus.COMPLETE
    assert result.verdict is not None
    assert result.verdict.status == VerdictStatus.UNKNOWN
    # the Claim is present — the floor abstains at the VERDICT, not by dropping
    # the claim (contrast the symbol-not-found path, where claim is None)
    assert result.claim is not None


async def test_compose_failure_returns_failed(make_fake_deps) -> None:
    no_call = ChatResponse(
        content="cannot help",
        usage=TokenUsage(input_tokens=1, output_tokens=1),
        finish_reason="stop",
        tool_calls=[],
    )
    deps = make_fake_deps(llm_response=no_call)
    result = await run_agent_query(build_agent_graph(deps), Query(symbol="codeoracle.x.y"))
    assert result.status == QueryStatus.FAILED
    assert result.claim is None
    assert "submit_explanation" in str(result.error)


async def test_run_agent_query_never_raises_on_collaborator_crash(make_fake_deps) -> None:
    # the resolver raises inside graph_query — the agent must still return a QueryResult
    deps = make_fake_deps(resolve_raises=RuntimeError("sourcebot is down"))
    result = await run_agent_query(build_agent_graph(deps), Query(symbol="codeoracle.x.y"))
    assert result.status == QueryStatus.FAILED  # invariant 6 — no exception escapes
    assert "sourcebot is down" in str(result.error)


async def test_injection_unsafe_symbol_returns_failed(make_fake_deps) -> None:
    deps = make_fake_deps()
    result = await run_agent_query(
        build_agent_graph(deps), Query(symbol="x'}) DETACH DELETE (n) //")
    )
    assert result.status == QueryStatus.FAILED
    assert "injection-safe" in str(result.error)


async def test_missing_snapshot_returns_failed(make_fake_deps) -> None:
    deps = make_fake_deps(has_snapshot=False)
    result = await run_agent_query(build_agent_graph(deps), Query(symbol="codeoracle.x.y"))
    assert result.status == QueryStatus.FAILED
    assert "no APPLIED snapshot" in str(result.error)


async def test_each_query_gets_a_distinct_trace_id(make_fake_deps) -> None:
    deps = make_fake_deps()
    r1 = await run_agent_query(build_agent_graph(deps), Query(symbol="codeoracle.a.b"))
    r2 = await run_agent_query(build_agent_graph(deps), Query(symbol="codeoracle.a.b"))
    assert r1.trace_id and r2.trace_id
    assert r1.trace_id != r2.trace_id


async def test_build_agent_graph_returns_an_invocable_graph(make_fake_deps) -> None:
    graph = build_agent_graph(make_fake_deps())
    assert hasattr(graph, "ainvoke")
```

- [ ] **Step 3: Run the test to verify it fails**

Run: `uv run pytest tests/unit/agent/test_graph.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'codeoracle.agent.graph'`.

- [ ] **Step 4: Write `agent/graph.py`**

Create `src/codeoracle/agent/graph.py`:

```python
"""Assembly of the explain-symbol agent graph.

``build_agent_graph`` wires the six nodes into a sequential LangGraph
``StateGraph``; ``run_agent_query`` seeds the initial state, invokes the
compiled graph, and maps the final state to a ``QueryResult``.
"""

from collections.abc import Awaitable, Callable
from typing import cast
from uuid import uuid4

from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from codeoracle.agent.dependencies import AgentDependencies
from codeoracle.agent.nodes.compose import compose_node
from codeoracle.agent.nodes.embed_query import embed_query_node
from codeoracle.agent.nodes.graph_query import graph_query_node
from codeoracle.agent.nodes.intent import intent_node
from codeoracle.agent.nodes.score import score_node
from codeoracle.agent.nodes.verify import verify_node
from codeoracle.agent.runtime import NodeFn, NodeUpdate
from codeoracle.agent.state import AgentState
from codeoracle.models import Query, QueryResult, QueryStatus


def _with_deps(
    node: NodeFn, deps: AgentDependencies
) -> Callable[[AgentState], Awaitable[NodeUpdate]]:
    """Bind ``deps`` to a node, yielding the single-argument coroutine LangGraph
    invokes.

    An explicit ``async def`` (not ``functools.partial``) so LangGraph
    unambiguously recognises the node as asynchronous — a ``partial`` over a
    coroutine function relies on the runner unwrapping it correctly.
    """

    async def bound(state: AgentState) -> NodeUpdate:
        return await node(state, deps)

    return bound


def build_agent_graph(deps: AgentDependencies) -> CompiledStateGraph:
    """Compile the explain-symbol agent graph for one query.

    The graph is a plain linear chain — ``intent -> graph_query -> embed_query
    -> compose -> score -> verify`` — with no conditional edges. Short-circuit
    on a terminal ``error`` / ``verdict`` is the ``@agent_node`` decorator's job
    (invariant 10). ``deps`` is bound into every node, so the compiled graph
    belongs to exactly one query (invariant 9); compilation is graph validation
    only — microseconds — so compile-per-query costs nothing.
    """
    builder = StateGraph(AgentState)
    builder.add_node("intent", _with_deps(intent_node, deps))
    builder.add_node("graph_query", _with_deps(graph_query_node, deps))
    builder.add_node("embed_query", _with_deps(embed_query_node, deps))
    builder.add_node("compose", _with_deps(compose_node, deps))
    builder.add_node("score", _with_deps(score_node, deps))
    builder.add_node("verify", _with_deps(verify_node, deps))

    builder.add_edge(START, "intent")
    builder.add_edge("intent", "graph_query")
    builder.add_edge("graph_query", "embed_query")
    builder.add_edge("embed_query", "compose")
    builder.add_edge("compose", "score")
    builder.add_edge("score", "verify")
    builder.add_edge("verify", END)
    return builder.compile()


async def run_agent_query(graph: CompiledStateGraph, query: Query) -> QueryResult:
    """Run one explain-symbol query through the compiled graph.

    Seeds every ``AgentState`` key, invokes the graph, and maps the final state
    to a ``QueryResult``. Always returns a ``QueryResult`` — never raises
    (invariant 6): the ``ainvoke`` call is wrapped defensively even though
    ``@agent_node`` already traps node-level failures.

    Three terminal outcomes:

    * ``error`` set                -> ``QueryResult.failed``
    * ``verdict`` set, ``claim`` not -> ``QueryResult`` COMPLETE, no claim
      (the symbol-not-found / UNKNOWN path)
    * ``claim`` + ``verdict`` set   -> ``QueryResult.proven``
    """
    trace_id = uuid4().hex
    initial: AgentState = {
        "query": query,
        "trace_id": trace_id,
        "snapshot_sha": "",
        "plan_handle_types": [],
        "symbol_found": False,
        "graph_citations": [],
        "embed_citations": [],
        "draft_claim": None,
        "claim": None,
        "verdict": None,
        "error": None,
    }
    try:
        final = cast(AgentState, await graph.ainvoke(initial))
    except Exception as exc:  # noqa: BLE001 — defence in depth (invariant 6)
        return QueryResult.failed(trace_id, f"agent graph crashed: {exc}")

    error = final["error"]
    if error is not None:
        return QueryResult.failed(trace_id, error)
    verdict = final["verdict"]
    if verdict is None:
        return QueryResult.failed(trace_id, "agent finished without producing a verdict")
    claim = final["claim"]
    if claim is None:
        # symbol-not-found: a COMPLETE query with an UNKNOWN verdict and no claim.
        return QueryResult(trace_id=trace_id, status=QueryStatus.COMPLETE, verdict=verdict)
    return QueryResult.proven(trace_id, claim, verdict)
```

`run_agent_query` constructs the symbol-not-found `QueryResult` inline rather than via a new classmethod: it is the single call site for that shape, so a named constructor on Plan 3's `QueryResult` would be speculation — and it keeps Plan 10 from modifying a Plan 3 model.

- [ ] **Step 5: Run the test to verify it passes**

Run: `uv run pytest tests/unit/agent/test_graph.py -v`
Expected: PASS (10 tests). If `ainvoke` returns coroutine objects instead of running nodes, LangGraph mis-detected a node as synchronous — confirm `_with_deps` returns an `async def` (it must) and that LangGraph 1.2 is installed.

- [ ] **Step 6: Add the public re-exports to `agent/__init__.py`**

Replace the contents of `src/codeoracle/agent/__init__.py`:

```python
"""CodeOracle agent process.

A LangGraph state machine that turns an ``explain_symbol`` query into a verified
``QueryResult``. ``run_agent_query`` is the entry point; ``build_agent_graph``
is the per-query factory the agent process's FastAPI route handler (Task 11)
calls per request via ``build_agent_dependencies``.
"""

from codeoracle.agent.dependencies import AgentDependencies
from codeoracle.agent.graph import build_agent_graph, run_agent_query
from codeoracle.agent.state import AgentState

__all__ = [
    "AgentDependencies",
    "AgentState",
    "build_agent_graph",
    "run_agent_query",
]
```

- [ ] **Step 7: Run the full agent suite + verify the re-exports**

```bash
uv run python -c "from codeoracle.agent import AgentDependencies, AgentState, build_agent_graph, run_agent_query; print('agent public surface OK')"
uv run pytest tests/unit/agent/ -v
```

Expected: `agent public surface OK`, and every agent test passes.

- [ ] **Step 8: Lint and type-check**

```bash
uv run ruff check src/codeoracle/agent/ tests/unit/agent/
uv run ruff format src/codeoracle/agent/ tests/unit/agent/
uv run ty check src/codeoracle/agent/
```

Expected: all clean. If `ty` flags `CompiledStateGraph` as needing type arguments, parameterise it as the installed LangGraph version expects (`CompiledStateGraph[AgentState]` or similar).

- [ ] **Step 9: Commit**

```bash
git add src/codeoracle/agent/graph.py src/codeoracle/agent/__init__.py tests/unit/agent/test_graph.py
git commit -m "feat(plan-10): Task 9 — graph assembly + run_agent_query + end-to-end tests"
```

---

## Task 10: Documentation, full-suite green, Plan 10 closure

**Files:**
- Modify: `codeoracle/README.md` — list the `codeoracle.agent` process
- No new source; this task verifies the whole plan integrates and closes it out.

- [ ] **Step 1: Run the full repository test suite**

```bash
uv run pytest
```

Expected: every test passes — Plans 1–9's tests *and* Plan 10's. The only Plan 10 change outside `src/codeoracle/agent/` is the additive `AgentError` in `core/exceptions.py`, so no prior test should regress. If any prior test fails, investigate before proceeding — Plan 10 must not regress the platform.

- [ ] **Step 2: Full lint and type-check**

```bash
uv run ruff check src/codeoracle/ tests/
uv run ruff format --check src/codeoracle/ tests/
uv run ty check src/codeoracle/agent/ src/codeoracle/core/exceptions.py
```

Expected: all clean. No `# type: ignore` should appear anywhere in `src/codeoracle/agent/` — the markers are confined to test files (the structural fakes).

- [ ] **Step 3: Update `codeoracle/README.md`**

Add the `codeoracle.agent` process to the README's package / process listing, matching the format Plans 5–9 used for their packages. Suggested entry:

```markdown
- `codeoracle.agent` — the LangGraph agent process. `run_agent_query` turns an
  `explain_symbol` query into a verified `QueryResult` by orchestrating
  retrieval (graph + embeddings), LLM composition, trust scoring, and
  verification. Entry point for the API (`codeoracle.api`) and MCP server.
```

If the README has no such listing, add a short "Agent" subsection under the architecture/processes section.

- [ ] **Step 4: Verify the §8.5 acceptance traceability**

Confirm each row of the "Behavioral audit" section below maps to a test that exists and passes. This is a read-through, not a code change — every Plan 10 invariant must have a named, passing test.

- [ ] **Step 5: Commit**

```bash
git add codeoracle/README.md
git commit -m "feat(plan-10): Task 10 — docs, full-suite green, Plan 10 closure"
```

---

## Plan 10 closure

Plan 10 delivers the **`codeoracle.agent` process** — the LangGraph state machine at the centre of the platform's vertical slice. It is the component that turns an engineer's question into an evidence-bound, verifiable answer.

**What was built, per deliverable:**

- **`AgentState`** (`state.py`) — the 11-key `TypedDict` integration substrate; no reducers, because the graph is a strictly sequential chain.
- **`is_safe_symbol_fqn`** (`fqn.py`) — the injection-safe-FQN gate; closes the Cypher-injection surface created by literal `cypher://` locators (invariant 3).
- **`AgentDependencies`** (`dependencies.py`) — the frozen, per-query collaborator bundle.
- **`@agent_node` + `resolve_to_citation`** (`runtime.py`) — the node runtime: the single short-circuit/span/exception mechanism (invariant 10) and the resolve-at-retrieval helper (invariant 1).
- **Six nodes** (`nodes/`) — `intent` (deterministic validation + snapshot resolution), `graph_query` (existence check + `GRAPH_QUERY` citation), `embed_query` (knn → `DOC_PARAGRAPH` / `CODE_SPAN` citations routed by `is_doc_path`), `compose` (LLM evidence selection + `DraftClaim` assembly), `score` (trust scoring), `verify` (verification).
- **`build_agent_graph` + `run_agent_query`** (`graph.py`) — the per-query factory and the entry point that maps the run to a `QueryResult`.

**Cross-plan modification:** the only Plan 10 change outside `src/codeoracle/agent/` is the additive `AgentError(CodeOracleError)` in `core/exceptions.py` — the established cross-plan pattern.

**What the agent process owns (Task 11) and what Plan 11 (API gateway) inherits:**

- `run_agent_query(graph, query)` is the per-request entry the agent's route handler invokes; `build_agent_graph(deps)` is the per-query factory called from within the same route handler.
- The **agent process** (Task 11) owns the **per-request composition**: opening a request-scoped `AsyncSession` from `app.state.session_factory`, then calling `build_agent_dependencies(session, sourcebot, llm, embeddings, settings)` which assembles `GraphRepository` + `EmbeddingsRepository` + `SnapshotRepository` (from the session) and the five-strategy `HandleResolverService` (Plan 8 L80-84 registry: 2 `SourcebotClient`-backed strategies + 1 `GraphRepository`-backed + 2 Stubs) + `VerifierService(resolver=resolver)` (Plan 9 L1411) + stateless `TrustMatrixService()` (Plan 9 L526). The process-scoped `SourcebotClient` is built once in the FastAPI lifespan and lifetime-managed via `aclose()` per Plan 8 L787-788. Plan 11 owns the **API gateway** role only: request validation against `Query`, `httpx`-based `AgentClient` issuing the HTTP call to the agent, and Plan 11's own response envelope.
- The **agent process** owns **OTel SDK init**: Task 11's `__main__.py` calls Plan 1A's `init_telemetry()` at startup (zero-arg; the function self-constructs `TelemetryConfig()` from `CODEORACLE_OTEL_*` env — `TelemetryConfig` is its own `BaseSettings`, not nested under `Settings`, per Plan 1A L456). FastAPI's OTel server instrumentor extracts incoming `traceparent` per request and sets it as the active context; agent node spans (`intent.parse`, `graph.query`, `embed.query`, `llm.compose`, `verifier.verify`, `resolve.<handle_type>`) inherit. Plan 11 propagates `traceparent` outbound on its `httpx` call to the agent.
- The **agent process** owns **lifespan shutdown drain**: `await sourcebot.aclose()` → `await llm.aclose()` → `await engine.dispose()`, each in its own try/except so partial-cleanup failures still progress (Plan 8 L787-788 composition-root-registers-aclose contract satisfied here for `SourcebotClient`; Plan 4 amendment Commit 2's `LLMProvider.aclose()` ABC default + provider overrides drain for `LLMProvider`). Plan 11 (API) and Plan 12 (MCP) inherit the same shutdown pattern when written.

**What Plan 10 deliberately did NOT do** (see Deviations): no `traces_query` node, no `QueryPlannerService`, no compose retry, no precise FQN→line-range graph query. (`DOC_PARAGRAPH` citations ARE emitted — routed by `is_doc_path` in `embed_query`. The pre-Plan-6 "no `DOC_PARAGRAPH`" deferral is closed now that Plan 6 ships doc-aware chunking. The prior `__main__.py` / OTel-SDK-install deferrals are now BOTH done by Task 11 — see amended Deviation 7 and the replaced Deviation 9.)

**Plan 13 reconciliation (single item, blocking slice acceptance):** §8.5 observability gate 9 lists a `traces.query` child span. The agent has no `traces_query` node (Deviation 1), so **Plan 13 MUST amend its gate-9 span-set assertion to drop `traces.query` before slice acceptance gate 9 can pass** — or restore a `traces_query` node here. This is a load-bearing cross-plan demand, not advisory: an unreconciled gate-9 assertion is a latent slice-acceptance failure. Plan 13's authoring task must verify this reconciliation explicitly (or call back to amend Plan 10) before the slice ships. Every other gate-9 span — including `resolve.<handle_type>`, emitted by `resolve_to_citation` at the agent's own resolve call sites — *is* produced.

---

## Task 11: Agent HTTP entrypoint — FastAPI app with lifespan, uvicorn, `codeoracle/agent/__main__.py`

**Files:**
- Create: `src/codeoracle/agent/__main__.py` (~10 lines — uvicorn entrypoint; no async resource construction here)
- Create: `src/codeoracle/agent/app.py` (FastAPI app factory + lifespan async context manager + `create_test_app` for tests)
- Create: `src/codeoracle/agent/server.py` (route handlers — `POST /v1/query`, `GET /v1/health`)
- Create: `src/codeoracle/models/health.py` (`HealthResponse` Pydantic model)
- Create: `tests/unit/agent/__init__.py` + `tests/unit/agent/conftest.py` + `tests/unit/agent/test_agent_http_contract.py`
- Modify: `src/codeoracle/agent/dependencies.py` (Task 3 file) — append `build_agent_dependencies(*, session, sourcebot, llm, embeddings, settings) -> AgentDependencies` composer
- Modify: `src/codeoracle/models/__init__.py` — re-export `HealthResponse`
- Modify: `infra/config/defaults.env` — append `CODEORACLE_AGENT__HOST=127.0.0.1` and `CODEORACLE_AGENT__PORT=8081`
- Modify: `pyproject.toml` — add `fastapi`, `uvicorn[standard]`, `opentelemetry-instrumentation-fastapi`; ensure `httpx` is in test-deps

**Pre-requisites** (must land before Task 11 executes):
- Plan 3 amendment (`Query`/`QueryResult` `model_config = ConfigDict(extra="forbid")` + `AgentConfig(BaseModel)` nested settings + `CODEORACLE_AGENT__*` env namespace).
- Plan 4 amendment (`LLMProvider.aclose()` default no-op on the ABC + `OpenAIProvider`/`AnthropicProvider`/`VLLMProvider`/`OllamaProvider` overrides).

This task ships the agent's HTTP surface, OTel SDK installation, and lifespan-managed process-scoped resources — the load-bearing inversion that resolves DECIDE-A (spec §3.2 + §4.1 + §4.2 mandate agent-as-process, not library-imported-by-Plan-11).

- [ ] **Step 1: Add deps to `pyproject.toml` + `uv lock` + `uv sync`**

Append to `[project.dependencies]`:

```toml
"fastapi >= 0.115, < 0.116",
"uvicorn[standard] >= 0.32, < 0.33",
"opentelemetry-instrumentation-fastapi >= 0.50b0",
```

Append to `[dependency-groups.dev]` (or whichever group `pytest` lives in):

```toml
"httpx >= 0.27",
```

Run: `uv lock && uv sync`. Verify: `uv run python -c "import fastapi, uvicorn, opentelemetry.instrumentation.fastapi; print('ok')"` — expect `ok`.

- [ ] **Step 2: Append agent config keys to `defaults.env`**

Append to `infra/config/defaults.env`:

```bash
CODEORACLE_AGENT__HOST=127.0.0.1
CODEORACLE_AGENT__PORT=8081
```

Plan 1A Task 3's `defaults.env` test is a subset-check (`assert not missing` against a required-key list), so adding new keys does NOT break it. No Plan 1A test amendment needed.

- [ ] **Step 3: Write failing integration tests at `tests/unit/agent/test_agent_http_contract.py`**

5 cases, all using `create_test_app(...)` with a stubbed request-handling chain. Conftest first:

```python
# tests/unit/agent/conftest.py
import pytest
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4
from codeoracle.adapters.llm.providers.mock import MockLLMProvider
from codeoracle.models.query import QueryResult, QueryStatus


@pytest.fixture(autouse=True)
def _required_settings_env(monkeypatch):
    """Plan 3 L381-387: Settings() requires CODEORACLE_EMBEDDING__MODEL + CODEORACLE_EMBEDDING__DIM
    (the embedding field has no default — required by env) AND CODEORACLE_DB__PASSWORD
    (DBConfig.password is required, no default — NFR D5-4). Without this fixture
    every test that calls Settings() raises ValidationError before the assertion
    phase runs. autouse=True + monkeypatch.setenv means every test in tests/unit/agent/
    inherits these env vars automatically. The fake engine + fake session_factory
    short-circuit real DB I/O, so the password value is never used to connect.
    """
    monkeypatch.setenv("CODEORACLE_EMBEDDING__MODEL", "test-embedding-model")
    monkeypatch.setenv("CODEORACLE_EMBEDDING__DIM", "128")
    monkeypatch.setenv("CODEORACLE_DB__PASSWORD", "test-db-pw")  # D5-4: required, no default


@pytest.fixture
def fake_llm() -> MockLLMProvider:
    """Plan 4 MockLLMProvider — inherits the default no-op aclose() from the ABC
    (Plan 4 amendment Commit 2). No drain side-effects in tests."""
    return MockLLMProvider(canned_response="explanation")


@pytest.fixture
def fake_sourcebot():
    """SourcebotClient stub — aclose() is an async no-op so any lifespan-drain
    invocation doesn't AttributeError. The composer wires this into CodeSpanResolver
    + DocParagraphResolver strategies, but stub_runner short-circuits the entire
    request-handling chain (including build_agent_dependencies), so the strategies
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
    """Async-CM factory. tests/unit/agent only needs the CM to enter/exit cleanly; the
    MagicMock session returned never has methods called on it because stub_runner
    short-circuits build_agent_dependencies."""
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
    """Stub the full request-handling chain: dep builder, graph builder, and runner.
    The handler runs as: open async session → build deps → build graph → run runner.
    Stubbing all three lets HTTP tests verify wire-contract behavior without touching
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
    monkeypatch.setattr("codeoracle.agent.server.build_agent_dependencies", lambda **_: MagicMock())
    monkeypatch.setattr("codeoracle.agent.server.build_agent_graph", lambda _: MagicMock())
    return fake_run


@pytest.fixture
def stub_runner_with_node_spans(monkeypatch):
    """Same as stub_runner, but the fake runner opens 2 child spans inside the
    request lifecycle so test_traceparent_propagates_to_node_spans can assert real
    propagation (server span + 2 child spans, all sharing one trace_id)."""
    from opentelemetry import trace as ot_trace

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
    monkeypatch.setattr("codeoracle.agent.server.build_agent_dependencies", lambda **_: MagicMock())
    monkeypatch.setattr("codeoracle.agent.server.build_agent_graph", lambda _: MagicMock())
    return fake_run
```

Then the 5 test cases:

```python
# tests/unit/agent/test_agent_http_contract.py
import pytest
from httpx import AsyncClient, ASGITransport

from codeoracle.agent.app import create_test_app
from codeoracle.core.config import Settings


@pytest.mark.asyncio
async def test_query_returns_queryresult(
    stub_runner, fake_engine_ok, fake_session_factory, fake_sourcebot, fake_llm
):
    app = create_test_app(
        Settings(),
        engine=fake_engine_ok,
        session_factory=fake_session_factory,
        sourcebot=fake_sourcebot,
        llm=fake_llm,
        embeddings=None,
    )
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        r = await c.post("/v1/query", json={"symbol": "Foo.bar"})
    assert r.status_code == 200
    body = r.json()
    assert "trace_id" in body and len(body["trace_id"]) == 32  # uuid4 hex
    assert body["status"] in {"PENDING", "RUNNING", "COMPLETE", "FAILED"}


@pytest.mark.asyncio
async def test_health_returns_ok_when_db_reachable(
    fake_engine_ok, fake_session_factory, fake_sourcebot, fake_llm
):
    app = create_test_app(
        Settings(),
        engine=fake_engine_ok,
        session_factory=fake_session_factory,
        sourcebot=fake_sourcebot,
        llm=fake_llm,
        embeddings=None,
    )
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        r = await c.get("/v1/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok", "db": "ok"}


@pytest.mark.asyncio
async def test_health_returns_503_when_db_unreachable(
    fake_engine_fail, fake_session_factory, fake_sourcebot, fake_llm
):
    app = create_test_app(
        Settings(),
        engine=fake_engine_fail,
        session_factory=fake_session_factory,
        sourcebot=fake_sourcebot,
        llm=fake_llm,
        embeddings=None,
    )
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        r = await c.get("/v1/health")
    assert r.status_code == 503
    assert r.json() == {"status": "degraded", "db": "fail"}


@pytest.mark.asyncio
async def test_query_rejects_extra_fields(
    stub_runner, fake_engine_ok, fake_session_factory, fake_sourcebot, fake_llm
):
    """Invariant 11: Query carries model_config = ConfigDict(extra="forbid") — Pydantic
    returns 422 on unknown wire fields. Pins Plan 3 amendment's extra="forbid"
    directive against silent field-name drift."""
    app = create_test_app(
        Settings(),
        engine=fake_engine_ok,
        session_factory=fake_session_factory,
        sourcebot=fake_sourcebot,
        llm=fake_llm,
        embeddings=None,
    )
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        r = await c.post("/v1/query", json={"symbol": "Foo.bar", "stray": "x"})
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_traceparent_propagates_to_node_spans(
    stub_runner_with_node_spans,
    fake_engine_ok,
    fake_session_factory,
    fake_sourcebot,
    fake_llm,
    monkeypatch,
):
    """Invariant 12: every incoming traceparent becomes the active OTel context;
    agent node spans inherit. Real propagation test:
      - Bypass OTel's one-shot _TRACER_PROVIDER sentinel via monkeypatch so the
        in-memory exporter actually receives spans.
      - Instrument the test app explicitly + uninstrument at teardown so this test
        doesn't double-instrument other apps in the suite.
      - Stubbed runner opens 2 child spans (intent.parse + verifier.verify).
      - Assert: server span + 2 child spans = 3 spans, all sharing the request's
        traceparent trace_id.
    """
    from opentelemetry import trace as ot_trace
    from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    # set_tracer_provider is documented one-shot. Patch the internal sentinel(s)
    # so this test's provider supplants any previously installed one.
    # _TRACER_PROVIDER is universal; some OTel versions also gate via a Once guard
    # at _TRACER_PROVIDER_SET_ONCE — reset both if present (verify against installed
    # OTel version per the execution-time checklist at the end of this task).
    monkeypatch.setattr(ot_trace, "_TRACER_PROVIDER", provider)
    if hasattr(ot_trace, "_TRACER_PROVIDER_SET_ONCE"):
        monkeypatch.setattr(
            ot_trace,
            "_TRACER_PROVIDER_SET_ONCE",
            ot_trace._TRACER_PROVIDER_SET_ONCE.__class__(),
        )

    app = create_test_app(
        Settings(),
        engine=fake_engine_ok,
        session_factory=fake_session_factory,
        sourcebot=fake_sourcebot,
        llm=fake_llm,
        embeddings=None,
    )
    FastAPIInstrumentor.instrument_app(app)
    try:
        traceparent = "00-12345678901234567890123456789012-1234567890123456-01"
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            await c.post(
                "/v1/query",
                json={"symbol": "Foo.bar"},
                headers={"traceparent": traceparent},
            )
        spans = exporter.get_finished_spans()
        trace_ids = {format(s.context.trace_id, "032x") for s in spans}
        assert len(spans) >= 3  # server span + 2 child spans (intent.parse, verifier.verify)
        assert trace_ids == {"12345678901234567890123456789012"}  # one shared trace_id
    finally:
        FastAPIInstrumentor.uninstrument_app(app)
```

- [ ] **Step 4: Run tests, expect all 5 to fail with `ModuleNotFoundError: codeoracle.agent.app`**

Run: `uv run pytest tests/unit/agent/ -v`
Expected: 5 failures, each with `ModuleNotFoundError: No module named 'codeoracle.agent.app'` (implementation modules don't exist yet).

- [ ] **Step 5: Create `src/codeoracle/models/health.py`** + re-export from `codeoracle.models`

```python
"""Health endpoint response model — published as an HTTP contract (Plan 10
Provides table). Mirrors Plan 3's other models: model_config = extra="forbid"
for wire-contract drift detection."""

from typing import Literal

from pydantic import BaseModel, ConfigDict


class HealthResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    status: Literal["ok", "degraded"]
    db: Literal["ok", "fail"]
```

Update `src/codeoracle/models/__init__.py` to re-export:

```python
from codeoracle.models.health import HealthResponse  # noqa: F401
```

- [ ] **Step 5a: Append `build_agent_dependencies` composer to `src/codeoracle/agent/dependencies.py`**

Append to the existing Task 3 file (which already declares `AgentDependencies` + `make_fake_deps`):

```python
from sqlalchemy.ext.asyncio import AsyncSession

from codeoracle.adapters.llm.embeddings import EmbeddingsProvider
from codeoracle.adapters.llm.interfaces import LLMProvider
from codeoracle.adapters.sourcebot.client import SourcebotClient
from codeoracle.core.config import Settings
from codeoracle.db.repositories.embeddings import EmbeddingsRepository
from codeoracle.db.repositories.graph import GraphRepository
from codeoracle.db.repositories.snapshots import SnapshotRepository
from codeoracle.models.handle import HandleType  # Plan 8's HandleType enum
from codeoracle.services.handle_resolution import HandleResolverService
from codeoracle.services.handle_resolution.strategies import (
    CodeSpanResolver,
    DocParagraphResolver,
    GraphQueryResolver,
    HandleResolverStrategy,
    StubResolver,
)
from codeoracle.services.trust_matrix import TrustMatrixService
from codeoracle.services.verifier import VerifierService


def build_agent_dependencies(
    *,
    session: AsyncSession,
    sourcebot: SourcebotClient,
    llm: LLMProvider,
    embeddings: EmbeddingsProvider,
    settings: Settings,
) -> AgentDependencies:
    """Per-request composer — assembles AgentDependencies from process-scoped
    providers plus a per-request session.

    Per-request data (the Query) is NOT passed here: it stays in the route-handler
    scope and is forwarded to run_agent_query(graph, query) directly. The composer's
    job is to wire process-scoped providers (sourcebot, llm, embeddings, settings)
    with a per-request session into the services AgentDependencies expects.
    """
    graph_repo = GraphRepository(session)
    embeddings_repo = EmbeddingsRepository(session)
    snapshot_repo = SnapshotRepository(session)

    # Plan 8 L80-84 strategy registry: 3 active strategies + 2 stubs for unimplemented
    # HandleTypes. The composition root owns the dict because each strategy has a
    # different constructor signature (Plan 8 L2406/L2638/L2979/L3151).
    strategies: dict[HandleType, HandleResolverStrategy] = {
        HandleType.CODE_SPAN:     CodeSpanResolver(sourcebot=sourcebot),
        HandleType.DOC_PARAGRAPH: DocParagraphResolver(sourcebot=sourcebot),
        HandleType.GRAPH_QUERY:   GraphQueryResolver(graph_repo=graph_repo),
        HandleType.TRACE_SPAN:    StubResolver(handle_type=HandleType.TRACE_SPAN),
        HandleType.DB_RECORD:     StubResolver(handle_type=HandleType.DB_RECORD),
    }
    resolver = HandleResolverService(strategies=strategies)  # Plan 8 L2047
    verifier = VerifierService(resolver=resolver)             # Plan 9 L1354 — resolver kw only
    trust_matrix = TrustMatrixService()                       # Plan 9 L526 — stateless

    return AgentDependencies(
        graph_repo=graph_repo,
        embeddings_repo=embeddings_repo,
        snapshot_repo=snapshot_repo,
        resolver=resolver,
        verifier=verifier,
        trust_matrix=trust_matrix,
        llm=llm,
        embeddings=embeddings,
    )
```

**Execution-time engineer-adjust note:** if a future Plan 8 amendment adds a new `HandleType` enum value, register its `HandleResolverStrategy` subclass in the `strategies` dict above. No other adjustment expected — every constructor signature is primary-source-verified (Plan 8 L80-84/L2047, Plan 9 L526/L1411, Plan 8 L2406/L2638/L2979/L3151).

- [ ] **Step 6: Implement `src/codeoracle/agent/app.py`** — FastAPI app factory with lifespan + `create_test_app`

```python
"""Agent FastAPI app — lifespan-managed process-scoped resources + test-mode factory.

The lifespan async context manager runs on uvicorn's own event loop so all async
resources (engine, session_factory, llm, embeddings, sourcebot) are bound to the
right loop — no asyncio.run() loop-binding bug here."""

from contextlib import asynccontextmanager
from typing import Any, AsyncIterator, Callable, Optional

import structlog
from fastapi import FastAPI
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor

from codeoracle.adapters.llm.config import LLMConfig
from codeoracle.adapters.llm.embeddings import EmbeddingsProviderFactory
from codeoracle.adapters.sourcebot.client import SourcebotClient
from codeoracle.adapters.sourcebot.config import SourcebotConfig  # Plan 8 L529 standalone BaseSettings
from codeoracle.agent.server import register_routes
from codeoracle.core.config import Settings
from codeoracle.db.session import make_async_engine, make_async_session_factory
from codeoracle.services.context_budget import ContextBudgetService
from codeoracle.services.llm import build_budgeted_provider

_log = structlog.get_logger(__name__)


def create_app(settings: Settings) -> FastAPI:
    """Production FastAPI app — lifespan-managed resources.

    LLMConfig + TelemetryConfig + SourcebotConfig are independent BaseSettings
    (Plan 4 L1041 + Plan 1A L456 + Plan 8 L529). LLMConfig() + SourcebotConfig()
    are read inside the lifespan; TelemetryConfig is read by init_telemetry() in
    __main__.py (zero-arg) before this factory is called.
    """
    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        app.state.settings = settings
        app.state.engine = make_async_engine()  # Plan 3 L2619 — zero-arg
        app.state.session_factory = make_async_session_factory(app.state.engine)

        # LLMConfig is a sibling BaseSettings (Plan 4 L1041), not nested under Settings.
        # ContextBudgetService wraps the provider via build_budgeted_provider (Plan 4 Task 12b).
        # max_tokens=24_000 is the Plan 4 L344 default — slice-scope conversation
        # budget. Plan 11 or Plan 13 can promote this to a config-bound field
        # (LLMConfig.budget_max_tokens / CODEORACLE_LLM_BUDGET_MAX_TOKENS) when multi-tenant
        # budget tuning becomes a slice requirement.
        llm_config = LLMConfig()
        app.state.llm = build_budgeted_provider(
            llm_config, ContextBudgetService(max_tokens=24_000)
        )  # Plan 4 Task 12b — budget-wrapped provider
        app.state.embeddings = EmbeddingsProviderFactory.build(settings)  # Plan 6 L3065 staticmethod

        # SourcebotClient is process-scoped: it owns an httpx.AsyncClient with auth
        # baked in (Plan 8 L827-836). Plan 8 L787-788 mandates "the composition root
        # registers an aclose-on-stop" — the agent process satisfies that contract
        # here. Used by CodeSpanResolver + DocParagraphResolver strategies (Plan 8
        # L80-81) that the per-request composer constructs.
        app.state.sourcebot = SourcebotClient(config=SourcebotConfig())
        try:
            yield
        finally:
            # Per-resource try/except: each drain runs even if earlier drains raise,
            # so partial-cleanup failures still progress through shutdown.
            try:
                await app.state.sourcebot.aclose()  # Plan 8 L883
            except Exception as e:
                _log.warning("agent.shutdown.sourcebot_aclose_failed", error=str(e))
            try:
                await app.state.llm.aclose()        # Plan 4 amendment Commit 2
            except Exception as e:
                _log.warning("agent.shutdown.llm_aclose_failed", error=str(e))
            try:
                await app.state.engine.dispose()
            except Exception as e:
                _log.warning("agent.shutdown.engine_dispose_failed", error=str(e))

    app = FastAPI(lifespan=lifespan)
    FastAPIInstrumentor.instrument_app(app)  # server-side traceparent extraction
    register_routes(app)
    return app


def create_test_app(
    settings: Settings,
    *,
    engine: Any,
    session_factory: Callable[[], Any],
    sourcebot: Any,
    llm: Any,
    embeddings: Optional[Any] = None,
) -> FastAPI:
    """Test FastAPI app — pre-built fakes injected into app.state, no lifespan
    (so no real DB/LLM/Sourcebot constructor runs).

    Does NOT instrument with FastAPIInstrumentor. The traceparent test installs
    the instrumentor itself and uninstruments in teardown (FastAPIInstrumentor
    is global state; double-instrumenting across tests doubles span counts)."""
    app = FastAPI()
    app.state.settings = settings
    app.state.engine = engine
    app.state.session_factory = session_factory
    app.state.sourcebot = sourcebot
    app.state.llm = llm
    app.state.embeddings = embeddings
    register_routes(app)
    return app
```

Implementation notes:
- `make_async_engine()` is zero-arg per Plan 3 L2619 + Plan 5 L2191/L2194/L6487/L6625/L6627.
- `build_budgeted_provider(LLMConfig(), budget)` — wraps the one-arg `LLMProviderFactory(config).create()` in `BudgetedLLMProvider` (Plan 4 Task 12b). NOT `.build()`.
- `EmbeddingsProviderFactory.build(settings)` — `@staticmethod` (Plan 6 L3065). NOT instance-method.
- `SourcebotClient(config=SourcebotConfig())` — process-scoped per Plan 8 L787-788's composition-root-registers-aclose contract.
- `create_test_app` does NOT instrument by default — only the one test exercising traceparent installs the instrumentor itself, with a paired uninstrument in teardown.

- [ ] **Step 7: Implement `src/codeoracle/agent/server.py`** — two route handlers

```python
"""Agent route handlers. Registered onto a FastAPI app by create_app /
create_test_app. The handlers are pure passthroughs: they open a request-scoped
AsyncSession, call build_agent_dependencies to assemble per-request deps, build
the per-query graph, and invoke run_agent_query — which itself owns trace_id
generation (Plan 10 Task 9, runner.py)."""

from fastapi import FastAPI, Request, Response
from sqlalchemy import text

from codeoracle.agent.dependencies import build_agent_dependencies
from codeoracle.agent.graph import build_agent_graph
from codeoracle.agent.runner import run_agent_query
from codeoracle.models.health import HealthResponse
from codeoracle.models.query import Query, QueryResult


def register_routes(app: FastAPI) -> None:
    @app.post("/v1/query", response_model=QueryResult)
    async def query(query: Query, request: Request) -> QueryResult:
        async with request.app.state.session_factory() as session:
            deps = build_agent_dependencies(
                session=session,
                sourcebot=request.app.state.sourcebot,
                llm=request.app.state.llm,
                embeddings=request.app.state.embeddings,
                settings=request.app.state.settings,
            )
            graph = build_agent_graph(deps)
            return await run_agent_query(graph, query)  # trace_id populated by runner

    @app.get("/v1/health", response_model=HealthResponse)
    async def health(request: Request, response: Response) -> HealthResponse:
        try:
            async with request.app.state.engine.connect() as conn:
                await conn.execute(text("SELECT 1"))
            return HealthResponse(status="ok", db="ok")
        except Exception:
            response.status_code = 503
            return HealthResponse(status="degraded", db="fail")
```

- [ ] **Step 8: Implement `src/codeoracle/agent/__main__.py`** — uvicorn entrypoint

```python
"""Agent process entrypoint. Invoked by Plan 1A's Dockerfile shim as
`python -m codeoracle.agent`. The function is intentionally tiny — async resource
construction happens INSIDE the FastAPI lifespan that uvicorn invokes on its
own event loop, so no asyncio.run() loop-binding bug here."""

import uvicorn

from codeoracle.agent.app import create_app
from codeoracle.core.config import Settings
from codeoracle.core.logging import configure_logging
from codeoracle.core.telemetry import init_telemetry


def main() -> None:
    settings = Settings()
    configure_logging(settings)
    init_telemetry()  # zero-arg; reads CODEORACLE_OTEL_* env per Plan 1A L485-495
    app = create_app(settings)  # lifespan runs on uvicorn's loop
    uvicorn.run(
        app,
        host=settings.agent.host,
        port=settings.agent.port,
        log_config=None,
    )


if __name__ == "__main__":
    main()
```

`init_telemetry()` is zero-arg because `TelemetryConfig` is its own `BaseSettings(env_prefix="CODEORACLE_OTEL_")` (Plan 1A L456), NOT nested under `Settings` (Plan 3 L503-540 shows `Settings` only nests `db: DBConfig` + `embedding: EmbeddingConfig` + `agent: AgentConfig` + `api: ApiConfig` + `indexing: IndexingConfig`). Plan 1A L495 reads `cfg = config or TelemetryConfig()`, so passing `None` lets the function self-construct from env.

- [ ] **Step 9: Run integration + adjacent tests + lint / type checks**

```bash
uv run pytest tests/unit/agent/ tests/unit/adapters/llm/providers/ tests/unit/agent/ -v
uv run ruff check src/codeoracle/agent/ src/codeoracle/models/health.py
uv run ruff format --check src/codeoracle/agent/ src/codeoracle/models/health.py
uv run ty check src/codeoracle/agent/
```

Expected: all 5 integration tests pass, no Plan 4 provider test regresses, no Plan 10 Tasks 1–10 unit test regresses, ruff + ty report zero findings.

**OTel sentinel verification (execution-time):** once `opentelemetry-api` + `opentelemetry-sdk` are installed in the venv, run:

```bash
uv run python -c "from opentelemetry import trace; print([n for n in dir(trace) if 'TRACER_PROVIDER' in n])"
```

to confirm the traceparent test's sentinel-name guards (`_TRACER_PROVIDER` + optional `_TRACER_PROVIDER_SET_ONCE`) match the installed OTel version. If a third sentinel appears (e.g., `_TRACER_PROVIDER_CONFIGURED`), add a matching `hasattr(...)` guard + `monkeypatch.setattr(...)` reset in `test_traceparent_propagates_to_node_spans`.

- [ ] **Step 10: Commit — `feat(plan-10): Task 11 — FastAPI HTTP entrypoint + lifespan-managed process-scoped resources`**

```bash
git add \
  src/codeoracle/agent/__main__.py \
  src/codeoracle/agent/app.py \
  src/codeoracle/agent/server.py \
  src/codeoracle/agent/dependencies.py \
  src/codeoracle/models/health.py \
  src/codeoracle/models/__init__.py \
  tests/unit/agent/__init__.py \
  tests/unit/agent/conftest.py \
  tests/unit/agent/test_agent_http_contract.py \
  infra/config/defaults.env \
  pyproject.toml \
  uv.lock
git commit -m "feat(plan-10): Task 11 — FastAPI HTTP entrypoint + lifespan-managed process-scoped resources"
```

This is the EXECUTION-time commit — the plan-doc commit (`docs(plan-10): amendment — Task 11 ...`) lands separately as Commit 3 of the amendment trilogy.

---

## Behavioral audit — every claim is pinned by a named test

Every plan invariant and every load-bearing behavior maps to a named test. The right-hand column names the §8.5 slice acceptance test each one enables (Plan 13 wires those against the real stack).

| Invariant / behavior | Pinned by (Plan 10 test) | Enables (§8.5 slice test) |
|---|---|---|
| Inv 1 — excerpts platform-owned | `test_runtime::test_resolve_to_citation_uses_resolver_output_as_excerpt`; `test_compose_node::test_compose_assembles_draft_claim_from_candidates` | `test_explain_existing_symbol_returns_proven_with_citations` |
| Inv 2 — deterministic Cypher order | `test_graph_query_node::test_callers_cypher_is_deterministically_ordered` | PROVEN holds for multi-caller symbols |
| Inv 3 — FQN injection safety | `test_fqn::test_rejects_injection_unsafe_symbols`; `test_intent_node::test_intent_rejects_injection_unsafe_symbol`; `test_graph::test_injection_unsafe_symbol_returns_failed` | hardening beyond §8.5 |
| Inv 4 — sequential execution | structural — `graph.py` wires only linear `add_edge`s; exercised by `test_graph` end-to-end | — |
| Inv 5 — `template_id` on every message | `test_compose_node::test_compose_messages_carry_template_ids` | request-fingerprint stability |
| Inv 6 — the agent never raises | `test_runtime::test_agent_node_traps_exception_into_error`; `test_graph::test_run_agent_query_never_raises_on_collaborator_crash` | — |
| Inv 7 — verdict returned verbatim | `test_verify_node::test_verify_returns_the_verifier_verdict_verbatim`; `test_graph::test_refuted_verdict_passes_through_verbatim` | `test_explain_tampered_excerpt_returns_refuted` |
| Inv 8 — handle-type closure | `test_intent_node::test_intent_never_plans_stub_handle_types`; compose closure assertion | `test_intent_classifier_never_produces_unsupported_handle_types` |
| Inv 9 — per-query composition | `test_graph::test_each_query_gets_a_distinct_trace_id` | — |
| Inv 10 — one short-circuit mechanism | `test_runtime::test_agent_node_short_circuits_when_error_set` / `..._when_verdict_set` | — |
| Symbol-not-found → UNKNOWN | `test_graph_query_node::test_graph_query_sets_unknown_verdict_when_symbol_missing`; `test_graph::test_missing_symbol_returns_complete_with_unknown_verdict` | `test_explain_deleted_symbol_returns_unknown` |
| PROVEN path end-to-end | `test_graph::test_proven_path_returns_complete_with_a_verified_claim` | `test_explain_existing_symbol_returns_proven_with_citations` |
| Compose failure → FAILED | `test_compose_node::test_compose_fails_when_llm_makes_no_tool_call`; `test_graph::test_compose_failure_returns_failed` | — |
| Confidence < `PROVEN_CONFIDENCE_FLOOR` → UNKNOWN (agent-level, real verifier) | `test_graph::test_low_confidence_surfaces_unknown_through_the_real_floor` (D-10.U8) | — (no §8.5 floor gate — Plan 13's UNKNOWN slice exercises the *symbol-not-found* path, a different cause; the floor is pinned only at this Plan-10 unit) |
| Locator format round-trips Plan 6 `source_path` → Plan 8 `parse_file_locator` | `test_embed_query_node::test_embed_query_locator_round_trips_through_plan8_parser` (D-10.U9) | `test_explain_existing_symbol_returns_proven_with_citations` (CODE_SPAN citations resolve) |
| Inv 11 — HTTP contract immutability | `test_agent_http_contract::test_query_rejects_extra_fields` | enables Plan 11's `AgentClient` schema-stability assertion (extra-field rejection on the wire contract) |
| Inv 12 — Traceparent propagation | `test_agent_http_contract::test_traceparent_propagates_to_node_spans` | enables §8.5 gate-9 trace-correlation assertion (caller span_id ≡ root node parent span_id) |

---

## Cross-plan symbol verification

Every symbol Plan 10 consumes from Plans 2–9 is verified by a task's Step 1 pre-flight check before that task's code is written. If a pre-flight fails, the symbol moved — locate it with `grep -rn "class <Name>"` / `def <name>` under `src/codeoracle/` and adjust the import.

| Consumed symbol(s) | Owning plan | Verified by |
|---|---|---|
| `langgraph.graph` (`StateGraph`, `START`, `END`), `opentelemetry` (`trace`, `Status`, `StatusCode`) | third-party | Task 1 Steps 1–4 |
| `CodeOracleError` | Plan 2 | Task 1 Step 5 |
| `Claim`, `DraftCitation`, `DraftClaim`, `HandleType`, `Query`, `Verdict` | Plan 3 | Task 2 Step 1 |
| `EmbeddingsProvider`, `LLMProvider`, `EmbeddingsRepository`, `GraphRepository`, `SnapshotRepository`, `HandleResolverService`, `TrustMatrixService`, `VerifierService` | Plans 4–9 | Task 3 Step 1 |
| `Query` (`.symbol`, `.snapshot_sha`), `HandleType` | Plan 3 | Task 4 Step 1 |
| `GraphRepository.execute_cypher`, `EvidenceHandle`, `Verdict`, `VerdictStatus` | Plans 3, 5 | Task 5 Step 1 |
| `EmbeddingsProvider.embed`, `EmbeddingsRepository.knn` (keyword-only signature), `EmbeddingHit`, `is_doc_path` (from `codeoracle.services.embedding.chunker`) | Plan 6 | Task 6 Step 1 |
| `ChatMessage`, `ChatResponse`, `ToolCall`, `ToolDefinition`, `LLMProvider.chat` | Plans 3, 4 | Task 7 Step 1 |
| `TrustMatrixService.score_each`, `VerifierService.verify` | Plan 9 | Task 8 Step 1 |
| `langgraph.graph.state.CompiledStateGraph`, `QueryResult`, `QueryStatus` | third-party, Plan 3 | Task 9 Step 1 |
| `fastapi.FastAPI`, `fastapi.testclient.TestClient`, `uvicorn.run`, `opentelemetry.instrumentation.fastapi.FastAPIInstrumentor` | third-party | Task 11 Step 1 |
| `Settings` (`settings.agent.host` / `settings.agent.port` / `settings.agent.shared_token` / `settings.db.*` / `settings.embedding.*`), `Query`, `QueryResult` | Plan 3 | Task 11 Step 1 |
| `init_telemetry`, `TelemetryConfig` | Plan 1A | Task 11 Step 1 |
| `build_budgeted_provider`, `ContextBudgetService` | Plan 4 | Task 11 Step 1 |
| `EmbeddingsProviderFactory` | Plan 6 | Task 11 Step 1 |
| `SourcebotClient`, `SourcebotConfig` | Plan 8 | Task 11 Step 1 |
| `HandleResolverService` + per-`HandleType` strategy classes | Plan 8 | Task 11 Step 1 |
| `VerifierService`, `TrustMatrixService` | Plan 9 | Task 11 Step 1 |
| `AgentDependencies`, `run_agent_query` | Plan 10 (Tasks 3, 9) | Task 11 Step 1 |

---

## Plan 10 close-out checklist

- [ ] All 11 task commits (`feat(plan-10): Task N — …`) are on the branch (Tasks 1–10 ship the agent's library surface; Task 11 ships the FastAPI HTTP entrypoint per the amendment).
- [ ] `uv run pytest` — the full repository suite is green; no Plan 1–9 test regressed.
- [ ] `uv run ruff check` / `ruff format --check` — clean across `src/codeoracle/` and `tests/`.
- [ ] `uv run ty check src/codeoracle/agent/ src/codeoracle/core/exceptions.py` — clean.
- [ ] No `# type: ignore` anywhere in `src/codeoracle/agent/` (the markers are confined to test files).
- [ ] `codeoracle/agent/__init__.py` re-exports `build_agent_graph`, `run_agent_query`, `AgentDependencies`, `AgentState`.
- [ ] All 12 plan invariants hold — each has a passing test in the Behavioral audit (Invariants 11 + 12 are pinned by Task 11's integration tests `test_query_rejects_extra_fields` and `test_traceparent_propagates_to_node_spans`).
- [ ] The 11 deviations are still accurate; Deviation 1 (`traces.query` span) carries the blocking Plan-13 gate-9 reconciliation (see §6 Deviation 1 and the "Plan 13 reconciliation" paragraph in this close-out section). Old Deviation 9 (no `agent/__main__.py`) is **removed** — agent ships as a process per spec §4.1; replaced by **new Deviation 9** declaring the no-auth-in-slice localhost-trust deferral (`settings.agent.shared_token` provisioned for Slice+1 reactivation). Deviation 7 is **rewritten** to reflect the OTel SDK install ownership split: Plan 1A delivers the bootstrap; Task 11 calls `init_telemetry()` at process start. Deviation 10 records the `KNN_K=4` slice-tuning choice (spec §4.2 reconciled to `k=4` in #44; `spec-reconcile: applied`). Deviation 11 records the reasoned exception to "OOP over functional" for the six nodes + `resolve_to_citation` (D-10.U2). (The unit-vs-integration LLM-testing split — `_FakeLLM` vs the Plan 13 real `MockLLMProvider` — is a §5.7 testing-strategy note, recorded in Task 3 next to `_FakeLLM`, not a §4 deviation; D-10.U3.)
- [ ] `codeoracle/README.md` lists the `codeoracle.agent` process.
- [ ] Plan 11 handoff is captured: Plan 11 owns the API gateway role only (request validation, `AgentClient` httpx wrapper, response envelope, outbound traceparent propagation). The **agent process** (Task 11) owns `AgentDependencies` construction (via `build_agent_dependencies(session, sourcebot, llm, embeddings, settings)`), OTel SDK init (`init_telemetry()` zero-arg), the request-scoped root span (via FastAPI instrumentor), and the lifespan-shutdown drain calling `SourcebotClient.aclose()` then `LLMProvider.aclose()` then `engine.dispose()` (satisfying Plan 8 L787-788's composition-root-registers-aclose contract for `SourcebotClient`).
