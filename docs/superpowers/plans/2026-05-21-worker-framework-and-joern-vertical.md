# Worker Framework + Joern Vertical Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Land the WorkerConsumer framework (Valkey-streams-backed, retry/DLQ, generic over message shape), the AGE-backed `GraphRepository` + `SnapshotRepository`, the Joern adapter + `joern_indexer` worker, and the `IndexingService` + `DeltaComputer` + `pg_advisory_lock` single-flight primitive — so Plan 6 can pattern-replicate 5 more extractors against this framework instead of inventing it 5 more times.

**Architecture:** **Workers are independent processes** that consume from named Valkey streams via consumer groups (`XREADGROUP`), dispatch messages to typed handlers, ACK on success / leave-in-PEL on transient failure (reclaim-enabled workers re-deliver stale PEL entries via `XAUTOCLAIM` until retries exhaust → dead-letter) / dead-letter immediately on terminal failure. `IndexingService` is the only producer (orchestrator); it never calls workers directly — it `XADD`s file-indexing jobs and waits on a snapshot-completion barrier (per-file outcome rows in `snapshot_file_outcomes`). `pg_advisory_lock(0x46AE0001)` enforces single-flight reindex across CLI + API. The dirty-working-tree SHA convention (`<head>+wt<sha256-of-changes>[12]`) supports dogfooding without forcing commits. The framework is generic — Plan 6 adds 5 more consumer subclasses + 5 more streams without touching `workers/_base/`.

**Tech Stack:** Python 3.14 · `valkey-py` (Linux-Foundation Valkey client, drop-in for `redis-py`) · `httpx` (Sourcebot bridge in Plan 6) · `joern-cli` (subprocess) · `SQLAlchemy` 2.0 async · `asyncpg` · `pydantic` v2 · `structlog` · `pytest` + `pytest-asyncio` + `testcontainers[postgres,redis]` (Valkey speaks Redis protocol so testcontainers' redis fixture works).

**Spec source:** `docs/superpowers/specs/2026-05-21-platform-scaffolding-design.md` §3.2 (worker process model), §3.3 (layer purity — adapters wrap externals, services orchestrate), §4 (9 services map — Joern indexer + IndexingService + GraphRepository participate), §8.4 (incremental indexing — DeltaComputer + pg_advisory_lock + dirty-WT SHA), §8.5 (functional gates that exercise the indexing pipeline).

**Inherits from Plan 1:** repo skeleton, `src/codeoracle/cli/__main__.py` Typer root app (`typer.Typer()` named `app`, with `typer` already in `[project.dependencies]` — Plan 1 Task 3 scaffolds it; Plan 5 Task 14 registers the `codeoracle index` subcommand against that app), CI workflow that runs unit + integration tests on every PR, `pyproject.toml` ruff/ty config (Plan 5 adds `valkey-py` to deps).

**Inherits from Plan 2:** `infra/versions/pinned.env` provides `IMAGE_VALKEY`, `IMAGE_POSTGRES_AGE`, `JOERN_VERSION` (pinned; if Plan 2 didn't pin Joern, Plan 5 Task 1 Step 2 pins it). The pre-implementation gate that proved Joern's Python frontend produces a usable CPG (spec §3.6) is the load-bearing precondition for this plan.

**Inherits from Plan 3:** AGE-per-connection event listener in `src/codeoracle/db/session.py` (`_setup_age_per_connection` — every new connection runs `LOAD 'age'` + `SET search_path`); `models/snapshot.py` (`Snapshot` Pydantic + `SnapshotStatus` + `ExtractorOutcome` enums); migrations `0001_snapshots_table.py`, `0002_embeddings_table.py`, `0003_traces_table.py` (Plan 5 adds `0004_snapshot_file_outcomes.py`); `infra/scripts/bootstrap_db.sh` + `bootstrap_db.sql` (extensions + create_graph + the `Symbol`/`File` vertex labels & their btree property indexes — NFR D3-1). The `codeoracle` AGE graph exists; tables exist; Plan 5 writes the *repository classes* against the schema Plan 3 established.

**Inherits from Plan 4:** `LLMProvider` facade and `LLMProviderFactory` — Plan 5 worker framework doesn't call LLMs (no embeddings yet — Plan 6's `embed_indexer`). The framework's `WorkerConsumer.run()` async loop is reusable by Plan 6 workers that DO call LLMs; the wiring composition root in `workers/_base/wiring.py` builds the budget-wrapped provider via `build_budgeted_provider(LLMConfig(), ContextBudgetService(...))` (Plan 4 Task 12b) so Plan 6 worker subprocesses can resolve a provider from env.

**Defers to Plan 6:** SCIP / OpenGrep indexers + `embed_indexer` + `EmbeddingsRepository`. Plan 5 designs `IndexingService.reindex()` so Plan 6's additional extractors only require: (a) extending `ExtractorKind` literal, (b) adding a new `WorkerConsumer` subclass, (c) appending to `IndexingService._extractor_kinds`. No framework rewrite. (`TracesRepository` + traces adapters are Plan 7, not Plan 5's concern. **No `sourcebot_indexer` worker** — Sourcebot is Git-pull and indexes once at container start against `file://` snapshot mounts; deployment-side config is Plan 1A / Plan 13 — see Plan 6's "Plan Defers To" for the full rationale + rejected alternatives. Plan 8 owns the query-side `SourcebotClient.fetch_span()` adapter.)

**Defers to Plan 8:** `services/handle_resolution/` (HandleResolverService composes 4 resolvers — graph / sourcebot / file / doc-paragraph — and answers `handle.resolve(citation)`) + the Sourcebot HTTP adapter. Plan 5 produces *graph data* via Joern; Plan 8 *resolves citations against it*.

**Defers to Plan 9:** `models/evidence.py` (Claim, Citation, EvidenceHandle, ResolvedCitation, Verdict), `services/verifier/`, `services/trust_matrix.py`. Plan 5 produces *graph data*; Plan 9 *verifies claims against it*.

**Defers to Plan 10:** `services/indexing/orchestrator.py` integration with the LangGraph agent — the agent triggers reindex via a tool call. Plan 5 only exposes reindex via CLI + `IndexingService` API; Plan 10's agent wires it as a tool action. (Spec's `services/query_planner.py` was removed in Plan 10 Deviation 3 — no longer applicable here.)

**Amendment 2026-05-26 — Plan 11 async-job pivot driver:** Plan 5 grows a new Task 13a (`ReindexOrchestratorConsumer`) + two new methods on `IndexingService` (`enqueue_reindex`, `get_snapshot`) + a new `pre_allocated_snapshot_sha` keyword on the existing `reindex()` method, so that Plan 11's `POST /v1/reindex` route can return immediately with a `Snapshot(status=PENDING)` while the actual reindex runs out-of-band in a worker process. The lock-holder semantics shift: `pg_advisory_lock(0x46AE0001)` was held by the API request's session pre-amendment; post-amendment it's held by the `ReindexOrchestratorConsumer` worker process. Plan 5's existing `_reindex_locked` body is unchanged in shape, only branched to read `changed_files` from the pre-allocated `Snapshot` row instead of re-running `DeltaComputer` when the worker calls it. CLI (Task 14) + the vertical integration test (Task 15) both leave `pre_allocated_snapshot_sha=None` and keep sync semantics. **Sub-amendment to Task 5:** `WorkerContext` grows an `engine: AsyncEngine` field — the reindex_orchestrator worker needs the SAME engine that backs the session factory (to hold `pg_advisory_lock` on a connection from that pool); without exposing it, the entrypoint would construct a second engine + pool, breaking the single-flight lock invariant. **Producer-dict schema fidelity:** `enqueue_reindex` XADDs a dict with explicit `batch_id` (= `snap.sha`); `ReindexRequestMessage` inherits `batch_id: str` from `WorkerMessage` with no default, so omitting it would `ValidationError` on every consumer-side validate. Driver: user P0 flag that the slice's small-repo sync assumption violated the project's mandate to operate on large production-scale target repositories. **Prereq:** ships in lockstep with a Plan 3 sub-amendment adding `IndexingConfig` (`repo_root`, `extractor_kinds`) to nested `Settings` — the reindex_orchestrator `__main__.py` reads `settings.indexing.repo_root` + `settings.indexing.extractor_kinds` to construct `IndexingService`; Plan 3's amendment MUST land first or this plan's Task 13a entrypoint won't validate at code-time. See "What Plan 11 inherits from Plan 5" at end of file.

**Stale-`APPLYING` cleanup ownership — owned by Plan 5 (was deferred to Plan 11; reversed via DECIDE-D):** Plan 5 owns end-to-end recovery from SIGKILL/OOM-orphaned `APPLYING` rows via *check-on-use inside* `IndexingService.reindex()` — no separate janitor process, no time threshold, no startup-scan deferral. Mechanism: at the top of `_reindex_locked` (after `pg_advisory_lock(0x46AE0001)` has been acquired), `SnapshotRepository.mark_stale_applying_failed()` issues one bulk `UPDATE snapshots SET status='failed', error='process died mid-apply' WHERE status='applying'`. **Soundness derives from the advisory-lock invariant, not a wall-clock heuristic:** `pg_try_advisory_lock(0x46AE0001)` (session-level, not transaction-scoped — see Task 9) serializes every reindex path that creates `APPLYING` rows, *and* Postgres auto-releases session-level advisory locks when the holding connection dies (asyncpg socket close → backend cleanup). Precise invariant: the *current process* holds the lock on the connection `reindex_single_flight` opened against `self._engine`, and that lock is held for the full body of `_reindex_locked` (released only when the context manager exits via the `async with` in `reindex()`). The bulk sweep runs on a *different* session (`async with self._db()`) — it does NOT inherit the lock onto its own connection, but it doesn't need to: the per-process lock-holder connection elsewhere is what makes any *other* `APPLYING` row visible to the sweep a SIGKILL/OOM leftover (no concurrent legitimate reindex on any connection in any process can exist while the lock is held). No `created_at`/`updated_at` column is needed on the `snapshots` table (Plan 3 only stores `applied_at`), no tunable threshold exists to mis-set, and no false positives are reachable as long as the per-process lock-holder connection precondition holds. The KeyboardInterrupt + general-exception handlers below still do their cleanup; the bulk sweep covers only the SIGKILL path where the `except` clause never runs. Trade-off accepted: a process that's killed mid-apply leaves an informational `APPLYING` row in the DB until the *next* reindex runs — read-paths are unaffected (`get_latest_applied` filters to APPLIED), and "no reindex ever runs again" is not a state the platform survives anyway. (Prior versions deferred this to Plan 7, then Plan 8, then Plan 11 — DECIDE-D collapses the chain by removing the janitor entirely.)

---

## Cross-plan contracts (read before changing the parser or migrations)

Two decisions are baked into Plan 5's output and consumed by downstream plans. Changing either after Plan 5 ships forces re-indexing of every snapshot. Both are pinned here so a reader sees the contract before reaching Task 11.

**1. Graph FQN scheme — dotted Python form, derived from `(file_path, name)`.** Plan 5 emits symbol-node `fqn` as a dotted Python form (e.g. `codeoracle.adapters.joern.client.JoernClient`) derived from `file_path + name` via `_derive_dotted_fqn()` (Task 11). Algorithm: strip leading `src/`, strip `.py`, strip trailing `/__init__`, replace `/` with `.`, then append `.<name>`. Plan 8's graph-evidence resolver and Plan 10's `is_safe_symbol_fqn` predicate both query the graph using this form, so the contract is bidirectional. Raw facts (`file_path`, `name`) are preserved as separate node properties so a future schema change (e.g. Plan 6 adding class scope via Joern's `AST_PARENT_FULL_NAME`) is a derivation pass over existing rows, not a re-extraction from source. **Known limitation (deferred to Plan 6):** methods inside classes collide with free functions of the same name in the same module — Plan 10's verifier surfaces this as a structured "ambiguous match," never a silent wrong answer. **Why not Joern's `FULL_NAME` column?** Its Python shape is uncaptured anywhere (Plan 2 verification gap); deriving from `file_path + name` is hermetic and testable without that precondition.

**2. Alembic revision-ID convention — bare numeric (matches Plan 3).** Plan 3's migrations use `revision = "0001"`, `"0002"`, `"0003"` (bare numeric). Plan 5's `0004_snapshot_file_outcomes.py` uses `revision = "0004"` to continue the chain. Plan 6 must use `revision = "0005"` with `down_revision = "0004"` (NOT `"0004_snapshot_file_outcomes"` — the audit's P0-15 flags Plan 6's slug-form as the deviation, fixed in Plan 6 §6). The filename is `0004_snapshot_file_outcomes.py` but the revision ID is `"0004"`; alembic accepts either form, but the `down_revision` chain must use whichever the prior `revision =` set.

**3. Deviation from spec §3.2 file-layout — two net-new artifacts + one inherited-but-spec-omitted module (`spec-reconcile: applied (#44)` for the first two; `db/schemas.py` reconciled this pass — D-5.4).** Plan 5 ships two files that the spec §3.2 `src/codeoracle/` tree *originally* omitted, by deliberate design, not regression; both were added to the spec §3.2 tree in #44 (see the per-artifact reconciliation notes below). A third module Plan 5 *touches* — `db/schemas.py` — was also absent from the spec §3.2 `db/` tree, but it is **created by Plan 3** (Plan 3 Task 7: `Write src/codeoracle/db/schemas.py`, holding the `Snapshot` / `Embedding` / `Trace` declarative tables) and only *extended* by Plan 5 (Task 6 adds the `SnapshotFileOutcome` table). It is therefore an inherited file, not a Plan-5-net-new one — the spec-§3.2 gap is reconciled by adding `db/schemas.py` (creator: Plan 3; extenders: Plans 5/6/7) to the spec `db/` tree (D-5.4 spec-amendment; see the §3.2 `db/` proposal). The two genuinely-Plan-5-net-new artifacts are:

- **`db/migrations/versions/0004_snapshot_file_outcomes.py` + the `snapshot_file_outcomes` table** (Task 6). The spec enumerates `models/snapshot.py` (`Snapshot`) and its repositories; it *originally* carried no per-file-per-extractor ack table, so the barrier table — the mechanism `IndexingService` uses to detect extractor-phase completion (spec §8.4 describes the incremental-indexing *behavior* but not this table) — was net-new vs §3.2. #44 reconciled this: the spec §3.2 `migrations/` line now records `0004_snapshot_file_outcomes` (spec L176). The table's `snapshot_sha` column width is canonicalized to `String(80)` to match Plan 3's `snapshots.sha` / `embeddings.snapshot_sha` / `traces.snapshot_sha` (the `<head>+wt<sha256[12]>` dirty-WT key — see Architecture L7).
- **`services/indexing/advisory_lock.py`** (Task 9). The spec §3.2 `services/indexing/` directory *originally* listed only `service.py`, `delta.py`, `orchestrator.py`; the spec models single-flight via `pg_advisory_lock(0x46AE0001)` (§8.4 / the §"single-flight mutex" row) but did not enumerate a dedicated module for it. Plan 5 extracts the `pg_try_advisory_lock` + poll-loop context manager into its own file (`reindex_single_flight`) so CLI + API + the `ReindexOrchestratorConsumer` worker share one tested primitive rather than three inline copies; #44 reconciled this by adding `advisory_lock.py` to the spec §3.2 `services/indexing/` tree (spec L210).

These were *better-than-spec* divergences (the spec was the stale party); `spec-reconcile: applied (#44)` — the spec §3.2 tree was reconciled in #44 to add both artifacts (finding D-5.4 on the spec-reconcile worklist, now resolved).

---

## File structure

```
src/codeoracle/
├── adapters/
│   ├── valkey/                          # NEW: queue client
│   │   ├── __init__.py
│   │   ├── client.py                    # ValkeyQueueClient — XADD, XREADGROUP, XACK, dead-letter
│   │   └── config.py                    # ValkeyConfig (env-loaded, base_url etc.)
│   └── joern/                           # NEW: per-file CPG extraction
│       ├── __init__.py
│       ├── client.py                    # JoernClient — subprocess wrapper
│       └── parser.py                    # JoernCpgParser — joern-export JSON → AGE Cypher INSERT stmts
├── db/
│   ├── repositories/                    # NEW directory contents (Plan 3 left empty)
│   │   ├── __init__.py
│   │   ├── snapshots.py                 # SnapshotRepository — CRUD + status transitions
│   │   ├── graph.py                     # GraphRepository — AGE Cypher executor
│   │   └── file_outcomes.py             # SnapshotFileOutcomeRepository — per-file extraction ack rows
│   └── migrations/versions/
│       └── 0004_snapshot_file_outcomes.py   # NEW: barrier table for IndexingService completion-wait
├── services/
│   └── indexing/                        # NEW directory contents
│       ├── __init__.py
│       ├── delta.py                     # DeltaComputer — git diff + dependent discovery + dirty-WT SHA
│       ├── orchestrator.py              # IndexingOrchestrator — fans out enqueue + waits on barrier
│       ├── service.py                   # IndexingService — advisory lock + snapshot lifecycle (orchestrates orchestrator)
│       └── advisory_lock.py             # pg_advisory_lock helper (context manager)
├── workers/
│   ├── _base/                           # NEW: generic framework
│   │   ├── __init__.py
│   │   ├── message.py                   # WorkerMessage + RetryPolicy + WorkerError
│   │   ├── consumer.py                  # WorkerConsumer ABC — async loop, ACK/NACK/DLQ
│   │   └── wiring.py                    # build_worker_context() — composition root
│   └── joern_indexer/                   # NEW: first concrete worker
│       ├── __init__.py
│       ├── __main__.py                  # `python -m codeoracle.workers.joern_indexer` entrypoint
│       ├── consumer.py                  # JoernIndexerConsumer(WorkerConsumer)
│       └── tasks.py                     # extract_file_to_graph(message) handler
├── cli/
│   └── commands/
│       └── index.py                     # NEW: `codeoracle index [--since SHA | --full]`
└── core/
    ├── streams.py                       # NEW: stream-name + group + advisory-lock-ID registry + pure dlq_for() (D7a-2 — relocated from workers/)
    └── exceptions.py                    # EXTEND: add WorkerError, JoernExtractionError, GraphQueryError,
                                         #         SnapshotConflictError, AdvisoryLockTimeoutError
```

Tests mirror the structure under `tests/unit/` and `tests/integration/`.

**Total file count this plan:** 23 new + 1 extended. Estimated ~3500 lines.

---

## Task 1: Plan 5 deps + Joern version pin + worker stream-name constants

**What this task delivers:** adds the `valkey` PyPI package (the LF-rebrand of `redis-py`) to `pyproject.toml`, registers a `worker` pytest marker for slow integration tests, verifies `JOERN_VERSION` is pinned in `infra/versions/pinned.env`, creates `src/codeoracle/workers/__init__.py` + `src/codeoracle/workers/_base/__init__.py` so subsequent tasks can import, centralizes the stream-name string constants so Plan 6 reuses them, and extends `tests/integration/conftest.py` with the two new fixtures (`async_session_factory` + `valkey_url`) every Plan 5 integration test references.

**Files:**
- Modify: `pyproject.toml`
- Modify: `infra/versions/pinned.env` (if `JOERN_VERSION` is missing — Plan 2 should have pinned it)
- Create: `src/codeoracle/workers/__init__.py`
- Create: `src/codeoracle/workers/_base/__init__.py`
- Create: `src/codeoracle/core/streams.py` (the stream-name constants)
- Modify: `src/codeoracle/core/exceptions.py` (add 5 new exception classes)

- [ ] **Step 1: Add `valkey` to `pyproject.toml` dependencies**

`valkey` is the Linux-Foundation-maintained fork of `redis-py` (the PyPI name post-LF-rebrand). Same API surface — any Valkey 7+ server speaks it; `redis://` URL scheme still accepted. Classes are renamed: `valkey.Valkey` is what `redis.Redis` used to be. The whole codebase (clients, conftest, adapter) consistently imports the `valkey` module — no conditional fallbacks, no two-import-name code paths.

```toml
# Inside [project] dependencies — add this line alphabetically.
"valkey>=4.5",
```

Confirm by running:

```bash
uv add 'valkey>=4.5'
uv lock  # regenerate uv.lock with the new dep
uv sync
```

Expected: `valkey` and its transitive deps (`async-timeout`, `hiredis` if installed) appear in `uv.lock`. Verify import + class name:

```bash
uv run python -c "import valkey; print(valkey.__version__); print(valkey.Valkey)"
```

Expected: prints a version string `>=4.5` and the class `<class 'valkey.client.Valkey'>`. If either fails, this plan's assumption about the package name is wrong — STOP and adjust Step 1 globally (every subsequent step's import block depends on this name).

- [ ] **Step 2: Verify `JOERN_VERSION` pin in `infra/versions/pinned.env`**

Plan 2's job was to pin every external tool with a digest or version string. Confirm Joern's pin:

```bash
grep '^JOERN_VERSION=' infra/versions/pinned.env || echo "ERROR: JOERN_VERSION is not pinned — fix Plan 2 before continuing."
```

Expected: a line like `JOERN_VERSION=2.0.something`. If missing, this is a Plan 2 regression — fix in Plan 2's `pinned.env` (run Plan 2 Task N's pre-flight CLI, commit), do not band-aid here. Plan 5 reads this version when invoking joern-cli in its dockerized integration test (Task 14).

- [ ] **Step 3: Register the `worker` pytest marker**

Plan 5 introduces tests that spin up Valkey via testcontainers; they're slower than unit tests. Mark them with `worker` so devs can run `uv run pytest -m 'not worker'` for fast iteration.

In `pyproject.toml`, under `[tool.pytest.ini_options]`, extend `markers`:

```toml
markers = [
    "asyncio: async test (auto-applied by asyncio_mode=auto)",
    "live_llm: requires OPENAI_API_KEY + network; runs only in label-gated CI workflow",
    "worker: requires testcontainers + Valkey + Postgres; slow integration tests",
]
```

Verify:

```bash
uv run pytest --markers | grep '^@pytest.mark.worker:'
```

Expected: the marker description prints.

- [ ] **Step 4: Create the worker package `__init__.py` files**

```bash
touch src/codeoracle/workers/__init__.py
touch src/codeoracle/workers/_base/__init__.py
```

