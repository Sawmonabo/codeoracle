# Snapshot Extractors (SCIP, OpenGrep, Embed) + Embeddings Repository Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Pattern-replicate Plan 5's `WorkerConsumer[T]` framework for three additional snapshot extractors (SCIP, OpenGrep, embed), extend the orchestrator with a `project` extraction mode (single message → N per-file outcome rows) so SCIP's project-scoped tool fits the per-file barrier, and ship the `EmbeddingsRepository` + `CodeReferencesRepository` that consume their writes. Result: a single `Reindex` for an CODEORACLE-managed snapshot fans out to four workers (joern + scip + opengrep + embed), all observed by `SnapshotFileOutcome`, all converging on `status=APPLIED` through Plan 5's barrier. (Plan 3's `SnapshotStatus` enum has no `READY` member — the terminal success state is `APPLIED`.)

**Architecture:** Three new vertical slices, each following Plan 5's four-file consumer template (`message.py` + `tasks.py` + `consumer.py` + `__main__.py`). Three new adapters (`adapters/scip/`, `adapters/opengrep/`, `adapters/llm/embeddings.py`) wrapping the underlying external tools. One framework extension: `ExtractorMode = Literal["per_file", "project"]` lets the orchestrator's `_INFO_BY_KIND` dispatch SCIP differently (one project-message instead of N file-messages) without touching the per-file barrier semantics. One new ABC: `EmbeddingsProvider` (separate from `LLMProvider`, since chat-completion and embeddings are distinct services in OpenAI/vLLM and need independent rate limits + endpoints). Two new repositories backed by Plan 3's `embeddings` table and a new `code_references` table.

**Tech Stack:** Python 3.14, asyncio, SQLAlchemy 2.0 async ORM, pgvector (Plan 3's `vector(EMBEDDING_DIM)` column), Apache AGE openCypher (Plan 5's `:Symbol` plus new `:Definition` + `:Reference` labels), Valkey 7 streams (Plan 5's `WorkerConsumer` + Plan 5's `SnapshotFileOutcome` barrier), `scip-python` (preflight-verified in Plan 2 §3.6), `opengrep` (CLI smoke test added inline — Plan 2 didn't cover it), `vllm` (OpenAI-compatible embeddings endpoint, falls back to OpenAI for dev), pytest + testcontainers + alembic.

**Implementation order:** Frame extensions → schemas → repositories → adapters → workers → integration. SCIP comes first inside the worker block (Tasks 9 + 11) because it's the project-mode anchor; OpenGrep and embed are per-file replications of Plan 5's Joern template.

---

## Plan Inheritance

This plan **inherits** the following from prior plans. Do not redefine; the names listed are stable contracts.

**From Plan 5 (`docs/superpowers/plans/2026-05-21-worker-framework-and-joern-vertical.md`):**

- `WorkerConsumer[TMessage]` ABC at `src/codeoracle/workers/_base/consumer.py` — XREADGROUP polling loop, XACK on success, retry/DLQ via `RetryPolicy`. ClassVars: `stream`, `group`, `message_type`. Constructor takes `queue_client`, `retry_policy`, `consumer_name`, `stop_event`. (Underscore-prefix package name `_base/` is intentional — flags this as a framework internal, not an importable namespace from outside `workers/`.)
- `WorkerMessage` Pydantic base at `src/codeoracle/workers/_base/message.py` — required `batch_id: str` (equals `snapshot_sha` for indexing flows so the barrier can query by batch).
- `RetryPolicy` **at the SAME file** `src/codeoracle/workers/_base/message.py` — `max_attempts: int` (the DLQ stream is NOT a `RetryPolicy` field — `WorkerConsumer.dead_letter` derives it via `dlq_for(stream)`). Plan 5 deliberately co-locates `WorkerMessage` + `RetryPolicy` in one module (there is NO separate `retry.py`) because both are immutable message-shape primitives the consumer constructs at startup.
- `WorkerError` hierarchy at `src/codeoracle/core/exceptions.py` — `WorkerError` (root) → `TransientWorkerError` (retry) / `TerminalWorkerError` (no retry, route to DLQ). Plan 5 introduced `JoernExtractionError(TerminalWorkerError)` — this plan adds three siblings (Task 1).
- `ValkeyQueueClient` at `src/codeoracle/adapters/valkey/client.py` — `enqueue(stream, payload)`, `read_pending(stream, group, consumer)`, `ack(stream, group, message_id)`, `dlq(stream, payload, error)`.
- `SnapshotFileOutcome` SQLAlchemy ORM at `src/codeoracle/db/schemas.py` (NOT `db/models/...` — Plan 5's `schemas.py` is the single declarative module that holds every `Base` subclass; Pydantic types in `models/` are a separate concern) — composite PK `(snapshot_sha, file_path, extractor)`, `outcome: str` (accepts `"success" | "failed" | "skipped"` — `skipped` is for unchanged-since-last-run files), `error: str | None` (Text column, NOT `error_message`), `applied_at: datetime` (server_default `func.now()`, NOT `created_at`/`updated_at`).
- `SnapshotFileOutcomeRepository` at `src/codeoracle/db/repositories/file_outcomes.py` (NOT `snapshot_file_outcome.py` — Plan 5's filename is `file_outcomes.py`) — `upsert(*, snapshot_sha, file_path, extractor, outcome, error=None)` (keyword-only signature; ON CONFLICT DO UPDATE — idempotent on retry), `count_completed(snapshot_sha, extractor)`, `count_failed(snapshot_sha, extractor)`.
- `IndexingOrchestrator` at `src/codeoracle/services/indexing/orchestrator.py` — owns `_STREAM_BY_KIND` (this plan **rewrites** it into `_INFO_BY_KIND` per-Task-2), `_enqueue_all`, `fan_out_and_wait`, polls the barrier on a configurable interval.
- `IndexingService` at `src/codeoracle/services/indexing/service.py` — owns `_extractor_kinds` (Plan 5 default `("joern",)` — this plan changes default to `("joern", "scip", "opengrep", "embed")` per-Task-14), `_OUTCOME_TO_EXTRACTOR_OUTCOME` mapping (`"success"`→SUCCESS / `"partial"`→SUCCESS / `"failed"`→FAILED / `"in_progress"`→FAILED — a kind aggregating to `"partial"` still extracted some nodes, so it counts as success; see Plan 5's `_OUTCOME_TO_EXTRACTOR_OUTCOME`), `Snapshot` status transitions `APPLYING → APPLIED | FAILED` (Plan 3's enum members are `PENDING / APPLYING / APPLIED / FAILED` — there is NO `READY` member; earlier prose using `READY` was a stale name from a pre-Plan-3 sketch).
- `streams.py` at `src/codeoracle/core/streams.py` — central constants module. Plan 5 defines `STREAM_INDEXING_JOERN`, `GROUP_JOERN_INDEXER`, `dlq_for(stream)`. This plan adds three streams + three groups (Task 1).
- `pg_advisory_lock(0x46AE0001)` CODEORACLE-REIDX-1 — single-flight reindex lock owned by `IndexingService`; this plan does NOT change locking.
- Test fixtures in `tests/conftest.py`: `async_session_factory` (Plan 3), `valkey_url` (Plan 5), `valkey_queue_client` (Plan 5), `pg_age_database` (Plan 5).
- `build_worker_context()` composition root at `src/codeoracle/workers/_base/context.py` (or wherever Plan 5 places it — name is the contract, path is implementation) — Plan 6's three new worker `__main__.py` runners (scip_indexer / opengrep_runner / embed_indexer) reuse it verbatim for dependency wiring (queue client, retry policy, repositories, session factory). No worker module reconstructs the context ad hoc.
- `SnapshotRepository` at `src/codeoracle/db/repositories/snapshot.py` (Plan 5) — the repository class that wraps the `Snapshot` ORM (distinct from `SnapshotStatus` enum, which Plan 6 references separately at L29). `IndexingService` (inherited by Plan 6 from Plan 5) composes this repository to drive `APPLYING → APPLIED | FAILED` transitions; Plan 6 does not subclass or extend it.
- Graph FQN scheme (dotted Python form derived from `(file_path, name)`) — the symbol-naming convention Plan 5 established for `:Symbol` AGE nodes. Plan 6's SCIP worker (Task 10) writes `:Definition` + `:Reference` AGE nodes whose `fqn` property follows the SAME algorithm, so Plan 9's verifier can correlate by `fqn` across extractor-scoped label namespaces (Architectural Invariant 4). Plan 6 does not redefine the scheme; it inherits and applies it to its new node labels.
- Alembic revision-ID convention — bare numeric (e.g., `"0005"`, NOT a generated hash) — established by Plan 3's 0001 and propagated through Plan 5's 0004. Plan 6's migration 0005 (Task 4 — `code_references` table) keeps the convention so the linear `alembic upgrade head` history stays human-orderable and the file-name → revision-ID identity is preserved.

**From Plan 4 (`docs/superpowers/plans/2026-05-21-llm-provider-abstraction.md`):**

- `LLMProvider` ABC at `src/codeoracle/adapters/llm/interfaces.py` (NOT `provider.py` — Plan 4's File Structure §82 places the ABC + `LLMProviderError` in `interfaces.py`) — chat-completion contract. This plan introduces a SIBLING ABC (`EmbeddingsProvider`), not an extension method on `LLMProvider`, because chat and embeddings are distinct services with independent rate limits and (under vLLM) potentially different model servers.
- `LLMConfig` at `src/codeoracle/adapters/llm/config.py` (NOT `core/config.py` — Plan 4 puts LLM-domain config beside the LLM adapter so the `core/` layer stays free of adapter-domain knowledge per spec §3.3) — `base_url`, `api_key`, `model` (chat model). This plan reuses `base_url` + `api_key` for the embeddings provider (OpenAI-compatible; vLLM serves both on the same base URL).
- `LLMProviderFactory` at `src/codeoracle/adapters/llm/factory.py` (Plan 4 Task 7) — constructs the concrete chat provider from `LLMConfig.provider`. This plan defines a **sibling** `EmbeddingsProviderFactory` in the SAME file as the ABC (`adapters/llm/embeddings.py`, appended after the ABC definition in Task 7 Step 7b) — NOT in `core/config.py`. Putting an adapter-constructing factory in `core/` would require `core/` to import from `adapters/llm/providers/*`, an upward layer edge that spec §3.3 forbids.
- `MockLLMProvider` testing pattern — this plan adds a `MockEmbeddingsProvider` mirror for unit tests; live-LLM gating pattern (`--run-live-llm` pytest flag) extended to `--run-live-embeddings` (same flag, same behavior).

**From Plan 3 (`docs/superpowers/plans/2026-05-21-core-foundation-and-db-bootstrap.md`):**

- `Embedding` ORM at `src/codeoracle/db/models/embedding.py` — composite PK `(snapshot_sha, source_path, chunk_start_line, chunk_end_line)`, `content: str`, `embedding: Mapped[Any]` (pgvector column whose dim is sed-synced from `pinned.env` into Plan 3's migration constant `EMBEDDING_DIM` in `src/codeoracle/db/migrations/versions/0002_embeddings_table.py`; the corresponding runtime config value is `EmbeddingConfig.dim` from `core/config.py`).
- Migration `0002_embeddings.py` (committed) — `embeddings` table + HNSW index `ix_embeddings_embedding_hnsw` on `embedding vector_cosine_ops`.
- `EmbeddingConfig` at `src/codeoracle/core/config.py` — Pydantic `BaseModel` (NOT `BaseSettings`; env-binding is inherited from the parent `Settings(BaseSettings)`'s `env_prefix="CODEORACLE_"` + `env_nested_delimiter="__"`, see Plan 3 `core/config.py`). Plan 3 declared two required fields with NO defaults — `model: str = Field(min_length=1)` and `dim: int = Field(ge=64, le=4096)` — to fail fast if env injection is missed (a wrong-dim embedding would silently mismatch the pgvector column dim baked into migration 0002). Plan 6 **extends** the class with `embed_chunk_lines: int = 100` + `embed_chunk_overlap: int = 20` (Task 3) and `kind` / `base_url` / `api_key` (Task 7) — all with defaults, so the required-fail-fast posture of Plan 3 is preserved (engineers still must inject `CODEORACLE_EMBEDDING__MODEL` and `CODEORACLE_EMBEDDING__DIM`). New env vars cascade through the parent: `CODEORACLE_EMBEDDING__EMBED_CHUNK_LINES`, `CODEORACLE_EMBEDDING__KIND`, `CODEORACLE_EMBEDDING__BASE_URL`, `CODEORACLE_EMBEDDING__API_KEY`. The api_key-presence precondition for non-mock providers is enforced on `OpenAIEmbeddingsProvider.__init__` (Task 7 Step 5), NOT on `EmbeddingConfig` — see the rationale block in Task 7 Step 5 and the regression test `test_embedding_config_constructs_without_api_key`.
- `AsyncSessionLocal` factory at `src/codeoracle/db/session.py` + `db_session_factory` dependency-injection shape (`async_sessionmaker[AsyncSession]`).
- Migration baseline: 0001 (core tables) + 0002 (embeddings) + 0003 (traces) + 0004 (Plan 5's snapshot_file_outcomes). This plan adds 0005 (Task 4).

**From Plan 2 (`docs/superpowers/plans/2026-05-21-pre-implementation-verification-gates.md`):**

- `scripts/preflight/check_scip_python.py` — already verified scip-python CLI shape at commit-time. Plan 6 trusts this; it does not re-verify scip-python in adapter code.
- `scripts/preflight/check_vllm_openai_compat.py` — verified vLLM serves OpenAI-shape chat completions at `/v1/chat/completions`. It does **not** cover `/v1/embeddings`; that contract is gated by the spec §3.6 scaffolding-day-1 preflight (`infra/scripts/preflight.sh`'s vLLM-embeddings row — blocking at scaffolding time, with the documented `adapters/llm/providers/vllm_embed.py` fallback recorded into `.cache/preflight/results.md` on failure). Plan 6's Task 8 additionally includes a one-line live-mode smoke (`embed(["test"])` returns `list[list[float]]` with len == 1, vec dim == config.dim) as a complementary CI-off regression check against the running provider — it does not replace the §3.6 preflight gate.
- `scripts/preflight/check_opengrep.py` — **does NOT exist**. Plan 2 is committed; we do not extend it. Instead, Task 10 (OpengrepRunner) includes a `verify_binary()` adapter method that runs `opengrep --version` in init and raises `OpengrepRunError("binary not callable")` if missing.

**From Plan 1 (`docs/superpowers/plans/2026-05-21-repo-skeleton-and-cicd.md`):**

- `pyproject.toml` `[tool.ruff.per-file-ignores]` allows `adapters/llm/providers/*` to import provider SDKs directly. This plan adds `adapters/llm/embeddings.py` and (one new file) `adapters/llm/providers/openai_embed.py` — the new providers file gets the same exception. Update the regex if it uses a fixed list rather than a glob.
- `pyproject.toml` `[tool.ruff.lint.flake8-tidy-imports.banned-api]` — direct imports of `openai`, `anthropic`, `litellm`, `vllm` are banned outside the per-file-ignored providers directory. This plan respects that boundary; the new `EmbeddingsProvider` consumers (the embed worker) call the ABC, not the provider SDK.

---

## Plan Defers To

The following items are explicitly NOT in this plan's scope and are assigned to a downstream plan.

- **Sourcebot indexer worker** — REMOVED FROM SCOPE during pre-write verification (2026-05-21). Sourcebot is a Git-pull indexer that supports `file://` URLs for local repos (verified via Sourcebot docs `docs.sourcebot.dev/docs/connections/local-repos`). Since CodeOracle snapshots are pinned to a specific SHA and mounted read-only into the Sourcebot container, Sourcebot indexes once at deployment time (container start, after the snapshot mount lands) and never reindexes. There is no per-file work for CodeOracle to orchestrate, so no `workers/sourcebot_indexer` consumer exists, no `indexing.sourcebot` stream is allocated (compare `streams.py` exports: `STREAM_INDEXING_JOERN` / `_SCIP` / `_OPENGREP` / `_EMBED` only), and no per-file outcome rows enter the Reindex barrier from Sourcebot.

   **Alternatives considered + rejected:**
   - *(a) Wrap Sourcebot in a per-snapshot indexer worker that writes a single trivial "indexed" outcome row.* Rejected: the row would always be `success` (Sourcebot's indexing-on-startup completes before CodeOracle ever publishes the snapshot), so the row would carry zero signal and inflate barrier expected counts for nothing.
   - *(b) Fork Sourcebot to expose a per-file index API.* Rejected: maintaining a fork to give CodeOracle orchestration hooks Sourcebot doesn't natively expose violates the "use upstream tools as-published" principle and creates a maintenance tail.
   - *(c) Stand up one Sourcebot container per snapshot (sidecar model).* Rejected: Sourcebot is heavy (Zoekt index + Postgres + web UI); per-snapshot containers would dominate memory budget and bring no upside over a single Sourcebot container with multiple `file://` connections.

   The deployment-side config (`deploy/sourcebot/connections.json` listing `file:///repos/<snapshot>` entries, one per SHA-pinned snapshot mount) is a Plan 1 / Plan 13 concern. The query-side `SourcebotClient.fetch_span(...)` adapter goes to Plan 8.
- **Plan 8 Sourcebot HTTP search API verification** — `SourcebotClient.fetch_span()` (used by Plan 8's `CodeSpanResolver` + `DocParagraphResolver`) depends on Sourcebot exposing an HTTP search endpoint. We were unable to confirm this in Sourcebot's public docs during pre-write verification (the docs cover connections + deployment but not the search/API surface). **Plan 8 MUST verify Sourcebot exposes an HTTP search API and document the endpoint shape during its preflight; if it does not, the CodeSpanResolver design needs revisiting before implementation.** This is logged here to surface the unknown rather than absorb it.
- **`ContinuousConsumer` framework + `traces_ingest` worker** — Plan 7. Traces ingest pulls from Tempo continuously (not snapshot-keyed), so the per-file barrier doesn't fit. Plan 7 introduces a second base class alongside `WorkerConsumer` and a different ingestion lifecycle. Plan 6's `ExtractorMode` literal is intentionally `Literal["per_file", "project"]` and not `Literal["per_file", "project", "continuous"]` — Plan 7 is expected to extend the literal when it lands.
- **`HandleResolverService` + four resolvers (file / db-func / sql-object / openapi)** — Plan 8. These are read-side services that compose the repositories shipped in this plan (`EmbeddingsRepository.knn`, `CodeReferencesRepository.count_by_symbol`) with Joern's symbol graph. Plan 6 ships the write side; Plan 8 ships the read side.
- **`VerifierService` + `TrustMatrixService` + evidence models** — Plan 9. The verifier reasons across `:Symbol` (Joern), `:Definition`/`:Reference` (SCIP), `code_references` rows (OpenGrep), and `embeddings` rows (this plan). Plan 9 owns the cross-extractor correlation; Plan 6 only ensures each extractor writes its own provenance namespace cleanly (separate AGE labels, separate tables).
- **LangGraph agent, FastAPI service, MCP service** — Plans 10–13.
- **Stale-`APPLYING` recovery** — NOT deferred downstream. The earlier "end-to-end stale-`APPLYING` janitor" was eliminated by amendment #46/G16: recovery is now a check-on-use cleanup inside Plan 5's `IndexingService.reindex` (a bulk sweep that fails stale `APPLYING` rows under the CODEORACLE-REIDX-1 advisory lock at the start of each Reindex). There is no separate janitor process, no time-threshold heuristic, and no Plan 10–13 deferral; Plan 6 inherits this behavior from Plan 5 unchanged.
- **`codeoracle index` CLI subcommand wiring of the new workers into `__main__`** — partially addressed in Task 11/12/13 (each worker ships its own `__main__.py` runner) but the unified `codeoracle index` orchestrator-CLI command continues to invoke `IndexingService.reindex(...)` as defined in Plan 5 — Plan 6 changes the default kinds (Task 14) but does not extend the CLI surface.
- **Re-running pre-existing migrations 0001–0004 against new schemas** — alembic upgrade path is linear; CI runs `alembic upgrade head` on a fresh database and asserts each migration is applied. This plan ships 0005 to be applied on top of 0004; no rebase of prior migrations.

---

## Cross-Plan Downstream Contracts (forward-references)

This section enumerates load-bearing surfaces this plan ships that downstream plans MUST consume in a specific way. Each entry is a contract that, if violated by the downstream plan, breaks evidence verification at runtime.

- **Plan 8 — `DocParagraphResolver` MUST use `ParagraphChunker` geometry.** Task 12's `chunker_for(file_path)` dispatches `LineChunker` for code files and `ParagraphChunker` (variable-length paragraphs) for documentation files (`.md`, `.rst`, `.adoc`, `.markdown`). Plan 8's `DocParagraphResolver` (sibling of `CodeSpanResolver`, named in spec §3.2 + §4.2 `services/resolvers/`) reads `embeddings` rows for doc files. It MUST read `(chunk_start_line, chunk_end_line)` directly from the row — it MUST NOT assume `embed_chunk_lines`-sized spans, because doc chunks are variable-length. Storage shape is uniform; only the chunker algorithm differs (see Architectural Invariant 5).

- **Plan 10 — agent re-enables `DOC_PARAGRAPH` citations.** Plan 10's earlier draft (Deviation 6) declared "no `DOC_PARAGRAPH` citations" because there was no doc-aware chunking — `embed_indexer` ran a code-shaped LineChunker over every file kind, producing meaningless line-windowed slices for prose. G14 in this plan (Task 12 Step 2 ParagraphChunker + Task 2's extension-routed `embed` stream) lands the missing capability, so Plan 10 MUST re-enable `DOC_PARAGRAPH` as a valid citation kind alongside `CODE_SPAN` (spec §3.3 `HandleType` enum). The agent's evidence-binding harness queries `embeddings` rows uniformly; only the citation-kind classification differs. Plan 6 exports the path-classification predicate `is_doc_path(source_path: str) -> bool` from `codeoracle.services.embedding.chunker` (Task 12 Step 2) so Plan 10's `embed_query` node routes each `EmbeddingHit` to `HandleType.DOC_PARAGRAPH` (doc extension) or `HandleType.CODE_SPAN` (everything else) using the *same* extension set `chunker_for` dispatched on — single source of truth, no drift. This is logged here as a durable cross-plan dependency.

- **Plan 8 — `SourcebotClient.fetch_span()` HTTP API surface unknown.** Already enumerated under "Plan Defers To"; restated here so the downstream-contracts section is single-source for plan-8 obligations. The contract: Plan 8 MUST verify Sourcebot exposes an HTTP search endpoint during preflight and document the endpoint shape, OR revisit `CodeSpanResolver`/`DocParagraphResolver` designs before implementation.

- **Plan 1A — variant Dockerfile matrix references Plan 6 worker modules.** Plan 1A's `codeoracle.worker` variant build (image-build matrix) bakes in the worker modules this plan ships at `src/codeoracle/workers/scip_indexer/`, `src/codeoracle/workers/opengrep_runner/`, and `src/codeoracle/workers/embed_indexer/` (Tasks 11/12/13). If Plan 1A's variant assignment ever rotates a worker out of the `workers` image variant, Plan 6's per-extractor barrier counts and stream routing must be re-validated against the new container topology.

- **Plan 5 — SCIP indexer ownership transfer (Plan 5 DEFERRED-prefix forward-pointer).** Plan 5 declared `DEFERRED: SCIP indexer` in its imports section as the forward-pointer to Plan 6's ownership. Plan 6 ships the SCIP indexer worker (Tasks 8 + 10 + 11), the `adapters/scip/` adapter, and the `:Definition` + `:Reference` AGE node labels (Architectural Invariant 4). This is the reciprocal of Plan 5's deferral.

- **Plan 5 — OpenGrep indexer ownership transfer (Plan 5 DEFERRED-prefix forward-pointer).** Plan 5 declared `DEFERRED: OpenGrep indexer` in its imports section as the forward-pointer to Plan 6's ownership. Plan 6 ships the per-file `workers/opengrep_runner` consumer (Tasks 9 + 11), the `adapters/opengrep/` runner adapter with `verify_binary()`, and the `code_references` table + `CodeReferencesRepository` storage stack (Tasks 4 + 5). This is the reciprocal of Plan 5's deferral.

- **Plan 5 — embed_indexer worker ownership transfer (Plan 5 DEFERRED-prefix forward-pointer).** Plan 5 declared `DEFERRED: embed_indexer worker` in its imports section as the forward-pointer to Plan 6's ownership. Plan 6 ships the per-file `workers/embed_indexer` consumer (Task 12), the `services/embedding/chunker.py` module (`LineChunker` / `ParagraphChunker` / `chunker_for` factory / `is_doc_path` predicate), and the `EmbeddingsProvider` ABC + `OpenAIEmbeddingsProvider` concrete (Task 7). This is the reciprocal of Plan 5's deferral.

- **Plan 5 — EmbeddingsRepository ownership transfer (Plan 5 DEFERRED-prefix forward-pointer).** Plan 5 declared `DEFERRED: EmbeddingsRepository` in its imports section as the forward-pointer to Plan 6's ownership. Plan 6 ships exactly this (Task 6 — `EmbeddingsRepository` at `src/codeoracle/db/repositories/embeddings.py` with `knn(query_embedding, snapshot_sha, k) -> list[EmbeddingHit]` consuming Plan 3's `ix_embeddings_embedding_hnsw` HNSW index). This is the reciprocal of Plan 5's deferral.

- **Plan 7 — `EmbeddingConfig` BaseModel-under-Settings is the reference pattern for nested config.** Plan 6's `EmbeddingConfig` (extended in Task 7 — Pydantic `BaseModel` nested under the parent `Settings(BaseSettings)` with `env_nested_delimiter="__"` propagating `CODEORACLE_EMBEDDING__*` env vars) is the canonical instance of the nested-config pattern Plan 7's `TracesConfig` mirrors. Plan 7 MUST NOT introduce a sibling `BaseSettings` for traces — the pattern is "one `Settings` parent, N `BaseModel` children" so a single `Settings()` instance loads every domain config from one env scan.

- **Plan 10 — `EmbeddingsProvider` ABC is the agent's embed-query interface.** Plan 10's `embed_query` node calls `EmbeddingsProvider.embed([query_text])` (Task 7's ABC at `src/codeoracle/adapters/llm/embeddings.py`) to obtain a query vector before invoking `EmbeddingsRepository.knn`. The agent MUST call the ABC, NOT a concrete provider — the `[tool.ruff.lint.flake8-tidy-imports.banned-api]` boundary inherited from Plan 1 forbids direct provider SDK imports outside `adapters/llm/providers/`.

- **Plan 10 — `EmbeddingsRepository.knn` is the agent's kNN-lookup contract.** Plan 10's `embed_query` node calls `EmbeddingsRepository.knn(query_embedding, snapshot_sha, k)` (Task 6) and consumes the `list[EmbeddingHit]` return value. The agent MUST NOT bypass the repository and issue raw `embedding <=> :query` SQL — the HNSW index choice + cosine-distance semantics are repository-internal and may evolve.

- **Plan 10 — `EmbeddingHit` is the kNN-lookup return value.** Plan 6 ships `EmbeddingHit` (Task 6) as the value type returned by `EmbeddingsRepository.knn`: carries the `embeddings` row columns plus a `distance` float (cosine distance — smallest distance = best match). Plan 10's agent surfaces the similarity score to the verifier via this type. Plan 10 MUST consume the type as-is; it MUST NOT introduce a parallel `AgentEmbeddingHit` wrapper.

- **Plan 10 — Architectural Invariant 5 (chunker-family ↔ handle-type) is the citation-kind routing contract.** Plan 6's Architectural Invariant 5 ("Chunk constants are CHUNKER-SCOPED invariants" — see Architectural Invariants block) governs the bidirectional mapping: doc-extension files chunked by `ParagraphChunker` → cited as `HandleType.DOC_PARAGRAPH`; code-extension files chunked by `LineChunker` → cited as `HandleType.CODE_SPAN`. Plan 10's `embed_query` node MUST use `is_doc_path()` (exported by `codeoracle.services.embedding.chunker`) for the routing decision — the SAME extension set `chunker_for` dispatched on, so writer geometry and reader citation kind never drift.

- **Plan 13 — all four extractors verified as a live E2E + three worker entrypoints invoked by the slice compose.** All four extractors (joern, scip, opengrep, embed) are verified as a live E2E in Task 7 + Task 8 per D-6.5. The three worker module entrypoints `workers/scip_indexer/__main__.py` (Task 4), `workers/opengrep_runner/__main__.py` (Task 5), `workers/embed_indexer/__main__.py` (Task 6) are invoked by `docker-compose.e2e.yml` as `python -m codeoracle.workers.scip_indexer`, `python -m codeoracle.workers.opengrep_runner`, `python -m codeoracle.workers.embed_indexer` (one PID 1 per `scip_indexer:` / `opengrep_runner:` / `embed_indexer:` service); dispatch is via Plan 1A entrypoint.sh + `environment.CODEORACLE_VARIANT: workers.<name>` runtime override per Plan 13 DECIDE-W.

---

## Spec Coverage Map

Each spec section that this plan implements (full or partial). Cross-reference against `docs/superpowers/specs/2026-05-21-platform-scaffolding-design.md`.

| Spec section | Implemented by | Notes |
|---|---|---|
| §1 Vision — evidence-bound proofs | (whole plan, indirect) | This plan ships three of the five primary evidence sources (symbol references via SCIP, textual references via OpenGrep, embedding similarity); Joern (Plan 5) is the fourth; traces (Plan 7) is the fifth. |
| §3.6 scip-python preflight (Plan 2 ref) | Trust (no rework) | `scripts/preflight/check_scip_python.py` already validates the binary; Task 9 wraps it without re-verifying the CLI shape. |
| §4.1 service map — `workers/scip_indexer` | Tasks 8 + 10 | Project-mode worker (single message per snapshot, N outcome rows). |
| §4.1 service map — `workers/opengrep_runner` | Tasks 9 + 11 | Per-file worker. Spec line 451 says "Textual reference cross-check"; this plan models that explicitly as `code_references` with `kind ∈ {import, call, string-literal, comment-mention}`. |
| §4.1 service map — `workers/embed_indexer` (per-chunk semantics, per-file outcome) | Tasks 6 + 12 | Task 6 = `EmbeddingsRepository` (storage); Task 12 = embed worker (internal chunking, one outcome row per file). |
| §4.1 service map — `IndexingOrchestrator` fan-out for four kinds | Tasks 2 + 13 | Task 2 adds `_INFO_BY_KIND` (per-mode dispatch + per-extractor file filter / `accepted_extensions`, G14) and shared `_filter_files_for` classmethod used by `_enqueue_all` / `_is_barrier_closed` / `_collect_outcomes` to keep fan-out and barrier counts in lockstep; Task 13 changes `DEFAULT_EXTRACTOR_KINDS` from `("joern",)` to `("joern", "scip", "opengrep", "embed")`. |
| §4.3 Indexing Layer — "extractor results dict keyed by extractor name" | Task 13 | Plan 5 already shapes `Snapshot.extractor_results: dict[str, ExtractorOutcome]`; this plan adds three keys. No schema change. |
| §6 LLM Layer — embeddings split from chat (vLLM may serve them differently) | Task 7 | `EmbeddingsProvider` is a sibling ABC of `LLMProvider`, NOT a method on it. |
| §7 Code-evidence layer — code references textual cross-check | Tasks 4 + 5 + 9 + 11 | `code_references` table (Task 4) + `CodeReferencesRepository` (Task 5) + `OpengrepRunner` adapter (Task 9) + per-file worker (Task 11). |
| §8 Knowledge graph — `:Definition` + `:Reference` nodes (SCIP) | Task 10 | NEW node labels distinct from Joern's `:Symbol` so provenance is preserved at the graph level. Plan 9 verifier correlates by `fqn` across labels. |
| §9 Embedding store — HNSW knn search | Task 6 | `EmbeddingsRepository.knn(query_vec, snapshot_sha, k)` uses Plan 3's HNSW index. |
| §10 Reindex lifecycle — APPLYING → APPLIED through barrier | Tasks 2 + 10 + 11 + 12 + 13 + 14 | Each new worker writes one outcome row per attempted file; project-mode SCIP writes one per file in `files` (even if SCIP didn't cover the file — explicit invariant). Terminal state is `SnapshotStatus.APPLIED` (Plan 3's enum); the earlier prose name `READY` is stale and never existed as a code token. |

---

## Architectural Invariants (introduced or affirmed by this plan)

These are not tasks; they are global constraints every task respects.

1. **Per-file barrier is sacred.** Every extractor — regardless of internal extraction unit (chunks, projects, single-file) — writes EXACTLY ONE `SnapshotFileOutcome` row per (file_path, extractor) pair per Reindex. Embed batches chunks internally → one outcome per file. SCIP runs project-wide → still writes one outcome per file (across `files: list[str]` in the project message). OpenGrep is per-file natively → one outcome per file. No exceptions; the barrier counts attempts (success + failed), not coverage.

2. **Project-mode SCIP outcome invariant.** When the SCIP worker runs `scip-python` on a project and parses output, the output may not cover every file in `files: list[str]` (`__init__.py` with no symbols, `.pyi` stub files, files with parse errors silently skipped by scip-python). The worker MUST write `outcome="success"` rows with `error_message=None` for every file in `files`, regardless of whether SCIP found symbols in that file. "We attempted SCIP on this file" is the contract; "we found N symbols" is recorded by the count of `:Definition` nodes the worker wrote for that file (queryable separately).

3. **SCIP failure granularity = project-level.** If `scip-python` exits non-zero on the project, the worker has no per-file attribution and writes `outcome="failed"` rows for ALL files in `files`, each with `error_message=<truncated stderr>`. The barrier still closes (all rows present); the orchestrator sees N FAILED outcomes for the SCIP extractor, so SCIP's kind-outcome aggregates to `"failed"`. IndexingService's outcome→status rule then marks the Snapshot FAILED only when NO kind succeeded (the all-failed→FAILED rule); if another kind (joern/opengrep/embed) succeeded, the Snapshot is APPLIED with SCIP coverage simply absent — partial failure is tolerated (this is the per-kind degradation #68 ratified, not all-or-nothing). Alternative considered: dead-letter the message with no outcome rows (leaving the Snapshot stuck in `APPLYING` until the next Reindex). Rejected because the barrier-by-count semantics are the orchestrator's only completion signal; with no rows the barrier never closes, so one stuck SCIP message would leave the Snapshot in `APPLYING` until the next Reindex's check-on-use cleanup sweeps it (Plan 5 `IndexingService.reindex` bulk-fails stale `APPLYING` rows under the advisory lock — amendment #46/G16; there is no standalone janitor). Writing FAILED rows now closes the barrier immediately and surfaces the failure on this Reindex rather than the next.

4. **AGE label namespaces are extractor-scoped.** Joern (Plan 5) writes `:Symbol` nodes. SCIP (this plan) writes `:Definition` + `:Reference` nodes. They share no MERGE keys. A symbol with FQN `codeoracle.services.indexing.Foo.bar` may exist in BOTH `(:Symbol {fqn: ...})` (Joern's view) AND `(:Definition {fqn: ...})` (SCIP's view), and that's intentional. Plan 9's verifier queries both labels and reports them as independent evidence sources. Rejected alternatives: (a) single `:Symbol` label with `provenance: list[str]` property — implicit fusion, easier to silently merge mis-resolved symbols; (b) single label with `extractor` property + MERGE-by-(fqn, extractor) — equivalent to current design but uses one heterogeneously-typed node label, hurting query ergonomics.

5. **Chunk constants are CHUNKER-SCOPED invariants.** Plan 6 ships TWO chunkers (Task 12 Step 2), dispatched per file by `chunker_for(file_path)`:
   - **`LineChunker`** governs code files (`.py`, `.pyi`). Constants: `EmbeddingConfig.embed_chunk_lines = 100`, `EmbeddingConfig.embed_chunk_overlap = 20` (Task 3). Plan 6 indexes code by these; Plan 8's `CodeSpanResolver` reads code by these.
   - **`ParagraphChunker`** governs documentation files (`.md`, `.rst`, `.adoc`, `.markdown`). Constant: `EmbeddingConfig.doc_chunk_max_lines = 200` (Task 3, added by G14). Plan 6 indexes docs by paragraph runs (blank-line bounded); Plan 8's `DocParagraphResolver` (sibling of `CodeSpanResolver`) reads docs by `(start_line, end_line)` directly from each `embeddings` row — it MUST NOT assume `embed_chunk_lines`-sized spans, because doc chunks are variable-length.

   Storage shape is uniform across chunker kinds — `embeddings.{source_path, chunk_start_line, chunk_end_line, content}` — only the chunking algorithm differs. If any of these three constants ever change, the corresponding writer + reader change in lockstep — they're config-loaded into both, so an env override (`CODEORACLE_EMBEDDING__EMBED_CHUNK_LINES`, `CODEORACLE_EMBEDDING__EMBED_CHUNK_OVERLAP`, `CODEORACLE_EMBEDDING__DOC_CHUNK_MAX_LINES`) flows to both processes from the same `Settings` instance.

6. **EmbeddingsProvider lifetime = process lifetime.** The provider is constructed once at worker startup, holds a single `httpx.AsyncClient` (with connection pooling), and is torn down on `stop_event`. No per-message provider construction. This is invariant of which concrete provider is selected (OpenAI / vLLM-OpenAI-compat).

7. **No new framework rewrites.** Plan 5's `WorkerConsumer` ABC is the ONLY worker base class introduced by this plan. New consumers subclass it directly. New abstractions (`ExtractorMode` literal, `_INFO_BY_KIND` map) are local to the orchestrator and ≤ 20 LOC of net change in `services/indexing/orchestrator.py`.

---

## Pre-Implementation Verification

Before starting Task 1, the implementer runs these commands and confirms expected output. The Plan does not gate on these — they are sanity checks to catch environment drift.

```bash
# Plan 5's tests still pass (we're extending, not rewriting)
uv run pytest tests/unit/workers/ tests/integration/workers/ tests/unit/services/indexing/ tests/integration/services/indexing/ -v
# Expected: all green

# Plan 3's embeddings migration is at head
uv run alembic current
# Expected: 0004 (head — Plan 5's snapshot_file_outcomes)

# scip-python binary callable (Plan 2 preflight already verified; quick re-check)
scip-python --version
# Expected: prints version, exits 0

# opengrep binary callable (Plan 2 did NOT verify; first-time check)
opengrep --version
# Expected: prints version, exits 0. If missing: install via `brew install opengrep` / system package.

# scip CLI (used by parser to print .scip files in text mode)
scip --version
# Expected: prints version, exits 0

# vLLM embeddings endpoint reachable. NOTE: the AUTHORITATIVE check on the
# `/v1/embeddings` OpenAI-contract is the spec §3.6 scaffolding-day-1 PREFLIGHT
# GATE owned by `infra/scripts/preflight.sh` (one-shot, blocking at scaffolding
# time; on failure it records the documented fallback — route embeddings via
# `adapters/llm/providers/vllm_embed.py` — into `.cache/preflight/results.md`).
# The curl below is a quick Plan-6 re-check of that gate's subject before
# live-mode tests; it is NOT the gate and does not replace it.
curl -s -X POST "${LLM_BASE_URL:-http://localhost:8000}/v1/embeddings" \
  -H "Authorization: Bearer ${LLM_API_KEY:-sk-test}" \
  -H "Content-Type: application/json" \
  -d '{"input": "smoke test", "model": "'${EMBEDDING_MODEL:-text-embedding-3-small}'"}' \
  | jq '.data[0].embedding | length'
# Expected: prints the embedding dim integer (1536 for openai text-embedding-3-small; may differ for vLLM-served models).
# If missing/failing: the spec §3.6 preflight fallback governs; skip the Task 8 live
# embeddings smoke locally with `pytest -m 'not live_embeddings'` (it is CI-off anyway).
```

If any sanity check fails, surface to the user before starting Task 1; do not proceed with broken environment.

---

## Tasks

The plan contains 15 tasks. Frame extensions first (1–3), schemas + repositories (4–7), adapters (8–10), workers (11–13), integration (14–15).

### Task 1: Frame Extensions — streams.py + exceptions.py

**Files:**
- Modify: `src/codeoracle/core/streams.py` (Plan 5 created; add three streams + three groups)
- Modify: `src/codeoracle/core/exceptions.py` (Plan 5 created; add three subclasses)
- Test: `tests/unit/core/test_streams_extensions_plan6.py` (new)
- Test: `tests/unit/core/test_exceptions_plan6.py` (new)

- [ ] **Step 1: Write the failing test (streams)**

Create `tests/unit/core/test_streams_extensions_plan6.py`:

```python
"""Plan 6 frame-extension tests for `core/streams.py`.

These tests pin the names of the three new stream constants and three new group
constants. They do not assert exact string values (those are an implementation
detail of `streams.py` and may evolve), but they DO assert that:

(1) Each new constant exists with the expected `STREAM_*` / `GROUP_*` naming.
(2) `dlq_for(STREAM_INDEXING_X)` returns a stable, non-empty string for each
    new stream. This proves DLQ derivation works uniformly for all extractors
    without per-extractor hardcoding.
(3) The three new streams are MUTUALLY DISTINCT and DISTINCT from the existing
    `STREAM_INDEXING_JOERN`. A copy-paste error that aliased two streams would
    silently route SCIP messages to the OpenGrep worker.
"""

from codeoracle.core import streams


def test_stream_constants_plan6_exist_and_are_strings() -> None:
    assert isinstance(streams.STREAM_INDEXING_SCIP, str)
    assert isinstance(streams.STREAM_INDEXING_OPENGREP, str)
    assert isinstance(streams.STREAM_INDEXING_EMBED, str)
    assert streams.STREAM_INDEXING_SCIP != ""
    assert streams.STREAM_INDEXING_OPENGREP != ""
    assert streams.STREAM_INDEXING_EMBED != ""


def test_group_constants_plan6_exist_and_are_strings() -> None:
    assert isinstance(streams.GROUP_SCIP_INDEXER, str)
    assert isinstance(streams.GROUP_OPENGREP_RUNNER, str)
    assert isinstance(streams.GROUP_EMBED_INDEXER, str)


def test_streams_plan6_mutually_distinct() -> None:
    all_streams = {
        streams.STREAM_INDEXING_JOERN,
        streams.STREAM_INDEXING_SCIP,
        streams.STREAM_INDEXING_OPENGREP,
        streams.STREAM_INDEXING_EMBED,
    }
    # Set length == 4 proves no duplicates; a copy-paste alias would collapse two.
    assert len(all_streams) == 4


def test_groups_plan6_mutually_distinct() -> None:
    all_groups = {
        streams.GROUP_JOERN_INDEXER,
        streams.GROUP_SCIP_INDEXER,
        streams.GROUP_OPENGREP_RUNNER,
        streams.GROUP_EMBED_INDEXER,
    }
    assert len(all_groups) == 4


def test_dlq_for_works_for_each_new_stream() -> None:
    # Plan 5 defined `dlq_for(stream) -> str` returning e.g. "indexing.scip.dlq"
    # for an input of "indexing.scip". Plan 6 doesn't re-verify the exact
    # transformation, only that it produces stable distinct outputs for the
    # three new streams.
    dlqs = {
        streams.dlq_for(streams.STREAM_INDEXING_SCIP),
        streams.dlq_for(streams.STREAM_INDEXING_OPENGREP),
        streams.dlq_for(streams.STREAM_INDEXING_EMBED),
    }
    assert len(dlqs) == 3
    for d in dlqs:
        assert isinstance(d, str)
        assert d.endswith(".dlq")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/core/test_streams_extensions_plan6.py -v`

Expected: FAIL with `AttributeError: module 'codeoracle.core.streams' has no attribute 'STREAM_INDEXING_SCIP'`.

- [ ] **Step 3: Add the new constants to streams.py**

Modify `src/codeoracle/core/streams.py`. Plan 5's existing content remains; append the new constants in the same style. The final file looks like:

```python
"""Stream + consumer-group name registry.

Owned by Plan 5 (worker framework); extended by Plan 6 (SCIP, OpenGrep, embed)
and later Plan 7 (continuous traces ingest). The convention is:

- One STREAM_<NAMESPACE>_<KIND> constant per Valkey stream.
- One GROUP_<WORKER_NAME> constant per consumer group on that stream.
- `dlq_for(stream)` returns the conventional DLQ stream name for any stream.
- Constants are accessed by name from the orchestrator and worker modules; no
  worker module constructs stream names ad hoc.
"""

# --- Indexing namespace (snapshot-keyed extractors; per-file barrier) ---

STREAM_INDEXING_JOERN: str = "indexing.joern"
STREAM_INDEXING_SCIP: str = "indexing.scip"
STREAM_INDEXING_OPENGREP: str = "indexing.opengrep"
STREAM_INDEXING_EMBED: str = "indexing.embed"

# Consumer groups — one per worker process / worker class.
GROUP_JOERN_INDEXER: str = "joern-indexer"
GROUP_SCIP_INDEXER: str = "scip-indexer"
GROUP_OPENGREP_RUNNER: str = "opengrep-runner"
GROUP_EMBED_INDEXER: str = "embed-indexer"

# Reindex async-job stream + group — Plan 5's 2026-05-26 amendment, shown here
# so this "final file" depiction stays faithful. Plan 11's queue-metrics
# refresher (NFR D4-4) imports both names, so a wholesale paste that dropped
# them would break the API metrics import. Values mirror Plan 5; the indexer
# groups above diverge from Plan 5 on hyphen-vs-underscore — a pre-existing
# Plan 5<->6 drift tracked separately, not reconciled here.
STREAM_REINDEX_REQUESTS: str = "codeoracle:reindex:requests"
GROUP_REINDEX_ORCHESTRATOR: str = "reindex_orchestrator"


def dlq_for(stream: str) -> str:
    """Conventional DLQ stream name for a primary stream.

    `indexing.joern` -> `indexing.joern.dlq`, etc. The convention is stable
    across all indexing streams introduced by Plans 5+ and Plan 6.
    """
    return f"{stream}.dlq"
```

(Plan 5's `streams.py` already had `STREAM_INDEXING_JOERN`, `GROUP_JOERN_INDEXER`, `dlq_for`, and — from its 2026-05-26 amendment — the reindex pair `STREAM_REINDEX_REQUESTS` / `GROUP_REINDEX_ORCHESTRATOR`; the three new STREAM constants and three new GROUP constants are Plan 6's net additions. Plan 5's file may have had comment markers like `# Plan 6: SCIP / OpenGrep / Sourcebot / Embed go here` — remove those markers when adding the real constants. Do not retain the Sourcebot placeholder, since Plan 6 explicitly removed Sourcebot from scope.)

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/unit/core/test_streams_extensions_plan6.py -v`

Expected: PASS (5 tests).

- [ ] **Step 5: Write the failing exceptions test**

Create `tests/unit/core/test_exceptions_plan6.py`:

```python
"""Plan 6 frame-extension tests for `core/exceptions.py`.

Three new TerminalWorkerError subclasses are added: ScipExtractionError,
OpengrepRunError, EmbedExtractionError. They share the WorkerError hierarchy
and are routed by Plan 5's WorkerConsumer to the DLQ (NO retry).
"""

import pytest

from codeoracle.core.exceptions import (
    EmbedExtractionError,
    JoernExtractionError,
    OpengrepRunError,
    ScipExtractionError,
    TerminalWorkerError,
    WorkerError,
)


@pytest.mark.parametrize("cls", [ScipExtractionError, OpengrepRunError, EmbedExtractionError])
def test_each_is_terminal_worker_error(cls: type[Exception]) -> None:
    err = cls("boom")
    assert isinstance(err, TerminalWorkerError)
    assert isinstance(err, WorkerError)
    assert str(err) == "boom"


def test_distinct_from_joern_and_from_each_other() -> None:
    # We need each extractor's failures to be catchable independently — code
    # that handles a SCIP failure must NOT accidentally catch an embed failure
    # via the same `except` clause. The minimal contract: each subclass is
    # not a parent of the others.
    siblings = [JoernExtractionError, ScipExtractionError, OpengrepRunError, EmbedExtractionError]
    for cls in siblings:
        instance = cls("x")
        for other in siblings:
            if other is cls:
                continue
            assert not isinstance(instance, other), f"{cls.__name__} should not be a {other.__name__}"
```

- [ ] **Step 6: Run test to verify it fails**

Run: `uv run pytest tests/unit/core/test_exceptions_plan6.py -v`

Expected: FAIL with `ImportError: cannot import name 'ScipExtractionError' from 'codeoracle.core.exceptions'`.

- [ ] **Step 7: Add the three new exception classes to exceptions.py**

Modify `src/codeoracle/core/exceptions.py`. The full structure (Plan 5 + Plan 6 additions) looks like:

```python
"""Worker exception hierarchy.

The `WorkerConsumer` base class (Plan 5) catches exceptions thrown from
`handle()` and dispatches based on type:

- `TransientWorkerError` — log, NACK (re-queue for reclaim per `RetryPolicy`).
- `TerminalWorkerError` — log, ACK + route to DLQ (no retry).
- Any other `Exception` (e.g., AttributeError) — wrap in a generic terminal
  error, ACK + route to DLQ. Defensive default; transient errors should be
  raised explicitly.

Each extractor has its own terminal subclass so failure attribution at the DLQ
is easier (the DLQ consumer can branch by exception class name).
"""


class WorkerError(Exception):
    """Root of worker exception hierarchy."""


class TransientWorkerError(WorkerError):
    """Worker error that should be retried per RetryPolicy."""


class TerminalWorkerError(WorkerError):
    """Worker error that should NOT be retried — route directly to DLQ."""


# --- Extractor-specific terminal errors (Plan 5 + Plan 6) ---

class JoernExtractionError(TerminalWorkerError):
    """Joern subprocess exited non-zero or produced unparsable output."""


class ScipExtractionError(TerminalWorkerError):
    """scip-python subprocess exited non-zero or .scip output is unparsable.

    Project-scoped: when raised by the SCIP worker, ALL files in the project
    message are written as `outcome=failed` with the same error message
    (per architectural invariant 3).
    """


class OpengrepRunError(TerminalWorkerError):
    """opengrep subprocess exited non-zero or rule output is unparsable.

    Per-file: only the failing file's outcome is marked failed.
    """


class EmbedExtractionError(TerminalWorkerError):
    """Embedding provider returned a malformed response (wrong vector dim,
    wrong response count vs input batch size, or non-2xx + non-retryable
    error code such as 400 invalid-input).

    Transient errors (timeouts, 429 rate limit, 5xx) are NOT raised as
    EmbedExtractionError — they are raised as `TransientWorkerError` so
    WorkerConsumer retries them via the reclaim ladder.
    """
```

- [ ] **Step 8: Run test to verify it passes**

Run: `uv run pytest tests/unit/core/test_exceptions_plan6.py -v`

Expected: PASS (4 tests).

- [ ] **Step 9: Run the whole Plan 5 + Plan 6 frame test suite to confirm no regression**

Run: `uv run pytest tests/unit/workers/ tests/integration/workers/ tests/unit/core/ -v`

Expected: all green, including Plan 5's existing tests.

- [ ] **Step 10: Commit**

```bash
git add tests/unit/core/test_streams_extensions_plan6.py tests/unit/core/test_exceptions_plan6.py \
        src/codeoracle/core/streams.py src/codeoracle/core/exceptions.py
git commit -m "feat(workers): add scip/opengrep/embed streams + exceptions (Plan 6 Task 1)"
```

---

### Task 2: ExtractorMode + `_INFO_BY_KIND` Orchestrator Extension

**Files:**
- Create: `src/codeoracle/services/indexing/extractor_mode.py`
- Modify: `src/codeoracle/services/indexing/orchestrator.py` (Plan 5 created; rewrite `_STREAM_BY_KIND` into `_INFO_BY_KIND` + extend `_enqueue_all`)
- Test: `tests/unit/services/indexing/test_orchestrator_project_mode.py` (new)
- Test: `tests/unit/services/indexing/test_orchestrator_extractor_mode_literal.py` (new)

**Context for the implementer:** Plan 5's `IndexingOrchestrator._enqueue_all` looks roughly like (paraphrasing — read Plan 5 plan for exact code):

```python
class IndexingOrchestrator:
    _STREAM_BY_KIND: dict[str, str] = {
        "joern": STREAM_INDEXING_JOERN,
        # Plan 6: scip, opengrep, embed (Sourcebot REMOVED — see Plan 6 Defers section)
    }

    async def _enqueue_all(
        self,
        snapshot_sha: str,
        repo_root: str,
        files: list[str],
        extractor_kinds: tuple[str, ...],
    ) -> None:
        for kind in extractor_kinds:
            stream = self._STREAM_BY_KIND[kind]
            for file_path in files:
                payload = {
                    "batch_id": snapshot_sha,
                    "snapshot_sha": snapshot_sha,
                    "file_path": file_path,
                    "repo_root": repo_root,
                }
                await self._queue.enqueue(stream, payload)
```

Plan 6 changes this in THREE ways:

1. `_STREAM_BY_KIND: dict[str, str]` is replaced by `_INFO_BY_KIND: dict[str, _ExtractorInfo]` where `_ExtractorInfo = NamedTuple("ExtractorInfo", [("stream", str), ("mode", ExtractorMode), ("accepted_extensions", frozenset[str] | None)])`.
2. `_enqueue_all` branches on `info.mode`:
   - `per_file`: existing per-file loop (joern, opengrep, embed).
   - `project`: ONE message containing the filtered file list (scip).
3. **G14 (per-extractor file routing).** `_enqueue_all` consults `info.accepted_extensions` and routes only matching files to each extractor — joern/scip/opengrep see only `.py`/`.pyi`; embed sees everything (and dispatches `LineChunker` vs `ParagraphChunker` per-file at Task 12 Step 2). The barrier helpers (`_is_barrier_closed`, `_collect_outcomes`) compute `expected` per-extractor from the filtered set, so an extractor with no routable files in a snapshot is trivially complete.

The barrier semantics in `fan_out_and_wait` are EXTENDED (not broken): it still polls `SnapshotFileOutcomeRepository.count_completed + count_failed >= expected_per_extractor`, but `expected_per_extractor` is now `len(_filter_files_for(files, kind))` — was `len(files)` pre-G14. A shared classmethod `_filter_files_for(files, kind)` keeps the filter rule in ONE place; drift between fan-out (what gets enqueued) and barrier (what gets expected) would hang the barrier forever. Project-mode workers write N outcomes from one message (where N is the size of the routed file list); per-file workers write 1 outcome per routed file.

- [ ] **Step 1: Write the failing test (ExtractorMode literal type)**

Create `tests/unit/services/indexing/test_orchestrator_extractor_mode_literal.py`:

```python
"""ExtractorMode is a Literal type pinning two modes (per_file, project).

Plan 7 is expected to extend this literal to add "continuous" (for traces
ingest, which has neither snapshot-keyed nor per-file semantics). The literal
is intentionally NOT a class hierarchy or enum — keeping it as a string
literal lets it serialize trivially into Pydantic messages and into the
Snapshot.extractor_results dict.
"""

from typing import get_args

from codeoracle.services.indexing.extractor_mode import ExtractorMode


def test_extractor_mode_literal_pins_two_modes() -> None:
    modes = set(get_args(ExtractorMode))
    assert modes == {"per_file", "project"}


def test_extractor_mode_used_in_orchestrator_info_map() -> None:
    from codeoracle.services.indexing.orchestrator import IndexingOrchestrator

    info_map = IndexingOrchestrator._INFO_BY_KIND  # type: ignore[attr-defined]
    for kind, info in info_map.items():
        assert info.mode in get_args(ExtractorMode), f"kind={kind} has invalid mode={info.mode}"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/services/indexing/test_orchestrator_extractor_mode_literal.py -v`

Expected: FAIL with `ModuleNotFoundError: No module named 'codeoracle.services.indexing.extractor_mode'`.

- [ ] **Step 3: Create extractor_mode.py**

Create `src/codeoracle/services/indexing/extractor_mode.py`:

```python
"""Extractor execution-mode literal — Plan 6 frame extension.

`per_file` — orchestrator enqueues one message per (file, extractor). The worker
processes one file per message and writes one `SnapshotFileOutcome` row. This is
the natural fit for tools that operate on a single file at a time: Joern (Plan
5), OpenGrep (this plan), embed (this plan).

`project` — orchestrator enqueues ONE message per (snapshot, extractor) carrying
the full file list. The worker runs the tool once on the project and then writes
N `SnapshotFileOutcome` rows (one per file in the list, regardless of whether
the tool covered each file individually). This fits tools that are designed to
operate at project scope: scip-python (this plan).

Plan 7 (continuous traces ingest) is expected to extend this literal to add a
third mode, `continuous`, that has neither snapshot-keyed nor per-file
semantics. When Plan 7 lands, both the literal here AND the `_INFO_BY_KIND`
map in `orchestrator.py` need a coordinated update; the literal is the
single source of truth for which modes the orchestrator understands.
"""

from typing import Literal

ExtractorMode = Literal["per_file", "project"]
```

- [ ] **Step 4: Write the failing test (project-mode enqueue shape)**

Create `tests/unit/services/indexing/test_orchestrator_project_mode.py`:

```python
"""Project-mode orchestrator behavior.

These tests do NOT exercise a real SCIP worker — they pin the orchestrator's
enqueue shape (one message per project-mode extractor, N messages per
per_file-mode extractor) and the barrier's counting behavior when a single
worker writes N outcomes from one message.

Fixtures (Plan 3 + Plan 5):
- `async_session_factory` — yields `async_sessionmaker[AsyncSession]` bound to
  the testcontainers PG instance.
- `valkey_queue_client` — Plan 5 ValkeyQueueClient bound to the testcontainers
  Valkey instance.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from typing import Any

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from codeoracle.adapters.valkey.client import ValkeyQueueClient
from codeoracle.db.repositories.snapshot_file_outcome import (
    SnapshotFileOutcomeRepository,
    SnapshotFileOutcomeRow,
)
from codeoracle.services.indexing.orchestrator import IndexingOrchestrator
from codeoracle.core import streams


@pytest_asyncio.fixture
async def orchestrator(
    valkey_queue_client: ValkeyQueueClient,
    async_session_factory: async_sessionmaker[AsyncSession],
) -> AsyncIterator[IndexingOrchestrator]:
    orch = IndexingOrchestrator(
        queue_client=valkey_queue_client,
        db_session_factory=async_session_factory,
        poll_interval_seconds=0.1,
    )
    yield orch


@pytest.mark.asyncio
async def test_per_file_extractor_enqueues_n_messages(
    orchestrator: IndexingOrchestrator,
    valkey_queue_client: ValkeyQueueClient,
) -> None:
    """Per-file extractor (joern, opengrep, embed): N files → N messages."""
    await orchestrator._enqueue_all(  # type: ignore[attr-defined]
        snapshot_sha="sha-test-perfile",
        repo_root="/tmp/fake-repo",
        files=["a.py", "b.py", "c.py"],
        extractor_kinds=("joern",),
    )

    pending = await valkey_queue_client.peek_pending_count(streams.STREAM_INDEXING_JOERN)
    assert pending == 3


@pytest.mark.asyncio
async def test_project_mode_extractor_enqueues_one_message(
    orchestrator: IndexingOrchestrator,
    valkey_queue_client: ValkeyQueueClient,
) -> None:
    """Project-mode extractor (scip): N files → 1 message containing all files."""
    await orchestrator._enqueue_all(  # type: ignore[attr-defined]
        snapshot_sha="sha-test-project",
        repo_root="/tmp/fake-repo",
        files=["a.py", "b.py", "c.py"],
        extractor_kinds=("scip",),
    )

    pending = await valkey_queue_client.peek_pending_count(streams.STREAM_INDEXING_SCIP)
    assert pending == 1

    msg_payload = await valkey_queue_client.peek_first_payload(streams.STREAM_INDEXING_SCIP)
    assert msg_payload["snapshot_sha"] == "sha-test-project"
    assert msg_payload["batch_id"] == "sha-test-project"
    assert msg_payload["repo_root"] == "/tmp/fake-repo"
    assert msg_payload["files"] == ["a.py", "b.py", "c.py"]


@pytest.mark.asyncio
async def test_mixed_kinds_enqueue_correctly(
    orchestrator: IndexingOrchestrator,
    valkey_queue_client: ValkeyQueueClient,
) -> None:
    """Mixed: per_file + project + per_file enqueue independently on their streams."""
    await orchestrator._enqueue_all(  # type: ignore[attr-defined]
        snapshot_sha="sha-mixed",
        repo_root="/tmp/fake-repo",
        files=["a.py", "b.py"],
        extractor_kinds=("joern", "scip", "opengrep", "embed"),
    )

    assert await valkey_queue_client.peek_pending_count(streams.STREAM_INDEXING_JOERN) == 2
    assert await valkey_queue_client.peek_pending_count(streams.STREAM_INDEXING_SCIP) == 1
    assert await valkey_queue_client.peek_pending_count(streams.STREAM_INDEXING_OPENGREP) == 2
    assert await valkey_queue_client.peek_pending_count(streams.STREAM_INDEXING_EMBED) == 2


@pytest.mark.asyncio
async def test_barrier_closes_when_project_worker_writes_n_outcomes(
    orchestrator: IndexingOrchestrator,
    async_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Project-mode barrier semantics: orchestrator polls outcome count by
    (snapshot_sha, extractor), not by message count. A worker that writes
    N outcomes from 1 message closes the barrier exactly when N rows exist."""
    snapshot_sha = "sha-barrier"
    files = ["x.py", "y.py", "z.py"]

    # Simulate the SCIP worker writing 3 outcomes from one consumed message.
    async with async_session_factory() as session:
        repo = SnapshotFileOutcomeRepository(session)
        for f in files:
            await repo.upsert(
                SnapshotFileOutcomeRow(
                    snapshot_sha=snapshot_sha,
                    file_path=f,
                    extractor="scip",
                    outcome="success",
                    error_message=None,
                )
            )
        await session.commit()

    # The orchestrator's _is_barrier_closed for ("scip",) and 3 files: true.
    closed = await orchestrator._is_barrier_closed(  # type: ignore[attr-defined]
        snapshot_sha=snapshot_sha,
        files=files,
        extractor_kinds=("scip",),
    )
    assert closed


# ---- G14: per-extractor file routing tests --------------------------------


@pytest.mark.asyncio
async def test_g14_code_extractor_filters_out_doc_files(
    orchestrator: IndexingOrchestrator,
    valkey_queue_client: ValkeyQueueClient,
) -> None:
    """joern accepts only .py / .pyi — .md files must NOT reach the joern stream."""
    await orchestrator._enqueue_all(  # type: ignore[attr-defined]
        snapshot_sha="sha-g14-code-filter",
        repo_root="/tmp/fake-repo",
        files=["a.py", "README.md", "docs/intro.md", "b.py"],
        extractor_kinds=("joern",),
    )

    # Only a.py + b.py routed to joern.
    pending = await valkey_queue_client.peek_pending_count(streams.STREAM_INDEXING_JOERN)
    assert pending == 2


@pytest.mark.asyncio
async def test_g14_embed_extractor_accepts_all_files(
    orchestrator: IndexingOrchestrator,
    valkey_queue_client: ValkeyQueueClient,
) -> None:
    """embed has accepted_extensions=None — all files reach the embed stream."""
    await orchestrator._enqueue_all(  # type: ignore[attr-defined]
        snapshot_sha="sha-g14-embed-all",
        repo_root="/tmp/fake-repo",
        files=["a.py", "README.md", "docs/intro.md", "b.py"],
        extractor_kinds=("embed",),
    )

    pending = await valkey_queue_client.peek_pending_count(streams.STREAM_INDEXING_EMBED)
    assert pending == 4  # all four files


@pytest.mark.asyncio
async def test_g14_scip_project_message_carries_only_code_files(
    orchestrator: IndexingOrchestrator,
    valkey_queue_client: ValkeyQueueClient,
) -> None:
    """SCIP (project-mode + code-only filter): the single project message's
    `files` list contains only routed code files; doc files are excluded."""
    await orchestrator._enqueue_all(  # type: ignore[attr-defined]
        snapshot_sha="sha-g14-scip",
        repo_root="/tmp/fake-repo",
        files=["a.py", "README.md", "b.py", "CHANGELOG.md"],
        extractor_kinds=("scip",),
    )

    pending = await valkey_queue_client.peek_pending_count(streams.STREAM_INDEXING_SCIP)
    assert pending == 1

    payload = await valkey_queue_client.peek_first_payload(streams.STREAM_INDEXING_SCIP)
    assert payload["files"] == ["a.py", "b.py"]  # README/CHANGELOG filtered out


@pytest.mark.asyncio
async def test_g14_all_doc_snapshot_skips_code_extractor_enqueue(
    orchestrator: IndexingOrchestrator,
    valkey_queue_client: ValkeyQueueClient,
) -> None:
    """When the snapshot has zero code files, code-extractor streams stay empty
    (no zero-files project message, no per-file messages)."""
    await orchestrator._enqueue_all(  # type: ignore[attr-defined]
        snapshot_sha="sha-g14-all-docs",
        repo_root="/tmp/fake-repo",
        files=["README.md", "docs/intro.md", "CHANGELOG.md"],
        extractor_kinds=("joern", "scip", "opengrep", "embed"),
    )

    assert await valkey_queue_client.peek_pending_count(streams.STREAM_INDEXING_JOERN) == 0
    assert await valkey_queue_client.peek_pending_count(streams.STREAM_INDEXING_SCIP) == 0
    assert await valkey_queue_client.peek_pending_count(streams.STREAM_INDEXING_OPENGREP) == 0
    # Embed accepts all → 3 messages.
    assert await valkey_queue_client.peek_pending_count(streams.STREAM_INDEXING_EMBED) == 3


@pytest.mark.asyncio
async def test_g14_barrier_trivially_closes_when_no_code_routed(
    orchestrator: IndexingOrchestrator,
) -> None:
    """A snapshot of only .md files passed to extractor_kinds=("joern",) closes
    the joern barrier immediately — expected=0, completed+failed=0.

    Without G14 this would have hung forever: expected was len(files) raw, so
    the barrier waited for joern outcomes on .md files that were never enqueued.
    """
    closed = await orchestrator._is_barrier_closed(  # type: ignore[attr-defined]
        snapshot_sha="sha-g14-no-code",
        files=["README.md", "docs/intro.md"],
        extractor_kinds=("joern",),
    )
    assert closed


@pytest.mark.asyncio
async def test_g14_collect_outcomes_returns_success_for_zero_expected(
    orchestrator: IndexingOrchestrator,
) -> None:
    """_collect_outcomes returns "success" (not "failed") for an extractor whose
    filtered file set is empty — no work routed = trivially complete."""
    out = await orchestrator._collect_outcomes(  # type: ignore[attr-defined]
        snapshot_sha="sha-g14-collect-zero",
        files=["README.md"],
        extractor_kinds=("joern", "scip", "opengrep", "embed"),
    )
    assert out["joern"] == "success"
    assert out["scip"] == "success"
    assert out["opengrep"] == "success"
    # Embed gets the .md and writes nothing yet → barrier reports "failed"
    # because expected=1, total=0 (barrier timed out OR the worker hasn't
    # written yet). This branch is exercised by other tests; here we only
    # pin the zero-expected branch on the code extractors.


def test_g14_filter_files_for_helper_is_pure_and_classmethod() -> None:
    """`_filter_files_for` is a @classmethod (no instance state) — callable on
    the class directly. Tests AND production code both go through it."""
    routed_joern = IndexingOrchestrator._filter_files_for(  # type: ignore[attr-defined]
        files=["a.py", "b.PY", "c.pyi", "README.md", "data.txt"],
        kind="joern",
    )
    assert routed_joern == ["a.py", "b.PY", "c.pyi"]  # case-insensitive on suffix

    routed_embed = IndexingOrchestrator._filter_files_for(  # type: ignore[attr-defined]
        files=["a.py", "README.md", "data.txt"],
        kind="embed",
    )
    assert routed_embed == ["a.py", "README.md", "data.txt"]
```

- [ ] **Step 5: Run test to verify it fails**

Run: `uv run pytest tests/unit/services/indexing/test_orchestrator_project_mode.py -v`

Expected: FAIL — orchestrator may have `_STREAM_BY_KIND` but not `_INFO_BY_KIND`; `_enqueue_all` doesn't branch on mode; `STREAM_INDEXING_SCIP` may or may not be reachable from the orchestrator yet.

- [ ] **Step 6: Rewrite the orchestrator**

Modify `src/codeoracle/services/indexing/orchestrator.py`. The diff is concentrated: replace `_STREAM_BY_KIND` (Plan 5) with `_INFO_BY_KIND` (now carrying `accepted_extensions`), rewrite `_enqueue_all` to route by extension, and extend the barrier helpers (`_is_barrier_closed`, `_collect_outcomes`) to compute expected counts per-extractor from the filtered file set.

**Why per-extractor routing (G14):** Code extractors (joern, scip, opengrep) only operate on Python files (`.py`, `.pyi`); they will silently produce `outcome="skipped"` (or worse, `outcome="failed"` on a syntax error) for `.md` / `.rst` / `.txt`. The embed worker (this plan, Task 12 with ParagraphChunker) handles ALL files including documentation. Routing at fan-out time means code extractors never see doc files, the per-file barrier respects the filtered count, and ingest cost stays bounded. See Architectural Invariant 5 and Cross-Plan Downstream Contracts (Plan 8 / Plan 10).

```python
from __future__ import annotations

import asyncio
import pathlib
from collections.abc import Iterable
from typing import NamedTuple

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from codeoracle.adapters.valkey.client import ValkeyQueueClient
from codeoracle.db.repositories.snapshot_file_outcome import SnapshotFileOutcomeRepository
from codeoracle.services.indexing.extractor_mode import ExtractorMode
from codeoracle.core import streams


# Module-level constants for per-extractor file routing (G14).
#
# `_CODE_EXTENSIONS`: file suffixes that the code-graph extractors operate on.
# Joern, SCIP, and OpenGrep all target Python today; this set widens when
# multi-language support lands (out of scope for Plan 6).
#
# `None` in `_ExtractorInfo.accepted_extensions` means "all files" — only
# `embed` uses that, because `chunker_for` (Task 12 Step 2) dispatches
# `ParagraphChunker` for doc files and `LineChunker` for code, so embed has
# no kind-restriction at the orchestrator level.
_CODE_EXTENSIONS: frozenset[str] = frozenset({".py", ".pyi"})


class _ExtractorInfo(NamedTuple):
    """Per-kind orchestrator metadata: stream + execution mode + file filter.

    `accepted_extensions = None` means the extractor accepts all files.
    Otherwise, fan-out filters `files` to entries whose lowercased suffix
    is in this set; the barrier's expected count is the size of the filtered
    list (NOT len(files) raw), so an extractor that legitimately has no work
    in a given snapshot is considered trivially complete.
    """

    stream: str
    mode: ExtractorMode
    accepted_extensions: frozenset[str] | None  # None = accept all


class IndexingOrchestrator:
    """Fans out extraction work across N worker streams and waits on the
    SnapshotFileOutcome barrier to close.

    Owned by Plan 5; extended by Plan 6 to add `_INFO_BY_KIND` (replacing
    `_STREAM_BY_KIND`), project-mode dispatch, and per-extractor file
    routing (G14). Plan 7 will further extend to handle continuous-mode
    (traces).
    """

    _INFO_BY_KIND: dict[str, _ExtractorInfo] = {
        "joern": _ExtractorInfo(
            stream=streams.STREAM_INDEXING_JOERN,
            mode="per_file",
            accepted_extensions=_CODE_EXTENSIONS,
        ),
        "scip": _ExtractorInfo(
            stream=streams.STREAM_INDEXING_SCIP,
            mode="project",
            accepted_extensions=_CODE_EXTENSIONS,
        ),
        "opengrep": _ExtractorInfo(
            stream=streams.STREAM_INDEXING_OPENGREP,
            mode="per_file",
            accepted_extensions=_CODE_EXTENSIONS,
        ),
        "embed": _ExtractorInfo(
            stream=streams.STREAM_INDEXING_EMBED,
            mode="per_file",
            accepted_extensions=None,  # all files (chunker_for dispatches per ext)
        ),
    }

    def __init__(
        self,
        *,
        queue_client: ValkeyQueueClient,
        db_session_factory: async_sessionmaker[AsyncSession],
        poll_interval_seconds: float = 1.0,
        max_wait_seconds: float = 600.0,
    ) -> None:
        self._queue = queue_client
        self._db_factory = db_session_factory
        self._poll = poll_interval_seconds
        self._max_wait = max_wait_seconds

    @classmethod
    def _filter_files_for(cls, files: Iterable[str], kind: str) -> list[str]:
        """Return the subset of `files` accepted by extractor `kind`.

        Pure / classmethod so the same filter logic is reachable from
        `_enqueue_all` (decide what to enqueue) and `_is_barrier_closed` /
        `_collect_outcomes` (decide how many outcomes to expect). Keeping
        the rule in ONE place is load-bearing — drift between fan-out and
        barrier means the barrier hangs forever (expecting outcomes that
        will never be written).
        """
        info = cls._INFO_BY_KIND[kind]
        if info.accepted_extensions is None:
            return list(files)
        return [f for f in files if pathlib.PurePosixPath(f).suffix.lower() in info.accepted_extensions]

    async def fan_out_and_wait(
        self,
        *,
        snapshot_sha: str,
        repo_root: str,
        files: list[str],
        extractor_kinds: tuple[str, ...],
    ) -> dict[str, str]:
        """Enqueue per-mode messages, then poll the barrier until all
        outcomes have landed or `max_wait_seconds` elapses.

        Returns: `dict[extractor_kind, "success" | "partial" | "failed"]`.

        - "success": every (file, extractor) row has outcome="success" (or no
          work was routed to this extractor, which is trivially complete).
        - "failed": every routed (file, extractor) row has outcome="failed".
        - "partial": mixed. The IndexingService's
          `_OUTCOME_TO_EXTRACTOR_OUTCOME` map treats "partial" as SUCCESS
          (any-files-landed semantics).
        """
        await self._enqueue_all(
            snapshot_sha=snapshot_sha,
            repo_root=repo_root,
            files=files,
            extractor_kinds=extractor_kinds,
        )

        deadline = asyncio.get_event_loop().time() + self._max_wait
        while True:
            if await self._is_barrier_closed(
                snapshot_sha=snapshot_sha,
                files=files,
                extractor_kinds=extractor_kinds,
            ):
                break
            if asyncio.get_event_loop().time() > deadline:
                # Barrier did not close within max_wait. The IndexingService
                # is responsible for transitioning the Snapshot to a terminal
                # status (likely FAILED) and surfacing the timeout.
                break
            await asyncio.sleep(self._poll)

        return await self._collect_outcomes(
            snapshot_sha=snapshot_sha,
            files=files,
            extractor_kinds=extractor_kinds,
        )

    async def _enqueue_all(
        self,
        *,
        snapshot_sha: str,
        repo_root: str,
        files: list[str],
        extractor_kinds: tuple[str, ...],
    ) -> None:
        """Enqueue messages per-extractor according to its ExtractorMode
        and `accepted_extensions` filter (G14).

        per_file: one message per (filtered_file, extractor).
        project:  one message per (snapshot, extractor), carrying the
                  filtered file list.

        If an extractor's filter excludes every file in the snapshot
        (`routed == []`), no messages are enqueued for it. The barrier
        helpers treat that extractor as trivially complete (0 expected,
        0 written).
        """
        for kind in extractor_kinds:
            info = self._INFO_BY_KIND[kind]
            routed = self._filter_files_for(files, kind)
            if not routed:
                # No files of this kind in this snapshot — skip enqueue.
                # _is_barrier_closed treats expected=0 as trivially closed.
                continue
            if info.mode == "per_file":
                for file_path in routed:
                    await self._queue.enqueue(
                        info.stream,
                        {
                            "batch_id": snapshot_sha,
                            "snapshot_sha": snapshot_sha,
                            "file_path": file_path,
                            "repo_root": repo_root,
                        },
                    )
            elif info.mode == "project":
                await self._queue.enqueue(
                    info.stream,
                    {
                        "batch_id": snapshot_sha,
                        "snapshot_sha": snapshot_sha,
                        "repo_root": repo_root,
                        "files": routed,
                    },
                )
            else:
                # Defensive — `ExtractorMode` is a Literal so this branch is
                # ostensibly unreachable. Raised as a programming error rather
                # than silently dropping work.
                raise AssertionError(f"unknown ExtractorMode: {info.mode!r}")

    async def _is_barrier_closed(
        self,
        *,
        snapshot_sha: str,
        files: list[str],
        extractor_kinds: tuple[str, ...],
    ) -> bool:
        """Closed when COUNT(completed) + COUNT(failed) >= expected for
        every extractor kind. Expected is computed per-extractor from the
        filtered file set (G14) — an extractor that accepts no file in the
        snapshot has expected=0 and is trivially closed.

        Counts are queried via the repository; the orchestrator never reads
        outcome rows directly.
        """
        async with self._db_factory() as session:
            repo = SnapshotFileOutcomeRepository(session)
            for kind in extractor_kinds:
                expected = len(self._filter_files_for(files, kind))
                if expected == 0:
                    continue  # No work routed; trivially closed.
                completed = await repo.count_completed(snapshot_sha=snapshot_sha, extractor=kind)
                failed = await repo.count_failed(snapshot_sha=snapshot_sha, extractor=kind)
                if completed + failed < expected:
                    return False
        return True

    async def _collect_outcomes(
        self,
        *,
        snapshot_sha: str,
        files: list[str],
        extractor_kinds: tuple[str, ...],
    ) -> dict[str, str]:
        """Collapse per-file outcome counts into one summary per extractor,
        using per-extractor expected counts from the filtered file set (G14).

        Per Plan 5's IndexingService.OUTCOME_TO_EXTRACTOR_OUTCOME mapping:
        - expected == 0     → "success" (no work routed = trivially complete)
        - all success       → "success"
        - all failed        → "failed"
        - mixed             → "partial"
        - missing (barrier timed out) → "failed"
        """
        out: dict[str, str] = {}
        async with self._db_factory() as session:
            repo = SnapshotFileOutcomeRepository(session)
            for kind in extractor_kinds:
                expected = len(self._filter_files_for(files, kind))
                if expected == 0:
                    out[kind] = "success"  # No work routed = trivially complete.
                    continue
                ok = await repo.count_completed(snapshot_sha=snapshot_sha, extractor=kind)
                bad = await repo.count_failed(snapshot_sha=snapshot_sha, extractor=kind)
                total = ok + bad
                if total < expected:
                    out[kind] = "failed"  # incomplete = treat as failure for the service
                elif ok == expected:
                    out[kind] = "success"
                elif bad == expected:
                    out[kind] = "failed"
                else:
                    out[kind] = "partial"
        return out
```

- [ ] **Step 7: Verify `ValkeyQueueClient` test helpers exist**

The `peek_pending_count` and `peek_first_payload` test helpers on `ValkeyQueueClient` are used by the project-mode tests. Plan 5 ships them as part of the queue client (debug / testability surface). If they are missing:

- Add `peek_pending_count(stream) -> int` on `ValkeyQueueClient` — `XLEN stream`.
- Add `peek_first_payload(stream) -> dict[str, Any]` — `XRANGE stream - + COUNT 1`, JSON-decode the payload field.

If the methods are present from Plan 5, skip this step.

- [ ] **Step 8: Run all Task 2 tests**

Run: `uv run pytest tests/unit/services/indexing/test_orchestrator_extractor_mode_literal.py tests/unit/services/indexing/test_orchestrator_project_mode.py -v`

Expected: PASS (13 tests — 2 in `test_orchestrator_extractor_mode_literal.py` (literal pinning + info-map consistency); 4 in `test_orchestrator_project_mode.py` for per_file/project/mixed/barrier-N-outcomes; 7 G14 tests for code-filter / embed-all / scip-project-filter / all-doc-skip / trivial-barrier-close / collect-zero-expected / `_filter_files_for` helper purity).

- [ ] **Step 9: Run full Plan 5 + Plan 6 indexing test suite to confirm no regression**

Run: `uv run pytest tests/unit/services/indexing/ tests/integration/services/indexing/ -v`

Expected: all green. Plan 5's existing orchestrator tests (single-kind joern fan-out, barrier-close-on-N-outcomes) continue passing because per-file behavior is preserved.

- [ ] **Step 10: Commit**

```bash
git add tests/unit/services/indexing/test_orchestrator_extractor_mode_literal.py \
        tests/unit/services/indexing/test_orchestrator_project_mode.py \
        src/codeoracle/services/indexing/extractor_mode.py \
        src/codeoracle/services/indexing/orchestrator.py
git commit -m "feat(indexing): add ExtractorMode literal + project-mode enqueue (Plan 6 Task 2)"
```

---

### Task 3: Embed Chunk Constants in EmbeddingConfig

**Files:**
- Modify: `src/codeoracle/core/config.py` (Plan 3 created `EmbeddingConfig`; add three fields — `embed_chunk_lines`, `embed_chunk_overlap`, `doc_chunk_max_lines`)
- Test: `tests/unit/core/test_embedding_config_chunk_constants.py` (new)

**Why:** Plan 6 must pin chunk parameters in `EmbeddingConfig` (not in the worker, not in a constants file) so that Plan 8's `CodeSpanResolver` + `DocParagraphResolver` read the SAME values when looking up chunks by line range. Drift between writer (here) and reader (Plan 8) would silently misalign line boundaries.

**Two chunker families:** Plan 6 Task 12 dispatches `LineChunker` for code files (governed by `embed_chunk_lines` + `embed_chunk_overlap`) and `ParagraphChunker` for documentation files (governed by `doc_chunk_max_lines`). All three constants live on `EmbeddingConfig` so a single `Settings()` instance carries both regimes — env-override flows uniformly. See Architectural Invariant 5 for the storage-shape invariant.

- [ ] **Step 1: Write the failing test**

Create `tests/unit/core/test_embedding_config_chunk_constants.py`:

```python
"""EmbeddingConfig now carries embed_chunk_lines + embed_chunk_overlap.

Defaults: 100 lines per chunk, 20-line overlap between adjacent chunks. Both
fields are env-overridable via the parent Settings(BaseSettings)'s nested-env
cascade — env vars `CODEORACLE_EMBEDDING__EMBED_CHUNK_LINES` and
`CODEORACLE_EMBEDDING__EMBED_CHUNK_OVERLAP` (see Plan 3 `core/config.py` for the
env_prefix='CODEORACLE_' + env_nested_delimiter='__' parent config).

EmbeddingConfig itself is a `BaseModel` (not `BaseSettings`) per Plan 3 — env
binding lives on the parent. Tests that construct EmbeddingConfig directly
must therefore pass the Plan 3 required fields (`model`, `dim`); tests that
exercise env-override go through `Settings()` so the parent's cascade fires.
"""

import os
import pathlib

import pytest

from codeoracle.core.config import EmbeddingConfig, Settings


# Helper: Plan 3 made `model` and `dim` required-no-default to fail fast on
# missing env injection. Tests that don't care about those fields fill them
# with sane defaults here.
#
# Why we pass `kind="mock"` even though the validator doesn't require it:
#   - Plan 6 Task 7 adds `kind` as a Literal["openai", "vllm", "mock"] field
#     with default "openai". The api_key precondition for openai/vllm lives
#     on OpenAIEmbeddingsProvider.__init__, NOT on the validator — so a test
#     can construct EmbeddingConfig() with kind="openai" + api_key="" today
#     and the construction succeeds. Defensive `kind="mock"` here protects
#     us from a hypothetical future kind-keyed validator rule (e.g., "kind
#     must match base_url scheme") that might be added without a sweep of
#     test-helpers.
#   - The `kind` field doesn't exist yet in Task 3 (it's added by Task 7),
#     but Pydantic v2 BaseModel defaults to `extra='ignore'`, so unknown
#     kwargs are silently dropped pre-Task 7 and honored post-Task 7 —
#     forward-compatible regardless of which task this helper runs against.
def _ec(**overrides) -> EmbeddingConfig:
    base = {"model": "text-embedding-3-small", "dim": 1536, "kind": "mock"}
    base.update(overrides)
    return EmbeddingConfig(**base)


def test_default_chunk_lines_is_100() -> None:
    config = _ec()
    assert config.embed_chunk_lines == 100


def test_default_chunk_overlap_is_20() -> None:
    config = _ec()
    assert config.embed_chunk_overlap == 20


def test_env_override_chunk_lines(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
) -> None:
    """Env override flows through parent Settings's `CODEORACLE_EMBEDDING__` cascade.

    NOTE: we drive env override through Settings(), not EmbeddingConfig
    directly — EmbeddingConfig is a BaseModel and does not itself read env.
    """
    monkeypatch.chdir(tmp_path)  # escape repo .env so it doesn't bleed in
    monkeypatch.setenv("CODEORACLE_EMBEDDING__MODEL", "text-embedding-3-small")
    monkeypatch.setenv("CODEORACLE_EMBEDDING__DIM", "1536")
    monkeypatch.setenv("CODEORACLE_DB__PASSWORD", "test-db-pw")  # D5-4: required, no default
    monkeypatch.setenv("CODEORACLE_EMBEDDING__EMBED_CHUNK_LINES", "50")
    settings = Settings()
    assert settings.embedding.embed_chunk_lines == 50


def test_env_override_chunk_overlap(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("CODEORACLE_EMBEDDING__MODEL", "text-embedding-3-small")
    monkeypatch.setenv("CODEORACLE_EMBEDDING__DIM", "1536")
    monkeypatch.setenv("CODEORACLE_DB__PASSWORD", "test-db-pw")  # D5-4: required, no default
    monkeypatch.setenv("CODEORACLE_EMBEDDING__EMBED_CHUNK_OVERLAP", "5")
    settings = Settings()
    assert settings.embedding.embed_chunk_overlap == 5


def test_default_doc_chunk_max_lines_is_200() -> None:
    """ParagraphChunker (doc files) is bounded by doc_chunk_max_lines.

    Default 200 — well above typical markdown paragraph length (5-20 lines),
    so most paragraphs sail through unsplit; only pathologically long blocks
    (large code-fence dumps, generated tables) hit the split path.
    """
    config = _ec()
    assert config.doc_chunk_max_lines == 200


def test_env_override_doc_chunk_max_lines(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("CODEORACLE_EMBEDDING__MODEL", "text-embedding-3-small")
    monkeypatch.setenv("CODEORACLE_EMBEDDING__DIM", "1536")
    monkeypatch.setenv("CODEORACLE_DB__PASSWORD", "test-db-pw")  # D5-4: required, no default
    monkeypatch.setenv("CODEORACLE_EMBEDDING__DOC_CHUNK_MAX_LINES", "80")
    settings = Settings()
    assert settings.embedding.doc_chunk_max_lines == 80


def test_overlap_must_be_less_than_chunk_size() -> None:
    """Sanity guard: overlap >= chunk_size would create infinite loops in
    the chunker. The model validator rejects this."""
    with pytest.raises(ValueError):
        _ec(embed_chunk_lines=50, embed_chunk_overlap=50)
    with pytest.raises(ValueError):
        _ec(embed_chunk_lines=50, embed_chunk_overlap=100)


def test_degenerate_stride_rejected() -> None:
    """D-6.13: overlap < chunk_lines is not sufficient. A tiny stride
    (chunk_lines - overlap < _MIN_CHUNK_STRIDE) yields O(file) near-duplicate
    chunks even though overlap < chunk_lines holds. The validator rejects it."""
    # stride 1 — the canonical degenerate case
    with pytest.raises(ValueError):
        _ec(embed_chunk_lines=2, embed_chunk_overlap=1)
    # stride 5 — still below the floor of 10
    with pytest.raises(ValueError):
        _ec(embed_chunk_lines=100, embed_chunk_overlap=95)
    # stride exactly at the floor (10) is accepted
    cfg = _ec(embed_chunk_lines=30, embed_chunk_overlap=20)
    assert cfg.embed_chunk_lines - cfg.embed_chunk_overlap == 10


def test_negative_chunk_size_rejected() -> None:
    with pytest.raises(ValueError):
        _ec(embed_chunk_lines=0)
    with pytest.raises(ValueError):
        _ec(embed_chunk_lines=-10)


def test_negative_doc_chunk_max_lines_rejected() -> None:
    """doc_chunk_max_lines must be >= 1; zero/negative would mean
    ParagraphChunker emits zero chunks for every doc file (silently broken)."""
    with pytest.raises(ValueError):
        _ec(doc_chunk_max_lines=0)
    with pytest.raises(ValueError):
        _ec(doc_chunk_max_lines=-1)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/core/test_embedding_config_chunk_constants.py -v`

Expected: FAIL — `EmbeddingConfig` from Plan 3 has `model: str` and `dim: int` (both required, no defaults) but no chunk fields.

- [ ] **Step 3: Add the two fields to EmbeddingConfig**

Modify `src/codeoracle/core/config.py`. Locate the existing `EmbeddingConfig` (Plan 3, a **BaseModel** — env-binding is on the parent `Settings(BaseSettings)`, see Plan 3 `model_config = SettingsConfigDict(env_prefix="CODEORACLE_", env_nested_delimiter="__")`). Add the two fields + a model validator:

```python
# In src/codeoracle/core/config.py, alongside Plan 3's `model` and `dim`.

from pydantic import BaseModel, Field, model_validator

# Minimum forward stride (chunk_lines - overlap) the LineChunker is allowed to
# take. A stride below this floor is degenerate: adjacent chunks would share
# nearly all their lines (near-duplicate embeddings) and the chunk count would
# blow up to O(file_lines / stride) — e.g. chunk_lines=2, overlap=1 → stride=1 →
# one chunk per line. The floor of 10 caps chunk count at ~file_lines/10 and
# keeps every sane geometry (the smallest in-tree config is chunk_lines=10,
# overlap=0 → stride=10). See D-6.13.
_MIN_CHUNK_STRIDE = 10


class EmbeddingConfig(BaseModel):  # NOTE: BaseModel, not BaseSettings — Plan 3 shape
    """Embedding model + dimension + chunking configuration.

    Plan 3 declared `model: str = Field(min_length=1)` and `dim: int = Field(ge=64, le=4096)`
    as required-no-default to fail fast if env injection is missed (a process
    that boots without `CODEORACLE_EMBEDDING__MODEL` + `CODEORACLE_EMBEDDING__DIM` would
    otherwise silently produce wrong-dim embeddings that mismatch migration
    0002's pgvector column dim). Plan 6 PRESERVES that posture: the new fields
    we add below all have defaults, so the existing required-required
    contract on `model`/`dim` is unchanged.

    Plan 6 adds chunking parameters. The writer (`workers/embed_indexer`,
    Plan 6) and the reader (`services/resolvers/code_span`, Plan 8) load
    the SAME `EmbeddingConfig` instance via `get_settings().embedding`, so
    any env-override flows to both sides in lockstep.

    Env vars (cascaded through parent Settings's `env_prefix="CODEORACLE_"` +
    `env_nested_delimiter="__"`):
      - CODEORACLE_EMBEDDING__MODEL                (Plan 3, required)
      - CODEORACLE_EMBEDDING__DIM                  (Plan 3, required)
      - CODEORACLE_EMBEDDING__EMBED_CHUNK_LINES    (Plan 6 Task 3 — LineChunker, code files)
      - CODEORACLE_EMBEDDING__EMBED_CHUNK_OVERLAP  (Plan 6 Task 3 — LineChunker, code files)
      - CODEORACLE_EMBEDDING__DOC_CHUNK_MAX_LINES  (Plan 6 Task 3 — ParagraphChunker, doc files)
      - CODEORACLE_EMBEDDING__KIND                 (Plan 6 Task 7)
      - CODEORACLE_EMBEDDING__BASE_URL             (Plan 6 Task 7)
      - CODEORACLE_EMBEDDING__API_KEY              (Plan 6 Task 7)
    """

    # Plan 3 fields — DO NOT MODIFY. Required, no defaults, fail fast on missing env.
    model: str = Field(min_length=1)
    dim: int = Field(ge=64, le=4096)

    # Plan 6 Task 3 additions. Two chunker families share this config:
    #   - LineChunker (code files: .py, .pyi) reads embed_chunk_lines + embed_chunk_overlap
    #   - ParagraphChunker (doc files: .md, .rst, .adoc, .markdown) reads doc_chunk_max_lines
    # See Architectural Invariant 5 and `codeoracle.services.embedding.chunker.chunker_for`.
    embed_chunk_lines: int = Field(default=100, ge=1, description="Lines per LineChunker chunk (code files).")
    embed_chunk_overlap: int = Field(default=20, ge=0, description="Line overlap between adjacent LineChunker chunks.")
    doc_chunk_max_lines: int = Field(default=200, ge=1, description="Max lines per ParagraphChunker chunk (doc files); paragraphs longer than this split via LineChunker-style stride.")

    @model_validator(mode="after")
    def _validate_embedding_config(self) -> "EmbeddingConfig":
        """Sole validator for EmbeddingConfig — enforces chunk-geometry only.

        Plan 6 Task 7 adds `kind` / `base_url` / `api_key` as fields but does
        NOT extend this validator. The api_key precondition lives in
        OpenAIEmbeddingsProvider.__init__ (Task 7 Step 5) — a model validator
        runs on every instance including test-only instances that never build
        a real provider, so the check belongs on the class that consumes the
        key, not on the config that carries it.

        Invariant: exactly one @model_validator on EmbeddingConfig. If a
        future task wants to add another rule, EDIT this function in place,
        do not add a second @model_validator.
        """
        if self.embed_chunk_overlap >= self.embed_chunk_lines:
            raise ValueError(
                f"embed_chunk_overlap ({self.embed_chunk_overlap}) must be less than "
                f"embed_chunk_lines ({self.embed_chunk_lines}) — otherwise the chunker loops."
            )
        # Reject degenerate strides. overlap < chunk_lines alone is not enough:
        # chunk_lines=2, overlap=1 satisfies it but yields stride=1 → O(file)
        # near-duplicate chunks. Require the forward stride to clear a sane floor
        # so chunk count stays bounded and adjacent chunks carry distinct content.
        stride = self.embed_chunk_lines - self.embed_chunk_overlap
        if stride < _MIN_CHUNK_STRIDE:
            raise ValueError(
                f"chunk stride (embed_chunk_lines - embed_chunk_overlap = {stride}) "
                f"must be >= {_MIN_CHUNK_STRIDE}; a smaller stride produces "
                f"O(file) near-duplicate chunks. Increase embed_chunk_lines or "
                f"decrease embed_chunk_overlap."
            )
        return self

    # NO `class Config:` / `model_config = ConfigDict(...)` here. EmbeddingConfig
    # is a BaseModel — env_file, env_prefix, etc. all live on the parent
    # Settings(BaseSettings). Per-class env config would be ignored on BaseModel.
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/unit/core/test_embedding_config_chunk_constants.py -v`

Expected: PASS (10 tests — 7 for embed_chunk_lines + embed_chunk_overlap defaults / env / validator (incl. the degenerate-stride rejection, D-6.13), 3 for doc_chunk_max_lines default / env / negative-rejection).

- [ ] **Step 5: Run the wider config test suite to confirm Plan 3 config still passes**

Run: `uv run pytest tests/unit/core/ -v`

Expected: all green.

- [ ] **Step 6: Commit**

```bash
git add tests/unit/core/test_embedding_config_chunk_constants.py src/codeoracle/core/config.py
git commit -m "feat(config): pin embed chunk geometry in EmbeddingConfig (Plan 6 Task 3)"
```

---

### Task 4: Migration 0005 — `code_references` Table

**Files:**
- Create: `src/codeoracle/db/migrations/versions/0005_code_references.py`
- Create: `src/codeoracle/db/models/code_reference.py`
- Test: `tests/integration/db/test_migration_0005_smoke.py` (new)
- Test: `tests/integration/db/test_code_reference_model_roundtrip.py` (new)

**Schema rationale:** `code_references` stores per-snapshot textual references extracted by OpenGrep. Composite PK `(snapshot_sha, file_path, symbol_name, line_number, kind)` — multiple references to the same symbol can exist on the same file at different lines, and the SAME line can have multiple references of different kinds (a function call AND a string-literal mentioning the same name). Indexed by `(symbol_name, snapshot_sha)` for Plan 8's `count_by_symbol` queries.

**Why a separate table (not AGE):** OpenGrep results are textual matches keyed by symbol name + rule + line — fundamentally tabular, not graph. Putting them in AGE would waste graph storage on facts that don't participate in graph traversal. Plan 9's verifier joins `code_references` rows with AGE `:Symbol` nodes via FQN; no graph storage needed for the textual side.

- [ ] **Step 1: Write the migration smoke test**

Create `tests/integration/db/test_migration_0005_smoke.py`:

```python
"""Smoke test: alembic upgrade head applies 0005 cleanly on a fresh testcontainers PG.

This test does NOT assert specific column types in detail — those are exercised
by the ORM round-trip test (test_code_reference_model_roundtrip.py). It only
asserts the table exists, has the expected composite PK, and has the two
expected indexes.

Fixtures (Plan 3):
- `alembic_config_for_test_pg`: yields a configured alembic Config bound to a
  fresh testcontainers postgres instance.
- `async_session_factory`: factory bound to the SAME instance after upgrade.
"""

from __future__ import annotations

import pytest
from sqlalchemy import inspect, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker


@pytest.mark.asyncio
async def test_code_references_table_created(
    async_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with async_session_factory() as session:
        result = await session.execute(
            text(
                """
                SELECT column_name, data_type, is_nullable
                FROM information_schema.columns
                WHERE table_schema = 'public' AND table_name = 'code_references'
                ORDER BY ordinal_position
                """
            )
        )
        rows = result.all()

    by_name = {r[0]: (r[1], r[2]) for r in rows}
    assert "snapshot_sha" in by_name
    assert "file_path" in by_name
    assert "symbol_name" in by_name
    assert "line_number" in by_name
    assert "kind" in by_name
    # All five columns are NOT NULL (they're in the PK).
    for col in ("snapshot_sha", "file_path", "symbol_name", "line_number", "kind"):
        assert by_name[col][1] == "NO", f"{col} should be NOT NULL (it's in the PK)"


@pytest.mark.asyncio
async def test_code_references_composite_pk(
    async_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with async_session_factory() as session:
        result = await session.execute(
            text(
                """
                SELECT kcu.column_name
                FROM information_schema.table_constraints tc
                JOIN information_schema.key_column_usage kcu
                  ON tc.constraint_name = kcu.constraint_name
                WHERE tc.table_name = 'code_references' AND tc.constraint_type = 'PRIMARY KEY'
                ORDER BY kcu.ordinal_position
                """
            )
        )
        pk_cols = [r[0] for r in result.all()]
    assert pk_cols == ["snapshot_sha", "file_path", "symbol_name", "line_number", "kind"]


@pytest.mark.asyncio
async def test_code_references_symbol_lookup_index_exists(
    async_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Plan 8's CodeReferencesRepository.count_by_symbol queries by
    (symbol_name, snapshot_sha). An index on this pair must exist."""
    async with async_session_factory() as session:
        result = await session.execute(
            text(
                """
                SELECT indexname FROM pg_indexes
                WHERE schemaname = 'public' AND tablename = 'code_references'
                """
            )
        )
        index_names = {r[0] for r in result.all()}
    # Expected indexes (names from the migration):
    assert "ix_code_references_symbol_lookup" in index_names
    assert "ix_code_references_snapshot" in index_names


@pytest.mark.asyncio
async def test_kind_check_constraint_rejects_invalid_kind(
    async_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """The `kind` column has a CHECK constraint pinning the four allowed values."""
    async with async_session_factory() as session:
        with pytest.raises(Exception):  # IntegrityError or DBAPIError depending on driver
            await session.execute(
                text(
                    """
                    INSERT INTO code_references
                        (snapshot_sha, file_path, symbol_name, line_number, kind)
                    VALUES
                        ('sha-test', 'a.py', 'foo', 1, 'bogus-kind')
                    """
                )
            )
            await session.commit()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/integration/db/test_migration_0005_smoke.py -v`

Expected: FAIL — `code_references` table does not exist (migration 0005 hasn't been written).

- [ ] **Step 3: Write the alembic migration**

Create `src/codeoracle/db/migrations/versions/0005_code_references.py`:

```python
"""code_references: per-snapshot textual references from OpenGrep.

Revision ID: 0005
Revises: 0004
Create Date: 2026-05-21

The table stores results from `workers/opengrep_runner` (Plan 6). Each row is
one textual reference of one kind at one line. `kind` is one of:

- "import"           — Python `from X import Y` / JavaScript `import Y from "X"`.
- "call"             — Function/method invocation at this line.
- "string-literal"   — String literal containing the symbol name.
- "comment-mention"  — Comment or docstring mentioning the symbol name.

Plan 9's verifier queries `count_by_symbol(symbol_name, snapshot_sha)` to
cross-validate Joern/SCIP claims about how often a symbol is referenced.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
# Bare-numeric convention — matches Plan 3 ("0001"/"0002"/"0003") + Plan 5
# ("0004") + Plan 7 ("0006"). The slug-suffix form ("0005_code_references")
# breaks the down_revision chain because Plan 7's down_revision = "0005"
# and Plan 5's revision = "0004"; alembic will fail to walk the chain.
# See Plan 5's "Cross-plan contracts" §2 for the convention statement.
revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


_ALLOWED_KINDS = ("import", "call", "string-literal", "comment-mention")


def upgrade() -> None:
    op.create_table(
        "code_references",
        sa.Column("snapshot_sha", sa.String(length=64), nullable=False),
        sa.Column("file_path", sa.String(length=1024), nullable=False),
        sa.Column("symbol_name", sa.String(length=512), nullable=False),
        sa.Column("line_number", sa.Integer, nullable=False),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint(
            "snapshot_sha", "file_path", "symbol_name", "line_number", "kind",
            name="pk_code_references",
        ),
        sa.CheckConstraint(
            "kind IN ('import', 'call', 'string-literal', 'comment-mention')",
            name="ck_code_references_kind",
        ),
        sa.CheckConstraint(
            "line_number >= 1",
            name="ck_code_references_line_positive",
        ),
    )
    op.create_index(
        "ix_code_references_symbol_lookup",
        "code_references",
        ["symbol_name", "snapshot_sha"],
    )
    op.create_index(
        "ix_code_references_snapshot",
        "code_references",
        ["snapshot_sha"],
    )


def downgrade() -> None:
    op.drop_index("ix_code_references_snapshot", table_name="code_references")
    op.drop_index("ix_code_references_symbol_lookup", table_name="code_references")
    op.drop_table("code_references")
```

- [ ] **Step 4: Run smoke test to verify it passes**

Run: `uv run pytest tests/integration/db/test_migration_0005_smoke.py -v`

Expected: PASS (4 tests).

- [ ] **Step 5: Write ORM model + round-trip test**

Create `tests/integration/db/test_code_reference_model_roundtrip.py`:

```python
"""ORM round-trip test: insert + read a CodeReference row."""

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy import select

from codeoracle.db.models.code_reference import CodeReference


@pytest.mark.asyncio
async def test_insert_and_read_back(
    async_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with async_session_factory() as session:
        row = CodeReference(
            snapshot_sha="sha-roundtrip",
            file_path="a.py",
            symbol_name="my_func",
            line_number=42,
            kind="call",
        )
        session.add(row)
        await session.commit()

        result = await session.execute(
            select(CodeReference).where(CodeReference.snapshot_sha == "sha-roundtrip")
        )
        loaded = result.scalar_one()
        assert loaded.symbol_name == "my_func"
        assert loaded.line_number == 42
        assert loaded.kind == "call"
        assert loaded.file_path == "a.py"
```

- [ ] **Step 6: Run model test to verify it fails**

Run: `uv run pytest tests/integration/db/test_code_reference_model_roundtrip.py -v`

Expected: FAIL — `codeoracle.db.models.code_reference` module does not exist.

- [ ] **Step 7: Create the ORM model**

Create `src/codeoracle/db/models/code_reference.py`:

```python
"""CodeReference: per-snapshot textual reference row.

Plan 6 — backed by migration 0005. Composite primary key spans all five
columns: (snapshot_sha, file_path, symbol_name, line_number, kind).

Idempotency: writers (`workers/opengrep_runner`) use `CodeReferencesRepository`
which performs DELETE-WHERE + INSERT inside one transaction per file. This
guarantees no duplicates across re-runs of the same file in the same snapshot.
"""

from __future__ import annotations

import datetime as dt
from typing import Literal

from sqlalchemy import CheckConstraint, DateTime, Integer, String
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from codeoracle.db.base import Base


CodeReferenceKind = Literal["import", "call", "string-literal", "comment-mention"]


class CodeReference(Base):
    __tablename__ = "code_references"
    __table_args__ = (
        CheckConstraint(
            "kind IN ('import', 'call', 'string-literal', 'comment-mention')",
            name="ck_code_references_kind",
        ),
        CheckConstraint("line_number >= 1", name="ck_code_references_line_positive"),
    )

    snapshot_sha: Mapped[str] = mapped_column(String(64), primary_key=True)
    file_path: Mapped[str] = mapped_column(String(1024), primary_key=True)
    symbol_name: Mapped[str] = mapped_column(String(512), primary_key=True)
    line_number: Mapped[int] = mapped_column(Integer, primary_key=True)
    kind: Mapped[str] = mapped_column(String(32), primary_key=True)
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.current_timestamp(),
    )
```

- [ ] **Step 8: Run model test to verify it passes**

Run: `uv run pytest tests/integration/db/test_code_reference_model_roundtrip.py -v`

Expected: PASS.

- [ ] **Step 9: Run all migration tests to verify chain works end to end**

Run: `uv run pytest tests/unit/db/ tests/integration/db/ -v`

Expected: all green, including Plan 3 + Plan 5 + Plan 6's migration smoke tests.

- [ ] **Step 10: Commit**

```bash
git add tests/integration/db/test_migration_0005_smoke.py tests/integration/db/test_code_reference_model_roundtrip.py \
        src/codeoracle/db/migrations/versions/0005_code_references.py \
        src/codeoracle/db/models/code_reference.py
git commit -m "feat(db): migration 0005 + CodeReference ORM (Plan 6 Task 4)"
```

---

### Task 5: CodeReferencesRepository

**Files:**
- Create: `src/codeoracle/db/repositories/code_references.py`
- Test: `tests/integration/db/repositories/test_code_references_repo.py` (new)

**API:** Two methods.

1. `replace_file_references(snapshot_sha, file_path, references: list[CodeReferenceRow]) -> None` — atomically replace ALL references for one (snapshot, file). The writer is `workers/opengrep_runner` (Task 12), one call per consumed message.

2. `count_by_symbol(symbol_name, snapshot_sha) -> int` — total reference count for a symbol in a snapshot, summed across all files and kinds. Plan 9's verifier uses this to validate "Joern says symbol X is used N times" claims against the textual evidence.

We also expose `count_by_symbol_and_kind(...)` because Plan 9 may want finer slicing (e.g., "how many calls vs how many string-literals?"). Cheap to ship now while we're already here.

- [ ] **Step 1: Write the failing tests**

Create `tests/integration/db/repositories/test_code_references_repo.py`:

```python
"""CodeReferencesRepository — unit tests against testcontainers PG."""

from __future__ import annotations

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from codeoracle.db.repositories.code_references import (
    CodeReferenceRow,
    CodeReferencesRepository,
)


@pytest_asyncio.fixture
async def session(
    async_session_factory: async_sessionmaker[AsyncSession],
) -> AsyncSession:
    async with async_session_factory() as s:
        yield s


@pytest.mark.asyncio
async def test_replace_inserts_rows(session: AsyncSession) -> None:
    repo = CodeReferencesRepository(session)
    rows = [
        CodeReferenceRow(snapshot_sha="s1", file_path="a.py", symbol_name="foo", line_number=10, kind="call"),
        CodeReferenceRow(snapshot_sha="s1", file_path="a.py", symbol_name="foo", line_number=20, kind="call"),
        CodeReferenceRow(snapshot_sha="s1", file_path="a.py", symbol_name="bar", line_number=5, kind="import"),
    ]
    await repo.replace_file_references(snapshot_sha="s1", file_path="a.py", references=rows)
    await session.commit()

    count = await repo.count_by_symbol(symbol_name="foo", snapshot_sha="s1")
    assert count == 2

    count_bar = await repo.count_by_symbol(symbol_name="bar", snapshot_sha="s1")
    assert count_bar == 1


@pytest.mark.asyncio
async def test_replace_deletes_existing_then_inserts(session: AsyncSession) -> None:
    """`replace_file_references` is atomic: all existing rows for this
    (snapshot, file) are deleted, then the new rows inserted. A subsequent
    call with fewer rows REDUCES the count, not adds to it."""
    repo = CodeReferencesRepository(session)

    first = [
        CodeReferenceRow(snapshot_sha="s2", file_path="x.py", symbol_name="foo", line_number=1, kind="call"),
        CodeReferenceRow(snapshot_sha="s2", file_path="x.py", symbol_name="foo", line_number=2, kind="call"),
        CodeReferenceRow(snapshot_sha="s2", file_path="x.py", symbol_name="foo", line_number=3, kind="call"),
    ]
    await repo.replace_file_references(snapshot_sha="s2", file_path="x.py", references=first)
    await session.commit()
    assert await repo.count_by_symbol(symbol_name="foo", snapshot_sha="s2") == 3

    second = [
        CodeReferenceRow(snapshot_sha="s2", file_path="x.py", symbol_name="foo", line_number=99, kind="import"),
    ]
    await repo.replace_file_references(snapshot_sha="s2", file_path="x.py", references=second)
    await session.commit()
    assert await repo.count_by_symbol(symbol_name="foo", snapshot_sha="s2") == 1


@pytest.mark.asyncio
async def test_replace_only_affects_target_file(session: AsyncSession) -> None:
    """`replace_file_references(s, file=A)` MUST NOT delete rows for file=B
    even if they share snapshot_sha and symbol_name."""
    repo = CodeReferencesRepository(session)

    await repo.replace_file_references(
        snapshot_sha="s3",
        file_path="a.py",
        references=[
            CodeReferenceRow(snapshot_sha="s3", file_path="a.py", symbol_name="foo", line_number=1, kind="call"),
        ],
    )
    await repo.replace_file_references(
        snapshot_sha="s3",
        file_path="b.py",
        references=[
            CodeReferenceRow(snapshot_sha="s3", file_path="b.py", symbol_name="foo", line_number=1, kind="call"),
        ],
    )
    await session.commit()

    # foo has one reference in each file: total 2.
    assert await repo.count_by_symbol(symbol_name="foo", snapshot_sha="s3") == 2

    # Now re-replace `a.py` with an empty list.
    await repo.replace_file_references(snapshot_sha="s3", file_path="a.py", references=[])
    await session.commit()

    # b.py's row is preserved.
    assert await repo.count_by_symbol(symbol_name="foo", snapshot_sha="s3") == 1


@pytest.mark.asyncio
async def test_count_by_symbol_isolated_by_snapshot(session: AsyncSession) -> None:
    repo = CodeReferencesRepository(session)
    await repo.replace_file_references(
        snapshot_sha="s4-a",
        file_path="a.py",
        references=[
            CodeReferenceRow(snapshot_sha="s4-a", file_path="a.py", symbol_name="foo", line_number=1, kind="call"),
        ],
    )
    await repo.replace_file_references(
        snapshot_sha="s4-b",
        file_path="a.py",
        references=[
            CodeReferenceRow(snapshot_sha="s4-b", file_path="a.py", symbol_name="foo", line_number=1, kind="call"),
            CodeReferenceRow(snapshot_sha="s4-b", file_path="a.py", symbol_name="foo", line_number=2, kind="call"),
        ],
    )
    await session.commit()

    assert await repo.count_by_symbol(symbol_name="foo", snapshot_sha="s4-a") == 1
    assert await repo.count_by_symbol(symbol_name="foo", snapshot_sha="s4-b") == 2


@pytest.mark.asyncio
async def test_count_by_symbol_and_kind(session: AsyncSession) -> None:
    repo = CodeReferencesRepository(session)
    await repo.replace_file_references(
        snapshot_sha="s5",
        file_path="a.py",
        references=[
            CodeReferenceRow(snapshot_sha="s5", file_path="a.py", symbol_name="foo", line_number=1, kind="import"),
            CodeReferenceRow(snapshot_sha="s5", file_path="a.py", symbol_name="foo", line_number=10, kind="call"),
            CodeReferenceRow(snapshot_sha="s5", file_path="a.py", symbol_name="foo", line_number=20, kind="call"),
            CodeReferenceRow(snapshot_sha="s5", file_path="a.py", symbol_name="foo", line_number=30, kind="string-literal"),
        ],
    )
    await session.commit()

    by_kind = await repo.count_by_symbol_and_kind(symbol_name="foo", snapshot_sha="s5")
    assert by_kind == {"import": 1, "call": 2, "string-literal": 1}


@pytest.mark.asyncio
async def test_replace_with_empty_list_is_legal(session: AsyncSession) -> None:
    """Pure delete (no new rows). Used when OpenGrep found nothing in a file
    on re-run after previously finding matches."""
    repo = CodeReferencesRepository(session)
    await repo.replace_file_references(
        snapshot_sha="s6",
        file_path="empty.py",
        references=[
            CodeReferenceRow(snapshot_sha="s6", file_path="empty.py", symbol_name="foo", line_number=1, kind="call"),
        ],
    )
    await session.commit()
    assert await repo.count_by_symbol(symbol_name="foo", snapshot_sha="s6") == 1

    await repo.replace_file_references(snapshot_sha="s6", file_path="empty.py", references=[])
    await session.commit()
    assert await repo.count_by_symbol(symbol_name="foo", snapshot_sha="s6") == 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/integration/db/repositories/test_code_references_repo.py -v`

Expected: FAIL — `codeoracle.db.repositories.code_references` module does not exist.

- [ ] **Step 3: Implement the repository**

Create `src/codeoracle/db/repositories/code_references.py`:

```python
"""CodeReferencesRepository: textual reference writes/reads.

The writer is `workers/opengrep_runner` (Plan 6 Task 12): one call to
`replace_file_references` per consumed per-file message, inside the worker's
session/transaction.

The reader is Plan 9's verifier service (`VerifierService.cross_check_symbol`),
which calls `count_by_symbol` and `count_by_symbol_and_kind`.

Idempotency: `replace_file_references` performs DELETE-WHERE + INSERT inside a
single transaction. A second call with the same (snapshot_sha, file_path) and
identical refs produces an identical row set (modulo `created_at`); the
underlying composite PK (snapshot_sha, file_path, symbol_name, line_number, kind)
prevents accidental duplicates.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from sqlalchemy import delete, func, insert, select
from sqlalchemy.ext.asyncio import AsyncSession

from codeoracle.db.models.code_reference import CodeReference, CodeReferenceKind


@dataclass(frozen=True, slots=True)
class CodeReferenceRow:
    """Plain data carrier passed across the worker → repo boundary.

    Frozen to make it suitable for use in `dict`/`set` semantics inside
    the worker (deduplication of identical references from overlapping rule
    pattern matches).
    """

    snapshot_sha: str
    file_path: str
    symbol_name: str
    line_number: int
    kind: CodeReferenceKind


class CodeReferencesRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def replace_file_references(
        self,
        *,
        snapshot_sha: str,
        file_path: str,
        references: list[CodeReferenceRow],
    ) -> None:
        """Atomically replace all references for (snapshot_sha, file_path).

        Empty `references` list is legal — it's a pure delete of any prior
        rows for that file. The session is NOT committed here; the caller
        (worker) commits when its overall handle() transaction completes.
        """
        await self._session.execute(
            delete(CodeReference).where(
                CodeReference.snapshot_sha == snapshot_sha,
                CodeReference.file_path == file_path,
            )
        )
        if references:
            await self._session.execute(
                insert(CodeReference),
                [
                    {
                        "snapshot_sha": r.snapshot_sha,
                        "file_path": r.file_path,
                        "symbol_name": r.symbol_name,
                        "line_number": r.line_number,
                        "kind": r.kind,
                    }
                    for r in references
                ],
            )

    async def count_by_symbol(
        self,
        *,
        symbol_name: str,
        snapshot_sha: str,
    ) -> int:
        """Total count of references to `symbol_name` in `snapshot_sha` across
        all files and kinds."""
        result = await self._session.execute(
            select(func.count()).select_from(CodeReference).where(
                CodeReference.symbol_name == symbol_name,
                CodeReference.snapshot_sha == snapshot_sha,
            )
        )
        return int(result.scalar_one())

    async def count_by_symbol_and_kind(
        self,
        *,
        symbol_name: str,
        snapshot_sha: str,
    ) -> dict[str, int]:
        """Per-kind count of references for one symbol in one snapshot.

        Returns a dict keyed by `kind`. Kinds with zero references are NOT
        included in the result; callers should `.get(kind, 0)` rather than
        indexing directly.
        """
        result = await self._session.execute(
            select(CodeReference.kind, func.count()).where(
                CodeReference.symbol_name == symbol_name,
                CodeReference.snapshot_sha == snapshot_sha,
            ).group_by(CodeReference.kind)
        )
        return {row[0]: int(row[1]) for row in result.all()}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/integration/db/repositories/test_code_references_repo.py -v`

Expected: PASS (6 tests).

- [ ] **Step 5: Commit**

```bash
git add tests/integration/db/repositories/test_code_references_repo.py \
        src/codeoracle/db/repositories/code_references.py
git commit -m "feat(db): CodeReferencesRepository (Plan 6 Task 5)"
```

---

### Task 6: EmbeddingsRepository

**Files:**
- Create: `src/codeoracle/db/repositories/embeddings.py`
- Test: `tests/integration/db/repositories/test_embeddings_repo.py` (new)

**Context:** Plan 3's `Embedding` ORM defines the column layout — `(snapshot_sha, source_path, chunk_start_line, chunk_end_line, content, embedding)`. Plan 6 ships the repository that the embed worker uses to write chunks and that Plan 8's `CodeSpanResolver` will use to query nearest-neighbors.

**API:** Two methods.

1. `replace_file_embeddings(snapshot_sha, source_path, chunks: list[EmbeddingChunk]) -> None` — atomic DELETE-WHERE + INSERT, scoped to one (snapshot, file). Same pattern as `CodeReferencesRepository.replace_file_references` for the same reason: idempotent re-runs of an embed worker on the same file produce a deterministic row set.

2. `knn(query_embedding, snapshot_sha, k) -> list[EmbeddingHit]` — Plan 3's HNSW index (`ix_embeddings_embedding_hnsw`, cosine) is leveraged via `embedding <=> :query ORDER BY embedding <=> :query LIMIT :k`. Returns hits sorted by ascending distance (smallest distance = best match).

`EmbeddingHit` carries the same columns as a row plus the `distance` float so Plan 8 can surface the similarity score to the verifier.

- [ ] **Step 1: Write the failing tests**

Create `tests/integration/db/repositories/test_embeddings_repo.py`:

```python
"""EmbeddingsRepository — unit tests against testcontainers PG with pgvector + HNSW index."""

from __future__ import annotations

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from codeoracle.core.config import EmbeddingConfig
from codeoracle.db.repositories.embeddings import (
    EmbeddingChunk,
    EmbeddingHit,
    EmbeddingsRepository,
)


@pytest_asyncio.fixture
async def session(
    async_session_factory: async_sessionmaker[AsyncSession],
) -> AsyncSession:
    async with async_session_factory() as s:
        yield s


def _dummy_vec(dim: int, base: float = 0.0) -> list[float]:
    """Stable test vector — small bias toward index `(i + base) % dim`.

    Useful for KNN tests: we can construct two vectors that are obviously
    closer/farther in cosine space.
    """
    v = [0.0] * dim
    v[int(base) % dim] = 1.0
    return v


@pytest.mark.asyncio
async def test_replace_inserts_chunks(session: AsyncSession) -> None:
    repo = EmbeddingsRepository(session)
    config = EmbeddingConfig(model="text-embedding-3-small", dim=1536, kind="mock")

    chunks = [
        EmbeddingChunk(
            snapshot_sha="s1",
            source_path="a.py",
            chunk_start_line=1,
            chunk_end_line=100,
            content="def foo(): pass",
            embedding=_dummy_vec(config.dim, 0),
        ),
        EmbeddingChunk(
            snapshot_sha="s1",
            source_path="a.py",
            chunk_start_line=81,
            chunk_end_line=180,
            content="def bar(): pass",
            embedding=_dummy_vec(config.dim, 1),
        ),
    ]
    await repo.replace_file_embeddings(snapshot_sha="s1", source_path="a.py", chunks=chunks)
    await session.commit()

    hits = await repo.knn(
        query_embedding=_dummy_vec(config.dim, 0),
        snapshot_sha="s1",
        k=10,
    )
    assert len(hits) == 2
    # The first hit should be the chunk whose vector is exactly equal to the query.
    assert hits[0].chunk_start_line == 1
    assert hits[0].chunk_end_line == 100


@pytest.mark.asyncio
async def test_replace_is_atomic(session: AsyncSession) -> None:
    """A subsequent replace_file_embeddings call MUST clear prior chunks for that file."""
    repo = EmbeddingsRepository(session)
    config = EmbeddingConfig(model="text-embedding-3-small", dim=1536, kind="mock")

    initial = [
        EmbeddingChunk(
            snapshot_sha="s2", source_path="a.py", chunk_start_line=1, chunk_end_line=100,
            content="v1", embedding=_dummy_vec(config.dim, 0),
        ),
        EmbeddingChunk(
            snapshot_sha="s2", source_path="a.py", chunk_start_line=101, chunk_end_line=200,
            content="v2", embedding=_dummy_vec(config.dim, 1),
        ),
    ]
    await repo.replace_file_embeddings(snapshot_sha="s2", source_path="a.py", chunks=initial)
    await session.commit()

    # Re-run with a different chunk set; the prior two MUST be gone.
    second = [
        EmbeddingChunk(
            snapshot_sha="s2", source_path="a.py", chunk_start_line=50, chunk_end_line=150,
            content="vNEW", embedding=_dummy_vec(config.dim, 2),
        ),
    ]
    await repo.replace_file_embeddings(snapshot_sha="s2", source_path="a.py", chunks=second)
    await session.commit()

    hits = await repo.knn(
        query_embedding=_dummy_vec(config.dim, 2),
        snapshot_sha="s2",
        k=10,
    )
    assert len(hits) == 1
    assert hits[0].content == "vNEW"


@pytest.mark.asyncio
async def test_replace_only_affects_target_file(session: AsyncSession) -> None:
    """Replacing chunks for one file MUST NOT touch another file in the same snapshot."""
    repo = EmbeddingsRepository(session)
    config = EmbeddingConfig(model="text-embedding-3-small", dim=1536, kind="mock")

    await repo.replace_file_embeddings(
        snapshot_sha="s3", source_path="a.py",
        chunks=[
            EmbeddingChunk(
                snapshot_sha="s3", source_path="a.py", chunk_start_line=1, chunk_end_line=100,
                content="A", embedding=_dummy_vec(config.dim, 0),
            ),
        ],
    )
    await repo.replace_file_embeddings(
        snapshot_sha="s3", source_path="b.py",
        chunks=[
            EmbeddingChunk(
                snapshot_sha="s3", source_path="b.py", chunk_start_line=1, chunk_end_line=100,
                content="B", embedding=_dummy_vec(config.dim, 5),
            ),
        ],
    )
    await session.commit()

    # Clear `a.py` only.
    await repo.replace_file_embeddings(snapshot_sha="s3", source_path="a.py", chunks=[])
    await session.commit()

    hits = await repo.knn(
        query_embedding=_dummy_vec(config.dim, 5),
        snapshot_sha="s3",
        k=10,
    )
    assert len(hits) == 1
    assert hits[0].source_path == "b.py"


@pytest.mark.asyncio
async def test_knn_isolated_by_snapshot(session: AsyncSession) -> None:
    repo = EmbeddingsRepository(session)
    config = EmbeddingConfig(model="text-embedding-3-small", dim=1536, kind="mock")

    await repo.replace_file_embeddings(
        snapshot_sha="s4-A", source_path="a.py",
        chunks=[
            EmbeddingChunk(
                snapshot_sha="s4-A", source_path="a.py", chunk_start_line=1, chunk_end_line=100,
                content="content-A", embedding=_dummy_vec(config.dim, 0),
            ),
        ],
    )
    await repo.replace_file_embeddings(
        snapshot_sha="s4-B", source_path="a.py",
        chunks=[
            EmbeddingChunk(
                snapshot_sha="s4-B", source_path="a.py", chunk_start_line=1, chunk_end_line=100,
                content="content-B", embedding=_dummy_vec(config.dim, 0),
            ),
        ],
    )
    await session.commit()

    hits_a = await repo.knn(
        query_embedding=_dummy_vec(config.dim, 0),
        snapshot_sha="s4-A", k=10,
    )
    hits_b = await repo.knn(
        query_embedding=_dummy_vec(config.dim, 0),
        snapshot_sha="s4-B", k=10,
    )
    assert all(h.snapshot_sha == "s4-A" for h in hits_a)
    assert all(h.snapshot_sha == "s4-B" for h in hits_b)
    assert hits_a[0].content == "content-A"
    assert hits_b[0].content == "content-B"


@pytest.mark.asyncio
async def test_knn_respects_k(session: AsyncSession) -> None:
    repo = EmbeddingsRepository(session)
    config = EmbeddingConfig(model="text-embedding-3-small", dim=1536, kind="mock")
    chunks = []
    for i in range(10):
        chunks.append(
            EmbeddingChunk(
                snapshot_sha="s5", source_path="a.py",
                chunk_start_line=i * 100 + 1, chunk_end_line=(i + 1) * 100,
                content=f"chunk-{i}", embedding=_dummy_vec(config.dim, i),
            )
        )
    await repo.replace_file_embeddings(snapshot_sha="s5", source_path="a.py", chunks=chunks)
    await session.commit()

    hits = await repo.knn(query_embedding=_dummy_vec(config.dim, 0), snapshot_sha="s5", k=3)
    assert len(hits) == 3
    # All hits have ascending distance.
    for i in range(len(hits) - 1):
        assert hits[i].distance <= hits[i + 1].distance


@pytest.mark.asyncio
async def test_replace_empty_chunk_list_is_legal(session: AsyncSession) -> None:
    repo = EmbeddingsRepository(session)
    await repo.replace_file_embeddings(snapshot_sha="s6", source_path="empty.py", chunks=[])
    await session.commit()
    hits = await repo.knn(
        query_embedding=[0.1] * EmbeddingConfig(model="text-embedding-3-small", dim=1536, kind="mock").dim,
        snapshot_sha="s6", k=10,
    )
    assert hits == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/integration/db/repositories/test_embeddings_repo.py -v`

Expected: FAIL — `codeoracle.db.repositories.embeddings` does not exist.

- [ ] **Step 3: Implement the repository**

Create `src/codeoracle/db/repositories/embeddings.py`:

```python
"""EmbeddingsRepository: per-chunk vector writes + KNN reads.

Backed by Plan 3's `embeddings` table and HNSW index `ix_embeddings_embedding_hnsw`
(cosine distance operator `<=>`).

The writer is `workers/embed_indexer` (Plan 6 Task 13): one call to
`replace_file_embeddings` per consumed per-file message, with the file's full
chunk list.

The reader is Plan 8's `CodeSpanResolver` and the agent's similarity-search
proof generator: `knn(query_embedding, snapshot_sha, k)` returns the k nearest
chunks for one snapshot.

Atomicity: `replace_file_embeddings` performs DELETE-WHERE + INSERT inside a
single transaction (no commit; caller commits). A second call with the same
(snapshot_sha, source_path) replaces the prior chunk set entirely.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy import bindparam, delete, insert, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from codeoracle.db.models.embedding import Embedding


@dataclass(frozen=True, slots=True)
class EmbeddingChunk:
    """Plain data carrier passed across the worker → repo boundary."""

    snapshot_sha: str
    source_path: str
    chunk_start_line: int
    chunk_end_line: int
    content: str
    embedding: list[float]


@dataclass(frozen=True, slots=True)
class EmbeddingHit:
    """KNN result row.

    `distance` is cosine distance — lower is more similar (0 = identical,
    2 = opposite). The repo returns hits ordered ascending by `distance`.
    """

    snapshot_sha: str
    source_path: str
    chunk_start_line: int
    chunk_end_line: int
    content: str
    distance: float


class EmbeddingsRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def replace_file_embeddings(
        self,
        *,
        snapshot_sha: str,
        source_path: str,
        chunks: list[EmbeddingChunk],
    ) -> None:
        """Atomically replace all embeddings for (snapshot_sha, source_path).

        Empty `chunks` list is legal — pure delete of any prior chunks.
        """
        await self._session.execute(
            delete(Embedding).where(
                Embedding.snapshot_sha == snapshot_sha,
                Embedding.source_path == source_path,
            )
        )
        if chunks:
            await self._session.execute(
                insert(Embedding),
                [
                    {
                        "snapshot_sha": c.snapshot_sha,
                        "source_path": c.source_path,
                        "chunk_start_line": c.chunk_start_line,
                        "chunk_end_line": c.chunk_end_line,
                        "content": c.content,
                        "embedding": c.embedding,
                    }
                    for c in chunks
                ],
            )

    async def knn(
        self,
        *,
        query_embedding: list[float],
        snapshot_sha: str,
        k: int,
    ) -> list[EmbeddingHit]:
        """Top-k chunks by cosine distance.

        Uses pgvector's `<=>` (cosine) operator and the HNSW index defined
        in migration 0002. The index is unnamed in the query path (PG picks
        it by data type + operator class). The per-query `SET LOCAL` GUCs
        below (`hnsw.ef_search` + `hnsw.iterative_scan`) stop the post-scan
        `snapshot_sha` filter from silently under-returning < k rows on a
        multi-snapshot table (NFR D3-2).

        Results are ordered by distance ascending — best match first.
        """
        # Use a parameterized raw SQL because pgvector's distance operator
        # isn't first-class in SQLAlchemy's expression language. The query
        # is parameterized (`:query`, `:snapshot_sha`, `:k`); no injection.
        stmt = text(
            """
            SELECT snapshot_sha, source_path, chunk_start_line, chunk_end_line, content,
                   (embedding <=> :query) AS distance
            FROM embeddings
            WHERE snapshot_sha = :snapshot_sha
            ORDER BY embedding <=> :query
            LIMIT :k
            """
        )
        # Filtered-HNSW correctness (NFR D3-2): the snapshot_sha predicate is
        # applied AFTER the ANN scan, so the default ef_search=40 returns the
        # 40 globally-nearest rows and the filter can drop them all → silently
        # < k (down to 0) results as snapshots accumulate. Widen the candidate
        # pool (ef_search >= k) and enable iterative scanning so PG keeps
        # fetching until k rows satisfy the filter. SET LOCAL is txn-scoped —
        # the AsyncSession holds one transaction across these executes — so the
        # GUCs apply to the SELECT below and never leak onto the pooled
        # connection. Requires pgvector >= 0.8.0 (pinned for PG18 in Plan 2).
        await self._session.execute(
            text(f"SET LOCAL hnsw.ef_search = {max(k, 100)}")
        )
        await self._session.execute(
            text("SET LOCAL hnsw.iterative_scan = strict_order")
        )
        result = await self._session.execute(
            stmt,
            {
                "query": query_embedding,
                "snapshot_sha": snapshot_sha,
                "k": k,
            },
        )
        return [
            EmbeddingHit(
                snapshot_sha=row[0],
                source_path=row[1],
                chunk_start_line=row[2],
                chunk_end_line=row[3],
                content=row[4],
                distance=float(row[5]),
            )
            for row in result.all()
        ]
```

**Note on the raw SQL + pgvector binding:** the pgvector Python adapter
(`pgvector-sqlalchemy`) registers the `Vector` SQL type that maps Python
`list[float]` to PG `vector(N)` for binds AND results. The `:query` parameter
binds a Python `list[float]` and is sent as a pgvector literal automatically.
If `pgvector-sqlalchemy` is NOT installed (Plan 3 sets it up; verify via
`uv pip show pgvector` before running the tests), the `<=>` operator will
fail with `ProgrammingError`.

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/integration/db/repositories/test_embeddings_repo.py -v`

Expected: PASS (6 tests).

- [ ] **Step 5: Commit**

```bash
git add tests/integration/db/repositories/test_embeddings_repo.py \
        src/codeoracle/db/repositories/embeddings.py
git commit -m "feat(db): EmbeddingsRepository with knn + replace_file_embeddings (Plan 6 Task 6)"
```

---

### Task 7: EmbeddingsProvider ABC + OpenAIEmbeddingsProvider

**Files:**
- Create: `src/codeoracle/adapters/llm/embeddings.py` (ABC + concrete + factory binding)
- Create: `src/codeoracle/adapters/llm/providers/openai_embed.py` (concrete provider; gets ruff per-file-ignore for direct `openai` import)
- Create: `src/codeoracle/adapters/llm/providers/mock_embed.py` (test double, in non-test path so callers can inject it without conftest gymnastics)
- Modify: `src/codeoracle/core/config.py` — add three fields (`kind`, `base_url`, `api_key`) to `EmbeddingConfig` (config-domain). **The `EmbeddingsProviderFactory` itself does NOT live in `core/`** — it goes in `adapters/llm/embeddings.py` alongside the ABC. Putting it in `core/` would require `core/` to import `adapters/llm/providers/*`, an upward `core → adapters` layer edge that spec §3.3 forbids (D-6.10).
- Modify: `src/codeoracle/adapters/llm/embeddings.py` — add `EmbeddingsProviderFactory.build(settings) -> EmbeddingsProvider` next to the ABC (one file, one domain).
- Modify: `pyproject.toml` — add `adapters/llm/providers/openai_embed.py` to ruff `per-file-ignores` (matches Plan 4's pattern for chat-completion providers)
- Test: `tests/unit/adapters/llm/test_embeddings_provider.py` (new)
- Test: `tests/integration/adapters/llm/test_openai_embeddings_provider_live.py` (new; gated by `--run-live-embeddings`)

**Design rationale (re-stated for the implementer):** `EmbeddingsProvider` is a SIBLING ABC of `LLMProvider`, not a method on it. Why:

1. Different OpenAI endpoint: `/v1/chat/completions` (chat) vs `/v1/embeddings` (embed).
2. Different rate limit buckets in OpenAI's billing.
3. Different model selection: e.g., `gpt-4o` for chat, `text-embedding-3-small` for embed.
4. Under vLLM, the chat-model and the embedding-model may be served by separate vLLM processes (or even separate machines).
5. Lifecycles can diverge: an CodeOracle deployment might run chat against the cloud OpenAI API while running embeddings against an on-prem vLLM, or vice versa. Making them siblings allows independent provider selection per service.

Defining them as siblings keeps each ABC focused and lets the worker DI receive the precisely-typed dependency it needs.

- [ ] **Step 1: Write the failing tests (ABC contract + mock)**

Create `tests/unit/adapters/llm/test_embeddings_provider.py`:

```python
"""EmbeddingsProvider ABC contract test + MockEmbeddingsProvider behavior test.

Live OpenAI/vLLM tests live in `test_openai_embeddings_provider_live.py` and
are gated by `--run-live-embeddings` (mirrors Plan 4's `--run-live-llm`).
"""

import pytest

from codeoracle.adapters.llm.embeddings import EmbeddingsProvider
from codeoracle.adapters.llm.providers.mock_embed import MockEmbeddingsProvider


def test_abc_cannot_be_instantiated() -> None:
    with pytest.raises(TypeError):
        EmbeddingsProvider()  # type: ignore[abstract]


@pytest.mark.asyncio
async def test_mock_returns_per_input_vector() -> None:
    provider = MockEmbeddingsProvider(dim=1536)
    out = await provider.embed(["alpha", "beta", "gamma"])
    assert len(out) == 3
    for vec in out:
        assert len(vec) == 1536
        assert all(isinstance(x, float) for x in vec)


@pytest.mark.asyncio
async def test_mock_deterministic_for_same_input() -> None:
    """The MockEmbeddingsProvider hashes inputs to vectors deterministically.
    This lets tests assert specific neighbor relationships without flakiness."""
    provider = MockEmbeddingsProvider(dim=1536)
    out1 = await provider.embed(["alpha"])
    out2 = await provider.embed(["alpha"])
    assert out1 == out2

    out_alpha = await provider.embed(["alpha"])
    out_beta = await provider.embed(["beta"])
    assert out_alpha != out_beta


@pytest.mark.asyncio
async def test_mock_empty_input_returns_empty_list() -> None:
    provider = MockEmbeddingsProvider(dim=1536)
    out = await provider.embed([])
    assert out == []


@pytest.mark.asyncio
async def test_mock_dim_mismatch_raises_on_construction() -> None:
    """Sanity: zero or negative dim is invalid."""
    with pytest.raises(ValueError):
        MockEmbeddingsProvider(dim=0)
    with pytest.raises(ValueError):
        MockEmbeddingsProvider(dim=-100)


def test_mock_vectors_are_unit_normalized() -> None:
    """For cosine-distance KNN tests, unit-normalized vectors are easier to
    reason about. The mock returns vectors with magnitude == 1.0 (within
    floating-point tolerance) so the cosine distance between identical inputs
    is exactly 0."""
    import math
    provider = MockEmbeddingsProvider(dim=1536)
    import asyncio
    vecs = asyncio.run(provider.embed(["alpha", "beta"]))
    for vec in vecs:
        magnitude = math.sqrt(sum(x * x for x in vec))
        assert abs(magnitude - 1.0) < 1e-6


def test_openai_embeddings_provider_rejects_empty_api_key() -> None:
    """Pin the design decision: the api_key precondition lives on
    OpenAIEmbeddingsProvider.__init__, NOT on EmbeddingConfig's model validator.

    Without this test, a future engineer could "consolidate" the check into
    EmbeddingConfig and break Plan 3's `tests/unit/core/test_config.py` (which
    directly constructs EmbeddingConfig(model=..., dim=...) at lines 447, 451)
    plus the `_ec()` helper in `tests/unit/core/test_embedding_config_chunk_constants.py`
    — both of which intentionally construct EmbeddingConfig without an api_key.
    See the rationale block in OpenAIEmbeddingsProvider.__init__."""
    from codeoracle.adapters.llm.providers.openai_embed import OpenAIEmbeddingsProvider

    with pytest.raises(ValueError, match="api_key is required"):
        OpenAIEmbeddingsProvider(
            base_url="https://api.openai.com",
            api_key="",
            model="text-embedding-3-small",
            dim=1536,
        )


def test_embedding_config_constructs_without_api_key() -> None:
    """Pin the dual of `test_openai_embeddings_provider_rejects_empty_api_key`:
    constructing EmbeddingConfig with no api_key succeeds. Task 7 added
    `kind` / `base_url` / `api_key` as fields with defaults but MUST NOT
    add a cross-plan break to Plan 3's `tests/unit/core/test_config.py`
    where lines 447, 451 directly construct `EmbeddingConfig(model=..., dim=...)`.

    The pair of tests (this + the provider-rejection one above) enforces
    the architecture: precondition lives on the consumer class, not the
    config carrier."""
    from codeoracle.core.config import EmbeddingConfig

    cfg = EmbeddingConfig(model="text-embedding-3-small", dim=1536)
    assert cfg.kind == "openai"  # default
    assert cfg.api_key == ""  # default — note: NOT validated at this layer
    assert cfg.base_url == "https://api.openai.com"  # default
    assert cfg.embed_chunk_lines == 100  # default (added Task 3)
    assert cfg.embed_chunk_overlap == 20  # default (added Task 3)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/adapters/llm/test_embeddings_provider.py -v`

Expected: FAIL — collection error initially (the top-level imports of `codeoracle.adapters.llm.embeddings.EmbeddingsProvider` + `codeoracle.adapters.llm.providers.mock_embed.MockEmbeddingsProvider` can't resolve until Steps 3-4 land). After Steps 3-4 the file becomes collectable; the two design-decision regression tests then remain RED until Step 5 (which adds the `if not api_key:` raise inside `OpenAIEmbeddingsProvider.__init__` — function-body import + ValueError) and Step 7 (which adds the `kind` / `base_url` / `api_key` fields to `EmbeddingConfig` — without them `cfg.kind` raises `AttributeError`).

- [ ] **Step 3: Create the ABC**

Create `src/codeoracle/adapters/llm/embeddings.py`:

```python
"""EmbeddingsProvider ABC + factory binding.

Plan 6. Sibling of `LLMProvider` (Plan 4). Each CodeOracle deployment selects a
concrete provider via `EmbeddingsProviderFactory.build(settings)`:
- "openai" — `OpenAIEmbeddingsProvider` (default)
- "vllm"   — `OpenAIEmbeddingsProvider` (vLLM serves OpenAI-compatible
             embeddings on `/v1/embeddings`; the same concrete works, the
             choice is purely a base_url + api_key change)
- "mock"   — `MockEmbeddingsProvider` (deterministic, used in non-live tests)

The factory is in `core/config.py` rather than this file to keep `adapters/`
provider-import-free outside `providers/`.
"""

from __future__ import annotations

from abc import ABC, abstractmethod


class EmbeddingsProvider(ABC):
    """Embedding vector provider — converts a batch of text strings into
    a batch of vectors of the configured embedding dimension.

    Contract:
    - `embed([])` returns `[]`.
    - `embed(texts)` returns a list of the same length as `texts`.
    - Each returned vector has length == the provider's configured dim
      (matched against `EmbeddingConfig.dim` by the calling
      worker BEFORE writing to pgvector).
    - Implementations are async + safe to call concurrently from multiple
      tasks. Concrete providers may internally batch / pool connections.
    """

    @abstractmethod
    async def embed(self, texts: list[str]) -> list[list[float]]:
        """Embed each input text. Returns vectors in the same order."""
        raise NotImplementedError

    @property
    @abstractmethod
    def dim(self) -> int:
        """Vector dimension this provider produces. Used by workers to
        validate against `EmbeddingConfig.dim` at construction
        time — catches model/config mismatches before any embeddings are
        written to the DB."""
        raise NotImplementedError
```

- [ ] **Step 4: Create MockEmbeddingsProvider**

Create `src/codeoracle/adapters/llm/providers/mock_embed.py`:

```python
"""MockEmbeddingsProvider — deterministic test double.

NOT in `tests/` because non-test callers (e.g., E2E integration tests in
`tests/e2e/` that orchestrate real workers but want stable embeddings)
import it directly.

Deterministic strategy: hash each input string into a seeded RNG, produce
a unit vector. Same input → same vector. Different input → very likely
different vector (collision probability ≈ 2^-64, fine for tests).
"""

from __future__ import annotations

import hashlib
import math
import random

from codeoracle.adapters.llm.embeddings import EmbeddingsProvider


class MockEmbeddingsProvider(EmbeddingsProvider):
    def __init__(self, dim: int) -> None:
        if dim <= 0:
            raise ValueError(f"dim must be positive, got {dim}")
        self._dim = dim

    @property
    def dim(self) -> int:
        return self._dim

    async def embed(self, texts: list[str]) -> list[list[float]]:
        return [self._stable_unit_vector(t) for t in texts]

    def _stable_unit_vector(self, text: str) -> list[float]:
        digest = hashlib.sha256(text.encode("utf-8")).digest()
        # First 8 bytes as a u64 seed for the RNG.
        seed = int.from_bytes(digest[:8], byteorder="big", signed=False)
        rng = random.Random(seed)
        raw = [rng.gauss(0.0, 1.0) for _ in range(self._dim)]
        magnitude = math.sqrt(sum(x * x for x in raw))
        if magnitude == 0.0:  # astronomically unlikely; defensive
            raw[0] = 1.0
            return raw
        return [x / magnitude for x in raw]
```

- [ ] **Step 5: Create OpenAIEmbeddingsProvider**

Create `src/codeoracle/adapters/llm/providers/openai_embed.py`:

```python
"""OpenAIEmbeddingsProvider — concrete EmbeddingsProvider against an
OpenAI-compatible `/v1/embeddings` endpoint.

Works for:
- OpenAI's hosted API (https://api.openai.com)
- vLLM serving OpenAI-compatible embeddings (any base URL)
- Any third-party with an OpenAI-shape embeddings endpoint

Direct `openai` import is permitted in this file ONLY (see
`pyproject.toml` `[tool.ruff.lint.flake8-tidy-imports.banned-api]` /
`per-file-ignores`).

Error mapping:
- HTTP 429, 5xx, network timeouts -> `TransientWorkerError`
- HTTP 400 invalid input, 401 auth, 404 model-not-found -> `EmbedExtractionError`
- Response shape mismatch (wrong dim, wrong batch count) -> `EmbedExtractionError`
"""

from __future__ import annotations

import asyncio
from typing import Any

import openai  # noqa: TID251 — provider implementation allowed direct SDK import

from codeoracle.adapters.llm.embeddings import EmbeddingsProvider
from codeoracle.core.exceptions import EmbedExtractionError, TransientWorkerError


class OpenAIEmbeddingsProvider(EmbeddingsProvider):
    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        model: str,
        dim: int,
        timeout_seconds: float = 30.0,
    ) -> None:
        if dim <= 0:
            raise ValueError(f"dim must be positive, got {dim}")
        # api_key precondition lives HERE, not on EmbeddingConfig. Rationale:
        # the model validator on EmbeddingConfig fires on every instance,
        # including unit-test instances that never construct a real provider.
        # Pushing the check to the class that actually consumes the key keeps
        # config construction cheap for tests and concentrates the precondition
        # where the consequence (calling OpenAI with empty Authorization) lives.
        # Workers construct the provider at startup via EmbeddingsProviderFactory.build,
        # so fail-fast is unchanged in practice — the process dies at boot, not
        # at first message — see worker code in Tasks 4 / 5 / 6.
        if not api_key:
            raise ValueError(
                "api_key is required for OpenAIEmbeddingsProvider — set "
                "CODEORACLE_EMBEDDING__API_KEY (or use CODEORACLE_EMBEDDING__KIND=mock for tests)"
            )
        self._client = openai.AsyncOpenAI(
            base_url=base_url,
            api_key=api_key,
            timeout=timeout_seconds,
        )
        self._model = model
        self._dim = dim

    @property
    def dim(self) -> int:
        return self._dim

    async def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        try:
            response = await self._client.embeddings.create(
                model=self._model,
                input=texts,
            )
        except openai.RateLimitError as e:
            raise TransientWorkerError(f"openai rate limit: {e}") from e
        except (openai.APITimeoutError, openai.APIConnectionError, asyncio.TimeoutError) as e:
            raise TransientWorkerError(f"openai transient: {e}") from e
        except openai.APIStatusError as e:
            # 5xx is transient; 4xx is terminal.
            if e.status_code >= 500:
                raise TransientWorkerError(f"openai 5xx: {e}") from e
            raise EmbedExtractionError(f"openai 4xx: {e.status_code} {e.message}") from e

        if len(response.data) != len(texts):
            raise EmbedExtractionError(
                f"openai returned {len(response.data)} vectors for {len(texts)} inputs"
            )

        vectors: list[list[float]] = []
        for i, item in enumerate(response.data):
            vec = list(item.embedding)
            if len(vec) != self._dim:
                raise EmbedExtractionError(
                    f"openai vector {i} has dim {len(vec)}, expected {self._dim}"
                )
            vectors.append(vec)
        return vectors

    async def aclose(self) -> None:
        """Release underlying httpx client. Called on worker stop_event."""
        await self._client.close()
```

- [ ] **Step 6: Verify ruff per-file-ignore already covers openai_embed.py (no-op)**

Plan 4's `pyproject.toml` uses a GLOB:

```toml
[tool.ruff.lint.per-file-ignores]
"src/codeoracle/adapters/llm/providers/*.py" = ["TID251"]
```

`src/codeoracle/adapters/llm/providers/openai_embed.py` matches `providers/*.py`, so the new file inherits the TID251 exemption automatically. No edit needed.

Verify by running:

```bash
uv run ruff check src/codeoracle/adapters/llm/providers/openai_embed.py
```

Expected: pass (no TID251 violations even though the file `import openai`s directly).

**Do NOT list `providers/litellm.py` in the per-file-ignores** — no plan creates that file. Plan 4 bans `import litellm` (via `banned-api` at the project level, Plan 4 line 154) precisely *because* no provider needs it; the matching per-file-ignore would be dead config (D-6.15). If a future plan adds a litellm provider, the GLOB above covers it.

- [ ] **Step 7a: Extend `EmbeddingConfig` in `core/config.py` with provider-selection fields**

Add `kind`, `base_url`, `api_key` to `EmbeddingConfig` (which lives in `core/config.py` per Plan 3 — extending it stays inside the `core/` layer because we're only adding declarative Pydantic fields, no adapter imports). **Task 7 does NOT touch the `_validate_embedding_config` model-validator from Task 3** — the chunk-geometry rule stays as Task 3 wrote it. The api_key precondition lives in `OpenAIEmbeddingsProvider.__init__` (Step 5 of this task), not in the validator. Rationale: a model validator runs on every `EmbeddingConfig` instance, including ones constructed purely for unit tests that never build a real provider — pushing the precondition to the class that actually consumes the key keeps test-only config construction cheap and avoids cross-plan blast radius into Plan 3's `tests/unit/core/test_config.py` (which directly constructs `EmbeddingConfig(model=..., dim=...)`).

**Step 7a MUST land before Step 7b** — Step 7b's factory reads `settings.embedding.kind` / `.base_url` / `.api_key`, which don't exist until this step extends the model. Engineers writing 7b first will hit `AttributeError` at import time.

```python
class EmbeddingConfig(BaseModel):  # BaseModel — env-binding is on parent Settings
    # Plan 3 (required, no defaults — DO NOT add defaults; the fail-fast posture
    # protects against wrong-dim embeddings if env injection is missed).
    model: str = Field(min_length=1)
    dim: int = Field(ge=64, le=4096)

    # Plan 6 Task 3 (already present after Task 3).
    embed_chunk_lines: int = Field(default=100, ge=1)
    embed_chunk_overlap: int = Field(default=20, ge=0)

    # Plan 6 Task 7: NEW fields (provider selection). No per-field `env=`
    # aliases — EmbeddingConfig is a BaseModel, not BaseSettings, so per-field
    # `env=` would be ignored. Env binding cascades from the parent
    # `Settings(BaseSettings)` via env_prefix="CODEORACLE_" + env_nested_delimiter="__",
    # giving env vars `CODEORACLE_EMBEDDING__KIND`, `CODEORACLE_EMBEDDING__BASE_URL`,
    # `CODEORACLE_EMBEDDING__API_KEY` automatically.
    kind: Literal["openai", "vllm", "mock"] = Field(default="openai")
    base_url: str = Field(default="https://api.openai.com")
    api_key: str = Field(default="")  # api_key="" is acceptable on EmbeddingConfig;
                                       # OpenAIEmbeddingsProvider.__init__ raises if
                                       # asked to build a real provider with "". See
                                       # Step 5 of this task and the regression test
                                       # `test_openai_embeddings_provider_rejects_empty_api_key`.

    # _validate_embedding_config (chunk-geometry only) was added in Task 3 and is
    # NOT modified here. Task 7 is a pure-additive change to EmbeddingConfig: the
    # three fields above, no validator edits, no per-class env config. See the
    # Task 3 step for the validator's source.

    # NO `class Config:` / `model_config = ConfigDict(...)` here. Env binding
    # lives on the parent Settings(BaseSettings) — see Plan 3 `core/config.py`.
```

(Env-var contract for the new fields: `CODEORACLE_EMBEDDING__KIND`, `CODEORACLE_EMBEDDING__BASE_URL`, `CODEORACLE_EMBEDDING__API_KEY`. Plan 3's `CODEORACLE_EMBEDDING__MODEL` and `CODEORACLE_EMBEDDING__DIM` continue unchanged. The implementer adds these to whatever `.env.example` Plan 1 ships alongside the existing env-var documentation — if Plan 1 didn't ship one, that's out of scope for Plan 6.)

- [ ] **Step 7b: Add EmbeddingsProviderFactory to `adapters/llm/embeddings.py`**

Modify `src/codeoracle/adapters/llm/embeddings.py` (created earlier in this task with the ABC) to also expose the factory. The factory lives **next to the ABC**, NOT in `core/config.py` — `adapters/llm/embeddings.py` already imports the concrete providers under `adapters/llm/providers/`, so the layer edge stays adapter→adapter (legal). Putting the factory in `core/` would require `core/` to import `adapters/llm/providers/*`, an upward `core → adapters` edge that spec §3.3 forbids (D-6.10 — see also Plan 4's `LLMProviderFactory` which lives at `adapters/llm/factory.py` for the same reason).

```python
# Append to src/codeoracle/adapters/llm/embeddings.py (after the ABC defn).

from typing import TYPE_CHECKING

from codeoracle.adapters.llm.providers.mock_embed import MockEmbeddingsProvider
from codeoracle.adapters.llm.providers.openai_embed import OpenAIEmbeddingsProvider

if TYPE_CHECKING:
    # Settings import lives in TYPE_CHECKING to avoid a runtime cycle:
    # core/config.py never imports this module, so a deferred type-only
    # hint here is the cleanest way to type-annotate `build(settings)`.
    from codeoracle.core.config import Settings


class EmbeddingsProviderFactory:
    """Selects an EmbeddingsProvider implementation from settings.

    `EmbeddingConfig.kind` (added in Step 7a) selects:
    - "openai" -> OpenAIEmbeddingsProvider with OpenAI base URL
    - "vllm"   -> OpenAIEmbeddingsProvider with vLLM base URL (OpenAI-compatible)
    - "mock"   -> MockEmbeddingsProvider (tests only; the api_key precondition
                  in OpenAIEmbeddingsProvider.__init__ is bypassed because the
                  mock never constructs a real client)
    """

    @staticmethod
    def build(settings: "Settings") -> EmbeddingsProvider:
        kind = settings.embedding.kind
        if kind == "mock":
            return MockEmbeddingsProvider(dim=settings.embedding.dim)
        if kind in ("openai", "vllm"):
            return OpenAIEmbeddingsProvider(
                base_url=settings.embedding.base_url,
                api_key=settings.embedding.api_key,
                model=settings.embedding.model,
                dim=settings.embedding.dim,
            )
        raise ValueError(f"unknown embeddings provider kind: {kind!r}")
```

- [ ] **Step 8: Run unit tests to verify ABC + mock + design-contract regressions**

Run: `uv run pytest tests/unit/adapters/llm/test_embeddings_provider.py -v`

Expected: PASS (9 tests — 7 ABC/mock-contract tests + the 2 design-decision regression tests `test_openai_embeddings_provider_rejects_empty_api_key` + `test_embedding_config_constructs_without_api_key`).

- [ ] **Step 9: Write live OpenAI/vLLM test (gated by `--run-live-embeddings`)**

Create `tests/integration/adapters/llm/test_openai_embeddings_provider_live.py`:

```python
"""Live OpenAI/vLLM embedding round-trip — gated by --run-live-embeddings.

Skipped by default. Run with:
  uv run pytest tests/integration/adapters/llm/test_openai_embeddings_provider_live.py --run-live-embeddings

This test asserts:
1. The configured endpoint accepts a single-input batch.
2. The returned vector dim matches EmbeddingConfig.dim.
3. The returned vector contains float values (not strings, not None).
4. A second call with the same input returns the same vector (provider
   determinism — OpenAI guarantees this for embeddings).

It uses real env vars (CODEORACLE_EMBEDDING__BASE_URL, CODEORACLE_EMBEDDING__API_KEY, CODEORACLE_EMBEDDING__MODEL).
"""

import os

import pytest

from codeoracle.adapters.llm.providers.openai_embed import OpenAIEmbeddingsProvider


pytestmark = pytest.mark.live_embeddings


@pytest.mark.asyncio
async def test_live_embeddings_single_input() -> None:
    base_url = os.environ.get("CODEORACLE_EMBEDDING__BASE_URL", "https://api.openai.com")
    api_key = os.environ.get("CODEORACLE_EMBEDDING__API_KEY")
    model = os.environ.get("CODEORACLE_EMBEDDING__MODEL", "text-embedding-3-small")
    dim = int(os.environ.get("CODEORACLE_EMBEDDING__DIM", "1536"))

    if not api_key:
        pytest.skip("CODEORACLE_EMBEDDING__API_KEY not set")

    provider = OpenAIEmbeddingsProvider(base_url=base_url, api_key=api_key, model=model, dim=dim)
    try:
        vecs = await provider.embed(["smoke test"])
        assert len(vecs) == 1
        assert len(vecs[0]) == dim
        assert all(isinstance(x, float) for x in vecs[0])

        vecs2 = await provider.embed(["smoke test"])
        assert vecs == vecs2
    finally:
        await provider.aclose()
```

Add `--run-live-embeddings` to `tests/conftest.py` (mirroring Plan 4's `--run-live-llm`):

```python
def pytest_addoption(parser):  # type: ignore[no-redef]
    # Plan 4 already added --run-live-llm; add a sibling here.
    parser.addoption(
        "--run-live-embeddings",
        action="store_true",
        default=False,
        help="Run live-embedding tests (requires EMBED_* env vars).",
    )


def pytest_collection_modifyitems(config, items):  # type: ignore[no-redef]
    if not config.getoption("--run-live-embeddings"):
        skip_live = pytest.mark.skip(reason="needs --run-live-embeddings")
        for item in items:
            if "live_embeddings" in item.keywords:
                item.add_marker(skip_live)
```

(If `pytest_addoption` already exists in conftest, append the new option and the new skip-loop branch alongside the existing live-llm branch.)

- [ ] **Step 10: Verify live test is skipped without flag (the default)**

Run: `uv run pytest tests/integration/adapters/llm/test_openai_embeddings_provider_live.py -v`

Expected: SKIPPED (1 test, skipped due to `--run-live-embeddings` flag missing).

- [ ] **Step 11: Commit**

```bash
git add tests/unit/adapters/llm/test_embeddings_provider.py \
        tests/integration/adapters/llm/test_openai_embeddings_provider_live.py \
        src/codeoracle/adapters/llm/embeddings.py \
        src/codeoracle/adapters/llm/providers/openai_embed.py \
        src/codeoracle/adapters/llm/providers/mock_embed.py \
        src/codeoracle/core/config.py \
        tests/conftest.py \
        pyproject.toml
git commit -m "feat(adapters): EmbeddingsProvider ABC + OpenAI/Mock providers (Plan 6 Task 7)"
```

---

### Task 8: SCIP Adapter — ScipClient + ScipIndexParser

**Files:**
- Create: `src/codeoracle/adapters/scip/__init__.py` (exports)
- Create: `src/codeoracle/adapters/scip/client.py` (subprocess wrapper)
- Create: `src/codeoracle/adapters/scip/parser.py` (text-format `.scip` output parser)
- Test: `tests/unit/adapters/scip/test_scip_client.py` (subprocess shape)
- Test: `tests/unit/adapters/scip/test_scip_parser.py` (text-format parser)
- Test fixtures: `tests/unit/adapters/scip/fixtures/sample.scip.txt` (golden output)

**Background:** `scip-python` is a static indexer (similar in spirit to LSIF). Its `index` subcommand emits a binary `.scip` file. The `scip` CLI tool's `print` subcommand renders that file as text. Plan 6 parses the text format because (a) it's stable across versions, (b) Python parsing is trivial, (c) avoiding protobuf bindings simplifies dependencies. The trade-off — slightly slower than reading the binary — is acceptable for a project-mode worker that runs occasionally.

**Subprocess safety:** Same `argv-only` (no `shell=True`) pattern as Plan 5's `JoernClient`. The implementer must NOT interpolate user-controlled paths into a shell string; always pass `["scip-python", "index", "--project-name", project_name, "--output", output_path, project_dir]` as a list.

**Sample text output (golden fixture):** the test fixture is what `scip print` produces against a small two-file Python project. The parser must handle line-by-line iteration of records like:

```
File: src/codeoracle/example.py
Symbol: scip-python python . . `src/codeoracle/example.py`/foo()
Definition: 5:0-5:8
Reference: 12:4-12:7
Reference: 18:8-18:11

File: src/codeoracle/example2.py
Symbol: scip-python python . . `src/codeoracle/example.py`/foo()
Reference: 3:4-3:7
```

Note: ranges are 0-indexed (line:col-line:col). The parser converts to 1-indexed for storage (matching `code_references.line_number >= 1` constraint).

- [ ] **Step 1: Write the parser test**

Create `tests/unit/adapters/scip/fixtures/sample.scip.txt`:

```
File: src/codeoracle/example.py
Symbol: scip-python python . . `src/codeoracle/example.py`/foo()
Definition: 4:0-4:8
Reference: 11:4-11:7
Reference: 17:8-17:11

File: src/codeoracle/example.py
Symbol: scip-python python . . `src/codeoracle/example.py`/Bar#
Definition: 20:6-20:9

File: src/codeoracle/example2.py
Symbol: scip-python python . . `src/codeoracle/example.py`/foo()
Reference: 2:4-2:7
```

Create `tests/unit/adapters/scip/test_scip_parser.py`:

```python
"""ScipIndexParser — text-format `scip print` output parser."""

from __future__ import annotations

import pathlib

import pytest

from codeoracle.adapters.scip.parser import (
    ScipIndexParser,
    ScipParseError,
    ScipReference,
    ScipDefinition,
)


FIXTURES = pathlib.Path(__file__).parent / "fixtures"


def test_parses_sample_fixture() -> None:
    sample_text = (FIXTURES / "sample.scip.txt").read_text()
    parser = ScipIndexParser()
    result = parser.parse(sample_text)

    # We expect:
    # - 2 file entries
    # - 1 definition of foo() in example.py at line 5 (0-idx 4 -> 1-idx 5)
    # - 1 definition of Bar in example.py at line 21
    # - 2 references to foo() in example.py (lines 12, 18)
    # - 1 reference to foo() in example2.py (line 3)
    assert len(result.definitions) == 2
    assert len(result.references) == 3

    foo_def = next(d for d in result.definitions if d.fqn.endswith("/foo()"))
    assert foo_def.file_path == "src/codeoracle/example.py"
    assert foo_def.line_number == 5  # 1-indexed

    bar_def = next(d for d in result.definitions if d.fqn.endswith("/Bar#"))
    assert bar_def.file_path == "src/codeoracle/example.py"
    assert bar_def.line_number == 21

    foo_refs = [r for r in result.references if r.fqn.endswith("/foo()")]
    foo_ref_lines = sorted([(r.file_path, r.line_number) for r in foo_refs])
    assert foo_ref_lines == [
        ("src/codeoracle/example.py", 12),
        ("src/codeoracle/example.py", 18),
        ("src/codeoracle/example2.py", 3),
    ]


def test_empty_input_returns_empty_result() -> None:
    parser = ScipIndexParser()
    result = parser.parse("")
    assert result.definitions == []
    assert result.references == []


def test_files_covered_excludes_files_with_no_occurrences() -> None:
    """The parser exposes `result.files_covered` — the set of file paths that
    appeared at least once. Workers use this to detect files NOT in the SCIP
    output (which still need outcome rows written, per architectural invariant 2)."""
    sample_text = (FIXTURES / "sample.scip.txt").read_text()
    parser = ScipIndexParser()
    result = parser.parse(sample_text)
    assert result.files_covered == {"src/codeoracle/example.py", "src/codeoracle/example2.py"}


def test_malformed_line_raises_with_context() -> None:
    """A garbled line should raise ScipParseError with the line content and
    line number for diagnostics; the worker catches this and treats it as
    project-level failure (writes outcome=failed for all changeset files)."""
    parser = ScipIndexParser()
    bad = """File: src/x.py
NOT A VALID LINE
"""
    with pytest.raises(ScipParseError) as exc_info:
        parser.parse(bad)
    assert "NOT A VALID LINE" in str(exc_info.value)
    assert "line 2" in str(exc_info.value)


def test_ignores_blank_lines() -> None:
    sample_text = """
File: a.py

Symbol: scip-python python . . `a.py`/foo()
Definition: 0:0-0:3

"""
    parser = ScipIndexParser()
    result = parser.parse(sample_text)
    assert len(result.definitions) == 1
    assert result.definitions[0].fqn.endswith("/foo()")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/adapters/scip/test_scip_parser.py -v`

Expected: FAIL — parser module doesn't exist.

- [ ] **Step 3: Implement the parser**

Create `src/codeoracle/adapters/scip/parser.py`:

```python
"""ScipIndexParser — text-format `scip print` output parser.

The text format is whitespace-significant line-oriented:

  File: <path>
  Symbol: <scip-symbol-identifier>
  Definition: <start_line>:<start_col>-<end_line>:<end_col>
  Reference: <start_line>:<start_col>-<end_line>:<end_col>
  ...

Records are grouped per (File, Symbol) pair: each Symbol record under a
File record applies to that File until the next File or Symbol record.
Definitions and References can repeat for the same Symbol.

Line/column numbers in SCIP are 0-indexed; this parser converts to 1-indexed
to align with `code_references.line_number >= 1` and with how editors display
line numbers (humans count from 1).

Out-of-scope: parsing the binary protobuf `.scip` format. The text path is
sufficient for Plan 6's volume; if performance becomes a concern, a separate
adapter using `scip-protobuf` Python bindings can be added later.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field


@dataclass(frozen=True, slots=True)
class ScipDefinition:
    """One definition occurrence in a file."""

    file_path: str
    fqn: str  # The full SCIP symbol identifier — used as MERGE key in AGE.
    line_number: int  # 1-indexed start line.
    end_line: int  # 1-indexed end line.


@dataclass(frozen=True, slots=True)
class ScipReference:
    """One reference occurrence in a file."""

    file_path: str
    fqn: str
    line_number: int
    end_line: int


@dataclass(frozen=True, slots=True)
class ScipParseResult:
    definitions: list[ScipDefinition] = field(default_factory=list)
    references: list[ScipReference] = field(default_factory=list)
    files_covered: set[str] = field(default_factory=set)


class ScipParseError(ValueError):
    """Raised when `scip print` output is structurally malformed.

    The worker catches this and treats it as a project-level failure
    (per architectural invariant 3: SCIP failure granularity = project-level).
    """


_FILE_RE = re.compile(r"^File:\s+(.+)$")
_SYMBOL_RE = re.compile(r"^Symbol:\s+(.+)$")
_OCCURRENCE_RE = re.compile(
    r"^(Definition|Reference):\s+(\d+):(\d+)-(\d+):(\d+)$"
)


class ScipIndexParser:
    def parse(self, text: str) -> ScipParseResult:
        result = ScipParseResult()
        current_file: str | None = None
        current_symbol: str | None = None

        for idx, raw in enumerate(text.splitlines(), start=1):
            line = raw.strip()
            if not line:
                continue

            if (m := _FILE_RE.match(line)):
                current_file = m.group(1).strip()
                current_symbol = None
                result.files_covered.add(current_file)
                continue

            if (m := _SYMBOL_RE.match(line)):
                if current_file is None:
                    raise ScipParseError(
                        f"line {idx}: 'Symbol:' record before any 'File:' record: {line!r}"
                    )
                current_symbol = m.group(1).strip()
                continue

            if (m := _OCCURRENCE_RE.match(line)):
                if current_file is None or current_symbol is None:
                    raise ScipParseError(
                        f"line {idx}: occurrence record without active File+Symbol: {line!r}"
                    )
                kind = m.group(1)
                start_line_0 = int(m.group(2))
                end_line_0 = int(m.group(4))
                # SCIP is 0-indexed; we store 1-indexed.
                start_line = start_line_0 + 1
                end_line = end_line_0 + 1
                if kind == "Definition":
                    result.definitions.append(
                        ScipDefinition(
                            file_path=current_file,
                            fqn=current_symbol,
                            line_number=start_line,
                            end_line=end_line,
                        )
                    )
                else:  # Reference
                    result.references.append(
                        ScipReference(
                            file_path=current_file,
                            fqn=current_symbol,
                            line_number=start_line,
                            end_line=end_line,
                        )
                    )
                continue

            raise ScipParseError(f"line {idx}: unrecognized record: {line!r}")

        return result
```

- [ ] **Step 4: Run parser test to verify it passes**

Run: `uv run pytest tests/unit/adapters/scip/test_scip_parser.py -v`

Expected: PASS (5 tests).

- [ ] **Step 5: Write the subprocess client test**

Create `tests/unit/adapters/scip/test_scip_client.py`:

```python
"""ScipClient — subprocess wrapper for scip-python + scip.

These tests use the `unittest.mock` patch on `asyncio.create_subprocess_exec`
to validate the argv structure WITHOUT actually invoking scip-python (which
isn't available in CI in all environments — although Plan 2's preflight does
verify it locally).

Live e2e exercise of the real binary happens in the SCIP worker integration
test (Task 10) inside the testcontainers slice.
"""

from __future__ import annotations

import asyncio
import pathlib
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from codeoracle.adapters.scip.client import ScipClient
from codeoracle.core.exceptions import ScipExtractionError


@pytest.mark.asyncio
async def test_index_invokes_scip_python_with_correct_argv(tmp_path: pathlib.Path) -> None:
    """argv shape: ['scip-python', 'index', '--project-name', name, '--output', out_path, project_dir]"""
    project = tmp_path / "project"
    project.mkdir()
    output = tmp_path / "index.scip"

    mock_proc = AsyncMock()
    mock_proc.communicate = AsyncMock(return_value=(b"", b""))
    mock_proc.returncode = 0

    with patch("asyncio.create_subprocess_exec", new=AsyncMock(return_value=mock_proc)) as p:
        client = ScipClient()
        await client.index_project(
            project_dir=str(project), project_name="codeoracle", output_path=str(output),
        )
        # Verify argv:
        called_args = p.call_args[0]
        assert called_args[0] == "scip-python"
        assert called_args[1] == "index"
        assert "--project-name" in called_args
        assert "codeoracle" in called_args
        assert "--output" in called_args
        assert str(output) in called_args
        assert str(project) in called_args


@pytest.mark.asyncio
async def test_index_raises_scip_extraction_error_on_nonzero_exit(
    tmp_path: pathlib.Path,
) -> None:
    mock_proc = AsyncMock()
    mock_proc.communicate = AsyncMock(return_value=(b"", b"oh no"))
    mock_proc.returncode = 2

    with patch("asyncio.create_subprocess_exec", new=AsyncMock(return_value=mock_proc)):
        client = ScipClient()
        with pytest.raises(ScipExtractionError) as exc_info:
            await client.index_project(
                project_dir=str(tmp_path), project_name="codeoracle", output_path=str(tmp_path / "i.scip"),
            )
        # Error message includes truncated stderr for triage.
        assert "oh no" in str(exc_info.value)


@pytest.mark.asyncio
async def test_print_index_invokes_scip_with_correct_argv(tmp_path: pathlib.Path) -> None:
    scip_file = tmp_path / "index.scip"
    scip_file.write_bytes(b"fake")

    mock_proc = AsyncMock()
    mock_proc.communicate = AsyncMock(return_value=(b"text-output", b""))
    mock_proc.returncode = 0

    with patch("asyncio.create_subprocess_exec", new=AsyncMock(return_value=mock_proc)) as p:
        client = ScipClient()
        text = await client.print_index(scip_file=str(scip_file))
        assert text == "text-output"
        called_args = p.call_args[0]
        assert called_args[0] == "scip"
        assert called_args[1] == "print"
        assert str(scip_file) in called_args


@pytest.mark.asyncio
async def test_print_index_raises_on_nonzero_exit(tmp_path: pathlib.Path) -> None:
    scip_file = tmp_path / "index.scip"
    scip_file.write_bytes(b"fake")
    mock_proc = AsyncMock()
    mock_proc.communicate = AsyncMock(return_value=(b"", b"corrupted file"))
    mock_proc.returncode = 1
    with patch("asyncio.create_subprocess_exec", new=AsyncMock(return_value=mock_proc)):
        client = ScipClient()
        with pytest.raises(ScipExtractionError) as exc_info:
            await client.print_index(scip_file=str(scip_file))
        assert "corrupted file" in str(exc_info.value)


@pytest.mark.asyncio
async def test_index_kills_and_raises_on_timeout(tmp_path: pathlib.Path) -> None:
    """A hung scip-python is killed and surfaced as ScipExtractionError, not
    left to wedge the handle and stall the reindex barrier. Bounding the handle
    this way is the precondition for ever turning stream reclaim on."""

    async def _hang() -> tuple[bytes, bytes]:
        await asyncio.sleep(30)
        return (b"", b"")

    mock_proc = AsyncMock()
    mock_proc.communicate = _hang
    mock_proc.kill = MagicMock()  # Process.kill() is synchronous
    mock_proc.wait = AsyncMock()

    with patch("asyncio.create_subprocess_exec", new=AsyncMock(return_value=mock_proc)):
        client = ScipClient(index_timeout_seconds=0.01)
        with pytest.raises(ScipExtractionError, match="index timed out"):
            await client.index_project(
                project_dir=str(tmp_path), project_name="codeoracle",
                output_path=str(tmp_path / "i.scip"),
            )
    mock_proc.kill.assert_called_once()
    mock_proc.wait.assert_awaited_once()


@pytest.mark.asyncio
async def test_print_kills_and_raises_on_timeout(tmp_path: pathlib.Path) -> None:
    """Same liveness bound on the print phase, sized off print_timeout_seconds."""
    scip_file = tmp_path / "index.scip"
    scip_file.write_bytes(b"fake")

    async def _hang() -> tuple[bytes, bytes]:
        await asyncio.sleep(30)
        return (b"", b"")

    mock_proc = AsyncMock()
    mock_proc.communicate = _hang
    mock_proc.kill = MagicMock()
    mock_proc.wait = AsyncMock()

    with patch("asyncio.create_subprocess_exec", new=AsyncMock(return_value=mock_proc)):
        client = ScipClient(print_timeout_seconds=0.01)
        with pytest.raises(ScipExtractionError, match="print timed out"):
            await client.print_index(scip_file=str(scip_file))
    mock_proc.kill.assert_called_once()
    mock_proc.wait.assert_awaited_once()
```

- [ ] **Step 6: Run client test to verify it fails**

Run: `uv run pytest tests/unit/adapters/scip/test_scip_client.py -v`

Expected: FAIL — `client.py` does not exist.

- [ ] **Step 7: Implement the subprocess client**

Create `src/codeoracle/adapters/scip/client.py`:

```python
"""ScipClient — argv-only subprocess wrapper.

NO `shell=True`. NO string interpolation into a shell command. All inputs
are passed as positional argv entries. This is the same safety pattern as
Plan 5's JoernClient.
"""

from __future__ import annotations

import asyncio
from typing import Final

from codeoracle.core.exceptions import ScipExtractionError


_STDERR_TRUNCATE: Final[int] = 4096

# Liveness ceilings for the scip subprocesses. A scip handle runs index then
# print back-to-back (count_per_poll=1), so worst-case handle = index + print
# = 360s, comfortably under the IndexingOrchestrator completion barrier (600s,
# Plan 5). Without these a hung scip-python would stall the handle forever and
# the reindex barrier could never close. They bound the handle so a hung run is
# killed and surfaced as a per-file `failed` outcome instead.
DEFAULT_SCIP_INDEX_TIMEOUT_SECONDS: Final[float] = 300.0
DEFAULT_SCIP_PRINT_TIMEOUT_SECONDS: Final[float] = 60.0


class ScipClient:
    """Wraps `scip-python index` + `scip print`.

    `index_project` runs `scip-python` to build the binary `.scip` index for
    a project directory. `print_index` runs `scip` to render that file as text.
    The combination lets us avoid linking against scip's protobuf bindings.

    Both methods raise `ScipExtractionError` on non-zero exit; the worker
    catches this and treats it as a project-level failure (architectural
    invariant 3 — all changeset files written as outcome=failed). Each
    subprocess also runs under a wall-clock timeout (`index_timeout_seconds`
    / `print_timeout_seconds`); on expiry the child is killed and the same
    `ScipExtractionError` is raised, so a hung scip run can never wedge the
    handle or stall the reindex barrier.
    """

    def __init__(
        self,
        *,
        index_timeout_seconds: float = DEFAULT_SCIP_INDEX_TIMEOUT_SECONDS,
        print_timeout_seconds: float = DEFAULT_SCIP_PRINT_TIMEOUT_SECONDS,
    ) -> None:
        self._index_timeout = index_timeout_seconds
        self._print_timeout = print_timeout_seconds

    async def _communicate_or_kill(
        self,
        proc: asyncio.subprocess.Process,
        *,
        timeout: float,
        phase: str,
    ) -> tuple[bytes, bytes]:
        """Await `proc.communicate()` under a wall-clock ceiling. On expiry,
        SIGKILL the subprocess, reap it, and raise a terminal
        `ScipExtractionError` so the worker records outcome=failed instead of
        leaving a wedged handle that would stall the reindex barrier."""
        try:
            return await asyncio.wait_for(proc.communicate(), timeout=timeout)
        except TimeoutError as exc:
            proc.kill()
            await proc.wait()
            raise ScipExtractionError(
                f"scip {phase} timed out after {timeout:.0f}s"
            ) from exc

    async def index_project(
        self,
        *,
        project_dir: str,
        project_name: str,
        output_path: str,
    ) -> None:
        argv = [
            "scip-python",
            "index",
            "--project-name", project_name,
            "--output", output_path,
            project_dir,
        ]
        proc = await asyncio.create_subprocess_exec(
            *argv,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await self._communicate_or_kill(
            proc, timeout=self._index_timeout, phase="index"
        )
        if proc.returncode != 0:
            stderr_tail = stderr.decode("utf-8", errors="replace")[-_STDERR_TRUNCATE:]
            raise ScipExtractionError(
                f"scip-python exited {proc.returncode}: {stderr_tail}"
            )

    async def print_index(self, *, scip_file: str) -> str:
        argv = ["scip", "print", scip_file]
        proc = await asyncio.create_subprocess_exec(
            *argv,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await self._communicate_or_kill(
            proc, timeout=self._print_timeout, phase="print"
        )
        if proc.returncode != 0:
            stderr_tail = stderr.decode("utf-8", errors="replace")[-_STDERR_TRUNCATE:]
            raise ScipExtractionError(
                f"scip print exited {proc.returncode}: {stderr_tail}"
            )
        return stdout.decode("utf-8", errors="replace")
```

- [ ] **Step 8: Create `__init__.py`**

Create `src/codeoracle/adapters/scip/__init__.py`:

```python
"""SCIP adapter: scip-python + scip subprocess wrapping + text-format parser.

Plan 6. Used by `workers/scip_indexer` (project-mode worker).
"""

from codeoracle.adapters.scip.client import ScipClient
from codeoracle.adapters.scip.parser import (
    ScipDefinition,
    ScipIndexParser,
    ScipParseError,
    ScipParseResult,
    ScipReference,
)

__all__ = [
    "ScipClient",
    "ScipIndexParser",
    "ScipParseError",
    "ScipParseResult",
    "ScipDefinition",
    "ScipReference",
]
```

- [ ] **Step 9: Run all SCIP adapter tests**

Run: `uv run pytest tests/unit/adapters/scip/ -v`

Expected: PASS (9 tests: 5 parser + 4 client).

- [ ] **Step 10: Commit**

```bash
git add tests/unit/adapters/scip/ src/codeoracle/adapters/scip/
git commit -m "feat(adapters): SCIP client + parser (Plan 6 Task 8)"
```

---

### Task 9: OpenGrep Adapter — OpengrepRunner + CodeOracle Rule Pack

**Files:**
- Create: `src/codeoracle/adapters/opengrep/__init__.py`
- Create: `src/codeoracle/adapters/opengrep/runner.py` (subprocess wrapper + JSON-output parser)
- Create: `src/codeoracle/adapters/opengrep/rules/imports.yml` (Python + JavaScript import rules)
- Create: `src/codeoracle/adapters/opengrep/rules/calls.yml` (call-site rules)
- Create: `src/codeoracle/adapters/opengrep/rules/string_literals.yml` (string-literal mentions)
- Create: `src/codeoracle/adapters/opengrep/rules/comment_mentions.yml` (comment mentions)
- Test: `tests/unit/workers/opengrep_runner/test_opengrep_runner_tasks.py` (mocked subprocess + JSON parsing)
- Test: `tests/unit/workers/opengrep_runner/test_opengrep_runner_consumer.py` (gated by binary presence)
- Test fixtures: `tests/fixtures/opengrep/sample_output.json`

**Design:** OpenGrep (a semgrep fork) takes one or more YAML rule files and one or more source files, and emits JSON of matches. We bundle four rule files keyed by `kind` (matching `CodeReferenceKind`):

- `imports.yml` — captures Python `from X import Y` / `import X` patterns; JavaScript ES `import` patterns.
- `calls.yml` — captures function/method call sites `$X($_)`.
- `string_literals.yml` — captures string literals whose value matches a symbol-name pattern (used to find references in dynamic code).
- `comment_mentions.yml` — captures comment lines mentioning a symbol name.

The rules use OpenGrep's metavariable syntax (`$X`) to extract the symbol name. The runner parses the JSON output and emits one `OpengrepMatch` per finding.

**Note on the symbol name source:** OpenGrep matches symbols structurally — it doesn't know the symbol exists in Joern or SCIP. For Plan 6, we capture EVERY identifier that matches a pattern; Plan 9's verifier filters by joining `code_references.symbol_name` against AGE's `:Symbol{fqn}` nodes. This means `code_references` may contain matches for identifiers that aren't real symbols (e.g., a method named `foo` matches both `obj.foo()` and an unrelated string `"foo"`). That's fine — the join with the symbol graph rejects spurious matches.

- [ ] **Step 1: Write the runner JSON-parsing test**

Create `tests/fixtures/opengrep/sample_output.json`:

```json
{
  "version": "1.0",
  "results": [
    {
      "check_id": "codeoracle-imports-python",
      "path": "src/codeoracle/example.py",
      "start": {"line": 3, "col": 1},
      "end": {"line": 3, "col": 30},
      "extra": {
        "lines": "from codeoracle.foo import bar",
        "metavars": {
          "$NAME": {"abstract_content": "bar"}
        }
      }
    },
    {
      "check_id": "codeoracle-calls-python",
      "path": "src/codeoracle/example.py",
      "start": {"line": 12, "col": 5},
      "end": {"line": 12, "col": 12},
      "extra": {
        "lines": "    bar(42)",
        "metavars": {
          "$NAME": {"abstract_content": "bar"}
        }
      }
    },
    {
      "check_id": "codeoracle-string-literals",
      "path": "src/codeoracle/example.py",
      "start": {"line": 20, "col": 10},
      "end": {"line": 20, "col": 17},
      "extra": {
        "lines": "    msg = \"call bar\"",
        "metavars": {
          "$NAME": {"abstract_content": "bar"}
        }
      }
    }
  ]
}
```

Create `tests/unit/workers/opengrep_runner/test_opengrep_runner_tasks.py`:

```python
"""OpengrepRunner — subprocess wrapper + JSON-output parsing."""

from __future__ import annotations

import asyncio
import json
import pathlib
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from codeoracle.adapters.opengrep.runner import (
    OpengrepMatch,
    OpengrepRunner,
)
from codeoracle.core.exceptions import OpengrepRunError


FIXTURES = pathlib.Path(__file__).parent / "fixtures"


@pytest.mark.asyncio
async def test_scan_invokes_opengrep_with_correct_argv(tmp_path: pathlib.Path) -> None:
    """argv shape: ['opengrep', 'scan', '--config', rules_dir, '--json', file_path]"""
    sample_file = tmp_path / "example.py"
    sample_file.write_text("")
    rules_dir = tmp_path / "rules"
    rules_dir.mkdir()

    fake_output = json.dumps({"results": []}).encode("utf-8")
    mock_proc = AsyncMock()
    mock_proc.communicate = AsyncMock(return_value=(fake_output, b""))
    mock_proc.returncode = 0

    with patch("asyncio.create_subprocess_exec", new=AsyncMock(return_value=mock_proc)) as p:
        runner = OpengrepRunner(rules_dir=str(rules_dir))
        await runner.scan_file(file_path=str(sample_file))

        called_args = p.call_args[0]
        assert called_args[0] == "opengrep"
        assert "scan" in called_args
        assert "--config" in called_args
        assert str(rules_dir) in called_args
        assert "--json" in called_args
        assert str(sample_file) in called_args


@pytest.mark.asyncio
async def test_parses_sample_fixture(tmp_path: pathlib.Path) -> None:
    rules_dir = tmp_path / "rules"
    rules_dir.mkdir()
    sample_file = tmp_path / "example.py"
    sample_file.write_text("")

    fixture_text = (FIXTURES / "sample_output.json").read_bytes()
    mock_proc = AsyncMock()
    mock_proc.communicate = AsyncMock(return_value=(fixture_text, b""))
    mock_proc.returncode = 0

    with patch("asyncio.create_subprocess_exec", new=AsyncMock(return_value=mock_proc)):
        runner = OpengrepRunner(rules_dir=str(rules_dir))
        matches = await runner.scan_file(file_path=str(sample_file))

    assert len(matches) == 3
    by_kind = {m.kind for m in matches}
    assert by_kind == {"import", "call", "string-literal"}

    import_match = next(m for m in matches if m.kind == "import")
    assert import_match.symbol_name == "bar"
    assert import_match.line_number == 3

    call_match = next(m for m in matches if m.kind == "call")
    assert call_match.symbol_name == "bar"
    assert call_match.line_number == 12

    literal_match = next(m for m in matches if m.kind == "string-literal")
    assert literal_match.symbol_name == "bar"
    assert literal_match.line_number == 20


@pytest.mark.asyncio
async def test_unknown_check_id_raises(tmp_path: pathlib.Path) -> None:
    """A `check_id` that doesn't map to one of the 4 known kinds is a bug —
    either a typo in the bundled rules, or someone added a rule without
    updating the runner's check_id->kind map. Raise immediately."""
    rules_dir = tmp_path / "rules"
    rules_dir.mkdir()
    sample_file = tmp_path / "x.py"
    sample_file.write_text("")

    bogus_output = json.dumps({
        "results": [
            {
                "check_id": "totally-bogus-rule",
                "path": str(sample_file),
                "start": {"line": 1, "col": 1},
                "end": {"line": 1, "col": 5},
                "extra": {"metavars": {"$NAME": {"abstract_content": "x"}}},
            }
        ]
    }).encode("utf-8")
    mock_proc = AsyncMock()
    mock_proc.communicate = AsyncMock(return_value=(bogus_output, b""))
    mock_proc.returncode = 0

    with patch("asyncio.create_subprocess_exec", new=AsyncMock(return_value=mock_proc)):
        runner = OpengrepRunner(rules_dir=str(rules_dir))
        with pytest.raises(OpengrepRunError) as exc_info:
            await runner.scan_file(file_path=str(sample_file))
        assert "totally-bogus-rule" in str(exc_info.value)


@pytest.mark.asyncio
async def test_nonzero_exit_raises(tmp_path: pathlib.Path) -> None:
    rules_dir = tmp_path / "rules"
    rules_dir.mkdir()
    sample_file = tmp_path / "x.py"
    sample_file.write_text("")

    mock_proc = AsyncMock()
    mock_proc.communicate = AsyncMock(return_value=(b"", b"rule parse error"))
    mock_proc.returncode = 2

    with patch("asyncio.create_subprocess_exec", new=AsyncMock(return_value=mock_proc)):
        runner = OpengrepRunner(rules_dir=str(rules_dir))
        with pytest.raises(OpengrepRunError) as exc_info:
            await runner.scan_file(file_path=str(sample_file))
        assert "rule parse error" in str(exc_info.value)


@pytest.mark.asyncio
async def test_unparsable_stdout_raises(tmp_path: pathlib.Path) -> None:
    rules_dir = tmp_path / "rules"
    rules_dir.mkdir()
    sample_file = tmp_path / "x.py"
    sample_file.write_text("")

    mock_proc = AsyncMock()
    mock_proc.communicate = AsyncMock(return_value=(b"this is not JSON", b""))
    mock_proc.returncode = 0
    with patch("asyncio.create_subprocess_exec", new=AsyncMock(return_value=mock_proc)):
        runner = OpengrepRunner(rules_dir=str(rules_dir))
        with pytest.raises(OpengrepRunError) as exc_info:
            await runner.scan_file(file_path=str(sample_file))
        assert "not JSON" in str(exc_info.value) or "decode" in str(exc_info.value).lower()


@pytest.mark.asyncio
async def test_empty_results_returns_empty_list(tmp_path: pathlib.Path) -> None:
    rules_dir = tmp_path / "rules"
    rules_dir.mkdir()
    sample_file = tmp_path / "empty.py"
    sample_file.write_text("")

    mock_proc = AsyncMock()
    mock_proc.communicate = AsyncMock(return_value=(b'{"results": []}', b""))
    mock_proc.returncode = 0
    with patch("asyncio.create_subprocess_exec", new=AsyncMock(return_value=mock_proc)):
        runner = OpengrepRunner(rules_dir=str(rules_dir))
        matches = await runner.scan_file(file_path=str(sample_file))
    assert matches == []


@pytest.mark.asyncio
async def test_scan_kills_and_raises_on_timeout(tmp_path: pathlib.Path) -> None:
    """A wedged opengrep scan is killed and surfaced as OpengrepRunError. With
    count_per_poll=5 an unbounded scan would stall the whole batch past the
    reindex barrier; the timeout bounds each file's handle."""
    rules_dir = tmp_path / "rules"
    rules_dir.mkdir()
    sample_file = tmp_path / "x.py"
    sample_file.write_text("")

    async def _hang() -> tuple[bytes, bytes]:
        await asyncio.sleep(30)
        return (b"", b"")

    mock_proc = AsyncMock()
    mock_proc.communicate = _hang
    mock_proc.kill = MagicMock()  # Process.kill() is synchronous
    mock_proc.wait = AsyncMock()

    with patch("asyncio.create_subprocess_exec", new=AsyncMock(return_value=mock_proc)):
        runner = OpengrepRunner(rules_dir=str(rules_dir), scan_timeout_seconds=0.01)
        with pytest.raises(OpengrepRunError, match="timed out"):
            await runner.scan_file(file_path=str(sample_file))
    mock_proc.kill.assert_called_once()
    mock_proc.wait.assert_awaited_once()
```

- [ ] **Step 2: Run runner test to verify it fails**

Run: `uv run pytest tests/unit/workers/opengrep_runner/test_opengrep_runner_tasks.py -v`

Expected: FAIL — runner module does not exist.

- [ ] **Step 3: Implement the runner**

Create `src/codeoracle/adapters/opengrep/runner.py`:

```python
"""OpengrepRunner — subprocess wrapper for opengrep CLI.

Argv shape (no `shell=True`):
  opengrep scan --config <rules_dir> --json <file_path>

The JSON output is parsed into a list of `OpengrepMatch` records, each
classified by `kind` via the `check_id` field. The `check_id`->kind map is
hardcoded in `_CHECK_ID_TO_KIND` — adding a new rule means adding a new
entry there.

Symbol-name extraction: every rule captures the symbol name into the `$NAME`
metavariable; the runner reads `extra.metavars.$NAME.abstract_content`. If a
match has no `$NAME` metavar, it's a bug in the rule (the rule must capture
the identifier into $NAME) — the runner raises OpengrepRunError.

Verify-binary check: `verify_binary()` runs `opengrep --version`; if it
fails the runner raises `OpengrepRunError("binary not callable")`. The
worker calls this once at startup and bails before consuming any messages
if the binary is missing.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from typing import Any, Final, Literal

from codeoracle.core.exceptions import OpengrepRunError


CodeReferenceKind = Literal["import", "call", "string-literal", "comment-mention"]


# Bundled rule check_ids — these must match the IDs defined in rules/*.yml.
_CHECK_ID_TO_KIND: Final[dict[str, CodeReferenceKind]] = {
    "codeoracle-imports-python": "import",
    "codeoracle-imports-js": "import",
    "codeoracle-calls-python": "call",
    "codeoracle-calls-js": "call",
    "codeoracle-string-literals": "string-literal",
    "codeoracle-comment-mentions": "comment-mention",
}

_STDERR_TRUNCATE: Final[int] = 4096

# Liveness ceiling for one opengrep scan. The consumer reads count_per_poll=5
# files per poll and scans them back-to-back (Plan 5's WorkerConsumer drains a
# batch sequentially — `for raw in consumed: await _process_one`), so a
# worst-case batch = 5 × this. Held at 100s so a fully-pathological batch
# (5 × 100 = 500s) stays comfortably under the IndexingOrchestrator completion
# barrier (600s, Plan 5) with ~100s of slack — at 120s the batch would reach
# the 600s barrier exactly and could fire mid-batch, mis-marking not-yet-reached
# files FAILED. Per-file opengrep scans are normally sub-second; this is a
# kill-switch for a wedged scan, not an expected duration.
DEFAULT_OPENGREP_SCAN_TIMEOUT_SECONDS: Final[float] = 100.0


@dataclass(frozen=True, slots=True)
class OpengrepMatch:
    """One match from one OpenGrep rule, with kind classified by check_id."""

    file_path: str
    symbol_name: str
    line_number: int  # 1-indexed (OpenGrep is already 1-indexed)
    kind: CodeReferenceKind


class OpengrepRunner:
    def __init__(
        self,
        *,
        rules_dir: str,
        scan_timeout_seconds: float = DEFAULT_OPENGREP_SCAN_TIMEOUT_SECONDS,
    ) -> None:
        self._rules_dir = rules_dir
        self._scan_timeout = scan_timeout_seconds

    async def verify_binary(self) -> str:
        """Smoke-test the opengrep binary. Returns the version string;
        raises `OpengrepRunError("binary not callable")` if missing."""
        try:
            proc = await asyncio.create_subprocess_exec(
                "opengrep", "--version",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except FileNotFoundError as e:
            raise OpengrepRunError(f"binary not callable: {e}") from e
        stdout, stderr = await proc.communicate()
        if proc.returncode != 0:
            raise OpengrepRunError(
                f"opengrep --version exited {proc.returncode}: "
                f"{stderr.decode('utf-8', errors='replace')[-_STDERR_TRUNCATE:]}"
            )
        return stdout.decode("utf-8", errors="replace").strip()

    async def scan_file(self, *, file_path: str) -> list[OpengrepMatch]:
        """Scan one file against the bundled rule pack. Returns matches."""
        argv = ["opengrep", "scan", "--config", self._rules_dir, "--json", file_path]
        proc = await asyncio.create_subprocess_exec(
            *argv,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(), timeout=self._scan_timeout
            )
        except TimeoutError as exc:
            # A wedged scan is killed and surfaced as a per-file failure rather
            # than stalling the handle (and, with count_per_poll=5, the rest of
            # the batch) past the reindex barrier.
            proc.kill()
            await proc.wait()
            raise OpengrepRunError(
                f"opengrep scan timed out after {self._scan_timeout:.0f}s: {file_path}"
            ) from exc
        if proc.returncode != 0:
            stderr_tail = stderr.decode("utf-8", errors="replace")[-_STDERR_TRUNCATE:]
            raise OpengrepRunError(f"opengrep exited {proc.returncode}: {stderr_tail}")

        try:
            payload = json.loads(stdout.decode("utf-8", errors="replace"))
        except json.JSONDecodeError as e:
            raise OpengrepRunError(f"opengrep stdout not JSON: {e}; head={stdout[:200]!r}") from e

        results = payload.get("results", [])
        matches: list[OpengrepMatch] = []
        for r in results:
            check_id = r.get("check_id", "")
            kind = _CHECK_ID_TO_KIND.get(check_id)
            if kind is None:
                raise OpengrepRunError(
                    f"unknown check_id={check_id!r}; rule pack drifted from runner _CHECK_ID_TO_KIND"
                )
            try:
                path = r["path"]
                line_number = int(r["start"]["line"])
                metavars = r["extra"]["metavars"]
                symbol_name = metavars["$NAME"]["abstract_content"]
            except (KeyError, ValueError, TypeError) as e:
                raise OpengrepRunError(
                    f"malformed opengrep result for check_id={check_id!r}: {e}; result={r!r}"
                ) from e
            matches.append(
                OpengrepMatch(
                    file_path=path,
                    symbol_name=symbol_name,
                    line_number=line_number,
                    kind=kind,
                )
            )
        return matches
```

- [ ] **Step 4: Create the four rule files**

Create `src/codeoracle/adapters/opengrep/rules/imports.yml`:

```yaml
rules:
  - id: codeoracle-imports-python
    languages: [python]
    message: "Python import: $NAME"
    severity: INFO
    patterns:
      - pattern-either:
          - pattern: from $MODULE import $NAME
          - pattern: from $MODULE import $NAME as $_
          - pattern: import $NAME
          - pattern: import $NAME as $_

  - id: codeoracle-imports-js
    languages: [javascript, typescript]
    message: "JS/TS import: $NAME"
    severity: INFO
    patterns:
      - pattern-either:
          - pattern: import $NAME from "$_"
          - pattern: import { $NAME } from "$_"
          - pattern: import { $NAME as $_ } from "$_"
          - pattern: const $NAME = require("$_")
```

Create `src/codeoracle/adapters/opengrep/rules/calls.yml`:

```yaml
rules:
  - id: codeoracle-calls-python
    languages: [python]
    message: "Python call: $NAME"
    severity: INFO
    patterns:
      - pattern-either:
          - pattern: $NAME($...ARGS)
          - pattern: $OBJ.$NAME($...ARGS)

  - id: codeoracle-calls-js
    languages: [javascript, typescript]
    message: "JS/TS call: $NAME"
    severity: INFO
    patterns:
      - pattern-either:
          - pattern: $NAME($...ARGS)
          - pattern: $OBJ.$NAME($...ARGS)
```

Create `src/codeoracle/adapters/opengrep/rules/string_literals.yml`:

```yaml
rules:
  - id: codeoracle-string-literals
    languages: [python, javascript, typescript]
    message: "String literal mentioning identifier: $NAME"
    severity: INFO
    pattern-regex: |
      "(?<NAME>[a-zA-Z_][a-zA-Z_0-9]{2,})"
```

(The regex syntax for capturing into `$NAME` may differ slightly between OpenGrep versions; if `pattern-regex` doesn't support named captures, the implementer may need to switch to a `pattern` form. Verify via `opengrep --version` and the matching docs at implementation time.)

Create `src/codeoracle/adapters/opengrep/rules/comment_mentions.yml`:

```yaml
rules:
  - id: codeoracle-comment-mentions
    languages: [python, javascript, typescript]
    message: "Comment mentioning identifier: $NAME"
    severity: INFO
    pattern-regex: |
      (?:#|//)\s*.*\b(?<NAME>[a-zA-Z_][a-zA-Z_0-9]{2,})\b
```

(Comment-mention rules use 3+ char identifiers to avoid trivially noisy 1-2 char matches like `a` or `i`.)

- [ ] **Step 5: Create `__init__.py`**

Create `src/codeoracle/adapters/opengrep/__init__.py`:

```python
"""OpenGrep adapter: subprocess runner + bundled CodeOracle rule pack.

Plan 6. Used by `workers/opengrep_runner` (per-file worker).
"""

import pathlib

from codeoracle.adapters.opengrep.runner import OpengrepMatch, OpengrepRunner

# The bundled rules directory is shipped alongside the adapter module so that
# the worker can locate it without hardcoding install paths.
DEFAULT_RULES_DIR: str = str(pathlib.Path(__file__).parent / "rules")

__all__ = ["OpengrepMatch", "OpengrepRunner", "DEFAULT_RULES_DIR"]
```

- [ ] **Step 6: Write the rule-pack smoke test (live binary required)**

Create `tests/unit/workers/opengrep_runner/test_opengrep_runner_consumer.py`:

```python
"""Live OpenGrep smoke test — runs the bundled rule pack against a tiny fixture.

Skipped if `opengrep` is not on PATH. This catches rule-pack regressions
during local dev; CI runs it inside the Docker container that has opengrep
installed.
"""

from __future__ import annotations

import pathlib
import shutil

import pytest

from codeoracle.adapters.opengrep import DEFAULT_RULES_DIR, OpengrepRunner


pytestmark = pytest.mark.skipif(
    shutil.which("opengrep") is None,
    reason="opengrep binary not on PATH",
)


@pytest.mark.asyncio
async def test_verify_binary_succeeds() -> None:
    runner = OpengrepRunner(rules_dir=DEFAULT_RULES_DIR)
    version = await runner.verify_binary()
    assert version, "opengrep --version returned empty output"


@pytest.mark.asyncio
async def test_python_import_match(tmp_path: pathlib.Path) -> None:
    f = tmp_path / "x.py"
    f.write_text("from os import path\n")
    runner = OpengrepRunner(rules_dir=DEFAULT_RULES_DIR)
    matches = await runner.scan_file(file_path=str(f))
    import_kinds = {m for m in matches if m.kind == "import"}
    assert any(m.symbol_name == "path" for m in import_kinds), (
        f"expected to find import of 'path'; got matches: {matches}"
    )


@pytest.mark.asyncio
async def test_python_call_match(tmp_path: pathlib.Path) -> None:
    f = tmp_path / "x.py"
    f.write_text("def main():\n    foo(1)\n")
    runner = OpengrepRunner(rules_dir=DEFAULT_RULES_DIR)
    matches = await runner.scan_file(file_path=str(f))
    assert any(m.kind == "call" and m.symbol_name == "foo" for m in matches), (
        f"expected to find call to 'foo'; got matches: {matches}"
    )
```

- [ ] **Step 7: Run mocked runner tests + run live smoke if opengrep on PATH**

Run: `uv run pytest tests/unit/workers/opengrep_runner/ -v`

Expected: PASS (mocked tests); live smoke either passes or is skipped depending on binary availability.

- [ ] **Step 8: Commit**

```bash
git add tests/unit/workers/opengrep_runner/ src/codeoracle/adapters/opengrep/
git commit -m "feat(adapters): OpenGrep runner + 4-rule CodeOracle pack (Plan 6 Task 9)"
```

---

### Task 10: SCIP Indexer Worker (project-mode anchor)

**Files:**
- Create: `src/codeoracle/db/repositories/scip_graph.py` (AGE writes for `:Definition` + `:Reference` nodes)
- Test: `tests/integration/db/repositories/test_scip_graph_repo.py`
- Create: `src/codeoracle/workers/scip_indexer/__init__.py`
- Create: `src/codeoracle/workers/scip_indexer/message.py`
- Create: `src/codeoracle/workers/scip_indexer/tasks.py` (pure function `extract_project_to_graph`)
- Create: `src/codeoracle/workers/scip_indexer/consumer.py` (`ScipIndexerConsumer`)
- Create: `src/codeoracle/workers/scip_indexer/__main__.py` (process entrypoint)
- Test: `tests/unit/workers/scip_indexer/test_scip_indexer_message.py`
- Test: `tests/integration/workers/scip_indexer/test_scip_indexer_tasks.py`
- Test: `tests/integration/workers/scip_indexer/test_scip_indexer_consumer.py`

**Project-mode invariants (re-stated for the implementer; reading these BEFORE writing code is non-negotiable):**

1. **One message per snapshot reindex**, not one per file. The orchestrator enqueues `{batch_id, snapshot_sha, repo_root, files: [...]}` exactly once.
2. **One outcome row per file** in `files`, regardless of SCIP coverage. If scip-python doesn't emit symbols for a file (no symbols, parse error silently skipped), the worker STILL writes `outcome=success, error_message=None` for that file. Spec line 1312: "Per-file SCIP-python extraction." — we honor this at the barrier level even when SCIP itself operates project-wide.
3. **Project-level failure → all-file failure**. If `scip-python index` or `scip print` exits non-zero, the worker writes `outcome=failed` rows for EVERY file in `files`, each with `error_message=<truncated stderr or parser error>`. The exception is NOT re-raised; the message IS ACK'd.
4. **AGE label namespace is SCIP-only**. The worker MERGEs `(:Definition {fqn, snapshot_sha, file_path, line_start, line_end})` and `(:Reference {fqn, snapshot_sha, file_path, line_start, line_end})` — never `:Symbol` (Joern's namespace).
5. **Per-file atomicity**. The worker loops `files` and opens ONE transaction per file containing (a) AGE deletes for existing nodes of (snapshot_sha, file_path) with labels `:Definition` + `:Reference`, (b) inserts of new nodes from parser output, (c) `SnapshotFileOutcomeRepository.upsert` of the outcome row. Commit. Then move to next file. A mid-worker crash leaves already-committed files complete; redelivery resumes from the next file. Idempotency: the DELETE-WHERE step guarantees no duplicate nodes across re-runs.

- [ ] **Step 1: Write the failing ScipGraphRepository test**

Create `tests/integration/db/repositories/test_scip_graph_repo.py`:

```python
"""ScipGraphRepository — AGE writes/reads for `:Definition` + `:Reference` nodes.

The repository COMPOSES Plan 5's `GraphRepository` (at `codeoracle.db.repositories.graph`)
— there is no separate `codeoracle.db.age_driver` module; that name was a stale
sketch from an earlier draft (P0-13 / D-6.8). Plan 5's `GraphRepository.execute_cypher`
already owns the `SELECT * FROM cypher('codeoracle', $$...$$)` SQL wrapping, AGE
error translation (`GraphQueryError`), and the agtype-decode convention. This
class only owns the SCIP-specific schema (label namespace `:Definition` and
`:Reference`, property shape).

Tests use the testcontainers PG+AGE instance via the `pg_age_database` fixture.
"""

from __future__ import annotations

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from codeoracle.adapters.scip.parser import ScipDefinition, ScipReference
from codeoracle.db.repositories.scip_graph import ScipGraphRepository


@pytest_asyncio.fixture
async def session(async_session_factory: async_sessionmaker[AsyncSession]) -> AsyncSession:
    async with async_session_factory() as s:
        yield s


@pytest.mark.asyncio
async def test_replace_file_subgraph_inserts_nodes(session: AsyncSession) -> None:
    repo = ScipGraphRepository(session)
    defs = [
        ScipDefinition(
            file_path="src/codeoracle/example.py", fqn="`example.py`/foo()",
            line_number=5, end_line=5,
        ),
    ]
    refs = [
        ScipReference(
            file_path="src/codeoracle/example.py", fqn="`example.py`/foo()",
            line_number=12, end_line=12,
        ),
        ScipReference(
            file_path="src/codeoracle/example.py", fqn="`example.py`/foo()",
            line_number=18, end_line=18,
        ),
    ]
    await repo.replace_file_subgraph(
        snapshot_sha="s1",
        file_path="src/codeoracle/example.py",
        definitions=defs,
        references=refs,
    )
    await session.commit()

    assert await repo.count_definitions(snapshot_sha="s1", file_path="src/codeoracle/example.py") == 1
    assert await repo.count_references(snapshot_sha="s1", file_path="src/codeoracle/example.py") == 2


@pytest.mark.asyncio
async def test_replace_is_atomic_per_file(session: AsyncSession) -> None:
    """Second replace_file_subgraph for same (snapshot, file) MUST delete prior nodes."""
    repo = ScipGraphRepository(session)
    await repo.replace_file_subgraph(
        snapshot_sha="s2", file_path="a.py",
        definitions=[ScipDefinition(file_path="a.py", fqn="X", line_number=1, end_line=1)],
        references=[ScipReference(file_path="a.py", fqn="X", line_number=2, end_line=2)],
    )
    await session.commit()
    assert await repo.count_definitions(snapshot_sha="s2", file_path="a.py") == 1
    assert await repo.count_references(snapshot_sha="s2", file_path="a.py") == 1

    # Second call with different content.
    await repo.replace_file_subgraph(
        snapshot_sha="s2", file_path="a.py",
        definitions=[
            ScipDefinition(file_path="a.py", fqn="Y", line_number=10, end_line=10),
            ScipDefinition(file_path="a.py", fqn="Z", line_number=20, end_line=20),
        ],
        references=[],
    )
    await session.commit()
    assert await repo.count_definitions(snapshot_sha="s2", file_path="a.py") == 2
    assert await repo.count_references(snapshot_sha="s2", file_path="a.py") == 0


@pytest.mark.asyncio
async def test_replace_only_affects_target_file(session: AsyncSession) -> None:
    repo = ScipGraphRepository(session)
    await repo.replace_file_subgraph(
        snapshot_sha="s3", file_path="a.py",
        definitions=[ScipDefinition(file_path="a.py", fqn="X", line_number=1, end_line=1)],
        references=[],
    )
    await repo.replace_file_subgraph(
        snapshot_sha="s3", file_path="b.py",
        definitions=[ScipDefinition(file_path="b.py", fqn="X", line_number=1, end_line=1)],
        references=[],
    )
    await session.commit()

    # Re-replace a.py to empty; b.py must be preserved.
    await repo.replace_file_subgraph(
        snapshot_sha="s3", file_path="a.py", definitions=[], references=[],
    )
    await session.commit()

    assert await repo.count_definitions(snapshot_sha="s3", file_path="a.py") == 0
    assert await repo.count_definitions(snapshot_sha="s3", file_path="b.py") == 1


@pytest.mark.asyncio
async def test_replace_isolated_by_snapshot(session: AsyncSession) -> None:
    repo = ScipGraphRepository(session)
    await repo.replace_file_subgraph(
        snapshot_sha="s4-A", file_path="a.py",
        definitions=[ScipDefinition(file_path="a.py", fqn="X", line_number=1, end_line=1)],
        references=[],
    )
    await repo.replace_file_subgraph(
        snapshot_sha="s4-B", file_path="a.py",
        definitions=[
            ScipDefinition(file_path="a.py", fqn="X", line_number=1, end_line=1),
            ScipDefinition(file_path="a.py", fqn="Y", line_number=2, end_line=2),
        ],
        references=[],
    )
    await session.commit()
    assert await repo.count_definitions(snapshot_sha="s4-A", file_path="a.py") == 1
    assert await repo.count_definitions(snapshot_sha="s4-B", file_path="a.py") == 2
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/integration/db/repositories/test_scip_graph_repo.py -v`

Expected: FAIL — `scip_graph` module doesn't exist.

- [ ] **Step 3: Implement ScipGraphRepository**

Create `src/codeoracle/db/repositories/scip_graph.py`:

```python
"""ScipGraphRepository — `:Definition` + `:Reference` AGE writes.

Plan 6. The writer is `workers/scip_indexer` (project-mode worker). Reads are
done by Plan 9's verifier directly via openCypher queries.

Label namespace: `:Definition` and `:Reference`. These are disjoint from
Plan 5's Joern-side `:Symbol` namespace (architectural invariant 4). The
verifier in Plan 9 correlates `:Symbol{fqn}` (Joern) with `:Definition{fqn}`
(SCIP) by FQN equality — same value of `fqn` across labels indicates the
same symbol from two evidence sources.

Atomicity: `replace_file_subgraph` deletes ALL `:Definition`/`:Reference`
nodes for (snapshot_sha, file_path), then inserts the provided sets. The
session is NOT committed here — the worker commits per-file.

============================================================================
AGE EXECUTION + PARAMETER-BINDING NOTE (read before editing):
============================================================================

This repository COMPOSES Plan 5's `GraphRepository` (at
`codeoracle.db.repositories.graph`). All Cypher execution flows through
`GraphRepository.execute_cypher(cypher_body, *, column_spec=...)`, which
wraps the body in `SELECT * FROM cypher('codeoracle', $$...$$) AS (...)` and
translates AGE errors to `GraphQueryError`. There is NO separate
`codeoracle.db.age_driver` module — that name was a stale draft (P0-13 / D-6.8).

Apache AGE's Cypher body lives inside `$$...$$` dollar-quoted strings,
which means SQLAlchemy's `:foo` / `%s` parameter binding (which targets the
SQL layer) does NOT substitute into the Cypher body. AGE has a parametrized
`cypher('graph', $$body$$, $1)` form, but the param is an agtype map
referenced inside the body as a single bound name — usable, but Plan 5's
`GraphRepository.execute_cypher` doesn't currently pass values through to
the inner Cypher block as native Cypher parameters (its `params` argument
is a Postgres parameters map for the outer SQL).

To stay portable regardless of Plan 5's exact binding shape, this repository
uses VALIDATE-THEN-FORMAT: each value passed into a Cypher string is first
validated against a strict regex allowlist (`_SAFE_SHA_RE`, `_SAFE_PATH_RE`,
`_SAFE_FQN_RE`), then formatted into the string. Integer columns
(`line_start`/`line_end`) are validated as int and formatted as digits.

This pattern is safe BECAUSE all values originate from inside CodeOracle:
  - `snapshot_sha` comes from `IndexingService` (git SHA, already validated)
  - `file_path` comes from the snapshot manifest (path under repo_root)
  - `fqn` comes from `scip-python` output (validated by the parser)
  - line numbers are ints already

The validator regexes are deliberately tight — anything that doesn't match
raises `ValueError` rather than silently passing garbage to Cypher. If a
future plan adds untrusted user input to a Cypher write site, that plan
MUST switch to AGE's parametrized cypher() form (and add a regression test
that proves binding works through GraphRepository).
============================================================================
"""

from __future__ import annotations

import re

from sqlalchemy.ext.asyncio import AsyncSession

from codeoracle.adapters.scip.parser import ScipDefinition, ScipReference
from codeoracle.db.repositories.graph import GraphRepository

# Validation regexes for the validate-then-format Cypher-injection escape
# strategy. See the module docstring "AGE PARAMETER-BINDING NOTE" for why.
_SAFE_SHA_RE = re.compile(r"^[a-fA-F0-9_-]{1,128}$")  # git SHA or test sha
_SAFE_PATH_RE = re.compile(r"^[A-Za-z0-9_./-]{1,512}$")  # repo-relative paths
# FQN can contain language-syntax punctuation. Allow common scip-python forms:
# `module.py`/Class#method(). + Java-ish forms with $ and (. Forbid quote chars
# (', ") and Cypher meta (}, {) so injection into property literal is impossible.
_SAFE_FQN_RE = re.compile(r"^[A-Za-z0-9_./()$#\[\]@%`<>,:\s-]{1,1024}$")


def _safe_sha(s: str) -> str:
    if not _SAFE_SHA_RE.match(s):
        raise ValueError(f"unsafe snapshot_sha value for AGE Cypher: {s!r}")
    return s


def _safe_path(p: str) -> str:
    if not _SAFE_PATH_RE.match(p):
        raise ValueError(f"unsafe file_path value for AGE Cypher: {p!r}")
    return p


def _safe_fqn(f: str) -> str:
    if not _SAFE_FQN_RE.match(f):
        raise ValueError(f"unsafe fqn value for AGE Cypher: {f!r}")
    return f


def _safe_line(n: int) -> int:
    if not isinstance(n, int) or n < 0 or n > 10_000_000:
        raise ValueError(f"unsafe line number for AGE Cypher: {n!r}")
    return n


class ScipGraphRepository:
    """SCIP-side façade over Plan 5's `GraphRepository`.

    Composes (does not extend) `GraphRepository` — the caller passes a
    session; this class constructs its own `GraphRepository(session)` and
    delegates all Cypher execution to `execute_cypher`. The session is
    owned by the caller; both repositories share it so a single transaction
    wraps SCIP writes + the worker's barrier upsert.
    """

    def __init__(self, session: AsyncSession) -> None:
        self._graph = GraphRepository(session)

    async def replace_file_subgraph(
        self,
        *,
        snapshot_sha: str,
        file_path: str,
        definitions: list[ScipDefinition],
        references: list[ScipReference],
    ) -> None:
        """Atomically replace all SCIP nodes for (snapshot_sha, file_path).

        Empty `definitions`/`references` lists are legal (pure delete).
        """
        sha_s = _safe_sha(snapshot_sha)
        path_s = _safe_path(file_path)

        # Delete existing nodes for this (snapshot, file). Note AGE Cypher
        # does NOT support `OR` between label tests in the WHERE clause the
        # way openCypher does (`n:Definition OR n:Reference`). We issue two
        # separate DETACH DELETE statements instead.
        await self._graph.execute_cypher(
            f"""
            MATCH (n:Definition)
            WHERE n.snapshot_sha = '{sha_s}' AND n.file_path = '{path_s}'
            DETACH DELETE n
            """,
        )
        await self._graph.execute_cypher(
            f"""
            MATCH (n:Reference)
            WHERE n.snapshot_sha = '{sha_s}' AND n.file_path = '{path_s}'
            DETACH DELETE n
            """,
        )

        # Insert new definitions.
        #
        # IMPORTANT: we store `path_s` (the outer file_path argument, which
        # came from message.files — canonical relative paths) on EVERY node,
        # NOT `d.file_path` from the ScipDefinition record. SCIP-python may
        # emit absolute paths or paths prefixed with `src/codeoracle/` etc., and
        # `tasks.py` already grouped definitions by their normalized path
        # via _normalize_scip_path_to_message_file. The grouping contract
        # is "all elements in this list belong to file_path" — so reusing
        # `path_s` is correct and keeps the storage key consistent with the
        # DELETE above and the count_definitions() lookup below. Re-validating
        # `d.file_path` per element here would (a) re-introduce the mismatch
        # the normalizer was added to fix, and (b) reject SCIP-emitted paths
        # whose shape doesn't match _SAFE_PATH_RE.
        for d in definitions:
            fqn_s = _safe_fqn(d.fqn)
            line_start = _safe_line(d.line_number)
            line_end = _safe_line(d.end_line)
            await self._graph.execute_cypher(
                f"""
                CREATE (n:Definition {{
                    fqn: '{fqn_s}',
                    snapshot_sha: '{sha_s}',
                    file_path: '{path_s}',
                    line_start: {line_start},
                    line_end: {line_end}
                }})
                """,
            )

        # Insert new references — same path-canonicalization rationale as
        # the definitions block above; use `path_s`, not `r.file_path`.
        for r in references:
            fqn_s = _safe_fqn(r.fqn)
            line_start = _safe_line(r.line_number)
            line_end = _safe_line(r.end_line)
            await self._graph.execute_cypher(
                f"""
                CREATE (n:Reference {{
                    fqn: '{fqn_s}',
                    snapshot_sha: '{sha_s}',
                    file_path: '{path_s}',
                    line_start: {line_start},
                    line_end: {line_end}
                }})
                """,
            )

    async def count_definitions(self, *, snapshot_sha: str, file_path: str) -> int:
        sha_s = _safe_sha(snapshot_sha)
        path_s = _safe_path(file_path)
        rows = await self._graph.execute_cypher(
            f"""
            MATCH (n:Definition)
            WHERE n.snapshot_sha = '{sha_s}' AND n.file_path = '{path_s}'
            RETURN count(n) AS c
            """,
            column_spec="c agtype",
        )
        # AGE returns the agtype-encoded count as a string; the
        # str(...).strip('"') is defensive against both `5` and `"5"` shapes
        # — same pattern as Plan 5's GraphRepository.find_callers.
        return int(str(rows[0]["c"]).strip('"'))

    async def count_references(self, *, snapshot_sha: str, file_path: str) -> int:
        sha_s = _safe_sha(snapshot_sha)
        path_s = _safe_path(file_path)
        rows = await self._graph.execute_cypher(
            f"""
            MATCH (n:Reference)
            WHERE n.snapshot_sha = '{sha_s}' AND n.file_path = '{path_s}'
            RETURN count(n) AS c
            """,
            column_spec="c agtype",
        )
        return int(str(rows[0]["c"]).strip('"'))
```

The class is deliberately thin: SCIP-shape values get validated through
`_safe_*` helpers (above), then handed to `GraphRepository.execute_cypher`.
There is no private `_cypher` / `_cypher_returning` plumbing here because
Plan 5's `execute_cypher` already provides exactly that surface — duplicating
it would re-introduce the very layering confusion that the `age_driver`
draft caused.

- [ ] **Step 4: Run repository test to verify it passes**

Run: `uv run pytest tests/integration/db/repositories/test_scip_graph_repo.py -v`

Expected: PASS (4 tests).

- [ ] **Step 5: Write the SCIP indexer message test**

Create `tests/unit/workers/scip_indexer/test_scip_indexer_message.py`:

```python
"""ScipIndexerMessage — Pydantic model, project-mode shape."""

import pytest
from pydantic import ValidationError

from codeoracle.workers.scip_indexer.message import ScipIndexerMessage


def test_required_fields() -> None:
    msg = ScipIndexerMessage(
        batch_id="sha-x",
        snapshot_sha="sha-x",
        repo_root="/tmp/repo",
        files=["a.py", "b.py"],
    )
    assert msg.snapshot_sha == "sha-x"
    assert msg.batch_id == "sha-x"
    assert msg.files == ["a.py", "b.py"]


def test_batch_id_must_equal_snapshot_sha() -> None:
    """ScipIndexerMessage enforces batch_id == snapshot_sha so the barrier
    invariant ('batch_id is the polling key') is statically checkable."""
    with pytest.raises(ValidationError):
        ScipIndexerMessage(
            batch_id="different",
            snapshot_sha="sha-x",
            repo_root="/tmp/repo",
            files=["a.py"],
        )


def test_empty_files_list_is_legal() -> None:
    """A snapshot with no Python files (none for SCIP to index) is legal —
    the worker still must write zero outcome rows and ACK the message."""
    msg = ScipIndexerMessage(
        batch_id="sha-x",
        snapshot_sha="sha-x",
        repo_root="/tmp/repo",
        files=[],
    )
    assert msg.files == []


def test_files_required() -> None:
    """`files` is required (not optional) — the message MUST carry the file
    list so the worker knows how many outcome rows to write."""
    with pytest.raises(ValidationError):
        ScipIndexerMessage(
            batch_id="sha-x",
            snapshot_sha="sha-x",
            repo_root="/tmp/repo",
        )  # type: ignore[call-arg]
```

- [ ] **Step 6: Implement ScipIndexerMessage**

Create `src/codeoracle/workers/scip_indexer/message.py`:

```python
"""ScipIndexerMessage — payload schema for `indexing.scip` stream.

Project-mode shape: ONE message per (snapshot, scip-indexer) carrying the
file list. The worker runs scip-python on `repo_root` and writes one outcome
row per file in `files`.
"""

from __future__ import annotations

from pydantic import field_validator

from codeoracle.workers._base.message import WorkerMessage


class ScipIndexerMessage(WorkerMessage):
    snapshot_sha: str
    repo_root: str
    files: list[str]

    @field_validator("snapshot_sha")
    @classmethod
    def _snapshot_sha_eq_batch_id(cls, v: str, info) -> str:
        # info.data has the already-validated fields.
        batch_id = info.data.get("batch_id")
        if batch_id is not None and batch_id != v:
            raise ValueError(
                f"snapshot_sha ({v!r}) must equal batch_id ({batch_id!r})"
            )
        return v
```

- [ ] **Step 7: Run message test to verify it passes**

Run: `uv run pytest tests/unit/workers/scip_indexer/test_scip_indexer_message.py -v`

Expected: PASS (4 tests).

- [ ] **Step 8: Write the `extract_project_to_graph` pure-function test**

Create `tests/integration/workers/scip_indexer/test_scip_indexer_tasks.py`:

```python
"""Pure-function tests for `extract_project_to_graph` — the worker's logic
factored out of the consumer so it can be unit-tested without Valkey.

The function takes injected adapters (ScipClient, ScipIndexParser,
ScipGraphRepository, SnapshotFileOutcomeRepository) and a message; runs the
project-mode pipeline; returns the per-file outcome status map.
"""

from __future__ import annotations

import pathlib
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from codeoracle.adapters.scip.parser import (
    ScipDefinition,
    ScipIndexParser,
    ScipParseResult,
    ScipReference,
)
from codeoracle.core.exceptions import ScipExtractionError
from codeoracle.db.repositories.scip_graph import ScipGraphRepository
from codeoracle.db.repositories.snapshot_file_outcome import SnapshotFileOutcomeRepository
from codeoracle.workers.scip_indexer.message import ScipIndexerMessage
from codeoracle.workers.scip_indexer.tasks import extract_project_to_graph


@pytest_asyncio.fixture
async def session(
    async_session_factory: async_sessionmaker[AsyncSession],
) -> AsyncSession:
    async with async_session_factory() as s:
        yield s


@pytest.mark.asyncio
async def test_writes_outcomes_for_all_files_in_message(
    session: AsyncSession, tmp_path: pathlib.Path,
) -> None:
    """Every file in `message.files` MUST receive an outcome row, regardless
    of SCIP coverage (architectural invariant 2)."""
    msg = ScipIndexerMessage(
        batch_id="sha-cov",
        snapshot_sha="sha-cov",
        repo_root=str(tmp_path),
        files=["a.py", "b.py", "c.py"],  # SCIP will only find symbols in a.py
    )

    # Fake adapters.
    scip_client = MagicMock()
    scip_client.index_project = AsyncMock(return_value=None)
    scip_client.print_index = AsyncMock(return_value="<text>")

    parser = MagicMock(spec=ScipIndexParser)
    parser.parse = MagicMock(return_value=ScipParseResult(
        definitions=[
            ScipDefinition(file_path="a.py", fqn="`a.py`/foo()", line_number=5, end_line=5),
        ],
        references=[],
        files_covered={"a.py"},  # b.py, c.py not in covered set
    ))

    graph_repo = ScipGraphRepository(session)
    outcome_repo = SnapshotFileOutcomeRepository(session)

    statuses = await extract_project_to_graph(
        message=msg,
        scip_client=scip_client,
        parser=parser,
        scip_graph_repo=graph_repo,
        outcome_repo=outcome_repo,
        session=session,
    )

    assert statuses == {"a.py": "success", "b.py": "success", "c.py": "success"}

    # The outcome rows for ALL three files exist.
    completed_count = await outcome_repo.count_completed(snapshot_sha="sha-cov", extractor="scip")
    assert completed_count == 3


@pytest.mark.asyncio
async def test_scip_failure_marks_all_files_failed(
    session: AsyncSession, tmp_path: pathlib.Path,
) -> None:
    """Architectural invariant 3: project-level failure → all-file failure."""
    msg = ScipIndexerMessage(
        batch_id="sha-fail",
        snapshot_sha="sha-fail",
        repo_root=str(tmp_path),
        files=["a.py", "b.py"],
    )

    scip_client = MagicMock()
    scip_client.index_project = AsyncMock(side_effect=ScipExtractionError("scip blew up"))
    scip_client.print_index = AsyncMock(return_value="")

    parser = MagicMock(spec=ScipIndexParser)
    graph_repo = ScipGraphRepository(session)
    outcome_repo = SnapshotFileOutcomeRepository(session)

    statuses = await extract_project_to_graph(
        message=msg,
        scip_client=scip_client,
        parser=parser,
        scip_graph_repo=graph_repo,
        outcome_repo=outcome_repo,
        session=session,
    )
    assert statuses == {"a.py": "failed", "b.py": "failed"}

    failed_count = await outcome_repo.count_failed(snapshot_sha="sha-fail", extractor="scip")
    assert failed_count == 2


@pytest.mark.asyncio
async def test_parse_failure_marks_all_files_failed(
    session: AsyncSession, tmp_path: pathlib.Path,
) -> None:
    from codeoracle.adapters.scip.parser import ScipParseError

    msg = ScipIndexerMessage(
        batch_id="sha-parsefail",
        snapshot_sha="sha-parsefail",
        repo_root=str(tmp_path),
        files=["a.py"],
    )

    scip_client = MagicMock()
    scip_client.index_project = AsyncMock(return_value=None)
    scip_client.print_index = AsyncMock(return_value="garbage")

    parser = MagicMock(spec=ScipIndexParser)
    parser.parse = MagicMock(side_effect=ScipParseError("bad output"))

    graph_repo = ScipGraphRepository(session)
    outcome_repo = SnapshotFileOutcomeRepository(session)

    statuses = await extract_project_to_graph(
        message=msg,
        scip_client=scip_client,
        parser=parser,
        scip_graph_repo=graph_repo,
        outcome_repo=outcome_repo,
        session=session,
    )
    assert statuses == {"a.py": "failed"}


@pytest.mark.asyncio
async def test_scip_emits_divergent_paths_still_matches_message_files(
    session: AsyncSession, tmp_path: pathlib.Path,
) -> None:
    """SCIP-python may emit paths that don't byte-identically match the
    `message.files` strings — e.g., SCIP outputs `src/codeoracle/example.py`
    while the orchestrator dispatched `files=["example.py"]` (or vice versa,
    or with a leading "./" prefix). The worker MUST normalize and still
    write the SCIP graph to the corresponding message_file slot.

    Without this normalization, the symptom is silent: outcome=success rows
    land for every file but `count_definitions` returns 0 because the path
    lookup missed.
    """
    msg = ScipIndexerMessage(
        batch_id="sha-pathnorm",
        snapshot_sha="sha-pathnorm",
        repo_root=str(tmp_path),
        files=["example.py"],  # orchestrator dispatched short path
    )

    scip_client = MagicMock()
    scip_client.index_project = AsyncMock(return_value=None)
    scip_client.print_index = AsyncMock(return_value="<text>")

    # SCIP-python emits the LONGER path with subdirectory prefix.
    parser = MagicMock(spec=ScipIndexParser)
    parser.parse = MagicMock(return_value=ScipParseResult(
        definitions=[
            ScipDefinition(
                file_path="src/codeoracle/example.py",  # NOT byte-identical to message.files
                fqn="`example.py`/foo()",
                line_number=5, end_line=5,
            ),
        ],
        references=[],
        files_covered={"src/codeoracle/example.py"},
    ))

    graph_repo = ScipGraphRepository(session)
    outcome_repo = SnapshotFileOutcomeRepository(session)

    statuses = await extract_project_to_graph(
        message=msg,
        scip_client=scip_client,
        parser=parser,
        scip_graph_repo=graph_repo,
        outcome_repo=outcome_repo,
        session=session,
    )
    assert statuses == {"example.py": "success"}
    # CRITICAL ASSERTION: the symbol actually landed in the graph, keyed
    # under "example.py" (the message.files path), not "src/codeoracle/example.py".
    assert await graph_repo.count_definitions(
        snapshot_sha="sha-pathnorm", file_path="example.py",
    ) == 1


@pytest.mark.asyncio
async def test_scip_emits_absolute_path_still_matches(
    session: AsyncSession, tmp_path: pathlib.Path,
) -> None:
    """SCIP may emit absolute paths (`/repo/example.py`); the normalizer
    strips `repo_root` and matches."""
    msg = ScipIndexerMessage(
        batch_id="sha-abspath",
        snapshot_sha="sha-abspath",
        repo_root="/repo",
        files=["example.py"],
    )

    scip_client = MagicMock()
    scip_client.index_project = AsyncMock(return_value=None)
    scip_client.print_index = AsyncMock(return_value="<text>")

    parser = MagicMock(spec=ScipIndexParser)
    parser.parse = MagicMock(return_value=ScipParseResult(
        definitions=[
            ScipDefinition(
                file_path="/repo/example.py",  # absolute
                fqn="X",
                line_number=1, end_line=1,
            ),
        ],
        references=[],
        files_covered={"/repo/example.py"},
    ))

    graph_repo = ScipGraphRepository(session)
    outcome_repo = SnapshotFileOutcomeRepository(session)

    statuses = await extract_project_to_graph(
        message=msg,
        scip_client=scip_client,
        parser=parser,
        scip_graph_repo=graph_repo,
        outcome_repo=outcome_repo,
        session=session,
    )
    assert statuses == {"example.py": "success"}
    assert await graph_repo.count_definitions(
        snapshot_sha="sha-abspath", file_path="example.py",
    ) == 1


@pytest.mark.asyncio
async def test_empty_files_list_writes_no_outcomes(
    session: AsyncSession, tmp_path: pathlib.Path,
) -> None:
    """Edge case: no Python files in the snapshot. Worker must NOT raise;
    must NOT block scip-python. Returns empty status dict."""
    msg = ScipIndexerMessage(
        batch_id="sha-empty",
        snapshot_sha="sha-empty",
        repo_root=str(tmp_path),
        files=[],
    )

    scip_client = MagicMock()
    scip_client.index_project = AsyncMock(return_value=None)
    scip_client.print_index = AsyncMock(return_value="")

    parser = MagicMock(spec=ScipIndexParser)
    parser.parse = MagicMock(return_value=ScipParseResult())

    graph_repo = ScipGraphRepository(session)
    outcome_repo = SnapshotFileOutcomeRepository(session)

    statuses = await extract_project_to_graph(
        message=msg,
        scip_client=scip_client,
        parser=parser,
        scip_graph_repo=graph_repo,
        outcome_repo=outcome_repo,
        session=session,
    )
    assert statuses == {}
```

- [ ] **Step 9: Implement `extract_project_to_graph`**

Create `src/codeoracle/workers/scip_indexer/tasks.py`:

```python
"""Pure-function pipeline for project-mode SCIP extraction.

The consumer's `handle()` method delegates to this function. Factoring it
out lets the function be unit-tested with mocked adapters (no Valkey, no
real subprocess).

Returns a per-file outcome status dict: {file_path: "success" | "failed"}.

Architectural invariants enforced here:
- One outcome row per file in `message.files` (invariant 2).
- Project-level failure → all-file failure (invariant 3).
- Per-file atomicity for the graph + outcome writes (invariant 5).
"""

from __future__ import annotations

import logging
import tempfile
import pathlib

from sqlalchemy.ext.asyncio import AsyncSession

from codeoracle.adapters.scip.client import ScipClient
from codeoracle.adapters.scip.parser import ScipIndexParser, ScipParseError
from codeoracle.core.exceptions import ScipExtractionError
from codeoracle.db.repositories.scip_graph import ScipGraphRepository
from codeoracle.db.repositories.snapshot_file_outcome import (
    SnapshotFileOutcomeRepository,
    SnapshotFileOutcomeRow,
)
from codeoracle.workers.scip_indexer.message import ScipIndexerMessage


_LOG = logging.getLogger(__name__)


async def extract_project_to_graph(
    *,
    message: ScipIndexerMessage,
    scip_client: ScipClient,
    parser: ScipIndexParser,
    scip_graph_repo: ScipGraphRepository,
    outcome_repo: SnapshotFileOutcomeRepository,
    session: AsyncSession,
) -> dict[str, str]:
    """Run SCIP on the project, write per-file subgraphs + outcomes.

    The `session` parameter is the worker's outer session — it is COMMITTED
    incrementally per-file, then handed back. The function does NOT close it.
    """
    if not message.files:
        return {}

    # 1. Run scip-python + scip print. On failure, mark all files failed.
    try:
        with tempfile.TemporaryDirectory(prefix="scip-") as tmpdir:
            output_path = str(pathlib.Path(tmpdir) / "index.scip")
            await scip_client.index_project(
                project_dir=message.repo_root,
                project_name="codeoracle",  # arbitrary; SCIP uses for symbol prefixing
                output_path=output_path,
            )
            scip_text = await scip_client.print_index(scip_file=output_path)
    except ScipExtractionError as e:
        _LOG.error("scip extraction failed; marking all files failed", exc_info=e)
        return await _mark_all_failed(
            message=message,
            outcome_repo=outcome_repo,
            session=session,
            error_message=f"scip extraction: {str(e)[:512]}",
        )

    # 2. Parse output. Parse failure is project-wide.
    try:
        result = parser.parse(scip_text)
    except ScipParseError as e:
        _LOG.error("scip output unparsable; marking all files failed", exc_info=e)
        return await _mark_all_failed(
            message=message,
            outcome_repo=outcome_repo,
            session=session,
            error_message=f"scip parse: {str(e)[:512]}",
        )

    # 3. NORMALIZE SCIP-emitted file paths to message.files entries, then
    #    group. SCIP-python emits file_paths relative to its project root;
    #    those may NOT be byte-identical to message.files entries (leading
    #    "./", different intermediate path prefix, abs path). If we skipped
    #    normalization, `defs_by_file.get(file_path, [])` would silently
    #    return [] for every file and write outcome=success rows with an
    #    empty graph — a true silent-failure mode.
    defs_by_file: dict[str, list] = {}
    for d in result.definitions:
        matched = _normalize_scip_path_to_message_file(
            d.file_path, message.files, message.repo_root,
        )
        if matched is not None:
            defs_by_file.setdefault(matched, []).append(d)
    refs_by_file: dict[str, list] = {}
    for r in result.references:
        matched = _normalize_scip_path_to_message_file(
            r.file_path, message.files, message.repo_root,
        )
        if matched is not None:
            refs_by_file.setdefault(matched, []).append(r)

    # 4. Loop files. For each file: one transaction.
    statuses: dict[str, str] = {}
    for file_path in message.files:
        defs = defs_by_file.get(file_path, [])
        refs = refs_by_file.get(file_path, [])
        try:
            await scip_graph_repo.replace_file_subgraph(
                snapshot_sha=message.snapshot_sha,
                file_path=file_path,
                definitions=defs,
                references=refs,
            )
            await outcome_repo.upsert(
                SnapshotFileOutcomeRow(
                    snapshot_sha=message.snapshot_sha,
                    file_path=file_path,
                    extractor="scip",
                    outcome="success",
                    error_message=None,
                )
            )
            await session.commit()
            statuses[file_path] = "success"
        except Exception as e:
            # Per-file write failure (graph error, PG error, etc.) — log + mark
            # this file failed; continue with remaining files.
            _LOG.exception("per-file SCIP write failed for %s", file_path)
            await session.rollback()
            await outcome_repo.upsert(
                SnapshotFileOutcomeRow(
                    snapshot_sha=message.snapshot_sha,
                    file_path=file_path,
                    extractor="scip",
                    outcome="failed",
                    error_message=f"per-file scip write: {str(e)[:512]}",
                )
            )
            await session.commit()
            statuses[file_path] = "failed"

    return statuses


async def _mark_all_failed(
    *,
    message: ScipIndexerMessage,
    outcome_repo: SnapshotFileOutcomeRepository,
    session: AsyncSession,
    error_message: str,
) -> dict[str, str]:
    """Write `outcome=failed` rows for every file in `message.files`."""
    for file_path in message.files:
        await outcome_repo.upsert(
            SnapshotFileOutcomeRow(
                snapshot_sha=message.snapshot_sha,
                file_path=file_path,
                extractor="scip",
                outcome="failed",
                error_message=error_message,
            )
        )
    await session.commit()
    return {f: "failed" for f in message.files}


def _normalize_scip_path_to_message_file(
    scip_path: str, message_files: list[str], repo_root: str
) -> str | None:
    """Map a SCIP-emitted file path to one of `message_files` (or None if no match).

    Why this exists: SCIP-python emits paths relative to whatever it considers
    the project root. `message.files` contains paths relative to `repo_root`.
    They are NOT guaranteed byte-identical. Without normalization, the
    `defs_by_file.get(file_path, [])` lookup at the per-file loop returns []
    for every file, silently dropping all symbols while the barrier closes
    with outcome=success — a true silent-failure mode.

    Match strategy (in order):
      1. Strip leading `repo_root/` (absolute-path emission).
      2. Strip leading `./`.
      3. Exact match in `message_files`? Return it.
      4. Suffix match: any `message_file` is the trailing component of
         `scip_path` (or vice versa)? Return that.
      5. Otherwise None — symbol is for a file outside the requested set
         (build artifact, third-party module SCIP traversed). Drop silently.
    """
    import os
    p = scip_path
    # Strip absolute repo_root prefix in either separator.
    for sep in (os.sep, "/"):
        prefix = repo_root.rstrip(sep) + sep
        if p.startswith(prefix):
            p = p[len(prefix) :]
            break
    # Strip leading "./".
    if p.startswith("./"):
        p = p[2:]
    if p in message_files:
        return p
    # Suffix-match either direction.
    for mf in message_files:
        if p.endswith("/" + mf) or mf.endswith("/" + p):
            return mf
    return None
```

- [ ] **Step 10: Run tasks test to verify it passes**

Run: `uv run pytest tests/integration/workers/scip_indexer/test_scip_indexer_tasks.py -v`

Expected: PASS (6 tests — 4 pipeline behaviors + 2 path-normalization tests).

- [ ] **Step 11: Write the consumer test**

Create `tests/integration/workers/scip_indexer/test_scip_indexer_consumer.py`:

```python
"""ScipIndexerConsumer — handle() integration.

These tests verify the consumer's wiring (it constructs a session from the
factory, calls extract_project_to_graph, returns without raising) rather
than re-testing extract_project_to_graph itself (already covered).
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from codeoracle.adapters.scip.client import ScipClient
from codeoracle.adapters.scip.parser import ScipIndexParser, ScipParseResult
from codeoracle.workers.scip_indexer.consumer import ScipIndexerConsumer
from codeoracle.workers.scip_indexer.message import ScipIndexerMessage


@pytest.mark.asyncio
async def test_handle_invokes_pipeline_and_commits(
    async_session_factory: async_sessionmaker[AsyncSession],
    valkey_queue_client,  # Plan 5 fixture
) -> None:
    scip_client = MagicMock(spec=ScipClient)
    scip_client.index_project = AsyncMock(return_value=None)
    scip_client.print_index = AsyncMock(return_value="")
    parser = MagicMock(spec=ScipIndexParser)
    parser.parse = MagicMock(return_value=ScipParseResult())

    from codeoracle.workers._base.message import RetryPolicy

    import asyncio

    consumer = ScipIndexerConsumer(
        queue_client=valkey_queue_client,
        retry_policy=RetryPolicy(
            max_attempts=2,
        ),
        consumer_name="scip-test",
        stop_event=asyncio.Event(),
        scip_client=scip_client,
        parser=parser,
        db_session_factory=async_session_factory,
    )

    msg = ScipIndexerMessage(
        batch_id="sha-handle",
        snapshot_sha="sha-handle",
        repo_root="/tmp/repo",
        files=["a.py"],
    )
    # Direct handle() invocation — not through poll loop.
    await consumer.handle(msg)

    # Verify scip_client was called (the pipeline ran).
    scip_client.index_project.assert_awaited_once()
    scip_client.print_index.assert_awaited_once()
    parser.parse.assert_called_once()
```

- [ ] **Step 12: Implement the consumer**

Create `src/codeoracle/workers/scip_indexer/consumer.py`:

```python
"""ScipIndexerConsumer — project-mode WorkerConsumer.

Stream: indexing.scip
Group:  scip-indexer
count_per_poll = 1 (project messages are slow; running multiple in parallel
risks Valkey connection idle timeouts and double-running scip-python on
the same project).
"""

from __future__ import annotations

import asyncio

from sqlalchemy.ext.asyncio import async_sessionmaker, AsyncSession

from codeoracle.adapters.scip.client import ScipClient
from codeoracle.adapters.scip.parser import ScipIndexParser
from codeoracle.adapters.valkey.client import ValkeyQueueClient
from codeoracle.db.repositories.scip_graph import ScipGraphRepository
from codeoracle.db.repositories.snapshot_file_outcome import SnapshotFileOutcomeRepository
from codeoracle.workers._base.consumer import WorkerConsumer
from codeoracle.workers._base.message import RetryPolicy
from codeoracle.workers.scip_indexer.message import ScipIndexerMessage
from codeoracle.workers.scip_indexer.tasks import extract_project_to_graph
from codeoracle.core.streams import GROUP_SCIP_INDEXER, STREAM_INDEXING_SCIP


class ScipIndexerConsumer(WorkerConsumer[ScipIndexerMessage]):
    stream = STREAM_INDEXING_SCIP
    group = GROUP_SCIP_INDEXER
    message_type = ScipIndexerMessage
    count_per_poll = 1
    block_ms = 5000

    def __init__(
        self,
        *,
        queue_client: ValkeyQueueClient,
        retry_policy: RetryPolicy,
        consumer_name: str,
        stop_event: asyncio.Event,
        scip_client: ScipClient,
        parser: ScipIndexParser,
        db_session_factory: async_sessionmaker[AsyncSession],
    ) -> None:
        super().__init__(
            queue_client=queue_client,
            retry_policy=retry_policy,
            consumer_name=consumer_name,
            stop_event=stop_event,
            # reclaim_min_idle_ms intentionally omitted → the inherited reaper
            # stays OFF for this stream. The scip handle is now bounded by the
            # ScipClient index/print timeouts (default 300s + 60s), so the
            # double-run hazard that previously gated reclaim is gone. Turning
            # reclaim ON is a deferred follow-up: size reclaim_min_idle_ms off
            # the worst-case handle (index + print) while keeping
            # 2 * worst_case + one_handle < the orchestrator completion barrier
            # (600s, Plan 5).
        )
        self._scip_client = scip_client
        self._parser = parser
        self._db_factory = db_session_factory

    async def handle(self, message: ScipIndexerMessage) -> None:
        async with self._db_factory() as session:
            scip_graph_repo = ScipGraphRepository(session)
            outcome_repo = SnapshotFileOutcomeRepository(session)
            await extract_project_to_graph(
                message=message,
                scip_client=self._scip_client,
                parser=self._parser,
                scip_graph_repo=scip_graph_repo,
                outcome_repo=outcome_repo,
                session=session,
            )
```

- [ ] **Step 13: Implement the worker entrypoint**

Create `src/codeoracle/workers/scip_indexer/__main__.py`:

```python
"""SCIP indexer worker entrypoint — `python -m codeoracle.workers.scip_indexer`."""

from __future__ import annotations

import asyncio
import logging
import signal

from codeoracle.adapters.scip.client import ScipClient
from codeoracle.adapters.scip.parser import ScipIndexParser
from codeoracle.adapters.valkey.client import ValkeyQueueClient
from codeoracle.core.config import Settings
from codeoracle.db.session import make_async_engine, make_async_session_factory
from codeoracle.workers._base.message import RetryPolicy
from codeoracle.workers.scip_indexer.consumer import ScipIndexerConsumer


_LOG = logging.getLogger(__name__)


async def _main() -> None:
    logging.basicConfig(level=logging.INFO)
    settings = Settings()
    queue_client = ValkeyQueueClient.from_settings(settings)
    # Plan 3 / Plan 5 contract: make_async_engine() reads Settings internally
    # via get_settings(); make_async_session_factory(engine) binds the factory.
    engine = make_async_engine()
    db_factory = make_async_session_factory(engine)

    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop_event.set)

    consumer = ScipIndexerConsumer(
        queue_client=queue_client,
        retry_policy=RetryPolicy(
            max_attempts=2,
        ),
        consumer_name=f"scip-{settings.worker_instance_id}",
        stop_event=stop_event,
        scip_client=ScipClient(),
        parser=ScipIndexParser(),
        db_session_factory=db_factory,
    )

    _LOG.info("scip-indexer worker starting")
    await consumer.run()
    _LOG.info("scip-indexer worker stopped")


if __name__ == "__main__":
    asyncio.run(_main())
```

- [ ] **Step 14: Create __init__.py**

Create `src/codeoracle/workers/scip_indexer/__init__.py`:

```python
"""SCIP indexer worker — project-mode extraction.

Consumes `indexing.scip` stream; one message per snapshot reindex; writes
N `SnapshotFileOutcome` rows + N `:Definition`/`:Reference` subgraphs to AGE.
"""

from codeoracle.workers.scip_indexer.consumer import ScipIndexerConsumer
from codeoracle.workers.scip_indexer.message import ScipIndexerMessage

__all__ = ["ScipIndexerConsumer", "ScipIndexerMessage"]
```

- [ ] **Step 15: Run all SCIP indexer tests**

Run: `uv run pytest tests/unit/workers/scip_indexer/ tests/integration/workers/scip_indexer/ tests/integration/db/repositories/test_scip_graph_repo.py -v`

Expected: PASS.

- [ ] **Step 16: Commit**

```bash
git add tests/integration/db/repositories/test_scip_graph_repo.py src/codeoracle/db/repositories/scip_graph.py \
        tests/unit/workers/scip_indexer/ tests/integration/workers/scip_indexer/ src/codeoracle/workers/scip_indexer/
git commit -m "feat(workers): scip-indexer (project-mode) + ScipGraphRepository (Plan 6 Task 10)"
```

---

### Task 11: OpenGrep Runner Worker (per-file)

**Files:**
- Create: `src/codeoracle/workers/opengrep_runner/__init__.py`
- Create: `src/codeoracle/workers/opengrep_runner/message.py`
- Create: `src/codeoracle/workers/opengrep_runner/tasks.py` (pure function `extract_file_to_references`)
- Create: `src/codeoracle/workers/opengrep_runner/consumer.py`
- Create: `src/codeoracle/workers/opengrep_runner/__main__.py`
- Test: `tests/unit/workers/opengrep_runner/test_opengrep_runner_message.py`
- Test: `tests/unit/workers/opengrep_runner/test_opengrep_runner_tasks.py`
- Test: `tests/unit/workers/opengrep_runner/test_opengrep_runner_consumer.py`

**Per-file pattern (template from Plan 5's `joern_indexer/`):**

1. Message carries `(batch_id, snapshot_sha, repo_root, file_path)` — same shape as JoernIndexerMessage from Plan 5.
2. Consumer subclasses `WorkerConsumer[OpengrepRunnerMessage]`; ClassVars `stream`, `group`, `message_type`.
3. `handle()` opens a session via factory, instantiates repos, calls `extract_file_to_references(...)`, lets the session context manager close.
4. Errors:
   - `OpengrepRunError` (binary missing, exit non-zero, malformed JSON) → terminal → outcome=failed → DLQ.
   - `TransientWorkerError` (PG connection lost mid-write) → WorkerConsumer retries.
   - File doesn't exist → terminal → outcome=failed (write the row even on failure).

- [ ] **Step 1: Write the failing message test**

Create `tests/unit/workers/opengrep_runner/test_opengrep_runner_message.py`:

```python
"""OpengrepRunnerMessage — Pydantic model, per-file shape (same as Joern's)."""

import pytest
from pydantic import ValidationError

from codeoracle.workers.opengrep_runner.message import OpengrepRunnerMessage


def test_required_fields() -> None:
    msg = OpengrepRunnerMessage(
        batch_id="sha-x",
        snapshot_sha="sha-x",
        repo_root="/tmp/repo",
        file_path="a.py",
    )
    assert msg.snapshot_sha == "sha-x"
    assert msg.file_path == "a.py"
    assert msg.batch_id == msg.snapshot_sha


def test_batch_id_must_equal_snapshot_sha() -> None:
    with pytest.raises(ValidationError):
        OpengrepRunnerMessage(
            batch_id="other",
            snapshot_sha="sha-x",
            repo_root="/tmp/repo",
            file_path="a.py",
        )


def test_file_path_required() -> None:
    with pytest.raises(ValidationError):
        OpengrepRunnerMessage(
            batch_id="sha-x",
            snapshot_sha="sha-x",
            repo_root="/tmp/repo",
        )  # type: ignore[call-arg]
```

- [ ] **Step 2: Implement the message**

Create `src/codeoracle/workers/opengrep_runner/message.py`:

```python
"""OpengrepRunnerMessage — payload schema for `indexing.opengrep` stream.

Per-file shape: ONE message per (snapshot, opengrep, file) carrying just
the file path. The worker reads the file content via the file system,
runs opengrep against the CodeOracle rule pack, and writes one CodeReferences
row set + one outcome row.
"""

from __future__ import annotations

from pydantic import field_validator

from codeoracle.workers._base.message import WorkerMessage


class OpengrepRunnerMessage(WorkerMessage):
    snapshot_sha: str
    repo_root: str
    file_path: str

    @field_validator("snapshot_sha")
    @classmethod
    def _snapshot_sha_eq_batch_id(cls, v: str, info) -> str:
        batch_id = info.data.get("batch_id")
        if batch_id is not None and batch_id != v:
            raise ValueError(f"snapshot_sha ({v!r}) must equal batch_id ({batch_id!r})")
        return v
```

- [ ] **Step 3: Run message test to verify it passes**

Run: `uv run pytest tests/unit/workers/opengrep_runner/test_opengrep_runner_message.py -v`

Expected: PASS (3 tests).

- [ ] **Step 4: Write the failing tasks test**

Create `tests/unit/workers/opengrep_runner/test_opengrep_runner_tasks.py`:

```python
"""Pure-function tests for `extract_file_to_references`."""

from __future__ import annotations

import pathlib
from unittest.mock import AsyncMock, MagicMock

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from codeoracle.adapters.opengrep.runner import OpengrepMatch, OpengrepRunner
from codeoracle.core.exceptions import OpengrepRunError
from codeoracle.db.repositories.code_references import CodeReferencesRepository
from codeoracle.db.repositories.snapshot_file_outcome import SnapshotFileOutcomeRepository
from codeoracle.workers.opengrep_runner.message import OpengrepRunnerMessage
from codeoracle.workers.opengrep_runner.tasks import extract_file_to_references


@pytest_asyncio.fixture
async def session(
    async_session_factory: async_sessionmaker[AsyncSession],
) -> AsyncSession:
    async with async_session_factory() as s:
        yield s


@pytest.mark.asyncio
async def test_success_writes_references_and_outcome(session: AsyncSession) -> None:
    msg = OpengrepRunnerMessage(
        batch_id="sha-og-ok",
        snapshot_sha="sha-og-ok",
        repo_root="/tmp",
        file_path="a.py",
    )
    runner = MagicMock(spec=OpengrepRunner)
    runner.scan_file = AsyncMock(return_value=[
        OpengrepMatch(file_path="a.py", symbol_name="foo", line_number=10, kind="call"),
        OpengrepMatch(file_path="a.py", symbol_name="foo", line_number=20, kind="call"),
        OpengrepMatch(file_path="a.py", symbol_name="bar", line_number=5, kind="import"),
    ])
    refs_repo = CodeReferencesRepository(session)
    outcome_repo = SnapshotFileOutcomeRepository(session)

    await extract_file_to_references(
        message=msg,
        runner=runner,
        refs_repo=refs_repo,
        outcome_repo=outcome_repo,
        session=session,
    )

    assert await refs_repo.count_by_symbol(symbol_name="foo", snapshot_sha="sha-og-ok") == 2
    assert await refs_repo.count_by_symbol(symbol_name="bar", snapshot_sha="sha-og-ok") == 1
    assert await outcome_repo.count_completed(snapshot_sha="sha-og-ok", extractor="opengrep") == 1


@pytest.mark.asyncio
async def test_opengrep_failure_marks_file_failed(session: AsyncSession) -> None:
    msg = OpengrepRunnerMessage(
        batch_id="sha-og-fail",
        snapshot_sha="sha-og-fail",
        repo_root="/tmp",
        file_path="b.py",
    )
    runner = MagicMock(spec=OpengrepRunner)
    runner.scan_file = AsyncMock(side_effect=OpengrepRunError("opengrep blew up"))
    refs_repo = CodeReferencesRepository(session)
    outcome_repo = SnapshotFileOutcomeRepository(session)

    await extract_file_to_references(
        message=msg,
        runner=runner,
        refs_repo=refs_repo,
        outcome_repo=outcome_repo,
        session=session,
    )

    # No references written.
    assert await refs_repo.count_by_symbol(symbol_name="foo", snapshot_sha="sha-og-fail") == 0
    # Outcome marked failed (NOT raised — caller doesn't see the exception).
    assert await outcome_repo.count_failed(snapshot_sha="sha-og-fail", extractor="opengrep") == 1
    assert await outcome_repo.count_completed(snapshot_sha="sha-og-fail", extractor="opengrep") == 0


@pytest.mark.asyncio
async def test_zero_matches_still_writes_success_outcome(session: AsyncSession) -> None:
    """A file with no opengrep matches still completes successfully — the
    file was 'attempted'. This affirms architectural invariant 1 (barrier
    counts attempts, not coverage)."""
    msg = OpengrepRunnerMessage(
        batch_id="sha-og-empty",
        snapshot_sha="sha-og-empty",
        repo_root="/tmp",
        file_path="empty.py",
    )
    runner = MagicMock(spec=OpengrepRunner)
    runner.scan_file = AsyncMock(return_value=[])
    refs_repo = CodeReferencesRepository(session)
    outcome_repo = SnapshotFileOutcomeRepository(session)

    await extract_file_to_references(
        message=msg,
        runner=runner,
        refs_repo=refs_repo,
        outcome_repo=outcome_repo,
        session=session,
    )

    assert await refs_repo.count_by_symbol(symbol_name="foo", snapshot_sha="sha-og-empty") == 0
    assert await outcome_repo.count_completed(snapshot_sha="sha-og-empty", extractor="opengrep") == 1


@pytest.mark.asyncio
async def test_resolves_file_path_against_repo_root(session: AsyncSession, tmp_path: pathlib.Path) -> None:
    """The message carries `repo_root` and a relative `file_path`. The
    worker passes `repo_root + '/' + file_path` to OpenGrep as the absolute
    path to scan."""
    (tmp_path / "x.py").write_text("def foo(): pass\n")
    msg = OpengrepRunnerMessage(
        batch_id="sha-og-resolve",
        snapshot_sha="sha-og-resolve",
        repo_root=str(tmp_path),
        file_path="x.py",
    )
    runner = MagicMock(spec=OpengrepRunner)
    runner.scan_file = AsyncMock(return_value=[])
    refs_repo = CodeReferencesRepository(session)
    outcome_repo = SnapshotFileOutcomeRepository(session)

    await extract_file_to_references(
        message=msg,
        runner=runner,
        refs_repo=refs_repo,
        outcome_repo=outcome_repo,
        session=session,
    )

    # Runner was called with the absolute resolved path.
    call_kwargs = runner.scan_file.call_args.kwargs
    assert call_kwargs["file_path"] == str(tmp_path / "x.py")
```

- [ ] **Step 5: Implement the tasks module**

Create `src/codeoracle/workers/opengrep_runner/tasks.py`:

```python
"""Pure-function pipeline for per-file OpenGrep extraction.

The consumer's `handle()` method delegates to this function.

Error handling:
- `OpengrepRunError` → write outcome=failed with truncated message, do NOT re-raise.
- Any other exception → write outcome=failed and re-raise (so WorkerConsumer's
  generic error path routes to DLQ).
"""

from __future__ import annotations

import logging
import os

from sqlalchemy.ext.asyncio import AsyncSession

from codeoracle.adapters.opengrep.runner import OpengrepRunner
from codeoracle.core.exceptions import OpengrepRunError
from codeoracle.db.repositories.code_references import (
    CodeReferenceRow,
    CodeReferencesRepository,
)
from codeoracle.db.repositories.snapshot_file_outcome import (
    SnapshotFileOutcomeRepository,
    SnapshotFileOutcomeRow,
)
from codeoracle.workers.opengrep_runner.message import OpengrepRunnerMessage


_LOG = logging.getLogger(__name__)


async def extract_file_to_references(
    *,
    message: OpengrepRunnerMessage,
    runner: OpengrepRunner,
    refs_repo: CodeReferencesRepository,
    outcome_repo: SnapshotFileOutcomeRepository,
    session: AsyncSession,
) -> None:
    """Scan one file with OpenGrep, write CodeReferences + outcome."""
    abs_file_path = os.path.join(message.repo_root, message.file_path)

    try:
        matches = await runner.scan_file(file_path=abs_file_path)
    except OpengrepRunError as e:
        _LOG.exception("opengrep run failed for %s", message.file_path)
        await outcome_repo.upsert(
            SnapshotFileOutcomeRow(
                snapshot_sha=message.snapshot_sha,
                file_path=message.file_path,
                extractor="opengrep",
                outcome="failed",
                error_message=f"opengrep: {str(e)[:512]}",
            )
        )
        await session.commit()
        return

    # Convert matches → CodeReferenceRow (deduplication via set semantics).
    seen: set[tuple[str, str, int, str]] = set()
    rows: list[CodeReferenceRow] = []
    for m in matches:
        key = (message.file_path, m.symbol_name, m.line_number, m.kind)
        if key in seen:
            continue
        seen.add(key)
        rows.append(
            CodeReferenceRow(
                snapshot_sha=message.snapshot_sha,
                file_path=message.file_path,
                symbol_name=m.symbol_name,
                line_number=m.line_number,
                kind=m.kind,
            )
        )

    try:
        await refs_repo.replace_file_references(
            snapshot_sha=message.snapshot_sha,
            file_path=message.file_path,
            references=rows,
        )
        await outcome_repo.upsert(
            SnapshotFileOutcomeRow(
                snapshot_sha=message.snapshot_sha,
                file_path=message.file_path,
                extractor="opengrep",
                outcome="success",
                error_message=None,
            )
        )
        await session.commit()
    except Exception as e:
        await session.rollback()
        await outcome_repo.upsert(
            SnapshotFileOutcomeRow(
                snapshot_sha=message.snapshot_sha,
                file_path=message.file_path,
                extractor="opengrep",
                outcome="failed",
                error_message=f"db write: {str(e)[:512]}",
            )
        )
        await session.commit()
        raise
```

- [ ] **Step 6: Run tasks test to verify it passes**

Run: `uv run pytest tests/unit/workers/opengrep_runner/test_opengrep_runner_tasks.py -v`

Expected: PASS (4 tests).

- [ ] **Step 7: Implement the consumer**

Create `src/codeoracle/workers/opengrep_runner/consumer.py`:

```python
"""OpengrepRunnerConsumer — per-file WorkerConsumer.

Stream: indexing.opengrep
Group:  opengrep-runner
count_per_poll = 5 (per-file is fast; can parallelize with sibling workers).
"""

from __future__ import annotations

import asyncio

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from codeoracle.adapters.opengrep.runner import OpengrepRunner
from codeoracle.adapters.valkey.client import ValkeyQueueClient
from codeoracle.db.repositories.code_references import CodeReferencesRepository
from codeoracle.db.repositories.snapshot_file_outcome import SnapshotFileOutcomeRepository
from codeoracle.workers._base.consumer import WorkerConsumer
from codeoracle.workers._base.message import RetryPolicy
from codeoracle.workers.opengrep_runner.message import OpengrepRunnerMessage
from codeoracle.workers.opengrep_runner.tasks import extract_file_to_references
from codeoracle.core.streams import GROUP_OPENGREP_RUNNER, STREAM_INDEXING_OPENGREP


class OpengrepRunnerConsumer(WorkerConsumer[OpengrepRunnerMessage]):
    stream = STREAM_INDEXING_OPENGREP
    group = GROUP_OPENGREP_RUNNER
    message_type = OpengrepRunnerMessage
    count_per_poll = 5
    block_ms = 5000

    def __init__(
        self,
        *,
        queue_client: ValkeyQueueClient,
        retry_policy: RetryPolicy,
        consumer_name: str,
        stop_event: asyncio.Event,
        runner: OpengrepRunner,
        db_session_factory: async_sessionmaker[AsyncSession],
    ) -> None:
        super().__init__(
            queue_client=queue_client,
            retry_policy=retry_policy,
            consumer_name=consumer_name,
            stop_event=stop_event,
            # reclaim_min_idle_ms intentionally omitted → the inherited reaper
            # stays OFF for this stream. The opengrep handle is now bounded by
            # the OpengrepRunner scan timeout (default 100s), so the double-run
            # hazard that previously gated reclaim is gone. Turning reclaim ON
            # is a deferred follow-up that needs extra care here: count_per_poll
            # = 5 makes the in-flight window 5 * scan_timeout, so
            # reclaim_min_idle_ms must clear a full batch and still keep
            # reclaim_min_idle + one_batch < the orchestrator completion barrier
            # (600s, Plan 5).
        )
        self._runner = runner
        self._db_factory = db_session_factory

    async def handle(self, message: OpengrepRunnerMessage) -> None:
        async with self._db_factory() as session:
            await extract_file_to_references(
                message=message,
                runner=self._runner,
                refs_repo=CodeReferencesRepository(session),
                outcome_repo=SnapshotFileOutcomeRepository(session),
                session=session,
            )
```

- [ ] **Step 8: Write consumer test**

Create `tests/unit/workers/opengrep_runner/test_opengrep_runner_consumer.py`:

```python
"""OpengrepRunnerConsumer — handle() wiring sanity."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from codeoracle.adapters.opengrep.runner import OpengrepRunner
from codeoracle.workers._base.message import RetryPolicy
from codeoracle.workers.opengrep_runner.consumer import OpengrepRunnerConsumer
from codeoracle.workers.opengrep_runner.message import OpengrepRunnerMessage


@pytest.mark.asyncio
async def test_consumer_handle_runs_pipeline(
    async_session_factory: async_sessionmaker[AsyncSession],
    valkey_queue_client,
) -> None:
    runner = MagicMock(spec=OpengrepRunner)
    runner.scan_file = AsyncMock(return_value=[])

    consumer = OpengrepRunnerConsumer(
        queue_client=valkey_queue_client,
        retry_policy=RetryPolicy(
            max_attempts=2,
        ),
        consumer_name="opengrep-test",
        stop_event=asyncio.Event(),
        runner=runner,
        db_session_factory=async_session_factory,
    )

    msg = OpengrepRunnerMessage(
        batch_id="sha-cw",
        snapshot_sha="sha-cw",
        repo_root="/tmp/repo",
        file_path="a.py",
    )
    await consumer.handle(msg)
    runner.scan_file.assert_awaited_once()
```

- [ ] **Step 9: Implement the worker entrypoint**

Create `src/codeoracle/workers/opengrep_runner/__main__.py`:

```python
"""OpenGrep runner worker entrypoint — `python -m codeoracle.workers.opengrep_runner`."""

from __future__ import annotations

import asyncio
import logging
import signal

from codeoracle.adapters.opengrep import DEFAULT_RULES_DIR
from codeoracle.adapters.opengrep.runner import OpengrepRunner
from codeoracle.adapters.valkey.client import ValkeyQueueClient
from codeoracle.core.config import Settings
from codeoracle.core.exceptions import OpengrepRunError
from codeoracle.db.session import make_async_engine, make_async_session_factory
from codeoracle.workers._base.message import RetryPolicy
from codeoracle.workers.opengrep_runner.consumer import OpengrepRunnerConsumer


_LOG = logging.getLogger(__name__)


async def _main() -> None:
    logging.basicConfig(level=logging.INFO)
    settings = Settings()
    queue_client = ValkeyQueueClient.from_settings(settings)
    # Plan 3 / Plan 5 contract: make_async_engine() reads Settings internally
    # via get_settings(); make_async_session_factory(engine) binds the factory.
    engine = make_async_engine()
    db_factory = make_async_session_factory(engine)

    runner = OpengrepRunner(rules_dir=DEFAULT_RULES_DIR)
    try:
        version = await runner.verify_binary()
        _LOG.info("opengrep binary OK: %s", version)
    except OpengrepRunError as e:
        _LOG.error("opengrep binary missing/unusable; refusing to start: %s", e)
        return

    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop_event.set)

    consumer = OpengrepRunnerConsumer(
        queue_client=queue_client,
        retry_policy=RetryPolicy(
            max_attempts=3,
        ),
        consumer_name=f"opengrep-{settings.worker_instance_id}",
        stop_event=stop_event,
        runner=runner,
        db_session_factory=db_factory,
    )

    _LOG.info("opengrep-runner worker starting")
    await consumer.run()
    _LOG.info("opengrep-runner worker stopped")


if __name__ == "__main__":
    asyncio.run(_main())
```

- [ ] **Step 10: Create __init__.py**

Create `src/codeoracle/workers/opengrep_runner/__init__.py`:

```python
"""OpenGrep runner worker — per-file textual reference extraction.

Consumes `indexing.opengrep` stream; one message per (snapshot, file);
writes CodeReferences rows + one outcome row per consumed message.
"""

from codeoracle.workers.opengrep_runner.consumer import OpengrepRunnerConsumer
from codeoracle.workers.opengrep_runner.message import OpengrepRunnerMessage

__all__ = ["OpengrepRunnerConsumer", "OpengrepRunnerMessage"]
```

- [ ] **Step 11: Run all OpenGrep worker tests**

Run: `uv run pytest tests/unit/workers/opengrep_runner/ -v`

Expected: PASS.

- [ ] **Step 12: Commit**

```bash
git add tests/unit/workers/opengrep_runner/ src/codeoracle/workers/opengrep_runner/
git commit -m "feat(workers): opengrep-runner (per-file) (Plan 6 Task 11)"
```

---

### Task 12: Embed Indexer Worker (per-file with internal chunking)

**Files:**
- Create: `src/codeoracle/services/embedding/chunker.py` (`Chunker` ABC + `LineChunker` + `ParagraphChunker` + `chunker_for` factory + `is_doc_path` predicate — single module, three classes, one factory, one predicate)
- Create: `src/codeoracle/workers/embed_indexer/__init__.py`
- Create: `src/codeoracle/workers/embed_indexer/message.py`
- Create: `src/codeoracle/workers/embed_indexer/tasks.py`
- Create: `src/codeoracle/workers/embed_indexer/consumer.py`
- Create: `src/codeoracle/workers/embed_indexer/__main__.py`
- Test: `tests/unit/services/embedding/test_line_chunker.py` (LineChunker pure-function tests)
- Test: `tests/unit/services/embedding/test_paragraph_chunker.py` (ParagraphChunker pure-function tests — G14)
- Test: `tests/unit/services/embedding/test_chunker_for.py` (factory dispatch tests + `is_doc_path` predicate tests — G14 + Plan 10 cross-plan consumer)
- Test: `tests/unit/workers/embed_indexer/test_embed_message.py`
- Test: `tests/integration/workers/embed_indexer/test_embed_tasks.py`
- Test: `tests/integration/workers/embed_indexer/test_embed_consumer.py`

**Chunking design — two chunkers, dispatched by file extension (G14):**

`Chunker` is an ABC with one method: `chunk(lines: list[str]) -> list[Chunk]`. Storage shape `(start_line, end_line, text)` is uniform across implementations — only the algorithm differs. The factory `chunker_for(file_path, *, embedding_config)` selects the implementation:

- **`LineChunker(chunk_lines, overlap)`** — code files (`.py`, `.pyi`). Produces non-overlapping-at-boundaries chunks with `overlap` lines of shared content between adjacent chunks. For a 500-line file with `chunk_lines=100`, `overlap=20`:
  - Chunk 1: lines 1–100
  - Chunk 2: lines 81–180 (20-line overlap)
  - Chunk 3: lines 161–260
  - Chunk 4: lines 241–340
  - Chunk 5: lines 321–420
  - Chunk 6: lines 401–500

- **`ParagraphChunker(max_paragraph_lines)`** — documentation files (`.md`, `.rst`, `.adoc`, `.markdown`). A paragraph is a run of consecutive non-blank lines bounded by blank lines (CommonMark §4.8); each paragraph becomes one Chunk. Paragraphs longer than `max_paragraph_lines` are split via LineChunker-style stride (no overlap — paragraphs are already semantic units). Why a different algorithm: line-windowed chunks over prose produce arbitrary mid-sentence cuts that wreck retrieval — paragraph boundaries align with semantic units, so embeddings carry meaningful context.

The chunker is pure: input is a list of lines, output is a list of `Chunk(start_line, end_line, text)`. The consumer reads the file, calls `chunker_for(message.file_path, embedding_config=...)` to dispatch per-message, runs the chunker, embeds all chunks in one batch call (efficient), writes them in one transaction.

**Atomicity:** One file → one transaction. `EmbeddingsRepository.replace_file_embeddings` clears prior chunks then inserts the new ones; outcome row upserts; commit. If anything fails mid-write, rollback + write outcome=failed.

**Dim mismatch handling:** If the provider's returned vectors have length != `EmbeddingConfig.dim`, raise `EmbedExtractionError` (config bug, terminal). The worker catches this, marks the file failed.

**Cross-plan invariant:** Plan 8's `CodeSpanResolver` reads code embeddings; `DocParagraphResolver` reads doc embeddings. Both read `(chunk_start_line, chunk_end_line)` from each `embeddings` row directly — they MUST NOT assume `embed_chunk_lines`-sized spans, because doc chunks are variable-length paragraphs. See Architectural Invariant 5 + Cross-Plan Downstream Contracts.

- [ ] **Step 1: Write the chunker test**

Create `tests/unit/services/embedding/test_line_chunker.py`:

```python
"""LineChunker — pure-function tests, no I/O."""

import pytest

from codeoracle.services.embedding.chunker import Chunk, LineChunker


def test_empty_input_returns_empty_list() -> None:
    chunker = LineChunker(chunk_lines=100, overlap=20)
    assert chunker.chunk([]) == []


def test_single_line_file() -> None:
    chunker = LineChunker(chunk_lines=100, overlap=20)
    chunks = chunker.chunk(["only line"])
    assert chunks == [Chunk(start_line=1, end_line=1, text="only line")]


def test_short_file_under_chunk_size_single_chunk() -> None:
    """50-line file with chunk_lines=100 → one chunk covering lines 1-50."""
    chunker = LineChunker(chunk_lines=100, overlap=20)
    lines = [f"line {i}" for i in range(1, 51)]
    chunks = chunker.chunk(lines)
    assert len(chunks) == 1
    assert chunks[0].start_line == 1
    assert chunks[0].end_line == 50
    assert chunks[0].text == "\n".join(lines)


def test_exact_chunk_size_single_chunk() -> None:
    """Exactly chunk_lines lines → one chunk."""
    chunker = LineChunker(chunk_lines=100, overlap=20)
    lines = [f"line {i}" for i in range(1, 101)]
    chunks = chunker.chunk(lines)
    assert len(chunks) == 1
    assert chunks[0].start_line == 1
    assert chunks[0].end_line == 100


def test_two_chunks_with_overlap() -> None:
    """120-line file with chunk_lines=100, overlap=20 → 2 chunks (1-100, 81-120)."""
    chunker = LineChunker(chunk_lines=100, overlap=20)
    lines = [f"line {i}" for i in range(1, 121)]
    chunks = chunker.chunk(lines)
    assert len(chunks) == 2
    assert chunks[0].start_line == 1 and chunks[0].end_line == 100
    assert chunks[1].start_line == 81 and chunks[1].end_line == 120


def test_500_line_file_multiple_chunks_with_overlap() -> None:
    """500-line file with chunk_lines=100, overlap=20 → 6 chunks."""
    chunker = LineChunker(chunk_lines=100, overlap=20)
    lines = [f"line {i}" for i in range(1, 501)]
    chunks = chunker.chunk(lines)
    assert len(chunks) == 6
    expected = [(1, 100), (81, 180), (161, 260), (241, 340), (321, 420), (401, 500)]
    actual = [(c.start_line, c.end_line) for c in chunks]
    assert actual == expected


def test_overlap_zero_means_no_shared_content() -> None:
    chunker = LineChunker(chunk_lines=10, overlap=0)
    lines = [f"line {i}" for i in range(1, 31)]
    chunks = chunker.chunk(lines)
    assert len(chunks) == 3
    assert [(c.start_line, c.end_line) for c in chunks] == [(1, 10), (11, 20), (21, 30)]


def test_overlap_must_be_less_than_chunk_lines() -> None:
    """The chunker rejects invalid geometry to fail fast (mirrors the
    EmbeddingConfig validator). `chunk_lines=100, overlap=100` would loop forever."""
    with pytest.raises(ValueError):
        LineChunker(chunk_lines=100, overlap=100)
    with pytest.raises(ValueError):
        LineChunker(chunk_lines=100, overlap=200)


def test_last_chunk_clamps_to_end_of_file() -> None:
    """A 250-line file with chunk_lines=100, overlap=20 → chunks (1-100),
    (81-180), (161-250) — the last chunk is shorter than chunk_lines."""
    chunker = LineChunker(chunk_lines=100, overlap=20)
    lines = [f"line {i}" for i in range(1, 251)]
    chunks = chunker.chunk(lines)
    assert len(chunks) == 3
    assert chunks[-1].start_line == 161
    assert chunks[-1].end_line == 250
```

- [ ] **Step 2: Implement Chunker ABC + LineChunker + ParagraphChunker + chunker_for**

Create `src/codeoracle/services/embedding/chunker.py`:

```python
"""Chunker family — split a file's lines into embeddable chunks.

Plan 6 ships two chunker implementations behind one ABC, dispatched per file
by `chunker_for(file_path)`:

- LineChunker (code files: .py, .pyi) — fixed-size overlapping line windows.
- ParagraphChunker (doc files: .md, .rst, .adoc, .markdown) — semantic
  paragraph runs bounded by blank lines.

Storage shape `(start_line, end_line, text)` is uniform across implementations;
only the chunking algorithm differs. Plan 8's CodeSpanResolver and
DocParagraphResolver both read `(chunk_start_line, chunk_end_line)` directly
from each `embeddings` row — they MUST NOT assume embed_chunk_lines-sized
spans, because doc chunks are variable-length. See Architectural Invariant 5.

Both writer (workers/embed_indexer) and reader (Plan 8 resolvers) load the
same `EmbeddingConfig` instance via `get_settings().embedding`, so any
env-override (CODEORACLE_EMBEDDING__EMBED_CHUNK_LINES, CODEORACLE_EMBEDDING__DOC_CHUNK_MAX_LINES)
flows to both sides in lockstep.
"""

from __future__ import annotations

import pathlib
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    # Settings import lives in TYPE_CHECKING to avoid a runtime cycle:
    # core/config.py never imports this module, so a deferred type-only
    # hint here is the cleanest way to type-annotate `chunker_for`.
    from codeoracle.core.config import EmbeddingConfig

@dataclass(frozen=True, slots=True)
class Chunk:
    """One chunk's metadata + text. Line numbers are 1-indexed and inclusive."""

    start_line: int
    end_line: int
    text: str


class Chunker(ABC):
    """Strategy ABC. Implementations differ in how they slice lines into chunks;
    storage shape `(start_line, end_line, text)` is uniform.

    Implementations MUST be pure (no I/O), deterministic, and return chunks in
    file order with strictly non-decreasing `start_line` (so the writer can
    insert them with predictable ordering).
    """

    @abstractmethod
    def chunk(self, lines: list[str]) -> list[Chunk]:
        ...


class LineChunker(Chunker):
    """Fixed-size overlapping line windows. Used for code files.

    Algorithm: stride = chunk_lines - overlap. First chunk covers [1, chunk_lines];
    each subsequent chunk starts `stride` lines later. The last chunk's end_line
    is clamped to the file's total line count.
    """

    def __init__(self, *, chunk_lines: int, overlap: int) -> None:
        if chunk_lines <= 0:
            raise ValueError(f"chunk_lines must be positive, got {chunk_lines}")
        if overlap < 0:
            raise ValueError(f"overlap must be non-negative, got {overlap}")
        if overlap >= chunk_lines:
            raise ValueError(
                f"overlap ({overlap}) must be strictly less than chunk_lines ({chunk_lines})"
            )
        # NOTE: the stride floor that rejects degenerate geometries like
        # chunk_lines=2, overlap=1 (stride=1 → O(file) near-duplicate chunks)
        # lives in EmbeddingConfig's `_validate_embedding_config` (core/config.py,
        # Task 3, D-6.13) — the single owned gate. `chunker_for` is the only
        # production construction site and always feeds already-validated config
        # values, so no second floor is re-asserted here (one authoritative
        # invariant at the config boundary, not two kept in lockstep).
        self._chunk_lines = chunk_lines
        self._overlap = overlap

    def chunk(self, lines: list[str]) -> list[Chunk]:
        if not lines:
            return []
        total = len(lines)
        stride = self._chunk_lines - self._overlap
        chunks: list[Chunk] = []
        start_zero = 0  # 0-indexed for math
        while start_zero < total:
            end_zero = min(start_zero + self._chunk_lines, total)
            text = "\n".join(lines[start_zero:end_zero])
            chunks.append(
                Chunk(
                    start_line=start_zero + 1,  # 1-indexed
                    end_line=end_zero,           # 1-indexed (inclusive)
                    text=text,
                )
            )
            if end_zero == total:
                break
            start_zero += stride
        return chunks


class ParagraphChunker(Chunker):
    """Markdown-style paragraph chunker. Used for doc files (.md, .rst, .adoc).

    A paragraph is a run of consecutive non-blank lines (CommonMark §4.8).
    Blank lines (empty or whitespace-only) act as paragraph separators and
    are NOT included in any chunk. Each paragraph becomes one Chunk.

    Paragraphs longer than `max_paragraph_lines` are split via LineChunker-style
    stride (no overlap — paragraphs are already semantic units). This guards
    against pathological inputs (one giant code-fence dump, a generated
    table-of-symbols) that would otherwise produce one enormous embedding
    request and blow the context window.

    Empty input or all-blank input returns []. A file with no blank lines
    returns one chunk covering the whole file (or N chunks if it exceeds
    `max_paragraph_lines`).
    """

    def __init__(self, *, max_paragraph_lines: int) -> None:
        if max_paragraph_lines <= 0:
            raise ValueError(
                f"max_paragraph_lines must be positive, got {max_paragraph_lines}"
            )
        self._max = max_paragraph_lines

    @staticmethod
    def _is_blank(line: str) -> bool:
        return not line.strip()

    def chunk(self, lines: list[str]) -> list[Chunk]:
        if not lines:
            return []
        chunks: list[Chunk] = []
        i = 0
        total = len(lines)
        while i < total:
            # Skip blank lines.
            if self._is_blank(lines[i]):
                i += 1
                continue
            # Collect a run of non-blank lines.
            run_start = i  # 0-indexed
            while i < total and not self._is_blank(lines[i]):
                i += 1
            run_end = i  # 0-indexed exclusive

            # Emit one chunk per `max_paragraph_lines` slice of this paragraph.
            slice_start = run_start
            while slice_start < run_end:
                slice_end = min(slice_start + self._max, run_end)
                chunks.append(
                    Chunk(
                        start_line=slice_start + 1,  # 1-indexed
                        end_line=slice_end,           # 1-indexed (inclusive)
                        text="\n".join(lines[slice_start:slice_end]),
                    )
                )
                slice_start = slice_end
        return chunks


# File extensions routed to ParagraphChunker. All others (including unknown)
# get LineChunker. Lowercase comparison; suffix taken via pathlib.PurePosixPath
# so the same logic works on Windows-style path strings passed through the
# orchestrator unchanged.
_DOC_EXTENSIONS: frozenset[str] = frozenset({".md", ".rst", ".adoc", ".markdown"})


def is_doc_path(source_path: str) -> bool:
    """Whether `source_path` is a documentation file (Markdown / reST / AsciiDoc).

    Public path-classification predicate, parallel to `chunker_for`'s internal
    dispatch. Plan 10's `embed_query` node uses this to route an `EmbeddingHit`
    to `HandleType.DOC_PARAGRAPH` (doc extension) vs `HandleType.CODE_SPAN`
    (everything else), reusing the same `_DOC_EXTENSIONS` constant `chunker_for`
    consults — so what got chunked as a doc cites as a doc, no drift.

    Suffix comparison matches `chunker_for`: lowercase, `pathlib.PurePosixPath`
    so Windows-style separators in path strings are handled unchanged.
    """
    return pathlib.PurePosixPath(source_path).suffix.lower() in _DOC_EXTENSIONS


def chunker_for(file_path: str, *, embedding_config: "EmbeddingConfig") -> Chunker:
    """Dispatch by file extension. Used by the embed-indexer consumer to pick
    the right chunker per message — code files get LineChunker, doc files get
    ParagraphChunker, unknown extensions default to LineChunker (conservative:
    a `.txt` file still gets a meaningful overlapping-line treatment).

    The embed extractor's orchestrator-side filter is `accepted_extensions=None`
    (Task 2 `_INFO_BY_KIND["embed"]`), so this factory sees every kind of file.
    Code extractors (joern/scip/opengrep) never see doc files at all — that
    filtering happens at fan-out (`IndexingOrchestrator._filter_files_for`).
    """
    suffix = pathlib.PurePosixPath(file_path).suffix.lower()
    if suffix in _DOC_EXTENSIONS:
        return ParagraphChunker(max_paragraph_lines=embedding_config.doc_chunk_max_lines)
    return LineChunker(
        chunk_lines=embedding_config.embed_chunk_lines,
        overlap=embedding_config.embed_chunk_overlap,
    )
```

- [ ] **Step 2b: Write ParagraphChunker tests (G14)**

Create `tests/unit/services/embedding/test_paragraph_chunker.py`:

```python
"""ParagraphChunker — pure-function tests for doc-file chunking (G14).

A paragraph is a run of consecutive non-blank lines bounded by blank lines
(CommonMark §4.8). Each paragraph becomes one Chunk. Paragraphs longer than
max_paragraph_lines split via stride.
"""

import pytest

from codeoracle.services.embedding.chunker import Chunk, ParagraphChunker


def test_empty_input_returns_empty_list() -> None:
    chunker = ParagraphChunker(max_paragraph_lines=200)
    assert chunker.chunk([]) == []


def test_only_blank_lines_returns_empty_list() -> None:
    """A doc file with only blank lines has no paragraphs → no chunks."""
    chunker = ParagraphChunker(max_paragraph_lines=200)
    assert chunker.chunk(["", "   ", "\t", ""]) == []


def test_single_paragraph_one_chunk() -> None:
    """A run of non-blank lines with no internal blanks → one chunk covering it."""
    chunker = ParagraphChunker(max_paragraph_lines=200)
    chunks = chunker.chunk(["# Heading", "Paragraph line 1.", "Paragraph line 2."])
    assert chunks == [Chunk(start_line=1, end_line=3, text="# Heading\nParagraph line 1.\nParagraph line 2.")]


def test_two_paragraphs_separated_by_blank_line() -> None:
    """Two non-blank runs separated by a blank → two chunks. Blank line is
    NOT included in either chunk."""
    chunker = ParagraphChunker(max_paragraph_lines=200)
    chunks = chunker.chunk(["Para A line 1", "Para A line 2", "", "Para B line 1"])
    assert chunks == [
        Chunk(start_line=1, end_line=2, text="Para A line 1\nPara A line 2"),
        Chunk(start_line=4, end_line=4, text="Para B line 1"),
    ]


def test_multiple_blank_lines_between_paragraphs() -> None:
    """Several blank lines in a row still separate exactly two paragraphs;
    chunk indices skip across all the blanks."""
    chunker = ParagraphChunker(max_paragraph_lines=200)
    chunks = chunker.chunk(["A", "", "", "", "B"])
    assert chunks == [
        Chunk(start_line=1, end_line=1, text="A"),
        Chunk(start_line=5, end_line=5, text="B"),
    ]


def test_leading_blank_lines_are_skipped() -> None:
    """Doc files often start with blank lines or front-matter blanks;
    chunker advances past them."""
    chunker = ParagraphChunker(max_paragraph_lines=200)
    chunks = chunker.chunk(["", "", "Heading"])
    assert chunks == [Chunk(start_line=3, end_line=3, text="Heading")]


def test_trailing_blank_lines_are_skipped() -> None:
    chunker = ParagraphChunker(max_paragraph_lines=200)
    chunks = chunker.chunk(["Heading", "", ""])
    assert chunks == [Chunk(start_line=1, end_line=1, text="Heading")]


def test_whitespace_only_line_treated_as_blank() -> None:
    """A line containing only spaces or tabs separates paragraphs."""
    chunker = ParagraphChunker(max_paragraph_lines=200)
    chunks = chunker.chunk(["A", "   \t  ", "B"])
    assert chunks == [
        Chunk(start_line=1, end_line=1, text="A"),
        Chunk(start_line=3, end_line=3, text="B"),
    ]


def test_long_paragraph_splits_into_chunks_of_max_paragraph_lines() -> None:
    """A 7-line paragraph with max=3 splits into chunks (3, 3, 1)."""
    chunker = ParagraphChunker(max_paragraph_lines=3)
    lines = [f"line {i}" for i in range(1, 8)]
    chunks = chunker.chunk(lines)
    assert len(chunks) == 3
    assert chunks[0] == Chunk(start_line=1, end_line=3, text="line 1\nline 2\nline 3")
    assert chunks[1] == Chunk(start_line=4, end_line=6, text="line 4\nline 5\nline 6")
    assert chunks[2] == Chunk(start_line=7, end_line=7, text="line 7")


def test_max_paragraph_lines_must_be_positive() -> None:
    with pytest.raises(ValueError):
        ParagraphChunker(max_paragraph_lines=0)
    with pytest.raises(ValueError):
        ParagraphChunker(max_paragraph_lines=-5)


def test_realistic_markdown_block() -> None:
    """Real-world shape: headings, paragraphs, a code-fence (treated as one
    paragraph because it has no internal blank lines), and a list."""
    chunker = ParagraphChunker(max_paragraph_lines=200)
    lines = [
        "# Title",
        "",
        "Introductory paragraph",
        "with two lines.",
        "",
        "```python",
        "def foo():",
        "    return 42",
        "```",
        "",
        "- bullet one",
        "- bullet two",
    ]
    chunks = chunker.chunk(lines)
    assert len(chunks) == 4
    # Heading
    assert chunks[0] == Chunk(start_line=1, end_line=1, text="# Title")
    # Intro paragraph
    assert chunks[1] == Chunk(start_line=3, end_line=4, text="Introductory paragraph\nwith two lines.")
    # Code fence (no blanks inside → one chunk)
    assert chunks[2].start_line == 6 and chunks[2].end_line == 9
    assert "def foo()" in chunks[2].text
    # Bullet list (consecutive non-blanks → one chunk)
    assert chunks[3] == Chunk(start_line=11, end_line=12, text="- bullet one\n- bullet two")
```

- [ ] **Step 2c: Write chunker_for factory tests (G14)**

Create `tests/unit/services/embedding/test_chunker_for.py`:

```python
"""chunker_for + is_doc_path — extension-based dispatch + the public path predicate.

`chunker_for` is the embed-indexer's internal factory; `is_doc_path` is the
public predicate Plan 10's `embed_query` uses to route knn hits to
DOC_PARAGRAPH vs CODE_SPAN. Both consult the same `_DOC_EXTENSIONS` set, so
they MUST agree per-path — the cross-`is_doc_path`/`chunker_for` consistency
test below pins that contract.
"""

import pytest

from codeoracle.core.config import EmbeddingConfig
from codeoracle.services.embedding.chunker import (
    LineChunker,
    ParagraphChunker,
    chunker_for,
    is_doc_path,
)


def _config() -> EmbeddingConfig:
    return EmbeddingConfig(model="text-embedding-3-small", dim=1536, kind="mock")


def test_python_file_dispatches_to_line_chunker() -> None:
    assert isinstance(chunker_for("foo.py", embedding_config=_config()), LineChunker)


def test_python_stub_file_dispatches_to_line_chunker() -> None:
    assert isinstance(chunker_for("types.pyi", embedding_config=_config()), LineChunker)


def test_markdown_file_dispatches_to_paragraph_chunker() -> None:
    assert isinstance(chunker_for("README.md", embedding_config=_config()), ParagraphChunker)


def test_uppercase_markdown_extension_dispatches_to_paragraph_chunker() -> None:
    """Suffix comparison is case-insensitive (real repos have README.MD)."""
    assert isinstance(chunker_for("CHANGELOG.MD", embedding_config=_config()), ParagraphChunker)


def test_rst_file_dispatches_to_paragraph_chunker() -> None:
    assert isinstance(chunker_for("docs/intro.rst", embedding_config=_config()), ParagraphChunker)


def test_asciidoc_file_dispatches_to_paragraph_chunker() -> None:
    assert isinstance(chunker_for("api.adoc", embedding_config=_config()), ParagraphChunker)


def test_long_markdown_extension_dispatches_to_paragraph_chunker() -> None:
    """Some repos use the long form .markdown."""
    assert isinstance(chunker_for("README.markdown", embedding_config=_config()), ParagraphChunker)


def test_unknown_extension_dispatches_to_line_chunker() -> None:
    """Conservative default: anything not in _DOC_EXTENSIONS gets LineChunker.
    A .txt file still produces meaningful overlapping-line chunks.
    """
    assert isinstance(chunker_for("notes.txt", embedding_config=_config()), LineChunker)


def test_no_extension_dispatches_to_line_chunker() -> None:
    """Files like `LICENSE` or `Dockerfile` with no suffix → LineChunker default."""
    assert isinstance(chunker_for("LICENSE", embedding_config=_config()), LineChunker)


def test_line_chunker_inherits_config_geometry() -> None:
    """LineChunker built by factory carries embed_chunk_lines + embed_chunk_overlap."""
    config = _config()
    chunker = chunker_for("foo.py", embedding_config=config)
    # Exercise via behavior, not private attribute access.
    lines = [f"l{i}" for i in range(1, 121)]
    chunks = chunker.chunk(lines)
    # With defaults (100 lines, 20 overlap) on 120 lines → 2 chunks.
    assert len(chunks) == 2
    assert chunks[0].end_line == 100
    assert chunks[1].start_line == 81


def test_paragraph_chunker_inherits_config_geometry() -> None:
    """ParagraphChunker built by factory carries doc_chunk_max_lines."""
    config = EmbeddingConfig(model="m", dim=1536, kind="mock", doc_chunk_max_lines=5)
    chunker = chunker_for("README.md", embedding_config=config)
    # 12 non-blank lines, max=5 → 3 chunks (5, 5, 2).
    lines = [f"line {i}" for i in range(1, 13)]
    chunks = chunker.chunk(lines)
    assert len(chunks) == 3
    assert chunks[0].end_line == 5
    assert chunks[1].start_line == 6 and chunks[1].end_line == 10
    assert chunks[2].start_line == 11 and chunks[2].end_line == 12


# --- is_doc_path predicate (Plan 10 cross-plan consumer) ---------------------


@pytest.mark.parametrize(
    "path",
    [
        "README.md",
        "docs/intro.rst",
        "api.adoc",
        "README.markdown",
        "CHANGELOG.MD",   # case-insensitive (mirrors chunker_for)
        "subdir/a/b/notes.md",
        "C:\\repo\\README.md",  # Windows-style separators handled by PurePosixPath
    ],
)
def test_is_doc_path_recognises_doc_extensions(path: str) -> None:
    assert is_doc_path(path) is True


@pytest.mark.parametrize(
    "path",
    [
        "foo.py",
        "types.pyi",
        "notes.txt",      # unknown extension is NOT a doc (matches LineChunker default)
        "LICENSE",        # no extension is NOT a doc
        "Dockerfile",
        "foo.json",
        "foo.markdown.bak",  # the LAST suffix wins; .bak is not a doc
    ],
)
def test_is_doc_path_rejects_non_doc_extensions(path: str) -> None:
    assert is_doc_path(path) is False


@pytest.mark.parametrize(
    "path",
    [
        "foo.py", "types.pyi", "README.md", "docs/intro.rst", "api.adoc",
        "README.markdown", "CHANGELOG.MD", "notes.txt", "LICENSE",
    ],
)
def test_is_doc_path_agrees_with_chunker_for_dispatch(path: str) -> None:
    """The two paths through the doc-vs-code decision MUST agree per-path.

    If they diverge — e.g., `chunker_for` adds `.txt` to the doc set but
    `is_doc_path` doesn't — Plan 10 would emit CODE_SPAN citations for chunks
    that were paragraph-chunked, mis-classifying the evidence kind. This test
    pins single-source-of-truth: both consult `_DOC_EXTENSIONS`, so they
    cannot disagree without a code-level change to the constant.
    """
    is_doc = is_doc_path(path)
    chunker = chunker_for(path, embedding_config=_config())
    assert isinstance(chunker, ParagraphChunker) is is_doc
```

- [ ] **Step 3: Run all chunker tests to verify they pass**

Run: `uv run pytest tests/unit/services/embedding/ -v`

Expected: PASS (54 tests — 9 LineChunker + 11 ParagraphChunker + 11 `chunker_for` + 23 `is_doc_path` (7 doc + 7 non-doc + 9 `chunker_for`-agreement parametrized cases)).

- [ ] **Step 4: Implement the message**

Create `src/codeoracle/workers/embed_indexer/message.py`:

```python
"""EmbedIndexerMessage — payload for `indexing.embed` stream."""

from __future__ import annotations

from pydantic import field_validator

from codeoracle.workers._base.message import WorkerMessage


class EmbedIndexerMessage(WorkerMessage):
    snapshot_sha: str
    repo_root: str
    file_path: str

    @field_validator("snapshot_sha")
    @classmethod
    def _snapshot_sha_eq_batch_id(cls, v: str, info) -> str:
        batch_id = info.data.get("batch_id")
        if batch_id is not None and batch_id != v:
            raise ValueError(f"snapshot_sha ({v!r}) must equal batch_id ({batch_id!r})")
        return v
```

Create `tests/unit/workers/embed_indexer/test_embed_message.py`:

```python
"""EmbedIndexerMessage — same shape as Joern/OpenGrep per-file messages."""

import pytest
from pydantic import ValidationError

from codeoracle.workers.embed_indexer.message import EmbedIndexerMessage


def test_required_fields() -> None:
    msg = EmbedIndexerMessage(
        batch_id="sha-e",
        snapshot_sha="sha-e",
        repo_root="/tmp/repo",
        file_path="a.py",
    )
    assert msg.snapshot_sha == "sha-e"
    assert msg.file_path == "a.py"


def test_batch_id_must_equal_snapshot_sha() -> None:
    with pytest.raises(ValidationError):
        EmbedIndexerMessage(
            batch_id="other",
            snapshot_sha="sha-e",
            repo_root="/tmp/repo",
            file_path="a.py",
        )
```

Run: `uv run pytest tests/unit/workers/embed_indexer/test_embed_message.py -v` — expect PASS.

- [ ] **Step 5: Write the failing tasks test**

Create `tests/integration/workers/embed_indexer/test_embed_tasks.py`:

```python
"""Pure-function tests for `extract_file_to_embeddings`."""

from __future__ import annotations

import pathlib

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from codeoracle.adapters.llm.providers.mock_embed import MockEmbeddingsProvider
from codeoracle.core.config import EmbeddingConfig
from codeoracle.db.repositories.embeddings import EmbeddingsRepository
from codeoracle.db.repositories.snapshot_file_outcome import SnapshotFileOutcomeRepository
from codeoracle.services.embedding.chunker import LineChunker
from codeoracle.workers.embed_indexer.message import EmbedIndexerMessage
from codeoracle.workers.embed_indexer.tasks import extract_file_to_embeddings


@pytest_asyncio.fixture
async def session(
    async_session_factory: async_sessionmaker[AsyncSession],
) -> AsyncSession:
    async with async_session_factory() as s:
        yield s


@pytest.mark.asyncio
async def test_success_writes_chunks_and_outcome(
    session: AsyncSession, tmp_path: pathlib.Path,
) -> None:
    f = tmp_path / "a.py"
    f.write_text("\n".join(f"line {i}" for i in range(1, 251)) + "\n")
    msg = EmbedIndexerMessage(
        batch_id="sha-e-ok",
        snapshot_sha="sha-e-ok",
        repo_root=str(tmp_path),
        file_path="a.py",
    )
    config = EmbeddingConfig(model="text-embedding-3-small", dim=1536, kind="mock")
    provider = MockEmbeddingsProvider(dim=config.dim)
    chunker = LineChunker(
        chunk_lines=config.embed_chunk_lines,
        overlap=config.embed_chunk_overlap,
    )
    embeddings_repo = EmbeddingsRepository(session)
    outcome_repo = SnapshotFileOutcomeRepository(session)

    await extract_file_to_embeddings(
        message=msg,
        embeddings_provider=provider,
        chunker=chunker,
        embeddings_repo=embeddings_repo,
        outcome_repo=outcome_repo,
        session=session,
        embedding_dim=config.dim,
    )

    hits = await embeddings_repo.knn(
        query_embedding=(await provider.embed(["line 50"]))[0],
        snapshot_sha="sha-e-ok",
        k=10,
    )
    assert len(hits) == 3  # 250 lines / 100 chunk_lines with 20 overlap → 3 chunks
    assert await outcome_repo.count_completed(snapshot_sha="sha-e-ok", extractor="embed") == 1


@pytest.mark.asyncio
async def test_missing_file_marks_failed(
    session: AsyncSession, tmp_path: pathlib.Path,
) -> None:
    msg = EmbedIndexerMessage(
        batch_id="sha-e-miss",
        snapshot_sha="sha-e-miss",
        repo_root=str(tmp_path),
        file_path="does-not-exist.py",
    )
    config = EmbeddingConfig(model="text-embedding-3-small", dim=1536, kind="mock")
    provider = MockEmbeddingsProvider(dim=config.dim)
    chunker = LineChunker(
        chunk_lines=config.embed_chunk_lines, overlap=config.embed_chunk_overlap,
    )
    embeddings_repo = EmbeddingsRepository(session)
    outcome_repo = SnapshotFileOutcomeRepository(session)

    await extract_file_to_embeddings(
        message=msg,
        embeddings_provider=provider,
        chunker=chunker,
        embeddings_repo=embeddings_repo,
        outcome_repo=outcome_repo,
        session=session,
        embedding_dim=config.dim,
    )
    assert await outcome_repo.count_failed(snapshot_sha="sha-e-miss", extractor="embed") == 1


@pytest.mark.asyncio
async def test_empty_file_writes_zero_chunks_and_success(
    session: AsyncSession, tmp_path: pathlib.Path,
) -> None:
    f = tmp_path / "empty.py"
    f.write_text("")
    msg = EmbedIndexerMessage(
        batch_id="sha-e-empty",
        snapshot_sha="sha-e-empty",
        repo_root=str(tmp_path),
        file_path="empty.py",
    )
    config = EmbeddingConfig(model="text-embedding-3-small", dim=1536, kind="mock")
    provider = MockEmbeddingsProvider(dim=config.dim)
    chunker = LineChunker(
        chunk_lines=config.embed_chunk_lines, overlap=config.embed_chunk_overlap,
    )
    embeddings_repo = EmbeddingsRepository(session)
    outcome_repo = SnapshotFileOutcomeRepository(session)

    await extract_file_to_embeddings(
        message=msg,
        embeddings_provider=provider,
        chunker=chunker,
        embeddings_repo=embeddings_repo,
        outcome_repo=outcome_repo,
        session=session,
        embedding_dim=config.dim,
    )
    # No chunks → outcome=success (file attempted).
    assert await outcome_repo.count_completed(snapshot_sha="sha-e-empty", extractor="embed") == 1


@pytest.mark.asyncio
async def test_provider_wrong_dim_marks_failed(
    session: AsyncSession, tmp_path: pathlib.Path,
) -> None:
    f = tmp_path / "a.py"
    f.write_text("line one\n")
    msg = EmbedIndexerMessage(
        batch_id="sha-e-wrongdim",
        snapshot_sha="sha-e-wrongdim",
        repo_root=str(tmp_path),
        file_path="a.py",
    )
    # Provider configured for dim=512 but worker expects 1536.
    provider = MockEmbeddingsProvider(dim=512)
    chunker = LineChunker(chunk_lines=100, overlap=20)
    embeddings_repo = EmbeddingsRepository(session)
    outcome_repo = SnapshotFileOutcomeRepository(session)

    await extract_file_to_embeddings(
        message=msg,
        embeddings_provider=provider,
        chunker=chunker,
        embeddings_repo=embeddings_repo,
        outcome_repo=outcome_repo,
        session=session,
        embedding_dim=1536,
    )
    assert await outcome_repo.count_failed(snapshot_sha="sha-e-wrongdim", extractor="embed") == 1
```

- [ ] **Step 6: Implement the tasks module**

Create `src/codeoracle/workers/embed_indexer/tasks.py`:

```python
"""Pure-function pipeline for per-file embedding extraction.

The consumer's `handle()` method delegates to this function. The consumer
constructs the appropriate chunker per message via `chunker_for(file_path,
embedding_config=...)` (LineChunker for code, ParagraphChunker for docs —
G14) and passes it in via DI. This function only sees the Chunker ABC —
it does not know or care which concrete implementation it received.

Pipeline:
1. Read file as text (UTF-8, errors="replace" — corrupt bytes get replaced
   characters, NOT a hard failure).
2. Split into lines (`text.splitlines(keepends=False)`).
3. Chunk via the injected Chunker (LineChunker for code, ParagraphChunker for docs).
4. Embed all chunk texts in one batch call (efficient — single HTTP round-trip).
5. Validate every returned vector has length == embedding_dim.
6. `EmbeddingsRepository.replace_file_embeddings(...)` (atomic delete + insert).
7. `SnapshotFileOutcomeRepository.upsert(outcome=success)`.
8. Commit.

On any error: rollback + write outcome=failed (with truncated message).
TransientWorkerError from the provider (rate limit, 5xx) is re-raised so the
WorkerConsumer's retry path picks it up.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession

from codeoracle.adapters.llm.embeddings import EmbeddingsProvider
from codeoracle.core.exceptions import (
    EmbedExtractionError,
    TransientWorkerError,
)
from codeoracle.db.repositories.embeddings import EmbeddingChunk, EmbeddingsRepository
from codeoracle.db.repositories.snapshot_file_outcome import (
    SnapshotFileOutcomeRepository,
    SnapshotFileOutcomeRow,
)
from codeoracle.services.embedding.chunker import Chunker
from codeoracle.workers.embed_indexer.message import EmbedIndexerMessage


_LOG = logging.getLogger(__name__)


async def extract_file_to_embeddings(
    *,
    message: EmbedIndexerMessage,
    embeddings_provider: EmbeddingsProvider,
    chunker: Chunker,
    embeddings_repo: EmbeddingsRepository,
    outcome_repo: SnapshotFileOutcomeRepository,
    session: AsyncSession,
    embedding_dim: int,
) -> None:
    abs_path = Path(message.repo_root) / message.file_path

    # 1. Read file.
    try:
        text = abs_path.read_text(encoding="utf-8", errors="replace")
    except FileNotFoundError as e:
        _LOG.error("embed: file not found: %s", abs_path)
        await outcome_repo.upsert(
            SnapshotFileOutcomeRow(
                snapshot_sha=message.snapshot_sha,
                file_path=message.file_path,
                extractor="embed",
                outcome="failed",
                error_message=f"file not found: {abs_path}",
            )
        )
        await session.commit()
        return
    except OSError as e:
        _LOG.exception("embed: file read error for %s", abs_path)
        await outcome_repo.upsert(
            SnapshotFileOutcomeRow(
                snapshot_sha=message.snapshot_sha,
                file_path=message.file_path,
                extractor="embed",
                outcome="failed",
                error_message=f"file read: {str(e)[:512]}",
            )
        )
        await session.commit()
        return

    lines = text.splitlines()
    chunk_records = chunker.chunk(lines)

    # 2. Embed.
    if chunk_records:
        try:
            vectors = await embeddings_provider.embed([c.text for c in chunk_records])
        except TransientWorkerError:
            # Re-raise so WorkerConsumer retries via the reclaim ladder.
            raise
        except EmbedExtractionError as e:
            _LOG.exception("embed: provider terminal error")
            await outcome_repo.upsert(
                SnapshotFileOutcomeRow(
                    snapshot_sha=message.snapshot_sha,
                    file_path=message.file_path,
                    extractor="embed",
                    outcome="failed",
                    error_message=f"embed provider: {str(e)[:512]}",
                )
            )
            await session.commit()
            return
        except Exception as e:
            _LOG.exception("embed: unexpected provider error")
            await outcome_repo.upsert(
                SnapshotFileOutcomeRow(
                    snapshot_sha=message.snapshot_sha,
                    file_path=message.file_path,
                    extractor="embed",
                    outcome="failed",
                    error_message=f"embed unexpected: {str(e)[:512]}",
                )
            )
            await session.commit()
            return

        # 3. Validate dims.
        if len(vectors) != len(chunk_records):
            await outcome_repo.upsert(
                SnapshotFileOutcomeRow(
                    snapshot_sha=message.snapshot_sha,
                    file_path=message.file_path,
                    extractor="embed",
                    outcome="failed",
                    error_message=(
                        f"provider returned {len(vectors)} vectors for "
                        f"{len(chunk_records)} chunks"
                    ),
                )
            )
            await session.commit()
            return

        for i, vec in enumerate(vectors):
            if len(vec) != embedding_dim:
                await outcome_repo.upsert(
                    SnapshotFileOutcomeRow(
                        snapshot_sha=message.snapshot_sha,
                        file_path=message.file_path,
                        extractor="embed",
                        outcome="failed",
                        error_message=(
                            f"vector {i} dim={len(vec)} expected={embedding_dim}"
                        ),
                    )
                )
                await session.commit()
                return

        embedding_chunks = [
            EmbeddingChunk(
                snapshot_sha=message.snapshot_sha,
                source_path=message.file_path,
                chunk_start_line=c.start_line,
                chunk_end_line=c.end_line,
                content=c.text,
                embedding=vectors[i],
            )
            for i, c in enumerate(chunk_records)
        ]
    else:
        embedding_chunks = []

    # 4. Write.
    try:
        await embeddings_repo.replace_file_embeddings(
            snapshot_sha=message.snapshot_sha,
            source_path=message.file_path,
            chunks=embedding_chunks,
        )
        await outcome_repo.upsert(
            SnapshotFileOutcomeRow(
                snapshot_sha=message.snapshot_sha,
                file_path=message.file_path,
                extractor="embed",
                outcome="success",
                error_message=None,
            )
        )
        await session.commit()
    except Exception as e:
        await session.rollback()
        await outcome_repo.upsert(
            SnapshotFileOutcomeRow(
                snapshot_sha=message.snapshot_sha,
                file_path=message.file_path,
                extractor="embed",
                outcome="failed",
                error_message=f"db write: {str(e)[:512]}",
            )
        )
        await session.commit()
        raise
```

- [ ] **Step 7: Run tasks test to verify it passes**

Run: `uv run pytest tests/integration/workers/embed_indexer/test_embed_tasks.py -v`

Expected: PASS (4 tests).

- [ ] **Step 8: Implement the consumer**

Create `src/codeoracle/workers/embed_indexer/consumer.py`:

```python
"""EmbedIndexerConsumer — per-file WorkerConsumer.

Stream: indexing.embed
Group:  embed-indexer
count_per_poll = 5 (per-file embed is bottlenecked by the provider; sibling
workers can parallelize across files).

Chunker dispatch (G14): the consumer carries the EmbeddingConfig and calls
`chunker_for(message.file_path, embedding_config=...)` per message — code
files get LineChunker, doc files get ParagraphChunker. Both chunker classes
are pure / cheap to construct; we do not cache them on `self` because the
config is the only state and is already cached.
"""

from __future__ import annotations

import asyncio

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from codeoracle.adapters.llm.embeddings import EmbeddingsProvider
from codeoracle.adapters.valkey.client import ValkeyQueueClient
from codeoracle.core.config import EmbeddingConfig
from codeoracle.db.repositories.embeddings import EmbeddingsRepository
from codeoracle.db.repositories.snapshot_file_outcome import SnapshotFileOutcomeRepository
from codeoracle.services.embedding.chunker import chunker_for
from codeoracle.workers._base.consumer import WorkerConsumer
from codeoracle.workers._base.message import RetryPolicy
from codeoracle.workers.embed_indexer.message import EmbedIndexerMessage
from codeoracle.workers.embed_indexer.tasks import extract_file_to_embeddings
from codeoracle.core.streams import GROUP_EMBED_INDEXER, STREAM_INDEXING_EMBED


class EmbedIndexerConsumer(WorkerConsumer[EmbedIndexerMessage]):
    stream = STREAM_INDEXING_EMBED
    group = GROUP_EMBED_INDEXER
    message_type = EmbedIndexerMessage
    count_per_poll = 5
    block_ms = 5000

    def __init__(
        self,
        *,
        queue_client: ValkeyQueueClient,
        retry_policy: RetryPolicy,
        consumer_name: str,
        stop_event: asyncio.Event,
        embeddings_provider: EmbeddingsProvider,
        embedding_config: EmbeddingConfig,
        db_session_factory: async_sessionmaker[AsyncSession],
    ) -> None:
        super().__init__(
            queue_client=queue_client,
            retry_policy=retry_policy,
            consumer_name=consumer_name,
            stop_event=stop_event,
            # reclaim_min_idle_ms intentionally omitted → the inherited reaper
            # stays OFF for this stream: a handle embeds N chunks bounded only by
            # the provider's per-request HTTP timeout (no per-handle ceiling), so
            # min-idle can't be sized > handle by construction the way Joern's is.
            # Safe activation is gated on a per-handle bound (banked
            # unbounded-subprocess discovery).
        )
        self._provider = embeddings_provider
        self._embedding_config = embedding_config
        self._db_factory = db_session_factory

    async def handle(self, message: EmbedIndexerMessage) -> None:
        # G14: dispatch chunker per message — code files get LineChunker,
        # doc files (.md, .rst, .adoc) get ParagraphChunker. The Chunker ABC
        # erases the difference for the downstream task function.
        chunker = chunker_for(message.file_path, embedding_config=self._embedding_config)
        async with self._db_factory() as session:
            await extract_file_to_embeddings(
                message=message,
                embeddings_provider=self._provider,
                chunker=chunker,
                embeddings_repo=EmbeddingsRepository(session),
                outcome_repo=SnapshotFileOutcomeRepository(session),
                session=session,
                embedding_dim=self._embedding_config.dim,
            )
```

- [ ] **Step 9: Write consumer test**

Create `tests/integration/workers/embed_indexer/test_embed_consumer.py`:

```python
"""EmbedIndexerConsumer — handle() wiring sanity, including G14 chunker dispatch."""

from __future__ import annotations

import asyncio
import pathlib

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from codeoracle.adapters.llm.providers.mock_embed import MockEmbeddingsProvider
from codeoracle.core.config import EmbeddingConfig
from codeoracle.db.repositories.embeddings import EmbeddingsRepository
from codeoracle.workers._base.message import RetryPolicy
from codeoracle.workers.embed_indexer.consumer import EmbedIndexerConsumer
from codeoracle.workers.embed_indexer.message import EmbedIndexerMessage


def _make_consumer(
    *,
    queue_client,
    db_session_factory: async_sessionmaker[AsyncSession],
    config: EmbeddingConfig,
) -> EmbedIndexerConsumer:
    return EmbedIndexerConsumer(
        queue_client=queue_client,
        retry_policy=RetryPolicy(
            max_attempts=2,
        ),
        consumer_name="embed-test",
        stop_event=asyncio.Event(),
        embeddings_provider=MockEmbeddingsProvider(dim=config.dim),
        embedding_config=config,
        db_session_factory=db_session_factory,
    )


@pytest.mark.asyncio
async def test_consumer_handle_runs_pipeline_for_code_file(
    async_session_factory: async_sessionmaker[AsyncSession],
    valkey_queue_client,
    tmp_path: pathlib.Path,
) -> None:
    """Code file (.py) → LineChunker dispatch → at least one chunk row."""
    (tmp_path / "a.py").write_text("def foo(): pass\n")
    config = EmbeddingConfig(model="text-embedding-3-small", dim=1536, kind="mock")
    consumer = _make_consumer(
        queue_client=valkey_queue_client,
        db_session_factory=async_session_factory,
        config=config,
    )
    msg = EmbedIndexerMessage(
        batch_id="sha-ec-py",
        snapshot_sha="sha-ec-py",
        repo_root=str(tmp_path),
        file_path="a.py",
    )
    await consumer.handle(msg)

    async with async_session_factory() as session:
        repo = EmbeddingsRepository(session)
        provider = MockEmbeddingsProvider(dim=config.dim)
        hits = await repo.knn(
            query_embedding=(await provider.embed(["foo"]))[0],
            snapshot_sha="sha-ec-py",
            k=5,
        )
        assert len(hits) >= 1


@pytest.mark.asyncio
async def test_consumer_handle_dispatches_paragraph_chunker_for_markdown(
    async_session_factory: async_sessionmaker[AsyncSession],
    valkey_queue_client,
    tmp_path: pathlib.Path,
) -> None:
    """G14: markdown file → ParagraphChunker dispatch → paragraph-bounded chunks.

    The doc file has three paragraphs (heading, intro, second), separated
    by blank lines; ParagraphChunker emits one chunk per paragraph (3 rows).
    LineChunker dispatch would have emitted a single chunk covering all 6
    lines — the row count + paragraph-bounded spans distinguish the two paths.

    Uses EmbeddingsRepository.knn (Task 6) to read back all rows for the
    snapshot — with MockEmbeddingsProvider's stable vectors and k larger
    than the chunk count, knn returns every row regardless of query content.
    """
    md_path = tmp_path / "README.md"
    md_path.write_text("# Title\n\nFirst paragraph line 1.\nFirst paragraph line 2.\n\nSecond paragraph.\n")
    config = EmbeddingConfig(model="text-embedding-3-small", dim=1536, kind="mock")
    consumer = _make_consumer(
        queue_client=valkey_queue_client,
        db_session_factory=async_session_factory,
        config=config,
    )
    msg = EmbedIndexerMessage(
        batch_id="sha-ec-md",
        snapshot_sha="sha-ec-md",
        repo_root=str(tmp_path),
        file_path="README.md",
    )
    await consumer.handle(msg)

    async with async_session_factory() as session:
        repo = EmbeddingsRepository(session)
        provider = MockEmbeddingsProvider(dim=config.dim)
        hits = await repo.knn(
            query_embedding=(await provider.embed(["anything"]))[0],
            snapshot_sha="sha-ec-md",
            k=10,
        )
        # Three paragraphs → three chunks → three rows.
        assert len(hits) == 3
        # Each row's (start_line, end_line) is paragraph-bounded, NOT a
        # fixed 100-line window — variable spans prove ParagraphChunker ran.
        spans = sorted((h.chunk_start_line, h.chunk_end_line) for h in hits)
        assert spans == [(1, 1), (3, 4), (6, 6)]
```

- [ ] **Step 10: Implement the worker entrypoint**

Create `src/codeoracle/workers/embed_indexer/__main__.py`:

```python
"""Embed indexer worker entrypoint — `python -m codeoracle.workers.embed_indexer`."""

from __future__ import annotations

import asyncio
import logging
import signal

from codeoracle.adapters.llm.embeddings import EmbeddingsProviderFactory
from codeoracle.adapters.valkey.client import ValkeyQueueClient
from codeoracle.core.config import Settings
from codeoracle.db.session import make_async_engine, make_async_session_factory
from codeoracle.workers._base.message import RetryPolicy
from codeoracle.workers.embed_indexer.consumer import EmbedIndexerConsumer


_LOG = logging.getLogger(__name__)


async def _main() -> None:
    logging.basicConfig(level=logging.INFO)
    settings = Settings()
    queue_client = ValkeyQueueClient.from_settings(settings)
    # Plan 3 / Plan 5 contract: make_async_engine() reads Settings internally
    # via get_settings(); make_async_session_factory(engine) binds the factory.
    engine = make_async_engine()
    db_factory = make_async_session_factory(engine)
    provider = EmbeddingsProviderFactory.build(settings)

    if provider.dim != settings.embedding.dim:
        _LOG.error(
            "embeddings provider dim=%d but EmbeddingConfig.dim=%d — refusing to start",
            provider.dim, settings.embedding.dim,
        )
        return

    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop_event.set)

    # G14: EmbedIndexerConsumer holds the EmbeddingConfig and dispatches
    # chunker_for(file_path) per message — code files get LineChunker,
    # doc files get ParagraphChunker. No chunker instance is constructed
    # here at startup; the consumer builds one per message.
    consumer = EmbedIndexerConsumer(
        queue_client=queue_client,
        retry_policy=RetryPolicy(
            max_attempts=3,
        ),
        consumer_name=f"embed-{settings.worker_instance_id}",
        stop_event=stop_event,
        embeddings_provider=provider,
        embedding_config=settings.embedding,
        db_session_factory=db_factory,
    )

    _LOG.info("embed-indexer worker starting")
    try:
        await consumer.run()
    finally:
        # If the provider is OpenAIEmbeddingsProvider, close its httpx client.
        close = getattr(provider, "aclose", None)
        if close is not None:
            await close()
    _LOG.info("embed-indexer worker stopped")


if __name__ == "__main__":
    asyncio.run(_main())
```

- [ ] **Step 11: Create __init__.py**

Create `src/codeoracle/workers/embed_indexer/__init__.py`:

```python
"""Embed indexer worker — per-file embedding extraction with internal chunking.

Consumes `indexing.embed` stream; one message per (snapshot, file); writes
N chunk rows to `embeddings` + one outcome row per consumed message.
"""

from codeoracle.workers.embed_indexer.consumer import EmbedIndexerConsumer
from codeoracle.workers.embed_indexer.message import EmbedIndexerMessage

__all__ = ["EmbedIndexerConsumer", "EmbedIndexerMessage"]
```

- [ ] **Step 12: Run all embed worker tests**

Run: `uv run pytest tests/unit/workers/embed_indexer/ tests/integration/workers/embed_indexer/ tests/unit/services/embedding/ -v`

Expected: PASS.

- [ ] **Step 13: Commit**

```bash
git add tests/unit/services/embedding/ src/codeoracle/services/embedding/ \
        tests/unit/workers/embed_indexer/ tests/integration/workers/embed_indexer/ src/codeoracle/workers/embed_indexer/
git commit -m "feat(workers): embed-indexer (per-file + chunking) (Plan 6 Task 12)"
```

---

### Task 13: Wire IndexingService Default to Include All Four Kinds + Three-Kind Integration Test (joern integration covered by Plan 5)

**Files:**
- Modify: `src/codeoracle/services/indexing/service.py` (Plan 5; change `_extractor_kinds` default)
- Test: `tests/unit/services/indexing/test_service_default_kinds.py` (new)
- Test: `tests/integration/services/indexing/test_service_integration_three_kinds.py` (new)

**The change is small but load-bearing:**

```python
# Plan 5:
DEFAULT_EXTRACTOR_KINDS: tuple[str, ...] = ("joern",)

# Plan 6:
DEFAULT_EXTRACTOR_KINDS: tuple[str, ...] = ("joern", "scip", "opengrep", "embed")
```

Reviewers MUST see this as an intentional API extension; the `IndexingService` constructor's `extractor_kinds` parameter (if it exists from Plan 5) still allows callers to override.

The integration test orchestrates **the three NEW workers introduced by Plan 6** (scip, opengrep, embed) end-to-end against testcontainers PG+AGE+Valkey, with mocked subprocesses for scip-python and opengrep (so the test runs without those binaries installed) and the `MockEmbeddingsProvider`.

**Scope decision — why three, not four:** Plan 5 ships its own integration test for `JoernIndexerConsumer` covering the joern wiring end-to-end. Re-asserting joern here would either duplicate Plan 5's test or hand-roll a fragile copy of its consumer construction. The Plan 6 integration test proves the THREE new workers fan out and the barrier closes for the three new extractor kinds; joern's barrier behavior is unchanged from Plan 5 and is regression-tested by Plan 5's suite. The Task 14 E2E test (next) also exercises the three new extractors end-to-end against live binaries.

- [ ] **Step 1: Write the failing default-kinds test**

Create `tests/unit/services/indexing/test_service_default_kinds.py`:

```python
"""Plan 6 changes IndexingService's default extractor_kinds.

This pins the public contract: a caller constructing IndexingService with no
explicit kinds gets all four extractors fanned out. Plan 7 may add a 5th
("traces") if traces are added to the snapshot-keyed barrier; the current
expectation is that traces are CONTINUOUS and do NOT join this set.
"""

from codeoracle.services.indexing.service import (
    DEFAULT_EXTRACTOR_KINDS,
    IndexingService,
)


def test_default_includes_all_four() -> None:
    assert set(DEFAULT_EXTRACTOR_KINDS) == {"joern", "scip", "opengrep", "embed"}


def test_default_does_not_include_traces() -> None:
    """Plan 7 ContinuousConsumer + traces_ingest is out of scope for the
    snapshot-keyed barrier. Asserting absence pins the design."""
    assert "traces" not in DEFAULT_EXTRACTOR_KINDS
    assert "sourcebot" not in DEFAULT_EXTRACTOR_KINDS
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/services/indexing/test_service_default_kinds.py -v`

Expected: FAIL — Plan 5's default is `("joern",)`.

- [ ] **Step 3: Update the default**

Modify `src/codeoracle/services/indexing/service.py`. Locate the existing `DEFAULT_EXTRACTOR_KINDS` constant (Plan 5) and change it:

```python
# Plan 5: DEFAULT_EXTRACTOR_KINDS = ("joern",)
# Plan 6: extend to the four snapshot-keyed extractors. Traces (continuous)
# is intentionally absent — Plan 7 owns it via a separate consumer base class
# (ContinuousConsumer) that does NOT participate in the per-file barrier.
DEFAULT_EXTRACTOR_KINDS: tuple[str, ...] = ("joern", "scip", "opengrep", "embed")
```

- [ ] **Step 4: Verify default-kinds test passes**

Run: `uv run pytest tests/unit/services/indexing/test_service_default_kinds.py -v`

Expected: PASS (2 tests).

- [ ] **Step 5: Write the three-kinds integration test (mocked subprocess + Mock embeddings)**

Create `tests/integration/services/indexing/test_service_integration_three_kinds.py`:

```python
"""IndexingService end-to-end with the three NEW Plan 6 extractor kinds active.

This is a barrier-close test for the three workers Plan 6 introduces. The
fourth kind (joern) is covered by Plan 5's own integration test; including
it here would duplicate that coverage or hand-roll a fragile copy of Plan 5's
consumer construction. The change to `DEFAULT_EXTRACTOR_KINDS` is asserted
separately in `test_service_default_kinds.py`; this test validates the
barrier-close behavior the three new kinds exercise together.

Fixtures used:
- testcontainers PG+AGE+Valkey (real)
- Mocked ScipClient (in-test fake that emits a small ScipParseResult)
- Mocked OpengrepRunner (returns empty matches — barrier still closes)
- MockEmbeddingsProvider (deterministic vectors)

Live SCIP + OpenGrep binary exercise is the E2E test (Task 14, next).
"""

from __future__ import annotations

import asyncio
import pathlib
from unittest.mock import AsyncMock, MagicMock

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from codeoracle.adapters.llm.providers.mock_embed import MockEmbeddingsProvider
from codeoracle.adapters.opengrep.runner import OpengrepRunner
from codeoracle.adapters.scip.client import ScipClient
from codeoracle.adapters.scip.parser import ScipDefinition, ScipIndexParser, ScipParseResult
from codeoracle.adapters.valkey.client import ValkeyQueueClient
from codeoracle.core.config import EmbeddingConfig, Settings
from codeoracle.db.repositories.snapshot_file_outcome import SnapshotFileOutcomeRepository
from codeoracle.services.indexing.orchestrator import IndexingOrchestrator
from codeoracle.services.indexing.service import IndexingService
from codeoracle.workers._base.message import RetryPolicy
from codeoracle.workers.embed_indexer.consumer import EmbedIndexerConsumer
from codeoracle.workers.opengrep_runner.consumer import OpengrepRunnerConsumer
from codeoracle.workers.scip_indexer.consumer import ScipIndexerConsumer


@pytest.mark.asyncio
async def test_three_new_kinds_fan_out_and_barrier_closes(
    async_session_factory: async_sessionmaker[AsyncSession],
    valkey_queue_client: ValkeyQueueClient,
    tmp_path: pathlib.Path,
) -> None:
    # Fixtures: tiny repo with 2 Python files.
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "a.py").write_text("def foo(): pass\n")
    (repo / "b.py").write_text("def bar(): pass\n")
    snapshot_sha = "sha-3kinds"
    files = ["a.py", "b.py"]

    # Build the SCIP fake — returns a valid ScipParseResult covering only a.py.
    scip_client = MagicMock(spec=ScipClient)
    scip_client.index_project = AsyncMock(return_value=None)
    scip_client.print_index = AsyncMock(return_value="scip-text")
    scip_parser = MagicMock(spec=ScipIndexParser)
    scip_parser.parse = MagicMock(return_value=ScipParseResult(
        definitions=[ScipDefinition(file_path="a.py", fqn="`a.py`/foo()", line_number=1, end_line=1)],
        references=[],
        files_covered={"a.py"},
    ))

    # OpenGrep fake — empty matches.
    og_runner = MagicMock(spec=OpengrepRunner)
    og_runner.scan_file = AsyncMock(return_value=[])

    config = EmbeddingConfig(model="text-embedding-3-small", dim=1536, kind="mock")
    embed_provider = MockEmbeddingsProvider(dim=config.dim)

    stop_event = asyncio.Event()

    # Construct each of the three new Plan 6 consumers.
    scip_consumer = ScipIndexerConsumer(
        queue_client=valkey_queue_client,
        retry_policy=RetryPolicy(max_attempts=2),
        consumer_name="scip-integ",
        stop_event=stop_event,
        scip_client=scip_client,
        parser=scip_parser,
        db_session_factory=async_session_factory,
    )
    og_consumer = OpengrepRunnerConsumer(
        queue_client=valkey_queue_client,
        retry_policy=RetryPolicy(max_attempts=2),
        consumer_name="og-integ",
        stop_event=stop_event,
        runner=og_runner,
        db_session_factory=async_session_factory,
    )
    embed_consumer = EmbedIndexerConsumer(
        queue_client=valkey_queue_client,
        retry_policy=RetryPolicy(max_attempts=2),
        consumer_name="embed-integ",
        stop_event=stop_event,
        embeddings_provider=embed_provider,
        embedding_config=config,  # G14: consumer dispatches chunker_for per message
        db_session_factory=async_session_factory,
    )

    # Start the three Plan 6 consumers concurrently.
    consumer_tasks = [
        asyncio.create_task(scip_consumer.run()),
        asyncio.create_task(og_consumer.run()),
        asyncio.create_task(embed_consumer.run()),
    ]

    # Build the orchestrator, kick off a reindex with the three new kinds only.
    orchestrator = IndexingOrchestrator(
        queue_client=valkey_queue_client,
        db_session_factory=async_session_factory,
        poll_interval_seconds=0.2,
        max_wait_seconds=30.0,
    )
    outcomes = await orchestrator.fan_out_and_wait(
        snapshot_sha=snapshot_sha,
        repo_root=str(repo),
        files=files,
        extractor_kinds=("scip", "opengrep", "embed"),
    )

    # Stop workers.
    stop_event.set()
    for t in consumer_tasks:
        try:
            await asyncio.wait_for(t, timeout=2.0)
        except asyncio.TimeoutError:
            t.cancel()

    # Assertions: each extractor produced an outcome status.
    assert set(outcomes.keys()) == {"scip", "opengrep", "embed"}
    for kind, status in outcomes.items():
        assert status in ("success", "partial", "failed"), f"{kind}={status}"
    # SCIP, OpenGrep, embed all returned success (mocks happy).
    assert outcomes["scip"] == "success"
    assert outcomes["opengrep"] == "success"
    assert outcomes["embed"] == "success"
```

- [ ] **Step 6: Run integration test to verify it passes**

Run: `uv run pytest tests/integration/services/indexing/test_service_integration_three_kinds.py -v`

Expected: PASS.

- [ ] **Step 7: Run full IndexingService test suite to verify Plan 5 + Plan 6 coexist**

Run: `uv run pytest tests/unit/services/indexing/ tests/integration/services/indexing/ -v`

Expected: all green.

- [ ] **Step 8: Commit**

```bash
git add tests/unit/services/indexing/test_service_default_kinds.py \
        tests/integration/services/indexing/test_service_integration_three_kinds.py \
        src/codeoracle/services/indexing/service.py
git commit -m "feat(indexing): default to 4 extractor kinds + 3-kind integration test (Plan 6 Task 13)"
```

---

### Task 14: E2E Vertical Slice — Full 3-Extractor Reindex with Live Binaries (joern E2E in Plan 5)

**Files:**
- Create: `tests/e2e/test_full_extraction_pipeline.py` (gated by `--run-e2e` flag)
- Modify: `tests/conftest.py` (add `--run-e2e` if not already present)

**Goal:** Run the FULL Plan 6 stack — testcontainers with PG+AGE + Valkey + real scip-python + opengrep binaries available on PATH — against a TINY fixture project. Verify the three NEW Plan 6 extractors all produce real artifacts and the barrier closes for the three-kind dispatch. (Plan 5 ships its own joern E2E; the 4-kind end-to-end with all extractors live is exercised by Plan 13's E2E vertical slice.)

This test must produce:

- ≥ 1 `:Definition` node in AGE from SCIP (Plan 6)
- ≥ 1 row in `code_references` from OpenGrep (Plan 6)
- ≥ 1 row in `embeddings` from embed (Plan 6, using either MockEmbeddingsProvider OR live `--run-live-embeddings` if available)
- Snapshot status transitions APPLYING → APPLIED for the three-kind dispatch (Plan 3's `SnapshotStatus` terminal success member is `APPLIED`)
- All outcomes rows present (per-file barrier closed correctly for scip/opengrep/embed)

**Environment:** The test assumes scip-python + opengrep + scip CLIs are on PATH (Plan 6's preflight verifies). If they aren't, the test is SKIPPED (not failed) — local devs without the binaries shouldn't be blocked.

- [ ] **Step 1: Add the `--run-e2e` flag to conftest if missing**

Modify `tests/conftest.py`:

```python
def pytest_addoption(parser):
    # Plan 4: --run-live-llm
    # Plan 6: --run-live-embeddings (Task 7)
    # Plan 6: --run-e2e (this task)
    parser.addoption("--run-e2e", action="store_true", default=False,
                     help="Run E2E tests (requires scip-python + opengrep on PATH).")


def pytest_collection_modifyitems(config, items):
    if not config.getoption("--run-e2e"):
        skip_e2e = pytest.mark.skip(reason="needs --run-e2e")
        for item in items:
            if "e2e" in item.keywords:
                item.add_marker(skip_e2e)
```

(Merge with existing pytest_addoption / pytest_collection_modifyitems if they exist; don't redefine.)

- [ ] **Step 2: Write the E2E test**

Create `tests/e2e/test_full_extraction_pipeline.py`:

```python
"""E2E: full 3-extractor (Plan 6) reindex against a tiny live fixture.

Skipped without --run-e2e. Requires on PATH:
- scip-python
- scip
- opengrep
(Plan 5 ships joern's own E2E; the 4-kind end-to-end with joern + Plan 6 is
exercised by Plan 13's full E2E vertical slice.)

Embeddings: uses MockEmbeddingsProvider by default. If --run-live-embeddings
is also passed, attempts to use the configured live provider; falls back to
Mock if env vars are missing.
"""

from __future__ import annotations

import asyncio
import os
import pathlib
import shutil

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from codeoracle.adapters.llm.providers.mock_embed import MockEmbeddingsProvider
from codeoracle.adapters.opengrep import DEFAULT_RULES_DIR
from codeoracle.adapters.opengrep.runner import OpengrepRunner
from codeoracle.adapters.scip.client import ScipClient
from codeoracle.adapters.scip.parser import ScipIndexParser
from codeoracle.adapters.valkey.client import ValkeyQueueClient
from codeoracle.core.config import EmbeddingConfig
from codeoracle.db.repositories.code_references import CodeReferencesRepository
from codeoracle.db.repositories.embeddings import EmbeddingsRepository
from codeoracle.db.repositories.scip_graph import ScipGraphRepository
from codeoracle.db.repositories.snapshot_file_outcome import SnapshotFileOutcomeRepository
from codeoracle.services.indexing.orchestrator import IndexingOrchestrator
from codeoracle.workers._base.message import RetryPolicy
from codeoracle.workers.embed_indexer.consumer import EmbedIndexerConsumer
from codeoracle.workers.opengrep_runner.consumer import OpengrepRunnerConsumer
from codeoracle.workers.scip_indexer.consumer import ScipIndexerConsumer


pytestmark = [
    pytest.mark.e2e,
    pytest.mark.skipif(
        shutil.which("scip-python") is None
        or shutil.which("scip") is None
        or shutil.which("opengrep") is None,
        reason="E2E needs scip-python + scip + opengrep on PATH",
    ),
]


@pytest.mark.asyncio
async def test_full_3_extractor_pipeline(
    async_session_factory: async_sessionmaker[AsyncSession],
    valkey_queue_client: ValkeyQueueClient,
    tmp_path: pathlib.Path,
) -> None:
    """End-to-end: 2-file Python project goes through scip, opengrep, embed.
    Joern is intentionally NOT in this E2E — Plan 5's E2E covers it; Plan 13's
    final vertical slice covers the 4-extractor live combination."""
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "main.py").write_text(
        "def greet(name):\n    return f\"hello {name}\"\n\n\nprint(greet(\"world\"))\n"
    )
    (repo / "helpers.py").write_text(
        "from main import greet\n\n\ndef shout():\n    return greet(\"loud\").upper()\n"
    )
    snapshot_sha = "e2e-sha-001"
    files = ["main.py", "helpers.py"]
    config = EmbeddingConfig(model="text-embedding-3-small", dim=1536, kind="mock")

    stop_event = asyncio.Event()

    # Build real adapters.
    scip_consumer = ScipIndexerConsumer(
        queue_client=valkey_queue_client,
        retry_policy=RetryPolicy(max_attempts=2),
        consumer_name="scip-e2e",
        stop_event=stop_event,
        scip_client=ScipClient(),
        parser=ScipIndexParser(),
        db_session_factory=async_session_factory,
    )
    og_consumer = OpengrepRunnerConsumer(
        queue_client=valkey_queue_client,
        retry_policy=RetryPolicy(max_attempts=2),
        consumer_name="og-e2e",
        stop_event=stop_event,
        runner=OpengrepRunner(rules_dir=DEFAULT_RULES_DIR),
        db_session_factory=async_session_factory,
    )
    embed_consumer = EmbedIndexerConsumer(
        queue_client=valkey_queue_client,
        retry_policy=RetryPolicy(max_attempts=2),
        consumer_name="embed-e2e",
        stop_event=stop_event,
        embeddings_provider=MockEmbeddingsProvider(dim=config.dim),
        embedding_config=config,  # G14: consumer dispatches chunker_for per message
        db_session_factory=async_session_factory,
    )

    # Start consumers concurrently.
    tasks = [
        asyncio.create_task(scip_consumer.run()),
        asyncio.create_task(og_consumer.run()),
        asyncio.create_task(embed_consumer.run()),
    ]

    # Fan out a reindex (joern omitted in this E2E because Plan 5's joern is
    # tested in its own E2E; Plan 6 focuses on its three additions).
    orchestrator = IndexingOrchestrator(
        queue_client=valkey_queue_client,
        db_session_factory=async_session_factory,
        poll_interval_seconds=0.5,
        max_wait_seconds=120.0,  # SCIP project-mode can be slow on cold cache
    )
    outcomes = await orchestrator.fan_out_and_wait(
        snapshot_sha=snapshot_sha,
        repo_root=str(repo),
        files=files,
        extractor_kinds=("scip", "opengrep", "embed"),
    )

    # Stop consumers.
    stop_event.set()
    for t in tasks:
        try:
            await asyncio.wait_for(t, timeout=3.0)
        except asyncio.TimeoutError:
            t.cancel()

    # Outcomes: all three extractors succeeded (best-case).
    assert outcomes["scip"] in ("success", "partial"), f"scip outcome={outcomes['scip']!r}"
    assert outcomes["opengrep"] in ("success", "partial"), f"opengrep outcome={outcomes['opengrep']!r}"
    assert outcomes["embed"] in ("success", "partial"), f"embed outcome={outcomes['embed']!r}"

    # Per-extractor evidence: actual artifacts written.
    async with async_session_factory() as session:
        scip_repo = ScipGraphRepository(session)
        og_repo = CodeReferencesRepository(session)
        embed_repo = EmbeddingsRepository(session)
        outcome_repo = SnapshotFileOutcomeRepository(session)

        # SCIP: at least one :Definition (greet, shout) somewhere.
        total_defs = sum([
            await scip_repo.count_definitions(snapshot_sha=snapshot_sha, file_path=f)
            for f in files
        ])
        assert total_defs >= 1, "expected at least one :Definition node from SCIP"

        # OpenGrep: at least one code reference (e.g., import of greet).
        # We don't assert exact count because OpenGrep rule patterns may evolve.
        n_greet = await og_repo.count_by_symbol(symbol_name="greet", snapshot_sha=snapshot_sha)
        assert n_greet >= 1, "expected at least one OpenGrep match for 'greet'"

        # Embed: at least one embedding row per file.
        # We KNN against MockEmbeddingsProvider's vector for the file content;
        # the file IS in the index, so the top hit's source_path matches.
        provider = MockEmbeddingsProvider(dim=config.dim)
        query_vec = (await provider.embed(["greet"]))[0]
        hits = await embed_repo.knn(query_embedding=query_vec, snapshot_sha=snapshot_sha, k=10)
        assert len(hits) >= 2, "expected at least 2 embedding chunks across the 2-file project"

        # Outcome counts: 2 files × 3 extractors = 6 rows, all success.
        for ext in ("scip", "opengrep", "embed"):
            completed = await outcome_repo.count_completed(snapshot_sha=snapshot_sha, extractor=ext)
            failed = await outcome_repo.count_failed(snapshot_sha=snapshot_sha, extractor=ext)
            assert completed + failed == 2, f"{ext}: expected 2 outcome rows, got {completed + failed}"
            assert failed == 0, f"{ext}: unexpected failures: {failed}"
```

- [ ] **Step 3: Verify the E2E test is skipped without flag**

Run: `uv run pytest tests/e2e/test_full_extraction_pipeline.py -v`

Expected: SKIPPED (1 test, skipped due to `--run-e2e` missing OR missing binaries on PATH).

- [ ] **Step 4: Verify the E2E test PASSES when flag is on AND binaries are present**

This step is informational; CI either has the binaries or it doesn't. If the implementer's local environment has them:

Run: `uv run pytest tests/e2e/test_full_extraction_pipeline.py -v --run-e2e`

Expected: PASS (1 test, ~60s wall-clock).

- [ ] **Step 5: Run the entire test suite to confirm Plan 6 introduces no regressions**

Run: `uv run pytest -v --tb=short`

Expected: all green (E2E + live-embeddings still skipped by default; everything else green).

- [ ] **Step 6: Commit**

```bash
git add tests/e2e/test_full_extraction_pipeline.py tests/conftest.py
git commit -m "test(e2e): full 3-extractor pipeline E2E with live SCIP+OpenGrep (Plan 6 Task 14)"
```

- [ ] **Step 7: Push the branch**

```bash
git push origin <branch>
```

(After the final commit; final approval gate is the PR review with the live E2E green in CI.)

---

## Self-Review Checklist (run after writing — performed at plan-write time, not during execution)

1. **Spec coverage check:**
   - §4.1 service map `sourcebot` — listed in the spec as a *service* (HTTP span-fetch dependency), not a worker; row 9 of §4.1 since the 2026-05-23 spec amendment. INTENTIONALLY no `workers/sourcebot_indexer`; rationale + three rejected alternatives in "Plan Defers To" with upstream-docs citation (`docs.sourcebot.dev/docs/connections/local-repos`). Sourcebot indexes once at container start against `file://` snapshot mounts; nothing for CodeOracle's barrier to wait on, no per-snapshot reindex trigger, no stream allocated. Plan 8 owns the *query-side* `SourcebotClient.fetch_span(...)`.
   - §4.1 service map `workers/traces_ingest` — out of scope; Plan 7.
   - §4.1 service map `IndexingOrchestrator` fan-out for 4 kinds — Task 2 (`_INFO_BY_KIND`) + Task 13 (default kinds).
   - §6 LLM Layer embeddings split from chat — Task 7 (`EmbeddingsProvider` ABC).
   - §7 Code-evidence layer textual cross-check — Tasks 4–5 + 9 + 11.
   - §8 Knowledge graph `:Definition` + `:Reference` (SCIP) — Task 10.
   - §9 Embedding store HNSW knn — Task 6.
   - §10 Reindex APPLYING → APPLIED through barrier — Tasks 2 + 10/11/12 + 13 + 14. (Terminal state `SnapshotStatus.APPLIED`, NOT `READY`.)
   - All in-scope spec sections have at least one task. Sourcebot deferral is explicit.

2. **Placeholder scan:**
   - "TBD" / "TODO" / "implement later" — none in user-facing plan content (one or two "(adapter signature may differ; implementer reads Plan 5)" notes are acceptable cross-plan handoffs, not placeholders for unwritten work).
   - "Add appropriate error handling" / "add validation" — none. Errors are mapped explicitly per task (Transient vs Terminal; what gets written to outcome row vs DLQ).
   - "Write tests for the above (without code)" — none. Every test step has full test code.
   - "Similar to Task N" — none. Code is repeated where each task needs it (4-file Joern template replicated as the SCIP / OpenGrep / Embed scaffolds).

3. **Type / contract consistency:**
   - `CodeReferenceRow` (dataclass) shape matches the `CodeReference` ORM columns — yes: (snapshot_sha, file_path, symbol_name, line_number, kind).
   - `EmbeddingChunk` shape matches `Embedding` ORM columns — yes: (snapshot_sha, source_path, chunk_start_line, chunk_end_line, content, embedding).
   - `OpengrepMatch.kind` = `CodeReferenceKind` Literal — yes.
   - `ScipDefinition` / `ScipReference` fields (file_path, fqn, line_number, end_line) — consistent across parser, ScipGraphRepository, tasks.
   - `WorkerConsumer` ClassVars (`stream`, `group`, `message_type`) populated on all three new consumers — yes (Tasks 10, 11, 12).
   - `WorkerMessage.batch_id == snapshot_sha` validator on all three message types — yes.
   - `ExtractorMode` Literal includes ("per_file", "project") — yes; Plan 7 may extend.
   - `IndexingOrchestrator._INFO_BY_KIND` includes 4 entries (joern + 3 new), each carrying `(stream, mode, accepted_extensions)` — yes (Task 2). `accepted_extensions` is `_CODE_EXTENSIONS` for joern/scip/opengrep and `None` (accept-all) for embed.
   - `DEFAULT_EXTRACTOR_KINDS` matches `_INFO_BY_KIND` keys — yes (Task 13: `("joern", "scip", "opengrep", "embed")`).
   - `chunker_for(file_path, *, embedding_config) -> Chunker` (G14) returns `LineChunker` for `.py`/`.pyi` and `ParagraphChunker` for `.md`/`.rst`/`.adoc`/`.markdown` — covered by 11 cases in `test_chunker_for.py` (Task 12 Step 2c). The `Chunker` ABC erases the distinction at the `extract_file_to_embeddings` seam, so the task function reads `Chunk.start_line` / `Chunk.end_line` / `Chunk.text` symbolically and the `Chunk → EmbeddingChunk` translation flows unchanged regardless of chunker kind.
   - `is_doc_path(source_path: str) -> bool` is the public path-classification predicate (Task 12 Step 2): Plan 10's `embed_query` node imports it from `codeoracle.services.embedding.chunker` to route each `EmbeddingHit` to `HandleType.DOC_PARAGRAPH` (doc extension) or `HandleType.CODE_SPAN` (everything else). Both `is_doc_path` and `chunker_for` consult the same `_DOC_EXTENSIONS` set, and `test_chunker_for.py`'s parametrized agreement test (`test_is_doc_path_agrees_with_chunker_for_dispatch`) asserts `isinstance(chunker_for(path, …), ParagraphChunker) is is_doc_path(path)` per path — single source of truth, no drift between "what got chunked as a doc" and "what gets cited as a doc."
   - `STREAM_INDEXING_*` + `GROUP_*` constants used in consumer ClassVars MATCH the streams.py exports — yes.
   - DLQ derivation is centralized in `WorkerConsumer.dead_letter` via `dlq_for(stream)` (Plan 5); `RetryPolicy` carries NO `dlq_stream` field (removed — it was a redundant second source of DLQ truth) — yes.

4. **Architectural invariant compliance:**
   - Invariant 1 (per-file barrier sacred): every worker writes 1 outcome row per (file, extractor) per Reindex. SCIP writes N from 1 message — verified in Task 10 Step 1 + Task 10 invariant 2. OpenGrep + Embed write 1 each. Joern (Plan 5) writes 1 each. ✓
   - Invariant 2 (SCIP outcome for every file): tasks.py writes outcomes for ALL files in `message.files`, even if SCIP didn't cover them (Task 10 Step 9). ✓
   - Invariant 3 (SCIP failure = all-file failure): `_mark_all_failed` writes outcome=failed for every file in `message.files` on ScipExtractionError or ScipParseError (Task 10 Step 9). ✓
   - Invariant 4 (AGE label namespaces): SCIP uses `:Definition` + `:Reference`; Joern keeps `:Symbol`. No MERGE on shared FQNs. ✓
   - Invariant 5 (chunk constants chunker-scoped — G14): embed_chunk_lines + embed_chunk_overlap govern LineChunker (code files); doc_chunk_max_lines governs ParagraphChunker (doc files). All three live on `EmbeddingConfig` (Task 3); writer (embed_indexer's `chunker_for` dispatch in Task 12) and reader (Plan 8's CodeSpanResolver + DocParagraphResolver, deferred) load from the same Settings instance. Storage shape `(chunk_start_line, chunk_end_line)` is uniform across chunker kinds. ✓
   - Invariant 6 (provider lifetime = process lifetime): `__main__.py` constructs once, `aclose()` on stop_event (Task 12 Step 10). ✓
   - Invariant 7 (no new framework rewrites): `WorkerConsumer` is the only base class; `_INFO_BY_KIND` is local; `ExtractorMode` literal is 1 line of code. ✓

5. **Outstanding unknown / risk:**
   - Sourcebot HTTP search API surface (Plan 8 blocker) — explicitly surfaced in "Plan Defers To".
   - OpenGrep `pattern-regex` named capture syntax — may need a minor rule rewrite at implementation time depending on installed opengrep version. Logged inline in Task 9 Step 4.
   - ~~AGE driver helper signatures from Plan 5~~ — RESOLVED by P0-13 (Task 10 composes Plan 5's `GraphRepository` directly via `self._graph.execute_cypher(...)`; no driver-helper coupling remains).

6. **Declared deviations + resolved polish (catalogued in `docs/superpowers/plan-audit-findings.md`):**
   - **D-6.3** — Declared deviation: OpenGrep's string-literal + comment-mention reference rules (Task 9 — `string_literals.yml` / `comment_mentions.yml`) use a 3-char-minimum identifier pattern (`[a-zA-Z_][a-zA-Z_0-9]{2,}`), so 1-2 character symbol names (`a`, `i`, `os`) are intentionally not captured as textual references. This is a deliberate noise-floor (rationale inline at Task 9 Step: avoids the flood of trivial `a`/`i` matches), NOT a SCIP-parser identifier filter and NOT a future enhancement. Recorded here as an accepted coverage deviation vs the 100%-coverage doctrine; `import` and `call` reference kinds are unaffected.
   - **D-6.4 — Declared deviation, SECURITY-REVIEW-REQUIRED:** `ScipGraphRepository` (Task 10 Step 3) builds its `:Definition`/`:Reference` AGE Cypher by VALIDATE-THEN-FORMAT — each value is checked against a strict regex allowlist (`_SAFE_SHA_RE` / `_SAFE_PATH_RE` / `_SAFE_FQN_RE`) and integers are int-validated, then f-string-interpolated into the Cypher body — rather than native parameter binding. AGE's Cypher body lives inside a `$$…$$` dollar-quoted string, so SQLAlchemy's `:foo`/`%s` binding (which targets the outer SQL layer) does not reach it, and Plan 5's `GraphRepository.execute_cypher` does not currently thread values into AGE's parametrized `cypher('graph', $$body$$, $1)` form. This is sound FOR THE SLICE because every interpolated value originates inside CodeOracle (git SHA from `IndexingService`, path from the snapshot manifest, FQN from `scip-python` output, line numbers already ints) — there is no untrusted input on this write path. Full rationale + the tight allowlist regexes are in the module docstring's "AGE EXECUTION + PARAMETER-BINDING NOTE" (Task 10 Step 3). **Tracked hand-off:** the security reviewer MUST confirm the allowlist tightness before sign-off, and any future plan that adds untrusted input to a Cypher write site MUST switch to AGE's parametrized `cypher()` form (with a regression test proving binding through `GraphRepository`). Logged here so this is a tracked, owned deviation rather than a silent docstring-only closure.
   - **D-6.11 — RESOLVED to a documented owned preflight (not silently advisory):** spec §3.6's vLLM `/v1/embeddings` row is a **scaffolding-day-1 one-shot manual/staging preflight** owned by `infra/scripts/preflight.sh` — §3.6 is explicitly "one bash command run during scaffolding day 1 … one-shot at scaffolding day 1 (distinct from `infra/scripts/check_pinned_env.sh`, the CI-recurring pin-consistency lint)", so it was never a CI-recurring gate and CI cannot host a GPU-backed vLLM server at scaffolding time. The §3.6 gate therefore stays a real, owned, blocking preflight (its failure records the column-3 fallback — route embeddings via `adapters/llm/providers/vllm_embed.py` — into `.cache/preflight/results.md`); Plan 6's Task 8 `--run-live-embeddings` smoke (`embed(["test"])` returns `list[list[float]]`, len 1, dim == `config.dim`) is a **complementary, CI-off regression check against the running provider**, NOT a replacement for the preflight gate. Plan 2's committed `check_vllm_openai_compat.py` covers `/v1/chat/completions`, not `/v1/embeddings`; the `/v1/embeddings` contract assertion is the §3.6 `preflight.sh` row (a proposed §3.6 amendment tightens that row from "returns a parseable response" to an explicit `.data[0].embedding` non-empty-float-array assertion, and notes Plan 6's live smoke is complementary). No blocking gate was downgraded — the preflight remains blocking at scaffolding day 1, owned by the preflight script; the earlier "blocking→advisory downgrade" framing was the actual defect (the spec already specifies a one-shot scaffolding-day-1 manual gate, which is the realistic gating).
   - **D-6.12 — RESOLVED (field-casing):** the ledger's D-6.12 is the `EmbeddingConfig` field-casing inconsistency; all fields are now snake_case (`model`, `dim`, `embed_chunk_lines`, `embed_chunk_overlap`, `doc_chunk_max_lines`, `kind`, `base_url`, `api_key` — Task 3 / Task 7). The env vars they bind to are UPPER_CASE (`CODEORACLE_EMBEDDING__EMBED_CHUNK_LINES`, …) by env-var convention; that is not a field-casing issue. (A separate, net-new cosmetic — renaming the private `_GRAPH` constant in `ScipGraphRepository` to `_AGE_GRAPH_NAME` — is unrelated to the ledger's D-6.12 and was previously mis-numbered as it; it is optional and carries no behavioral change.)
   - **D-6.13 — RESOLVED (correctness):** `_validate_embedding_config` (Task 3) now rejects degenerate strides, not just the overlap-loop case. It keeps the `embed_chunk_overlap >= embed_chunk_lines` guard AND additionally requires the forward stride `embed_chunk_lines - embed_chunk_overlap >= _MIN_CHUNK_STRIDE` (10), so configs like `chunk_lines=2, overlap=1` (stride 1 → O(file) near-duplicate chunks) are rejected; `test_degenerate_stride_rejected` (Task 3 tests) pins it. The floor lives in exactly one place — the `EmbeddingConfig` validator — because `chunker_for` is the sole production construction site for the code-path `LineChunker` and always feeds it already-validated config values; no second floor is duplicated into `LineChunker.__init__` (one authoritative invariant at the config boundary, not two kept in lockstep — `LineChunker.__init__` carries only an explanatory NOTE pointing at the config gate). Resolved in this hardening pass.

   D-6.3 and the D-6.4 security hand-off are tracked deviations; D-6.11/D-6.12/D-6.13 are resolved in this hardening pass and catalogued in `docs/superpowers/plan-audit-findings.md` (working-tree-only audit doc).

---

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-05-21-snapshot-extractors-and-embeddings.md`. Two execution options:

**1. Subagent-Driven (recommended)** — dispatch a fresh subagent per task, review between tasks, fast iteration. Uses `superpowers:subagent-driven-development`.

**2. Inline Execution** — execute tasks in this session using `superpowers:executing-plans`, batch execution with checkpoints.

Which approach?
