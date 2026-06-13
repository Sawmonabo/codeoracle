# CodeOracle Platform Scaffolding — Design Spec

**Date:** 2026-05-21
**Author:** SAbossedgh + Claude (Opus 4.7, codeoracle session)
**Status:** Draft — awaiting user review before invoking writing-plans
**Supersedes:** Architecture §22 "Phase 3+ platform-deferred" language for the slice work
**References:** `docs/architecture.md`, `docs/roadmap.md`, `docs/investigation-brief.md`, `CLAUDE.md`

---

## Changelog

Plan-audit reconciliation pass. Every amendment below was applied as a single batched edit on the date shown; see `docs/superpowers/plan-audit-findings.md` for the audit findings each amendment closes.

| Date | Section | Change | Rationale |
|---|---|---|---|
| 2026-05-22 | §3.2 | Drop `services/query_planner.py` from the tree. | D-10.3 — no plan owned this service; every plan deferred to "Plan 8" with no such task. Intent-to-resolution-plan is folded into Plan 10's agent graph; the orphan reference is removed. |
| 2026-05-22 | §3.5 | `search_path` order flipped to `"$user", public, ag_catalog`. | D-3.1 — the prior order put `ag_catalog` first, so an unqualified `CREATE TABLE` lands in `ag_catalog` and pollutes AGE's schema. Plan 3 already ships the correct order; spec now matches. |
| 2026-06-13 | §7.5 | Runtime policy updated to Python 3.14+ (`requires-python = ">=3.14"`, Ruff `py314`). | User decision: CodeOracle requires Python 3.14 or newer across project metadata, tooling, CI, and runtime images. |
| 2026-05-22 | §9.3 #3 | Embedding model decided: OpenAI `text-embedding-3-small` (1536-dim). | P0-1 / D-2.1 — pgvector schema cannot be coded until dimension is fixed (re-indexing every vector is a one-way door). Consistent with the OpenAI dev-default established in §5.4. The earlier `BAAI/bge-large-en-v1.5` recommendation is superseded. |
| 2026-05-22 | §3.7 (new) | Joern parser contract pinned: dotted FQN scheme + `:Definition` node schema + three-edge graph contract (`:DEFINES`, `:CALLS`, `:IMPORTS`; `:USES` deferred with a reactivation trigger) + required cross-file symbol-linking pass + unresolved-callee retention policy (`file_path=NULL` for external/stdlib/dynamic-dispatch). | P0-8 / D-5.10 — per-file Joern extraction cannot resolve `:CALLS` edges crossing files; without a pinned FQN scheme + linking pass, every consumer (`find_callers`, `explain_symbol`, `DeltaComputer`) operates on undefined semantics. Pinned *before* Plan 5 codes the parser — re-emitting FQNs later forces a full graph re-index. |
| 2026-05-22 | §8.4 | Dependent-discovery Cypher rewritten against the §3.7 `:Definition` / `:CALLS` / `:IMPORTS` schema (two UNION'd patterns). | Internal-consistency fix — the prior single-line query targeted `:Symbol` nodes and File-level `:CALLS`/`:USES` edges that §3.7 does not emit. Plan 5 codes the parser to §3.7's shape, so Plan 6's `DeltaComputer` must query that shape or silently return empty dependents on every incremental reindex. |
| 2026-05-22 | §1.4 (new) | Add explicit scope boundary: CodeOracle covers retrieval / comprehension / audit over indexed code; **synthesis (authoring new code) is out of scope**. Integration with synthesis tools (Claude Code, Cursor, Copilot) is via MCP exposure of the primitive registry (§10). | User-surfaced gap during compound-question stress test — the slice's single-intent design cascades to UNKNOWN, and the boundary between CodeOracle ("prove existing code") and dev-assist agents ("author new code") must be **named**, not implied. The evidence-binding invariant is structurally incompatible with synthesis (cannot cite bytes that don't exist yet); growing CodeOracle into a general-purpose dev assistant would dissolve the property that justifies its existence. |
| 2026-05-22 | §10 (new); current §10 → §11 | Add target-architecture section: **PrimitiveRegistry + orchestrator agent + intent classifier** with explicit slice-vs-target framing, contract-level `Primitive` Protocol, bounded LLM role table, and §10.6 planning roadmap (Plans 14-18). Current §10 (References) renumbers to §11 (§10.1→§11.1, §10.2→§11.2, §10.3→§11.3). | Same trigger as §1.4 — compound-question stress test revealed that fixed `Literal` intent types and a single hardcoded retrieval plan cascade to UNKNOWN for real engineer queries. Pins the contracts Plans 14-18 will inherit so the slice's interfaces (Plan 11 API, Plan 12 MCP, Plan 13 E2E) do not lock in shapes that block the orchestration layer. Spec previously had no roadmap section; §9 enumerated open questions but not direction-of-travel. |
| 2026-05-23 | §3.2 / §4.1 / §8.2 | Drop `workers/sourcebot_indexer`; reframe Sourcebot as a *service*, not an in-tree worker — removed from the `workers/` tree (§3.2), renumbered out of the process table (§4.1), and restated as a service row in the deployed-processes table (§8.2). Sourcebot indexes once at container start against `file://` snapshot mounts; Plan 8's `SourcebotClient` consumes its HTTP span-fetch (no per-snapshot reindex orchestration). | Sourcebot is an external indexing service, not a per-snapshot CodeOracle worker module — the worker-tree + ingestion references were the wrong abstraction (commit 9dc72b4). This row backfills the changelog entry that amendment omitted — §7.4 orphan #5. |
| 2026-06-01 | §3.7 + §8.4 | Reverted Joern's graph node label from `:Definition` back to `:Symbol` throughout §3.7 (FQN scheme, node schema, `:DEFINES`/`:CALLS` endpoints, linking-pass docstring + MERGE Cypher) and §8.4 (both dependent-discovery patterns). §3.7's substantive contract is unchanged — dotted FQN normalization, node schema, the three edges, the cross-file symbol-linking pass, and unresolved-callee retention all stand; §8.4 keeps its two-UNION'd-pattern structure. Only the label reverts. Also corrected the linking-pass method placement from `JoernCpgParser` to `GraphRepository` (the pass runs MATCH/MERGE/DELETE Cypher and owns the AGE session; the existing Cypher block already runs "via `GraphRepository`", so this resolves an internal contradiction). | D-5.10 / G15 — Plan 6 Architectural Invariant 4 keeps AGE label namespaces extractor-scoped: Joern writes `:Symbol`, SCIP writes `:Definition` + `:Reference`, sharing no MERGE keys so Plan 9 correlates the same FQN across labels as **independent evidence sources**. The 2026-05-22 `:Symbol`→`:Definition` adoption (rows above) would have made §3.7's linking-pass MERGE — which carries no extractor discriminator — splice Joern's `:CALLS` edges onto SCIP's `:Definition` nodes, silently fusing two independent evidence sources into one. Discriminating check (grep `docs/` for an ADR that deliberately unified symbol nodes under `:Definition`): negative — the adoption was an unreconciled oversight, not a deliberate override, so reverting is safe. Plan 6 untouched. |
| 2026-06-02 | §7.5 | Replace invalid `[tool.ty]` keys (`src.root`, `rules.strict`) with ty's real schema (`environment.root`, `rules.all = error`) | ty has no strict mode and no `src.root` key per its config reference + typing FAQ; D-1.7 |
| 2026-06-02 | §7.5 | `[tool.uv]` carries `package = true` (was `managed = true`) | `package` and `managed` are orthogonal; `package = true` is required for the project to be importable/installable; D-1.12 |
| 2026-06-02 | §5.4 | AnthropicProviderConfig default model `claude-sonnet-4-6` → `claude-opus-4-8` | Conform to the decided MODEL PIN (Q1); D-1.14 |
| 2026-06-02 | §3.6 | SCIP-python gate staleness probe re-keyed from `npm view … time.modified` to the latest-version last-publish timestamp (`npm view <pkg> version` → `npm view <pkg> "time[<that-version>]"`). | D-2.12 — `time.modified` mutates on deprecate / owner-change / dist-tag moves, so it false-negatives an abandoned package; a prior pass (#44) had propagated the flawed field into the spec. Spec now matches Plan 2 Task 6 Step 1's mutation-proof last-publish signal. |
| 2026-06-02 | §5.2 | Add `tool_calls: list["ToolCall"] = []` to `ChatMessage`. | D-3.2 — tool-calling is intrinsic to modern provider message schemas (Q3 KEEP); spec §5.2 now matches Plan 3's shipped `models/llm.py`. |
| 2026-06-02 | §5.2 | Constrain `TokenUsage.input_tokens` / `output_tokens` with `Field(ge=0)` (add `Field` to the import), add derived `total_tokens` property. | D-3.3 — completes the #44 partial reconcile (which hardened `EvidenceHandle` in §6.1 + added `frozen=True` to `TokenUsage`); token tallies are non-negative provenance and `total_tokens` is a pure derivation. Spec now matches Plan 3's shipped model. |
| 2026-06-02 | §5.4 | Add max_context_tokens field to all four provider configs (OpenAI 128_000 / Anthropic 200_000 / vLLM 32_768 / Ollama 32_768) | D-4.11 (b) PLAN-WINS — §5.2 line 523 declares max_context_tokens() abstractly but §5.4 had no field feeding it; resolves an internal spec inconsistency |
| 2026-06-02 | §5.4 | Add request_timeout_seconds field to all four provider configs (OpenAI/vLLM 60.0; Anthropic/Ollama 120.0) | D-4.11 (b) PLAN-WINS — every provider's __init__ binds the SDK/HTTP client with this per-call timeout (D6-2); field was undeclared in spec |
| 2026-06-02 | §8.5 | Footnote the cold-reindex budget to acknowledge per-file JVM boot + worker-replica parallelism as the throughput mechanism, with the persistent-server optimization deferred to post-slice. | Reconciles D-5.2 (JVM-per-file trade-off) with the §8.5 budget on the spec side. |
| 2026-06-02 | §3.2 | Add `db/schemas.py` (+ `db/orm.py`) to the `db/` tree — declarative ORM table module created by Plan 3 (Task 7), extended by Plans 5/6/7; previously omitted. | Closes D-5.4's last sub-item; the spec `db/` tree omitted the SQLAlchemy declarative module every migration + repository references. |
| 2026-06-02 | §3.2 | traces_ingest annotated as ContinuousConsumer (HTTP poll of Tempo, no stream/tasks.py) | Old "consumes traces.ingest" line was stale + self-contradictory vs the declared sibling-ABC design (D-7.2 resolution). |
| 2026-06-02 | §6 | Worked graph_query example: RETURN c → RETURN c.fqn AS result, and anchor c as (c:Symbol {snapshot_sha:$snapshot_sha}) | D-8.2 (scalar-per-row conformance) + D-8.1 (structural snapshot-pin coherence; :Symbol per D-5.10/G15) |
| 2026-06-02 | §8.5 | Criterion 11 now requires the citation-resolution log line on a dedicated INFO-floored audit channel that survives the global `log_level` filter | Criterion 11 was unsatisfiable in prod for the info/warn outcomes — the filtering bound logger drops them above its floor (D-9.3); the audit channel keeps the level gradient but guarantees emission. |
| 2026-06-02 | §5.6 + §7.5 | Add `"ollama".msg` to the banned-import sets in BOTH the §5.6 and §7.5 `flake8-tidy-imports.banned-api` blocks (matching each block's existing `.msg` form). | D-4.11 cross-plan — the plan corpus bans `ollama` alongside `openai`/`anthropic`/`litellm` (Plan 4 L7/L43/L161); the spec's two ruff blocks lagged. Direct `ollama` SDK imports must route via `codeoracle.adapters.llm` like every other provider. |

---

## 1. Context and motivation

### 1.1 Two-roles working directory

This repository now serves two roles:

1. **CodeOracle orchestrator state** — `docs/`, `.swarm/`. Persistent state for long-running evidence-first analysis, planning, and coordination work. Continues uninterrupted alongside platform work.
2. **CodeOracle platform code** — `src/codeoracle/` (this spec). The production-grade evidence-first agentic application intelligence platform being built as conventional software, independent of any specific audit cadence.

### 1.2 What the platform is

Per `docs/architecture.md`, CodeOracle is an evidence-first agentic application intelligence platform that builds a verifiable knowledge graph over an application (frontend → API → backend → DB → traces → docs) and uses agents to answer engineer questions with evidence-bound proofs, not chunked-RAG guesses.

Core principle: *the LLM should explain; the platform should prove.* If the platform cannot prove, it returns `UNKNOWN`. If a citation has drifted from its source, it returns `REFUTED`. Hallucination is structurally precluded by the three-valued verdict.

The chosen production stack (PostgreSQL 18 + Apache AGE + pgvector, Joern, SCIP, Qwen3.6 served via vLLM, etc.) is documented in `docs/architecture.md`. This spec scaffolds the platform code to embody that architecture.

### 1.3 Why scaffold now, instead of waiting for the audit to finish

Long-running orchestration and documentation work can continue for many sessions. The platform code:

- Doesn't extract from external target working trees in the slice; the slice corpus is `src/codeoracle/` itself.
- Has its own CI/CD, tests, dependencies — conventional engineering, independent of audit cadence.
- Validates the architecture's stack choices on owned real code before broader external corpora need the platform to be production-ready.

By the time broader production usage arrives, the platform should be past the slice and into Phase 6+ of the roadmap.

### 1.4 Scope boundary: understanding vs synthesis

CodeOracle produces **verifiable evidence about code that exists**. Every claim CodeOracle emits ships with citations that round-trip through the platform's verifier — the LLM may explain, but only the platform can prove. If the platform cannot prove, the verdict is `UNKNOWN`.

**In scope:** retrieval, comprehension, trace-bound analysis, and audit-of-record over an indexed snapshot. Questions of the form *"how does X work / who calls X / what does spec Y say about behavior Z / what runtime trace evidence exists for endpoint W"* — anything where the answer is grounded in bytes that already exist somewhere in the indexed codebase, the trace store, or the doc corpus.

**Out of scope: synthesis.** CodeOracle does not author new code, generate queries, propose refactors, or write tests. Those tasks belong to dev-assist tooling (Claude Code, Cursor, Copilot) and require capabilities (code generation, edit ergonomics, conversational state) CodeOracle deliberately does not build. The evidence-binding invariant that makes CodeOracle trustworthy is structurally incompatible with synthesis — you cannot cite bytes that do not exist yet.

**Integration path with synthesis tooling.** CodeOracle exposes its primitive registry (see §10) via MCP. A user authoring a datastore query against an indexed application snapshot uses Claude Code (or similar) for the synthesis itself and dispatches CodeOracle's `find_db_schema` / `find_db_query_patterns` primitives via MCP to ground that generation in real evidence from the codebase. The two tools are complementary, not competitive: CodeOracle supplies provable context; the synthesis tool authors the change.

This boundary is load-bearing. Trying to grow CodeOracle into a general-purpose dev assistant would dissolve the property that justifies its existence.

---

## 2. Decision: Vertical Slice (Approach A)

### 2.1 Alternatives considered

| Approach | Description | Trade-off |
|---|---|---|
| **A. Vertical slice** | Build ONE query end-to-end through all 9 services. Small test corpus. | Proves architecture works. Doesn't prove it scales. |
| **B. Horizontal layers** | Build each service to full feature-completeness one at a time (api → mcp → workers → ...). | Each layer is "done" but nothing works end-to-end until the very end. |
| **C. Spec-only, defer code** | Write detailed component specs; defer all code until audit closes. | Zero risk of building wrong thing. Zero validation either. |

### 2.2 Why Approach A wins

- **End-to-end fidelity is the only way to find architectural seams that don't fit.** A 9-service architecture has 36 boundary pairs; only end-to-end traffic exercises them.
- **Slice's corpus is the platform's own source code** — dogfood property. If the platform can't explain itself, the pitch collapses.
- **Roadmap alignment**: `docs/roadmap.md` Phase 3+ assumed platform-build deferred. Vertical slice satisfies the spirit of "build only what's needed" while satisfying the letter of "platform must exist when audit completes."
- **Slice is small enough to ship in a session-or-two of Claude Code work**; horizontal layering is months of partial code before any value emerges.

### 2.3 Trade-off accepted

- The slice does not prove the platform works at enterprise scale (500K+ LOC corpora). Scale is a Phase 6+ concern.
- The slice does not exercise foreign-language extraction (Joern + SCIP-python only for v1; JS/TS/Go in Phase 6).
- The slice's LLM cost is non-zero — every CI run with integration tests calls a live LLM. Mitigated by mocking the LLM in unit tests, using OpenAI as cheaper-than-Anthropic dev default, and gating LLM-touching integration tests behind a CI label.

---

## 3. Repository layout

### 3.1 Top-level

```
codeoracle/
├── .github/
│   ├── workflows/                   # CI/CD pipelines (see §7)
│   ├── renovate.json5
│   └── CODEOWNERS
├── docs/                            # orchestrator state — existing, unchanged
├── .swarm/                          # orchestrator state — existing, unchanged
├── CLAUDE.md                        # session-entry guidance (existing, extended for platform conventions)
├── README.md                        # platform pitch + quickstart (new)
├── infra/                           # operational infrastructure (see §3.4)
│   ├── docker/
│   ├── compose/
│   ├── k8s/
│   ├── observability/
│   ├── scripts/                     # check_file_length.py + helpers
│   ├── security/                    # semgrep custom rules, sbom config
│   └── config/                      # default config templates
├── src/
│   └── codeoracle/                      # platform code (see §3.2)
├── tests/
│   ├── unit/                        # fast, no external deps
│   ├── integration/                 # against docker-compose stack
│   └── characterization/            # incremental-vs-full equivalence tests
├── pyproject.toml                   # SINGLE config root for uv/ruff/ty/pytest
├── uv.lock
├── .pre-commit-config.yaml
├── .env.example
└── Makefile                         # thin wrappers around uv run ...
```

### 3.2 `src/codeoracle/` — strict layered architecture

The directory location of every class reveals its role. See `feedback_strict_layer_purity` memory; this is non-negotiable.

```
src/codeoracle/
├── core/                            # cross-cutting infrastructure ONLY
│   ├── config.py                    # platform-wide Pydantic BaseSettings root
│   ├── logging.py                   # structlog setup
│   ├── telemetry.py                 # OTel setup
│   └── exceptions.py                # base exception hierarchy
├── utils/                           # generic utilities — pure functions allowed
│   └── async_helpers.py             # gather_with_concurrency, etc.
├── models/                          # ALL domain models — pure Pydantic, no I/O
│   ├── __init__.py                  # public re-exports
│   ├── evidence.py                  # Claim, Citation, EvidenceHandle, ResolvedCitation, Verdict + enums
│   ├── llm.py                       # ChatMessage, ChatResponse, TokenUsage, ToolDefinition
│   ├── query.py                     # Query, QueryResult, QueryStatus
│   ├── snapshot.py                  # Snapshot(sha, parent_sha, changed_files, status, extractor_results, error, applied_at) + SnapshotStatus + ExtractorOutcome enums
│   └── trust.py                     # TrustLevel enum, SourceKind enum
├── db/                              # platform's OWN database (PG18 + AGE + pgvector)
│   ├── session.py                   # async SQLAlchemy session factory
│   ├── orm.py                       # SQLAlchemy DeclarativeBase (Base) for all platform tables
│   ├── schemas.py                   # declarative ORM tables migrations reference (Snapshot, Embedding, Trace, SnapshotFileOutcome) — created by Plan 3, extended by Plans 5/6/7
│   ├── repositories/
│   │   ├── __init__.py
│   │   ├── evidence.py              # EvidenceRepository
│   │   ├── graph.py                 # GraphRepository (AGE Cypher)
│   │   ├── embeddings.py            # EmbeddingsRepository (pgvector)
│   │   ├── snapshots.py             # SnapshotRepository
│   │   └── traces.py                # TracesRepository (ingested OTel spans)
│   └── migrations/                  # alembic (incl. 0004_snapshot_file_outcomes — per-file extractor-ack barrier table)
├── adapters/                        # I/O wrappers for EXTERNAL systems ONLY
│   ├── mongo/                       # optional read-only document datastore adapter (slice doesn't use)
│   ├── postgres/                    # optional read-only external relational datastore adapter (slice doesn't use)
│   ├── joern/                       # JoernClient — per-file extraction CLI wrapper
│   ├── opengrep/                    # OpenGrepRunner
│   ├── sourcebot/                   # SourcebotClient — HTTP API for span fetch
│   ├── traces/                      # OTel/Tempo client
│   ├── valkey/                      # Valkey queue client
│   └── llm/                         # LLM provider facade
│       ├── interfaces.py            # LLMProvider ABC ONLY
│       ├── factory.py               # LLMProviderFactory
│       ├── config.py                # LLMConfig (Pydantic Settings)
│       └── providers/
│           ├── openai.py            # OpenAIProvider — DEV DEFAULT
│           ├── anthropic.py         # AnthropicProvider
│           ├── vllm.py              # VLLMProvider — production target
│           └── ollama.py            # OllamaProvider
├── services/                        # business logic — proper modules where complexity warrants
│   ├── verifier/
│   │   ├── __init__.py
│   │   └── service.py               # VerifierService
│   ├── handle_resolution/
│   │   ├── __init__.py
│   │   ├── service.py               # HandleResolverService (orchestrator)
│   │   └── strategies/
│   │       ├── __init__.py
│   │       ├── base.py              # HandleResolverStrategy ABC + ResolverError
│   │       ├── code_span.py         # CodeSpanResolver (uses SourcebotClient)
│   │       ├── graph_query.py       # GraphQueryResolver (uses GraphRepository)
│   │       ├── doc_paragraph.py     # DocParagraphResolver (uses SourcebotClient)
│   │       └── stub.py              # StubResolver for TRACE_SPAN, DB_RECORD (raises UnsupportedHandleTypeError)
│   ├── indexing/                    # incremental + full reindex (load-bearing — see §8.4)
│   │   ├── __init__.py
│   │   ├── advisory_lock.py         # reindex_single_flight — pg_advisory_lock single-flight primitive
│   │   ├── service.py               # IndexingService
│   │   ├── delta.py                 # DeltaComputer — git diff + dependent discovery
│   │   └── orchestrator.py          # Coordinates Joern + SCIP + OpenGrep + Sourcebot + embed
│   ├── context_budget.py            # ContextBudgetService (16-24K cap enforcement)
│   ├── llm/
│   │   ├── __init__.py
│   │   └── budgeted_provider.py     # BudgetedLLMProvider decorator + build_budgeted_provider (NFR D7a-1)
│   └── trust_matrix.py              # TrustMatrixService
├── api/                             # HTTP API process (FastAPI)
│   ├── __main__.py                  # entrypoint
│   ├── app.py                       # FastAPI app factory
│   ├── deps.py                      # dependency-injection wiring
│   └── routes/
│       ├── query.py                 # POST /v1/query
│       ├── reindex.py               # POST /v1/reindex
│       └── health.py
├── cli/                             # `codeoracle` CLI — dev tool, NOT a deployed service
│   ├── __main__.py                  # entrypoint (`python -m codeoracle.cli ...`)
│   └── commands/
│       ├── index.py                 # codeoracle index [--since SHA | --full]
│       └── query.py                 # codeoracle query "<text>" (thin wrapper over /v1/query)
├── mcp/                             # MCP server process — stdio (dev) + streamable HTTP (service-mode parity with /v1/query)
│   ├── __main__.py
│   ├── server.py                    # exposes explain_symbol tool + reindex tool
│   └── transports.py                # stdio + http transport wiring
├── agent/                           # LangGraph agent process
│   ├── __main__.py
│   ├── graph.py                     # the LangGraph state graph
│   └── nodes/                       # individual graph nodes (intent_parse, graph_query, embed_query, compose, verify)
└── workers/                         # background worker processes — long-running Valkey queue consumers
    ├── _base/
    │   ├── consumer.py              # WorkerConsumer ABC: poll(queue) → dispatch(message) → ack/nack
    │   ├── message.py               # WorkerMessage schema, retry policy, DLQ
    │   └── wiring.py                # Worker-process composition root (ContextBudgetService, factories, repos)
    ├── joern_indexer/                # consumes "indexing.joern" → calls adapters/joern + db/repositories/graph
    │   ├── __main__.py
    │   ├── consumer.py              # extends WorkerConsumer
    │   └── tasks.py                 # per-file indexing functions
    ├── scip_indexer/                 # consumes "indexing.scip"
    ├── opengrep_runner/              # consumes "indexing.opengrep"
    ├── traces_ingest/                # ContinuousConsumer — polls Tempo (HTTP) into PG; no Valkey stream, no tasks.py
    └── embed_indexer/                # consumes "indexing.embed"
    # No sourcebot_indexer/ — Sourcebot indexes once at container start
    # against file:// snapshot mounts (deployment-side, not per-snapshot
    # orchestration). See Plan 6 "Plan Defers To" for the full rationale.
```

**Worker process model**: each `workers/<name>/__main__.py` is a long-running consumer process. It polls one or more Valkey streams, dispatches each message to its `tasks.py` handler, ACKs on success, NACKs on failure — redelivered by the reclaim ladder up to N retries, paced by a fixed reclaim min-idle rather than a per-attempt backoff curve — and dead-letters after N failures. This is the same shape as production-grade Celery/RQ workers but built directly on Valkey streams (one less moving piece than Celery). `IndexingService` enqueues work; it does NOT call workers directly.

### 3.3 Layer purity rules

These are enforced structurally (ruff `flake8-tidy-imports.banned-api`) and reviewed in every PR:

1. **`models/`** holds pure Pydantic data types and enums. No I/O. Behavior limited to `@field_validator` / `@model_validator`.
2. **`adapters/`** holds I/O wrappers for EXTERNAL systems only. Test: *"could I delete this if we no longer used Joern / Sourcebot / OpenAI / Postgres-as-target?"* If yes, it's an adapter.
3. **`services/`** holds business-logic classes that compose models + adapters + repositories. Anything that applies domain rules or orchestrates adapters is a service. Never an adapter.
4. **`core/`** holds cross-cutting infrastructure: config, logging, telemetry, exception base. Not domain. Not services.
5. **`db/`** holds the platform's own database access (PG18 + AGE + pgvector). Repository pattern.
6. **Processes (`api/`, `mcp/`, `agent/`, `workers/`)** are thin entrypoints. They wire services together; they don't contain business logic.

### 3.4 `infra/` — operational infrastructure

```
infra/
├── docker/
│   ├── Dockerfile                   # unified — multi-stage, args for image variants
│   └── Dockerfile.model-server      # CUDA base for vLLM
├── compose/
│   └── docker-compose.yml           # all services for local dev / integration tests
├── k8s/                             # production manifests (deferred to Phase 7+)
├── observability/
│   ├── otel-collector.yaml
│   ├── tempo-config.yaml
│   ├── prometheus.yml
│   └── grafana/dashboards/
├── scripts/
│   ├── check_file_length.py         # 500/700 line cap enforcer
│   ├── bootstrap_dev.sh
│   └── seed_test_corpus.sh          # initial index of src/codeoracle/
├── security/
│   └── semgrep/codeoracle-custom.yml    # custom SAST rules
└── config/
    └── defaults.env                 # default env values
```

Separates "what the system is" (`src/codeoracle/`) from "how it runs" (`infra/`).

### 3.5 Bootstrap migration order — and the AGE-per-connection contract

AGE has a per-connection contract: `LOAD 'age'` and `SET search_path = "$user", public, ag_catalog` are session-scoped — they apply only to the connection that ran them. Bootstrap alone is insufficient; every app connection (API, workers, CLI, alembic) must re-establish AGE context on connect, or queries against `cypher()` / `agtype` fail with "type does not exist." The spec uses a two-layer approach.

**Layer 1: one-shot bootstrap (idempotent — `infra/scripts/bootstrap_db.sh`)**

```sql
-- Step 1: extensions (idempotent via IF NOT EXISTS)
CREATE EXTENSION IF NOT EXISTS age;            -- Apache AGE (graph)
CREATE EXTENSION IF NOT EXISTS vector;         -- pgvector (embeddings)

-- Step 2: per-database default search_path (persists; affects future connections)
ALTER DATABASE codeoracle SET search_path = "$user", public, ag_catalog;

-- Step 3: load AGE on THIS connection so create_graph resolves
LOAD 'age';

-- Step 4: create the AGE graph (idempotent — guarded against duplicate_schema)
DO $$ BEGIN
  PERFORM create_graph('codeoracle');
EXCEPTION WHEN duplicate_schema THEN
  NULL;  -- graph already exists; safe to re-run
END $$;

-- Step 5 (run from shell after the above SQL completes):
-- uv run alembic upgrade head
```

Alembic uses the same connection pool as the app (configured in `db/session.py`); the layer-2 event listener (below) ensures alembic's connections also `LOAD 'age'`, so migrations can freely reference `agtype` / `cypher()`.

**Layer 2: per-connection event listener (`src/codeoracle/db/session.py`)**

```python
from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine

@event.listens_for(Engine, "connect")
def _setup_age_per_connection(dbapi_conn, _connection_record):
    """Every fresh DB connection loads AGE and sets search_path.

    Required because LOAD and SET search_path are session-scoped in Postgres.
    Without this, alembic / API / workers / CLI all blow up on first cypher() call.
    """
    with dbapi_conn.cursor() as cur:
        cur.execute("LOAD 'age'")
        cur.execute('SET search_path = "$user", public, ag_catalog')
```

This runs on every new connection from any process that imports `db.session` — alembic, FastAPI, worker consumers, CLI commands. AGE context is a structural property of the platform's DB layer, not bootstrap discipline that can drift.

Bootstrap script invocation points: docker-compose `entrypoint`, `infra/scripts/bootstrap_dev.sh` for local setup, K8s init container in Phase 7+, CI `bash infra/scripts/bootstrap_db.sh` (§7.2).

**Alternative considered**: adding `age` to `shared_preload_libraries` in `postgresql.conf` (loads AGE at server start, no per-session LOAD needed). Rejected as primary mechanism: requires modifying the official `postgres:18` image entrypoint or a custom postgres config volume — more moving parts, and breaks if someone runs against a stock-config Postgres in dev. The event-listener approach works against any vanilla Postgres-with-AGE-extension. (May add `shared_preload_libraries` as an additional belt-and-suspenders defense in Phase 7+ prod config.)

### 3.6 Pre-implementation verification gates

These external-dependency assumptions are load-bearing. Verifying them is the FIRST scaffolding task — before any platform code is written. A negative result triggers a documented fallback rather than mid-implementation reconciliation.

| Assumption | Verification | Fallback if fails |
|---|---|---|
| **Apache AGE supports PostgreSQL 18** | Step 1 — `docker manifest inspect apache/age:release_PG18` (or the equivalent for whatever AGE-bundled image we standardize on); if the manifest exists, the image bundles AGE for PG18. Step 2 — `docker run --rm <that-image> psql -c 'CREATE EXTENSION age; LOAD age; SELECT create_graph(...);'` succeeds. (Stock `postgres:18` does NOT ship AGE; verification against it always fails for a reason that isn't "PG18 isn't supported.") PG18 GA'd 2025-09; AGE has historically lagged PG majors by 6-12 months — fallback path is the most likely outcome. | Pin `postgres:17` (with AGE: `apache/age:release_PG17`) in `compose/docker-compose.yml` and CI `services.postgres.image`. Update `docs/architecture.md` with the version actually in use. Re-evaluate at every AGE release. |
| **Joern's Python frontend produces a usable CPG** | Run `joern-parse infra/preflight-fixtures/sample_py.py` (a planted non-trivial fixture — class + cross-call) and confirm the emitted CPG is non-empty (any bytes — parser produced nodes, not just a header). The fixture is used instead of `src/codeoracle/services/verifier/` because the verifier service doesn't exist at scaffolding day 1 (Plan 9 creates it); coupling preflight to a not-yet-scaffolded path would force empty-file substitution silently — defeating the gate. The full `cpg.method("verify").caller.l` call-graph assertion is in Plan 5's Joern parser test suite (where the Scala REPL dep is already paid for), not in this preflight gate — see the Plan 2 §6 deviation #4 rationale. | If Python frontend is too sparse to support the slice's call-graph queries, fall back to SCIP-only for Python (SCIP-python is mature); document that Joern is used only for non-Python languages added in Phase 6. |
| **SCIP-python emits indexes resolvable by the LSIF/SCIP toolchain** | Index `infra/preflight-fixtures/` (the planted fixture) with `scip-python index --project-name=codeoracle` and confirm `scip print <emitted-index>` contains the fixture's symbols (e.g., `verify`). The runner writes the index into a tmpdir to avoid source-tree pollution; the index path is an implementation detail of `infra/scripts/preflight.sh`, not a spec assertion. The planted fixture is used for the same reason the Joern gate does — `src/codeoracle/` doesn't exist at scaffolding day 1. Additionally, confirm the indexer packages are still published and maintained by reading the **last-publish** timestamp — NOT `time.modified` (which updates on any registry-metadata mutation: a `npm deprecate`, an owner change, or a `dist-tag` move, none of which is a new release, so it can false-negative an abandoned package). Resolve the latest version with `npm view @sourcegraph/scip-python version` (= `dist-tags.latest`), then read that exact version's publish time with `npm view @sourcegraph/scip-python "time[<that-version>]"` (and the same for `scip-typescript`). A last-publish timestamp older than ~18 months, or a deprecated/unpublished status, triggers the column-3 fallback rather than proceeding, since Sourcegraph has been narrowing SCIP scope through 2024–2025 and these indexers may have been archived. | If unresolved, fall back to extracting via `pyright --outputjson` or `tree-sitter-python` (slice can degrade gracefully — call-graph fidelity drops; verifier still works on the symbols it can extract). |
| **vLLM serves the OpenAI Embeddings API contract** | `curl -X POST $VLLM_BASE/v1/embeddings -d '{"model":"...","input":"hello"}'` returns a parseable response. | If vLLM embeddings endpoint diverges from OpenAI shape, route embeddings through a separate adapter `adapters/llm/providers/vllm_embed.py` rather than the chat provider. |

Each verification is one bash command run during scaffolding day 1, recorded as a `infra/scripts/preflight.sh` whose pass/fail goes into `.cache/preflight/results.md`. The script transcribes the column-3 fallbacks verbatim — a gate failure records the fallback path *into the results file* rather than aborting, so the executor reads which alternative is now load-bearing and updates `docs/architecture.md` + the consuming Plan 3+ task accordingly. Slice work proceeds against whichever fallback applied; no mid-implementation reconciliation. The runner is one-shot at scaffolding day 1 (distinct from `infra/scripts/check_pinned_env.sh`, the CI-recurring pin-consistency lint — different job, different sequencing).

### 3.7 Joern parser contract — FQN scheme, `:Symbol` node schema, and cross-file symbol-linking pass

Decided 2026-05-22 (closes P0-8 / D-5.10). The Joern parser is load-bearing for symbol resolution — every consumer (`services/handle_resolution/strategies/graph_query.py`, Plan 10's `graph_query` node serving `explain_symbol` queries, and `services/indexing/delta.py`'s `find_callers` query) depends on a deterministic graph shape. `adapters/joern/JoernClient` extracts CPGs and `db/repositories/graph.GraphRepository` writes them, but the *contract* — what FQNs look like, what `:Symbol` nodes carry, how cross-file edges materialize — is pinned here so that all three consumers and the parser can be coded against the same shape. **Re-emitting FQNs later is a one-way door** (every existing `:Symbol` and `:CALLS` row must be re-extracted), so this contract changes only through an explicit migration.

**FQN normalization — dotted, language-aware.** Joern's native form is `<file>::<name>` (Python) / `<file>::<class>::<method>` (Java/etc.) — file-prefixed and language-dependent. Every consumer would have to know the source path and handle separator variants. Normalize at parse time to a single canonical form: `package.module.ClassName.method_name` (dotted, language-agnostic, file-path-independent).

- Python: `codeoracle.services.verifier.service.VerifierService.verify` (derived from the package + module path + qualified callable).
- Java: `com.example.foo.Bar.baz`.
- Anonymous / nested callables: `<parent_fqn>.<local_name>@<lineno>` — `lineno` disambiguates anonymous siblings within the same parent.

**`:Symbol` node schema.** Every callable / class / module definition emits exactly one `:Symbol` node:

| Property | Type | Notes |
|---|---|---|
| `fqn` | string | The normalized dotted FQN above. Together with `snapshot_sha`, the unique identifier in graph queries. |
| `name` | string | Local name (last `.`-segment of `fqn`). |
| `kind` | enum | `function` \| `method` \| `class` \| `module`. |
| `file_path` | string | Repo-relative path. |
| `start_line` | int | 1-indexed inclusive. |
| `end_line` | int | 1-indexed inclusive. |
| `language` | string | `python`, `java`, etc. — drives downstream filters. |
| `snapshot_sha` | string | Snapshot the definition was extracted from. |

**Edges.** Three edge types make up the parser's graph contract. Every consumer (slice query, `find_callers`, incremental dependent-discovery in §8.4) reads only these:

| Edge | From | To | Meaning |
|---|---|---|---|
| `:DEFINES` | `:File` | `:Symbol` | The file in which this definition appears. Resolves at per-file parse time. |
| `:CALLS` | `:Symbol` | `:Symbol` | A call from one definition to another. Cross-file edges materialize in two phases — see linking pass below. |
| `:IMPORTS` | `:File` | `:File` | File-level import edge (Python `import` / `from … import …`, Java `import`, etc.). Resolves at per-file parse time via `MERGE` on `path` + `snapshot_sha` — the linking pass does not touch `:IMPORTS`. |

`:USES` (non-call references — type annotations, attribute access, mock targets) is **out of scope** for the slice. Reactivation trigger: introduce `:USES` when the agent ships a `find_references` intent or the audit pipeline needs non-call reference tracking; adding it requires extending the linking pass to a second edge label.

**Cross-file symbol-linking pass — required.** `JoernClient` extracts one file at a time and `db/repositories/graph.replace_file_subgraph()` writes one file's nodes; per-file CPGs cannot resolve a `CALLS` whose callee lives in another file because the callee `:Symbol` does not exist in the per-file CPG. Without a second pass, `find_callers` silently returns empty for cross-file callers — the worst kind of bug, because every test that fits inside a single file passes.

Plan 5 MUST implement the linking pass as `GraphRepository.link_cross_file_symbols(snapshot_sha)` — the pass executes MATCH/MERGE/DELETE Cypher and therefore lives on the repository that owns the AGE session, not on the stateless `JoernCpgParser` (which only converts a per-file CPG into Cypher INSERT strings and holds no session; the Cypher block below already runs "via `GraphRepository`"). The indexing service MUST invoke it exactly once per snapshot — after the per-file extraction barrier drains and before the snapshot transitions to `APPLIED`, inside the `pg_advisory_lock(0x46AE0001)` single-flight section that already guards the reindex:

```python
async def link_cross_file_symbols(self, snapshot_sha: str) -> None:
    """Resolve unresolved CALLS edges by FQN match across the whole snapshot.

    Pre: every per-file extraction for `snapshot_sha` has persisted its
    :Symbol nodes (their `file_path` is set) AND its provisional
    :CALLS-to-unresolved edges (the callee :Symbol has file_path=NULL).

    Post: every cross-file :CALLS edge whose callee fqn matches a resolved
    :Symbol (`file_path IS NOT NULL`) is redirected to the resolved
    node, and the consumed unresolved placeholder is deleted. Provisional
    :CALLS to fqns with no match in the snapshot (stdlib, third-party,
    dynamic dispatch unresolvable at static parse) are RETAINED — the
    unresolved :Symbol stays with `file_path=NULL` so consumers can
    distinguish external dependencies from internal ones. Queries that
    want only internal symbols filter on `WHERE callee.file_path IS NOT
    NULL`. Idempotent — a second run finds no unresolved callees with new
    matches and is a no-op.
    """
```

The Cypher MERGE used by the pass (executed via `GraphRepository`):

```cypher
MATCH (caller:Symbol {snapshot_sha: $sha})-[r:CALLS]->(unresolved:Symbol {snapshot_sha: $sha})
WHERE unresolved.file_path IS NULL
MATCH (resolved:Symbol {fqn: unresolved.fqn, snapshot_sha: $sha})
WHERE resolved.file_path IS NOT NULL
MERGE (caller)-[:CALLS]->(resolved)
DELETE r, unresolved
```

The linking pass is part of every `services/indexing/IndexingService.reindex(snapshot_sha)` call — never a separate user-triggered step. `find_callers` and any other cross-file graph query MUST assume the link pass has run for the snapshot it queries; running the pass twice on the same snapshot is a no-op.

---

## 4. The 9 services

### 4.1 Service map

Per architecture §3, the platform consists of these processes. Each runs in its own container:

| # | Service | Role | Slice query path? |
|---|---|---|---|
| 1 | `api` | HTTP entrypoint (FastAPI) — `/v1/query`, `/v1/reindex`, `/v1/health` | Yes |
| 2 | `mcp` | MCP server — `explain_symbol` and `reindex` tools | Yes (parity test) |
| 3 | `agent` | LangGraph orchestrator — intent parse → resolution plan → LLM compose | Yes |
| 4 | `workers/joern_indexer` | Joern extraction, per-file, results land in PG+AGE | Yes |
| 5 | `workers/scip_indexer` | SCIP extraction (Python), per-file | Yes |
| 6 | `workers/opengrep_runner` | Textual reference cross-check | Yes |
| 7 | `workers/embed_indexer` | Embed files via vLLM, store in pgvector | Yes |
| 8 | `workers/traces_ingest` | Pull OTel traces from Tempo into PG | Yes (ingests platform's own self-traces) |
| 9 | `sourcebot` (service, not a worker) | HTTP span-fetch consumed by Plan 8's `SourcebotClient`. Indexes once at container start against `file://` snapshot mounts; no per-snapshot reindex orchestration. | Yes (query path only) |

Plus shared infrastructure: PostgreSQL 18 + AGE + pgvector (single instance), Valkey (queue), vLLM (embedding model server), Tempo (trace storage), OpenTelemetry Collector.

### 4.2 Service interactions for the slice query

When a user issues `POST /v1/query { type: "explain_symbol", symbol: "<fqn>", corpus: "self" }`:

```
1. api → agent (REST)
   Payload: {symbol, corpus, snapshot_sha}

2. agent (LangGraph):
   node "intent_parse"   → llm_provider.chat(small model) → confirms intent is explain_symbol
   node "graph_query"    → db.GraphRepository.find_symbol_and_callers(symbol, snapshot_sha)
   node "embed_query"    → vllm embed (query) → db.EmbeddingsRepository.knn(query_vec, k=4)
   node "traces_query"   → db.TracesRepository.find_spans_for_symbol(symbol)
   node "compose"        → llm_provider.chat(large model)
                            → output: DraftClaim (handle + excerpt per citation; LLM does NOT set source_trust)
   node "score"          → services.trust_matrix.TrustMatrixService.score_each(draft)
                            → output: Claim with platform-computed source_trust per citation
   node "verify"         → services.verifier.VerifierService.verify(claim)
                            → uses services.handle_resolution.HandleResolverService
                              → CodeSpanResolver → adapters.sourcebot.SourcebotClient.fetch_span()
                              → GraphQueryResolver → db.GraphRepository.run_cypher()
                              → DocParagraphResolver → adapters.sourcebot.SourcebotClient.fetch_span()

3. agent → api: {verdict, claim}

4. api → user: HTTP 200 {verdict, claim, trace_id}
```

Every LLM call goes through `adapters/llm/factory.py` → concrete provider; structurally enforced by ruff `flake8-tidy-imports.banned-api`.

---

## 5. LLM provider abstraction

### 5.1 Why this matters

Per memory `project-llm-provider-agnostic`: the platform must work against any backend (vLLM+Qwen3.6 in prod, OpenAI in dev, Anthropic or Ollama as alternatives). Provider lock-in is forbidden.

Per memory `project-dev-llm-provider-openai`: **OpenAI is the dev default**. `CODEORACLE_LLM_PROVIDER=openai` is the documented default in `.env.example`, README, demos, and compose files. Anthropic and vLLM remain config-selectable but are not the default.

### 5.2 The interface

`src/codeoracle/adapters/llm/interfaces.py` — the ABC ONLY (no data models):

```python
from abc import ABC, abstractmethod
from codeoracle.models.llm import ChatMessage, ChatResponse, ToolDefinition

class LLMProvider(ABC):
    @abstractmethod
    async def chat(
        self,
        messages: list[ChatMessage],
        *,
        tools: list[ToolDefinition] | None = None,
        structured_output: type | None = None,
    ) -> ChatResponse: ...

    @abstractmethod
    def count_tokens(self, text: str) -> int: ...

    @abstractmethod
    def model_name(self) -> str: ...

    @abstractmethod
    def max_context_tokens(self) -> int: ...
```

`src/codeoracle/models/llm.py` — pure data types:

```python
from pydantic import BaseModel, ConfigDict, Field
from typing import Literal

class ChatMessage(BaseModel):
    role: Literal["system", "user", "assistant", "tool"]
    content: str
    tool_call_id: str | None = None
    tool_calls: list["ToolCall"] = []

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
    finish_reason: Literal["stop", "length", "tool_calls", "content_filter"]
    tool_calls: list["ToolCall"] = []

class ToolDefinition(BaseModel):
    name: str
    description: str
    parameters_schema: dict  # JSON Schema
```

### 5.3 Factory

`src/codeoracle/adapters/llm/factory.py`:

```python
from codeoracle.adapters.llm.config import LLMConfig
from codeoracle.adapters.llm.interfaces import LLMProvider

class LLMProviderFactory:
    """Constructs the RAW, provider-specific LLMProvider for `config.provider`.
    Budget enforcement is NOT applied here — it is composed around the result by
    `BudgetedLLMProvider` (services/llm/), so adapters/ keeps no upward edge into
    services/. Production code MUST build providers through
    `codeoracle.services.llm.build_budgeted_provider`, never this factory directly (a
    bare factory product is unbounded)."""

    def __init__(self, config: LLMConfig):
        self._config = config

    def create(self) -> LLMProvider:
        from codeoracle.adapters.llm.providers.openai import OpenAIProvider
        from codeoracle.adapters.llm.providers.anthropic import AnthropicProvider
        from codeoracle.adapters.llm.providers.vllm import VLLMProvider
        from codeoracle.adapters.llm.providers.ollama import OllamaProvider
        from codeoracle.adapters.llm.providers.mock import MockLLMProvider

        match self._config.provider:
            case "openai":    return OpenAIProvider(self._config.openai)
            case "anthropic": return AnthropicProvider(self._config.anthropic)
            case "vllm":      return VLLMProvider(self._config.vllm)
            case "ollama":    return OllamaProvider(self._config.ollama)
            case "mock":      return MockLLMProvider(self._config.mock)   # test-only
            case _:           raise ValueError(f"unsupported provider: {self._config.provider}")
```

### 5.4 Config

`src/codeoracle/adapters/llm/config.py`:

```python
from pydantic import BaseModel, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict
from typing import Literal

class OpenAIProviderConfig(BaseModel):
    api_key: SecretStr                    # secret — masked in repr/model_dump/logs (D5-1)
    model: str = "gpt-4o-mini"           # dev default — cheap, fast
    base_url: str | None = None          # for OpenAI-compatible endpoints
    max_context_tokens: int = 128_000
    request_timeout_seconds: float = 60.0

class AnthropicProviderConfig(BaseModel):
    api_key: SecretStr                    # secret — masked in repr/model_dump/logs (D5-1)
    model: str = "claude-opus-4-8"
    max_output_tokens: int = 4096  # Anthropic requires max_tokens on every request
    max_context_tokens: int = 200_000
    request_timeout_seconds: float = 120.0

class VLLMProviderConfig(BaseModel):
    base_url: str = "http://vllm:8000/v1"
    model: str = "Qwen/Qwen3.6-32B"
    api_key: SecretStr = SecretStr("EMPTY")  # vLLM ignores it; OpenAI SDK requires non-empty (D5-1)
    max_context_tokens: int = 32_768
    request_timeout_seconds: float = 60.0

class OllamaProviderConfig(BaseModel):
    base_url: str = "http://ollama:11434"
    model: str = "qwen2.5:32b"
    max_context_tokens: int = 32_768
    request_timeout_seconds: float = 120.0

class MockLLMProviderConfig(BaseModel):
    """Test-only — loads scripted responses keyed by request fingerprint."""
    scripts_dir: str = "tests/fixtures/llm_scripts"
    strict: bool = True                          # raise on unknown request, vs. return a default

class LLMConfig(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="CODEORACLE_LLM_", env_nested_delimiter="__")

    provider: Literal["openai", "anthropic", "vllm", "ollama", "mock"] = "openai"   # DEV DEFAULT — "mock" is test-only
    openai: OpenAIProviderConfig | None = None
    anthropic: AnthropicProviderConfig | None = None
    vllm: VLLMProviderConfig | None = None
    ollama: OllamaProviderConfig | None = None
    mock: MockLLMProviderConfig | None = None
```

### 5.5 Context budget — a service, not an adapter

`src/codeoracle/services/context_budget.py` (per memory `strict-layer-purity` — ContextBudget enforces a domain rule, not an LLM-specific thing):

```python
from codeoracle.models.llm import ChatMessage

class ContextBudgetExceeded(Exception): ...

class ContextBudgetService:
    """Enforces the platform-wide 16-24K token cap on LLM calls."""

    def __init__(self, max_tokens: int = 24_000):
        self._max_tokens = max_tokens

    def check(self, messages: list[ChatMessage], token_counter) -> int:
        total = sum(token_counter(m.content) for m in messages)
        if total > self._max_tokens:
            raise ContextBudgetExceeded(f"{total} > {self._max_tokens}")
        return total
```

Budget enforcement is **composed, not inherited** — a service-layer decorator wraps a raw provider (NFR D7a-1: a provider in `adapters/` must not import a service):

```python
# src/codeoracle/services/llm/budgeted_provider.py
class BudgetedLLMProvider(LLMProvider):
    def __init__(self, inner: LLMProvider, budget: ContextBudgetService):
        self._inner = inner
        self._budget = budget

    async def chat(self, messages, *, tools=None, structured_output=None):
        self._budget.check(messages, self._inner.count_tokens)   # before the network call
        return await self._inner.chat(messages, tools=tools, structured_output=structured_output)
    # count_tokens / model_name / max_context_tokens / aclose delegate to inner

def build_budgeted_provider(config: LLMConfig, budget: ContextBudgetService) -> LLMProvider:
    """The single blessed construction path — wraps the factory product so no
    composition root can hold an unbounded provider (fail-safe)."""
    return BudgetedLLMProvider(LLMProviderFactory(config).create(), budget)
```

Raw providers under `adapters/llm/providers/` know nothing about the budget, keeping `adapters/llm/` free of any upward import into `services/`. Enforcement is structural, not by convention: the only provider a composition root constructs is a wrapped one.

**Composition wiring**: `codeoracle.services.llm.build_budgeted_provider(config, budget)` is the single blessed path. Composition roots call it once and expose the wrapped `LLMProvider` via DI — the agent process in its FastAPI lifespan (Plan 10) and worker subprocesses in `workers/_base/wiring.py` (Plan 5), each constructing its own `ContextBudgetService` (workers don't share the agent's dependency graph; any future API LLM path uses the same helper). Other services — `VerifierService`, `TrustMatrixService`, `HandleResolverService` — never see the budget; it's an LLM-call-path concern only, never a domain concern.

### 5.6 Structural enforcement via ruff

In `pyproject.toml`:

```toml
[tool.ruff.lint.flake8-tidy-imports.banned-api]
"openai".msg     = "Import via codeoracle.adapters.llm — direct SDK imports banned outside providers/"
"anthropic".msg  = "Import via codeoracle.adapters.llm — direct SDK imports banned outside providers/"
"litellm".msg    = "Import via codeoracle.adapters.llm — direct SDK imports banned outside providers/"
"ollama".msg     = "Import via codeoracle.adapters.llm — direct SDK imports banned outside providers/"

[tool.ruff.lint.per-file-ignores]
"src/codeoracle/adapters/llm/providers/*.py" = ["TID251"]   # only allowed location
```

A PR adding `import openai` in `services/agent.py` fails CI. No vigilance required.

### 5.7 LLM testing strategy

Tests must not burn live-LLM credits on every PR, but live-provider drift is a real risk. Two-tier strategy:

| Tier | Provider | When run | Purpose |
|---|---|---|---|
| **Default** | `MockLLMProvider` (CODEORACLE_LLM_PROVIDER=mock) | Every PR, every push to main | Deterministic. Loads scripted responses from `tests/fixtures/llm_scripts/<test_name>.yaml`. Strict mode: unknown request → test fails (forces fixture maintenance). |
| **Live-LLM gate** | `OpenAIProvider` (or anthropic/vllm via matrix) | PR labeled `run-live-llm` OR nightly schedule | Validates real-provider behavior. Catches "we silently broke prompt formatting." Runs the same 9 slice integration tests. |

Fixture format example (`tests/fixtures/llm_scripts/test_explain_existing_symbol.yaml`):

```yaml
- request_fingerprint: "intent_parse:explain_symbol:VerifierService.verify"
  response:
    content: '{"intent":"explain_symbol","symbol":"codeoracle.services.verifier.service.VerifierService.verify"}'
    usage: { input_tokens: 84, output_tokens: 32 }
    finish_reason: stop

- request_fingerprint: "compose:VerifierService.verify"
  response:
    content: '{"statement":"...","citations":[...],"confidence":0.88}'
    usage: { input_tokens: 1320, output_tokens: 256 }
    finish_reason: stop
```

`request_fingerprint` is a stable hash of (system_prompt_template_id, user_message_template_id, key parameters) — not raw bytes — so tests don't break on cosmetic prompt edits.

Live-LLM gate failure does NOT block the PR (label-gated; advisory). Default test gate failure DOES block.

**Slice acceptance ceremony** runs the live-LLM gate manually before tagging the slice "shipped" (§8.7).

---

## 6. Evidence Verifier

The Verifier is the platform's mechanism for converting an LLM-produced Claim into a trust-bound Verdict.

### 6.1 Models — `src/codeoracle/models/evidence.py`

```python
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
    """LLM-produced citation. source_trust is NOT here — it's platform-computed by TrustMatrixService."""
    handle: EvidenceHandle
    excerpt: str

class Citation(BaseModel):
    """Post-TrustMatrix scoring. source_trust is platform-owned (the LLM cannot forge it)."""
    handle: EvidenceHandle
    excerpt: str
    source_trust: int = Field(ge=0, le=10)

class ResolvedCitation(BaseModel):
    citation: Citation
    resolved: bool
    matches_excerpt: bool
    actual_content: str | None

class DraftClaim(BaseModel):
    """What the LLM produces. Platform calls TrustMatrixService.score_each() to convert to Claim before VerifierService.verify()."""
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
    """Post-scoring claim — what the Verifier sees. Citations carry platform-computed source_trust."""
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
    status: VerdictStatus
    reason: str
    resolved_citations: list[ResolvedCitation]
```

### 6.2 VerifierService — `src/codeoracle/services/verifier/service.py`

```python
import asyncio
import unicodedata

from codeoracle.models.evidence import Claim, Verdict, VerdictStatus, ResolvedCitation, Citation
from codeoracle.services.handle_resolution import HandleResolverService
from codeoracle.services.handle_resolution.strategies.base import ResolverError


PROVEN_CONFIDENCE_FLOOR = 0.6   # below this, UNKNOWN regardless of citation resolution


def _normalize_for_comparison(text: str) -> str:
    """NFC unicode + LF EOLs + per-line rstrip + outer strip. Used to compare excerpt vs. live content."""
    nfc = unicodedata.normalize("NFC", text)
    lf = nfc.replace("\r\n", "\n").replace("\r", "\n")
    return "\n".join(line.rstrip() for line in lf.split("\n")).strip()


class VerifierService:
    """Validates Claim records. Returns PROVEN, UNKNOWN, or REFUTED.

    Abstains to UNKNOWN if confidence is below floor OR any citation fails to resolve.
    Returns REFUTED if any resolved citation's content drifts from its excerpt.
    Returns PROVEN only when confidence ≥ floor AND all citations resolve AND match.
    """

    def __init__(self, resolver: HandleResolverService):
        self._resolver = resolver

    async def verify(self, claim: Claim) -> Verdict:
        if claim.confidence < PROVEN_CONFIDENCE_FLOOR:
            return Verdict(
                status=VerdictStatus.UNKNOWN,
                reason=f"claim confidence {claim.confidence:.2f} below PROVEN floor {PROVEN_CONFIDENCE_FLOOR}",
                resolved_citations=[],
            )

        resolved = await asyncio.gather(*(self._resolve_one(c) for c in claim.citations))

        unresolved = [r for r in resolved if not r.resolved]
        if unresolved:
            return Verdict(
                status=VerdictStatus.UNKNOWN,
                reason=f"{len(unresolved)} of {len(resolved)} citations failed to resolve",
                resolved_citations=resolved,
            )

        mismatched = [r for r in resolved if not r.matches_excerpt]
        if mismatched:
            return Verdict(
                status=VerdictStatus.REFUTED,
                reason=f"{len(mismatched)} citations have content drift or tampering",
                resolved_citations=resolved,
            )

        return Verdict(
            status=VerdictStatus.PROVEN,
            reason=f"All {len(resolved)} citations resolved and match cached excerpts",
            resolved_citations=resolved,
        )

    async def _resolve_one(self, citation: Citation) -> ResolvedCitation:
        try:
            actual = await self._resolver.resolve(citation.handle)
            return ResolvedCitation(
                citation=citation,
                resolved=True,
                matches_excerpt=_normalize_for_comparison(actual) == _normalize_for_comparison(citation.excerpt),
                actual_content=actual,
            )
        except ResolverError:
            return ResolvedCitation(
                citation=citation,
                resolved=False,
                matches_excerpt=False,
                actual_content=None,
            )
```

**Why normalize**: a citation excerpt is created when the LLM composes — between then and verify time, the file may have been re-read with a different line-ending policy, or Sourcebot may return content with trailing whitespace differences. Without normalization, `test_explain_existing_symbol_returns_proven_with_citations` would be flaky against any file the editor touched. Normalization is one-way: REFUTED still fires for any *meaningful* drift (different characters, different code).

**Why a confidence floor**: without it, an LLM that returned `confidence=0.05` with one accidentally-resolvable citation would PROVEN, which is a structural lie. Floor at 0.6 (initial; raise later based on calibration data from slice usage).

### 6.3 Handle resolution — `src/codeoracle/services/handle_resolution/`

A **service** (not an adapter — see memory `strict-layer-purity`). It dispatches to per-handle-type strategies, each of which uses real adapters underneath.

`strategies/base.py`:

```python
from abc import ABC, abstractmethod
from codeoracle.models.evidence import EvidenceHandle

class ResolverError(Exception): ...
class UnsupportedHandleTypeError(ResolverError): ...

class HandleResolverStrategy(ABC):
    @abstractmethod
    async def resolve(self, handle: EvidenceHandle) -> str: ...
```

`service.py`:

```python
from codeoracle.models.evidence import EvidenceHandle, HandleType
from codeoracle.services.handle_resolution.strategies.base import (
    HandleResolverStrategy,
    UnsupportedHandleTypeError,
)

class HandleResolverService:
    """Dispatches handle resolution by handle type."""

    def __init__(self, strategies: dict[HandleType, HandleResolverStrategy]):
        self._strategies = strategies

    async def resolve(self, handle: EvidenceHandle) -> str:
        strategy = self._strategies.get(handle.type)
        if strategy is None:
            raise UnsupportedHandleTypeError(handle.type)
        return await strategy.resolve(handle)
```

`strategies/code_span.py` (representative):

```python
from codeoracle.adapters.sourcebot import SourcebotClient
from codeoracle.models.evidence import EvidenceHandle
from codeoracle.services.handle_resolution.strategies.base import (
    HandleResolverStrategy,
    ResolverError,
)

class CodeSpanResolver(HandleResolverStrategy):
    """Resolves file://path:start-end handles via Sourcebot."""

    def __init__(self, sourcebot: SourcebotClient):
        self._sourcebot = sourcebot

    async def resolve(self, handle: EvidenceHandle) -> str:
        path, line_range = self._parse_locator(handle.locator)
        try:
            return await self._sourcebot.fetch_span(
                path=path, line_range=line_range, snapshot=handle.snapshot_sha,
            )
        except Exception as e:
            raise ResolverError(f"Failed to fetch span {handle.locator}: {e}") from e

    @staticmethod
    def _parse_locator(locator: str) -> tuple[str, tuple[int, int]]:
        # parse "file://path/to/file:42-58"
        ...
```

`StubResolver` raises `UnsupportedHandleTypeError` for `TRACE_SPAN` and `DB_RECORD` — these handle types are not produced by the slice's intent classifier (integration-tested at §8.5).

### 6.4 Trust matrix — `src/codeoracle/services/trust_matrix.py`

Implements the source-trust ranking from architecture §16. The platform owns trust; the LLM never sets it. The agent's `score` node (§4.2) converts the LLM's `DraftClaim` into a `Claim` via `score_each()` before passing it to the Verifier.

```python
from codeoracle.models.evidence import (
    Citation, Claim, DraftCitation, DraftClaim, HandleType,
)


class TrustMatrixService:
    """Maps (HandleType, source_age_days) → 0..10 trust score, and converts DraftClaim → Claim."""

    _BASE_RANKS: dict[HandleType, int] = {
        HandleType.CODE_SPAN:     10,  # current code at the snapshot SHA
        HandleType.GRAPH_QUERY:    9,  # platform's own extracted graph
        HandleType.TRACE_SPAN:     9,  # observed runtime behavior
        HandleType.DB_RECORD:      7,  # schema-bound
        HandleType.DOC_PARAGRAPH:  3,  # docs decay
    }

    def trust(self, handle_type: HandleType, source_age_days: int) -> int:
        base = self._BASE_RANKS[handle_type]
        if source_age_days > 90:
            base = max(0, base - 2)
        return base

    def score_each(self, draft: DraftClaim, *, source_age_days: int = 0) -> Claim:
        """Convert LLM-produced DraftClaim into a Verifier-ready Claim by attaching platform-owned source_trust.

        source_age_days defaults to 0 (current snapshot only) in slice scope; multi-snapshot history
        in Phase 7+ will pass per-citation ages computed from snapshot metadata.
        """
        scored_citations = [self._score(dc, source_age_days) for dc in draft.citations]
        scored_contradictions = [self._score(dc, source_age_days) for dc in draft.contradictions]
        return Claim(
            statement=draft.statement,
            citations=scored_citations,
            confidence=draft.confidence,
            contradictions=scored_contradictions,
        )

    def _score(self, dc: DraftCitation, source_age_days: int) -> Citation:
        return Citation(
            handle=dc.handle,
            excerpt=dc.excerpt,
            source_trust=self.trust(dc.handle.type, source_age_days),
        )
```

The two-Pydantic-type design (DraftClaim ⊕ Claim) makes "LLM cannot forge trust" a *type-system invariant*: the Verifier's `verify()` signature accepts only `Claim`, so the only path to it is through `score_each()`. No runtime vigilance required.

**What `source_trust` is — and is NOT — used for**: it flows from `score_each()` through the Verifier into `Verdict.resolved_citations[].citation.source_trust`, surfaced in the API response for downstream **ranking** and **UI display** (e.g., showing higher-trust citations first). It does NOT gate the verdict status. The PROVEN/UNKNOWN/REFUTED decision depends only on (confidence floor, resolution success, excerpt match) — `source_trust` is informational metadata, not a gating criterion. Conflating these would create a hidden coupling where the Verifier's behavior changes based on TrustMatrix tuning; keeping them separate makes each service's contract minimal and auditable.

---

## 7. CI/CD pipeline

Per memory `no-separate-infra-prep-phase`: every gate runs on every PR from commit #1. No "Phase 0 infra prep."

### 7.1 Workflow files

```
.github/workflows/
├── ci.yml                       # ruff + ty + pytest + coverage + file-length
├── image-build.yml              # Docker build + Trivy scan (per-variant matrix)
├── security.yml                 # gitleaks + osv-scanner + semgrep
└── lockfile-refresh.yml         # weekly: uv lock --upgrade
```

### 7.2 `ci.yml` — primary PR gate

```yaml
name: CI
on:
  pull_request:
  push:
    branches: [main]

concurrency:
  group: ci-${{ github.ref }}
  cancel-in-progress: true

jobs:
  lint-and-type:
    runs-on: ubuntu-24.04
    steps:
      - uses: actions/checkout@v4
      - uses: astral-sh/setup-uv@v3
        with:
          enable-cache: true
          cache-dependency-glob: "uv.lock"
      - run: uv sync --frozen
      - run: uv run ruff check --output-format=github .
      - run: uv run ruff format --check .
      - run: uv run ty check
      - run: uv run python infra/scripts/check_file_length.py src/codeoracle tests

  test:
    runs-on: ubuntu-24.04
    env:
      CODEORACLE_LLM_PROVIDER: mock                     # default PR run never hits a live LLM endpoint
    services:
      postgres:
        image: postgres:18                      # see §3.6 — fallback to postgres:17 if AGE+PG18 incompatible
        env: { POSTGRES_PASSWORD: ci }
        ports: ["5432:5432"]
        options: >-
          --health-cmd pg_isready --health-interval 10s
          --health-timeout 5s --health-retries 5
    steps:
      - uses: actions/checkout@v4
      - uses: astral-sh/setup-uv@v3
      - run: uv sync --frozen
      - run: bash infra/scripts/bootstrap_db.sh
      - run: uv run pytest -m "not live_llm" --cov-fail-under=85
      - uses: codecov/codecov-action@v4
        with: { file: .cache/coverage/coverage.xml }

  test-live-llm:
    runs-on: ubuntu-24.04
    if: contains(github.event.pull_request.labels.*.name, 'run-live-llm')
    env:
      CODEORACLE_LLM_PROVIDER: openai
      CODEORACLE_LLM_OPENAI__API_KEY: ${{ secrets.OPENAI_API_KEY }}
    services:
      postgres:
        image: postgres:18
        env: { POSTGRES_PASSWORD: ci }
        ports: ["5432:5432"]
    steps:
      - uses: actions/checkout@v4
      - uses: astral-sh/setup-uv@v3
      - run: uv sync --frozen
      - run: bash infra/scripts/bootstrap_db.sh
      - run: uv run pytest -m live_llm
```

Key choices:
- `astral-sh/setup-uv@v3` — first-party. Handles install + cache.
- `--frozen` — fails on lockfile drift. No silent dependency shifts between dev and CI.
- `postgres:18` — real PG18 service. Integration tests run against actual database (mocks would mask AGE/pgvector behavior).
- `concurrency.cancel-in-progress` — newer pushes cancel stale runs. Saves CI minutes.

### 7.3 `image-build.yml` — Docker + Trivy

Matrix over the 5 image variants (api, agent, mcp, worker-joern, worker-tools); one Dockerfile with `EXTRAS` and `NEEDS_JDK` build args produces all five. Each gets its own Trivy scan with `severity: HIGH,CRITICAL exit-code: 1`. SARIF uploaded to GitHub code-scanning UI.

### 7.4 `security.yml` — secrets + lockfile + SAST

- `gitleaks` on full history every PR (`fetch-depth: 0`)
- `osv-scanner --lockfile=uv.lock` — catches lockfile vulns Trivy might miss
- `semgrep` with `p/python`, `p/owasp-top-ten`, `p/secrets`, plus custom rules in `infra/security/semgrep/codeoracle-custom.yml`

### 7.5 Astral toolchain configs — all in `pyproject.toml`

Single config root (no `ruff.toml`, no `mypy.ini`, no separate dotfiles):

```toml
[project]
name = "codeoracle"
requires-python = ">=3.14"
dynamic = ["version"]

[tool.uv]
package = true  # build+install the project itself so `codeoracle.*` is importable; `managed` defaults true and is orthogonal

[tool.ruff]
target-version = "py314"
line-length = 100
src = ["src", "tests"]

[tool.ruff.lint]
select = ["E","F","W","I","N","UP","B","S","C90","TID","RUF","ASYNC","ANN","PT"]
ignore = ["ANN101", "ANN102", "S101"]

[tool.ruff.lint.mccabe]
max-complexity = 10

[tool.ruff.lint.per-file-ignores]
"tests/**/*.py" = ["S101", "ANN", "D"]
"src/codeoracle/adapters/llm/providers/*.py" = ["TID251"]

[tool.ruff.lint.flake8-tidy-imports.banned-api]
"openai".msg     = "Import via codeoracle.adapters.llm"
"anthropic".msg  = "Import via codeoracle.adapters.llm"
"litellm".msg    = "Import via codeoracle.adapters.llm"
"ollama".msg     = "Import via codeoracle.adapters.llm"

[tool.ty.environment]
root = ["src"]

[tool.ty.src]
respect-ignore-files = true

[tool.ty.rules]
all = "error"

[tool.pytest.ini_options]
testpaths = ["tests"]
asyncio_mode = "auto"
addopts = "-ra --strict-markers"
markers = ["integration", "characterization", "live_llm"]
```

### 7.6 File-length enforcement

`infra/scripts/check_file_length.py` — 30-line Python script. Soft warn at 500, hard fail at 700. Wired into both pre-commit and CI. Per memory `project-code-quality-bar`.

### 7.7 Pre-commit (mirror of CI)

```yaml
# .pre-commit-config.yaml
repos:
  - repo: https://github.com/astral-sh/ruff-pre-commit
    rev: v0.8.0
    hooks: [{id: ruff, args: [--fix]}, {id: ruff-format}]
  - repo: https://github.com/astral-sh/uv-pre-commit
    rev: 0.5.0
    hooks: [{id: uv-lock}]
  - repo: local
    hooks:
      - id: check-file-length
        name: Enforce 700-line file cap
        entry: python infra/scripts/check_file_length.py src/codeoracle tests
        language: system
        pass_filenames: false
      - id: ty
        name: Type-check with ty
        entry: uv run ty check
        language: system
        types: [python]
        pass_filenames: false
```

### 7.8 Renovate config — `.github/renovate.json5`

Grouped bumps (Astral toolchain together, FastAPI ecosystem together, LLM SDKs together), patch-only auto-merge, weekly lockfile maintenance, major-bump review-required.

### 7.9 Secret handling across environments

Every LLM provider that isn't `mock` needs API credentials. Secrets policy by environment:

| Env | Source | Path to running slice |
|---|---|---|
| **Dev (local)** | `.env` file at repo root (gitignored). `.env.example` is the template; `gitleaks` rejects any committed real key. Loaded via `pydantic-settings` automatically. | `cp .env.example .env && $EDITOR .env && uv run python -m codeoracle.api` |
| **CI (PR runs)** | NONE. Default PR test job sets `CODEORACLE_LLM_PROVIDER=mock` so no provider secrets are required. | (no key needed — see §7.2) |
| **CI (live-LLM gate)** | `${{ secrets.OPENAI_API_KEY }}` injected as `CODEORACLE_LLM_OPENAI__API_KEY` only on jobs gated by the `run-live-llm` PR label or nightly schedule. | (see `test-live-llm` job in §7.2) |
| **Prod (Phase 7+)** | External secret manager (1Password / AWS Secrets Manager / HashiCorp Vault — decision deferred). Workload identity binding rather than baked-in keys. | Out of slice scope. |

Defensive layers:
- `gitleaks` runs on full history every PR (§7.4) — catches accidentally committed keys.
- `OpenAIProviderConfig.api_key` is `SecretStr` (as are the Anthropic/vLLM provider keys), matching Plan 8's `SourcebotConfig.api_key`: repr-masking is the primary defense against leakage when a config is nested in a log payload / exception / `model_dump()` / span. The `core/logging.py` `structlog` processor that strips top-level `api_key`/`secret`/`token` event keys is a second layer.
- `.env.example` contains only placeholder values (`sk-fake-replace-me`); `.env` is in `.gitignore` and `.dockerignore`.

### 7.10 Deliberately deferred CI checks

- Multi-Python-version matrix (pin 3.14+ only)
- Mutation testing
- Performance regression tests (no SLO yet)
- Container signing (cosign/sigstore) — Phase 7+
- License-compliance gate — Phase 6+ (need policy doc first)

These slot in as additional workflow files when needed.

---

## 8. The vertical slice

### 8.1 The slice query

> "Explain function `<fully-qualified-symbol>` — what does it do, who calls it, and what evidence supports this?"

API:

```http
POST /v1/query HTTP/1.1
Content-Type: application/json

{
  "type": "explain_symbol",
  "symbol": "codeoracle.services.verifier.service.VerifierService.verify",
  "corpus": "self",
  "snapshot_sha": null
}
```

Response (illustrative — exact citations, snapshot SHA, and excerpts depend on the current state of `src/codeoracle/` at slice time; do not match this byte-for-byte in tests):

```json
{
  "trace_id": "01J...",
  "verdict": {
    "status": "PROVEN",
    "reason": "All 4 citations resolved and match cached excerpts at snapshot abc1234.",
    "resolved_citations": [/* per-citation outcomes */]
  },
  "claim": {
    "statement": "VerifierService.verify takes a Claim, resolves each citation's handle to its current content via HandleResolverService, and returns a Verdict — PROVEN if all resolve and match, UNKNOWN if any fail to resolve, REFUTED if any resolved-but-mismatched.",
    "citations": [
      {"handle": {"type": "code_span", "locator": "file://src/codeoracle/services/verifier/service.py:14-38", "snapshot_sha": "abc1234"}, "excerpt": "...", "source_trust": 10},
      {"handle": {"type": "graph_query", "locator": "cypher://MATCH (c:Symbol {snapshot_sha:$snapshot_sha})-[:CALLS]->(t {fqn:'...verify'}) RETURN c.fqn AS result", "snapshot_sha": "abc1234"}, "excerpt": "[codeoracle.api.routes.query.handle_explain_symbol]", "source_trust": 9},
      {"handle": {"type": "doc_paragraph", "locator": "file://src/codeoracle/services/verifier/service.py:15-20", "snapshot_sha": "abc1234"}, "excerpt": "Validates Claim records...", "source_trust": 2},
      {"handle": {"type": "code_span", "locator": "file://tests/integration/test_verifier.py:42-71", "snapshot_sha": "abc1234"}, "excerpt": "...", "source_trust": 8}
    ],
    "confidence": 0.88,
    "contradictions": []
  }
}
```

### 8.2 Per-component slice responsibility

This table is broader than §4's "9 services" — it includes the 9 deployed processes AND their internal components (db repositories, domain services, handle-resolution strategies) exercised by the slice query.

| # | Component | Slice responsibility | Real or stub |
|---|---|---|---|
| 1 | `api` | Accept POST, route to agent, return Claim/Verdict | Real |
| 2 | `mcp` | Expose `explain_symbol` MCP tool; identical logic | Real |
| 3 | `agent` (LangGraph) | Plan + execute: intent → graph + embed + traces → compose → verify | Real |
| 4 | `llm_provider` | Intent parse + final compose (2 calls per query) | Real (OpenAI dev default) |
| 5 | `db/repositories/graph` | AGE Cypher: `CALLS` edges in/out of target symbol | Real |
| 6 | `db/repositories/embeddings` | pgvector ANN search for top-k related docs/tests | Real |
| 7 | `workers/joern_indexer` | Per-file Joern extraction; results into AGE | Real |
| 8 | `workers/scip_indexer` | Per-file SCIP-python extraction | Real |
| 9 | `workers/opengrep_runner` | Textual cross-check of graph results | Real |
| 10 | `sourcebot` HTTP (service, not a worker) | Indexed once at container start against `file://` snapshot mounts; serves span fetch via Plan 8's `SourcebotClient` | Real |
| 11 | `workers/embed_indexer` | Embed every file (vLLM) → pgvector | Real |
| 12 | `workers/traces_ingest` | Pull platform's own self-traces from Tempo | Real |
| 13 | `services/verifier` | Resolve every citation, classify verdict | Real |
| 14 | `services/handle_resolution` | Strategy per handle type | CODE_SPAN, GRAPH_QUERY, DOC_PARAGRAPH = real; TRACE_SPAN, DB_RECORD = `StubResolver` (raises) |
| 15 | `services/indexing` | Full and incremental reindex orchestration | Real (see §8.4) |
| 16 | `services/trust_matrix` | Score citations by handle type + age | Real |
| 17 | `services/context_budget` | Enforce 16-24K token cap on all LLM calls | Real |

### 8.3 Test corpus

**Primary and only**: `src/codeoracle/` itself, at the current HEAD SHA.

Rationale:
- Owned code, no external-repo licensing or access-boundary concerns.
- ~3K LOC at slice time. Joern indexes in <5min, full extraction footprint <500MB.
- Dogfood property: if the platform can't explain itself, the pitch collapses.
- Zero dependency on external target repositories or audit snapshot directories.

Out of slice corpus: foreign Python projects, non-Python codebases.

### 8.4 Incremental indexing (in scope — load-bearing)

Per memory `dont-defer-load-bearing-architecture`: the slice ships with both full and incremental reindex. Incremental is not a deferral target because removing it would force a rewrite of the extraction pipeline at non-toy scale.

| Capability | Implementation |
|---|---|
| File-level extraction throughout | Joern per-file CLI, SCIP-python per-file native, OpenGrep stateless, Sourcebot per-file, embeddings per-chunk |
| `codeoracle index --since <sha>` / `POST /v1/reindex` | Accepts a base SHA (defaults to last applied snapshot) |
| Compute changed-file list | `git diff --name-only <base> HEAD` (plus untracked files when working tree is dirty) |
| Discover dependents | Cypher per the §3.7 edge contract: two UNION'd patterns — `:CALLS` traversed via `:Symbol` nodes, and `:IMPORTS` File-to-File. Full query below the table. |
| Re-process changed + dependents | Same per-file extraction pipeline; per-extractor outcome recorded on Snapshot |
| Atomic per-changeset graph mutation | AGE transaction: DELETE then INSERT per file, commit-or-rollback |
| Snapshot tracking | `Snapshot(sha, parent_sha, changed_files, status, extractor_results, error, applied_at)` in PG. `SnapshotStatus = pending \| applying \| applied \| failed`. `extractor_results: dict[str, ExtractorOutcome]` where `ExtractorOutcome = success \| skipped \| failed` per worker. `error: str \| None` captures the failing extractor's message — recoverable without leaving the index in an indeterminate state. |
| **Snapshot SHA when working tree is dirty** | `<head-sha>+wt<sha256-of-tracked-changes>[12]` (e.g. `abc1234+wt5e7a9f0c8b3d`). Computed by hashing the concatenation of (path, content) for every git-tracked changed file. Allows edits-then-reindex without forcing a commit (dogfooding requirement). The CLI logs the SHA actually used; a subsequent `git commit` followed by `codeoracle index` resolves to a clean SHA naturally. |
| **Single-flight mutex** | `pg_advisory_lock(0x46AE0001)` (constant lock-id `CODEORACLE-REIDX-1`) acquired by both `codeoracle index` CLI and `POST /v1/reindex` before any extractor runs; released by `pg_advisory_unlock` in the `finally` block. Cross-process safe (single Postgres) — second concurrent caller blocks (configurable via `pg_try_advisory_lock` for fail-fast behavior). Advisory locks are session-scoped (per-connection): when the holding connection closes — including on process crash — Postgres releases the lock automatically. No stale-lock recovery code required. |

Dependent-discovery Cypher (per §3.7's `:Symbol` / `:CALLS` / `:IMPORTS` schema; `:USES` is out of scope per §3.7):

```cypher
-- Dependents of $changed_files in snapshot $sha:
-- (1) files defining a caller of a definition defined in a changed file
MATCH (changed:File {snapshot_sha: $sha})-[:DEFINES]->(target:Symbol)
      <-[:CALLS]-(caller_def:Symbol)<-[:DEFINES]-(dep:File)
WHERE changed.file_path IN $changed_files AND NOT dep.file_path IN $changed_files
RETURN DISTINCT dep.file_path AS path

UNION

-- (2) files that import a changed file
MATCH (dep:File)-[:IMPORTS]->(changed:File {snapshot_sha: $sha})
WHERE changed.file_path IN $changed_files AND NOT dep.file_path IN $changed_files
RETURN DISTINCT dep.file_path AS path
```

Pattern (1) traverses the Symbol-level `:CALLS` graph (post-linking-pass per §3.7): a changed file → its definitions → callers of those definitions → the files those callers belong to. Pattern (2) catches file-level dependencies that have no call edge yet (e.g., a freshly added `import foo` with no usage). Both filter out files already in `$changed_files` since they're being re-processed anyway.

The `File`-node path accessor is `file_path`, not `path`: `file_path` is the per-file partition key the parser writes (§3.7) and `replace_file_subgraph` deletes by, shared by every node in a file's subgraph (File and Symbol alike). `File` carries no bare `path` property, so reading `changed.path` / `dep.path` would bind NULL and silently return zero dependents. The `AS path` alias is only the *output column* name and is unrelated to the stored property — do not "simplify" the accessor to `.path`.

Deferred conveniences (NOT load-bearing):
- **FS-watcher** (inotify/fswatch auto-trigger) — Phase 8
- **Multi-snapshot history + time-travel queries** — Phase 7+
- **Concurrent reindex requests** — Phase 8 (slice is single-flight via the pg_advisory_lock above; "concurrent" means parallel non-overlapping changesets, which requires per-file or per-path locks)

### 8.5 Acceptance criteria

The slice ships when **all** of the following pass.

#### Functional gates — 5 integration tests

```python
# tests/integration/test_slice_explain_symbol.py

def test_explain_existing_symbol_returns_proven_with_citations():
    """Query an existing function → PROVEN with ≥3 citations spanning ≥2 handle types."""

def test_explain_deleted_symbol_returns_unknown():
    """Query a symbol that doesn't exist in graph → UNKNOWN, reason='symbol not found'."""

def test_explain_tampered_excerpt_returns_refuted():
    """Inject a citation whose excerpt no longer matches Sourcebot's content → REFUTED."""

def test_mcp_explain_symbol_matches_api_response():
    """Same query via MCP and HTTP API → byte-equal Claim payloads (modulo trace_id)."""

# tests/integration/test_slice_reindex.py

def test_reindex_endpoint_produces_new_snapshot():
    """POST /v1/reindex → returns {snapshot_sha: <new>}; queries against new SHA reflect file edits."""
```

```python
# tests/integration/test_slice_incremental.py

def test_incremental_reindex_matches_full_reindex():
    """Characterization: after the same series of edits, incremental and full reindex
    produce byte-equal graph state (sorted-canonical comparison)."""

def test_incremental_reindex_invalidates_dependents():
    """Rename a symbol in file A. Reindex incrementally. Callers in file B should now
    show that reference as unresolved (not still pointing to the old name)."""

def test_incremental_reindex_performance():
    """Single-file edit → reindex completes in < 10s p95.
    No-op edit (whitespace only) → reindex completes in < 2s p95."""
```

```python
# tests/integration/test_intent_classifier_handle_types.py

def test_intent_classifier_never_produces_unsupported_handle_types():
    """For every symbol in src/codeoracle/, the classifier's output does NOT include
    TRACE_SPAN or DB_RECORD handle types (which would StubResolver-raise in v1)."""
```

#### Architectural invariants (CI-enforced)

1. `ruff check` — zero violations
2. `ruff format --check` — zero diffs
3. `ty check` — zero errors (strict mode)
4. `check_file_length.py` — zero files >700 lines
5. `pytest --cov-fail-under=85` — coverage ≥85%
6. `trivy image` on all 5 variants — zero fixable HIGH/CRITICAL
7. `osv-scanner --lockfile=uv.lock` — zero unfixed vulns
8. `semgrep` on `src/codeoracle/` — zero findings on `p/owasp-top-ten` + `p/secrets`

#### Observability

9. Every `/v1/query` emits one OTel root span with child spans: `intent.parse`, `graph.query`, `embed.query`, `traces.query`, `llm.compose`, `verifier.verify`, `resolve.<handle_type>` (per citation).
10. Slice's own traces are ingested back into the platform via `workers/traces_ingest` — closing the loop.
11. Verifier emits one structured log line per citation resolution outcome (level=info on resolve, warn on unresolved, error on excerpt mismatch) on a dedicated audit channel whose floor is fixed at INFO independent of the global `log_level`, so the line survives at any production verbosity (the global `make_filtering_bound_logger(log_level)` would otherwise drop the info/warn outcomes once an operator raises `log_level` to WARNING/ERROR).

#### Performance (measured, not gated)

| Operation | Target |
|---|---|
| Full reindex (cold, 3K LOC) | < 3min p95 |
| Incremental reindex (1 file changed) | < 10s p95 |
| Incremental reindex (no-op) | < 2s p95 |
| Query (median) | < 8s |
| Query (p99) | < 30s |

Recorded as Codecov-like CI artifacts; not gate blockers at slice stage. (The Joern extractor boots a JVM per file by design — stateless, crash-safe; cold-reindex throughput is scaled by running N `joern_indexer` worker replicas on the shared consumer group, so the <3min budget is met at slice scale (~3K LOC). A persistent Joern server is a deferred post-slice optimization should a production-scale corpus breach the budget — see Plan 5 Task 11 `JoernClient` docstring.)

### 8.6 Explicit non-goals

| Out of scope | Returns in |
|---|---|
| Multi-tenancy / auth | Phase 7 (production deploy) |
| Web UI | Phase 6+ |
| Foreign-language corpora (JS, Go, Java) | Phase 6 (extractor expansion) |
| FS-watcher (auto-reindex on save) | Phase 8 |
| Multi-snapshot history / time-travel queries | Phase 7+ |
| Concurrent reindex requests | Phase 8 |
| Production K8s deploy | Phase 9 |
| LLM fine-tuning | Maybe never; depends on perf gap |
| Distributed worker scaling (Celery groups, Ray) | Phase 8 |
| `TRACE_SPAN` and `DB_RECORD` handle types | Slice +1 |
| Multi-model voting / ensemble | Slice +2 |

### 8.7 Slice acceptance ceremony

Slice is "shipped" when:

1. The 9 integration tests from §8.5 pass against `docker compose up -d` of the full stack.
2. CI green on the slice PR (all gates from §8.5).
3. **Live demo (PROVEN path)**: developer runs MCP `explain_symbol` against `codeoracle.services.verifier.service.VerifierService.verify`, sees a Claim with ≥3 citations and `verdict.status == "PROVEN"`.
4. **Failure demo (UNKNOWN path)**: developer runs MCP `explain_symbol` against `codeoracle.services.deleted.NotARealService`, sees `verdict.status == "UNKNOWN"` with reason "symbol not found in graph at snapshot abc1234".
5. **Live-edit demo**: developer edits one line in `codeoracle/services/verifier/service.py`, runs `codeoracle index --since HEAD~1`, sees reindex complete in <10s, runs the same MCP query, response reflects edit at new snapshot SHA. Then runs `codeoracle index --full`; characterization test passes.

Demo scripts live under `scripts/demos/`; asciinema recordings are committed under `tests/fixtures/acceptance-recordings/`.

---

## 9. Open questions and future decisions

These do NOT block slice work; they're flagged for explicit decisions later.

1. **vLLM vs Ollama as the production self-hosted target.** Architecture defaults to vLLM+Qwen3.6. Ollama is simpler operationally but slower throughput. Decide based on production-load testing in Phase 6.
2. **Graph-diff cost as corpus grows.** Cypher dependency discovery (§8.4) is O(changed_files × references). At 500K-LOC scale, may need a denormalized "file → dependent files" projection. Monitor; introduce projection if needed.
3. **Embedding model choice — DECIDED 2026-05-22.** OpenAI `text-embedding-3-small` (1536-dim). Pinned because: (a) consistent with the OpenAI dev-default established in §5.4 — one provider serves chat and embeddings in dev; (b) re-indexing every vector is a one-way door, so the dimension must be fixed *before* Plan 3 codes the `pgvector` migration; (c) production migration to a self-hosted model is a deliberate decision deferred to post-slice — the 1536-dim self-hosted landscape is genuinely sparse (`nomic-embed-text-v1.5` is natively 768-dim with Matryoshka-truncation support; `bge-m3` is natively 1024-dim; few open-weights models are natively 1536-dim), so a future swap may require MRL-truncation tooling or accepting a one-time re-index. The column dimension must be pinned now regardless, and 1536 is the most portable choice across hosted and self-hosted options that the dev-default provider supports out of the box. The earlier `BAAI/bge-large-en-v1.5` (1024-dim) recommendation is superseded. Plan 3's pgvector column type is `vector(1536)`.
4. **Confidence-floor calibration.** §6.2 sets `PROVEN_CONFIDENCE_FLOOR = 0.6` initially. Once slice usage produces real (verdict, ground-truth) pairs, recalibrate (likely upward — 0.7-0.8 is common in production calibration). Tracked, not blocking.
5. **Compaction / archival of old snapshots.** Single-snapshot in slice; once multi-snapshot lands, need a retention policy. Likely "keep last N + last per-day for M days."
6. **Production secret manager choice.** §7.9 defers prod secret-store selection (1Password / AWS Secrets Manager / Vault) to Phase 7. Decision driven by chosen production-deploy target.

---

## 10. Target architecture: primitive catalog, orchestrator, and intent classifier

**What this section is and what it is not.** This section describes the **target** architecture — the shape the platform converges on across Plans 14-18. **It is NOT the slice.** The slice (Plans 1-13) implements §10's *foundation pattern* — a single deterministic intent dispatch, platform-fetched evidence, LLM composition, byte-binding verification — but ships only **one** primitive: `explain_symbol`. The slice does not include the orchestrator, the intent classifier, or the PrimitiveRegistry as runtime components; those land in Plan 14+.

Read this section as direction-of-travel. It pins the contracts that Plans 14-18 will inherit so the slice's interfaces (Plan 11 API, Plan 12 MCP, Plan 13 E2E) do not lock in shapes that block the orchestration layer.

### 10.1 Slice scope vs target scope

| Layer | Slice (Plans 1-13) | Target (Plans 14-18+) |
|---|---|---|
| Intent | `Query.type: Literal["explain_symbol"]` — typed at API boundary, no LLM classifier | Free-text NL → LLM intent classifier → typed `OrchestrationPlan` |
| Retrieval | One agent, six nodes, one plan (`[CODE_SPAN, GRAPH_QUERY]`) | N primitives, each with its own resolver; orchestrator LLM loop dispatches them in sequence |
| Composition | Compose node selects evidence by index, writes prose | Same — composition pattern unchanged |
| Verification | Verifier round-trips every cite | Same — verifier unchanged; works on any handle type |

The slice's discipline (deterministic primitives, platform-owned bytes, LLM-selects-not-authors) IS the target architecture's foundation. The target adds an orchestrator above and more primitives alongside.

### 10.2 PrimitiveRegistry

Replace fixed `Literal` intent types with an extensible **PrimitiveRegistry**. Each primitive declares:

```python
class Primitive(Protocol):
    name: str  # e.g., "find_callers"
    input_schema: type[BaseModel]  # Pydantic input
    output_handle_types: tuple[HandleType, ...]  # which handles it can produce

    async def resolve(
        self, input: BaseModel, deps: AgentDependencies
    ) -> EvidenceBundle: ...
```

A `PrimitiveRegistry` keeps a dict of primitives by name. Both the LLM intent classifier and the orchestrator loop dispatch through the registry — the registry IS the platform/LLM boundary.

The `Primitive` shape pinned here is contract-level: Plans 14-18 code against this exact Protocol. The slice's `explain_symbol` becomes the first registry entry (a 1-2 task refactor in Plan 14, not slice work); Plans 15-18 add the rest. The minimum primitive set covering common codebase-question shapes is enumerated in §10.6.

### 10.3 Orchestrator agent — LLM loop driver

A second LangGraph agent (separate from the slice's single-intent agent, which becomes one primitive within the registry):

```text
Free-text question OR structured intent
    ↓
[intent_classifier node]  LLM → initial primitive queue
    ↓
[orchestrator loop node]  (bounded by step budget, default 8)
    pop next primitive from queue
    dispatch via PrimitiveRegistry → typed EvidenceBundle
    LLM examines accumulated bundle, decides:
        - enqueue more primitives, OR
        - stop (sufficient evidence), OR
        - give up with diagnostic
    ↓
[compose node]            LLM authors prose, selects evidence by index
    ↓
[verify node]             Verifier round-trips every cite (handle-type agnostic)
    ↓
QueryResult
```

Bounds:

- **Step budget** — default 8 dispatches per query. This default is a placeholder pending post-slice calibration against measured compound-query traces (typical `find_callers` + `explain_symbol` chains observed in early usage); it is configurable per intent class via `OrchestratorConfig`. The orchestrator hard-caps at the configured value and surfaces a "budget exceeded" diagnostic — it never silently truncates.
- **Token budget** — composition stays single-shot (no retry loops in compose; that's a post-slice hardening item).
- **Registry-only dispatch** — orchestrator cannot invoke anything that isn't a registered primitive. No raw tool calls.

### 10.4 Intent classifier — front door

A small LLM call at the top of the orchestrator agent translates free-text NL into an initial `OrchestrationPlan`:

```python
class OrchestrationPlan(BaseModel):
    primitives: list[PrimitiveInvocation]   # initial queue
    confidence: float                        # classifier's confidence
    unsupported_reason: str | None           # populated when no primitives match
```

If the classifier cannot map the question to any registered primitive, it returns `unsupported_reason` and the orchestrator emits an honest `UNKNOWN` with a diagnostic listing supported intent categories. The vocabulary boundary is *named*, not silent.

API and MCP callers may bypass the classifier by supplying a `{type, ...}` structured query — Plan 11 (API) and Plan 12 (MCP) design those paths.

**Design trade-off — two LLMs vs one.** A single LLM could classify on its first call and drive the loop on subsequent calls (the standard ReAct-style pattern). CodeOracle splits the two for prompt-size discipline (classifier prompt is short and focused on registry vocabulary; orchestrator prompt carries the accumulated evidence bundle) and per-stage observability (each LLM call is independently logged, scored, and rate-limited). Cost is roughly 2× LLM calls per query at the orchestration layer — small relative to compose-node tokens. Re-evaluate if measured cost or latency becomes a bottleneck post-slice.

### 10.5 LLM role boundaries (re-affirms §1.4)

The LLM is invoked at three named places, each with bounded authority. NONE of them fetches bytes or authors citations:

| Role | Allowed | Forbidden |
|---|---|---|
| Intent classifier (§10.4) | Pick primitives from registry; reject ambiguous queries with diagnostic | Invent primitives; bypass registry |
| Orchestrator (§10.3) | Pick **next** primitive given accumulated bundle | Author bytes; skip a dispatched primitive's evidence; exceed step budget |
| Composer (existing slice node) | Select evidence by index, write prose, cite by index | Author byte content; emit uncited claims; modify excerpts |

What no LLM ever does: fetch bytes; author citations; retry an unresolved handle; exceed the step budget.

### 10.6 Planning roadmap — Plans 14-18

The slice (Plans 1-13) ships one primitive (`explain_symbol`) end-to-end. Subsequent plans grow the primitive catalog and add the orchestration shell:

| Plan | Scope | Adds |
|---|---|---|
| 14 | Orchestrator agent + PrimitiveRegistry promotion | LLM loop driver; promotes Plan 10's hardcoded plan to a registry entry |
| 15 | Doc + test extractors | `find_specs_for(concept)` and `find_tests_for(target)` primitives |
| 16 | DB-schema + frontend extractors | `find_db_schema`, `find_db_query_patterns`, `find_frontend_action` primitives |
| 17 | Trace primitives | `find_traces_for` primitive (builds on Plan 7's OTel ingestion) |
| 18 | Data-flow primitives | `find_writers_of`, `find_readers_of`, `trace_upstream` primitives (extends §3.7 with `:DB_WRITES` / `:DB_READS` edges) |

Plans 14-18 are explicitly out of slice scope. They are listed here so Plans 11-13 (API, MCP, E2E) design their interfaces to accommodate the future orchestration layer without rework — Plan 11 accepts both structured `{type, ...}` queries and free-text orchestrator-routed input; Plan 12 exposes each registered primitive as its own MCP tool (the synthesis-tool integration path described in §1.4).

---

## 11. References

### 11.1 Inside this repo

- `docs/architecture.md` — production architecture (the WHAT)
- `docs/roadmap.md` — phased rollout (the WHEN)
- `docs/investigation-brief.md` — platform pitch (the WHY)
- `docs/postgres-decision.md` — PG18 + AGE + pgvector justification
- `docs/stack-research.md` — comparative stack analysis
- `CLAUDE.md` — session-entry guidance + platform conventions

### 11.2 Memories that govern this work

- `project-llm-provider-agnostic` — LLM access via single interface
- `project-dev-llm-provider-openai` — OpenAI is dev default
- `project-oop-preference` — classes + DI over free functions
- `project-code-quality-bar` — 500/700 line cap, no slop/debt
- `strict-layer-purity` — models/, services/, adapters wrap external only
- `dont-defer-load-bearing-architecture` — deferral test is "forces a rewrite?" not "is hard?"
- `no-separate-infra-prep-phase` — CI/CD from commit #1, not Phase 0
- `research-match-kind-not-domain` — match deployable-app patterns, not library patterns
- `ai-velocity-timing` — estimate in hours/days/sessions, not weeks/months

### 11.3 External reference architectures studied

Deployable multi-service AI applications (the right KIND):
- Onyx (formerly Danswer) — closest analog; structured retrieval + verification
- OpenHands — agent orchestration
- LangFuse — observability for LLM apps
- Khoj — local + hosted LLM swap
- Phoenix — trace-first LLM apps
- Letta — `__main__.py` per process pattern

---

**End of design spec. Ready for review.**