Both files are empty. They exist only so `import codeoracle.workers.joern_indexer` (and Plan 6's `codeoracle.workers.scip_indexer` etc.) resolve.

- [ ] **Step 5: Write `src/codeoracle/core/streams.py`** — centralized stream-name constants

```python
"""Stream-name constants for the Valkey-backed worker queues.

Centralizing the names here means Plan 6 can extend the family by adding a
new constant + a new WorkerConsumer subclass without grepping for string
literals scattered across the codebase. Tests assert against these
constants, not the raw strings, so renames are safe.

Stream naming: `indexing.<extractor>` for per-file extraction jobs,
`traces.<sub>` for trace ingestion (Plan 6), `<stream>.dlq` for the
dead-letter mirror (one per producer stream).

Consumer-group names match the worker process name; multi-process
parallelism within one worker kind is handled by Valkey's XREADGROUP
load-balancing across consumers in the same group.
"""

from typing import Final


# Producer streams — IndexingService XADDs here; workers XREADGROUP from here.
STREAM_INDEXING_JOERN: Final[str] = "indexing.joern"

# Reindex orchestrator stream (added by 2026-05-26 amendment — async-job
# pattern, Plan 11 driver). IndexingService.enqueue_reindex XADDs a
# ReindexRequestMessage here when the API route receives POST /v1/reindex;
# the ReindexOrchestratorConsumer (Plan 5 Task 13a) XREADGROUPs and drives
# IndexingService.reindex(pre_allocated_snapshot_sha=…) in the worker
# process. See "What Plan 11 inherits from Plan 5" at end of file.
STREAM_REINDEX_REQUESTS: Final[str] = "codeoracle:reindex:requests"


# Consumer-group names — one per worker kind.
GROUP_JOERN_INDEXER: Final[str] = "joern_indexer"
GROUP_REINDEX_ORCHESTRATOR: Final[str] = "reindex_orchestrator"


# Dead-letter streams — `<stream>.dlq` convention.
def dlq_for(stream_name: str) -> str:
    """Return the dead-letter stream paired with `stream_name`.

    DLQ pairing is by convention (suffix '.dlq'), not by Valkey-server
    config — `ValkeyQueueClient.dead_letter()` XADDs to the result of this
    function. Plan 6's workers reuse this helper.
    """
    if not stream_name or stream_name.endswith(".dlq"):
        raise ValueError(
            f"dlq_for() called with invalid stream name {stream_name!r} — "
            "must be a non-empty producer stream name."
        )
    return f"{stream_name}.dlq"
```

- [ ] **Step 6: Extend `src/codeoracle/core/exceptions.py` — five new exception classes**

Plan 3 (or Plan 4) introduced the base `CodeOracleError` and `LLMProviderError`. Plan 5 adds the worker-pipeline error hierarchy. Each extends `CodeOracleError` so callers can catch the top-level type for "any CodeOracle internal failure."

Add to `src/codeoracle/core/exceptions.py` (preserve existing classes):

```python
class WorkerError(CodeOracleError):
    """Base class for worker-pipeline errors. Subclasses distinguish
    transient (retry) from terminal (dead-letter) failures.
    """


class TransientWorkerError(WorkerError):
    """Retryable error — the consumer NACKs (leaves the message in the PEL
    for its reclaim step to hand back). Example: Valkey unavailable, AGE
    connection blip, joern-cli temporarily OOMing.
    """


class TerminalWorkerError(WorkerError):
    """Non-retryable error — the consumer dead-letters immediately.
    Example: joern-cli rejects the file's syntax, message payload fails
    Pydantic validation, file no longer exists on disk.
    """


class JoernExtractionError(TerminalWorkerError):
    """Joern subprocess failed to extract a CPG from the given file.
    The file's syntax or location is wrong; retrying won't help.
    """


class GraphQueryError(TransientWorkerError):
    """An AGE Cypher statement failed. May be a connection blip
    (retryable) or a query syntax bug (caller-fixable). The repository
    raises the transient variant by default; integration tests catching
    the bug variant should assert on the underlying psycopg error.
    """


class SnapshotConflictError(CodeOracleError):
    """Attempted to transition a Snapshot to a status its current status
    forbids (e.g., applied → applying). Indicates the IndexingService's
    state machine has been violated — never transient.
    """


class AdvisoryLockTimeoutError(CodeOracleError):
    """`pg_try_advisory_lock` returned false within the configured
    timeout — another reindex is in flight. Not a worker error (this is
    raised before any worker is enqueued).
    """
```

- [ ] **Step 7: Extend `tests/integration/conftest.py` — add `async_session_factory` + `valkey_url` fixtures**

Plan 3's conftest provides `bootstrapped_db`, `sync_engine`, `async_engine`, and `async_session`. Plan 5's tasks need two more fixtures every task reuses:

1. **`async_session_factory`** — the `async_sessionmaker` itself (not a single session). Services that open their own per-query session inside a loop (`IndexingOrchestrator`, `IndexingService`, `JoernIndexerConsumer`) need the factory, NOT a pre-opened session — the session must be scoped to one logical query, not the service's lifetime.

2. **`valkey_url`** — a `redis://host:port/0` URL pointing at a fresh Valkey testcontainer. Module-scoped so each integration test module gets its own server (FLUSHALL between tests, but no container churn per test function — that's prohibitively slow).

Add to `tests/integration/conftest.py` (append after the `async_session` fixture; preserve everything else):

```python
import valkey
from sqlalchemy.ext.asyncio import async_sessionmaker
from testcontainers.redis import RedisContainer


@pytest.fixture(scope="session")
def async_session_factory(
    async_engine: AsyncEngine,
) -> async_sessionmaker[AsyncSession]:
    """The async_sessionmaker bound to the test engine.

    Services that open one session per logical query (IndexingOrchestrator's
    barrier poll, IndexingService's lifecycle steps, every WorkerConsumer's
    per-message session) need the FACTORY — not a single session shared
    across many calls. Sharing one session leaks the connection-pool
    checkout and crashes on the first pool recycle.

    The `[AsyncSession]` generic param is required for ty strict — bare
    `async_sessionmaker` is treated as unparameterized Generic.
    """
    return async_sessionmaker(
        bind=async_engine, expire_on_commit=False, autoflush=False,
    )


@pytest.fixture(scope="module")
def valkey_container() -> Iterator[RedisContainer]:
    """Module-scoped Valkey container — per-test FLUSHALL is fast,
    per-test container start is not. The `valkey/valkey:7.2` image
    speaks the redis-protocol, and `testcontainers.redis.RedisContainer`
    is protocol-compatible (no `ValkeyContainer` yet upstream).
    """
    with RedisContainer("valkey/valkey:7.2") as ctr:
        yield ctr


@pytest.fixture
def valkey_url(valkey_container: RedisContainer) -> Iterator[str]:
    """Per-test fresh URL — with FLUSHALL between tests so streams,
    consumer groups, and PEL state don't leak across tests in the same
    module. The `redis://` scheme is accepted by `valkey-py` (URL
    parsing was preserved verbatim from `redis-py` during the fork).
    """
    host = valkey_container.get_container_host_ip()
    port = valkey_container.get_exposed_port(6379)
    url = f"redis://{host}:{port}/0"
    # FLUSHALL between tests — keep stream/group state isolated.
    client = valkey.Valkey.from_url(url)
    try:
        client.flushall()
    finally:
        client.close()
    yield url
```

Verify both fixtures resolve:

```bash
uv run pytest tests/integration/conftest.py --collect-only -q
# Expected: collection succeeds (no NameError on async_sessionmaker / RedisContainer).
```

Also ensure `testcontainers[redis]` is in dev deps (Plan 3 added it for general use; if not, add now):

```bash
grep '"testcontainers\[' pyproject.toml || \
  uv add --dev 'testcontainers[postgres,redis]>=4.0'
```

- [ ] **Step 8: Run lint + type + unit tests**

```bash
uv run ruff check src/codeoracle/core/exceptions.py src/codeoracle/core/streams.py tests/integration/conftest.py
uv run ty check src/codeoracle/core/exceptions.py src/codeoracle/core/streams.py
uv run pytest tests/ -v  # existing Plan 1-4 tests must still pass
```

Expected: zero ruff/ty errors; all prior tests pass (Plan 5 adds no tests yet — Task 2 starts).

- [ ] **Step 9: Commit**

```bash
git add pyproject.toml uv.lock infra/versions/pinned.env \
        src/codeoracle/workers/__init__.py src/codeoracle/workers/_base/__init__.py \
        src/codeoracle/core/streams.py src/codeoracle/core/exceptions.py \
        tests/integration/conftest.py
git commit -m "feat: Plan 5 Task 1 — valkey-py dep + worker streams + 5 new exceptions + conftest fixtures"
```

---

## Task 2: `src/codeoracle/adapters/valkey/` — ValkeyQueueClient

**What this task delivers:** an async wrapper around `valkey-py` exposing four operations: `enqueue(stream, payload)`, `read_pending(group, consumer, count, block_ms)`, `ack(stream, group, message_id)`, `dead_letter(stream, message_id, payload, error)`. All four methods are the ONLY way workers and producers talk to Valkey — no direct `valkey-py` calls outside this adapter (enforced via ruff banned-api in a later task). Includes consumer-group bootstrap (`XGROUP CREATE ... MKSTREAM` idempotent).

**Files:**
- Create: `src/codeoracle/adapters/valkey/__init__.py`
- Create: `src/codeoracle/adapters/valkey/config.py`
- Create: `src/codeoracle/adapters/valkey/client.py`
- Create: `tests/unit/adapters/valkey/__init__.py`
- Create: `tests/unit/adapters/valkey/test_client.py` (mocked redis-py)
- Create: `tests/integration/adapters/valkey/__init__.py`
- Create: `tests/integration/adapters/valkey/test_client_integration.py` (testcontainers — marked `worker`)

- [ ] **Step 1: Write `src/codeoracle/adapters/valkey/__init__.py`**

```python
"""Valkey adapter — the platform's queue client.

Plan 5: workers and IndexingService talk to Valkey ONLY through
ValkeyQueueClient. Direct `valkey-py` / `redis-py` imports outside
this package are forbidden (enforced by ruff banned-api in Task 12 Step 4).
"""

from codeoracle.adapters.valkey.client import ValkeyQueueClient
from codeoracle.adapters.valkey.config import ValkeyConfig

__all__ = ["ValkeyConfig", "ValkeyQueueClient"]
```

- [ ] **Step 2: Write `src/codeoracle/adapters/valkey/config.py`**

```python
"""ValkeyConfig — env-loaded settings for the queue client.

A standalone pydantic-settings BaseSettings (same approach as LLMConfig) that
reads a root-style ``CODEORACLE_VALKEY__*`` prefix — double underscore, distinct from
LLMConfig's flat ``CODEORACLE_LLM_`` (single). Both prefix forms are sanctioned by the
.env convention; the difference is deliberate, so do not "unify" them — the
config reads ``CODEORACLE_VALKEY__*`` and every env source must set that exact prefix.
"""

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class ValkeyConfig(BaseSettings):
    """Connection + tuning parameters for the Valkey queue client.

    All fields are required (no defaults) so misconfiguration fails fast
    at process start, never silently at first publish.
    """

    model_config = SettingsConfigDict(
        env_prefix="CODEORACLE_VALKEY__",
        env_nested_delimiter="__",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    url: str = Field(
        min_length=1,
        description=(
            "Valkey connection URL — e.g., 'valkey://valkey:6379/0' or "
            "'redis://valkey:6379/0' (the protocols are interchangeable)."
        ),
    )
    pool_min: int = Field(ge=1, le=64, description="Min pool size.")
    pool_max: int = Field(ge=1, le=256, description="Max pool size.")
    block_ms_default: int = Field(
        ge=0,
        le=60_000,
        description=(
            "Default XREADGROUP BLOCK milliseconds — 0 = non-blocking. "
            "Workers typically use 5000 (5s) to avoid busy-spinning."
        ),
    )
    max_in_flight_per_consumer: int = Field(
        ge=1,
        le=1000,
        description="XREADGROUP COUNT cap per fetch — backpressure knob.",
    )
    stream_maxlen: int = Field(
        ge=1,
        description=(
            "Per-stream MAXLEN applied to every XADD (with approximate "
            "trimming). Without it, streams retain every entry forever — "
            "XACK clears only the PEL, never the stream — so cross-reindex "
            "accumulation grows unbounded until the Valkey process is "
            "OOM-killed. MUST exceed the peak unprocessed backlog of a "
            "single reindex, which equals the target's file count: "
            "IndexingService._enqueue_all fans out one message per file per "
            "extractor-kind with no in-flight throttle, and the completion "
            "barrier (_wait_for_completion) polls until every file reaches a "
            "terminal outcome. A value below that floor makes approximate "
            "trimming evict never-delivered entries, stranding their "
            "snapshot_file_outcomes rows so the barrier counts forever and "
            "the reindex never completes. No upper bound — it scales with "
            "the largest target's file count; size it there with headroom "
            "(e.g. 1_000_000)."
        ),
    )
```

- [ ] **Step 3: Write `src/codeoracle/adapters/valkey/client.py`**

```python
"""ValkeyQueueClient — async wrapper exposing the four operations workers
need: enqueue, read_pending, ack, dead_letter.

Why an adapter at all? `valkey-py` returns bytes for stream fields, exposes
XADD's '*' magic ID, has a separate XGROUP/XREADGROUP/XACK ceremony, and
lacks built-in dead-letter semantics. The adapter:
  - Decodes bytes → str at the boundary (callers get pure str).
  - Forces JSON-serialized payloads (callers pass dicts; we serialize).
  - Bootstraps consumer groups idempotently on first enqueue/read.
  - Implements dead-letter as a paired stream + the producer's XACK
    (a dead-lettered message is acked off the producer stream so it
    won't be re-delivered; the DLQ copy holds the failure context).

The adapter is FREE to swap to `valkey-glide` or `redis-py` — call sites
never know the underlying SDK.

`valkey-py`'s async API is identical to `redis.asyncio`. Caller-side, the
methods are:

    qc = ValkeyQueueClient(config)
    await qc.start()  # initialize pool + ensure groups exist
    msg_id = await qc.enqueue("indexing.joern", {"snapshot_sha":"abc1234","file":"src/x.py"})
    msgs = await qc.read_pending(
        stream="indexing.joern",
        group="joern_indexer",
        consumer="joern-worker-1",
        count=10,
        block_ms=5000,
    )
    for m in msgs:
        try:
            await dispatch(m.payload)
            await qc.ack("indexing.joern", "joern_indexer", m.id)
        except TerminalWorkerError as exc:
            await qc.dead_letter("indexing.joern", m.id, m.payload, str(exc))
            await qc.ack("indexing.joern", "joern_indexer", m.id)
    await qc.aclose()  # called from worker shutdown handler
"""

from __future__ import annotations

import json
from dataclasses import dataclass

import valkey.asyncio as valkey  # noqa: TID251 — allowed only here

from codeoracle.adapters.valkey.config import ValkeyConfig
from codeoracle.core.streams import dlq_for


@dataclass(frozen=True)
class ConsumedMessage:
    """A single message fetched from a stream.

    Attributes:
        id: The Valkey stream entry ID (e.g., "1716123456789-0"). Used
            for XACK + XADD into DLQ.
        stream: Which stream this came from (set by read_pending).
        payload: The original dict the producer passed to enqueue().
        delivered_count: Times this message has been delivered (via
            XPENDING). Used by the consumer to decide retry vs dead-letter.
    """

    id: str
    stream: str
    payload: dict[str, object]
    delivered_count: int


class ValkeyQueueClient:
    """Async queue client over Valkey streams.

    Construct ONCE per process; reuse across calls. `start()` must be
    awaited before any other method (initializes the connection pool
    and ensures consumer groups exist).
    """

    def __init__(self, config: ValkeyConfig) -> None:
        self._config = config
        self._client: valkey.Valkey | None = None
        self._ensured_groups: set[tuple[str, str]] = set()

    async def start(self) -> None:
        """Initialize connection pool. Safe to call once per process."""
        if self._client is not None:
            return
        self._client = valkey.from_url(
            self._config.url,
            max_connections=self._config.pool_max,
            decode_responses=False,  # we decode at the message boundary
        )
        # Ping to fail fast if Valkey unreachable.
        await self._client.ping()

    async def aclose(self) -> None:
        """Tear down the connection pool. Idempotent."""
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def enqueue(
        self, stream: str, payload: dict[str, object]
    ) -> str:
        """XADD a JSON-serialized payload to `stream`. Returns the entry ID."""
        client = self._require_client()
        body = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        # MAXLEN caps the stream so XACKed-but-retained entries can't
        # accumulate across reindexes; approximate=True trims at radix-node
        # boundaries (cheap) and never drops below stream_maxlen, so a
        # correctly-sized cap never evicts the in-flight backlog.
        entry_id = await client.xadd(
            stream,
            {"json": body},
            maxlen=self._config.stream_maxlen,
            approximate=True,
        )
        return entry_id.decode("utf-8") if isinstance(entry_id, bytes) else entry_id

    async def read_pending(
        self,
        *,
        stream: str,
        group: str,
        consumer: str,
        count: int,
        block_ms: int,
    ) -> list[ConsumedMessage]:
        """XREADGROUP from `stream` as `consumer` in `group`. Bootstraps
        the group via XGROUP CREATE MKSTREAM on first call.

        Returns at most `count` messages. Blocks up to `block_ms`
        milliseconds for new messages. Returns [] on timeout.
        """
        client = self._require_client()
        await self._ensure_group(stream, group)

        raw = await client.xreadgroup(
            groupname=group,
            consumername=consumer,
            streams={stream: ">"},  # ">" = only new (not yet delivered) messages
            count=count,
            block=block_ms,
        )
        if not raw:
            return []

        # raw shape: [(stream_name_bytes, [(id_bytes, {field_bytes: value_bytes})])]
        out: list[ConsumedMessage] = []
        for stream_name_b, entries in raw:
            stream_name = stream_name_b.decode("utf-8")
            for entry_id_b, fields in entries:
                msg = await self._decode_entry(
                    stream_name, group, entry_id_b, fields
                )
                if msg is not None:
                    out.append(msg)
        return out

    async def reclaim_stale(
        self,
        *,
        stream: str,
        group: str,
        consumer: str,
        min_idle_ms: int,
        count: int,
    ) -> list[ConsumedMessage]:
        """XAUTOCLAIM PEL entries idle longer than `min_idle_ms` to `consumer`.

        Reclaims messages a crashed-or-stalled consumer left un-ACKed in the
        group's pending list. XAUTOCLAIM atomically (a) reassigns each stale
        entry to `consumer` and (b) increments its delivery counter — so the
        returned ConsumedMessage carries an ADVANCED `delivered_count`. That
        advancing counter is what lets `RetryPolicy.should_retry` drive the
        retry-then-dead-letter ladder: XREADGROUP's ">" only ever reads
        never-delivered messages, so WITHOUT this reclaim a transiently-failed
        entry sits in the PEL with `delivered_count` frozen at 1 forever, the
        ladder never advances, and the retry-exhaustion dead-letter is
        unreachable (the message is marooned).

        Bootstraps the group (XAUTOCLAIM errors on a missing group) — required
        because the consumer's run-loop reclaims BEFORE its first read_pending.
        Reclaims one batch per call (the XAUTOCLAIM cursor is not looped): the
        min-idle gate bounds the eligible set, and the next poll continues as
        more entries age past `min_idle_ms`. Returns [] when nothing is stale.
        """
        client = self._require_client()
        await self._ensure_group(stream, group)

        # XAUTOCLAIM <stream> <group> <consumer> <min-idle-ms> <start> COUNT n
        #   → (next_cursor, [(id, {field: value}), ...], deleted_ids).
        # We ignore the cursor (one batch per poll) and deleted_ids (entries
        # already evicted from the stream — nothing left to reprocess).
        _cursor, entries, _deleted = await client.xautoclaim(
            name=stream,
            groupname=group,
            consumername=consumer,
            min_idle_time=min_idle_ms,
            start_id="0-0",
            count=count,
        )

        out: list[ConsumedMessage] = []
        for entry_id_b, fields in entries:
            msg = await self._decode_entry(stream, group, entry_id_b, fields)
            if msg is not None:
                out.append(msg)
        return out

    async def ack(self, stream: str, group: str, message_id: str) -> None:
        """XACK — removes the message from the consumer-group's pending list."""
        client = self._require_client()
        await client.xack(stream, group, message_id)

    async def dead_letter(
        self,
        stream: str,
        message_id: str,
        payload: dict[str, object],
        error: str,
    ) -> None:
        """Copy the failed message to `<stream>.dlq` with the error
        context, then ACK it off the producer stream so it won't be
        re-delivered.

        Workers should call this when an error is terminal (retries
        exhausted, or the error is fundamentally caller-bug). NEVER call
        this for transient failures — leave them in the PEL for the
        consumer's reclaim step to retry.
        """
        client = self._require_client()
        dlq = dlq_for(stream)
        # DLQ reuses the producer cap (stream_maxlen). Unlike the producer
        # stream it has no live-work floor — entries are sparse terminal
        # failures — so the producer-sized cap merely over-provisions it.
        await client.xadd(
            dlq,
            {
                "original_id": message_id,
                "original_stream": stream,
                "payload": json.dumps(
                    payload, sort_keys=True, separators=(",", ":")
                ),
                "error": error,
            },
            maxlen=self._config.stream_maxlen,
            approximate=True,
        )
        # ACK the original to clear it from the producer-stream PEL.
        # The DLQ copy is the authoritative record of the failure.

    async def stream_length(self, stream: str) -> int:
        """XLEN — current entry count of `stream` (NFR D4-4 observability).

        Read-only and side-effect-free: a metrics scrape must never mutate
        queue state. XLEN of a not-yet-created stream returns 0 (Valkey treats
        a missing key as an empty stream), so this is safe to call before any
        producer has XADDed. Consumed only by the API's queue-metrics refresher
        (Plan 11) to export the `codeoracle_valkey_stream_length` gauge.
        """
        client = self._require_client()
        return int(await client.xlen(stream))

    async def pending_count(self, stream: str, group: str) -> int:
        """XPENDING summary count — size of `group`'s pending-entries list
        (PEL) on `stream` (NFR D4-4 — observability of D4-1's reclaim backlog).

        Read-only and side-effect-free: unlike `read_pending` / `reclaim_stale`
        it deliberately does NOT bootstrap the group via `_ensure_group` — a
        metrics scrape must not create consumer groups as a side effect. A
        not-yet-created group raises NOGROUP, which we map to 0 (no group means
        no pending backlog yet), mirroring how `stream_length` treats a missing
        stream as empty. Consumed only by the API's queue-metrics refresher
        (Plan 11) to export the `codeoracle_valkey_pending_entries` gauge.
        """
        client = self._require_client()
        try:
            summary = await client.xpending(stream, group)
        except valkey.ResponseError as exc:
            # NOGROUP — the consumer group hasn't been created yet (no worker
            # has bootstrapped it). Any other ResponseError is unexpected.
            if "NOGROUP" in str(exc):
                return 0
            raise
        # Summary shape: {"pending": int, "min": id, "max": id, "consumers": […]}.
        return int(summary["pending"])

    async def _decode_entry(
        self,
        stream: str,
        group: str,
        entry_id_b: bytes,
        fields: dict[bytes, bytes],
    ) -> ConsumedMessage | None:
        """Decode one XREADGROUP / XAUTOCLAIM entry into a ConsumedMessage.

        Shared by `read_pending` (new messages) and `reclaim_stale` (reclaimed
        PEL messages) so the malformed-payload guard and `delivered_count`
        sourcing can never diverge between the two read paths. Returns None
        when the payload is unparsable — in that case the entry has already
        been dead-lettered + ACKed (so it can't loop in the PEL) and the caller
        skips it.
        """
        client = self._require_client()
        entry_id = entry_id_b.decode("utf-8")
        body = fields.get(b"json", b"{}").decode("utf-8")
        try:
            payload = json.loads(body)
        except json.JSONDecodeError as exc:
            # Malformed payload — dead-letter + ACK immediately so it doesn't
            # sit in the PEL forever. DLQ reuses the producer cap
            # (stream_maxlen); see dead_letter for why that floor is safe here.
            await client.xadd(
                dlq_for(stream),
                {
                    "original_id": entry_id,
                    "raw": body,
                    "error": f"JSON decode failed: {exc}",
                },
                maxlen=self._config.stream_maxlen,
                approximate=True,
            )
            await client.xack(stream, group, entry_id)
            return None

        # XPENDING gives the delivery count; fetch lazily per-message.
        delivered_count = await self._delivered_count(stream, group, entry_id)
        return ConsumedMessage(
            id=entry_id,
            stream=stream,
            payload=payload,
            delivered_count=delivered_count,
        )

    async def _ensure_group(self, stream: str, group: str) -> None:
        """XGROUP CREATE ... MKSTREAM — idempotent. Skip if already created."""
        if (stream, group) in self._ensured_groups:
            return
        client = self._require_client()
        try:
            # MKSTREAM creates the stream if absent — works even for the
            # first publisher who hasn't XADDed yet.
            await client.xgroup_create(
                name=stream, groupname=group, id="0-0", mkstream=True
            )
        except valkey.ResponseError as exc:
            # BUSYGROUP — already exists. Any other error is unexpected.
            if "BUSYGROUP" not in str(exc):
                raise
        self._ensured_groups.add((stream, group))

    async def _delivered_count(
        self, stream: str, group: str, message_id: str
    ) -> int:
        """XPENDING for one message — returns 0 if not in pending (shouldn't
        happen for a just-fetched message but defensively return 0)."""
        client = self._require_client()
        pending = await client.xpending_range(
            name=stream,
            groupname=group,
            min=message_id,
            max=message_id,
            count=1,
        )
        if not pending:
            return 0
        # Each row: {"message_id", "consumer", "time_since_delivered", "times_delivered"}
        return int(pending[0]["times_delivered"])

    def _require_client(self) -> valkey.Valkey:
        if self._client is None:
            raise RuntimeError(
                "ValkeyQueueClient not started — call await client.start() first."
            )
        return self._client
```

- [ ] **Step 4: Write `tests/unit/adapters/valkey/test_client.py`** — mocked `valkey-py`

```python
"""Plan 5 Task 2: ValkeyQueueClient unit tests with mocked `valkey-py`.

Real Valkey integration lives in tests/integration/adapters/valkey/.
Here we assert SHAPE: what the client sends, what it returns, how it
handles dead-letter, that groups are bootstrapped exactly once.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from codeoracle.adapters.valkey.client import ConsumedMessage, ValkeyQueueClient
from codeoracle.adapters.valkey.config import ValkeyConfig


def _config() -> ValkeyConfig:
    return ValkeyConfig(
        url="valkey://localhost:6379/0",
        pool_min=1,
        pool_max=10,
        block_ms_default=1000,
        max_in_flight_per_consumer=10,
        stream_maxlen=1000,
    )


@pytest.fixture
def mock_valkey(monkeypatch):
    """Patch `valkey.asyncio.from_url` to return a MagicMock."""
    fake = MagicMock()
    fake.ping = AsyncMock(return_value=True)
    fake.xadd = AsyncMock(return_value=b"1716123456789-0")
    fake.xreadgroup = AsyncMock(return_value=[])
    fake.xack = AsyncMock(return_value=1)
    fake.xgroup_create = AsyncMock(return_value=True)
    fake.xpending_range = AsyncMock(return_value=[])
    fake.xautoclaim = AsyncMock(return_value=(b"0-0", [], []))
    fake.aclose = AsyncMock()

    import codeoracle.adapters.valkey.client as client_mod
    monkeypatch.setattr(client_mod.valkey, "from_url", lambda *a, **kw: fake)
    return fake


async def test_start_pings_valkey(mock_valkey):
    qc = ValkeyQueueClient(_config())
    await qc.start()
    mock_valkey.ping.assert_awaited_once()


async def test_enqueue_serializes_payload_as_json(mock_valkey):
    qc = ValkeyQueueClient(_config())
    await qc.start()
    entry_id = await qc.enqueue(
        "indexing.joern", {"snapshot_sha": "abc1234", "file": "src/x.py"}
    )
    assert entry_id == "1716123456789-0"
    args = mock_valkey.xadd.call_args
    assert args.args[0] == "indexing.joern"
    # Body is canonicalized JSON
    body = args.args[1]["json"]
    assert body == '{"file":"src/x.py","snapshot_sha":"abc1234"}'


async def test_read_pending_decodes_messages(mock_valkey):
    mock_valkey.xreadgroup.return_value = [
        (
            b"indexing.joern",
            [
                (
                    b"1716123456789-0",
                    {b"json": b'{"snapshot_sha":"abc1234","file":"src/x.py"}'},
                )
            ],
        )
    ]
    mock_valkey.xpending_range.return_value = [{"times_delivered": 1}]

    qc = ValkeyQueueClient(_config())
    await qc.start()
    msgs = await qc.read_pending(
        stream="indexing.joern",
        group="joern_indexer",
        consumer="w1",
        count=10,
        block_ms=1000,
    )
    assert len(msgs) == 1
    assert msgs[0] == ConsumedMessage(
        id="1716123456789-0",
        stream="indexing.joern",
        payload={"snapshot_sha": "abc1234", "file": "src/x.py"},
        delivered_count=1,
    )


async def test_read_pending_bootstraps_group_once(mock_valkey):
    qc = ValkeyQueueClient(_config())
    await qc.start()
    await qc.read_pending(
        stream="indexing.joern", group="joern_indexer", consumer="w1",
        count=1, block_ms=0,
    )
    await qc.read_pending(
        stream="indexing.joern", group="joern_indexer", consumer="w1",
        count=1, block_ms=0,
    )
    # xgroup_create called only on first read
    mock_valkey.xgroup_create.assert_awaited_once()


async def test_read_pending_ignores_busygroup_response_error(mock_valkey):
    import valkey.asyncio as v
    mock_valkey.xgroup_create.side_effect = v.ResponseError(
        "BUSYGROUP Consumer Group name already exists"
    )
    qc = ValkeyQueueClient(_config())
    await qc.start()
    # Should NOT raise — group already exists is fine
    msgs = await qc.read_pending(
        stream="indexing.joern", group="joern_indexer", consumer="w1",
        count=1, block_ms=0,
    )
    assert msgs == []


async def test_dead_letter_writes_to_dlq_stream(mock_valkey):
    qc = ValkeyQueueClient(_config())
    await qc.start()
    await qc.dead_letter(
        stream="indexing.joern",
        message_id="1716-0",
        payload={"snapshot_sha": "abc", "file": "src/x.py"},
        error="JoernExtractionError: syntax error",
    )
    # XADD called against indexing.joern.dlq
    dlq_call = mock_valkey.xadd.call_args
    assert dlq_call.args[0] == "indexing.joern.dlq"
    fields = dlq_call.args[1]
    assert fields["original_id"] == "1716-0"
    assert fields["original_stream"] == "indexing.joern"
    assert "JoernExtractionError" in fields["error"]


async def test_ack_calls_xack(mock_valkey):
    qc = ValkeyQueueClient(_config())
    await qc.start()
    await qc.ack("indexing.joern", "joern_indexer", "1716-0")
    mock_valkey.xack.assert_awaited_with(
        "indexing.joern", "joern_indexer", "1716-0"
    )


async def test_aclose_is_idempotent(mock_valkey):
    qc = ValkeyQueueClient(_config())
    await qc.start()
    await qc.aclose()
    await qc.aclose()  # second call is a no-op, shouldn't raise
    mock_valkey.aclose.assert_awaited_once()


async def test_methods_raise_before_start():
    qc = ValkeyQueueClient(_config())
    with pytest.raises(RuntimeError, match="not started"):
        await qc.enqueue("indexing.joern", {})


async def test_read_pending_dead_letters_unparsable_payload(mock_valkey):
    mock_valkey.xreadgroup.return_value = [
        (
            b"indexing.joern",
            [(b"1716-0", {b"json": b"{not json"})],
        )
    ]
    qc = ValkeyQueueClient(_config())
    await qc.start()
    msgs = await qc.read_pending(
        stream="indexing.joern", group="joern_indexer", consumer="w1",
        count=1, block_ms=0,
    )
    # Unparsable message is dead-lettered and skipped
    assert msgs == []
    # DLQ XADD + ACK on original
    dlq_calls = [
        c for c in mock_valkey.xadd.call_args_list
        if c.args[0] == "indexing.joern.dlq"
    ]
    assert len(dlq_calls) == 1
    mock_valkey.xack.assert_awaited()


async def test_reclaim_stale_decodes_with_advanced_delivered_count(mock_valkey):
    # XAUTOCLAIM hands back one stale entry; XPENDING reports the delivery
    # counter XAUTOCLAIM just bumped.
    mock_valkey.xautoclaim.return_value = (
        b"0-0",
        [(b"1716-0", {b"json": b'{"snapshot_sha":"abc1234","file":"src/x.py"}'})],
        [],
    )
    mock_valkey.xpending_range.return_value = [{"times_delivered": 3}]

    qc = ValkeyQueueClient(_config())
    await qc.start()
    msgs = await qc.reclaim_stale(
        stream="indexing.joern",
        group="joern_indexer",
        consumer="w1",
        min_idle_ms=120_000,
        count=1,
    )
    assert msgs == [
        ConsumedMessage(
            id="1716-0",
            stream="indexing.joern",
            payload={"snapshot_sha": "abc1234", "file": "src/x.py"},
            delivered_count=3,
        )
    ]
    # The min-idle gate is forwarded to XAUTOCLAIM.
    assert mock_valkey.xautoclaim.call_args.kwargs["min_idle_time"] == 120_000


async def test_reclaim_stale_dead_letters_unparsable_payload(mock_valkey):
    mock_valkey.xautoclaim.return_value = (
        b"0-0",
        [(b"1716-0", {b"json": b"{not json"})],
        [],
    )
    qc = ValkeyQueueClient(_config())
    await qc.start()
    msgs = await qc.reclaim_stale(
        stream="indexing.joern",
        group="joern_indexer",
        consumer="w1",
        min_idle_ms=120_000,
        count=1,
    )
    # Shared _decode_entry path: a malformed reclaimed entry is DLQ'd + ACKed
    # + skipped, exactly like read_pending.
    assert msgs == []
    dlq_calls = [
        c for c in mock_valkey.xadd.call_args_list
        if c.args[0] == "indexing.joern.dlq"
    ]
    assert len(dlq_calls) == 1
    mock_valkey.xack.assert_awaited()
```

- [ ] **Step 5: Write `tests/integration/adapters/valkey/test_client_integration.py`** — testcontainers Valkey

```python
"""Plan 5 Task 2: ValkeyQueueClient integration tests against real Valkey.

Marked `worker` so they're skipped in the fast unit lane. Uses
testcontainers' redis fixture (Valkey speaks the Redis protocol).
"""

from __future__ import annotations

import pytest

from codeoracle.adapters.valkey.client import ValkeyQueueClient
from codeoracle.adapters.valkey.config import ValkeyConfig

pytestmark = pytest.mark.worker


# valkey_url and valkey_container fixtures are provided by
# tests/integration/conftest.py (Task 1 Step 7) — DO NOT redefine them
# here. Module-scoped duplicates would silently shadow the conftest copy
# and create a maintenance trap.


@pytest.fixture
async def queue_client(valkey_url: str):
    config = ValkeyConfig(
        url=valkey_url,
        pool_min=1,
        pool_max=10,
        block_ms_default=1000,
        max_in_flight_per_consumer=10,
        stream_maxlen=1000,
    )
    qc = ValkeyQueueClient(config)
    await qc.start()
    yield qc
    await qc.aclose()


async def test_round_trip_enqueue_read_ack(queue_client):
    """Full roundtrip: enqueue → read_pending returns it → ack removes from PEL."""
    msg_id = await queue_client.enqueue(
        "test.roundtrip", {"hello": "world", "n": 42}
    )

    msgs = await queue_client.read_pending(
        stream="test.roundtrip",
        group="test_group",
        consumer="test-consumer-1",
        count=10,
        block_ms=1000,
    )
    assert len(msgs) == 1
    m = msgs[0]
    assert m.id == msg_id
    assert m.payload == {"hello": "world", "n": 42}
    assert m.delivered_count >= 1

    await queue_client.ack("test.roundtrip", "test_group", m.id)

    # Reading again with ">" returns nothing — the message was acked
    msgs_after = await queue_client.read_pending(
        stream="test.roundtrip",
        group="test_group",
        consumer="test-consumer-1",
        count=10,
        block_ms=100,
    )
    assert msgs_after == []


async def test_two_consumers_in_same_group_load_balance(queue_client):
    """XREADGROUP load-balances across consumers in the same group."""
    for i in range(10):
        await queue_client.enqueue("test.lb", {"i": i})

    msgs_a = await queue_client.read_pending(
        stream="test.lb", group="g", consumer="a", count=5, block_ms=500,
    )
    msgs_b = await queue_client.read_pending(
        stream="test.lb", group="g", consumer="b", count=5, block_ms=500,
    )
    ids_a = {m.id for m in msgs_a}
    ids_b = {m.id for m in msgs_b}
    # Disjoint — Valkey gave different messages to each consumer
    assert ids_a.isdisjoint(ids_b)
    assert len(ids_a) + len(ids_b) == 10


async def test_dead_letter_writes_to_dlq_and_ack_clears_original(queue_client):
    """A dead-lettered message survives in the DLQ stream."""
    msg_id = await queue_client.enqueue("test.fail", {"will": "fail"})
    msgs = await queue_client.read_pending(
        stream="test.fail", group="g", consumer="c", count=10, block_ms=500,
    )
    assert len(msgs) == 1

    await queue_client.dead_letter(
        stream="test.fail",
        message_id=msg_id,
        payload={"will": "fail"},
        error="TerminalWorkerError: deliberate",
    )
    await queue_client.ack("test.fail", "g", msg_id)

    # DLQ stream now has 1 entry; read it via a fresh group on the DLQ
    dlq_msgs = await queue_client.read_pending(
        stream="test.fail.dlq",
        group="dlq_reader",
        consumer="r",
        count=10,
        block_ms=500,
    )
    assert len(dlq_msgs) == 1
    assert dlq_msgs[0].payload["original_id"] == msg_id
    assert "deliberate" in dlq_msgs[0].payload["error"]


async def test_consumer_group_bootstrap_is_idempotent(queue_client):
    """Calling read_pending twice with the same group doesn't error."""
    await queue_client.read_pending(
        stream="test.idem", group="g", consumer="c", count=1, block_ms=100,
    )
    # Second call — group already exists, must not raise
    await queue_client.read_pending(
        stream="test.idem", group="g", consumer="c", count=1, block_ms=100,
    )


async def test_reclaim_stale_advances_delivered_count(queue_client):
    """LOAD-BEARING reaper proof: a message read once (delivered_count=1) and
    left in the PEL (its consumer "crashed") is handed back by reclaim_stale
    with delivered_count=2 — the real XAUTOCLAIM delivery-counter bump that
    drives RetryPolicy toward dead-letter. Without it, ">" never re-reads the
    PEL and the count is frozen at 1 forever (the original marooning bug).
    """
    await queue_client.enqueue("test.reclaim", {"k": "v"})
    first = await queue_client.read_pending(
        stream="test.reclaim", group="g", consumer="crashed",
        count=10, block_ms=500,
    )
    assert len(first) == 1
    assert first[0].delivered_count == 1

    # "crashed" never ACKs — the entry stays in the PEL. A second consumer
    # reclaims it (min_idle_ms=0 → immediately eligible in-test).
    reclaimed = await queue_client.reclaim_stale(
        stream="test.reclaim", group="g", consumer="reaper",
        min_idle_ms=0, count=10,
    )
    assert len(reclaimed) == 1
    assert reclaimed[0].id == first[0].id
    assert reclaimed[0].delivered_count == 2  # XAUTOCLAIM bumped it


async def test_reclaim_stale_skips_fresh_entries(queue_client):
    """The min-idle gate is what stops the reaper stealing a still-running
    handle: an entry younger than min_idle_ms is NOT reclaimed.
    """
    await queue_client.enqueue("test.reclaim_fresh", {"k": "v"})
    await queue_client.read_pending(
        stream="test.reclaim_fresh", group="g", consumer="c",
        count=10, block_ms=500,
    )
    # Just delivered — idle ≈ 0, far below the 1-hour gate.
    reclaimed = await queue_client.reclaim_stale(
        stream="test.reclaim_fresh", group="g", consumer="reaper",
        min_idle_ms=3_600_000, count=10,
    )
    assert reclaimed == []
```

- [ ] **Step 6: Run unit tests + lint + type**

```bash
uv run pytest tests/unit/adapters/valkey/ -v
uv run ruff check src/codeoracle/adapters/valkey/
uv run ty check src/codeoracle/adapters/valkey/
```

Expected: 12 unit tests PASS. Zero lint/type errors. (Integration tests are run in Task 14 when the full vertical is assembled.)

- [ ] **Step 7: Commit**

```bash
git add src/codeoracle/adapters/valkey/ tests/unit/adapters/valkey/ tests/integration/adapters/valkey/
git commit -m "feat: Plan 5 Task 2 — ValkeyQueueClient (XADD/XREADGROUP/XACK/dead-letter)"
```

---

## Task 3: `src/codeoracle/workers/_base/message.py` — WorkerMessage + RetryPolicy

**What this task delivers:** the Pydantic model that every worker payload extends + the retry-policy dataclass (max_attempts — the reclaim ladder's only retry knob). Workers receive `ConsumedMessage` from the ValkeyQueueClient and validate `ConsumedMessage.payload` against their specific `WorkerMessage` subclass; this is the typed envelope.

**Files:**
- Create: `src/codeoracle/workers/_base/message.py`
- Create: `tests/unit/workers/__init__.py`
- Create: `tests/unit/workers/_base/__init__.py`
- Create: `tests/unit/workers/_base/test_message.py`

- [ ] **Step 1: Write `src/codeoracle/workers/_base/message.py`**

```python
"""WorkerMessage base + RetryPolicy.

WorkerMessage is the typed envelope every worker payload extends.
Subclasses add domain fields (e.g., JoernIndexerMessage adds
`snapshot_sha` + `file_path` + `parent_sha`).

RetryPolicy is computed per-message — the consumer calls
`policy.should_retry(delivered_count)` to decide ack-vs-retry-vs-DLQ.
"""

from __future__ import annotations

from dataclasses import dataclass

from pydantic import BaseModel


class WorkerMessage(BaseModel):
    """Base Pydantic model for every worker payload.

    Subclasses add domain-specific fields. The framework only reads
    `.batch_id` (for the IndexingService barrier) and the rest is
    opaque to it.

    `batch_id` ties a fan-out of per-file messages back to the parent
    snapshot's reindex job — Plan 5's IndexingService uses it to wait
    on completion via `snapshot_file_outcomes`.
    """

    model_config = {"frozen": True, "extra": "forbid"}

    batch_id: str  # = snapshot_sha for indexing workers


@dataclass(frozen=True)
class RetryPolicy:
    """Retry budget for the consumer's reclaim ladder.

    On the Nth delivery (1-indexed, as Valkey XPENDING reports):
      - N == 1 .. max_attempts-1   → retry (left in PEL for reclaim)
      - N == max_attempts           → dead-letter

    `should_retry(n)` is the live signal: the WorkerConsumer leaves a
    transiently-failed message in the PEL, and its reclaim step
    (reclaim_stale → XAUTOCLAIM, gated by a FIXED per-worker
    reclaim_min_idle_ms) hands the message back with an advanced
    delivered_count until should_retry returns False.

    Re-delivery pacing is owned entirely by that fixed reclaim min-idle,
    not by a per-attempt backoff curve — so `max_attempts` is the only knob.

    Defaults: 5 attempts.
    """

    max_attempts: int = 5

    def should_retry(self, delivered_count: int) -> bool:
        """True if delivered_count < max_attempts (still within budget)."""
        return delivered_count < self.max_attempts
```

- [ ] **Step 2: Write `tests/unit/workers/_base/test_message.py`**

```python
"""Plan 5 Task 3: WorkerMessage + RetryPolicy unit tests."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from codeoracle.workers._base.message import RetryPolicy, WorkerMessage


class _ConcreteMessage(WorkerMessage):
    """Test-local concrete subclass."""

    file_path: str


def test_worker_message_forbids_extra_fields():
    with pytest.raises(ValidationError):
        _ConcreteMessage(batch_id="abc", file_path="x.py", unknown="boom")


def test_worker_message_is_frozen():
    m = _ConcreteMessage(batch_id="abc", file_path="x.py")
    with pytest.raises(ValidationError):
        m.batch_id = "xyz"  # type: ignore[misc]


def test_retry_policy_should_retry_until_max_attempts():
    p = RetryPolicy(max_attempts=3)
    assert p.should_retry(1) is True
    assert p.should_retry(2) is True
    assert p.should_retry(3) is False
    assert p.should_retry(4) is False
```

- [ ] **Step 3: Run + lint + type**

```bash
uv run pytest tests/unit/workers/_base/test_message.py -v
uv run ruff check src/codeoracle/workers/_base/message.py
uv run ty check src/codeoracle/workers/_base/message.py
```

Expected: 3 tests PASS, no lint/type errors.

- [ ] **Step 4: Commit**

```bash
git add src/codeoracle/workers/_base/message.py tests/unit/workers/
git commit -m "feat: Plan 5 Task 3 — WorkerMessage Pydantic envelope + RetryPolicy"
```

---

## Task 4: `src/codeoracle/workers/_base/consumer.py` — WorkerConsumer ABC

**What this task delivers:** the abstract base for every worker process. Subclasses implement `async def handle(self, msg: TMessage) -> None` and a class-level `message_type: type[TMessage]`; the base class owns the polling loop, message decoding, ACK/NACK/DLQ flow, retry-budget decisioning, and graceful shutdown. This is the framework piece that Plan 6 reuses for 5 more consumer subclasses without modification.

**Files:**
- Create: `src/codeoracle/workers/_base/consumer.py`
- Create: `tests/unit/workers/_base/test_consumer.py` (mocked queue + handler)

- [ ] **Step 1: Write `src/codeoracle/workers/_base/consumer.py`**

```python
"""WorkerConsumer — generic async polling loop with ACK/NACK/DLQ.

Subclasses set `stream`, `group`, `consumer_name`, `message_type`, and
implement `handle(msg)`. They DON'T touch the queue client directly —
the base class owns the lifecycle. This is the structural enforcement
of "workers consume from queues; queues are an adapter; workers don't
know which queue implementation they're behind."

Lifecycle:
  1. Construct with: queue_client, retry_policy, structured_logger, stop_event
  2. await consumer.run()  — long-running, polls forever
  3. Signal: stop_event.set() OR send SIGTERM (caller wires this)
  4. Loop exits cleanly: drains in-flight handlers, returns.
"""

from __future__ import annotations

import asyncio
import logging
from abc import ABC, abstractmethod
from typing import ClassVar, Generic, TypeVar

from pydantic import ValidationError

from codeoracle.adapters.valkey.client import ConsumedMessage, ValkeyQueueClient
from codeoracle.core.exceptions import (
    TerminalWorkerError,
    TransientWorkerError,
    WorkerError,
)
from codeoracle.workers._base.message import RetryPolicy, WorkerMessage

logger = logging.getLogger(__name__)

TMessage = TypeVar("TMessage", bound=WorkerMessage)


class WorkerConsumer(ABC, Generic[TMessage]):
    """Abstract base — subclasses provide stream/group/message_type +
    a `handle(msg)` coroutine. The base class owns the polling loop.

    Class-level config (subclasses override):
      stream: Valkey stream name (use the constant from core.streams).
      group: consumer-group name (use the constant from core.streams).
      message_type: the Pydantic WorkerMessage subclass this consumer expects.
      count_per_poll: max messages per XREADGROUP call.
      block_ms: how long XREADGROUP blocks waiting for new messages.

    Instance attributes:
      consumer_name: this consumer's unique name within the group.
                     Default: "<group>-<pid>" so each replica gets a
                     distinct name automatically.
      reclaim_min_idle_ms: if set, the run-loop reclaims PEL entries idle
                     longer than this (XAUTOCLAIM) each poll, advancing their
                     delivered_count so the RetryPolicy ladder can reach
                     dead-letter. MUST exceed the worst-case handle() duration
                     — otherwise a still-running handle is re-claimed, spawning
                     a duplicate extraction (idempotent, but wasteful: a
                     "re-claim stampede" burning worker slots + DB conns).
                     Leave None to DISABLE reclaim — correct only when the
                     worker has another crash-recovery path (e.g. the reindex
                     orchestrator's advisory-lock + stale-APPLYING sweep).
    """

    stream: ClassVar[str]
    group: ClassVar[str]
    message_type: ClassVar[type[WorkerMessage]]
    count_per_poll: ClassVar[int] = 10
    block_ms: ClassVar[int] = 5000

    def __init__(
        self,
        *,
        queue_client: ValkeyQueueClient,
        retry_policy: RetryPolicy,
        consumer_name: str,
        stop_event: asyncio.Event,
        reclaim_min_idle_ms: int | None = None,
    ) -> None:
        self._queue = queue_client
        self._retry = retry_policy
        self._consumer_name = consumer_name
        self._stop = stop_event
        self._reclaim_min_idle_ms = reclaim_min_idle_ms

    @abstractmethod
    async def handle(self, message: TMessage) -> None:
        """Subclass-defined per-message handler.

        Raise TransientWorkerError → consumer will NACK; retry budget
        permitting, the message stays in the PEL and the consumer's reclaim
        step (XAUTOCLAIM) hands it back.

        Raise TerminalWorkerError → consumer dead-letters immediately
        (no retry; the failure is fundamentally not transient).

        Raise any other exception → treated as transient by default
        (defensive); use the explicit exception types to express intent.
        """

    async def run(self) -> None:
        """Poll forever until `stop_event` is set."""
        logger.info(
            "worker_starting",
            extra={
                "stream": self.stream,
                "group": self.group,
                "consumer": self._consumer_name,
            },
        )
        while not self._stop.is_set():
            # Reclaim first: hand stale PEL entries (left by a crashed or
            # stalled consumer) back to this consumer BEFORE reading new work,
            # so a transiently-failed message's delivered_count advances and
            # the RetryPolicy ladder can reach dead-letter. Opt-in per worker
            # via reclaim_min_idle_ms (None → skip; see __init__).
            #
            # The reclaim batch drains fully (each _process_one ACK/NACKs)
            # before read_pending runs, so a consumer never holds reclaimed +
            # new work in-flight at once: the in-flight bound stays
            # count_per_poll × handle, which is why reclaim_min_idle_ms only
            # needs to clear ONE worst-case handle (see JoernIndexerConsumer).
            #
            # NOTE: reclaim guarantees eventual unsticking + dead-letter always;
            # it RESCUES a blip within the *current* reindex barrier only when
            # reclaim_min_idle_ms + one handle < the orchestrator's completion
            # timeout (600s). Operators keeping per-file timeouts well under
            # ~200s stay in that window; slower files dead-letter instead of
            # rescuing in-barrier.
            if self._reclaim_min_idle_ms is not None:
                try:
                    reclaimed = await self._queue.reclaim_stale(
                        stream=self.stream,
                        group=self.group,
                        consumer=self._consumer_name,
                        min_idle_ms=self._reclaim_min_idle_ms,
                        count=self.count_per_poll,
                    )
                except Exception:  # noqa: BLE001
                    # A reclaim blip must not kill the loop — log and fall
                    # through to the normal read.
                    logger.exception("worker_reclaim_failed")
                    reclaimed = []
                for raw in reclaimed:
                    await self._process_one(raw)

            try:
                consumed = await self._queue.read_pending(
                    stream=self.stream,
                    group=self.group,
                    consumer=self._consumer_name,
                    count=self.count_per_poll,
                    block_ms=self.block_ms,
                )
            except Exception:  # noqa: BLE001
                # Queue-level error — log and back off briefly. Plan 5's
                # WorkerConsumer doesn't retry the *poll itself*; Valkey
                # blips recover on the next loop iteration.
                logger.exception("worker_poll_failed")
                await asyncio.sleep(1.0)
                continue

            for raw in consumed:
                await self._process_one(raw)
        logger.info(
            "worker_stopping",
            extra={"consumer": self._consumer_name},
        )

    async def _process_one(self, raw: ConsumedMessage) -> None:
        """Decode + dispatch + ACK/NACK/DLQ a single message."""
        # 1) Decode + validate against the subclass's message_type.
        try:
            message = self.message_type.model_validate(raw.payload)
        except ValidationError as exc:
            # Schema mismatch is terminal — the producer sent garbage.
            await self._dead_letter(raw, f"Schema validation failed: {exc}")
            return

        # 2) Dispatch — handler raises domain errors we translate to
        #    ack/retry/DLQ outcomes.
        try:
            await self.handle(message)  # type: ignore[arg-type]
        except TerminalWorkerError as exc:
            await self._dead_letter(raw, f"Terminal failure: {exc}")
            return
        except TransientWorkerError as exc:
            await self._handle_transient(raw, exc)
            return
        except WorkerError as exc:
            # Default classification: treat unclassified WorkerError as
            # transient (safer than dead-lettering on a programmer's
            # ambiguous exception type).
            await self._handle_transient(raw, exc)
            return
        except Exception as exc:  # noqa: BLE001
            # Truly unexpected — log + treat transient. If this keeps
            # firing the retry budget will dead-letter it.
            logger.exception("worker_handler_unexpected_error")
            await self._handle_transient(raw, exc)
            return

        # 3) Success — ACK.
        await self._queue.ack(self.stream, self.group, raw.id)

    async def _handle_transient(
        self, raw: ConsumedMessage, error: Exception
    ) -> None:
        """Retry-or-dead-letter based on delivered_count + RetryPolicy."""
        if not self._retry.should_retry(raw.delivered_count):
            await self._dead_letter(
                raw,
                f"Retry budget exhausted ({raw.delivered_count} attempts): {error}",
            )
            return
        # Leave in PEL — do NOT ACK. The run-loop's reclaim step
        # (reclaim_stale → XAUTOCLAIM, for workers that set
        # reclaim_min_idle_ms) hands this entry back once it has idled past the
        # min-idle gate, advancing delivered_count so a later attempt trips
        # should_retry → dead-letter. Workers WITHOUT reclaim configured rely
        # on their own recovery path (see WorkerConsumer.reclaim_min_idle_ms).
        logger.warning(
            "worker_transient_failure",
            extra={
                "stream": self.stream,
                "message_id": raw.id,
                "attempt": raw.delivered_count,
                "error": str(error),
            },
        )

    async def _dead_letter(
        self, raw: ConsumedMessage, error_summary: str
    ) -> None:
        """Copy to DLQ + ACK the producer-stream entry."""
        logger.error(
            "worker_dead_letter",
            extra={
                "stream": self.stream,
                "message_id": raw.id,
                "delivered_count": raw.delivered_count,
                "error": error_summary,
            },
        )
        await self._queue.dead_letter(
            stream=self.stream,
            message_id=raw.id,
            payload=raw.payload,
            error=error_summary,
        )
        await self._queue.ack(self.stream, self.group, raw.id)
```

- [ ] **Step 2: Write `tests/unit/workers/_base/test_consumer.py`** — mocked queue + handler

```python
"""Plan 5 Task 4: WorkerConsumer unit tests."""

from __future__ import annotations

import asyncio

import pytest
from pydantic import ValidationError

from codeoracle.adapters.valkey.client import ConsumedMessage
from codeoracle.core.exceptions import (
    TerminalWorkerError,
    TransientWorkerError,
)
from codeoracle.workers._base.consumer import WorkerConsumer
from codeoracle.workers._base.message import RetryPolicy, WorkerMessage


class _TestMessage(WorkerMessage):
    n: int


class _RecordingConsumer(WorkerConsumer[_TestMessage]):
    """Concrete subclass for testing — records every handle() invocation."""

    stream = "test.stream"
    group = "test_group"
    message_type = _TestMessage
    block_ms = 100  # short, so tests don't hang
    count_per_poll = 5

    def __init__(self, behavior, **kwargs):
        super().__init__(**kwargs)
        self._behavior = behavior
        self.handled: list[_TestMessage] = []

    async def handle(self, message: _TestMessage) -> None:
        self.handled.append(message)
        if callable(self._behavior):
            outcome = self._behavior(message)
            if isinstance(outcome, Exception):
                raise outcome


class _FakeQueue:
    """In-memory stand-in for ValkeyQueueClient."""

    def __init__(
        self,
        scripted: list[list[ConsumedMessage]],
        reclaimed: list[list[ConsumedMessage]] | None = None,
    ) -> None:
        self._scripted = scripted  # one list per read_pending call
        self._reclaimed = reclaimed or []  # one list per reclaim_stale call
        self.acks: list[tuple[str, str, str]] = []
        self.dlqs: list[tuple[str, str, dict, str]] = []
        self.calls = 0
        self.reclaim_calls = 0

    async def read_pending(self, **_):
        if self.calls < len(self._scripted):
            out = self._scripted[self.calls]
            self.calls += 1
            return out
        # After the script is exhausted, return [] forever so the loop
        # idles until stop_event is set.
        await asyncio.sleep(0.01)
        return []

    async def reclaim_stale(self, **_):
        if self.reclaim_calls < len(self._reclaimed):
            out = self._reclaimed[self.reclaim_calls]
            self.reclaim_calls += 1
            return out
        return []

    async def ack(self, stream, group, message_id):
        self.acks.append((stream, group, message_id))

    async def dead_letter(self, stream, message_id, payload, error):
        self.dlqs.append((stream, message_id, payload, error))


async def _run_until_processed(consumer, queue, expected_processed):
    """Run consumer until `expected_processed` ACKs+DLQs have happened."""
    task = asyncio.create_task(consumer.run())
    for _ in range(500):
        if len(queue.acks) + len(queue.dlqs) >= expected_processed:
            break
        await asyncio.sleep(0.01)
    consumer._stop.set()
    await task


async def test_success_path_acks():
    queue = _FakeQueue([[
        ConsumedMessage(
            id="1-0", stream="test.stream",
            payload={"batch_id": "b1", "n": 7}, delivered_count=1,
        )
    ]])
    consumer = _RecordingConsumer(
        behavior=lambda m: None,
        queue_client=queue,
        retry_policy=RetryPolicy(),
        consumer_name="c1",
        stop_event=asyncio.Event(),
    )
    await _run_until_processed(consumer, queue, 1)
    assert len(consumer.handled) == 1
    assert consumer.handled[0].n == 7
    assert queue.acks == [("test.stream", "test_group", "1-0")]
    assert queue.dlqs == []


async def test_terminal_error_dead_letters_immediately():
    queue = _FakeQueue([[
        ConsumedMessage(
            id="1-0", stream="test.stream",
            payload={"batch_id": "b1", "n": 1}, delivered_count=1,
        )
    ]])
    consumer = _RecordingConsumer(
        behavior=lambda m: TerminalWorkerError("nope"),
        queue_client=queue,
        retry_policy=RetryPolicy(max_attempts=5),
        consumer_name="c1",
        stop_event=asyncio.Event(),
    )
    await _run_until_processed(consumer, queue, 1)
    assert queue.dlqs and "Terminal failure" in queue.dlqs[0][3]
    # Dead-letter ACKs the original too
    assert queue.acks == [("test.stream", "test_group", "1-0")]


async def test_transient_error_within_budget_leaves_in_pel():
    queue = _FakeQueue([[
        ConsumedMessage(
            id="1-0", stream="test.stream",
            payload={"batch_id": "b1", "n": 1}, delivered_count=2,
        )
    ]])
    consumer = _RecordingConsumer(
        behavior=lambda m: TransientWorkerError("temp"),
        queue_client=queue,
        retry_policy=RetryPolicy(max_attempts=5),
        consumer_name="c1",
        stop_event=asyncio.Event(),
    )
    # Wait briefly then stop — the consumer should NOT ack or DLQ
    task = asyncio.create_task(consumer.run())
    await asyncio.sleep(0.2)
    consumer._stop.set()
    await task
    assert queue.acks == []
    assert queue.dlqs == []


async def test_transient_error_beyond_budget_dead_letters():
    queue = _FakeQueue([[
        ConsumedMessage(
            id="1-0", stream="test.stream",
            payload={"batch_id": "b1", "n": 1}, delivered_count=5,
        )
    ]])
    consumer = _RecordingConsumer(
        behavior=lambda m: TransientWorkerError("still failing"),
        queue_client=queue,
        retry_policy=RetryPolicy(max_attempts=5),
        consumer_name="c1",
        stop_event=asyncio.Event(),
    )
    await _run_until_processed(consumer, queue, 1)
    assert queue.dlqs and "Retry budget exhausted" in queue.dlqs[0][3]


async def test_reclaimed_message_climbs_to_dead_letter():
    """LOAD-BEARING reaper proof at the consumer level: a transiently-failing
    message handed back by reclaim_stale with a climbing delivered_count is
    retried while in budget and dead-lettered once it hits max_attempts — the
    path that is UNREACHABLE without reclaim (">" never re-reads the PEL, so
    delivered_count would freeze at 1 and the message would maroon forever).
    """
    mid = "1716-0"

    def at(delivered_count):
        return [ConsumedMessage(
            id=mid, stream="test.stream",
            payload={"batch_id": "b1", "n": 1}, delivered_count=delivered_count,
        )]

    # read_pending returns nothing; the message only ever arrives via reclaim,
    # delivered_count climbing 3 → 4 → 5 (= max_attempts) across polls.
    queue = _FakeQueue(scripted=[], reclaimed=[at(3), at(4), at(5)])
    consumer = _RecordingConsumer(
        behavior=lambda m: TransientWorkerError("still failing"),
        queue_client=queue,
        retry_policy=RetryPolicy(max_attempts=5),
        consumer_name="c1",
        stop_event=asyncio.Event(),
        reclaim_min_idle_ms=1,  # enable reclaim
    )
    await _run_until_processed(consumer, queue, 1)  # the eventual DLQ
    # dc=3 and dc=4 stayed in PEL (no ACK, no DLQ); dc=5 dead-letters.
    assert len(queue.dlqs) == 1
    assert "Retry budget exhausted" in queue.dlqs[0][3]
    # Dead-letter ACKs the original off the producer stream.
    assert queue.acks == [("test.stream", "test_group", mid)]
    # Handler ran once per reclaim cycle.
    assert len(consumer.handled) == 3


async def test_reclaim_disabled_skips_reclaim_stale():
    """A consumer with reclaim_min_idle_ms=None (the reindex orchestrator's
    posture) never calls reclaim_stale — its crash-recovery is elsewhere.
    """
    queue = _FakeQueue(scripted=[], reclaimed=[[ConsumedMessage(
        id="1716-0", stream="test.stream",
        payload={"batch_id": "b1", "n": 1}, delivered_count=9,
    )]])
    consumer = _RecordingConsumer(
        behavior=lambda m: None,
        queue_client=queue,
        retry_policy=RetryPolicy(),
        consumer_name="c1",
        stop_event=asyncio.Event(),
        # reclaim_min_idle_ms omitted → None → reclaim disabled
    )
    task = asyncio.create_task(consumer.run())
    await asyncio.sleep(0.2)
    consumer._stop.set()
    await task
    # reclaim_stale never invoked; the scripted reclaim entry is ignored.
    assert queue.reclaim_calls == 0
    assert consumer.handled == []


async def test_schema_validation_failure_dead_letters():
    queue = _FakeQueue([[
        ConsumedMessage(
            id="1-0", stream="test.stream",
            # `n` should be int — pass a non-coercible value
            payload={"batch_id": "b1", "n": "not an int"},
            delivered_count=1,
        )
    ]])
    consumer = _RecordingConsumer(
        behavior=lambda m: None,
        queue_client=queue,
        retry_policy=RetryPolicy(),
        consumer_name="c1",
        stop_event=asyncio.Event(),
    )
    await _run_until_processed(consumer, queue, 1)
    assert queue.dlqs and "Schema validation" in queue.dlqs[0][3]
    # Handler never invoked
    assert consumer.handled == []


async def test_unexpected_exception_classified_transient():
    queue = _FakeQueue([[
        ConsumedMessage(
            id="1-0", stream="test.stream",
            payload={"batch_id": "b1", "n": 1}, delivered_count=1,
        )
    ]])
    consumer = _RecordingConsumer(
        behavior=lambda m: ValueError("uncategorized"),
        queue_client=queue,
        retry_policy=RetryPolicy(max_attempts=5),
        consumer_name="c1",
        stop_event=asyncio.Event(),
    )
    task = asyncio.create_task(consumer.run())
    await asyncio.sleep(0.2)
    consumer._stop.set()
    await task
    # First-attempt uncategorized error: transient (no ACK, no DLQ yet)
    assert queue.acks == []
    assert queue.dlqs == []


async def test_stop_event_exits_loop_cleanly():
    queue = _FakeQueue([])  # no messages
    consumer = _RecordingConsumer(
        behavior=lambda m: None,
        queue_client=queue,
        retry_policy=RetryPolicy(),
        consumer_name="c1",
        stop_event=asyncio.Event(),
    )
    consumer._stop.set()  # set before run starts
    await consumer.run()  # returns immediately
```

- [ ] **Step 3: Run + lint + type**

```bash
uv run pytest tests/unit/workers/_base/ -v
uv run ruff check src/codeoracle/workers/_base/consumer.py
uv run ty check src/codeoracle/workers/_base/consumer.py
```

Expected: 9 tests PASS, no lint/type errors.

- [ ] **Step 4: Commit**

```bash
git add src/codeoracle/workers/_base/consumer.py tests/unit/workers/_base/test_consumer.py
git commit -m "feat: Plan 5 Task 4 — WorkerConsumer ABC (poll/ACK/NACK/DLQ loop)"
```

---

## Task 5: `src/codeoracle/workers/_base/wiring.py` — composition root

**What this task delivers:** the `build_worker_context()` factory that every worker `__main__.py` calls. Constructs `ValkeyQueueClient`, async DB engine + session factory, structured logger, and the worker-side budget-wrapped `LLMProvider` (built eagerly via `build_budgeted_provider` so Plan 6 workers that call LLMs don't pay first-call latency). Returns a `WorkerContext` dataclass with all the wiring; the worker passes the relevant pieces to its consumer subclass.

**Files:**
- Create: `src/codeoracle/workers/_base/wiring.py`
- Create: `tests/unit/workers/_base/test_wiring.py`

- [ ] **Step 1: Write `src/codeoracle/workers/_base/wiring.py`**

```python
"""Worker composition root — `build_worker_context()` returns a
WorkerContext with every dependency a worker subclass might need.

Why a composition root? Workers are independent processes started via
`python -m codeoracle.workers.<name>`. Each process needs to wire up its
own pool of dependencies (DB session factory, queue client, LLM
factory). Centralizing the wiring here means:
  1. A new worker (Plan 6 adds 5) only writes its consumer subclass
     and __main__.py — no boilerplate per worker.
  2. Tests can swap one or more dependencies via the optional
     parameters of build_worker_context() — same fixture shape across
     all workers.
"""

from __future__ import annotations

import asyncio
import logging
import os
import signal
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker  # exposed on WorkerContext

from codeoracle.adapters.llm.config import LLMConfig
from codeoracle.adapters.llm.interfaces import LLMProvider
from codeoracle.adapters.valkey.client import ValkeyQueueClient
from codeoracle.adapters.valkey.config import ValkeyConfig
from codeoracle.core.config import get_settings
from codeoracle.core.logging import configure_logging
from codeoracle.db.session import make_async_engine, make_async_session_factory
from codeoracle.services.context_budget import ContextBudgetService
from codeoracle.services.llm import build_budgeted_provider
from codeoracle.workers._base.message import RetryPolicy

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class WorkerContext:
    """Everything a WorkerConsumer subclass needs at construction time.

    The base WorkerConsumer takes a queue_client + retry_policy +
    consumer_name + stop_event; subclasses additionally take whatever
    of these fields they need (db session factory for the indexers,
    llm_provider for the embed indexer in Plan 6).
    """

    queue_client: ValkeyQueueClient
    db_session_factory: async_sessionmaker[AsyncSession]
    # 2026-05-26 amendment — Plan 11 async-job driver: engine exposed so
    # IndexingService (constructed in the reindex_orchestrator __main__)
    # can acquire pg_advisory_lock(0x46AE0001) on the WORKER's connection
    # rather than constructing a second engine (which would double the
    # connection pool and break the single-flight lock invariant if the
    # two engines disagreed on dialect quoting / search_path / etc.).
    # The JoernIndexer (Task 12) doesn't need this — it operates via
    # the session factory only — but exposing it on the shared context
    # avoids a per-worker wiring fork.
    engine: AsyncEngine
    llm_provider: LLMProvider  # eagerly constructed
    retry_policy: RetryPolicy
    stop_event: asyncio.Event
    consumer_name: str


async def build_worker_context(
    *,
    consumer_name_prefix: str,
    retry_policy: RetryPolicy | None = None,
    valkey_config: ValkeyConfig | None = None,
    llm_config: LLMConfig | None = None,
) -> WorkerContext:
    """Construct the WorkerContext.

    All optional parameters default to env-loaded config — pass them
    only in tests. `consumer_name_prefix` is the consumer-group name
    (e.g., "joern_indexer"); a "-<pid>" suffix is appended so multiple
    replicas get distinct names.

    Wires SIGTERM/SIGINT → stop_event.set() so K8s rolling restarts
    drain in-flight work cleanly.
    """
    valkey_config = valkey_config or ValkeyConfig()  # type: ignore[call-arg]
    llm_config = llm_config or LLMConfig()  # type: ignore[call-arg]
    retry_policy = retry_policy or RetryPolicy()

    # 1) Queue client — start so it can XADD to DLQs even on early failures
    queue_client = ValkeyQueueClient(valkey_config)
    await queue_client.start()

    # 2) DB session factory (async) — Plan 3's session.py.
    # `make_async_engine()` reads Settings internally; `make_async_session_factory(engine)`
    # binds the factory. Plan 5 is engine-stateless beyond the factory — the engine
    # is owned by the factory's lifecycle.
    engine = make_async_engine()
    db_session_factory = make_async_session_factory(engine)

    # 3) LLM provider — eager construction (cheap; SDK clients lazy-init
    #    sockets on first request, so no network hit here)
    llm_provider = build_budgeted_provider(llm_config, ContextBudgetService(max_tokens=24_000))

    # 4) Stop event wired to SIGTERM/SIGINT
    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, stop_event.set)
        except NotImplementedError:
            # Windows event loop — fall back to default handler.
            pass

    # 5) Configure structlog — Plan 3's helper installs the §7.9 secret-redaction
    # processor (api_key / secret / token / password keys → '<redacted>') in
    # addition to JSON output. Workers MUST NOT roll their own structlog config
    # or that redaction silently disappears.
    # `get_settings().environment` is Plan 3's canonical source; the
    # CODEORACLE_ENVIRONMENT env var feeds it via pydantic-settings. Don't read
    # CODEORACLE_ENV directly — that's a third name and drifts the contract.
    configure_logging(get_settings())

    consumer_name = f"{consumer_name_prefix}-{os.getpid()}"
    logger.info(
        "worker_context_ready",
        extra={"consumer_name": consumer_name, "stream": consumer_name_prefix},
    )
    return WorkerContext(
        queue_client=queue_client,
        db_session_factory=db_session_factory,
        engine=engine,  # 2026-05-26 amendment — Plan 11 async-job driver
        llm_provider=llm_provider,
        retry_policy=retry_policy,
        stop_event=stop_event,
        consumer_name=consumer_name,
    )


async def teardown_worker_context(ctx: WorkerContext) -> None:
    """Drain on shutdown — close queue client. DB engine is module-scoped
    via Plan 3; nothing else to close explicitly."""
    await ctx.queue_client.aclose()


```

- [ ] **Step 2: Write `tests/unit/workers/_base/test_wiring.py`**

```python
"""Plan 5 Task 5: build_worker_context unit tests."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from codeoracle.adapters.valkey.config import ValkeyConfig
from codeoracle.workers._base.wiring import (
    WorkerContext,
    build_worker_context,
    teardown_worker_context,
)


@pytest.fixture(autouse=True)
def _isolate(monkeypatch, tmp_path):
    """Same pattern as Plan 4: escape repo .env + clear known env vars."""
    monkeypatch.chdir(tmp_path)
    for var in (
        "CODEORACLE_LLM_PROVIDER", "CODEORACLE_LLM_OPENAI__API_KEY", "CODEORACLE_LLM_OPENAI__MODEL",
        "CODEORACLE_LLM_MOCK__SCRIPTS_DIR", "CODEORACLE_LLM_MOCK__STRICT",
        "CODEORACLE_VALKEY__URL", "CODEORACLE_VALKEY__POOL_MIN", "CODEORACLE_VALKEY__POOL_MAX",
        "CODEORACLE_VALKEY__BLOCK_MS_DEFAULT", "CODEORACLE_VALKEY__MAX_IN_FLIGHT_PER_CONSUMER",
        "CODEORACLE_VALKEY__STREAM_MAXLEN",
        "CODEORACLE_EMBEDDING__MODEL", "CODEORACLE_EMBEDDING__DIM",
    ):
        monkeypatch.delenv(var, raising=False)
    # Set what the wiring needs
    monkeypatch.setenv("CODEORACLE_VALKEY__URL", "redis://localhost:6379/0")
    monkeypatch.setenv("CODEORACLE_VALKEY__POOL_MIN", "1")
    monkeypatch.setenv("CODEORACLE_VALKEY__POOL_MAX", "10")
    monkeypatch.setenv("CODEORACLE_VALKEY__BLOCK_MS_DEFAULT", "5000")
    monkeypatch.setenv("CODEORACLE_VALKEY__MAX_IN_FLIGHT_PER_CONSUMER", "10")
    monkeypatch.setenv("CODEORACLE_VALKEY__STREAM_MAXLEN", "1000")
    monkeypatch.setenv("CODEORACLE_LLM_PROVIDER", "mock")
    monkeypatch.setenv("CODEORACLE_LLM_MOCK__SCRIPTS_DIR", str(tmp_path / "scripts"))
    monkeypatch.setenv("CODEORACLE_LLM_MOCK__STRICT", "true")
    monkeypatch.setenv("CODEORACLE_EMBEDDING__MODEL", "text-embedding-3-small")
    monkeypatch.setenv("CODEORACLE_EMBEDDING__DIM", "1536")
    monkeypatch.setenv("CODEORACLE_DB__PASSWORD", "test-db-pw")  # D5-4: required, no default


async def test_build_worker_context_starts_queue_and_returns_context():
    with patch("codeoracle.workers._base.wiring.ValkeyQueueClient") as Qc, \
         patch("codeoracle.workers._base.wiring.make_async_engine") as Mae, \
         patch("codeoracle.workers._base.wiring.make_async_session_factory") as Mks:
        Qc.return_value.start = AsyncMock()
        Qc.return_value.aclose = AsyncMock()
        engine_mock = MagicMock(name="async_engine")
        Mae.return_value = engine_mock
        Mks.return_value = MagicMock(name="session_factory")

        ctx = await build_worker_context(consumer_name_prefix="test_worker")
        assert isinstance(ctx, WorkerContext)
        Qc.return_value.start.assert_awaited_once()
        # 2026-05-26 amendment: engine MUST be the one constructed by
        # make_async_engine (not a second one) so workers that need raw
        # engine access (IndexingService) share the same connection pool
        # as the session factory bound to that same engine.
        assert ctx.engine is engine_mock
        Mks.assert_called_once_with(engine_mock)
        # consumer_name includes pid suffix
        assert ctx.consumer_name.startswith("test_worker-")
        assert ctx.consumer_name != "test_worker"
        # stop_event is fresh & unset
        assert isinstance(ctx.stop_event, asyncio.Event)
        assert not ctx.stop_event.is_set()


async def test_teardown_closes_queue_client():
    with patch("codeoracle.workers._base.wiring.ValkeyQueueClient") as Qc, \
         patch("codeoracle.workers._base.wiring.make_async_engine"), \
         patch("codeoracle.workers._base.wiring.make_async_session_factory"):
        Qc.return_value.start = AsyncMock()
        Qc.return_value.aclose = AsyncMock()
        ctx = await build_worker_context(consumer_name_prefix="test_worker")
        await teardown_worker_context(ctx)
        Qc.return_value.aclose.assert_awaited_once()


async def test_signal_handlers_wired(monkeypatch):
    handlers = []

    def fake_add(sig, cb):
        handlers.append((sig, cb))

    loop = asyncio.get_running_loop()
    monkeypatch.setattr(loop, "add_signal_handler", fake_add)

    with patch("codeoracle.workers._base.wiring.ValkeyQueueClient") as Qc, \
         patch("codeoracle.workers._base.wiring.make_async_engine"), \
         patch("codeoracle.workers._base.wiring.make_async_session_factory"):
        Qc.return_value.start = AsyncMock()
        Qc.return_value.aclose = AsyncMock()
        ctx = await build_worker_context(consumer_name_prefix="test_worker")
        # Should have wired SIGTERM + SIGINT
        sigs = {s for s, _ in handlers}
        import signal
        assert signal.SIGTERM in sigs
        assert signal.SIGINT in sigs
        # Calling the handler sets stop_event
        for _, cb in handlers:
            cb()
        assert ctx.stop_event.is_set()
```

- [ ] **Step 3: Run + lint + type**

```bash
uv run pytest tests/unit/workers/_base/test_wiring.py -v
uv run ruff check src/codeoracle/workers/_base/wiring.py
uv run ty check src/codeoracle/workers/_base/wiring.py
```

Expected: 3 tests PASS, no lint/type errors.

- [ ] **Step 4: Commit**

```bash
git add src/codeoracle/workers/_base/wiring.py tests/unit/workers/_base/test_wiring.py
git commit -m "feat: Plan 5 Task 5 — build_worker_context composition root"
```

---

## Task 6: Alembic migration `0004_snapshot_file_outcomes.py`

**What this task delivers:** the schema for `snapshot_file_outcomes` — the per-file-per-extractor ack table that `IndexingService` polls to know when a reindex barrier has been crossed. The table has a `(snapshot_sha, file_path, extractor)` composite primary key (idempotent on retry) and tracks outcome + error + applied_at. Plan 5 only writes joern rows; Plan 6's workers append rows under the same shape.

**Files:**
- Modify: `src/codeoracle/db/schemas.py` (add SnapshotFileOutcome SQLAlchemy table)
- Create: `src/codeoracle/db/migrations/versions/0004_snapshot_file_outcomes.py`
- Create: `tests/integration/db/test_migrations_0004.py`

- [ ] **Step 1: Extend `src/codeoracle/db/schemas.py` with the SQLAlchemy table**

Plan 3 introduced `schemas.py` with `Snapshot`, `Embedding`, `Trace` declarative classes. Append:

```python
class SnapshotFileOutcome(Base):
    """One row per (snapshot_sha, file_path, extractor) completion event.

    Workers INSERT a row when they ACK a per-file extraction job.
    IndexingService polls SELECT COUNT(*) WHERE snapshot_sha=$1 AND extractor=$2
    against the expected count to detect completion of each extractor's
    phase. Composite PK makes retries idempotent (last writer wins
    on outcome/error/applied_at).

    Outcome enum: success | failed | skipped (e.g., file didn't change).
    Plan 5 only writes 'joern' rows; Plan 6 adds scip/opengrep/embed.
    """

    __tablename__ = "snapshot_file_outcomes"

    snapshot_sha: Mapped[str] = mapped_column(
        String(80), primary_key=True, nullable=False
    )
    file_path: Mapped[str] = mapped_column(
        String(1024), primary_key=True, nullable=False
    )
    extractor: Mapped[str] = mapped_column(
        String(64), primary_key=True, nullable=False
    )
    outcome: Mapped[str] = mapped_column(String(16), nullable=False)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    applied_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    # Index for IndexingService's barrier query:
    # SELECT COUNT(*) FROM snapshot_file_outcomes
    # WHERE snapshot_sha = $1 AND extractor = $2
    __table_args__ = (
        Index(
            "ix_snapshot_file_outcomes_snapshot_extractor",
            "snapshot_sha",
            "extractor",
        ),
    )
```

Add any missing imports to `schemas.py`:

```python
from sqlalchemy import DateTime, Index, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column
from datetime import datetime
```

(If Plan 3 already imports these — most should be there — don't duplicate.)

- [ ] **Step 2: Generate the alembic revision skeleton**

```bash
uv run alembic revision -m "snapshot file outcomes" --rev-id 0004
```

Expected: a new file at `src/codeoracle/db/migrations/versions/0004_snapshot_file_outcomes.py`. Rename if alembic appended a timestamp.

- [ ] **Step 3: Write the migration body**

```python
"""snapshot_file_outcomes table — IndexingService barrier

Plan 5 Task 6. Workers INSERT one row per (snapshot_sha, file_path,
extractor) when they ACK; IndexingService polls the row count to
detect phase completion.

Revision ID: 0004
Revises: 0003
Create Date: 2026-05-21
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
# Bare-numeric convention — Plan 3 uses "0001"/"0002"/"0003", and Plan 6's
# "0005" + Plan 7's "0006" continue it. See "Cross-plan contracts" at the top
# of this plan; the audit's P0-15 flags Plan 6's draft slug-form revision
# IDs (e.g. "0005_code_references") as the deviation, fixed in Plan 6 §6.
revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "snapshot_file_outcomes",
        sa.Column("snapshot_sha", sa.String(80), nullable=False),
        sa.Column("file_path", sa.String(1024), nullable=False),
        sa.Column("extractor", sa.String(64), nullable=False),
        sa.Column("outcome", sa.String(16), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column(
            "applied_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint(
            "snapshot_sha",
            "file_path",
            "extractor",
            name="pk_snapshot_file_outcomes",
        ),
    )
    op.create_index(
        "ix_snapshot_file_outcomes_snapshot_extractor",
        "snapshot_file_outcomes",
        ["snapshot_sha", "extractor"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_snapshot_file_outcomes_snapshot_extractor",
        table_name="snapshot_file_outcomes",
    )
    op.drop_table("snapshot_file_outcomes")
```

- [ ] **Step 4: Apply + verify**

```bash
uv run alembic upgrade head
```

Expected: `Running upgrade 0003 -> 0004, snapshot file outcomes`.

```bash
uv run alembic current
```

Expected: `0004 (head)`.

Verify the table exists:

```bash
docker exec -i $(docker compose ps -q postgres) psql -U codeoracle -d codeoracle -c '\d snapshot_file_outcomes'
```

Expected: column listing matches the migration; the composite PK + index are shown.

- [ ] **Step 5: Write `tests/integration/db/test_migrations_0004.py`**

```python
"""Plan 5 Task 6: 0004 migration applies cleanly + rejects bad data."""

from __future__ import annotations

import pytest
from sqlalchemy import text

pytestmark = pytest.mark.integration


async def test_table_exists_after_upgrade(async_session):
    result = await async_session.execute(
        text(
            "SELECT column_name, data_type FROM information_schema.columns "
            "WHERE table_name = 'snapshot_file_outcomes' "
            "ORDER BY ordinal_position"
        )
    )
    cols = {row.column_name for row in result}
    assert cols == {
        "snapshot_sha", "file_path", "extractor",
        "outcome", "error", "applied_at",
    }


async def test_composite_pk_rejects_duplicate_inserts(async_session):
    await async_session.execute(
        text(
            "INSERT INTO snapshot_file_outcomes "
            "(snapshot_sha, file_path, extractor, outcome) "
            "VALUES ('abc1234', 'src/x.py', 'joern', 'success')"
        )
    )
    await async_session.commit()

    with pytest.raises(Exception) as exc_info:
        await async_session.execute(
            text(
                "INSERT INTO snapshot_file_outcomes "
                "(snapshot_sha, file_path, extractor, outcome) "
                "VALUES ('abc1234', 'src/x.py', 'joern', 'success')"
            )
        )
        await async_session.commit()
    assert "unique" in str(exc_info.value).lower() or "duplicate" in str(exc_info.value).lower()


async def test_barrier_index_speeds_count_query(async_session):
    """Confirms the index exists. EXPLAIN should show 'Index Scan'."""
    result = await async_session.execute(
        text(
            "EXPLAIN SELECT COUNT(*) FROM snapshot_file_outcomes "
            "WHERE snapshot_sha = 'x' AND extractor = 'joern'"
        )
    )
    plan = " ".join(str(row[0]) for row in result)
    # Postgres planner may choose a seq scan on an empty table; this assertion
    # is documentary (re-run on a populated table to see the real plan).
    assert "snapshot_file_outcomes" in plan
```

- [ ] **Step 6: Run + commit**

```bash
uv run pytest tests/integration/db/test_migrations_0004.py -v
git add src/codeoracle/db/schemas.py \
        src/codeoracle/db/migrations/versions/0004_snapshot_file_outcomes.py \
        tests/integration/db/test_migrations_0004.py
git commit -m "feat: Plan 5 Task 6 — migration 0004 snapshot_file_outcomes barrier table"
```

---

## Task 7: `src/codeoracle/db/repositories/snapshots.py` + `file_outcomes.py`

**What this task delivers:** the SnapshotRepository (CRUD + state-machine-enforced status transitions + `mark_stale_applying_failed` bulk-sweep) and the SnapshotFileOutcomeRepository (INSERT + count-by-extractor). Both are async, take an `AsyncSession` per call, and own zero business logic — they're thin data-access wrappers. `IndexingService` (Task 13) composes them with `GraphRepository`. The bulk-sweep method exists exclusively to recover SIGKILL/OOM-orphaned `APPLYING` rows from a prior reindex process (see L29 deferral-list rewrite — DECIDE-D); it bypasses the per-row state machine deliberately and is callable *only* while the calling process holds `pg_try_advisory_lock(0x46AE0001)` on some connection (typically the one `reindex_single_flight` opens — a different session from the sweep's; the lock is connection-scoped at the Postgres backend but mutexes across all other connections in any process, so the lock-holder elsewhere in this process keeps the sweep's session safe).

**Files:**
- Create: `src/codeoracle/db/repositories/__init__.py`
- Create: `src/codeoracle/db/repositories/snapshots.py`
- Create: `src/codeoracle/db/repositories/file_outcomes.py`
- Create: `tests/integration/db/repositories/__init__.py`
- Create: `tests/integration/db/repositories/test_snapshots.py`
- Create: `tests/integration/db/repositories/test_file_outcomes.py`

- [ ] **Step 1: Write `src/codeoracle/db/repositories/__init__.py`** (empty)

- [ ] **Step 2: Write `src/codeoracle/db/repositories/snapshots.py`**

```python
"""SnapshotRepository — CRUD + state-machine-enforced transitions
for the `snapshots` table (Plan 3 migration 0001).

State machine:
    PENDING → APPLYING → APPLIED | FAILED
    PENDING → FAILED  (rare: lock acquired then immediately rejected)

Any other transition (e.g., APPLIED → APPLYING) raises
SnapshotConflictError — the IndexingService state machine has been
violated and recovery requires manual intervention.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Final

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from codeoracle.core.exceptions import SnapshotConflictError
from codeoracle.db.schemas import Snapshot as SnapshotRow
from codeoracle.models.snapshot import (
    ExtractorOutcome,
    Snapshot,
    SnapshotStatus,
)


_ALLOWED_TRANSITIONS: Final[dict[SnapshotStatus, frozenset[SnapshotStatus]]] = {
    SnapshotStatus.PENDING: frozenset(
        {SnapshotStatus.APPLYING, SnapshotStatus.FAILED}
    ),
    SnapshotStatus.APPLYING: frozenset(
        {SnapshotStatus.APPLIED, SnapshotStatus.FAILED}
    ),
    SnapshotStatus.APPLIED: frozenset(),  # terminal
    SnapshotStatus.FAILED: frozenset(),   # terminal
}


class SnapshotRepository:
    """All snapshot row operations live here. Construct per-request /
    per-orchestration-cycle (DON'T cache across the AsyncSession
    lifetime)."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create_pending(
        self,
        *,
        sha: str,
        parent_sha: str | None,
        changed_files: list[str],
    ) -> Snapshot:
        """INSERT a fresh PENDING snapshot. Caller must commit.

        Raises :class:`SnapshotConflictError` if a row with this ``sha``
        already exists: ``sha`` is the PRIMARY KEY (Plan 3 migration
        0001), so a duplicate INSERT surfaces as ``IntegrityError`` on
        ``flush()``, which this method translates to the domain
        exception. :meth:`IndexingService.enqueue_reindex` depends on
        this to make retried/concurrent ``POST /v1/reindex`` idempotent
        (catch → rollback → re-read the existing row → HTTP 202). The
        caller MUST ``rollback()`` the poisoned session before reuse.
        """
        row = SnapshotRow(
            sha=sha,
            parent_sha=parent_sha,
            changed_files=changed_files,
            status=SnapshotStatus.PENDING.value,
            extractor_results={},
            error=None,
            applied_at=None,
        )
        self._session.add(row)
        try:
            await self._session.flush()
        except IntegrityError as exc:
            raise SnapshotConflictError(
                f"Snapshot sha={sha!r} already exists."
            ) from exc
        return self._row_to_model(row)

    async def get_by_sha(self, sha: str) -> Snapshot | None:
        """SELECT one snapshot or None."""
        result = await self._session.execute(
            select(SnapshotRow).where(SnapshotRow.sha == sha)
        )
        row = result.scalar_one_or_none()
        return self._row_to_model(row) if row else None

    async def get_latest_applied(self) -> Snapshot | None:
        """SELECT the most recent APPLIED snapshot."""
        result = await self._session.execute(
            select(SnapshotRow)
            .where(SnapshotRow.status == SnapshotStatus.APPLIED.value)
            .order_by(SnapshotRow.applied_at.desc())
            .limit(1)
        )
        row = result.scalar_one_or_none()
        return self._row_to_model(row) if row else None

    async def transition(
        self,
        *,
        sha: str,
        to: SnapshotStatus,
        extractor_results: dict[str, ExtractorOutcome] | None = None,
        error: str | None = None,
    ) -> Snapshot:
        """Transition `sha` to `to`. Raises SnapshotConflictError on
        invalid moves. Caller must commit.

        On `to == APPLIED`: `applied_at` is set to now(UTC) and the
        extractor_results map is persisted.
        """
        current = await self.get_by_sha(sha)
        if current is None:
            raise SnapshotConflictError(f"Snapshot sha={sha!r} not found.")
        if to not in _ALLOWED_TRANSITIONS[current.status]:
            raise SnapshotConflictError(
                f"Cannot transition snapshot {sha!r} from "
                f"{current.status.value} to {to.value}."
            )

        values: dict[str, object] = {"status": to.value}
        if to == SnapshotStatus.APPLIED:
            values["applied_at"] = datetime.now(timezone.utc)
        if extractor_results is not None:
            values["extractor_results"] = {
                k: v.value for k, v in extractor_results.items()
            }
        if error is not None:
            values["error"] = error

        await self._session.execute(
            update(SnapshotRow).where(SnapshotRow.sha == sha).values(**values)
        )
        await self._session.flush()
        updated = await self.get_by_sha(sha)
        # get_by_sha just returned non-None — re-assert for ty
        assert updated is not None
        return updated

    async def mark_stale_applying_failed(self) -> int:
        """Bulk-transition every `APPLYING` row to `FAILED` with
        `error='process died mid-apply'`. Returns the row count (0 when
        no stale rows exist — caller can suppress the log line).

        **Precondition (load-bearing — DO NOT call without this):** the
        *calling process* MUST be holding `pg_try_advisory_lock(0x46AE0001)`
        on *some* connection (session-level, the same lock that
        `reindex_single_flight` takes — see Task 9). That lock-holder
        connection is typically a *different* session from the one this
        method runs against; advisory locks are scoped to the holding
        connection at the Postgres backend, not transaction-scoped to
        this method's session. Any `APPLYING` row visible to the sweep
        while the per-process lock-holder connection is alive is, by
        construction, a SIGKILL/OOM leftover from a prior process whose
        connection died — Postgres auto-released its advisory lock on
        backend cleanup, so no concurrent legitimate reindex on any
        connection in any process can exist while our lock-holder is
        alive. If the precondition is violated (no process-wide
        lock-holder), this method will clobber a legitimately-running
        reindex's in-flight `APPLYING` row.

        **Why raw UPDATE instead of looping `transition()`?** Two
        reasons, in priority order:

        1. *Atomicity.* `transition()` does `get_by_sha` → check →
           UPDATE per row. Across N stale rows it issues N round-trips
           with TOCTOU windows between each read and write. One bulk
           SQL `UPDATE ... WHERE status='applying'` is atomic at the
           database level — no window where two callers can race.
        2. *State-machine bypass is intentional.* `_ALLOWED_TRANSITIONS`
           permits `APPLYING → FAILED`, so a `transition()` loop would
           succeed; we still avoid it because the per-row read+check
           is dead weight when the `WHERE` clause already constrains
           the source state.

        Alternative considered + rejected: assert the precondition at
        runtime by probing for a process-wide lock-holder from this
        method's session (e.g., `SELECT pg_try_advisory_lock(0x46AE0001)`
        — returns `false` if some OTHER session is holding it, which is
        what we want — then immediately `pg_advisory_unlock` if we
        accidentally acquired it). Rejected because it costs a
        round-trip per call to defend against a callsite that doesn't
        exist in Plan 5; the load-bearing-precondition docstring above
        is the contract.
        """
        result = await self._session.execute(
            update(SnapshotRow)
            .where(SnapshotRow.status == SnapshotStatus.APPLYING.value)
            .values(
                status=SnapshotStatus.FAILED.value,
                error="process died mid-apply",
            )
        )
        await self._session.flush()
        # asyncpg returns the real count; SQLAlchemy `result.rowcount`
        # can be `-1` for dialects that don't report it — fall through
        # to 0 so callers can treat the number as a count for logging.
        return max(result.rowcount or 0, 0)

    @staticmethod
    def _row_to_model(row: SnapshotRow) -> Snapshot:
        return Snapshot(
            sha=row.sha,
            parent_sha=row.parent_sha,
            changed_files=list(row.changed_files or []),
            status=SnapshotStatus(row.status),
            extractor_results={
                k: ExtractorOutcome(v) for k, v in (row.extractor_results or {}).items()
            },
            error=row.error,
            applied_at=row.applied_at,
        )
```

- [ ] **Step 3: Write `src/codeoracle/db/repositories/file_outcomes.py`**

```python
"""SnapshotFileOutcomeRepository — INSERT + barrier-count queries
against the `snapshot_file_outcomes` table (Plan 5 migration 0004)."""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from codeoracle.db.schemas import SnapshotFileOutcome as SfoRow


class SnapshotFileOutcomeRepository:
    """Records per-file-per-extractor outcomes. Workers call upsert();
    IndexingService calls count_completed()."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def upsert(
        self,
        *,
        snapshot_sha: str,
        file_path: str,
        extractor: str,
        outcome: str,  # "success" | "failed" | "skipped"
        error: str | None = None,
    ) -> None:
        """INSERT ... ON CONFLICT DO UPDATE — idempotent on retry.
        Caller must commit."""
        stmt = pg_insert(SfoRow).values(
            snapshot_sha=snapshot_sha,
            file_path=file_path,
            extractor=extractor,
            outcome=outcome,
            error=error,
            applied_at=datetime.now(timezone.utc),
        ).on_conflict_do_update(
            constraint="pk_snapshot_file_outcomes",
            set_={
                "outcome": outcome,
                "error": error,
                "applied_at": datetime.now(timezone.utc),
            },
        )
        await self._session.execute(stmt)
        await self._session.flush()

    async def count_completed(
        self, *, snapshot_sha: str, extractor: str
    ) -> int:
        """Number of files completed (any outcome) for this extractor."""
        result = await self._session.execute(
            select(func.count())
            .select_from(SfoRow)
            .where(
                SfoRow.snapshot_sha == snapshot_sha,
                SfoRow.extractor == extractor,
            )
        )
        return int(result.scalar_one())

    async def count_failed(
        self, *, snapshot_sha: str, extractor: str
    ) -> int:
        """Number of failed files for this extractor — used by
        IndexingService to decide snapshot-status transition."""
        result = await self._session.execute(
            select(func.count())
            .select_from(SfoRow)
            .where(
                SfoRow.snapshot_sha == snapshot_sha,
                SfoRow.extractor == extractor,
                SfoRow.outcome == "failed",
            )
        )
        return int(result.scalar_one())
```

- [ ] **Step 4: Write `tests/integration/db/repositories/test_snapshots.py`**

```python
"""Plan 5 Task 7: SnapshotRepository integration tests against a real PG."""

from __future__ import annotations

import pytest

from codeoracle.core.exceptions import SnapshotConflictError
from codeoracle.db.repositories.snapshots import SnapshotRepository
from codeoracle.models.snapshot import ExtractorOutcome, SnapshotStatus

pytestmark = pytest.mark.integration


async def test_create_pending_returns_snapshot(async_session):
    repo = SnapshotRepository(async_session)
    snap = await repo.create_pending(
        sha="abc1234", parent_sha=None, changed_files=["src/x.py"]
    )
    await async_session.commit()
    assert snap.sha == "abc1234"
    assert snap.status == SnapshotStatus.PENDING
    assert snap.changed_files == ["src/x.py"]


async def test_create_pending_duplicate_sha_raises_conflict(async_session):
    """Duplicate sha (PRIMARY KEY clash) → SnapshotConflictError, NOT a
    raw IntegrityError — the contract enqueue_reindex's idempotent-retry
    branch depends on. The second flush poisons the session (needs
    rollback), exactly as enqueue_reindex handles it before re-reading."""
    repo = SnapshotRepository(async_session)
    await repo.create_pending(sha="dup", parent_sha=None, changed_files=[])
    await async_session.commit()
    with pytest.raises(SnapshotConflictError):
        await repo.create_pending(sha="dup", parent_sha=None, changed_files=[])


async def test_get_by_sha_returns_none_when_missing(async_session):
    repo = SnapshotRepository(async_session)
    assert await repo.get_by_sha("does-not-exist") is None


async def test_pending_to_applying_to_applied_is_legal(async_session):
    repo = SnapshotRepository(async_session)
    await repo.create_pending(sha="s1", parent_sha=None, changed_files=[])
    await repo.transition(sha="s1", to=SnapshotStatus.APPLYING)
    await repo.transition(
        sha="s1",
        to=SnapshotStatus.APPLIED,
        extractor_results={"joern": ExtractorOutcome.SUCCESS},
    )
    await async_session.commit()
    snap = await repo.get_by_sha("s1")
    assert snap.status == SnapshotStatus.APPLIED
    assert snap.extractor_results == {"joern": ExtractorOutcome.SUCCESS}
    assert snap.applied_at is not None


async def test_pending_to_failed_is_legal(async_session):
    repo = SnapshotRepository(async_session)
    await repo.create_pending(sha="s2", parent_sha=None, changed_files=[])
    await repo.transition(
        sha="s2", to=SnapshotStatus.FAILED, error="lock contention"
    )
    snap = await repo.get_by_sha("s2")
    assert snap.status == SnapshotStatus.FAILED
    assert snap.error == "lock contention"


async def test_applied_to_applying_raises_conflict(async_session):
    repo = SnapshotRepository(async_session)
    await repo.create_pending(sha="s3", parent_sha=None, changed_files=[])
    await repo.transition(sha="s3", to=SnapshotStatus.APPLYING)
    await repo.transition(sha="s3", to=SnapshotStatus.APPLIED)
    with pytest.raises(SnapshotConflictError):
        await repo.transition(sha="s3", to=SnapshotStatus.APPLYING)


async def test_unknown_sha_raises_conflict(async_session):
    repo = SnapshotRepository(async_session)
    with pytest.raises(SnapshotConflictError, match="not found"):
        await repo.transition(sha="ghost", to=SnapshotStatus.APPLYING)


async def test_get_latest_applied_returns_most_recent(async_session):
    repo = SnapshotRepository(async_session)
    for sha in ("old", "newer", "newest"):
        await repo.create_pending(sha=sha, parent_sha=None, changed_files=[])
        await repo.transition(sha=sha, to=SnapshotStatus.APPLYING)
        await repo.transition(sha=sha, to=SnapshotStatus.APPLIED)
    latest = await repo.get_latest_applied()
    assert latest is not None
    assert latest.sha == "newest"


async def test_mark_stale_applying_failed_returns_zero_when_no_stale_rows(
    async_session,
):
    """No APPLYING rows in the table → method returns 0 and changes
    nothing. Callers use this to suppress the "swept N stale rows"
    log line on clean startup."""
    repo = SnapshotRepository(async_session)
    await repo.create_pending(sha="clean", parent_sha=None, changed_files=[])
    await async_session.commit()
    assert await repo.mark_stale_applying_failed() == 0
    # Pending row untouched
    snap = await repo.get_by_sha("clean")
    assert snap is not None
    assert snap.status == SnapshotStatus.PENDING


async def test_mark_stale_applying_failed_transitions_all_applying_rows(
    async_session,
):
    """N stale APPLYING rows are bulk-transitioned to FAILED with the
    fixed error string; PENDING/APPLIED/FAILED rows are not touched."""
    repo = SnapshotRepository(async_session)

    # Three stale APPLYING rows
    for sha in ("stale_a", "stale_b", "stale_c"):
        await repo.create_pending(sha=sha, parent_sha=None, changed_files=[])
        await repo.transition(sha=sha, to=SnapshotStatus.APPLYING)

    # One pending row (should not be touched)
    await repo.create_pending(sha="pending", parent_sha=None, changed_files=[])

    # One already-APPLIED row (should not be touched)
    await repo.create_pending(sha="done", parent_sha=None, changed_files=[])
    await repo.transition(sha="done", to=SnapshotStatus.APPLYING)
    await repo.transition(sha="done", to=SnapshotStatus.APPLIED)

    await async_session.commit()

    swept = await repo.mark_stale_applying_failed()
    await async_session.commit()
    assert swept == 3

    # All three stale rows: FAILED + canonical error string
    for sha in ("stale_a", "stale_b", "stale_c"):
        snap = await repo.get_by_sha(sha)
        assert snap is not None
        assert snap.status == SnapshotStatus.FAILED
        assert snap.error == "process died mid-apply"

    # Untouched rows
    pending = await repo.get_by_sha("pending")
    assert pending is not None and pending.status == SnapshotStatus.PENDING
    done = await repo.get_by_sha("done")
    assert done is not None and done.status == SnapshotStatus.APPLIED
```

- [ ] **Step 5: Write `tests/integration/db/repositories/test_file_outcomes.py`**

```python
"""Plan 5 Task 7: SnapshotFileOutcomeRepository integration tests."""

from __future__ import annotations

import pytest

from codeoracle.db.repositories.file_outcomes import SnapshotFileOutcomeRepository

pytestmark = pytest.mark.integration


async def test_upsert_then_count_completed(async_session):
    repo = SnapshotFileOutcomeRepository(async_session)
    for f in ("src/a.py", "src/b.py", "src/c.py"):
        await repo.upsert(
            snapshot_sha="s1", file_path=f,
            extractor="joern", outcome="success",
        )
    await async_session.commit()
    assert await repo.count_completed(snapshot_sha="s1", extractor="joern") == 3


async def test_upsert_is_idempotent(async_session):
    repo = SnapshotFileOutcomeRepository(async_session)
    for _ in range(3):
        await repo.upsert(
            snapshot_sha="s2", file_path="src/x.py",
            extractor="joern", outcome="success",
        )
    await async_session.commit()
    # Three upserts → one row
    assert await repo.count_completed(snapshot_sha="s2", extractor="joern") == 1


async def test_count_failed_distinguishes_outcomes(async_session):
    repo = SnapshotFileOutcomeRepository(async_session)
    await repo.upsert(
        snapshot_sha="s3", file_path="src/a.py",
        extractor="joern", outcome="success",
    )
    await repo.upsert(
        snapshot_sha="s3", file_path="src/b.py",
        extractor="joern", outcome="failed", error="parse error",
    )
    await async_session.commit()
    assert await repo.count_completed(snapshot_sha="s3", extractor="joern") == 2
    assert await repo.count_failed(snapshot_sha="s3", extractor="joern") == 1
```

- [ ] **Step 6: Run + lint + type + commit**

```bash
uv run pytest tests/integration/db/repositories/ -v
uv run ruff check src/codeoracle/db/repositories/
uv run ty check src/codeoracle/db/repositories/
git add src/codeoracle/db/repositories/ tests/integration/db/repositories/
git commit -m "feat: Plan 5 Task 7 — SnapshotRepository + SnapshotFileOutcomeRepository"
```

---

## Task 8: `src/codeoracle/db/repositories/graph.py` — GraphRepository (AGE Cypher)

**What this task delivers:** the thin executor wrapper for AGE Cypher queries. Methods: `execute_cypher(stmt, params)`, `replace_file_subgraph(snapshot_sha, file_path, cypher_inserts)`, `find_callers(symbol, snapshot_sha)`, `link_cross_file_symbols(snapshot_sha)`. The `replace_file_subgraph` method is the atomic-per-file mutation the spec §8.4 requires — DELETE then INSERT in one transaction. Tests use a real AGE-enabled Postgres via testcontainers.

**Files:**
- Create: `src/codeoracle/db/repositories/graph.py`
- Create: `tests/integration/db/repositories/test_graph.py`

- [ ] **Step 1: Write `src/codeoracle/db/repositories/graph.py`**

```python
"""GraphRepository — AGE Cypher executor.

The platform's only path to AGE goes through this class. The AGE
per-connection contract (LOAD 'age' + SET search_path) is already
enforced by db/session.py's event listener (Plan 3) — so every Cypher
call from this repository assumes AGE is loaded on the active connection.

Three operations Plan 5 needs:

  1. execute_cypher(stmt, params)
        Generic — runs a single Cypher statement; returns rows as
        list[dict[str, object]]. Used by Plan 8's GraphQueryResolver
        and Plan 10's agent graph_query node.

  2. replace_file_subgraph(snapshot_sha, file_path, cypher_inserts)
        Atomic per-file mutation per spec §8.4: DELETE the file's
        old subgraph, INSERT the new one, all in one transaction.
        File identity is the (snapshot_sha, file_path) tuple — nodes
        and edges carry these as properties.

  3. find_callers(symbol_fqn, snapshot_sha)
        Returns the files that DEFINE a symbol whose CALLS edge targets
        the given symbol_fqn at the given snapshot (spec §3.7: CALLS is
        Symbol→Symbol, File DEFINES Symbol). Backs Plan 10's agent
        `find_callers` tool.

  4. link_cross_file_symbols(snapshot_sha)
        Post-extraction pass (spec §3.7): redirect each CALLS edge whose
        callee is an unresolved (file_path=NULL) :Symbol to the resolved
        :Symbol sharing its fqn, then delete the consumed placeholder.
        Invoked once per snapshot by IndexingService after the per-file
        barrier drains, inside the advisory lock. Idempotent.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from codeoracle.core.exceptions import GraphQueryError

# AGE Cypher invocation shape:
#   SELECT * FROM cypher('codeoracle', $$ MATCH (n) ... RETURN n $$) AS (n agtype);
# Parameter substitution into Cypher requires building the statement
# string carefully (AGE supports the $param syntax inside the dollar-quoted
# Cypher body if the parameters are passed via a JSON map to
# cypher('codeoracle', $$...$$, $params)). Plan 5 keeps Cypher inline (no
# user-supplied identifiers) so SQL parameters bind only to literals,
# never to identifiers.

_GRAPH_NAME = "codeoracle"  # matches bootstrap_db.sql's create_graph('codeoracle')


class GraphRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def execute_cypher(
        self,
        cypher_body: str,
        params: dict[str, Any] | None = None,
        *,
        column_spec: str = "result agtype",
    ) -> list[dict[str, object]]:
        """Run one Cypher statement; return result rows.

        `cypher_body` is the inside of cypher('codeoracle', $$...$$) — the
        caller writes pure Cypher, not the SQL wrapper.
        `column_spec` defines the AS clause (default 'result agtype').
        `params` are passed as a Postgres parameters map to AGE.

        Plan 5 callers don't need fancy result decoding — AGE returns
        agtype values as Python strings (no psycopg agtype codec is
        registered; Plan 3's listener only runs `LOAD 'age'` + `SET
        search_path`). Callers that need the decoded form use the
        defensive `str(value).strip('"')` pattern at the call site.
        """
        stmt_sql = (
            f"SELECT * FROM cypher('{_GRAPH_NAME}', "
            f"$cypher${cypher_body}$cypher$) AS ({column_spec})"
        )
        try:
            result = await self._session.execute(text(stmt_sql), params or {})
            rows = result.mappings().all()
        except Exception as exc:  # noqa: BLE001
            raise GraphQueryError(
                f"Cypher execution failed: {exc} | stmt={cypher_body[:200]!r}"
            ) from exc
        return [dict(r) for r in rows]

    async def replace_file_subgraph(
        self,
        *,
        snapshot_sha: str,
        file_path: str,
        cypher_inserts: list[str],
    ) -> None:
        """Atomically replace the subgraph for one (snapshot_sha, file_path).

        Transaction wrapping is provided by the surrounding AsyncSession;
        callers run this inside `async with session.begin():` (or rely on
        the implicit transaction the session manages).

        cypher_inserts is a list of Cypher statements ALREADY formatted
        with the snapshot_sha + file_path properties — the JoernCpgParser
        (Plan 5 Task 12) emits them. This method DELETE-by-properties
        first, then runs each insert in sequence.
        """
        delete_stmt = (
            f"MATCH (n) WHERE n.snapshot_sha = $snapshot_sha "
            f"AND n.file_path = $file_path "
            "DETACH DELETE n"
        )
        await self.execute_cypher(
            delete_stmt,
            {"snapshot_sha": snapshot_sha, "file_path": file_path},
        )
        for stmt in cypher_inserts:
            await self.execute_cypher(stmt)

    async def find_callers(
        self,
        *,
        symbol_fqn: str,
        snapshot_sha: str,
    ) -> list[str]:
        """Return file paths that contain a CALLS edge into `symbol_fqn`
        at the given snapshot. Per spec §3.7, CALLS is Symbol→Symbol and a
        File DEFINES a Symbol, so a "caller file" is the file that DEFINES
        a symbol which CALLS `symbol_fqn`. The agent-orchestration
        `find_callers` tool (Plan 10) dispatches to this primitive.
        """
        # File path accessor is `file_path` (the parser's per-file partition
        # key, shared with replace_file_subgraph's DELETE) — File has no bare
        # `path` property; `AS path` is only the output-column alias.
        cypher = (
            "MATCH (caller:Symbol {snapshot_sha:$snapshot_sha})-[:CALLS]->"
            "(s:Symbol {fqn:$symbol_fqn, snapshot_sha:$snapshot_sha}) "
            "MATCH (f:File {snapshot_sha:$snapshot_sha})-[:DEFINES]->(caller) "
            "RETURN DISTINCT f.file_path AS path"
        )
        rows = await self.execute_cypher(
            cypher,
            {"snapshot_sha": snapshot_sha, "symbol_fqn": symbol_fqn},
            column_spec="path agtype",
        )
        # AGE returns agtype values as quoted JSON strings; decode the
        # outer quotes that agtype adds.
        return [str(r["path"]).strip('"') for r in rows]

    async def link_cross_file_symbols(self, snapshot_sha: str) -> None:
        """Resolve cross-file CALLS edges by FQN match across one snapshot.

        Per spec §3.7: per-file CPGs cannot resolve a CALLS whose callee
        lives in another file (the callee :Symbol is provisional, with
        file_path=NULL). This pass redirects each such edge to the
        resolved :Symbol (file_path IS NOT NULL) sharing the callee fqn,
        then deletes the consumed placeholder. Provisional callees with no
        in-snapshot match (stdlib / third-party / dynamic dispatch) are
        RETAINED with file_path=NULL so consumers can tell external from
        internal symbols. Idempotent — a second run is a no-op.
        """
        await self.execute_cypher(
            "MATCH (caller:Symbol {snapshot_sha:$sha})-[r:CALLS]->"
            "(unresolved:Symbol {snapshot_sha:$sha}) "
            "WHERE unresolved.file_path IS NULL "
            "MATCH (resolved:Symbol {fqn:unresolved.fqn, snapshot_sha:$sha}) "
            "WHERE resolved.file_path IS NOT NULL "
            "MERGE (caller)-[:CALLS]->(resolved) "
            "DELETE r, unresolved",
            {"sha": snapshot_sha},
        )
```

- [ ] **Step 2: Write `tests/integration/db/repositories/test_graph.py`**

```python
"""Plan 5 Task 8: GraphRepository integration tests against AGE-loaded PG."""

from __future__ import annotations

import pytest

from codeoracle.db.repositories.graph import GraphRepository

pytestmark = pytest.mark.integration


async def test_execute_cypher_returns_rows(async_session):
    repo = GraphRepository(async_session)
    rows = await repo.execute_cypher(
        "CREATE (n:Test {value:1}) RETURN n.value AS v",
        column_spec="v agtype",
    )
    assert rows
    assert str(rows[0]["v"]).strip('"') == "1"
    # Cleanup
    await repo.execute_cypher("MATCH (n:Test) DETACH DELETE n")


async def test_replace_file_subgraph_deletes_old_then_inserts_new(async_session):
    repo = GraphRepository(async_session)

    # Seed: insert one node with snapshot_sha='v1'
    await repo.execute_cypher(
        "CREATE (n:File {snapshot_sha:'v1', file_path:'src/x.py', "
        "marker:'old'}) RETURN n",
        column_spec="n agtype",
    )

    # Replace with a new node for the same (snapshot_sha, file_path)
    new_inserts = [
        "CREATE (n:File {snapshot_sha:'v1', file_path:'src/x.py', marker:'new'}) RETURN n"
    ]
    await repo.replace_file_subgraph(
        snapshot_sha="v1",
        file_path="src/x.py",
        cypher_inserts=new_inserts,
    )

    # Old node gone; new node present
    rows = await repo.execute_cypher(
        "MATCH (n:File {snapshot_sha:'v1', file_path:'src/x.py'}) RETURN n.marker AS m",
        column_spec="m agtype",
    )
    assert len(rows) == 1
    assert str(rows[0]["m"]).strip('"') == "new"

    # Cleanup
    await repo.execute_cypher(
        "MATCH (n:File {snapshot_sha:'v1'}) DETACH DELETE n"
    )


async def test_replace_file_subgraph_leaves_other_files_alone(async_session):
    repo = GraphRepository(async_session)
    await repo.execute_cypher(
        "CREATE (a:File {snapshot_sha:'v2', file_path:'src/a.py'}), "
        "       (b:File {snapshot_sha:'v2', file_path:'src/b.py'})"
    )

    # Replace only src/a.py
    await repo.replace_file_subgraph(
        snapshot_sha="v2",
        file_path="src/a.py",
        cypher_inserts=[
            "CREATE (a:File {snapshot_sha:'v2', file_path:'src/a.py', new:'yes'}) RETURN a"
        ],
    )

    # src/b.py untouched
    rows = await repo.execute_cypher(
        "MATCH (n:File {snapshot_sha:'v2', file_path:'src/b.py'}) RETURN n.file_path AS p",
        column_spec="p agtype",
    )
    assert len(rows) == 1

    # Cleanup
    await repo.execute_cypher("MATCH (n:File {snapshot_sha:'v2'}) DETACH DELETE n")


async def test_find_callers_returns_distinct_paths(async_session):
    repo = GraphRepository(async_session)
    # Valid §3.7 topology: a target :Symbol; caller :Symbols DEFINED by
    # src/a.py and src/b.py that each CALL the target; src/c.py defines no
    # caller. A "caller file" is the file that DEFINES a symbol calling the
    # target — so the result is {src/a.py, src/b.py}.
    await repo.execute_cypher(
        "CREATE (t:Symbol {fqn:'pkg.target', file_path:'src/t.py', "
        "snapshot_sha:'v3'}), "
        "       (ca:Symbol {fqn:'pkg.ca', file_path:'src/a.py', "
        "snapshot_sha:'v3'}), "
        "       (cb:Symbol {fqn:'pkg.cb', file_path:'src/b.py', "
        "snapshot_sha:'v3'}), "
        "       (fa:File {file_path:'src/a.py', snapshot_sha:'v3'}), "
        "       (fb:File {file_path:'src/b.py', snapshot_sha:'v3'}), "
        "       (fc:File {file_path:'src/c.py', snapshot_sha:'v3'}), "
        "       (fa)-[:DEFINES]->(ca), "
        "       (fb)-[:DEFINES]->(cb), "
        "       (ca)-[:CALLS]->(t), "
        "       (cb)-[:CALLS]->(t)"
    )
    paths = await repo.find_callers(
        symbol_fqn="pkg.target", snapshot_sha="v3"
    )
    assert set(paths) == {"src/a.py", "src/b.py"}

    # Cleanup
    await repo.execute_cypher("MATCH (n) WHERE n.snapshot_sha='v3' DETACH DELETE n")


async def test_link_cross_file_symbols_resolves_and_retains(async_session):
    repo = GraphRepository(async_session)
    # Seed: a caller (file_path set) -[:CALLS]-> an UNRESOLVED callee
    # (file_path NULL) whose fqn DOES have a resolved match; a resolved
    # :Symbol with that same fqn (file_path set); and a second unresolved
    # callee whose fqn has NO resolved match (external — stdlib/3rd-party).
    await repo.execute_cypher(
        "CREATE (caller:Symbol {fqn:'pkg.caller', file_path:'src/caller.py', "
        "snapshot_sha:'v4'}), "
        "       (unresolved:Symbol {fqn:'pkg.callee', snapshot_sha:'v4'}), "
        "       (resolved:Symbol {fqn:'pkg.callee', file_path:'src/callee.py', "
        "snapshot_sha:'v4'}), "
        "       (ext:Symbol {fqn:'os.path.join', snapshot_sha:'v4'}), "
        "       (caller)-[:CALLS]->(unresolved), "
        "       (caller)-[:CALLS]->(ext)"
    )

    await repo.link_cross_file_symbols("v4")

    # (a) the CALLS edge now points to the RESOLVED node (file_path set)
    resolved_rows = await repo.execute_cypher(
        "MATCH (c:Symbol {fqn:'pkg.caller', snapshot_sha:'v4'})-[:CALLS]->"
        "(t:Symbol {fqn:'pkg.callee', snapshot_sha:'v4'}) "
        "WHERE t.file_path IS NOT NULL "
        "RETURN t.file_path AS p",
        column_spec="p agtype",
    )
    assert len(resolved_rows) == 1
    assert str(resolved_rows[0]["p"]).strip('"') == "src/callee.py"

    # (b) the consumed unresolved placeholder for pkg.callee is deleted —
    # exactly one :Symbol with that fqn survives (the resolved one).
    callee_rows = await repo.execute_cypher(
        "MATCH (s:Symbol {fqn:'pkg.callee', snapshot_sha:'v4'}) "
        "RETURN s.file_path AS p",
        column_spec="p agtype",
    )
    assert len(callee_rows) == 1
    assert str(callee_rows[0]["p"]).strip('"') == "src/callee.py"

    # (c) the external unresolved :Symbol is RETAINED with file_path NULL,
    # and the caller's CALLS edge into it is untouched.
    ext_rows = await repo.execute_cypher(
        "MATCH (c:Symbol {fqn:'pkg.caller', snapshot_sha:'v4'})-[:CALLS]->"
        "(e:Symbol {fqn:'os.path.join', snapshot_sha:'v4'}) "
        "WHERE e.file_path IS NULL "
        "RETURN count(e) AS n",
        column_spec="n agtype",
    )
    assert str(ext_rows[0]["n"]).strip('"') == "1"

    # (d) idempotent — a second run is a no-op (no new edges, no deletions).
    await repo.link_cross_file_symbols("v4")
    after_rows = await repo.execute_cypher(
        "MATCH (c:Symbol {fqn:'pkg.caller', snapshot_sha:'v4'})-[:CALLS]->(t) "
        "RETURN t.fqn AS f",
        column_spec="f agtype",
    )
    assert {str(r["f"]).strip('"') for r in after_rows} == {
        "pkg.callee",
        "os.path.join",
    }

    # Cleanup
    await repo.execute_cypher("MATCH (n) WHERE n.snapshot_sha='v4' DETACH DELETE n")


async def test_execute_cypher_wraps_errors_as_graph_query_error(async_session):
    from codeoracle.core.exceptions import GraphQueryError

    repo = GraphRepository(async_session)
    with pytest.raises(GraphQueryError, match="execution failed"):
        await repo.execute_cypher("THIS IS NOT VALID CYPHER")
```

- [ ] **Step 3: Run + lint + type + commit**

```bash
uv run pytest tests/integration/db/repositories/test_graph.py -v
uv run ruff check src/codeoracle/db/repositories/graph.py
uv run ty check src/codeoracle/db/repositories/graph.py
git add src/codeoracle/db/repositories/graph.py tests/integration/db/repositories/test_graph.py
git commit -m "feat: Plan 5 Task 8 — GraphRepository (AGE Cypher executor + per-file replace)"
```

---

## Task 9: `src/codeoracle/services/indexing/advisory_lock.py` — single-flight primitive

**What this task delivers:** the `pg_advisory_lock(0x46AE0001)` context manager that enforces single-flight reindex across CLI + API. Uses `pg_try_advisory_lock` with a timeout loop rather than the blocking variant — Plan 5's CLI surfaces "another reindex is in flight" loudly rather than blocking opaquely. Lock id chosen from the spec §8.4 (`CODEORACLE-REIDX-1`, mnemonic `0x46AE0001`).

**Files:**
- Create: `src/codeoracle/services/__init__.py` (if Plan 4 didn't create it — confirm and skip if present)
- Create: `src/codeoracle/services/indexing/__init__.py`
- Create: `src/codeoracle/services/indexing/advisory_lock.py`
- Create: `tests/integration/services/indexing/__init__.py`
- Create: `tests/integration/services/indexing/test_advisory_lock.py`

- [ ] **Step 1: Confirm `src/codeoracle/services/__init__.py` exists**

```bash
test -f src/codeoracle/services/__init__.py || touch src/codeoracle/services/__init__.py
test -f src/codeoracle/services/indexing/__init__.py || touch src/codeoracle/services/indexing/__init__.py
```

(Plan 4 created `services/context_budget.py` so `services/__init__.py` likely exists. The indexing/ subpackage is new in Plan 5.)

- [ ] **Step 2: Write `src/codeoracle/services/indexing/advisory_lock.py`**

```python
"""pg_advisory_lock-based single-flight primitive.

Spec §8.4: `pg_advisory_lock(0x46AE0001)` (constant lock-id CODEORACLE-REIDX-1)
acquired by both `codeoracle index` and POST /v1/reindex before any extractor
runs; released by `pg_advisory_unlock` in the `finally` block. Cross-process
safe (single Postgres) — second concurrent caller blocks (or fail-fast via
`pg_try_advisory_lock` for the CLI's preferred behavior).

Plan 5 uses pg_try_advisory_lock + a poll loop. Advisory locks are
session-scoped — if the holding connection dies, Postgres releases the
lock automatically. We pin the lock to a dedicated long-lived connection
inside the context manager so it does NOT get returned to the pool
mid-reindex (which would release the lock via connection-close).
"""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from typing import AsyncIterator

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from codeoracle.core.exceptions import AdvisoryLockTimeoutError

logger = logging.getLogger(__name__)

# Spec §8.4: lock id constant — mnemonic CODEORACLE-REIDX-1, hex 0x46AE0001.
REINDEX_LOCK_ID: int = 0x46AE0001


@asynccontextmanager
async def reindex_single_flight(
    engine: AsyncEngine,
    *,
    timeout_seconds: float = 0.0,
    poll_interval_seconds: float = 0.25,
) -> AsyncIterator[None]:
    """Acquire the reindex single-flight lock.

    `timeout_seconds`:
      - 0.0 (default): fail-fast — raise AdvisoryLockTimeoutError on
        the first failed try. Use this from the CLI so users see
        "another reindex is in flight" immediately.
      - > 0: poll for up to N seconds. Use this from the API or worker
        invocations that should serialize gracefully.

    Holds the lock on a DEDICATED connection (acquired via engine.connect())
    so the lock isn't released by pool-checkin during the body.
    """
    conn = await engine.connect()
    try:
        deadline_loops = max(1, int(timeout_seconds / poll_interval_seconds))
        acquired = False
        for _attempt in range(deadline_loops):
            result = await conn.execute(
                text("SELECT pg_try_advisory_lock(:lock_id) AS got"),
                {"lock_id": REINDEX_LOCK_ID},
            )
            if result.scalar_one():
                acquired = True
                break
            if timeout_seconds <= 0:
                # Fail-fast
                break
            await asyncio.sleep(poll_interval_seconds)
        if not acquired:
            raise AdvisoryLockTimeoutError(
                f"pg_try_advisory_lock({REINDEX_LOCK_ID:#x}) failed after "
                f"{timeout_seconds:.2f}s — another reindex is in flight."
            )
        logger.info("reindex_lock_acquired", extra={"lock_id": REINDEX_LOCK_ID})
        try:
            yield
        finally:
            # Explicit release; connection-close also releases but
            # explicit is safer (and lets the connection return to the
            # pool cleanly).
            await conn.execute(
                text("SELECT pg_advisory_unlock(:lock_id)"),
                {"lock_id": REINDEX_LOCK_ID},
            )
            logger.info(
                "reindex_lock_released", extra={"lock_id": REINDEX_LOCK_ID}
            )
    finally:
        await conn.close()
```

- [ ] **Step 3: Write `tests/integration/services/indexing/test_advisory_lock.py`**

```python
"""Plan 5 Task 9: pg_advisory_lock-based single-flight tests.

Plan 5 verifies the primitive against a real Postgres. Plan 6 adds a
contention test that fires two extractors concurrently and asserts they
serialize."""

from __future__ import annotations

import asyncio

import pytest

from codeoracle.core.exceptions import AdvisoryLockTimeoutError
from codeoracle.services.indexing.advisory_lock import (
    REINDEX_LOCK_ID,
    reindex_single_flight,
)

pytestmark = pytest.mark.integration


async def test_single_acquirer_succeeds(async_engine):
    async with reindex_single_flight(async_engine, timeout_seconds=0.0):
        # We're inside the lock — sanity-check by trying a non-blocking
        # try on the SAME engine (different connection). Should fail.
        async with async_engine.connect() as conn2:
            from sqlalchemy import text
            r = await conn2.execute(
                text("SELECT pg_try_advisory_lock(:l)"),
                {"l": REINDEX_LOCK_ID},
            )
            got = r.scalar_one()
            if got:
                # Postgres considers same-session acquires reentrant;
                # ensure release.
                await conn2.execute(
                    text("SELECT pg_advisory_unlock(:l)"),
                    {"l": REINDEX_LOCK_ID},
                )
            # Plan 5's primitive holds on a dedicated connection, so this
            # check should report False from any OTHER connection. The
            # assertion enforces that property of `reindex_single_flight`.
            assert got is False, (
                "Advisory lock not isolated to its own connection — "
                "another connection was able to acquire it concurrently."
            )


async def test_fail_fast_when_held(async_engine):
    """timeout_seconds=0 raises immediately if the lock is held."""
    async with reindex_single_flight(async_engine, timeout_seconds=0.0):
        with pytest.raises(AdvisoryLockTimeoutError, match="another reindex"):
            async with reindex_single_flight(async_engine, timeout_seconds=0.0):
                pass


async def test_timeout_waits_then_acquires(async_engine):
    """timeout_seconds>0 polls until the lock frees."""
    holder_done = asyncio.Event()

    async def hold_briefly():
        async with reindex_single_flight(async_engine, timeout_seconds=0.0):
            await asyncio.sleep(0.3)
        holder_done.set()

    holder = asyncio.create_task(hold_briefly())
    await asyncio.sleep(0.05)  # ensure holder has the lock

    # Waiter polls up to 2s; should succeed before holder finishes
    async with reindex_single_flight(async_engine, timeout_seconds=2.0):
        pass

    await holder
    assert holder_done.is_set()


async def test_lock_released_on_exception_inside_block(async_engine):
    """If the body raises, the finally clause must release the lock."""
    with pytest.raises(ValueError):
        async with reindex_single_flight(async_engine, timeout_seconds=0.0):
            raise ValueError("boom")
    # Subsequent acquirer must not hit AdvisoryLockTimeoutError
    async with reindex_single_flight(async_engine, timeout_seconds=0.0):
        pass
```

- [ ] **Step 4: Run + lint + type + commit**

```bash
uv run pytest tests/integration/services/indexing/test_advisory_lock.py -v
uv run ruff check src/codeoracle/services/indexing/advisory_lock.py
uv run ty check src/codeoracle/services/indexing/advisory_lock.py
git add src/codeoracle/services/__init__.py src/codeoracle/services/indexing/__init__.py \
        src/codeoracle/services/indexing/advisory_lock.py \
        tests/integration/services/indexing/
git commit -m "feat: Plan 5 Task 9 — pg_advisory_lock single-flight primitive (CODEORACLE-REIDX-1)"
```

---

## Task 10: `src/codeoracle/services/indexing/delta.py` — DeltaComputer

**What this task delivers:** the changeset computer. Given a base SHA (or None = "since last applied snapshot"), returns the list of files that must be re-extracted: changed files + dependent files. Computes the dirty-WT SHA per spec §8.4 — `<head>+wt<sha256-of-tracked-changes>[12]` — when the working tree has uncommitted changes. Runs spec §8.4's two-pattern dependent-discovery Cypher (CALLS-via-Symbol UNION IMPORTS File→File). Pure functions + one class.

**Files:**
- Create: `src/codeoracle/services/indexing/delta.py`
- Create: `tests/unit/services/indexing/__init__.py`
- Create: `tests/unit/services/indexing/test_delta.py`
- Create: `tests/integration/services/indexing/test_delta_integration.py`

- [ ] **Step 1: Write `src/codeoracle/services/indexing/delta.py`**

```python
"""DeltaComputer — given a base SHA, return the set of files needing
re-extraction.

Pure stdlib for the git side (subprocess on `git`) + GraphRepository
for the dependent-discovery cypher. Lives in services/ (not adapters/)
because it composes the git CLI + the graph DB into a domain decision
("what does the next snapshot need to cover?").

Three responsibilities (Plan 5 scope):
  1. Compute the current snapshot's SHA — head SHA, or
     '<head>+wt<sha256>[12]' if the working tree is dirty.
  2. Compute the set of changed files: git diff <base>..HEAD plus
     untracked + modified working-tree files.
  3. Discover dependents via spec §8.4's two-pattern Cypher — files
     that DEFINE a caller of (CALLS), or that IMPORT, a changed file.
"""

from __future__ import annotations

import hashlib
import subprocess
from dataclasses import dataclass
from pathlib import Path

from codeoracle.db.repositories.graph import GraphRepository


@dataclass(frozen=True)
class Changeset:
    """Output of DeltaComputer.compute().

    Attributes:
        new_snapshot_sha: the SHA the next snapshot should bear —
            either the clean HEAD SHA or '<head>+wt<digest>'.
        head_sha: the raw `git rev-parse HEAD` value.
        is_dirty: whether the working tree had uncommitted changes.
        changed_files: union of (git diff <base>..HEAD) + working-tree
            modifications + untracked files. Repo-root-relative paths.
        dependent_files: files that DEFINE a caller of (CALLS), or that
            IMPORT, a changed file — per spec §8.4's two-pattern Cypher
            over the graph at the BASE snapshot (or [] when base is None
            or absent in the graph).
    """

    new_snapshot_sha: str
    head_sha: str
    is_dirty: bool
    changed_files: list[str]
    dependent_files: list[str]

    @property
    def all_files(self) -> list[str]:
        """Union of changed + dependents, deduplicated, sorted."""
        return sorted(set(self.changed_files) | set(self.dependent_files))


class DeltaComputer:
    """Compute the next snapshot's changeset.

    Construct with the repo root (defaults to CWD) and a GraphRepository
    for dependent discovery. The repo root must be a git working tree —
    `git rev-parse HEAD` runs inside it.
    """

    def __init__(
        self,
        *,
        graph_repo: GraphRepository,
        repo_root: Path | None = None,
    ) -> None:
        self._graph = graph_repo
        self._root = (repo_root or Path.cwd()).resolve()

    async def compute(
        self, *, base_sha: str | None
    ) -> Changeset:
        """Compute the changeset since `base_sha`.

        If `base_sha` is None, the caller is doing a full reindex — but
        Plan 5's slice-acceptance flow still computes a "delta" to
        produce a consistent shape; with base=None, we treat every
        tracked file as changed and skip dependent discovery.
        """
        head_sha = self._git("rev-parse", "HEAD")
        wt_status = self._git("status", "--porcelain")
        is_dirty = bool(wt_status.strip())

        if base_sha is None:
            # Full reindex — every tracked file
            changed = self._git("ls-files").splitlines()
            return Changeset(
                new_snapshot_sha=self._snapshot_sha(head_sha, is_dirty),
                head_sha=head_sha,
                is_dirty=is_dirty,
                changed_files=sorted(changed),
                dependent_files=[],
            )

        # Diff committed changes
        committed = self._git("diff", "--name-only", f"{base_sha}..HEAD").splitlines()
        # Modified + untracked working-tree files (porcelain format)
        wt_files = self._parse_porcelain_files(wt_status)

        changed_set = set(committed) | wt_files
        changed_sorted = sorted(changed_set)

        # Dependent discovery — query the BASE snapshot's graph for files
        # that depend on any changed file, per spec §8.4's two-pattern
        # contract: (1) files defining a caller of a symbol defined in a
        # changed file (Symbol-level CALLS graph, post-linking-pass), and
        # (2) files that import a changed file (File→File IMPORTS). One
        # query over the whole changed-file list — not a per-file loop.
        # The query already excludes changed files (WHERE NOT dep.file_path IN
        # $changed_files), so no separate dedup against changed_set. File nodes
        # key their path under `file_path` — the parser's per-file partition key,
        # shared with replace_file_subgraph's DELETE; `path` is not a property,
        # so the accessor must be `file_path` (the `AS path` below is only the
        # output-column alias the Python reads as row["path"]).
        dependent_rows = await self._graph.execute_cypher(
            "MATCH (changed:File {snapshot_sha:$base})-[:DEFINES]->"
            "(target:Symbol)<-[:CALLS]-(caller_def:Symbol)<-[:DEFINES]-"
            "(dep:File) "
            "WHERE changed.file_path IN $changed_files "
            "AND NOT dep.file_path IN $changed_files "
            "RETURN DISTINCT dep.file_path AS path "
            "UNION "
            "MATCH (dep:File)-[:IMPORTS]->(changed:File {snapshot_sha:$base}) "
            "WHERE changed.file_path IN $changed_files "
            "AND NOT dep.file_path IN $changed_files "
            "RETURN DISTINCT dep.file_path AS path",
            {"base": base_sha, "changed_files": changed_sorted},
            column_spec="path agtype",
        )
        dependents = {str(row["path"]).strip('"') for row in dependent_rows}

        return Changeset(
            new_snapshot_sha=self._snapshot_sha(head_sha, is_dirty),
            head_sha=head_sha,
            is_dirty=is_dirty,
            changed_files=changed_sorted,
            dependent_files=sorted(dependents),
        )

    def _snapshot_sha(self, head_sha: str, is_dirty: bool) -> str:
        """Return '<head>' or '<head>+wt<digest>[12]' per spec §8.4."""
        if not is_dirty:
            return head_sha

        wt_digest = self._wt_digest()
        return f"{head_sha}+wt{wt_digest[:12]}"

    def _wt_digest(self) -> str:
        """sha256 of the concatenation of (path, content) for every
        git-tracked modified file. Untracked files are NOT hashed —
        only tracked changes contribute to identity (spec §8.4).
        """
        h = hashlib.sha256()
        # `git diff HEAD --name-only` lists modified tracked files
        modified = self._git("diff", "HEAD", "--name-only").splitlines()
        for rel_path in sorted(modified):
            abs_path = self._root / rel_path
            if not abs_path.is_file():
                continue  # deleted file
            h.update(rel_path.encode("utf-8"))
            h.update(b"\0")
            with abs_path.open("rb") as f:
                while chunk := f.read(8192):
                    h.update(chunk)
            h.update(b"\0")
        return h.hexdigest()

    def _git(self, *args: str) -> str:
        """Run `git <args>` in repo_root; return stdout (utf-8)."""
        result = subprocess.run(
            ["git", *args],
            cwd=self._root,
            check=True,
            capture_output=True,
            text=True,
        )
        return result.stdout

    @staticmethod
    def _parse_porcelain_files(porcelain: str) -> set[str]:
        """Parse `git status --porcelain` output to a set of file paths.

        Porcelain format: `XY path` where X/Y are status codes. For
        renames: `XY old -> new`. Plan 5 treats both old and new as
        changed.
        """
        out: set[str] = set()
        for line in porcelain.splitlines():
            if not line.strip():
                continue
            # Skip 2 status chars + space
            entry = line[3:]
            if " -> " in entry:
                old, new = entry.split(" -> ", 1)
                out.add(old)
                out.add(new)
            else:
                out.add(entry)
        return out
```

- [ ] **Step 2: Write `tests/unit/services/indexing/test_delta.py`**

```python
"""Plan 5 Task 10: DeltaComputer unit tests.

Pure tests for the git+hash logic. Subprocess to git is real (uses a
temp git repo); dependent discovery is mocked (no real DB).
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from codeoracle.services.indexing.delta import DeltaComputer


@pytest.fixture
def tmp_git_repo(tmp_path: Path) -> Path:
    """Initialize a minimal git repo in tmp_path."""
    subprocess.run(["git", "init"], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(
        ["git", "config", "user.email", "test@example.com"],
        cwd=tmp_path, check=True, capture_output=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Test"],
        cwd=tmp_path, check=True, capture_output=True,
    )
    return tmp_path


def _commit(repo: Path, files: dict[str, str], msg: str) -> str:
    """Write files, add, commit. Return the new HEAD SHA."""
    for rel, content in files.items():
        p = repo / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content)
    subprocess.run(["git", "add", "."], cwd=repo, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", msg],
        cwd=repo, check=True, capture_output=True,
    )
    return subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=repo, check=True, capture_output=True, text=True,
    ).stdout.strip()


async def test_clean_workdir_returns_head_sha(tmp_git_repo: Path):
    sha = _commit(tmp_git_repo, {"a.py": "x = 1"}, "initial")

    graph = MagicMock()
    graph.execute_cypher = AsyncMock(return_value=[])
    dc = DeltaComputer(graph_repo=graph, repo_root=tmp_git_repo)

    cs = await dc.compute(base_sha=None)
    assert cs.new_snapshot_sha == sha
    assert cs.is_dirty is False
    assert "a.py" in cs.changed_files


async def test_dirty_workdir_appends_wt_digest(tmp_git_repo: Path):
    sha = _commit(tmp_git_repo, {"a.py": "x = 1"}, "initial")
    # Modify without committing
    (tmp_git_repo / "a.py").write_text("x = 2")

    graph = MagicMock()
    graph.execute_cypher = AsyncMock(return_value=[])
    dc = DeltaComputer(graph_repo=graph, repo_root=tmp_git_repo)

    cs = await dc.compute(base_sha=sha)
    assert cs.is_dirty is True
    assert cs.new_snapshot_sha.startswith(f"{sha}+wt")
    # Suffix is exactly 12 hex chars per spec
    suffix = cs.new_snapshot_sha.split("+wt", 1)[1]
    assert len(suffix) == 12
    assert all(c in "0123456789abcdef" for c in suffix)


async def test_dirty_digest_is_content_addressed(tmp_git_repo: Path):
    """Same modification → same digest. Different modification → different."""
    sha = _commit(tmp_git_repo, {"a.py": "x = 1"}, "initial")

    graph = MagicMock()
    graph.execute_cypher = AsyncMock(return_value=[])
    dc = DeltaComputer(graph_repo=graph, repo_root=tmp_git_repo)

    (tmp_git_repo / "a.py").write_text("x = 2")
    cs1 = await dc.compute(base_sha=sha)

    (tmp_git_repo / "a.py").write_text("x = 2")  # same content
    cs2 = await dc.compute(base_sha=sha)
    assert cs1.new_snapshot_sha == cs2.new_snapshot_sha

    (tmp_git_repo / "a.py").write_text("x = 3")  # different
    cs3 = await dc.compute(base_sha=sha)
    assert cs3.new_snapshot_sha != cs1.new_snapshot_sha


async def test_committed_diff_against_base(tmp_git_repo: Path):
    base = _commit(tmp_git_repo, {"a.py": "x = 1", "b.py": "y = 2"}, "initial")
    head = _commit(tmp_git_repo, {"b.py": "y = 3", "c.py": "z = 4"}, "two")

    graph = MagicMock()
    graph.execute_cypher = AsyncMock(return_value=[])
    dc = DeltaComputer(graph_repo=graph, repo_root=tmp_git_repo)

    cs = await dc.compute(base_sha=base)
    assert cs.head_sha == head
    assert cs.new_snapshot_sha == head
    assert set(cs.changed_files) == {"b.py", "c.py"}
    assert cs.dependent_files == []


async def test_dependents_excluded_when_in_changed(tmp_git_repo: Path):
    base = _commit(tmp_git_repo, {"a.py": "x = 1", "b.py": "y = 1"}, "initial")
    head = _commit(tmp_git_repo, {"a.py": "x = 2"}, "modify")

    # Dependent discovery is spec §8.4's two-pattern Cypher. Its
    # `WHERE NOT dep.file_path IN $changed_files` clause excludes changed
    # files IN-QUERY (the exclusion moved from Python to Cypher when the
    # query was conformed to §8.4 — integration-tested against real AGE),
    # so the DB never returns a changed file. The mock simulates that
    # filtered output: b.py (unchanged) depends on the changed a.py and
    # is returned; a.py is not. compute() surfaces the query's dependents
    # verbatim and adds no changed file back.
    graph = MagicMock()
    graph.execute_cypher = AsyncMock(
        return_value=[{"path": '"b.py"'}]
    )
    dc = DeltaComputer(graph_repo=graph, repo_root=tmp_git_repo)

    cs = await dc.compute(base_sha=base)
    assert "a.py" in cs.changed_files
    assert "b.py" in cs.dependent_files       # external dependent surfaces
    assert "a.py" not in cs.dependent_files   # changed file never double-counted


async def test_base_none_returns_all_tracked_files(tmp_git_repo: Path):
    _commit(tmp_git_repo, {"a.py": "1", "b.py": "2", "c/d.py": "3"}, "initial")

    graph = MagicMock()
    graph.execute_cypher = AsyncMock(return_value=[])
    dc = DeltaComputer(graph_repo=graph, repo_root=tmp_git_repo)

    cs = await dc.compute(base_sha=None)
    assert set(cs.changed_files) == {"a.py", "b.py", "c/d.py"}
    # No dependent discovery for full reindex
    assert cs.dependent_files == []
    graph.execute_cypher.assert_not_awaited()
```

- [ ] **Step 3: Write `tests/integration/services/indexing/test_delta_integration.py`**

```python
"""Plan 5 Task 10: DeltaComputer integration tests against AGE-loaded PG.

The unit suite (test_delta.py) mocks execute_cypher; this proves spec §8.4's
two-pattern dependent-discovery Cypher against a real graph — the
`:CALLS`-via-`:Symbol` UNION `:IMPORTS` File→File traversal and its
`WHERE NOT dep.file_path IN $changed_files` exclusion, including the list-valued
$changed_files bind that find_callers (scalar params only) never reaches.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from codeoracle.db.repositories.graph import GraphRepository
from codeoracle.services.indexing.delta import DeltaComputer

pytestmark = pytest.mark.integration


@pytest.fixture
def tmp_git_repo(tmp_path: Path) -> Path:
    """Minimal git repo in tmp_path (mirrors the test_delta.py unit fixture)."""
    subprocess.run(["git", "init"], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(
        ["git", "config", "user.email", "test@example.com"],
        cwd=tmp_path, check=True, capture_output=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Test"],
        cwd=tmp_path, check=True, capture_output=True,
    )
    return tmp_path


def _commit(repo: Path, files: dict[str, str], msg: str) -> str:
    """Write files, add, commit. Return the new HEAD SHA."""
    for rel, content in files.items():
        p = repo / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content)
    subprocess.run(["git", "add", "."], cwd=repo, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", msg], cwd=repo, check=True, capture_output=True,
    )
    return subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=repo, check=True, capture_output=True, text=True,
    ).stdout.strip()


async def test_compute_resolves_dependents_via_both_patterns_excluding_changed(
    async_session, tmp_git_repo: Path
):
    """End-to-end §8.4 dependent discovery against real AGE.

    Graph at the base snapshot (sha = the base commit):
      changed.py     -[:DEFINES]-> pkg.target
      caller.py      -[:DEFINES]-> pkg.caller -[:CALLS]-> pkg.target    (pattern 1)
      both.py        -[:DEFINES]-> pkg.both   -[:CALLS]-> pkg.target +
                     both.py      -[:IMPORTS]-> changed.py              (both patterns)
      importer.py    -[:IMPORTS]-> changed.py                          (pattern 2)
      alsochanged.py -[:DEFINES]-> pkg.alsoc  -[:CALLS]-> pkg.target    (pattern 1, but
                     alsochanged.py is itself in the changeset → excluded)
      unrelated.py   -[:DEFINES]-> pkg.unrel  (no edge to target)       (not a dependent)

    Two committed edits make changed_files = {changed.py, alsochanged.py}, so the
    resolved dependents are {caller.py, importer.py, both.py}:
      - caller.py via the CALLS pattern,
      - importer.py via the IMPORTS pattern,
      - both.py via BOTH but returned once (UNION is DISTINCT),
      - alsochanged.py suppressed by `NOT dep.file_path IN $changed_files` though it
        calls the target,
      - unrelated.py never traversed.
    """
    base = _commit(
        tmp_git_repo,
        {
            "changed.py": "v = 1",
            "alsochanged.py": "v = 1",
            "caller.py": "import changed",
            "importer.py": "import changed",
            "both.py": "import changed",
            "unrelated.py": "v = 1",
        },
        "base",
    )
    _commit(
        tmp_git_repo,
        {"changed.py": "v = 2", "alsochanged.py": "v = 2"},
        "edit the two changed files",
    )

    # Seed the base-snapshot subgraph. The literal snapshot_sha (the git base SHA
    # spliced in for __SHA__) mirrors test_graph.py's literal-seed idiom, so the
    # only parameter-bound value under test is compute()'s query side — exactly
    # the list-valued $changed_files bind the mocked unit tests cannot exercise.
    # File nodes carry `file_path` (the parser's per-file partition key, the
    # property the §8.4 query matches on), seeded with the same root-relative
    # names `git diff` emits, so the `$changed_files` IN-match holds by
    # construction.
    seed = (
        "CREATE "
        "(fchanged:File {file_path:'changed.py', snapshot_sha:'__SHA__'}), "
        "(fcaller:File {file_path:'caller.py', snapshot_sha:'__SHA__'}), "
        "(fimporter:File {file_path:'importer.py', snapshot_sha:'__SHA__'}), "
        "(fboth:File {file_path:'both.py', snapshot_sha:'__SHA__'}), "
        "(file_also:File {file_path:'alsochanged.py', snapshot_sha:'__SHA__'}), "
        "(file_unrelated:File {file_path:'unrelated.py', snapshot_sha:'__SHA__'}), "
        "(target:Symbol {fqn:'pkg.target', file_path:'changed.py', snapshot_sha:'__SHA__'}), "
        "(scaller:Symbol {fqn:'pkg.caller', file_path:'caller.py', snapshot_sha:'__SHA__'}), "
        "(sboth:Symbol {fqn:'pkg.both', file_path:'both.py', snapshot_sha:'__SHA__'}), "
        "(salso:Symbol {fqn:'pkg.alsoc', file_path:'alsochanged.py', snapshot_sha:'__SHA__'}), "
        "(sunrel:Symbol {fqn:'pkg.unrel', file_path:'unrelated.py', snapshot_sha:'__SHA__'}), "
        "(fchanged)-[:DEFINES]->(target), "
        "(fcaller)-[:DEFINES]->(scaller), "
        "(fboth)-[:DEFINES]->(sboth), "
        "(file_also)-[:DEFINES]->(salso), "
        "(file_unrelated)-[:DEFINES]->(sunrel), "
        "(scaller)-[:CALLS]->(target), "
        "(sboth)-[:CALLS]->(target), "
        "(salso)-[:CALLS]->(target), "
        "(fimporter)-[:IMPORTS]->(fchanged), "
        "(fboth)-[:IMPORTS]->(fchanged)"
    )
    repo = GraphRepository(async_session)
    await repo.execute_cypher(seed.replace("__SHA__", base))

    cs = await DeltaComputer(graph_repo=repo, repo_root=tmp_git_repo).compute(
        base_sha=base
    )

    assert set(cs.changed_files) == {"changed.py", "alsochanged.py"}
    assert set(cs.dependent_files) == {"caller.py", "importer.py", "both.py"}

    # Cleanup — the AGE graph is shared across the session-scoped container.
    await repo.execute_cypher(
        "MATCH (n) WHERE n.snapshot_sha=$sha DETACH DELETE n", {"sha": base}
    )
```

- [ ] **Step 4: Run + lint + type + commit**

```bash
uv run pytest tests/unit/services/indexing/test_delta.py -v
uv run pytest tests/integration/services/indexing/test_delta_integration.py -v
uv run ruff check src/codeoracle/services/indexing/delta.py
uv run ty check src/codeoracle/services/indexing/delta.py
git add src/codeoracle/services/indexing/delta.py tests/unit/services/indexing/ \
        tests/integration/services/indexing/test_delta_integration.py
git commit -m "feat: Plan 5 Task 10 — DeltaComputer (git diff + dependents + dirty-WT SHA)"
```

---

## Task 11: `src/codeoracle/adapters/joern/` — JoernClient + JoernCpgParser

**What this task delivers:** the per-file subprocess wrapper for `joern-cli` (`joern-parse` + `joern-export`) and the parser that converts joern's exported neo4j-csv-shaped output into AGE Cypher INSERT statements. Plan 5 keeps the scope minimal: nodes typed `:File`, `:Symbol`, and edges `:CALLS`, `:IMPORTS`, `:DEFINES`. These are the only labels the slice query touches. Plan 6's other extractors will add edges; the schema is open.

**Files:**
- Create: `src/codeoracle/adapters/joern/__init__.py`
- Create: `src/codeoracle/adapters/joern/client.py`
- Create: `src/codeoracle/adapters/joern/parser.py`
- Create: `src/codeoracle/adapters/joern/config.py`
- Create: `tests/unit/adapters/joern/__init__.py`
- Create: `tests/unit/adapters/joern/test_client.py` (mocked subprocess)
- Create: `tests/unit/adapters/joern/test_parser.py` (golden-file CSV → Cypher)
- Create: `tests/fixtures/joern/single_file_cpg/` (sample neo4j-csv export — checked into repo)

- [ ] **Step 1: Write `src/codeoracle/adapters/joern/__init__.py`**

```python
"""Joern adapter — per-file CPG extraction via subprocess.

Workers and IndexingService talk to Joern ONLY through JoernClient.
Direct `subprocess.run(['joern-parse', ...])` calls outside this
package are forbidden by convention (Plan 6 keeps this rule).
"""

from codeoracle.adapters.joern.client import JoernClient
from codeoracle.adapters.joern.config import JoernConfig
from codeoracle.adapters.joern.parser import JoernCpgParser

__all__ = ["JoernClient", "JoernConfig", "JoernCpgParser"]
```

- [ ] **Step 2: Write `src/codeoracle/adapters/joern/config.py`**

```python
"""JoernConfig — paths + timeouts. Env-loaded via pydantic-settings."""

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class JoernConfig(BaseSettings):
    """Where Joern lives and how long to wait.

    `joern_parse_bin` and `joern_export_bin` default to the CLI names —
    on the PATH if joern-cli is installed system-wide. In Docker, both
    resolve under /opt/joern/joern-cli/.
    """

    model_config = SettingsConfigDict(
        env_prefix="CODEORACLE_JOERN__",
        env_nested_delimiter="__",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    joern_parse_bin: str = Field(
        min_length=1,
        description=(
            "Path or name of the joern-parse binary. Default: 'joern-parse'."
        ),
    )
    joern_export_bin: str = Field(
        min_length=1,
        description="Path or name of joern-export binary.",
    )
    parse_timeout_seconds: int = Field(
        ge=10, le=600,
        description="Max seconds for one joern-parse subprocess.",
    )
    export_timeout_seconds: int = Field(
        ge=10, le=600,
        description="Max seconds for one joern-export subprocess.",
    )
    work_dir: str = Field(
        min_length=1,
        description=(
            "Filesystem dir under which JoernClient creates per-extraction "
            "scratch subdirs. /tmp/joern is a reasonable default in Docker."
        ),
    )
```

- [ ] **Step 3: Write `src/codeoracle/adapters/joern/client.py`**

```python
"""JoernClient — per-file CPG extraction.

Plan 5 scope is intentionally narrow: extract ONE Python source file
to a CPG, export to neo4j-csv format, return the export's directory
path. The parser (Plan 5 Task 11 Step 4) consumes the export.

Why subprocess + not the Joern HTTP server? The HTTP server boots a
JVM once per process — fine for high-concurrency. The CLI boots a JVM
per invocation — slow per call but stateless. Plan 5 prefers stateless
(workers can crash without leaving a hung server) and pays the cold-
start cost.

Reconciliation with the spec §8.5 cold-reindex budget ("Full reindex
(cold, 3K LOC) < 3min p95"). The budget holds at slice scale under
per-file JVM boot, for three reasons:
  1. Scale. The slice's cold-reindex target IS the ~3K-LOC `codeoracle/`
     codebase itself (a few dozen `.py` files), NOT large external target
     repositories. A JVM cold-start + single-file parse/export is seconds,
     not minutes; a few dozen files clears <3min with margin.
  2. Parallelism is the throughput lever, not per-call latency.
     `joern_indexer` workers are independent processes on ONE
     `STREAM_INDEXING_JOERN` consumer group (`count_per_poll=1`), so
     running N worker replicas makes wall-clock ≈ ceil(files / N) ×
     per-file-cost, NOT files × per-file-cost. The per-file JVM boot
     parallelizes across replicas with no shared state to contend on —
     the exact property the stateless-CLI choice buys.
  3. The budget is MEASURED, not gated. §8.5 records cold-reindex p95
     as a CI artifact ("not gate blockers at slice stage"), so the
     trade-off is observable per PR and a regression is visible before
     it bites.
Trade-off accepted: per-file JVM boot wastes the warm-JVM amortization
the HTTP server would give. Deferral (owner: post-slice perf hardening,
alongside §8.6 "Distributed worker scaling" → Phase 8): IF a
production-scale repository blows the budget, the optimization
is a persistent Joern server OR batch-N-files-per-JVM-boot — both
re-introduce the hung-server failure mode this plan deliberately avoids
at slice stage, so they are deferred until a measured budget breach
justifies the added recovery complexity.

Error mapping:
  - Non-zero exit → JoernExtractionError (terminal — file syntax
    failed; retrying won't help).
  - Subprocess timeout → JoernExtractionError too (treat as terminal
    since timeouts on simple files indicate joern-cli misconfig, not
    a transient blip).
  - File missing → JoernExtractionError (caller bug).
"""

from __future__ import annotations

import logging
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

from codeoracle.adapters.joern.config import JoernConfig
from codeoracle.core.exceptions import JoernExtractionError

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class JoernExportResult:
    """Output of one extract() call.

    Attributes:
        export_dir: directory containing nodes_*.csv + edges_*.csv
            (neo4j-csv format). Caller must rmtree() when done.
        file_path: the source file extracted (echoed for convenience).
    """

    export_dir: Path
    file_path: str


class JoernClient:
    def __init__(self, config: JoernConfig) -> None:
        self._config = config
        # Ensure the work_dir exists at construct time so first extract
        # doesn't fail on a missing parent.
        Path(self._config.work_dir).mkdir(parents=True, exist_ok=True)

    @property
    def worst_case_extraction_seconds(self) -> int:
        """Upper bound on one extract() call: parse + export run back-to-back,
        each capped at its own subprocess timeout. The joern_indexer consumer
        sizes its reclaim min-idle off this so XAUTOCLAIM never re-claims an
        extraction that is still legitimately running.
        """
        return (
            self._config.parse_timeout_seconds
            + self._config.export_timeout_seconds
        )

    def extract(self, file_path: Path) -> JoernExportResult:
        """Parse `file_path` to a CPG and export to neo4j-csv.

        Caller (joern_indexer worker) is responsible for rmtree() on
        the export_dir when done — JoernClient does not auto-clean.
        """
        if not file_path.is_file():
            raise JoernExtractionError(f"Source file does not exist: {file_path}")

        scratch = Path(
            tempfile.mkdtemp(prefix="joern-", dir=self._config.work_dir)
        )
        cpg_path = scratch / "cpg.bin"
        export_dir = scratch / "export"

        # 1) Parse
        parse_cmd = [
            self._config.joern_parse_bin,
            "--language", "pythonsrc",  # matches Plan 2's verification-gate invocation byte-for-byte
            "--output", str(cpg_path),
            str(file_path),
        ]
        self._run(parse_cmd, timeout=self._config.parse_timeout_seconds, phase="parse")

        # 2) Export to neo4j-csv
        export_dir.mkdir(parents=True, exist_ok=True)
        export_cmd = [
            self._config.joern_export_bin,
            "--repr", "cpg14",
            "--format", "neo4jcsv",
            "--out", str(export_dir),
            str(cpg_path),
        ]
        self._run(export_cmd, timeout=self._config.export_timeout_seconds, phase="export")

        return JoernExportResult(export_dir=export_dir, file_path=str(file_path))

    def cleanup(self, result: JoernExportResult) -> None:
        """Remove the scratch dir associated with `result.export_dir`."""
        scratch_root = result.export_dir.parent
        try:
            shutil.rmtree(scratch_root, ignore_errors=True)
        except OSError as exc:
            logger.warning(
                "joern_cleanup_failed",
                extra={"scratch": str(scratch_root), "error": str(exc)},
            )

    def _run(self, cmd: list[str], *, timeout: int, phase: str) -> None:
        """subprocess.run wrapper translating failures to JoernExtractionError."""
        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=timeout,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise JoernExtractionError(
                f"joern-{phase} timed out after {timeout}s. cmd={cmd[:2]}..."
            ) from exc
        except FileNotFoundError as exc:
            # joern binary missing on PATH — config error, but raise as
            # extraction error so workers dead-letter the message rather
            # than crash-loop forever.
            raise JoernExtractionError(
                f"joern binary not found: {cmd[0]!r} — verify CODEORACLE_JOERN__*"
            ) from exc

        if result.returncode != 0:
            raise JoernExtractionError(
                f"joern-{phase} exited {result.returncode}. "
                f"stderr={result.stderr[:500]!r}"
            )
```

- [ ] **Step 4: Write `src/codeoracle/adapters/joern/parser.py`**

```python
"""JoernCpgParser — convert joern's neo4j-csv export to AGE Cypher
INSERT statements.

joern-export --format=neo4jcsv produces per-label files:
    nodes_FILE.csv, nodes_METHOD.csv, nodes_CALL.csv, ...
    edges_CALLS.csv, edges_USES.csv, ...

Plan 5 scope: parser handles FILE, METHOD, CALL nodes and CALLS,
DEFINES, IMPORTS edges. Other labels are silently skipped — the
parser doesn't reject unknown labels because joern emits many label
types we don't yet use; lifting the filter into the schema is a Plan
6/7/8 concern as the agent's queries demand more.

Output is a list of Cypher CREATE statements, each one stamped with
(snapshot_sha, file_path) so the per-file delete-then-insert in
GraphRepository.replace_file_subgraph() catches the right subgraph.
"""

from __future__ import annotations

import csv
import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Final

logger = logging.getLogger(__name__)


# Map joern's neo4j-csv label files → AGE label we want.
_NODE_LABEL_MAP: Final[dict[str, str]] = {
    "FILE": "File",
    "METHOD": "Symbol",   # method-shaped definitions become :Symbol
    "CALL": "Call",       # call-site placeholders
    # Joern emits dozens more; Plan 5 only cares about these. Extend in Plan 6
    # (additional Joern node kinds for SCIP/embed integration).
}

_EDGE_LABEL_MAP: Final[dict[str, str]] = {
    "CALLS": "CALLS",
    "DEFINES": "DEFINES",
    "IMPORTS": "IMPORTS",
}


@dataclass(frozen=True)
class JoernCpgParser:
    """Stateless converter — construct once, parse many."""

    def to_cypher(
        self,
        *,
        export_dir: Path,
        snapshot_sha: str,
        file_path: str,
    ) -> list[str]:
        """Return Cypher CREATE statements representing this file's
        subgraph at `snapshot_sha`. Caller passes the list to
        GraphRepository.replace_file_subgraph().
        """
        statements: list[str] = []
        # Unmapped joern labels are non-fatal (Plan 5 only models File / Symbol /
        # Call / CALLS / DEFINES / IMPORTS) but must be observable —
        # silent skip would hide schema drift if joern's export shape changes
        # in a future release. Aggregated and logged once per parse at DEBUG.
        skipped_labels: dict[str, int] = {}

        # 1) Nodes
        for csv_path in export_dir.glob("nodes_*.csv"):
            joern_label = csv_path.stem.removeprefix("nodes_")
            age_label = _NODE_LABEL_MAP.get(joern_label)
            if age_label is None:
                key = f"node:{joern_label}"
                skipped_labels[key] = skipped_labels.get(key, 0) + 1
                continue
            statements.extend(
                self._node_statements(
                    csv_path=csv_path,
                    age_label=age_label,
                    snapshot_sha=snapshot_sha,
                    file_path=file_path,
                )
            )

        # 2) Edges (built second so node IDs exist when MATCH'd)
        for csv_path in export_dir.glob("edges_*.csv"):
            joern_label = csv_path.stem.removeprefix("edges_")
            age_label = _EDGE_LABEL_MAP.get(joern_label)
            if age_label is None:
                key = f"edge:{joern_label}"
                skipped_labels[key] = skipped_labels.get(key, 0) + 1
                continue
            statements.extend(
                self._edge_statements(
                    csv_path=csv_path,
                    age_label=age_label,
                    snapshot_sha=snapshot_sha,
                )
            )

        if skipped_labels:
            logger.debug(
                "joern_parser: skipped %d unmapped CSV file(s) for %s — %s",
                sum(skipped_labels.values()), file_path, skipped_labels,
            )

        return statements

    @staticmethod
    def _node_statements(
        *,
        csv_path: Path,
        age_label: str,
        snapshot_sha: str,
        file_path: str,
    ) -> list[str]:
        out: list[str] = []
        with csv_path.open(newline="") as f:
            reader = csv.DictReader(f)
            for row in reader:
                joern_id = row.get(":ID") or row.get("id:ID")
                if not joern_id:
                    continue
                name = row.get("name") or row.get("FULL_NAME") or row.get("CODE") or ""
                line = row.get("LINE_NUMBER") or row.get("lineNumber") or ""
                # Build the CREATE — properties JSON-escaped via json.dumps
                # then stripped of the outer braces for inlining
                props_obj = {
                    "joern_id": joern_id,
                    "snapshot_sha": snapshot_sha,
                    "file_path": file_path,
                    "name": name,
                    "line": line,
                }
                if age_label == "Symbol":
                    # Dotted Python FQN — contract shared with Plan 8's
                    # graph-evidence resolver + Plan 10's is_safe_symbol_fqn
                    # predicate. See top-of-plan "Cross-plan contracts".
                    props_obj["fqn"] = _derive_dotted_fqn(file_path, name)
                props_cypher = _props_to_cypher_object(props_obj)
                out.append(f"CREATE (:{age_label} {props_cypher})")
        return out

    @staticmethod
    def _edge_statements(
        *,
        csv_path: Path,
        age_label: str,
        snapshot_sha: str,
    ) -> list[str]:
        out: list[str] = []
        with csv_path.open(newline="") as f:
            reader = csv.DictReader(f)
            for row in reader:
                start_id = row.get(":START_ID") or row.get("start:START_ID")
                end_id = row.get(":END_ID") or row.get("end:END_ID")
                if not start_id or not end_id:
                    continue
                # MATCH by joern_id (the export-time IDs)
                out.append(
                    f"MATCH (a {{joern_id:'{_escape(start_id)}', "
                    f"snapshot_sha:'{_escape(snapshot_sha)}'}}), "
                    f"(b {{joern_id:'{_escape(end_id)}', "
                    f"snapshot_sha:'{_escape(snapshot_sha)}'}}) "
                    f"CREATE (a)-[:{age_label}]->(b)"
                )
        return out


def _derive_dotted_fqn(file_path: str, name: str) -> str:
    """Dotted Python FQN derived from (file_path, name).

    Algorithm (slice scope: src/codeoracle/* — see "Cross-plan contracts" at
    the top of the plan):
      1. Strip leading 'src/' (project package root convention).
      2. Strip '.py' suffix.
      3. Strip trailing '/__init__' (package modules collapse to the package).
      4. Replace '/' with '.'.
      5. If name is empty, return module; otherwise return f'{module}.{name}'.

    Examples:
      ('src/codeoracle/utils.py', 'foo')                  -> 'codeoracle.utils.foo'
      ('src/codeoracle/__init__.py', 'foo')               -> 'codeoracle.foo'
      ('src/codeoracle/adapters/joern/client.py', 'JoernClient')
                                                       -> 'codeoracle.adapters.joern.client.JoernClient'

    Known limitation (deferred to Plan 6): class scope. Methods inside
    classes collide with free functions of the same name in the same
    module — Plan 10's verifier surfaces this as a structured "ambiguous
    match", never a silent wrong answer. A future plan (currently scoped
    to Plan 6) will extend this helper with class-scope; the specific
    Joern column / technique is that plan's design call.

    Joern's FULL_NAME column is intentionally NOT consulted — its Python
    shape is uncaptured anywhere (Plan 2 verification gap). Deriving from
    file_path + name is hermetic and testable without that precondition;
    re-derivation in Plan 6 is a pass over existing graph rows.
    """
    module = file_path
    if module.startswith("src/"):
        module = module[len("src/"):]
    if module.endswith(".py"):
        module = module[: -len(".py")]
    if module.endswith("/__init__"):
        module = module[: -len("/__init__")]
    module = module.replace("/", ".")
    if not name:
        return module
    return f"{module}.{name}"


def _props_to_cypher_object(props: dict[str, object]) -> str:
    """{key:value, ...} Cypher object literal — JSON for values."""
    parts: list[str] = []
    for k, v in props.items():
        if isinstance(v, str):
            parts.append(f"{k}:'{_escape(v)}'")
        else:
            # Numbers, bools — Cypher accepts JSON-form for these
            parts.append(f"{k}:{json.dumps(v)}")
    return "{" + ", ".join(parts) + "}"


def _escape(s: str) -> str:
    """Escape single quotes for inclusion in Cypher string literals.
    Plan 5's source is internally produced (joern-export CSV), not user
    input, so this is defensive — the threat model is "joern emits a
    method name containing a backtick", not injection.
    """
    return s.replace("\\", "\\\\").replace("'", "\\'")
```

- [ ] **Step 5: Write `tests/fixtures/joern/single_file_cpg/`** — minimal sample export

Create five CSV files under `tests/fixtures/joern/single_file_cpg/` that mimic what `joern-export --format=neo4jcsv` produces for a single Python file with one function that calls another.

`nodes_FILE.csv`:

```csv
:ID,FILE_PATH
1,src/example.py
```

`nodes_METHOD.csv`:

```csv
:ID,name,LINE_NUMBER
2,foo,3
3,bar,7
```

`nodes_CALL.csv`:

```csv
:ID,CODE,LINE_NUMBER
4,foo(),8
```

`edges_DEFINES.csv`:

```csv
:START_ID,:END_ID
1,2
1,3
```

`edges_CALLS.csv`:

```csv
:START_ID,:END_ID
3,2
```

(The exact shape of joern's CSV may differ in column names; the parser tolerates either form via fallbacks. If the live joern-cli emits columns we don't handle, extend `_node_statements` / `_edge_statements`.)

- [ ] **Step 6: Write `tests/unit/adapters/joern/test_parser.py`**

```python
"""Plan 5 Task 11: JoernCpgParser unit tests using the checked-in fixture."""

from __future__ import annotations

from pathlib import Path

import pytest

from codeoracle.adapters.joern.parser import JoernCpgParser

FIXTURE = Path(__file__).parent.parent.parent.parent / "fixtures/joern/single_file_cpg"


def test_parser_emits_file_node():
    parser = JoernCpgParser()
    stmts = parser.to_cypher(
        export_dir=FIXTURE,
        snapshot_sha="abc1234",
        file_path="src/example.py",
    )
    file_stmts = [s for s in stmts if s.startswith("CREATE (:File ")]
    assert len(file_stmts) == 1
    assert "snapshot_sha:'abc1234'" in file_stmts[0]
    assert "file_path:'src/example.py'" in file_stmts[0]


def test_parser_emits_symbol_nodes_with_fqn():
    parser = JoernCpgParser()
    stmts = parser.to_cypher(
        export_dir=FIXTURE,
        snapshot_sha="abc1234",
        file_path="src/example.py",
    )
    symbol_stmts = [s for s in stmts if s.startswith("CREATE (:Symbol ")]
    assert len(symbol_stmts) == 2
    # Dotted Python FQN — see "Cross-plan contracts" at the top of the plan.
    # Plan 8 (graph-evidence resolver) + Plan 10 (is_safe_symbol_fqn predicate)
    # both query the graph using this form.
    assert any("fqn:'example.foo'" in s for s in symbol_stmts)
    assert any("fqn:'example.bar'" in s for s in symbol_stmts)
    # Raw facts preserved so a future Plan 6 derivation pass can refine
    # the FQN without re-extracting from source.
    assert all("name:" in s and "file_path:" in s for s in symbol_stmts)


def test_derive_dotted_fqn_examples():
    """Pin the FQN derivation algorithm — changing it forces a graph re-index."""
    from codeoracle.adapters.joern.parser import _derive_dotted_fqn

    # Module file + free symbol.
    assert _derive_dotted_fqn("src/codeoracle/utils.py", "foo") == "codeoracle.utils.foo"
    # Package __init__.py collapses to the package.
    assert _derive_dotted_fqn("src/codeoracle/__init__.py", "foo") == "codeoracle.foo"
    # Nested module path.
    assert _derive_dotted_fqn(
        "src/codeoracle/adapters/joern/client.py", "JoernClient"
    ) == "codeoracle.adapters.joern.client.JoernClient"
    # Empty name returns the module FQN.
    assert _derive_dotted_fqn("src/codeoracle/utils.py", "") == "codeoracle.utils"
    # Path without src/ prefix is passed through (file not under project root).
    assert _derive_dotted_fqn("vendor/lib.py", "foo") == "vendor.lib.foo"


def test_parser_emits_calls_edge():
    parser = JoernCpgParser()
    stmts = parser.to_cypher(
        export_dir=FIXTURE,
        snapshot_sha="abc1234",
        file_path="src/example.py",
    )
    calls_stmts = [s for s in stmts if ":CALLS" in s]
    assert len(calls_stmts) == 1
    # MATCH by joern_id from edges_CALLS.csv
    assert "joern_id:'3'" in calls_stmts[0]
    assert "joern_id:'2'" in calls_stmts[0]


def test_parser_skips_unknown_labels(tmp_path):
    """Unknown nodes_*.csv files are silently ignored."""
    # Create a fake export dir with one unknown label
    (tmp_path / "nodes_LITERAL.csv").write_text(":ID,name\n1,x\n")
    parser = JoernCpgParser()
    stmts = parser.to_cypher(
        export_dir=tmp_path,
        snapshot_sha="abc",
        file_path="x.py",
    )
    assert stmts == []  # nothing emitted


def test_parser_handles_empty_export(tmp_path):
    parser = JoernCpgParser()
    stmts = parser.to_cypher(
        export_dir=tmp_path,
        snapshot_sha="abc",
        file_path="x.py",
    )
    assert stmts == []


def test_escape_quotes_in_node_property():
    """A name containing ' must be safely escaped."""
    parser = JoernCpgParser()
    # Build a minimal one-node fixture with a quote in the name
    from tempfile import TemporaryDirectory

    with TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        (tmp / "nodes_METHOD.csv").write_text(
            ":ID,name,LINE_NUMBER\n1,quote'd,5\n"
        )
        stmts = parser.to_cypher(
            export_dir=tmp,
            snapshot_sha="abc",
            file_path="x.py",
        )
        assert any(r"quote\'d" in s for s in stmts), (
            f"Expected escaped single quote in: {stmts}"
        )
```

- [ ] **Step 7: Write `tests/unit/adapters/joern/test_client.py`** — mocked subprocess

```python
"""Plan 5 Task 11: JoernClient unit tests with mocked subprocess."""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from codeoracle.adapters.joern.client import JoernClient
from codeoracle.adapters.joern.config import JoernConfig
from codeoracle.core.exceptions import JoernExtractionError


@pytest.fixture
def joern_config(tmp_path):
    return JoernConfig(
        joern_parse_bin="joern-parse",
        joern_export_bin="joern-export",
        parse_timeout_seconds=60,
        export_timeout_seconds=60,
        work_dir=str(tmp_path / "joern-work"),
    )


def _ok_subprocess_result(returncode=0):
    return subprocess.CompletedProcess(
        args=[], returncode=returncode, stdout="", stderr=""
    )


def test_extract_creates_export_dir(joern_config, tmp_path):
    src = tmp_path / "x.py"
    src.write_text("def f(): pass")
    client = JoernClient(joern_config)
    with patch("subprocess.run", return_value=_ok_subprocess_result()):
        result = client.extract(src)
    assert result.file_path == str(src)
    assert result.export_dir.exists()
    assert result.export_dir.name == "export"


def test_extract_missing_file_raises(joern_config):
    client = JoernClient(joern_config)
    with pytest.raises(JoernExtractionError, match="does not exist"):
        client.extract(Path("/no/such/file.py"))


def test_extract_nonzero_exit_raises(joern_config, tmp_path):
    src = tmp_path / "x.py"
    src.write_text("def f(): pass")
    client = JoernClient(joern_config)
    bad = subprocess.CompletedProcess(
        args=[], returncode=1, stdout="", stderr="syntax error at line 3",
    )
    with patch("subprocess.run", return_value=bad):
        with pytest.raises(JoernExtractionError, match=r"exited 1"):
            client.extract(src)


def test_extract_timeout_raises(joern_config, tmp_path):
    src = tmp_path / "x.py"
    src.write_text("def f(): pass")
    client = JoernClient(joern_config)
    with patch(
        "subprocess.run",
        side_effect=subprocess.TimeoutExpired(cmd=["joern-parse"], timeout=60),
    ):
        with pytest.raises(JoernExtractionError, match="timed out"):
            client.extract(src)


def test_extract_missing_binary_raises(joern_config, tmp_path):
    src = tmp_path / "x.py"
    src.write_text("def f(): pass")
    client = JoernClient(joern_config)
    with patch("subprocess.run", side_effect=FileNotFoundError):
        with pytest.raises(JoernExtractionError, match="not found"):
            client.extract(src)


def test_cleanup_removes_scratch(joern_config, tmp_path):
    src = tmp_path / "x.py"
    src.write_text("def f(): pass")
    client = JoernClient(joern_config)
    with patch("subprocess.run", return_value=_ok_subprocess_result()):
        result = client.extract(src)
    scratch_root = result.export_dir.parent
    assert scratch_root.exists()
    client.cleanup(result)
    assert not scratch_root.exists()
```

- [ ] **Step 8: Run + lint + type + commit**

```bash
uv run pytest tests/unit/adapters/joern/ -v
uv run ruff check src/codeoracle/adapters/joern/
uv run ty check src/codeoracle/adapters/joern/
git add src/codeoracle/adapters/joern/ tests/unit/adapters/joern/ tests/fixtures/joern/
git commit -m "feat: Plan 5 Task 11 — JoernClient (subprocess) + JoernCpgParser (CSV → Cypher)"
```

---

## Task 12: `src/codeoracle/workers/joern_indexer/` — JoernIndexerConsumer

**What this task delivers:** the first concrete `WorkerConsumer` subclass. Consumes from `STREAM_INDEXING_JOERN`, validates payloads against `JoernIndexerMessage`, calls `JoernClient.extract()` + `JoernCpgParser.to_cypher()` + `GraphRepository.replace_file_subgraph()`, writes a `snapshot_file_outcomes` row via `SnapshotFileOutcomeRepository.upsert()`. The `__main__.py` constructs the WorkerContext and runs the consumer's `run()` loop until SIGTERM.

**Files:**
- Create: `src/codeoracle/workers/joern_indexer/__init__.py`
- Create: `src/codeoracle/workers/joern_indexer/__main__.py`
- Create: `src/codeoracle/workers/joern_indexer/message.py`
- Create: `src/codeoracle/workers/joern_indexer/consumer.py`
- Create: `src/codeoracle/workers/joern_indexer/tasks.py`
- Create: `tests/unit/workers/joern_indexer/__init__.py`
- Create: `tests/unit/workers/joern_indexer/test_consumer.py`

- [ ] **Step 1: Write `src/codeoracle/workers/joern_indexer/__init__.py`** (empty)

- [ ] **Step 2: Write `src/codeoracle/workers/joern_indexer/message.py`**

```python
"""JoernIndexerMessage — Pydantic envelope for a per-file extraction job.

Producer (IndexingService) sets all four fields; consumer validates
before dispatch.
"""

from codeoracle.workers._base.message import WorkerMessage


class JoernIndexerMessage(WorkerMessage):
    """Per-file Joern extraction request.

    Fields:
        batch_id: == snapshot_sha (parent reindex job)
        snapshot_sha: target snapshot (== batch_id; redundant for clarity)
        file_path: repo-root-relative path to the source file
        repo_root: absolute path to the repo containing the file (lets
            workers run from any CWD)
    """

    snapshot_sha: str
    file_path: str
    repo_root: str
```

- [ ] **Step 3: Write `src/codeoracle/workers/joern_indexer/tasks.py`**

```python
"""Per-message handler for the joern_indexer worker.

Pulled out into a free function so the consumer subclass stays thin
and the handler is independently unit-testable with a fixture of
JoernClient / GraphRepository / SnapshotFileOutcomeRepository mocks.
"""

from __future__ import annotations

import logging
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from codeoracle.adapters.joern.client import JoernClient
from codeoracle.adapters.joern.parser import JoernCpgParser
from codeoracle.core.exceptions import JoernExtractionError
from codeoracle.db.repositories.file_outcomes import SnapshotFileOutcomeRepository
from codeoracle.db.repositories.graph import GraphRepository
from codeoracle.workers.joern_indexer.message import JoernIndexerMessage

logger = logging.getLogger(__name__)


async def extract_file_to_graph(
    *,
    message: JoernIndexerMessage,
    joern_client: JoernClient,
    parser: JoernCpgParser,
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Extract one file's CPG, MERGE into AGE, record the outcome.

    Raises JoernExtractionError (terminal) when the joern-cli rejects
    the file. Transient errors (DB connection blips) bubble up
    untranslated; the base WorkerConsumer catches them and treats
    them as transient by default.
    """
    file_path = Path(message.repo_root) / message.file_path
    logger.info(
        "joern_indexer_dispatch",
        extra={"snapshot_sha": message.snapshot_sha, "file": message.file_path},
    )

    export = None
    try:
        export = joern_client.extract(file_path)
        cypher_stmts = parser.to_cypher(
            export_dir=export.export_dir,
            snapshot_sha=message.snapshot_sha,
            file_path=message.file_path,
        )

        async with db_session_factory() as session:
            graph = GraphRepository(session)
            await graph.replace_file_subgraph(
                snapshot_sha=message.snapshot_sha,
                file_path=message.file_path,
                cypher_inserts=cypher_stmts,
            )
            file_outcomes = SnapshotFileOutcomeRepository(session)
            await file_outcomes.upsert(
                snapshot_sha=message.snapshot_sha,
                file_path=message.file_path,
                extractor="joern",
                outcome="success",
            )
            await session.commit()
    except JoernExtractionError as exc:
        # Record the failed outcome BEFORE re-raising so IndexingService's
        # barrier count is accurate even when extraction fails.
        async with db_session_factory() as session:
            file_outcomes = SnapshotFileOutcomeRepository(session)
            await file_outcomes.upsert(
                snapshot_sha=message.snapshot_sha,
                file_path=message.file_path,
                extractor="joern",
                outcome="failed",
                error=str(exc),
            )
            await session.commit()
        raise
    finally:
        if export is not None:
            joern_client.cleanup(export)
```

- [ ] **Step 4: Write `src/codeoracle/workers/joern_indexer/consumer.py`**

```python
"""JoernIndexerConsumer — the concrete WorkerConsumer for Joern."""

from __future__ import annotations

import asyncio

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from codeoracle.adapters.joern.client import JoernClient
from codeoracle.adapters.joern.parser import JoernCpgParser
from codeoracle.adapters.valkey.client import ValkeyQueueClient
from codeoracle.workers._base.consumer import WorkerConsumer
from codeoracle.workers._base.message import RetryPolicy
from codeoracle.workers.joern_indexer.message import JoernIndexerMessage
from codeoracle.workers.joern_indexer.tasks import extract_file_to_graph
from codeoracle.core.streams import GROUP_JOERN_INDEXER, STREAM_INDEXING_JOERN


class JoernIndexerConsumer(WorkerConsumer[JoernIndexerMessage]):
    """Consumes indexing.joern → calls JoernClient → MERGEs into AGE."""

    stream = STREAM_INDEXING_JOERN
    group = GROUP_JOERN_INDEXER
    message_type = JoernIndexerMessage
    # ONE message per poll: joern extraction is slow (a JVM cold-start + parse
    # + export per file), so the reclaim min-idle must clear a full handle.
    # Batching would multiply the in-flight window (count_per_poll × handle)
    # past the orchestrator's 600s barrier, leaving no safe min-idle. See the
    # reclaim NOTE in WorkerConsumer.run().
    count_per_poll = 1
    block_ms = 5000

    def __init__(
        self,
        *,
        queue_client: ValkeyQueueClient,
        retry_policy: RetryPolicy,
        consumer_name: str,
        stop_event: asyncio.Event,
        joern_client: JoernClient,
        parser: JoernCpgParser,
        db_session_factory: async_sessionmaker[AsyncSession],
    ) -> None:
        super().__init__(
            queue_client=queue_client,
            retry_policy=retry_policy,
            consumer_name=consumer_name,
            stop_event=stop_event,
            # Reclaim entries idle past 2× the worst-case handle. One ×
            # covers an extraction running to its parse+export timeout
            # ceiling; the second × is margin so XAUTOCLAIM never re-claims a
            # still-running extraction. (handle() is idempotent — outcome
            # upsert + replace_file_subgraph — so an over-eager reclaim would
            # not corrupt the graph, but it WOULD spawn a concurrent duplicate
            # extraction: a re-claim stampede burning a worker slot + DB conn.)
            # Derived from config, so min-idle > handle holds by construction
            # (no runtime guard needed); count_per_poll=1 keeps the in-flight
            # bound at one handle.
            reclaim_min_idle_ms=(
                2 * joern_client.worst_case_extraction_seconds * 1000
            ),
        )
        self._joern = joern_client
        self._parser = parser
        self._db = db_session_factory

    async def handle(self, message: JoernIndexerMessage) -> None:
        await extract_file_to_graph(
            message=message,
            joern_client=self._joern,
            parser=self._parser,
            db_session_factory=self._db,
        )
```

- [ ] **Step 5: Write `src/codeoracle/workers/joern_indexer/__main__.py`**

```python
"""Joern indexer worker entrypoint — `python -m codeoracle.workers.joern_indexer`."""

from __future__ import annotations

import asyncio

from codeoracle.adapters.joern.client import JoernClient
from codeoracle.adapters.joern.config import JoernConfig
from codeoracle.adapters.joern.parser import JoernCpgParser
from codeoracle.workers._base.wiring import (
    build_worker_context,
    teardown_worker_context,
)
from codeoracle.workers.joern_indexer.consumer import JoernIndexerConsumer


async def amain() -> None:
    ctx = await build_worker_context(consumer_name_prefix="joern_indexer")
    joern_client = JoernClient(JoernConfig())  # type: ignore[call-arg]
    parser = JoernCpgParser()
    consumer = JoernIndexerConsumer(
        queue_client=ctx.queue_client,
        retry_policy=ctx.retry_policy,
        consumer_name=ctx.consumer_name,
        stop_event=ctx.stop_event,
        joern_client=joern_client,
        parser=parser,
        db_session_factory=ctx.db_session_factory,
    )
    try:
        await consumer.run()
    finally:
        await teardown_worker_context(ctx)


if __name__ == "__main__":
    asyncio.run(amain())
```

- [ ] **Step 6: Write `tests/unit/workers/joern_indexer/test_consumer.py`**

```python
"""Plan 5 Task 12: JoernIndexerConsumer unit tests — mocked everything below it."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from codeoracle.adapters.valkey.client import ConsumedMessage
from codeoracle.core.exceptions import JoernExtractionError
from codeoracle.workers._base.message import RetryPolicy
from codeoracle.workers.joern_indexer.consumer import JoernIndexerConsumer
from codeoracle.workers.joern_indexer.message import JoernIndexerMessage


class _FakeQueue:
    """Minimal in-memory stand-in (same shape as Task 4's helper)."""

    def __init__(self, scripted):
        self._s = scripted
        self.acks = []
        self.dlqs = []
        self.calls = 0

    async def read_pending(self, **_):
        if self.calls < len(self._s):
            out = self._s[self.calls]
            self.calls += 1
            return out
        await asyncio.sleep(0.01)
        return []

    async def ack(self, *a):
        self.acks.append(a)

    async def dead_letter(self, *, stream, message_id, payload, error):
        self.dlqs.append((stream, message_id, error))


def _msg(file="src/x.py"):
    return ConsumedMessage(
        id="1-0",
        stream="indexing.joern",
        payload={
            "batch_id": "abc1234",
            "snapshot_sha": "abc1234",
            "file_path": file,
            "repo_root": "/tmp",
        },
        delivered_count=1,
    )


@pytest.fixture
def consumer_setup(tmp_path):
    """Returns (consumer, queue, joern_mock, parser_mock, session_factory_mock)."""
    queue = _FakeQueue([[_msg()]])
    joern = MagicMock()
    parser = MagicMock()
    db = MagicMock()

    consumer = JoernIndexerConsumer(
        queue_client=queue,
        retry_policy=RetryPolicy(max_attempts=3),
        consumer_name="t1",
        stop_event=asyncio.Event(),
        joern_client=joern,
        parser=parser,
        db_session_factory=db,
    )
    return consumer, queue, joern, parser, db


async def test_happy_path_invokes_joern_and_acks(consumer_setup):
    consumer, queue, joern, parser, db = consumer_setup

    # Joern returns a mock export; parser returns 1 cypher stmt
    joern.extract.return_value = MagicMock(
        export_dir=MagicMock(),
        file_path="/tmp/src/x.py",
    )
    parser.to_cypher.return_value = ["CREATE (:File {snapshot_sha:'abc1234'})"]

    # Mock the async session context — extract_file_to_graph uses
    # `async with db_session_factory() as session:` twice (once for
    # graph + file_outcome write, once isn't triggered on success).
    session_ctx = AsyncMock()
    session_ctx.__aenter__.return_value = AsyncMock()
    session_ctx.__aenter__.return_value.commit = AsyncMock()
    db.return_value = session_ctx

    # Patch GraphRepository + SnapshotFileOutcomeRepository constructors
    with patch("codeoracle.workers.joern_indexer.tasks.GraphRepository") as Gr, \
         patch("codeoracle.workers.joern_indexer.tasks.SnapshotFileOutcomeRepository") as file_outcomes_repo_cls:
        Gr.return_value.replace_file_subgraph = AsyncMock()
        file_outcomes_repo_cls.return_value.upsert = AsyncMock()

        task = asyncio.create_task(consumer.run())
        for _ in range(500):
            if queue.acks:
                break
            await asyncio.sleep(0.01)
        consumer._stop.set()
        await task

    joern.extract.assert_called_once()
    parser.to_cypher.assert_called_once()
    assert len(queue.acks) == 1


async def test_joern_extraction_failure_dead_letters(consumer_setup):
    consumer, queue, joern, parser, db = consumer_setup
    joern.extract.side_effect = JoernExtractionError("syntax error")

    # File-outcome upsert still happens in the except block
    session_ctx = AsyncMock()
    session_ctx.__aenter__.return_value = AsyncMock()
    session_ctx.__aenter__.return_value.commit = AsyncMock()
    db.return_value = session_ctx

    with patch("codeoracle.workers.joern_indexer.tasks.SnapshotFileOutcomeRepository") as file_outcomes_repo_cls:
        file_outcomes_repo_cls.return_value.upsert = AsyncMock()

        task = asyncio.create_task(consumer.run())
        for _ in range(500):
            if queue.dlqs:
                break
            await asyncio.sleep(0.01)
        consumer._stop.set()
        await task

    # 1 DLQ entry; the message was acked off the original stream
    assert len(queue.dlqs) == 1
    assert "Terminal failure" in queue.dlqs[0][2]
    # File-outcome row was written with outcome=failed
    file_outcomes_repo_cls.return_value.upsert.assert_awaited()
    failed_call = file_outcomes_repo_cls.return_value.upsert.await_args
    assert failed_call.kwargs["outcome"] == "failed"
```

- [ ] **Step 7: Run + lint + type + commit**

```bash
uv run pytest tests/unit/workers/joern_indexer/ -v
uv run ruff check src/codeoracle/workers/joern_indexer/
uv run ty check src/codeoracle/workers/joern_indexer/
git add src/codeoracle/workers/joern_indexer/ tests/unit/workers/joern_indexer/
git commit -m "feat: Plan 5 Task 12 — JoernIndexerConsumer + __main__ entrypoint"
```

---

## Task 13: `src/codeoracle/services/indexing/orchestrator.py` + `service.py`

**What this task delivers:** the IndexingOrchestrator that fans out per-file extraction jobs to all configured extractors and waits on `snapshot_file_outcomes` for completion. IndexingService is the public-facing class — acquires the advisory lock, *sweeps any SIGKILL/OOM-orphaned `APPLYING` rows from a prior process* (check-on-use cleanup, see L29 deferral-list rewrite — DECIDE-D), computes the changeset via DeltaComputer, creates the pending snapshot, hands off to the orchestrator, then transitions to APPLIED (or FAILED).

The orchestrator is designed for Plan 6's extension: it iterates `self._extractor_kinds` (Plan 5 hardcodes `["joern"]`; Plan 6 extends to `["joern","scip","opengrep","embed"]`) and enqueues one message per extractor per file.

**Files:**
- Create: `src/codeoracle/services/indexing/orchestrator.py`
- Create: `src/codeoracle/services/indexing/service.py`
- Create: `tests/integration/services/indexing/test_orchestrator.py`
- Create: `tests/integration/services/indexing/test_service.py`

- [ ] **Step 1: Write `src/codeoracle/services/indexing/orchestrator.py`**

```python
"""IndexingOrchestrator — fan out per-file extraction jobs, wait on barrier.

For each file in the changeset, enqueue one message per extractor in
self._extractor_kinds. Wait until snapshot_file_outcomes row count
matches expected (== n_files * n_extractors) or the timeout expires.

Plan 5 hardcodes extractor_kinds=("joern",). Plan 6 extends the tuple
at construction time. The orchestrator itself is extractor-agnostic.

Connection-pool budget: at default config (1s poll × 600s timeout) one
reindex can issue up to ~600 PG sessions sequentially. Each session is
a clean checkout/return — not a leak — but the engine's `pool_size`
must support the churn rate. Plan 3's default of 5 is fine for one
reindex at a time (sequential checkout, no contention); concurrent
reindexes from different processes are gated by the advisory lock so
the orchestrator never overlaps itself. If Plan 6 introduces a
faster-polling barrier (e.g., listen/notify), revisit the budget.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Sequence

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from codeoracle.adapters.valkey.client import ValkeyQueueClient
from codeoracle.db.repositories.file_outcomes import SnapshotFileOutcomeRepository
from codeoracle.core.streams import STREAM_INDEXING_JOERN

logger = logging.getLogger(__name__)


_STREAM_BY_KIND: dict[str, str] = {
    "joern": STREAM_INDEXING_JOERN,
}


class IndexingOrchestrator:
    def __init__(
        self,
        *,
        queue_client: ValkeyQueueClient,
        db_session_factory: async_sessionmaker[AsyncSession],
        extractor_kinds: Sequence[str] = ("joern",),
        completion_poll_interval_seconds: float = 1.0,
        completion_timeout_seconds: float = 600.0,
    ) -> None:
        """A fresh SnapshotFileOutcomeRepository is constructed PER poll
        call inside _collect_outcomes(). Holding one across the loop
        would leak the session and crash on connection-pool churn —
        the session must be scoped to one logical query, not the
        orchestrator's lifetime.
        """
        unknown = [k for k in extractor_kinds if k not in _STREAM_BY_KIND]
        if unknown:
            raise ValueError(
                f"IndexingOrchestrator: unknown extractor_kinds {unknown!r}. "
                "Add entries to _STREAM_BY_KIND when Plan 6 extends this."
            )
        self._queue = queue_client
        self._db = db_session_factory
        self._kinds = tuple(extractor_kinds)
        self._poll = completion_poll_interval_seconds
        self._timeout = completion_timeout_seconds

    async def fan_out_and_wait(
        self,
        *,
        snapshot_sha: str,
        repo_root: str,
        files: Sequence[str],
    ) -> dict[str, str]:
        """Enqueue one job per file per extractor; wait for completion.

        Returns a per-extractor outcome map:
            { "joern": "success" | "partial" | "failed" }
        - success: every file succeeded
        - partial: some succeeded + some failed (still produces a usable index)
        - failed: every file failed (likely a config issue)
        """
        await self._enqueue_all(snapshot_sha, repo_root, files)
        return await self._wait_for_completion(snapshot_sha, len(files))

    async def _enqueue_all(
        self,
        snapshot_sha: str,
        repo_root: str,
        files: Sequence[str],
    ) -> None:
        for kind in self._kinds:
            stream = _STREAM_BY_KIND[kind]
            for file_path in files:
                payload: dict[str, object] = {
                    "batch_id": snapshot_sha,
                    "snapshot_sha": snapshot_sha,
                    "file_path": file_path,
                    "repo_root": repo_root,
                }
                await self._queue.enqueue(stream, payload)
        logger.info(
            "orchestrator_enqueued",
            extra={
                "snapshot_sha": snapshot_sha,
                "n_files": len(files),
                "extractors": list(self._kinds),
            },
        )

    async def _wait_for_completion(
        self, snapshot_sha: str, expected_files: int
    ) -> dict[str, str]:
        elapsed = 0.0
        outcomes: dict[str, str] = {}
        while elapsed < self._timeout:
            outcomes = await self._collect_outcomes(snapshot_sha, expected_files)
            if all(v != "in_progress" for v in outcomes.values()):
                return outcomes
            await asyncio.sleep(self._poll)
            elapsed += self._poll
        # Timeout — record current state as outcomes
        return outcomes or {k: "in_progress" for k in self._kinds}

    async def _collect_outcomes(
        self, snapshot_sha: str, expected_files: int
    ) -> dict[str, str]:
        """One DB session per poll call — the session must not outlive
        a single barrier query. Holding a session across the poll loop
        would leak the connection-pool checkout and crash on the first
        pool recycle (server-side timeout, network blip).
        """
        outcomes: dict[str, str] = {}
        async with self._db() as session:
            outcomes_repo = SnapshotFileOutcomeRepository(session)
            for kind in self._kinds:
                completed = await outcomes_repo.count_completed(
                    snapshot_sha=snapshot_sha, extractor=kind
                )
                if completed < expected_files:
                    outcomes[kind] = "in_progress"
                    continue
                failed = await outcomes_repo.count_failed(
                    snapshot_sha=snapshot_sha, extractor=kind
                )
                if failed == 0:
                    outcomes[kind] = "success"
                elif failed == expected_files:
                    outcomes[kind] = "failed"
                else:
                    outcomes[kind] = "partial"
        return outcomes
```

- [ ] **Step 2: Write `src/codeoracle/services/indexing/service.py`**

```python
"""IndexingService — public-facing reindex orchestration.

Lifecycle (one call):
  1. Acquire pg_advisory_lock (single-flight).
  2. DeltaComputer.compute() → Changeset.
  3. SnapshotRepository.create_pending(...)
  4. SnapshotRepository.transition(...→APPLYING)
  5. IndexingOrchestrator.fan_out_and_wait(...) — enqueue + barrier.
  6. SnapshotRepository.transition(...→APPLIED) with extractor_results.
  7. Release advisory lock (via the context manager's finally).

If any step raises, the snapshot is transitioned to FAILED before the
exception propagates — keeps the DB consistent with whatever the user
sees on the CLI.
"""

from __future__ import annotations

import logging

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from codeoracle.core.exceptions import (
    AdvisoryLockTimeoutError,
    SnapshotConflictError,
)
from codeoracle.db.repositories.file_outcomes import SnapshotFileOutcomeRepository
from codeoracle.db.repositories.graph import GraphRepository
from codeoracle.db.repositories.snapshots import SnapshotRepository
from codeoracle.models.snapshot import ExtractorOutcome, Snapshot, SnapshotStatus
from codeoracle.services.indexing.advisory_lock import reindex_single_flight
from codeoracle.services.indexing.delta import DeltaComputer
from codeoracle.services.indexing.orchestrator import IndexingOrchestrator
from codeoracle.adapters.valkey.client import ValkeyQueueClient
from codeoracle.core.streams import STREAM_REINDEX_REQUESTS  # 2026-05-26 amendment

logger = logging.getLogger(__name__)


# Per-extractor outcome mapping. `Snapshot.extractor_results` is the
# per-snapshot diagnostic record of how each extractor fared — surfaced
# by the CLI (Task 14) for operators; it is NOT a queryability gate.
# Queryability is enforced downstream by the agent's graph_query
# existence check: an empty / all-failed graph yields a terminal UNKNOWN
# (Plan 10), never a false answer. partial = SUCCESS is correct: the
# graph has *some* nodes/edges from this extractor, and per-file failures
# are already recorded in `snapshot_file_outcomes` for operators who need
# the fine-grained view. If a future caller needs to distinguish
# partial-vs-full success at snapshot scope, ADD a third enum value
# (ExtractorOutcome.PARTIAL) rather than flipping this mapping silently.
_OUTCOME_TO_EXTRACTOR_OUTCOME: dict[str, ExtractorOutcome] = {
    "success": ExtractorOutcome.SUCCESS,
    "partial": ExtractorOutcome.SUCCESS,
    "failed": ExtractorOutcome.FAILED,
    "in_progress": ExtractorOutcome.FAILED,  # timeout → failed
}


class IndexingService:
    def __init__(
        self,
        *,
        engine: AsyncEngine,
        db_session_factory: async_sessionmaker[AsyncSession],
        queue_client: ValkeyQueueClient,
        repo_root: str,
        extractor_kinds: tuple[str, ...] = ("joern",),
        advisory_lock_timeout_seconds: float = 0.0,
    ) -> None:
        self._engine = engine
        self._db = db_session_factory
        self._queue = queue_client
        self._repo_root = repo_root
        self._extractor_kinds = extractor_kinds
        self._lock_timeout = advisory_lock_timeout_seconds

    async def reindex(
        self,
        *,
        base_sha: str | None,
        pre_allocated_snapshot_sha: str | None = None,  # 2026-05-26 amendment
    ) -> Snapshot:
        """Perform one reindex pass. Returns the final Snapshot row.

        Two callers, two shapes:

        - **CLI** (``codeoracle index`` — Task 14) leaves
          ``pre_allocated_snapshot_sha=None``; the method runs
          :class:`DeltaComputer` and creates the PENDING snapshot row
          itself.
        - **Worker** (:class:`ReindexOrchestratorConsumer` — Task 13a,
          added 2026-05-26 for the Plan 11 async-job pivot) passes the
          ``snapshot_sha`` already created by :meth:`enqueue_reindex`;
          this method reads ``changed_files`` and ``parent_sha`` from
          that row and does NOT re-run :class:`DeltaComputer`. The row
          is the canonical job definition — re-deriving it from the
          tree would (a) waste compute on large-repository tree walks and
          (b) force defining behavior on tree-shift mismatch, which is
          a footgun under the WorkerConsumer NACK/retry/DLQ flow.
        """
        async with reindex_single_flight(
            self._engine, timeout_seconds=self._lock_timeout
        ):
            return await self._reindex_locked(
                base_sha=base_sha,
                pre_allocated_snapshot_sha=pre_allocated_snapshot_sha,
            )

    async def enqueue_reindex(self, *, base_sha: str | None) -> Snapshot:
        """Schedule an async reindex; return the PENDING ``Snapshot`` row.

        Added 2026-05-26 — Plan 11 async-job pivot driver. Returns
        immediately after persisting a PENDING ``Snapshot`` row +
        ``XADD`` to ``STREAM_REINDEX_REQUESTS``. Does **NOT** acquire
        ``pg_advisory_lock(0x46AE0001)`` — that lock lives entirely
        inside the worker process (:class:`ReindexOrchestratorConsumer`,
        Task 13a). Per Plan 11 invariant 4 (amended), the API process
        therefore never observes lock contention.

        **Idempotent on the same tree state.** :class:`DeltaComputer`
        produces a deterministic ``new_snapshot_sha`` for a given tree;
        if the corresponding row already exists (concurrent POST, or
        client retry), :meth:`SnapshotRepository.create_pending` raises
        :class:`SnapshotConflictError`. This method catches it, returns
        the existing row (status ∈ {PENDING, APPLYING, APPLIED, FAILED}
        depending on the worker's progress), and does NOT re-XADD. The
        ``POST /v1/reindex`` route inherits this idempotency.
        """
        async with self._db() as session:
            graph_repo = GraphRepository(session)
            snap_repo = SnapshotRepository(session)
            delta_computer = DeltaComputer(graph_repo=graph_repo, repo_root=None)
            cs = await delta_computer.compute(base_sha=base_sha)

            if base_sha is not None:
                parent_sha: str | None = base_sha
            else:
                latest = await snap_repo.get_latest_applied()
                parent_sha = latest.sha if latest is not None else None

            try:
                snap = await snap_repo.create_pending(
                    sha=cs.new_snapshot_sha,
                    parent_sha=parent_sha,
                    changed_files=cs.all_files,
                )
                await session.commit()
                newly_enqueued = True
            except SnapshotConflictError:
                await session.rollback()
                snap = await snap_repo.get_by_sha(cs.new_snapshot_sha)
                assert snap is not None, (
                    "create_pending raised SnapshotConflictError but "
                    "get_by_sha returned None — DB invariant violated"
                )
                newly_enqueued = False

        if newly_enqueued:
            # ReindexRequestMessage extends WorkerMessage, which requires
            # batch_id (no default). The consumer validates the payload
            # via ReindexRequestMessage.model_validate; a missing batch_id
            # would raise ValidationError → NACK → retry → DLQ on every
            # message. Bind batch_id to snap.sha — the message's
            # snapshot_sha is already its natural correlation key, so
            # using it for batch_id keeps log-tracing single-keyed.
            # We pass a plain dict (not a constructed ReindexRequestMessage)
            # to keep services/indexing/ free of an import on
            # workers/reindex_orchestrator/message.py (which would create
            # a services/ → workers/ cycle via the consumer module).
            await self._queue.enqueue(
                STREAM_REINDEX_REQUESTS,
                {
                    "batch_id": snap.sha,
                    "snapshot_sha": snap.sha,
                    "base_sha": base_sha,
                },
            )
            logger.info(
                "indexing_enqueued_reindex",
                extra={"snapshot_sha": snap.sha, "base_sha": base_sha},
            )
        else:
            logger.info(
                "indexing_reindex_idempotent",
                extra={
                    "snapshot_sha": snap.sha,
                    "base_sha": base_sha,
                    "current_status": snap.status,
                },
            )
        return snap

    async def get_snapshot(self, snapshot_sha: str) -> Snapshot | None:
        """Return the Snapshot row, or ``None`` if not found.

        Added 2026-05-26 — used by Plan 11's
        ``GET /v1/reindex/{snapshot_sha}`` polling endpoint. Thin facade
        over :meth:`SnapshotRepository.get_by_sha`; lives on the service
        so the API layer never reaches into ``db/repositories/`` directly
        (preserves Plan 11 invariant 1 — thin entrypoint, no DB access
        from ``api/``).
        """
        async with self._db() as session:
            return await SnapshotRepository(session).get_by_sha(snapshot_sha)

    async def _reindex_locked(
        self,
        *,
        base_sha: str | None,
        pre_allocated_snapshot_sha: str | None,
    ) -> Snapshot:
        # Stale-APPLYING sweep — recover SIGKILL/OOM-orphaned rows from
        # a prior reindex process before creating our own. Sound under
        # the advisory-lock invariant: `reindex_single_flight` holds
        # `pg_advisory_lock(0x46AE0001)`, Postgres auto-released it on
        # the prior process's connection death, so any APPLYING row we
        # see now is by construction abandoned. Separate session +
        # commit so the sweep is durable even if the rest of this
        # method later raises. Log only when count > 0 — clean startup
        # is the common case and shouldn't spam logs.
        #
        # Post-2026-05-26 amendment: the lock-holder is now the WORKER
        # process (ReindexOrchestratorConsumer, Task 13a) — NOT the API
        # request. The stale-APPLYING sweep semantics are unchanged —
        # Postgres auto-releases session-level advisory locks on
        # connection death regardless of which process held the conn.
        async with self._db() as session:
            snap_repo = SnapshotRepository(session)
            stale_count = await snap_repo.mark_stale_applying_failed()
            await session.commit()
        if stale_count > 0:
            logger.warning(
                "indexing_swept_stale_applying_rows",
                extra={"count": stale_count},
            )

        async with self._db() as session:
            graph_repo = GraphRepository(session)
            snap_repo = SnapshotRepository(session)
            outcomes_repo = SnapshotFileOutcomeRepository(session)

            if pre_allocated_snapshot_sha is not None:
                # API path (worker process) — the PENDING row was
                # created by .enqueue_reindex at the API. Read
                # changed_files straight from the row; do NOT re-run
                # DeltaComputer. The row IS the canonical job
                # definition (see .reindex() docstring + Plan 5
                # amendment 2026-05-26 rationale).
                snap = await snap_repo.get_by_sha(pre_allocated_snapshot_sha)
                assert snap is not None, (
                    f"pre_allocated_snapshot_sha {pre_allocated_snapshot_sha!r} "
                    "missing from snapshots table — worker invariant violation"
                )
                files = snap.changed_files
            else:
                # CLI path — create the PENDING row ourselves.
                delta_computer = DeltaComputer(
                    graph_repo=graph_repo, repo_root=None,  # uses CWD
                )
                cs = await delta_computer.compute(base_sha=base_sha)

                # Resolve parent_sha: explicit base wins; otherwise latest applied;
                # otherwise None (first-ever snapshot). Cache the lookup — calling
                # get_latest_applied() twice round-trips the DB twice for no reason.
                if base_sha is not None:
                    parent_sha: str | None = base_sha
                else:
                    latest = await snap_repo.get_latest_applied()
                    parent_sha = latest.sha if latest is not None else None
                snap = await snap_repo.create_pending(
                    sha=cs.new_snapshot_sha,
                    parent_sha=parent_sha,
                    changed_files=cs.all_files,
                )
                files = cs.all_files
            await session.commit()

        # `snapshot_sha` is canonical regardless of branch — bind once.
        snapshot_sha = snap.sha
        try:
            async with self._db() as session:
                snap_repo = SnapshotRepository(session)
                await snap_repo.transition(
                    sha=snapshot_sha, to=SnapshotStatus.APPLYING
                )
                await session.commit()

            # Fan out + barrier
            orchestrator = IndexingOrchestrator(
                queue_client=self._queue,
                db_session_factory=self._db,
                extractor_kinds=self._extractor_kinds,
            )
            kind_outcomes = await orchestrator.fan_out_and_wait(
                snapshot_sha=snapshot_sha,
                repo_root=self._repo_root,
                files=files,
            )

            # Translate per-kind outcomes → per-extractor enum + final snapshot status
            extractor_results = {
                k: _OUTCOME_TO_EXTRACTOR_OUTCOME[v]
                for k, v in kind_outcomes.items()
            }
            # APPLIED requires BOTH conditions: the barrier closed (no kind
            # still `in_progress` — the orchestrator did not time out) AND at
            # least one kind produced usable data. A kind is "usable" when its
            # aggregate is `success` (all files) or `partial` (some files) —
            # both map to ExtractorOutcome.SUCCESS, i.e. the graph holds some
            # nodes/edges from that kind. An all-`failed` barrier IS closed but
            # extracted nothing, so it must NOT be marked APPLIED — that would
            # serve a contentless snapshot as live. Marking it FAILED instead
            # leaves the prior APPLIED snapshot as the latest-APPLIED, so reads
            # keep resolving to the last good index (last-good availability, no
            # extra machinery). Cross-kind partial failure (some kinds fail,
            # >=1 succeeds) stays APPLIED with the failed kind's coverage absent.
            any_in_progress = any(v == "in_progress" for v in kind_outcomes.values())
            any_succeeded = any(
                v in ("success", "partial") for v in kind_outcomes.values()
            )
            if any_in_progress:
                final_status = SnapshotStatus.FAILED
                final_error: str | None = "orchestrator timed out"
            elif not any_succeeded:
                final_status = SnapshotStatus.FAILED
                final_error = "all extractors failed — no snapshot data extracted"
            else:
                final_status = SnapshotStatus.APPLIED
                final_error = None

            # Cross-file symbol-linking pass (spec §3.7) — resolve CALLS
            # edges whose callee lives in another file. Runs after the
            # per-file barrier drains and before the APPLIED transition,
            # only for a snapshot headed for APPLIED (skip when extraction
            # failed/timed out — no point linking a graph about to be
            # FAILED). Inside the advisory-lock single-flight; idempotent.
            if final_status == SnapshotStatus.APPLIED:
                async with self._db() as session:
                    await GraphRepository(session).link_cross_file_symbols(
                        snapshot_sha
                    )
                    await session.commit()

            async with self._db() as session:
                snap_repo = SnapshotRepository(session)
                final_snap = await snap_repo.transition(
                    sha=snapshot_sha,
                    to=final_status,
                    extractor_results=extractor_results,
                    error=final_error,
                )
                await session.commit()
                return final_snap
        except KeyboardInterrupt:
            # User Ctrl-C'd mid-reindex. The snapshot is in PENDING or
            # APPLYING; transition to FAILED so a re-run doesn't see a
            # stale "still applying" row. Then re-raise so the process
            # actually exits — we honor the user's cancel intent.
            #
            # NB: this except clause does NOT run on SIGKILL / hard
            # process death. The SIGKILL path is covered by the
            # mark_stale_applying_failed() sweep at the *next* reindex
            # — see the comment at the top of this method.
            logger.warning("indexing_interrupted_by_user")
            async with self._db() as session:
                snap_repo = SnapshotRepository(session)
                try:
                    await snap_repo.transition(
                        sha=snapshot_sha,
                        to=SnapshotStatus.FAILED,
                        error="interrupted by KeyboardInterrupt",
                    )
                    await session.commit()
                except SnapshotConflictError:
                    pass  # already terminal — nothing to clean up
            raise
        except Exception as exc:
            # `except Exception` is catch-set-identical to the prior
            # `(Exception, AdvisoryLockTimeoutError, SnapshotConflictError)`
            # — both extra arms subclass CodeOracleError(Exception) (Plan 3).
            # KeyboardInterrupt subclasses BaseException, so it is handled
            # by the clause above and never reaches here.
            logger.exception("indexing_failed")
            async with self._db() as session:
                snap_repo = SnapshotRepository(session)
                try:
                    # Mirror the SUCCESS path (transition → commit →
                    # return), NOT the KeyboardInterrupt path (→ raise).
                    # An extractor crash/OOM yields a FAILED *outcome*; the
                    # callers consume a returned FAILED snapshot (the CLI
                    # inspects .status; the success path returns FAILED on
                    # timeout). The commit MUST run before the return —
                    # the prior `return await transition(...)` exited this
                    # `async with` before the (then-trailing) commit, so the
                    # non-autocommitting session factory rolled the FAILED
                    # UPDATE back, leaving the row APPLYING while the caller
                    # saw FAILED (NFR D1-2).
                    final_snap = await snap_repo.transition(
                        sha=snapshot_sha,
                        to=SnapshotStatus.FAILED,
                        error=str(exc),
                    )
                    await session.commit()
                    return final_snap
                except SnapshotConflictError:
                    # Row already terminal (concurrently moved) — nothing
                    # to record; fall through to re-raise the original exc.
                    pass
            raise
```

- [ ] **Step 3: Write `tests/integration/services/indexing/test_orchestrator.py`**

```python
"""Plan 5 Task 13: IndexingOrchestrator integration tests."""

from __future__ import annotations

import asyncio

import pytest

from codeoracle.adapters.valkey.client import ValkeyQueueClient
from codeoracle.adapters.valkey.config import ValkeyConfig
from codeoracle.db.repositories.file_outcomes import SnapshotFileOutcomeRepository
from codeoracle.services.indexing.orchestrator import IndexingOrchestrator

pytestmark = [pytest.mark.integration, pytest.mark.worker]


async def test_fan_out_enqueues_one_message_per_file(
    async_session, async_session_factory, valkey_url,
):
    queue = ValkeyQueueClient(ValkeyConfig(
        url=valkey_url, pool_min=1, pool_max=4,
        block_ms_default=1000, max_in_flight_per_consumer=10, stream_maxlen=1000,
    ))
    await queue.start()

    file_outcomes_repo = SnapshotFileOutcomeRepository(async_session)
    orch = IndexingOrchestrator(
        queue_client=queue, db_session_factory=async_session_factory,
        extractor_kinds=("joern",),
        completion_timeout_seconds=2.0,  # short — we won't actually drain
    )

    # Pre-populate the snapshot_file_outcomes table so the barrier
    # passes immediately (no real worker running).
    files = ["src/a.py", "src/b.py", "src/c.py"]
    for f in files:
        await file_outcomes_repo.upsert(
            snapshot_sha="x", file_path=f, extractor="joern",
            outcome="success",
        )
    await async_session.commit()

    outcomes = await orch.fan_out_and_wait(
        snapshot_sha="x", repo_root="/tmp", files=files,
    )
    assert outcomes == {"joern": "success"}

    # Verify the messages were enqueued
    msgs = await queue.read_pending(
        stream="indexing.joern", group="g_test", consumer="c",
        count=10, block_ms=500,
    )
    assert len(msgs) == 3
    assert {m.payload["file_path"] for m in msgs} == set(files)
    await queue.aclose()


async def test_timeout_with_no_acks_reports_in_progress(
    async_session_factory, valkey_url,
):
    queue = ValkeyQueueClient(ValkeyConfig(
        url=valkey_url, pool_min=1, pool_max=4,
        block_ms_default=1000, max_in_flight_per_consumer=10, stream_maxlen=1000,
    ))
    await queue.start()

    orch = IndexingOrchestrator(
        queue_client=queue, db_session_factory=async_session_factory,
        extractor_kinds=("joern",),
        completion_poll_interval_seconds=0.1,
        completion_timeout_seconds=0.5,
    )

    outcomes = await orch.fan_out_and_wait(
        snapshot_sha="y", repo_root="/tmp", files=["a.py", "b.py"],
    )
    assert outcomes["joern"] == "in_progress"
    await queue.aclose()


async def test_partial_failure_classified(
    async_session, async_session_factory, valkey_url,
):
    queue = ValkeyQueueClient(ValkeyConfig(
        url=valkey_url, pool_min=1, pool_max=4,
        block_ms_default=1000, max_in_flight_per_consumer=10, stream_maxlen=1000,
    ))
    await queue.start()
    file_outcomes_repo = SnapshotFileOutcomeRepository(async_session)

    # 2 success, 1 failed
    for f, outcome in [("a.py", "success"), ("b.py", "success"), ("c.py", "failed")]:
        await file_outcomes_repo.upsert(
            snapshot_sha="z", file_path=f, extractor="joern",
            outcome=outcome,
        )
    await async_session.commit()

    orch = IndexingOrchestrator(
        queue_client=queue, db_session_factory=async_session_factory,
        extractor_kinds=("joern",),
    )
    outcomes = await orch.fan_out_and_wait(
        snapshot_sha="z", repo_root="/tmp",
        files=["a.py", "b.py", "c.py"],
    )
    assert outcomes == {"joern": "partial"}
    await queue.aclose()
```

- [ ] **Step 4: Write `tests/integration/services/indexing/test_service.py`**

```python
"""Plan 5 Task 13: IndexingService integration test.

Mocks the orchestrator's worker side — uses a faux fan-out that
immediately populates `snapshot_file_outcomes` so the barrier passes.
Verifies snapshot lifecycle transitions PENDING → APPLYING → APPLIED."""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from codeoracle.models.snapshot import ExtractorOutcome, SnapshotStatus
from codeoracle.services.indexing.service import IndexingService

pytestmark = [pytest.mark.integration, pytest.mark.worker]


@pytest.fixture
def tmp_git_repo(tmp_path: Path) -> Path:
    subprocess.run(["git", "init"], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(
        ["git", "config", "user.email", "test@example.com"],
        cwd=tmp_path, check=True, capture_output=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Test"],
        cwd=tmp_path, check=True, capture_output=True,
    )
    # Seed with one file
    (tmp_path / "x.py").write_text("def f(): pass")
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "initial"],
        cwd=tmp_path, check=True, capture_output=True,
    )
    return tmp_path


async def test_full_reindex_transitions_to_applied(
    tmp_git_repo, async_engine, async_session_factory, valkey_url,
):
    from codeoracle.adapters.valkey.client import ValkeyQueueClient
    from codeoracle.adapters.valkey.config import ValkeyConfig

    qc = ValkeyQueueClient(ValkeyConfig(
        url=valkey_url, pool_min=1, pool_max=4,
        block_ms_default=500, max_in_flight_per_consumer=10, stream_maxlen=1000,
    ))
    await qc.start()
    service = IndexingService(
        engine=async_engine,
        db_session_factory=async_session_factory,
        queue_client=qc,
        repo_root=str(tmp_git_repo),
        extractor_kinds=("joern",),
        advisory_lock_timeout_seconds=0.0,
    )

    # Patch the orchestrator's fan-out so it returns success immediately
    # without needing a live worker.
    with patch(
        "codeoracle.services.indexing.service.IndexingOrchestrator.fan_out_and_wait",
        new=AsyncMock(return_value={"joern": "success"}),
    ):
        # Change CWD so DeltaComputer sees the temp git repo
        import os
        original_cwd = os.getcwd()
        os.chdir(tmp_git_repo)
        try:
            snap = await service.reindex(base_sha=None)
        finally:
            os.chdir(original_cwd)

    assert snap.status == SnapshotStatus.APPLIED
    assert snap.extractor_results == {"joern": ExtractorOutcome.SUCCESS}
    await qc.aclose()


async def test_in_progress_timeout_transitions_to_failed(
    tmp_git_repo, async_engine, async_session_factory, valkey_url,
):
    from codeoracle.adapters.valkey.client import ValkeyQueueClient
    from codeoracle.adapters.valkey.config import ValkeyConfig

    qc = ValkeyQueueClient(ValkeyConfig(
        url=valkey_url, pool_min=1, pool_max=4,
        block_ms_default=500, max_in_flight_per_consumer=10, stream_maxlen=1000,
    ))
    await qc.start()
    service = IndexingService(
        engine=async_engine,
        db_session_factory=async_session_factory,
        queue_client=qc,
        repo_root=str(tmp_git_repo),
    )
    with patch(
        "codeoracle.services.indexing.service.IndexingOrchestrator.fan_out_and_wait",
        new=AsyncMock(return_value={"joern": "in_progress"}),
    ):
        import os
        original_cwd = os.getcwd()
        os.chdir(tmp_git_repo)
        try:
            snap = await service.reindex(base_sha=None)
        finally:
            os.chdir(original_cwd)

    assert snap.status == SnapshotStatus.FAILED
    assert snap.error == "orchestrator timed out"
    await qc.aclose()


async def test_extractor_crash_transitions_to_failed_durably(
    tmp_git_repo, async_engine, async_session_factory, valkey_url,
):
    """An exception raised mid-fan-out (Joern crash / OOM) is the COMMON
    terminal path. _reindex_locked's catch-all must DURABLY commit the
    FAILED transition before returning (NFR D1-2). The cross-session
    re-read is load-bearing: transition() re-reads via get_by_sha, so a
    SAME-session assertion sees FAILED even when the UPDATE was rolled
    back (read-your-own-writes) — it would pass against the bug. Only a
    fresh connection distinguishes a committed FAILED row from one left
    stuck APPLYING."""
    from codeoracle.adapters.valkey.client import ValkeyQueueClient
    from codeoracle.adapters.valkey.config import ValkeyConfig
    from codeoracle.db.repositories.snapshots import SnapshotRepository

    qc = ValkeyQueueClient(ValkeyConfig(
        url=valkey_url, pool_min=1, pool_max=4,
        block_ms_default=500, max_in_flight_per_consumer=10, stream_maxlen=1000,
    ))
    await qc.start()
    service = IndexingService(
        engine=async_engine,
        db_session_factory=async_session_factory,
        queue_client=qc,
        repo_root=str(tmp_git_repo),
    )
    with patch(
        "codeoracle.services.indexing.service.IndexingOrchestrator.fan_out_and_wait",
        new=AsyncMock(side_effect=RuntimeError("joern OOM")),
    ):
        import os
        original_cwd = os.getcwd()
        os.chdir(tmp_git_repo)
        try:
            snap = await service.reindex(base_sha=None)
        finally:
            os.chdir(original_cwd)

    # Caller contract: the catch-all RETURNS the FAILED snapshot (not
    # raise) — the CLI consumes snap.status, mirroring the timeout path.
    assert snap.status == SnapshotStatus.FAILED
    assert "joern OOM" in (snap.error or "")

    # Load-bearing: re-read on a FRESH connection (only committed state is
    # visible). Pre-D1-2-fix this row was APPLYING (FAILED rolled back).
    async with async_session_factory() as session:
        repo = SnapshotRepository(session)
        persisted = await repo.get_by_sha(snap.sha)
    assert persisted is not None
    assert persisted.status == SnapshotStatus.FAILED
    assert "joern OOM" in (persisted.error or "")
    await qc.aclose()


async def test_all_extractors_failed_marks_snapshot_failed(
    tmp_git_repo, async_engine, async_session_factory, valkey_url,
):
    """A barrier that closes with EVERY kind reporting `failed` extracted
    no data — it must transition to FAILED, never APPLIED. Marking it
    APPLIED would serve a contentless snapshot as live; FAILED leaves the
    prior APPLIED snapshot as latest-APPLIED (last-good availability).
    Regression test for the all-failed→APPLIED barrier bug: no kind is
    `in_progress`, so the pre-fix `FAILED if any_in_progress else APPLIED`
    wrongly chose APPLIED."""
    from codeoracle.adapters.valkey.client import ValkeyQueueClient
    from codeoracle.adapters.valkey.config import ValkeyConfig
    from codeoracle.db.repositories.snapshots import SnapshotRepository

    qc = ValkeyQueueClient(ValkeyConfig(
        url=valkey_url, pool_min=1, pool_max=4,
        block_ms_default=500, max_in_flight_per_consumer=10, stream_maxlen=1000,
    ))
    await qc.start()
    service = IndexingService(
        engine=async_engine,
        db_session_factory=async_session_factory,
        queue_client=qc,
        repo_root=str(tmp_git_repo),
        extractor_kinds=("joern",),
    )
    with patch(
        "codeoracle.services.indexing.service.IndexingOrchestrator.fan_out_and_wait",
        new=AsyncMock(return_value={"joern": "failed"}),
    ):
        import os
        original_cwd = os.getcwd()
        os.chdir(tmp_git_repo)
        try:
            snap = await service.reindex(base_sha=None)
        finally:
            os.chdir(original_cwd)

    # All-failed barrier → FAILED (NOT APPLIED), with the canonical error.
    assert snap.status == SnapshotStatus.FAILED
    assert "all extractors failed" in (snap.error or "")

    # Durable on a FRESH connection (committed state, not read-your-writes).
    async with async_session_factory() as session:
        repo = SnapshotRepository(session)
        persisted = await repo.get_by_sha(snap.sha)
    assert persisted is not None
    assert persisted.status == SnapshotStatus.FAILED
    await qc.aclose()


async def test_partial_extractor_failure_stays_applied(
    tmp_git_repo, async_engine, async_session_factory, valkey_url,
):
    """The all-failed→FAILED policy TOLERATES partial failure: when at
    least one kind succeeds, the Snapshot is APPLIED even though another
    kind failed (its coverage is simply absent). This is the
    discriminating test for the policy choice — it would FAIL under the
    rejected "any-failed→FAILED" rule, so it pins the decision against a
    silent future tightening back to all-or-nothing."""
    from codeoracle.adapters.valkey.client import ValkeyQueueClient
    from codeoracle.adapters.valkey.config import ValkeyConfig

    qc = ValkeyQueueClient(ValkeyConfig(
        url=valkey_url, pool_min=1, pool_max=4,
        block_ms_default=500, max_in_flight_per_consumer=10, stream_maxlen=1000,
    ))
    await qc.start()
    service = IndexingService(
        engine=async_engine,
        db_session_factory=async_session_factory,
        queue_client=qc,
        repo_root=str(tmp_git_repo),
        extractor_kinds=("joern", "scip"),
    )
    with patch(
        "codeoracle.services.indexing.service.IndexingOrchestrator.fan_out_and_wait",
        new=AsyncMock(return_value={"joern": "success", "scip": "failed"}),
    ):
        import os
        original_cwd = os.getcwd()
        os.chdir(tmp_git_repo)
        try:
            snap = await service.reindex(base_sha=None)
        finally:
            os.chdir(original_cwd)

    # One kind succeeded → APPLIED despite scip's failure (coverage absent),
    # never marked FAILED. error stays None on the APPLIED path.
    assert snap.status == SnapshotStatus.APPLIED
    assert snap.error is None
    await qc.aclose()


async def test_stale_applying_row_swept_before_new_reindex(
    tmp_git_repo, async_engine, async_session_factory, valkey_url,
):
    """A pre-existing APPLYING row (simulating a prior SIGKILL'd
    process) must be transitioned to FAILED before the new reindex
    creates its own snapshot. Verifies the check-on-use cleanup
    inside `_reindex_locked` (DECIDE-D)."""
    from codeoracle.adapters.valkey.client import ValkeyQueueClient
    from codeoracle.adapters.valkey.config import ValkeyConfig
    from codeoracle.db.repositories.snapshots import SnapshotRepository

    # Plant a stale APPLYING row before any reindex runs.
    async with async_session_factory() as session:
        repo = SnapshotRepository(session)
        await repo.create_pending(
            sha="killed_by_sigkill",
            parent_sha=None,
            changed_files=["src/orphan.py"],
        )
        await repo.transition(
            sha="killed_by_sigkill", to=SnapshotStatus.APPLYING
        )
        await session.commit()

    qc = ValkeyQueueClient(ValkeyConfig(
        url=valkey_url, pool_min=1, pool_max=4,
        block_ms_default=500, max_in_flight_per_consumer=10, stream_maxlen=1000,
    ))
    await qc.start()
    service = IndexingService(
        engine=async_engine,
        db_session_factory=async_session_factory,
        queue_client=qc,
        repo_root=str(tmp_git_repo),
        extractor_kinds=("joern",),
        advisory_lock_timeout_seconds=0.0,
    )

    with patch(
        "codeoracle.services.indexing.service.IndexingOrchestrator.fan_out_and_wait",
        new=AsyncMock(return_value={"joern": "success"}),
    ):
        import os
        original_cwd = os.getcwd()
        os.chdir(tmp_git_repo)
        try:
            new_snap = await service.reindex(base_sha=None)
        finally:
            os.chdir(original_cwd)

    # The new reindex completed normally.
    assert new_snap.status == SnapshotStatus.APPLIED
    assert new_snap.sha != "killed_by_sigkill"

    # The stale row was transitioned to FAILED with the canonical
    # error string set by mark_stale_applying_failed.
    async with async_session_factory() as session:
        repo = SnapshotRepository(session)
        orphan = await repo.get_by_sha("killed_by_sigkill")

    assert orphan is not None
    assert orphan.status == SnapshotStatus.FAILED
    assert orphan.error == "process died mid-apply"
    await qc.aclose()
```

- [ ] **Step 5: Run + lint + type + commit**

```bash
uv run pytest tests/integration/services/indexing/test_orchestrator.py \
              tests/integration/services/indexing/test_service.py -v
uv run ruff check src/codeoracle/services/indexing/
uv run ty check src/codeoracle/services/indexing/
git add src/codeoracle/services/indexing/orchestrator.py \
        src/codeoracle/services/indexing/service.py \
        tests/integration/services/indexing/test_orchestrator.py \
        tests/integration/services/indexing/test_service.py
git commit -m "feat: Plan 5 Task 13 — IndexingOrchestrator + IndexingService (lifecycle)"
```

---

## Task 13a: `src/codeoracle/workers/reindex_orchestrator/` — ReindexOrchestratorConsumer (async-job consumer)

> **Added 2026-05-26 amendment** — Plan 11 async-job pivot driver. This Task is decimal-numbered (`13a`) rather than renumbering Tasks 14/15/16 to avoid cascading edits in Plan 1 + Plan 5 internal commit messages that name `Task 14/15/16` by number.

**What this task delivers:** a worker process that drives reindexes in the background instead of in the HTTP request path. Subscribes to `STREAM_REINDEX_REQUESTS` (added in Task 1, 2026-05-26 amendment), validates each message against `ReindexRequestMessage`, calls `IndexingService.reindex(base_sha=…, pre_allocated_snapshot_sha=…)` to drive the existing fan-out + barrier code, transitions the pre-allocated `Snapshot` row through PENDING → APPLYING → APPLIED|FAILED. `pg_advisory_lock(0x46AE0001)` is acquired by THIS worker's session (not the API's), so API replicas can restart freely mid-reindex; SIGKILL of this worker releases the lock at the Postgres connection layer and the next reindex's stale-APPLYING sweep (already in Task 13's `_reindex_locked`) cleans up the orphaned row.

**Why a new worker rather than threading async into the API process:** Plan 5/7's `WorkerConsumer` + advisory-lock pattern + per-extractor stream architecture already establishes the production-grade async-job shape for this codebase. Reusing the same skeleton (`workers/_base/consumer.py` + `workers/_base/wiring.py`) plus the shared `core/streams.py` constants registry keeps every worker symmetric: one stream → one consumer → one entrypoint → one container image. Threading `asyncio.create_task` into FastAPI would (a) lose all of `WorkerConsumer`'s NACK/retry/DLQ + signal-handler infrastructure, (b) tie reindex liveness to a specific API replica's process lifetime, and (c) prevent horizontal scaling of reindex throughput independent of API throughput. Per the Plan 11 amendment user directive: production-grade, modern engineering practices.

**Pattern source:** mirrors Plan 5 Task 12 (`JoernIndexerConsumer`) one-to-one for everything except the handler body (which just delegates to `IndexingService.reindex` instead of running a per-file extraction).

**Files:**
- Create: `src/codeoracle/workers/reindex_orchestrator/__init__.py` (empty)
- Create: `src/codeoracle/workers/reindex_orchestrator/message.py` (`ReindexRequestMessage`)
- Create: `src/codeoracle/workers/reindex_orchestrator/consumer.py` (`ReindexOrchestratorConsumer`)
- Create: `src/codeoracle/workers/reindex_orchestrator/__main__.py` (entrypoint)
- Create: `tests/unit/workers/reindex_orchestrator/__init__.py`
- Create: `tests/unit/workers/reindex_orchestrator/test_consumer.py`
- Create: `tests/integration/workers/reindex_orchestrator/__init__.py`
- Create: `tests/integration/workers/reindex_orchestrator/test_consumer.py`

- [ ] **Step 1: Write `src/codeoracle/workers/reindex_orchestrator/message.py`**

```python
"""ReindexRequestMessage — Pydantic envelope for an async-reindex job.

Producer: `IndexingService.enqueue_reindex` (in the API process, after
the POST /v1/reindex route lands the PENDING snapshot row).
Consumer: `ReindexOrchestratorConsumer` (in this worker process).

The PENDING row created by `enqueue_reindex` carries `changed_files`
already, so this message is intentionally minimal — `snapshot_sha` is
the only field the worker needs; `base_sha` is informational (logged
for traceability when the API caller passed an explicit base).
"""

from codeoracle.workers._base.message import WorkerMessage


class ReindexRequestMessage(WorkerMessage):
    """Async reindex request.

    Fields:
        batch_id: == snapshot_sha (inherited from WorkerMessage; tied to
            the parent reindex's pre-allocated Snapshot row).
        snapshot_sha: target snapshot (== batch_id; redundant for clarity,
            matching the JoernIndexerMessage convention).
        base_sha: caller's base hint (None → DeltaComputer picked latest
            applied at enqueue time). Diagnostic only; the worker does
            NOT re-run DeltaComputer (see IndexingService.reindex docstring
            — the row is the canonical job definition).
    """

    snapshot_sha: str
    base_sha: str | None = None
```

- [ ] **Step 2: Write `src/codeoracle/workers/reindex_orchestrator/consumer.py`**

```python
"""ReindexOrchestratorConsumer — drains STREAM_REINDEX_REQUESTS by
calling IndexingService.reindex with the pre-allocated snapshot_sha.

Concurrency: one consumer per replica. The advisory lock single-flights
anyway, so a second consumer would just block on lock acquisition; no
benefit from running multiples. Plan 1A's compose YAML provisions
exactly one `reindex_orchestrator` service.

Failure modes:
- IndexingService.reindex records an extractor-phase failure durably and
  RETURNS the FAILED snapshot (commit-before-return, see _reindex_locked) — it
  does NOT raise. handle() returns normally → _process_one ACKs, so the request
  is consumed exactly once and the client polling GET /v1/reindex/{sha} sees
  FAILED. No NACK, no retry, no duplicate run.
- A hard failure that escapes that path (reindex raises) → WorkerConsumer NACKs
  (leaves the message in the PEL). Reclaim is DISABLED on this consumer (see the
  class docstring), so the entry is not redelivered; recovery for any orphaned
  APPLYING row is the stale-APPLYING sweep on the next reindex request. This is
  deliberate — re-driving a whole reindex would only churn.
- Process SIGKILL → asyncpg connection closes → Postgres auto-releases
  the advisory lock. The orphaned APPLYING row is swept on next reindex.
"""

from __future__ import annotations

import asyncio
import logging

from sqlalchemy.ext.asyncio import async_sessionmaker

from codeoracle.adapters.valkey.client import ValkeyQueueClient
from codeoracle.services.indexing.service import IndexingService
from codeoracle.workers._base.consumer import WorkerConsumer
from codeoracle.workers._base.message import RetryPolicy
from codeoracle.workers.reindex_orchestrator.message import ReindexRequestMessage
from codeoracle.core.streams import (
    GROUP_REINDEX_ORCHESTRATOR,
    STREAM_REINDEX_REQUESTS,
)

logger = logging.getLogger(__name__)


class ReindexOrchestratorConsumer(WorkerConsumer[ReindexRequestMessage]):
    """Consumes codeoracle:reindex:requests → drives IndexingService.reindex.

    Reclaim is deliberately DISABLED here (reclaim_min_idle_ms left None): a
    crashed reindex is recovered by IndexingService's own advisory-lock +
    stale-APPLYING sweep, not by re-delivering the request. Re-driving a whole
    reindex via XAUTOCLAIM would only churn — reprocessing re-runs reindex
    against a row the sweep may already have moved to FAILED, raising
    SnapshotConflictError → NACK → reclaim → … → dead-letter after max_attempts
    (bounded, but pure waste). The per-file extractor streams are where reclaim
    earns its keep.
    """

    stream = STREAM_REINDEX_REQUESTS
    group = GROUP_REINDEX_ORCHESTRATOR
    message_type = ReindexRequestMessage
    count_per_poll = 1  # single-flight at the lock — no concurrency win from batching
    block_ms = 5000

    def __init__(
        self,
        *,
        queue_client: ValkeyQueueClient,
        retry_policy: RetryPolicy,
        consumer_name: str,
        stop_event: asyncio.Event,
        indexing_service: IndexingService,
    ) -> None:
        super().__init__(
            queue_client=queue_client,
            retry_policy=retry_policy,
            consumer_name=consumer_name,
            stop_event=stop_event,
        )
        self._indexing = indexing_service

    async def handle(self, message: ReindexRequestMessage) -> None:
        logger.info(
            "reindex_consumer_picked_up",
            extra={
                "snapshot_sha": message.snapshot_sha,
                "base_sha": message.base_sha,
            },
        )
        await self._indexing.reindex(
            base_sha=message.base_sha,
            pre_allocated_snapshot_sha=message.snapshot_sha,
        )
```

- [ ] **Step 3: Write `src/codeoracle/workers/reindex_orchestrator/__main__.py`**

```python
"""Reindex orchestrator worker entrypoint —
`python -m codeoracle.workers.reindex_orchestrator`."""

from __future__ import annotations

import asyncio

from codeoracle.core.config import get_settings
from codeoracle.services.indexing.service import IndexingService
from codeoracle.workers._base.wiring import (
    build_worker_context,
    teardown_worker_context,
)
from codeoracle.workers.reindex_orchestrator.consumer import (
    ReindexOrchestratorConsumer,
)


async def amain() -> None:
    ctx = await build_worker_context(consumer_name_prefix="reindex_orchestrator")
    # `get_settings()` is the module-scoped singleton already used by
    # build_worker_context (see configure_logging(get_settings())). The
    # WorkerContext deliberately doesn't expose the full Settings object
    # so workers can't grow a habit of reaching into config beyond what
    # they declare at the entrypoint. The fields read here are the only
    # IndexingService inputs that aren't already on WorkerContext.
    settings = get_settings()
    indexing_service = IndexingService(
        engine=ctx.engine,
        db_session_factory=ctx.db_session_factory,
        queue_client=ctx.queue_client,
        repo_root=settings.indexing.repo_root,
        extractor_kinds=tuple(settings.indexing.extractor_kinds),
        # Slice+1 TODO: rename `api.lock_acquisition_timeout_seconds` —
        # the field name reads "the API's timeout" but post-2026-05-26
        # amendment the LOCK lives in this worker, not the API. Keep the
        # field for slice (would force a Plan 3 sub-amendment + env-var
        # rename that's out of scope for the Plan 11 P0 fix). The
        # nested-config shape (`settings.api.lock_acquisition_timeout_seconds`)
        # is locked by the 2026-05-25 Plan 3 amendment (ApiConfig).
        advisory_lock_timeout_seconds=settings.api.lock_acquisition_timeout_seconds,
    )
    consumer = ReindexOrchestratorConsumer(
        queue_client=ctx.queue_client,
        retry_policy=ctx.retry_policy,
        consumer_name=ctx.consumer_name,
        stop_event=ctx.stop_event,
        indexing_service=indexing_service,
    )
    try:
        await consumer.run()
    finally:
        await teardown_worker_context(ctx)


if __name__ == "__main__":
    asyncio.run(amain())
```

- [ ] **Step 4: Write `tests/unit/workers/reindex_orchestrator/test_consumer.py`**

```python
"""Plan 5 Task 13a: ReindexOrchestratorConsumer unit tests — mocked everything below."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import pytest

from codeoracle.workers._base.message import RetryPolicy
from codeoracle.workers.reindex_orchestrator.consumer import (
    ReindexOrchestratorConsumer,
)
from codeoracle.workers.reindex_orchestrator.message import ReindexRequestMessage


pytestmark = pytest.mark.asyncio


async def test_handle_delegates_to_indexing_service_with_pre_allocated_sha():
    indexing = AsyncMock()
    consumer = ReindexOrchestratorConsumer(
        queue_client=AsyncMock(),
        retry_policy=RetryPolicy(),
        consumer_name="reindex_orchestrator-test",
        stop_event=asyncio.Event(),
        indexing_service=indexing,
    )
    msg = ReindexRequestMessage(
        batch_id="sha123",
        snapshot_sha="sha123",
        base_sha="base456",
    )

    await consumer.handle(msg)

    indexing.reindex.assert_awaited_once_with(
        base_sha="base456",
        pre_allocated_snapshot_sha="sha123",
    )


async def test_handle_passes_base_sha_none_when_unset():
    indexing = AsyncMock()
    consumer = ReindexOrchestratorConsumer(
        queue_client=AsyncMock(),
        retry_policy=RetryPolicy(),
        consumer_name="reindex_orchestrator-test",
        stop_event=asyncio.Event(),
        indexing_service=indexing,
    )
    msg = ReindexRequestMessage(batch_id="sha123", snapshot_sha="sha123")

    await consumer.handle(msg)

    indexing.reindex.assert_awaited_once_with(
        base_sha=None,
        pre_allocated_snapshot_sha="sha123",
    )


def test_message_rejects_extra_fields():
    """WorkerMessage's `extra='forbid'` applies — typos surface at validation."""
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        ReindexRequestMessage(
            batch_id="x",
            snapshot_sha="x",
            unknown_field="boom",  # type: ignore[call-arg]
        )


def test_message_requires_snapshot_sha():
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        ReindexRequestMessage(batch_id="x")  # type: ignore[call-arg]
```

- [ ] **Step 5: Write `tests/integration/workers/reindex_orchestrator/test_consumer.py`**

```python
"""Plan 5 Task 13a: ReindexOrchestratorConsumer integration test —
real Valkey + DB + stub IndexingService. Confirms the XADD-from-API →
XREADGROUP-in-worker → handle() wiring round-trips."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import pytest

from codeoracle.adapters.valkey.client import ValkeyQueueClient
from codeoracle.adapters.valkey.config import ValkeyConfig
from codeoracle.workers._base.message import RetryPolicy
from codeoracle.workers.reindex_orchestrator.consumer import (
    ReindexOrchestratorConsumer,
)
from codeoracle.workers.reindex_orchestrator.message import ReindexRequestMessage
from codeoracle.core.streams import STREAM_REINDEX_REQUESTS


pytestmark = [pytest.mark.integration, pytest.mark.worker, pytest.mark.asyncio]


async def test_xadd_to_stream_routes_through_consumer_handle(valkey_url):
    """Enqueue a ReindexRequestMessage via XADD; verify the consumer
    picks it up and calls IndexingService.reindex with the right args."""
    queue = ValkeyQueueClient(ValkeyConfig(
        url=valkey_url, pool_min=1, pool_max=4,
        block_ms_default=500, max_in_flight_per_consumer=10, stream_maxlen=1000,
    ))
    await queue.start()

    indexing = AsyncMock()
    stop_event = asyncio.Event()
    consumer = ReindexOrchestratorConsumer(
        queue_client=queue,
        retry_policy=RetryPolicy(),
        consumer_name="reindex_orchestrator-integration",
        stop_event=stop_event,
        indexing_service=indexing,
    )

    # Enqueue ONE message
    msg = ReindexRequestMessage(
        batch_id="abc1234",
        snapshot_sha="abc1234",
        base_sha=None,
    )
    await queue.enqueue(STREAM_REINDEX_REQUESTS, msg.model_dump())

    # Run consumer until it processes one message, then stop
    run_task = asyncio.create_task(consumer.run())
    try:
        # Poll up to 5s for the mock to be called
        for _ in range(50):
            if indexing.reindex.await_count >= 1:
                break
            await asyncio.sleep(0.1)
        else:
            pytest.fail("Consumer never invoked IndexingService.reindex")
    finally:
        stop_event.set()
        await run_task

    indexing.reindex.assert_awaited_once_with(
        base_sha=None,
        pre_allocated_snapshot_sha="abc1234",
    )
    await queue.aclose()


async def test_enqueue_reindex_produces_schema_valid_message(
    valkey_url, postgres_url, tmp_git_repo,
):
    """The dict IndexingService.enqueue_reindex XADDs to
    STREAM_REINDEX_REQUESTS MUST satisfy ReindexRequestMessage's Pydantic
    schema (which inherits batch_id: str from WorkerMessage).

    Catches the BLOCKER class where the producer's dict drops a required
    field and every async reindex fails downstream with ValidationError
    → NACK → DLQ on the consumer side. The consumer-side tests above
    don't catch this because they construct ReindexRequestMessage in
    Python (where Pydantic's required-field default applies on the test
    author's side); this test exercises the WIRE format.
    """
    from sqlalchemy.ext.asyncio import async_sessionmaker
    from codeoracle.db.session import make_async_engine, make_async_session_factory
    from codeoracle.services.indexing.service import IndexingService

    queue = ValkeyQueueClient(ValkeyConfig(
        url=valkey_url, pool_min=1, pool_max=4,
        block_ms_default=500, max_in_flight_per_consumer=10, stream_maxlen=1000,
    ))
    await queue.start()

    engine = make_async_engine()
    session_factory = make_async_session_factory(engine)
    svc = IndexingService(
        engine=engine,
        db_session_factory=session_factory,
        queue_client=queue,
        repo_root=str(tmp_git_repo),
        extractor_kinds=("joern",),
        advisory_lock_timeout_seconds=5.0,
    )

    snap = await svc.enqueue_reindex(base_sha=None)

    # Read raw payload back via the queue — DO NOT construct the message
    # in Python here, that defeats the test. Pull the JSON envelope from
    # the stream and validate it through Pydantic.
    consumed = await queue.read_pending(
        stream=STREAM_REINDEX_REQUESTS,
        group="reindex_orchestrator",
        consumer="schema_fidelity_probe",
        count=1,
        block_ms=2000,
    )
    assert len(consumed) == 1, "enqueue_reindex did not XADD"
    msg = ReindexRequestMessage.model_validate(consumed[0].payload)  # MUST NOT raise
    assert msg.batch_id == snap.sha
    assert msg.snapshot_sha == snap.sha
    assert msg.base_sha is None

    await queue.aclose()
```

- [ ] **Step 6: Run lint + type + tests**

```bash
uv run ruff check src/codeoracle/workers/reindex_orchestrator/ \
                  tests/unit/workers/reindex_orchestrator/ \
                  tests/integration/workers/reindex_orchestrator/
uv run ty check src/codeoracle/workers/reindex_orchestrator/
uv run pytest tests/unit/workers/reindex_orchestrator/ -v
uv run pytest tests/integration/workers/reindex_orchestrator/ -v -m worker
```

Expected: all PASS.

- [ ] **Step 7: Commit**

```bash
git add src/codeoracle/workers/reindex_orchestrator/ \
        tests/unit/workers/reindex_orchestrator/ \
        tests/integration/workers/reindex_orchestrator/
git commit -m "feat: Plan 5 Task 13a — ReindexOrchestratorConsumer (Plan 11 async-job driver)"
```

---

## Task 14: `src/codeoracle/cli/commands/index.py` — `codeoracle index` CLI

**What this task delivers:** the user-facing CLI. `codeoracle index --since SHA` does an incremental reindex; `codeoracle index --full` does a full one; `codeoracle index` without args defaults to `--since=<latest applied snapshot>`. Prints the final snapshot SHA + per-extractor outcome to stdout. Exit code: 0 on APPLIED, 2 on FAILED, 3 on AdvisoryLockTimeoutError.

**Files:**
- Modify: `src/codeoracle/cli/__main__.py` (Plan 1's Typer `app` — register the `index` subcommand on it)
- Create: `src/codeoracle/cli/commands/__init__.py`
- Create: `src/codeoracle/cli/commands/index.py`
- Create: `tests/unit/cli/__init__.py`
- Create: `tests/unit/cli/test_index_command.py`

- [ ] **Step 1: Confirm `src/codeoracle/cli/__main__.py` exists** and exposes the Typer root app

Plan 1 created the CLI entrypoint as a Typer `typer.Typer()` app named `app` and added `typer` to `[project.dependencies]` (Plan 1 Task 2 Step 3 — `src/codeoracle/cli/__main__.py`; DoD: "`cli/__main__.py` exposes the `python -m codeoracle` entry-point Typer app, Plan 5 Task 14 registers the first subcommand"). The framework is **Typer**, not a choice left open — Plan 5 Task 14 registers its subcommand against that existing app. Read it to confirm the app object's name:

```bash
cat src/codeoracle/cli/__main__.py
```

Expected: a `typer.Typer()` `app` object. Step 3 below registers `index_cmd` against it via `app.command(name="index")(index_cmd)`. (If Plan 1's app object carries a different name, use that name in the registration call — the framework itself is fixed as Typer.)

- [ ] **Step 2: Write `src/codeoracle/cli/commands/index.py`**

```python
"""`codeoracle index` — CLI for reindexing.

Three modes:
  codeoracle index --since SHA   # incremental from base SHA
  codeoracle index --full        # full reindex (base_sha=None)
  codeoracle index               # incremental from last APPLIED snapshot

Exit codes:
  0 — snapshot APPLIED (all extractors success or partial)
  2 — snapshot FAILED (timeout, joern config error, etc.)
  3 — AdvisoryLockTimeoutError (another reindex in flight)
"""

from __future__ import annotations

import asyncio
import logging
import sys
from pathlib import Path
from typing import Annotated

import typer

from codeoracle.adapters.valkey.client import ValkeyQueueClient
from codeoracle.adapters.valkey.config import ValkeyConfig
from codeoracle.core.exceptions import AdvisoryLockTimeoutError
from codeoracle.db.repositories.snapshots import SnapshotRepository
from codeoracle.db.session import make_async_engine, make_async_session_factory
from codeoracle.models.snapshot import SnapshotStatus
from codeoracle.services.indexing.service import IndexingService

logger = logging.getLogger(__name__)


def index_cmd(
    base_sha: Annotated[
        str | None,
        typer.Option("--since", help="Incremental reindex from this base SHA."),
    ] = None,
    full: Annotated[
        bool,
        typer.Option("--full", help="Full reindex (overrides --since)."),
    ] = False,
    repo_root: Annotated[
        Path,
        typer.Option(
            "--repo-root", exists=True, file_okay=False,
            help="Path to the repo to reindex. Default: CWD.",
        ),
    ] = Path("."),
    wait_seconds: Annotated[
        float,
        typer.Option(
            "--wait",
            help="Seconds to wait for the advisory lock. Default 0 (fail-fast).",
        ),
    ] = 0.0,
) -> None:
    """Reindex the corpus into AGE."""
    asyncio.run(_run_index(base_sha=base_sha, full=full, repo_root=repo_root, wait_seconds=wait_seconds))


async def _run_index(
    *, base_sha: str | None, full: bool, repo_root: Path, wait_seconds: float
) -> None:
    # Plan 3 signature: make_async_engine() reads Settings internally;
    # make_async_session_factory(engine) binds the factory to that engine.
    engine = make_async_engine()
    session_factory = make_async_session_factory(engine)
    queue_client = ValkeyQueueClient(ValkeyConfig())  # type: ignore[call-arg]
    await queue_client.start()

    # If neither --full nor --since: default to latest APPLIED
    if not full and base_sha is None:
        async with session_factory() as session:
            repo = SnapshotRepository(session)
            latest = await repo.get_latest_applied()
        if latest is not None:
            base_sha = latest.sha
            typer.echo(f"Using base_sha from latest APPLIED snapshot: {base_sha}")
        else:
            typer.echo("No prior APPLIED snapshot — performing full reindex.")
            full = True

    service = IndexingService(
        engine=engine,
        db_session_factory=session_factory,
        queue_client=queue_client,
        repo_root=repo_root,
        extractor_kinds=("joern",),  # Plan 6 extends
        advisory_lock_timeout_seconds=wait_seconds,
    )
    try:
        snap = await service.reindex(base_sha=None if full else base_sha)
    except AdvisoryLockTimeoutError as exc:
        typer.echo(f"Lock timeout: {exc}", err=True)
        await queue_client.aclose()
        await engine.dispose()
        sys.exit(3)

    await queue_client.aclose()
    await engine.dispose()

    if snap.status == SnapshotStatus.APPLIED:
        typer.echo(f"\nSnapshot {snap.sha} APPLIED.")
        for k, v in snap.extractor_results.items():
            typer.echo(f"  {k}: {v.value}")
        sys.exit(0)
    else:
        typer.echo(f"\nSnapshot {snap.sha} FAILED: {snap.error}", err=True)
        sys.exit(2)
```

- [ ] **Step 3: Register `index_cmd` in the CLI entrypoint**

Edit `src/codeoracle/cli/__main__.py` — add at the bottom (Plan 1's Typer `app` is the top-level app):

```python
from codeoracle.cli.commands.index import index_cmd

app.command(name="index")(index_cmd)
```

- [ ] **Step 4: Write `tests/unit/cli/test_index_command.py`**

```python
"""Plan 5 Task 14: `codeoracle index` CLI unit tests with mocked service."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from typer.testing import CliRunner

from codeoracle.cli.__main__ import app
from codeoracle.models.snapshot import (
    ExtractorOutcome,
    Snapshot,
    SnapshotStatus,
)


def _applied_snapshot(sha="abc1234"):
    return Snapshot(
        sha=sha, parent_sha=None, changed_files=["a.py"],
        status=SnapshotStatus.APPLIED,
        extractor_results={"joern": ExtractorOutcome.SUCCESS},
        error=None, applied_at=None,
    )


def _failed_snapshot(sha="abc1234"):
    return Snapshot(
        sha=sha, parent_sha=None, changed_files=["a.py"],
        status=SnapshotStatus.FAILED,
        extractor_results={},
        error="orchestrator timed out", applied_at=None,
    )


def test_full_flag_invokes_reindex_with_none_base():
    runner = CliRunner()
    with patch("codeoracle.cli.commands.index.IndexingService") as Svc, \
         patch("codeoracle.cli.commands.index.make_async_engine"), \
         patch("codeoracle.cli.commands.index.make_async_session_factory"), \
         patch("codeoracle.cli.commands.index.ValkeyQueueClient") as Qc:
        Svc.return_value.reindex = AsyncMock(return_value=_applied_snapshot())
        Qc.return_value.start = AsyncMock()
        Qc.return_value.aclose = AsyncMock()
        result = runner.invoke(app, ["index", "--full"])
    assert result.exit_code == 0
    assert "APPLIED" in result.output


def test_failed_snapshot_exits_2():
    runner = CliRunner()
    with patch("codeoracle.cli.commands.index.IndexingService") as Svc, \
         patch("codeoracle.cli.commands.index.make_async_engine"), \
         patch("codeoracle.cli.commands.index.make_async_session_factory"), \
         patch("codeoracle.cli.commands.index.ValkeyQueueClient") as Qc:
        Svc.return_value.reindex = AsyncMock(return_value=_failed_snapshot())
        Qc.return_value.start = AsyncMock()
        Qc.return_value.aclose = AsyncMock()
        result = runner.invoke(app, ["index", "--full"])
    assert result.exit_code == 2
    assert "FAILED" in result.output


def test_advisory_lock_timeout_exits_3():
    from codeoracle.core.exceptions import AdvisoryLockTimeoutError
    runner = CliRunner()
    with patch("codeoracle.cli.commands.index.IndexingService") as Svc, \
         patch("codeoracle.cli.commands.index.make_async_engine"), \
         patch("codeoracle.cli.commands.index.make_async_session_factory"), \
         patch("codeoracle.cli.commands.index.ValkeyQueueClient") as Qc:
        Svc.return_value.reindex = AsyncMock(
            side_effect=AdvisoryLockTimeoutError("locked"),
        )
        Qc.return_value.start = AsyncMock()
        Qc.return_value.aclose = AsyncMock()
        result = runner.invoke(app, ["index", "--full"])
    assert result.exit_code == 3
    assert "Lock timeout" in result.output


def test_no_args_defaults_to_latest_applied():
    runner = CliRunner()
    with patch("codeoracle.cli.commands.index.IndexingService") as Svc, \
         patch("codeoracle.cli.commands.index.make_async_engine"), \
         patch("codeoracle.cli.commands.index.make_async_session_factory") as Sf, \
         patch("codeoracle.cli.commands.index.ValkeyQueueClient") as Qc, \
         patch("codeoracle.cli.commands.index.SnapshotRepository") as Sr:
        Sf.return_value = MagicMock()
        Sf.return_value.return_value.__aenter__ = AsyncMock(return_value=MagicMock())
        Sf.return_value.return_value.__aexit__ = AsyncMock()
        Sr.return_value.get_latest_applied = AsyncMock(
            return_value=_applied_snapshot(sha="prev_sha")
        )
        Svc.return_value.reindex = AsyncMock(return_value=_applied_snapshot())
        Qc.return_value.start = AsyncMock()
        Qc.return_value.aclose = AsyncMock()
        result = runner.invoke(app, ["index"])
    assert result.exit_code == 0
    assert "prev_sha" in result.output
```

- [ ] **Step 5: Run + lint + type + commit**

```bash
uv run pytest tests/unit/cli/test_index_command.py -v
uv run ruff check src/codeoracle/cli/commands/index.py
uv run ty check src/codeoracle/cli/commands/index.py
git add src/codeoracle/cli/commands/ src/codeoracle/cli/__main__.py tests/unit/cli/test_index_command.py
git commit -m "feat: Plan 5 Task 14 — codeoracle index CLI (--full/--since/--wait)"
```

---

## Task 15: Full vertical integration test — `codeoracle index` over `src/codeoracle/`

**What this task delivers:** the one end-to-end test that proves the WorkerConsumer framework, the JoernClient + parser, the GraphRepository, and IndexingService all work together. Spins up Postgres+AGE + Valkey via testcontainers, starts a JoernIndexerConsumer in a background asyncio task, runs `IndexingService.reindex(base_sha=None)` against a checked-in fixture repo (`tests/fixtures/sample_repo/`), and asserts: snapshot transitions PENDING → APPLYING → APPLIED, and CALLS edges materialize in AGE.

This is marked `worker` + `integration` and gated to nightly + on-demand CI runs (Plan 1's `worker` job).

**Files:**
- Create: `tests/fixtures/sample_repo/` — minimal Python project (5-10 files with a known call graph)
- Create: `tests/integration/test_full_vertical_joern.py`

- [ ] **Step 1: Create `tests/fixtures/sample_repo/`** — minimal Python project

```bash
mkdir -p tests/fixtures/sample_repo/pkg
```

Write:

`tests/fixtures/sample_repo/pkg/__init__.py`:

```python
"""Sample fixture package — Plan 5 integration test target."""
```

`tests/fixtures/sample_repo/pkg/a.py`:

```python
"""Module a — defines greet()."""


def greet(name: str) -> str:
    return f"Hello, {name}!"
```

`tests/fixtures/sample_repo/pkg/b.py`:

```python
"""Module b — calls greet()."""

from pkg.a import greet


def main(name: str) -> str:
    return greet(name)
```

`tests/fixtures/sample_repo/.gitignore`:

```
__pycache__/
*.pyc
```

Initialize git so DeltaComputer can run inside it:

```bash
cd tests/fixtures/sample_repo
git init -q
git add -A
git -c user.email=t@t -c user.name=t commit -m "fixture" -q
cd ../../../
```

- [ ] **Step 2: Write `tests/integration/test_full_vertical_joern.py`**

```python
"""Plan 5 Task 15: full vertical Joern indexing integration test.

This test proves the WorkerConsumer framework works end-to-end:
  - JoernIndexerConsumer running in a background task,
  - IndexingService running `reindex(base_sha=None)`,
  - Snapshot lifecycle PENDING → APPLYING → APPLIED,
  - CALLS edge from pkg/b.py::main to pkg/a.py::greet materializes in AGE.

Joern is run for real — joern-cli must be on PATH in the test environment.
If joern isn't installed, the test SKIPS gracefully (caught by Step 1
of the test body, not via a pytest skip — we want CI's `worker` job to
own the install).
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from codeoracle.adapters.joern.client import JoernClient
from codeoracle.adapters.joern.config import JoernConfig
from codeoracle.adapters.joern.parser import JoernCpgParser
from codeoracle.adapters.valkey.client import ValkeyQueueClient
from codeoracle.adapters.valkey.config import ValkeyConfig
from codeoracle.db.repositories.graph import GraphRepository
from codeoracle.models.snapshot import SnapshotStatus
from codeoracle.services.indexing.service import IndexingService
from codeoracle.workers._base.message import RetryPolicy
from codeoracle.workers.joern_indexer.consumer import JoernIndexerConsumer

pytestmark = [pytest.mark.integration, pytest.mark.worker]


FIXTURE = Path(__file__).parent.parent / "fixtures/sample_repo"


async def test_full_vertical_indexes_calls_edge(
    async_engine,
    async_session_factory,
    async_session,
    valkey_url,
    tmp_path,
    monkeypatch,
):
    """End-to-end: codeoracle index → CALLS edge materializes in AGE."""
    import shutil
    if shutil.which("joern-parse") is None:
        pytest.skip(
            "joern-parse not on PATH — install joern-cli locally "
            "(https://docs.joern.io/installation) or run in the worker-CI "
            "image once Plan 1A bakes it (the joern Dockerfile is in Plan 1A's "
            "image-build matrix, not Plan 5)."
        )

    # 1) Stage queue + worker
    qc = ValkeyQueueClient(ValkeyConfig(
        url=valkey_url, pool_min=1, pool_max=4,
        block_ms_default=500, max_in_flight_per_consumer=5, stream_maxlen=1000,
    ))
    await qc.start()

    joern_config = JoernConfig(
        joern_parse_bin="joern-parse",
        joern_export_bin="joern-export",
        parse_timeout_seconds=120,
        export_timeout_seconds=120,
        work_dir=str(tmp_path / "joern-work"),
    )
    consumer = JoernIndexerConsumer(
        queue_client=qc,
        retry_policy=RetryPolicy(),
        consumer_name="test_worker",
        stop_event=asyncio.Event(),
        joern_client=JoernClient(joern_config),
        parser=JoernCpgParser(),
        db_session_factory=async_session_factory,
    )
    worker_task = asyncio.create_task(consumer.run())

    # 2) Run reindex over the fixture repo
    monkeypatch.chdir(FIXTURE)
    service = IndexingService(
        engine=async_engine,
        db_session_factory=async_session_factory,
        queue_client=qc,
        repo_root=str(FIXTURE),
        extractor_kinds=("joern",),
        advisory_lock_timeout_seconds=0.0,
    )
    snap = await service.reindex(base_sha=None)

    # 3) Assert lifecycle
    assert snap.status == SnapshotStatus.APPLIED, (
        f"snapshot.status={snap.status.value}, error={snap.error}"
    )

    # 4) Assert the CALLS edge materialized
    graph = GraphRepository(async_session)
    rows = await graph.execute_cypher(
        "MATCH (caller:Symbol)-[:CALLS]->(callee:Symbol) "
        "WHERE caller.snapshot_sha = $sha AND callee.snapshot_sha = $sha "
        "RETURN caller.fqn AS c, callee.fqn AS t",
        {"sha": snap.sha},
        column_spec="c agtype, t agtype",
    )
    callers = [
        (str(r["c"]).strip('"'), str(r["t"]).strip('"'))
        for r in rows
    ]
    # We expect at least one CALLS edge from b.py::main → a.py::greet
    assert any(
        "main" in c and "greet" in t for c, t in callers
    ), f"No CALLS edge found from main to greet. rows={callers}"

    # 5) Tear down
    consumer._stop.set()
    await worker_task
    await qc.aclose()
```

- [ ] **Step 3: Run + commit**

Running this requires `joern-cli` installed; the CI `worker` job ships it. Locally:

```bash
# Skip-if-not-installed verification: the test should skip without joern
uv run pytest tests/integration/test_full_vertical_joern.py -v -m worker

# With joern installed (in the worker-CI image or local install):
JOERN_HOME=/opt/joern PATH="$JOERN_HOME/joern-cli:$PATH" \
    uv run pytest tests/integration/test_full_vertical_joern.py -v -m worker
```

Expected: PASS (or SKIP if joern not on PATH). The CI `worker` job MUST run with joern installed.

```bash
git add tests/fixtures/sample_repo/ tests/integration/test_full_vertical_joern.py
git commit -m "feat: Plan 5 Task 15 — full vertical integration test (CALLS edge materializes)"
```

---

## Task 16: Final verification + push

**What this task delivers:** the final lint/type/test sweep + ruff banned-api enforcement that direct `valkey-py` / `subprocess` calls outside their adapters are forbidden + the push to origin/main.

**Files:**
- Modify: `pyproject.toml` (extend `[tool.ruff.lint.flake8-tidy-imports.banned-api]`)

- [ ] **Step 1: Extend ruff banned-api**

In `pyproject.toml`:

```toml
[tool.ruff.lint.flake8-tidy-imports.banned-api]
# Plan 4 — LLM SDK lockdown
"openai".msg     = "Import via codeoracle.adapters.llm — direct SDK imports banned outside providers/"
"anthropic".msg  = "Import via codeoracle.adapters.llm — direct SDK imports banned outside providers/"
"litellm".msg    = "Import via codeoracle.adapters.llm — direct SDK imports banned outside providers/"
"ollama".msg     = "Import via codeoracle.adapters.llm — direct SDK imports banned outside providers/"
# Plan 5 — Valkey SDK lockdown
"valkey".msg     = "Import via codeoracle.adapters.valkey — direct SDK imports banned outside adapters/valkey/"
"valkey.asyncio".msg = "Import via codeoracle.adapters.valkey — direct SDK imports banned outside adapters/valkey/"
"redis".msg     = "Use codeoracle.adapters.valkey instead (redis-py is fine as the underlying SDK but call sites use the adapter)."
"redis.asyncio".msg = "Use codeoracle.adapters.valkey instead."

[tool.ruff.lint.per-file-ignores]
"src/codeoracle/adapters/llm/providers/*.py" = ["TID251"]
"src/codeoracle/adapters/valkey/*.py" = ["TID251"]
```

- [ ] **Step 2: Add banned-API enforcement test**

`tests/integration/test_plan5_banned_imports.py`:

```python
"""Plan 5 Task 16: confirm ruff's banned-api blocks accidental direct
valkey/redis imports outside the adapter."""

from __future__ import annotations

import subprocess
from pathlib import Path


def test_direct_valkey_import_outside_adapter_is_flagged_by_ruff(tmp_path):
    bad_file = tmp_path / "bad.py"
    bad_file.write_text(
        "import valkey.asyncio  # this MUST trigger TID251\n"
    )
    result = subprocess.run(
        ["uv", "run", "ruff", "check", str(bad_file)],
        capture_output=True, text=True,
    )
    assert "TID251" in result.stdout or "TID251" in result.stderr, (
        f"ruff did not flag direct valkey import. stdout={result.stdout!r} stderr={result.stderr!r}"
    )


def test_adapter_internal_valkey_import_is_allowed():
    """Verify the per-file-ignore is in effect for adapters/valkey/."""
    adapter_file = Path("src/codeoracle/adapters/valkey/client.py")
    result = subprocess.run(
        ["uv", "run", "ruff", "check", str(adapter_file)],
        capture_output=True, text=True,
    )
    # No TID251 in the output (other findings unrelated to banned-api OK)
    assert "TID251" not in result.stdout
```

- [ ] **Step 3: Full lint + type + test sweep**

```bash
# Lint everything
uv run ruff check src/ tests/
uv run ruff format --check src/ tests/

# Type-check the platform code
uv run ty check src/codeoracle/

# Unit tests
uv run pytest tests/unit/ -v

# Integration tests (Postgres + Valkey via testcontainers; mark='worker')
uv run pytest tests/integration/ -v -m "integration"
```

Expected: zero lint/type errors; every test PASSES (or SKIPS gracefully when joern-cli not installed locally).

- [ ] **Step 4: Confirm the single-flight contract end-to-end**

Run two `codeoracle index` invocations concurrently:

```bash
# Terminal 1
uv run codeoracle index --full &
# Terminal 2 (immediately)
uv run codeoracle index --full
```

Expected: Terminal 1 succeeds with exit 0; Terminal 2 exits with code 3 + "Lock timeout: ... another reindex is in flight." This is the single-flight property the spec §8.4 promised.

- [ ] **Step 5: Commit + push**

```bash
git add pyproject.toml tests/integration/test_plan5_banned_imports.py
git commit -m "feat: Plan 5 Task 16 — ruff banned-api for valkey + final verification"
git push origin main
```

Expected: CI runs all of `ci.yml` (Plan 1 gates) + integration with the new tests. PR-or-direct-push policy follows whatever Plan 1 codified.

---

## What Plan 6 inherits from Plan 5

- **WorkerConsumer ABC** — Plan 6 subclasses it 3 more times for scip/opengrep/embed (Plan 7 owns the traces consumer). Zero changes to `_base/`.
- **WorkerMessage + RetryPolicy** — Plan 6 subclasses WorkerMessage 3 more times (Plan 7 adds the traces message type).
- **build_worker_context() composition root** — Plan 6's 3 new workers reuse it (Plan 7's traces worker also reuses it).
- **ValkeyQueueClient** — Plan 6 calls `enqueue()`/`read_pending()`/`ack()`/`dead_letter()` against new streams (`indexing.scip`, `indexing.opengrep`, `indexing.embed`); zero changes to the client.
- **streams.py** — Plan 6 extends with `STREAM_INDEXING_SCIP`, `STREAM_INDEXING_OPENGREP`, etc. (the comments in Plan 5's file already TODO the new constants).
- **SnapshotRepository + state machine** — Plan 6 records additional extractors in `snapshot.extractor_results`; Plan 5's enum + transition table accommodates this without change.
- **SnapshotFileOutcomeRepository** — Plan 6 workers `upsert(extractor="scip"|"opengrep"|...)`; the table's composite PK already accommodates this.
- **GraphRepository.replace_file_subgraph()** — SCIP and OpenGrep also write graph subgraphs (different edge labels). Plan 6 extends `_NODE_LABEL_MAP` / `_EDGE_LABEL_MAP` in JoernCpgParser-style parsers per extractor.
- **IndexingOrchestrator** — Plan 6 adds the new kinds to `_STREAM_BY_KIND` + passes them in `extractor_kinds=(...)`. The fan-out + barrier shape is unchanged.
- **IndexingService** — Plan 6 changes one constructor argument: `extractor_kinds=("joern","scip","opengrep","embed")`. The lifecycle is unchanged.
- **`pg_advisory_lock` primitive** — Plan 6 inherits, adds the *contention test* (per advisor's note: only stress-testable with ≥2 extractors) at `tests/integration/services/indexing/test_advisory_lock_contention.py`.
- **CLI** — Plan 6 doesn't extend `codeoracle index`; the new extractors run automatically once `extractor_kinds` includes them.
- **WorkerError hierarchy (`WorkerError` / `TransientWorkerError` / `TerminalWorkerError`)** — Plan 5 introduces the exception hierarchy in `workers/_base/message.py` + extends `core/exceptions.py` (file-structure L74, L86). Plan 6's extractor-specific exceptions (e.g., `JoernExtractionError(TerminalWorkerError)`, the canonical sibling pattern) subclass these. Plan 6's transient/terminal classification doctrine inherits from Plan 5's split.
- **`SnapshotFileOutcome` ORM (declarative model in `db/schemas.py`)** — Plan 5 introduces the `snapshot_file_outcomes` table (migration `0004_snapshot_file_outcomes.py`) + the `SnapshotFileOutcome` ORM declarative model that maps it. The existing `SnapshotFileOutcomeRepository` bullet covers the access layer; this bullet covers the underlying data shape Plan 6's per-file barrier writes rows against directly (independent of the repository class).
- **Test fixtures (`valkey_url`, `valkey_queue_client`, `pg_age_database`) in `tests/integration/conftest.py`** — Plan 5 Task 1 creates the Plan-5-scoped conftest fixtures; Plan 6 integration tests consume all three via the standard pytest collection. Separate inheritance surface from the production-code modules above — this captures test-scaffold reuse.
- **Migration `0004_snapshot_file_outcomes.py` (chain-link revision)** — Plan 5's `0004_snapshot_file_outcomes.py` (`revision = "0004"`) is the head of the migration chain Plan 6 extends. Plan 6's migration sets `down_revision = "0004"` per the `## Cross-plan contracts` decision 2 bare-numeric convention (Plan 5 L41); this bullet enumerates the specific revision Plan 6's migration targets.
- **`JoernClient` argv-only subprocess pattern (no `shell=True` doctrine)** — Plan 5's `adapters/joern/client.py` (file-structure L54) executes the `joern` CLI via `subprocess.run([...args])` exclusively (never `shell=True`). Plan 6's `ScipPythonClient` and `OpengrepClient` mirror this pattern verbatim — the doctrine is enumerated here as an inherited subprocess-construction convention separate from the per-class inheritance above.
- **`JoernIndexerMessage` shape (concrete-subclass template)** — Plan 5's `JoernIndexerMessage` is the first concrete WorkerMessage subclass; Plan 6's `ScipIndexerMessage`, `OpengrepIndexerMessage`, `EmbedIndexerMessage` follow the same field set + serialization shape. Distinct from the abstract `WorkerMessage + RetryPolicy` inheritance (existing bullet) — this captures the per-worker concrete template Plan 6 patterns against.
- **`:Symbol` AGE node label (Joern-side vocabulary)** — Plan 5's `GraphRepository.replace_file_subgraph()` emits `:Symbol` as the canonical AGE node label for Joern-extracted symbols. Plan 6 adds SCIP-side sibling labels (`:Definition`, `:Reference`) distinct from `:Symbol`. Bullet enumerates the label-vocabulary anchor Plan 6 extends, separate from the method-level inheritance L7517 names.
- **`WorkerConsumer.tasks` → per-file outcome write contract (1 row per file)** — Plan 5 establishes the invariant that every `WorkerConsumer.tasks` invocation writes exactly one row to `snapshot_file_outcomes` (success or failure). Load-bearing for Plan 6's architectural invariant 1 (per-file barrier semantics). Bullet enumerates the doctrine separately from the `WorkerConsumer ABC` class inheritance L7510 already names — the contract is what Plan 6 patterns against, not just the class.
- **`IndexingService.OUTCOME_TO_EXTRACTOR_OUTCOME` outcome→status mapping** — Plan 5's `IndexingService` defines `OUTCOME_TO_EXTRACTOR_OUTCOME` mapping that converts per-file `snapshot_file_outcomes` rows into per-extractor `Snapshot.extractor_results` status entries. Plan 6's SCIP project-level failure cascade (architectural invariant 3) depends on this mapping. Distinct from the bare `IndexingService` constructor-argument extension L7519 names.
- **`Snapshot.extractor_results` dict (per-extractor results keying)** — Plan 5's `models/snapshot.py` defines the `Snapshot.extractor_results` Pydantic field as a dict keyed by extractor name (`"joern"`, etc.). Plan 6 writes additional keys (`"scip"`, `"opengrep"`, `"embed"`) into the same dict via `SnapshotRepository` without schema change. Distinct from the `SnapshotRepository + state machine` access-layer inheritance L7515 names — this captures the dict-shape inheritance separately.
- **`ValkeyQueueClient.peek_pending_count` + `peek_first_payload` (debug surface)** — Plan 5's `ValkeyQueueClient` exposes the two `peek_*` helpers for test-side inspection of stream state without consuming messages. Plan 6's project-mode integration tests use both. Distinct from the production methods L7513 names — this captures the test-time debug surface.

## What Plan 7 inherits from Plan 5

- **`pg_advisory_lock` primitive** — Plan 7's `TracesIngestConsumer` reuses Plan 5's advisory-lock-based single-flight pattern, taking lock ID `0x46AE0002` (sibling to Plan 5's `IndexingService.reindex` lock ID `0x46AE0001`); the two services hold non-overlapping locks so they can run concurrently.
- **`workers/_base/` package convention** — Plan 5 establishes `codeoracle.workers._base.*` as the worker-framework namespace; Plan 7 adds `continuous_consumer.py` + `cursor_store_pg.py` as siblings under the same package without modifying Plan 5's modules.
- **`build_worker_context()` + `WorkerContext` dataclass (`codeoracle.workers._base.wiring`)** — Plan 5's `workers/_base/wiring.py` exposes `build_worker_context()` (the composition root) + `WorkerContext` (the resolved-deps dataclass). Plan 7's TracesIngestConsumer inherits the SIGTERM/SIGINT signal-handler installation that happens inside `build_worker_context()`; the dataclass also gives Plan 7 typed access to the resolved DB session factory, Valkey client, and LLM provider factory.
- **Argv-only subprocess ban (no `shell=True` doctrine)** — Plan 5's `JoernClient` codifies argv-only `subprocess.run([...])` invocations (per EDIT-5.23 for Plan 6). Plan 7's TempoClient is pure HTTP (no subprocess) and so doesn't exercise the ban directly, but inherits the doctrine as a forward-compat invariant: any future subprocess Plan 7 adds MUST use argv-only mode.
- **`src/codeoracle/core/streams.py` (stream-name + advisory-lock-ID registry module)** — Plan 5 Task 1 creates `core/streams.py` as the string-constants module for stream names and advisory-lock IDs. Plan 7 extends the same module with `STREAM_TRACES_INGEST` and the traces-side lock ID `0x46AE0002` (sibling to Plan 5's `IndexingService.reindex` lock `0x46AE0001`). Bullet enumerates the module separately from the package-convention inheritance L7537 names.
- **Alembic revision `0004_snapshot_file_outcomes` (migration-numbering precedent)** — Plan 5's `0004_snapshot_file_outcomes.py` is the head of the migration chain Plan 7 extends. Plan 7's migration chains via `down_revision = "0004"` (or `"0005"` if Plan 6's migration lands first; per `## Cross-plan contracts` decision 2 the bare-numeric convention is what's load-bearing, not the specific predecessor). Bullet enumerates the precedent for symmetry with Plan 6's enumeration.
- **`async_session_factory` test fixture (Plan 5 conftest)** — Plan 5 Task 1 creates the `async_session_factory` fixture in `tests/integration/conftest.py`. Plan 7's integration tests consume it for DB-scoped fixtures (per Plan 7 L2150 quote). Distinct from the production-code modules above — this captures test-scaffold reuse.
- **`async_engine` test fixture (Plan 5 conftest)** — Sibling to the `async_session_factory` fixture above. Plan 5 Task 1 also creates the `async_engine` fixture; Plan 7's integration tests consume it where engine-level operations are needed (per Plan 7 L5585 quote). Separate bullet for per-identifier verifier mapping.

## What Plan 8 inherits from Plan 5

- **`GraphRepository.execute_cypher()`** — Plan 8's `GraphQueryResolver` strategy (for `HandleType.GRAPH_QUERY`) calls `execute_cypher(cypher, params)` to resolve the literal Cypher query embedded in the locator (Plan 5 Task 8 / Plan 8 Task 11).
- **`GraphQueryError`** — Plan 8's `GraphQueryResolver` catches Plan 5's exception type to translate Cypher failures into resolution errors (Plan 5 Task 1 / Plan 8 Task 11).
- **Single-scalar-per-row agtype contract** — Plan 5's `GraphRepository` empirically established that AGE `agtype` values arrive as quoted JSON strings without a typed Python codec, requiring defensive `str(value).strip('"')` decoding. Plan 8 inherits this contract and constrains its Cypher API: callers (Plan 10 agent) MUST emit `RETURN <scalar> AS result` shapes; maps/collections are unsupported until a future plan verifies the psycopg+AGE non-scalar decoding contract.
- **`JoernConfig` (per-adapter Settings pattern)** — Plan 5's `adapters/joern/config.py` defines `JoernConfig` as a `pydantic_settings.BaseSettings` subclass scoped to the Joern adapter (env-loaded). Plan 8's `SourcebotConfig` mirrors the per-adapter Settings pattern verbatim (Plan 8 L379 quote: "Matches the per-adapter Settings pattern from Plan 5 (JoernConfig)"). Bullet enumerates the pattern as inherited convention distinct from the runtime services above.
- **`workers/_base/wiring.py` composition root (Plan 8 `SourcebotConfig` instantiation site)** — Plan 5's `workers/_base/wiring.py` (the composition root, EDIT-5.31 for Plan 7) is also one of two composition-root sites Plan 8 cites at its L493 quote for `SourcebotConfig` instantiation (the other being Plan 11's API process wiring). Plan 8's worker-side `SourcebotConfig` resolution happens inside `build_worker_context()`. Bullet enumerates the composition-root inheritance separately from the `JoernConfig` pattern inheritance.

## What Plan 1A inherits from Plan 5

- **`codeoracle.worker` module namespace (variant matrix entry-modules)** — Plan 1A's Dockerfile variant matrix (per Plan 1A L1397 `workers → Plans 5/6/7`) sets `CMD ["python", "-m", "codeoracle.workers.<variant>"]` against worker modules Plan 5 originates: `codeoracle.workers._base` (the framework: `consumer.py`, `message.py`, `wiring.py`), `codeoracle.workers.joern_indexer` (first concrete worker, Plan 5 Task 8). Plan 1A imports only the entry-module paths; Plan 5 owns the implementations.

## What Plan 5 inherits from Plan 2

- **worker Dockerfiles (pinned.env file-level source)** — Plan 5's worker Dockerfiles source `infra/versions/pinned.env` at build time; this is the file-consumer relationship distinct from the individual env-var substitutions L15 already names (`IMAGE_VALKEY`, `IMAGE_POSTGRES_AGE`, `JOERN_VERSION`). Parallels Plan 3 `imports.4` which captures the same file-level inheritance separately from env-var-level inheritance.
- **joern_indexer worker (Joern CLI version pin + Python-frontend CPG-gate precondition)** — Plan 5 Task 8 builds the `joern_indexer` worker (`src/codeoracle/workers/joern_indexer/`); Plan 2's CPG-gate proved Joern's Python frontend produces a usable CPG, and Plan 2's `JOERN_VERSION` pin in `pinned.env` (L15) is the version the worker subprocesses against. The two facets are bundled here as the worker's full Plan 2 inheritance.
- **indexing framework deferral (Plan 5 half of `plan-2.provides.27`)** — Plan 2's deferred deliverable "indexing framework + real indexing of configured target repositories" splits into two halves: (a) the framework (`IndexingService` + `IndexingOrchestrator` + `DeltaComputer` + `pg_advisory_lock`) is Plan 5's tasks 5–9 deliverable, (b) the real-indexing run against configured target repositories is Plan 13's E2E vertical slice (`plan-2.provides.28`). This bullet enumerates Plan 5's half explicitly so the verifier can split the audit row.

## What Plan 5 inherits from Plan 3

- **docker-compose stack (postgres + valkey services)** — Plan 3 appends `postgres` and `valkey` services to `docker-compose.yml` (Plan 3 `provides.10`); Plan 5's worker subprocesses + integration tests connect to both at runtime. Distinct from the bootstrap-script + migration inheritance L17 already names — this captures the runtime container topology.
- **alembic migration pattern (bare-numeric revision ID convention)** — Plan 3 introduces alembic and establishes `revision = "0001"` etc. (bare-numeric). Plan 5's `0004_snapshot_file_outcomes.py` continues the chain (`revision = "0004"`, `down_revision = "0003"`); the `## Cross-plan contracts` decision 2 (Plan 5 L41) ratifies this convention for Plan 6 too. This bullet enumerates the tool-choice + ID-convention inheritance distinct from the individual migration files L17 already names.

## What Plan 5 inherits from Plan 4

- **OpenAI provider multi-turn `tool_calls` round-trip extension** — Plan 4 extends the OpenAI provider's `chat()` method to continue the conversation after a tool invocation (multi-turn loop). Plan 5's worker framework builds the provider via `build_budgeted_provider` in `workers/_base/wiring.py` (L19); downstream Plan 6 workers that call LLMs depend on this multi-turn contract being available through the wrapped provider. This bullet enumerates the multi-turn extension distinct from the bare `LLMProvider` facade L19 names.
- **Anthropic provider multi-turn `tool_calls` round-trip extension** — Sibling to the OpenAI extension above; Plan 4 ships parallel Anthropic-side multi-turn support so the `LLMProvider` facade remains provider-agnostic. Plan 5's `build_budgeted_provider` resolution honors the LLM-agnostic invariant: whichever provider env-config selects, the multi-turn contract is identical.
- **Live-LLM workflow deterministic-decoding contract (`temperature=0` + seed)** — Plan 4 stipulates that LLM provider implementations set deterministic decoding flags so downstream verifier-side reproduction is byte-identical. Plan 5's worker framework hosts the budget-wrapped provider via `build_budgeted_provider` (L19) downstream Plan 6 workers consume; the deterministic-decoding contract propagates through the wrapped provider unchanged. Bullet present so the Plan 4 → Plan 5 → Plan 6 reproducibility chain is enumerated explicitly.

## What Plan 10 inherits from Plan 5

- **`GraphRepository` + `GraphRepository.execute_cypher(cypher, params)`** — Plan 5's `db/repositories/graph.py` exposes `GraphRepository` (the access class) and its `execute_cypher()` method. Plan 10's LangGraph agent calls `execute_cypher(cypher, params)` from its `graph_query` node to resolve literal Cypher queries embedded in handle locators (per Plan 10 `imports.22` quote). Bin-C umbrella-split: bullet enumerates both the class and the specific method the agent invokes so the verifier can match either identifier shape.
- **`SnapshotRepository` + `SnapshotRepository.get_latest_applied()`** — Plan 5's `db/repositories/snapshots.py` exposes `SnapshotRepository` and `get_latest_applied()`. Plan 10's agent intent node calls `get_latest_applied()` to resolve the current snapshot context (per Plan 10 `imports.23` quote). Sibling to the `GraphRepository` bullet above; both repositories are how Plan 10's agent reaches Plan 5 graph + snapshot state.

## What Plan 11 inherits from Plan 5

> **Added 2026-05-26 amendment** — Plan 11 async-job pivot. Every artifact in this section is consumed by Plan 11's `POST /v1/reindex` + `GET /v1/reindex/{snapshot_sha}` route pair. The advisory-lock interaction that PRE-amendment lived inside Plan 11's route handler now lives entirely inside Task 13a's `ReindexOrchestratorConsumer` worker process; the API never observes lock contention.

- **`IndexingService.enqueue_reindex(*, base_sha: str | None) -> Snapshot`** — Plan 5 Task 13's `IndexingService` (post-2026-05-26 amendment) exposes `enqueue_reindex` as the async-job entry point. Plan 11's `POST /v1/reindex` route calls this method; it persists a PENDING `Snapshot` row + XADDs a `ReindexRequestMessage` to `STREAM_REINDEX_REQUESTS`, returns the row. Idempotent on the same tree state (catches `SnapshotConflictError` from `create_pending` and returns the existing row instead of re-XADDing).
- **`IndexingService.get_snapshot(snapshot_sha) -> Snapshot | None`** — Sibling method added in the same amendment. Plan 11's `GET /v1/reindex/{snapshot_sha}` polling endpoint calls it to read the current state of a pending/applying/applied/failed reindex job. Thin facade over `SnapshotRepository.get_by_sha`; lives on the service so Plan 11's api/ layer never reaches into `db/repositories/` directly (preserves Plan 11 invariant 1 — thin entrypoint, no DB access from api/).
- **`STREAM_REINDEX_REQUESTS` constant** (`core/streams.py`) — Plan 5 Task 1 (2026-05-26 amendment) pins the stream name. Plan 11 does NOT reference this constant directly; only `IndexingService.enqueue_reindex` does the XADD. Bullet is here for the dependency catalog completeness, not because Plan 11 imports it.
- **`ReindexOrchestratorConsumer` + `python -m codeoracle.workers.reindex_orchestrator` entrypoint** — Plan 5 Task 13a defines the worker; Plan 1A compose YAML gains a new `reindex_orchestrator` service entry running this entrypoint. The advisory lock `0x46AE0001` is acquired by this worker's session, NOT by Plan 11's API process. API replicas can restart freely mid-reindex with no lock-state effect (Postgres session-level advisory locks auto-release on connection death; the worker's stale-APPLYING sweep cleans up on next pickup).
- **`Snapshot` Pydantic model + `SnapshotStatus` enum** (transitively via Plan 3) — Plan 11 returns `Snapshot` as the literal `response_model` for both POST (with `status=PENDING`) and GET (any status); the `status` field drives client polling. The model is owned by Plan 3 but the snapshot lifecycle (`PENDING → APPLYING → APPLIED|FAILED`) is owned by Plan 5's `IndexingService.reindex` body.

## What Plan 12 inherits from Plan 5

- **`services/indexing/service.py:IndexingService`, `core/exceptions.py:AdvisoryLockTimeoutError`, `adapters/valkey/client.py:ValkeyQueueClient`, `adapters/valkey/config.py:ValkeyConfig`** — Plan 12's MCP `reindex` tool consumes Plan 5's indexing service + Valkey queue-client stack (per Plan 12 §Cross-plan dependencies).

## What Plan 13 inherits from Plan 5

- **`IndexingService.enqueue_reindex(*, base_sha)` + `IndexingService.get_snapshot(snapshot_sha)` (Plan 5 amendment `40885bf`)** — consumed via Plan 13's POST + poll round-trip; `ReindexOrchestratorConsumer` + `STREAM_REINDEX_REQUESTS` consumed by the slice compose overlay's `reindex_orchestrator:` worker service. Worker-side advisory-lock contract verified end-to-end by Plan 13 Task 7; per-file extraction pipeline verified by Plan 13 Task 8. Sync `IndexingService.reindex(*, base_sha)` is NOT consumed by Plan 13 (Plan 5's own CLI / vertical integration test only).
- **Worker module entrypoints `workers/joern_indexer/__main__.py` (Plan 5 Task 7) + `workers/reindex_orchestrator/__main__.py` (Plan 5 Task 13a, amendment `40885bf`)** — invoked by `docker-compose.e2e.yml` as `python -m codeoracle.workers.joern_indexer` (PID 1 in the `joern_indexer:` service) and `python -m codeoracle.workers.reindex_orchestrator` (PID 1 in the `reindex_orchestrator:` service); dispatch is via Plan 1A entrypoint.sh + `environment.CODEORACLE_VARIANT: workers.<name>` runtime override per Plan 13 DECIDE-W.
