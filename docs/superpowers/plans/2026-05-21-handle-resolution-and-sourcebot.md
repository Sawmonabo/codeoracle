# Handle Resolution + SourcebotClient Adapter Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement `HandleResolverService` + 4 resolver strategies + `SourcebotClient` HTTP adapter — the platform's evidence-resolution layer. The Verifier (Plan 9) will call this layer at verify time to re-fetch every citation's actual content, then compare against the LLM's claimed `excerpt`. If the resolver can't fetch (`UNKNOWN`) or the content drifted from the excerpt (`REFUTED`), the Verifier blocks the claim from being marked `PROVEN`.

**Architecture:** Strategy pattern. `HandleResolverService(strategies: dict[HandleType, HandleResolverStrategy])` dispatches by `handle.type`. Four concrete strategies:

| Strategy | HandleType(s) | Underlying I/O |
|---|---|---|
| `CodeSpanResolver` | `CODE_SPAN` | `SourcebotClient.fetch_span(path, lines, ref)` |
| `DocParagraphResolver` | `DOC_PARAGRAPH` | `SourcebotClient.fetch_span(path, lines, ref)` (same shape — type differentiates trust score, not the wire path) |
| `GraphQueryResolver` | `GRAPH_QUERY` | `GraphRepository.execute_cypher(cypher, params)` (Plan 5 Task 8) — re-runs the literal Cypher query embedded in the locator and stringifies the result |
| `StubResolver` | `TRACE_SPAN`, `DB_RECORD` | Raises `UnsupportedHandleTypeError` unconditionally — slice intent classifier doesn't emit these types |

**Tech stack:** `httpx.AsyncClient` (Sourcebot HTTP); `respx` (mock); Pydantic Settings (`SourcebotConfig`); SQLAlchemy `AsyncSession` (transitive via `GraphRepository`).

---

## Cross-plan dependencies

| Imports from | Identifier | Source |
|---|---|---|
| Plan 3 | `EvidenceHandle`, `HandleType`, `Citation`, `Claim`, `Verdict` | `codeoracle.models.evidence` (Task 4 — already created) |
| Plan 3 | `CodeOracleError` base | `codeoracle.core.exceptions` (Task 2) |
| Plan 5 | `GraphRepository.execute_cypher` | `codeoracle.db.repositories.graph` (Task 8) |
| Plan 4 | `httpx` runtime dep | `pyproject.toml` (added in Plan 4 for `LLMProvider` HTTPS) |
| Plan 7 | `respx` test-only dep | `pyproject.toml` `[project.optional-dependencies].test` |
| Plan 7 | `core/config.py` env-prefix convention `CODEORACLE_<COMPONENT>__` | reference pattern, not import |
| Plan 1A | `infra/config/defaults.env` | env file Plan 1A creates; consumed by `SourcebotConfig` (Task 3) for `CODEORACLE_SOURCEBOT_ENDPOINT` |
| Plan 1A | env var: `CODEORACLE_SOURCEBOT_ENDPOINT` (defaults.env) | read by `SourcebotConfig` (Task 3) — Plan 1A L598/L695 establishes the default |
| Plan 2 | Renovate config (Plan 1 Task 12) | Plan 1 Task 12 ships the Renovate config (`.github/renovate.json5` carries `packageRules` only — NO custom/regex managers; nothing in it reads `infra/versions/pinned.env`). Plan 8 does NOT extend any regex-manager set — none exists. Plan 8 adds zero new runtime deps (Task 1); any deps it did add would land in `pyproject.toml`/compose and be covered by Renovate's standard managers. The Sourcebot image pin (Plan 2 Task 8, in `infra/versions/pinned.env`) is covered by Plan 2 Task 13's `verification-pins` CI job, not by a regex manager. |
| Plan 5 | Single-scalar-per-row agtype contract (`RETURN <scalar> AS result`) | `codeoracle.db.repositories.graph.GraphRepository.execute_cypher` — `GraphQueryResolver` (Task 11) inherits the contract and forwards it to Plan 10 |
| Plan 5 | Graph FQN scheme — dotted Python form | `codeoracle.db.repositories.graph` — consumed by `GraphQueryResolver` and the `is_safe_symbol_fqn` validator pattern |
| Plan 6 | `EmbeddingHit.distance` float | `codeoracle.services.embeddings` (Plan 6) — similarity surface re-exposed via `HandleResolverService` to the Plan 9 Verifier |
| Plan 7 | DEFERRED: `HandleResolverService` graph projection mapping (`service.name` → repo/function nodes) | declared deferred by Plan 7; consumed by Plan 8 Task 8 composition-root wiring |

| Provides to | Identifier | Used by |
|---|---|---|
| Plan 9 | `HandleResolverService` | `VerifierService.__init__(resolver: HandleResolverService)` |
| Plan 9 | `ResolverError` base | `VerifierService._resolve_one` catches → `ResolvedCitation(resolved=False)` |
| Plan 10 | `SourcebotClient` (encapsulated by `HandleResolverService` — Plan 10's only import surface) | Agent's `graph_query` / `embed_query` nodes for span-fetch on retrieved hits |
| Plan 11 | `SourcebotConfig`, `HandleResolverService` | `api/deps.py` composition root |
| Plan 5 | DEFERRED: `SourcebotClient.fetch_span()` | forward-reference reserved for Plan 5; Plan 8 Task 4 ships the adapter |
| Plan 5 | DEFERRED: `services/handle_resolution/` (`HandleResolverService`) + Sourcebot HTTP adapter | forward-reference reserved for Plan 5; Plan 8 Tasks 4–13 ship the service tree |
| Plan 9 | `UnsupportedHandleTypeError` | VerifierService catch path for `TRACE_SPAN` / `DB_RECORD` stub-rejected types |
| Plan 9 | GRAPH_QUERY excerpt contract (`stringify_cypher_rows` output shape) | Verifier byte-compares LLM-quoted excerpt against the resolver's stringified rows (mirrors plan-8.provides.8 Plan-10 row) |
| Plan 9 | `stringify_cypher_rows` | `codeoracle.services.handle_resolution.strategies.graph_query` — imported directly by Plan 9 Verifier tests |
| Plan 9 | `CodeSpanResolver` empty-resolution contract | Verifier UNKNOWN-class semantics when Sourcebot returns no source bytes (Plan 9 Invariant 12 reasoning) |
| Plan 10 | `GraphQueryResolver` | Agent `graph_query` node — re-runs LLM-quoted Cypher verbatim against `GraphRepository` |
| Plan 10 | `parse_file_locator` | Agent uses the same parser as Plan 9 for `file://path:start-end` locators |
| Plan 10 | `DocParagraphResolver` | Agent depends on the `parse_file_locator` / `DocParagraphResolver` / `CodeSpanResolver` triple |
| Plan 10 | `CodeSpanResolver` | Agent depends on the `parse_file_locator` / `DocParagraphResolver` / `CodeSpanResolver` triple |
| Plan 13 | `HandleResolverService` + `SourcebotClient` + the four handle-type strategies | Consumed by Tasks 3-6; `+wt`-SHA stripping verified at integration scale by Task 8 |

**Layer purity reminder:** Per `feedback_strict_layer_purity` memory — `SourcebotClient` is in `adapters/` (wraps an EXTERNAL system); `HandleResolverService` is in `services/` (orchestrates strategies + adapters; applies domain rules). Strategies are sub-modules of the service — they live in `services/handle_resolution/strategies/`, NOT in `adapters/`, even though they call into adapters. The directory reveals the role.

---

## Plan-level invariants

1. **Bar A.** 100% accuracy, 100% coverage on resolution. The Verifier's correctness depends on the resolver returning either the *exact actual content* or raising `ResolverError`. No silent truncation, no half-failures.

2. **`fetch_span` line-range semantics — 1-indexed, inclusive of both endpoints.** Locator `file://path:42-58` means *lines 42 through 58 inclusive*, which is `source.splitlines()[41:58]` (17 lines). Any deviation breaks the Verifier's `matches_excerpt` comparison silently. Test `test_fetch_span_returns_inclusive_one_indexed_slice` (Task 6) pins this.

3. **EOL preservation through `splitlines` / `join`.** Sourcebot's `/api/source` returns the file's exact bytes (including final newline if present). The slicer MUST NOT add or strip a trailing newline; the comparison in `VerifierService._normalize_for_comparison` (Plan 9) already does NFC + LF normalization + `rstrip` per line + outer `strip` — but normalization runs on BOTH sides, so anything the slicer drops vs. what the LLM included in `excerpt` could cause an off-by-one drift on the final line. Use `"\n".join(lines[start_idx:end_idx])` — and document that the trailing newline of the LAST line is preserved verbatim only if it was part of the original line's content (it isn't, because `splitlines()` strips line terminators). The Verifier's normalization handles the difference.

4. **`StubResolver` is registered, not omitted.** It explicitly maps `TRACE_SPAN` + `DB_RECORD` to a strategy that raises — rather than leaving them unmapped (which would also raise via `HandleResolverService.resolve`'s default branch). Why explicit: whether the intent classifier ever emits these types is a slice-level concern owned by the end-to-end slice plan (spec §8.5), not provable inside this plan; what *this* plan guarantees is that if such a handle ever reaches the resolver, the registered stub gives a *typed* failure (`UnsupportedHandleTypeError`) at the resolver boundary rather than a `KeyError` ten frames up the stack — pinned here by `test_resolve_always_raises_unsupported` (Task 12) and, for the unmapped-type path, `test_resolve_raises_unsupported_for_unmapped_handle_type` (Task 9).

5. **`HandleResolverService` is stateless.** No caching, no memoization, no batching. Each `resolve(handle)` call hits the underlying I/O. The Verifier issues calls via `asyncio.gather(*self._resolve_one(c) for c in claim.citations)` — concurrency comes from there, not from the resolver. Why: caching would hide the very drift the Verifier exists to detect (cached pre-drift value would falsely match).

6. **SourcebotClient is *single-repo per instance*.** `SourcebotConfig.default_repo` is constructor-injected; `fetch_span(path, line_range, ref)` always uses it. Multi-repo dispatch is a Plan 11+ composition-root concern (one `SourcebotClient` per indexed repo, keyed by repo identifier). The slice scope indexes only `src/codeoracle/` so one client suffices. The decision is locked here so Plan 9 / Plan 10 don't accidentally encode multi-repo logic in the resolver layer.

7. **Sourcebot HTTP error classification.** Transient (caller may retry): 5xx, 429, timeouts, connect errors. Terminal (do not retry): 4xx (404 path-not-found, 401/403 auth fail), non-JSON response body, missing `source` field. Both surface as `SourcebotError` subclasses; the resolver wraps either into `ResolverError` so the Verifier gets a uniform contract.

8. **Composition root wires HandleResolverService for the slice with ALL 5 HandleType entries — no `defaultdict` or fallback.** Wiring is:
   ```python
   strategies = {
       HandleType.CODE_SPAN:     CodeSpanResolver(sourcebot),
       HandleType.DOC_PARAGRAPH: DocParagraphResolver(sourcebot),
       HandleType.GRAPH_QUERY:   GraphQueryResolver(graph_repo),
       HandleType.TRACE_SPAN:    StubResolver(HandleType.TRACE_SPAN),
       HandleType.DB_RECORD:     StubResolver(HandleType.DB_RECORD),
   }
   ```
   Test `test_service_dispatch_covers_all_handle_types` enumerates `HandleType` and asserts every member is a key. New handle types added in Phase 2+ will break this test until wired up — the *intent*.

9. **Sourcebot wire contract is type-pinned (closes D-8.4).** The `/api/source` response shape is owned by `SourcebotSourceResponse` (Pydantic v2, `extra="forbid"`, single field `source: str`) in `adapters/sourcebot/contract.py`. The adapter validates EVERY response — mocked OR live — against the model; schema drift raises `SourcebotTerminalError` with the `"contract validation"` prefix. Mocked test fixtures construct their payload via `SourcebotSourceResponse(source=...).model_dump()`, so respx fixtures and live calls share one source of truth. Why type-pinned: prior to this pass, the response shape was hard-committed in adapter code and the 20 `respx` tests mocked the unverified shape, leaving live drift detectable only by the skip-by-default live test. With the contract model, EVERY live call gates on validation, regardless of test gating.

10. **Dirty-tree `+wt` suffix is stripped at the resolver layer (closes G21 / M-8.1).** A CodeOracle snapshot SHA may carry a `+wt<12hex>` suffix when the working tree is dirty at extraction (see core/snapshot.py). Sourcebot indexes only true git refs; passing the suffixed form to `fetch_span` causes a 404. `CodeSpanResolver` and `DocParagraphResolver` call `head_sha_only(handle.snapshot_sha)` immediately before invoking `fetch_span`, normalizing the ref to the underlying head SHA. The adapter is layer-pure — it never strips and never knows about the CodeOracle dirty-tree convention. Why services and not adapters: the `+wt` suffix encodes domain knowledge of how CodeOracle represents dirty extraction; adapters wrap external systems only. Dirty-tree deltas are not resolvable from Sourcebot's view — callers accept that resolved spans reflect the underlying head, not the working tree.

---

## Task index

- **Task 1** — Plan 8 runtime deps + pytest markers + ruff banned-api refresh
- **Task 2** — `core/exceptions.py` extension: `ResolverError`, `UnsupportedHandleTypeError`, `SourcebotError` + Transient/Terminal subclasses
- **Task 3** — `adapters/sourcebot/config.py` — `SourcebotConfig` (env-loaded)
- **Task 3a** — `adapters/sourcebot/contract.py` — `SourcebotSourceResponse` Pydantic wire-contract model (single source of truth for `/api/source` shape)
- **Task 4** — `adapters/sourcebot/__init__.py` + `client.py` — `SourcebotClient.fetch_span()` (httpx async; client-side line slicing)
- **Task 5** — SourcebotClient unit tests (`respx`-mocked): wire format, line-slice semantics, auth header, error classification
- **Task 6** — SourcebotClient live integration test (gated by `--run-live-sourcebot`)
- **Task 7** — `services/handle_resolution/strategies/base.py` — `HandleResolverStrategy` ABC + locator helpers
- **Task 8** — `services/handle_resolution/service.py` — `HandleResolverService` dispatcher
- **Task 8a** — `services/handle_resolution/snapshot_ref.py` — `head_sha_only` helper to strip `+wt<12hex>` dirty-tree suffix from snapshot SHA before Sourcebot calls
- **Task 9** — `strategies/code_span.py` — `CodeSpanResolver` (parses `file://path:start-end`)
- **Task 10** — `strategies/doc_paragraph.py` — `DocParagraphResolver` (same parser, doc-path validation)
- **Task 11** — `strategies/graph_query.py` — `GraphQueryResolver` (parses `cypher://...`, runs against `GraphRepository`)
- **Task 12** — `strategies/stub.py` — `StubResolver` (registered for TRACE_SPAN, DB_RECORD)
- **Task 13** — `services/handle_resolution/__init__.py` re-exports + service-level integration test (all 4 strategies wired, dispatch correctness, all HandleType entries covered)
- **Task 14** — Documentation + final Plan 8 commit (architecture.md cross-ref, behavioral audit, cross-plan symbol verification)

---

## Task 1: Plan 8 runtime deps + pytest markers + ruff banned-api refresh

**What this task delivers:** every Plan 8 dependency declared in `pyproject.toml` BEFORE any code lands. `httpx` + `respx` already exist (Plan 7 added them). Plan 8 needs *zero* new runtime deps — Sourcebot's HTTP API is bog-standard JSON-over-HTTP and `httpx` handles it. The task earns its place by (a) confirming that fact in writing, (b) adding the `live-sourcebot` pytest marker the Task 6 live test will register against, and (c) extending the ruff `flake8-tidy-imports.banned-api` block so direct `import sourcebot_sdk` (a hypothetical official SDK if one ever ships) would route through `codeoracle.adapters.sourcebot` rather than landing in service / api / agent code paths.

**Files:**
- Modify: `pyproject.toml` (pytest markers + ruff banned-api)
- Modify: `tests/conftest.py` (register `--run-live-sourcebot` pytest CLI option — same pattern as `--run-live-tempo` from Plan 7 Task 6 and `--run-live-llm` from Plan 4 Task 14)

- [ ] **Step 1: Confirm `httpx` + `respx` already present**

Run:

```bash
uv pip list | grep -iE "^(httpx|respx)\b"
```

Expected:

```
httpx              0.27.x
respx              0.21.x
```

(Exact patch versions from `infra/versions/pinned.env`.) If either is absent, the prior plan was not fully committed — STOP and surface to the operator.

- [ ] **Step 2: Add `live-sourcebot` pytest marker + CLI option**

Open `pyproject.toml`. Under `[tool.pytest.ini_options]` → `markers`, append:

```toml
"live_sourcebot: requires a running Sourcebot instance (--run-live-sourcebot)",
```

(Keep markers in alphabetical order — `live_anthropic`, `live_llm`, `live_openai`, `live_sourcebot`, `live_tempo`, `live_vllm`. The block was already alphabetized by Plan 7.)

- [ ] **Step 3: Add `--run-live-sourcebot` CLI flag**

Open `tests/conftest.py`. Below the existing `pytest_addoption` body (which already registers `--run-live-llm` / `--run-live-tempo`), add inside the same `pytest_addoption(parser)` function:

```python
parser.addoption(
    "--run-live-sourcebot",
    action="store_true",
    default=False,
    help="Run tests that require a live Sourcebot instance (see SOURCEBOT_LIVE_* env vars).",
)
```

And in the existing `pytest_collection_modifyitems` body that walks items and applies skip markers, add a sibling branch:

```python
if "live_sourcebot" in item.keywords and not config.getoption("--run-live-sourcebot"):
    item.add_marker(pytest.mark.skip(reason="needs --run-live-sourcebot"))
```

Place it next to the existing `live_tempo` branch so the visual symmetry stays. Resist DRY-ing this into a helper — Plan 4 already established that the per-flag branching is more readable than a table-driven approach, and changing it now would force Plan 4 + Plan 7 to refactor too. Keep the duplication; it's an *intentional* cost-of-symmetry.

- [ ] **Step 4: Extend ruff banned-api block**

Open `pyproject.toml`. Under `[tool.ruff.lint.flake8-tidy-imports.banned-api]` (the block Plan 4 introduced), append:

```toml
"sourcebot_sdk" = { msg = "Use codeoracle.adapters.sourcebot.SourcebotClient instead of importing a Sourcebot SDK directly." }
```

(There is no published `sourcebot_sdk` on PyPI today; the entry is a defensive guard against future SDK adoption silently sneaking into service code. If you find this entry annoying because you're trying to *use* a real SDK inside the adapter, add `per-file-ignores` for `src/codeoracle/adapters/sourcebot/**` — mirroring how Plan 4 unblocks `import anthropic|openai|...` inside `adapters/llm/providers/`. Don't delete the rule.)

- [ ] **Step 5: Run config sanity checks**

```bash
uv run pytest --collect-only -m live_sourcebot 2>&1 | tail -5
```

Expected output mentions `0 tests collected` *plus* a `<no tests ran>` summary — NOT an "unknown mark" warning. If pytest emits `PytestUnknownMarkWarning: Unknown pytest.mark.live_sourcebot`, the marker registration failed (most likely cause: stray comma or wrong `markers = [...]` indentation in `pyproject.toml`).

```bash
uv run ruff check src/codeoracle/ tests/
```

Expected: existing baseline only — no new findings (Plan 8 hasn't added code yet, so this is a regression-guard run).

- [ ] **Step 6: Commit**

```bash
git add pyproject.toml tests/conftest.py
git commit -m "feat(plan-8): Task 1 — pytest live_sourcebot marker + ruff banned-api guard"
```

---


## Task 2: `core/exceptions.py` extension — resolver + sourcebot error hierarchy

**What this task delivers:** the new exception types Plan 8 will raise / catch. All under the existing `CodeOracleError` root (Plan 3 Task 2). Two parallel sub-trees:

- `ResolverError(CodeOracleError)` — base for handle-resolution failures. Verifier catches this and converts to `ResolvedCitation(resolved=False)`. Subclass: `UnsupportedHandleTypeError(ResolverError)` for stub-rejected types.
- `SourcebotError(CodeOracleError)` — base for Sourcebot adapter failures. Subclasses split by retry policy: `SourcebotTransientError` (5xx/429/timeout) vs `SourcebotTerminalError` (4xx/bad-payload).

**Why two trees, not one:** the resolver layer's failure semantics are domain-level (Verifier interprets as UNKNOWN). The adapter layer's failure semantics are infrastructure-level (a worker / retry policy interprets the transient/terminal split). Folding the two would force the Verifier to know about `SourcebotTransientError`, which leaks adapter concerns into a domain service. Strict layer purity (per `feedback_strict_layer_purity` memory) — keep them separate.

**Files:**
- Modify: `src/codeoracle/core/exceptions.py`
- Modify: `tests/unit/core/test_exceptions.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/unit/core/test_exceptions.py`:

```python
"""Plan 8 Task 2 — verify the resolver + sourcebot error tree shape.

The tests pin three structural properties:
  1. Every new error subclasses CodeOracleError (so the catch-all in
     api/middleware.py — Plan 11 — captures them).
  2. UnsupportedHandleTypeError IS-A ResolverError (so VerifierService
     can catch the parent without enumerating subtypes).
  3. SourcebotTransientError and SourcebotTerminalError both IS-A
     SourcebotError (parallel structure to TempoError from Plan 7).
"""

from __future__ import annotations

import pytest

from codeoracle.core.exceptions import (
    CodeOracleError,
    ResolverError,
    SourcebotError,
    SourcebotTerminalError,
    SourcebotTransientError,
    UnsupportedHandleTypeError,
)


def test_resolver_error_subclasses_oracle_error() -> None:
    assert issubclass(ResolverError, CodeOracleError)


def test_unsupported_handle_type_subclasses_resolver_error() -> None:
    assert issubclass(UnsupportedHandleTypeError, ResolverError)
    # Also: catching ResolverError captures the subtype.
    try:
        raise UnsupportedHandleTypeError("trace_span")
    except ResolverError as e:
        assert "trace_span" in str(e)
    else:
        pytest.fail("UnsupportedHandleTypeError did not propagate as ResolverError")


def test_sourcebot_error_tree_shape() -> None:
    assert issubclass(SourcebotError, CodeOracleError)
    assert issubclass(SourcebotTransientError, SourcebotError)
    assert issubclass(SourcebotTerminalError, SourcebotError)
    # Catching the parent captures both children.
    for cls in (SourcebotTransientError, SourcebotTerminalError):
        try:
            raise cls("boom")
        except SourcebotError as e:
            assert str(e) == "boom"
        else:
            pytest.fail(f"{cls.__name__} did not propagate as SourcebotError")


def test_sourcebot_and_resolver_trees_are_disjoint() -> None:
    """A SourcebotError is NOT-A ResolverError. The resolver explicitly
    wraps SourcebotError → ResolverError; the adapter never raises ResolverError
    directly. This test makes the set separation a structural property,
    not a comment that can rot."""
    assert not issubclass(SourcebotError, ResolverError)
    assert not issubclass(ResolverError, SourcebotError)
```

- [ ] **Step 2: Run to confirm failure**

```bash
uv run pytest tests/unit/core/test_exceptions.py -v -k "resolver or sourcebot"
```

Expected: `ImportError` — none of the new symbols exist yet.

- [ ] **Step 3: Implement the new exception classes**

Open `src/codeoracle/core/exceptions.py`. The file already contains `CodeOracleError`, `ConfigError`, `DatabaseError`, `MigrationError` (Plan 3 Task 2) and was extended by Plans 4/5/6/7 with `LLMProviderError`, `WorkerError`, `TransientWorkerError`, `TerminalWorkerError`, `JoernExtractionError`, `ScipExtractionError`, `OpengrepRunError`, `EmbedExtractionError`, `GraphQueryError`, `SnapshotConflictError`, `AdvisoryLockTimeoutError`. Append (after the worker tree, before the file's bottom-of-file `__all__` if one exists — confirm by reading the file first):

```python
# -----------------------------------------------------------------------------
# Plan 8 — handle resolution + Sourcebot adapter
# -----------------------------------------------------------------------------


class ResolverError(CodeOracleError):
    """A handle resolver couldn't fetch the cited content.

    VerifierService (Plan 9) catches this and produces a
    ResolvedCitation(resolved=False, matches_excerpt=False).
    Subclasses indicate WHY resolution failed; the Verifier doesn't
    branch on subtype — it treats any ResolverError as "unknown".
    """


class UnsupportedHandleTypeError(ResolverError):
    """The HandleResolverService received a handle whose HandleType
    has no registered strategy (or is registered to StubResolver).

    Slice-scope intent classifier emits only CODE_SPAN, DOC_PARAGRAPH,
    GRAPH_QUERY. If TRACE_SPAN or DB_RECORD arrives, StubResolver
    raises this. If a new HandleType is added (Phase 2+) but not yet
    wired into composition root, HandleResolverService.resolve raises
    this via its default branch.
    """


class SourcebotError(CodeOracleError):
    """Base for all SourcebotClient-emitted exceptions.

    Distinct from ResolverError — the adapter raises SourcebotError;
    the resolver catches it and wraps as ResolverError. Direct
    SourcebotError raises must never leak past the strategy layer.
    """


class SourcebotTransientError(SourcebotError):
    """5xx response, 429 rate-limit, connect-error, timeout — caller
    MAY retry. The slice's resolver does NOT retry (each verify call
    is one-shot); a future re-verify pass or background refresh would
    use this to drive backoff."""


class SourcebotTerminalError(SourcebotError):
    """4xx response (except 429), schema mismatch (missing `source`
    field), unparsable JSON — do not retry. Almost always indicates
    a stale handle (path no longer exists at that ref) or a config
    bug (wrong base_url, wrong api_key)."""
```

- [ ] **Step 4: Confirm tests pass**

```bash
uv run pytest tests/unit/core/test_exceptions.py -v -k "resolver or sourcebot"
```

Expected: 4 passes (the four new tests above).

- [ ] **Step 5: ruff + ty check**

```bash
uv run ruff check src/codeoracle/core/exceptions.py tests/unit/core/test_exceptions.py
uv run ty check src/codeoracle/core/exceptions.py
```

Expected: no findings.

- [ ] **Step 6: Commit**

```bash
git add src/codeoracle/core/exceptions.py tests/unit/core/test_exceptions.py
git commit -m "feat(plan-8): Task 2 — ResolverError + SourcebotError exception trees"
```

---


## Task 3: `adapters/sourcebot/config.py` — `SourcebotConfig`

**What this task delivers:** the env-loaded Pydantic Settings class for the SourcebotClient — base URL, API key (SecretStr), HTTP timeout, default repo identifier. Matches the per-adapter Settings pattern from Plan 5 (`JoernConfig`) and Plan 4 (`LLMConfig`).

**Files:**
- Create: `src/codeoracle/adapters/sourcebot/__init__.py` (re-exports — populated incrementally; Task 3 only sets the import for `SourcebotConfig`)
- Create: `src/codeoracle/adapters/sourcebot/config.py`
- Create: `tests/unit/adapters/sourcebot/__init__.py`
- Create: `tests/unit/adapters/sourcebot/test_config.py`
- Modify: `infra/config/defaults.env` (default values; secrets are NOT defaulted)
- Modify: `.env.example` (matching keys, with `<set_me>` placeholders for secrets)

- [ ] **Step 1: Write the failing test**

Create `tests/unit/adapters/sourcebot/__init__.py` as an empty file.

Create `tests/unit/adapters/sourcebot/test_config.py`:

```python
"""Plan 8 Task 3 — SourcebotConfig environment-loading tests.

Pins:
  1. Env prefix matches the per-adapter convention (CODEORACLE_SOURCEBOT__).
  2. api_key is SecretStr — its __repr__ does NOT leak the value.
  3. Validation ranges: timeout must be > 0; base_url must include scheme.
  4. default_repo is a free-form string but is rejected if empty.

Why SecretStr (not plain str) for api_key: it is redaction layer 1 —
SecretStr.__repr__ masks the value in any log/repr context, regardless
of key. Plan 3's _redact_secrets processor (Task 3) is layer 2, masking
event-dict values whose key matches api_key|secret|token|password.
"""

from __future__ import annotations

import pytest
from pydantic import SecretStr, ValidationError

from codeoracle.adapters.sourcebot.config import SourcebotConfig


def _env_block(monkeypatch: pytest.MonkeyPatch, **kw: str) -> None:
    """Helper: set all required env vars + any caller overrides."""
    defaults = {
        "CODEORACLE_SOURCEBOT__BASE_URL": "http://sourcebot.test:3000",
        "CODEORACLE_SOURCEBOT__API_KEY": "test-key-123",
        "CODEORACLE_SOURCEBOT__TIMEOUT_SECONDS": "5.0",
        "CODEORACLE_SOURCEBOT__DEFAULT_REPO": "github.com/Sawmonabo/codeoracle",
    }
    defaults.update(kw)
    for k, v in defaults.items():
        monkeypatch.setenv(k, v)


def test_config_loads_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    _env_block(monkeypatch)
    cfg = SourcebotConfig()
    assert cfg.base_url == "http://sourcebot.test:3000"
    assert isinstance(cfg.api_key, SecretStr)
    assert cfg.api_key.get_secret_value() == "test-key-123"
    assert cfg.timeout_seconds == 5.0
    assert cfg.default_repo == "github.com/Sawmonabo/codeoracle"


def test_api_key_does_not_appear_in_repr(monkeypatch: pytest.MonkeyPatch) -> None:
    _env_block(monkeypatch, CODEORACLE_SOURCEBOT__API_KEY="super-secret-do-not-log")
    cfg = SourcebotConfig()
    rendered = repr(cfg) + str(cfg.api_key)
    assert "super-secret-do-not-log" not in rendered, (
        "api_key leaked via repr — SecretStr repr-masking is Plan 3 redaction layer 1"
    )


def test_base_url_requires_scheme(monkeypatch: pytest.MonkeyPatch) -> None:
    _env_block(monkeypatch, CODEORACLE_SOURCEBOT__BASE_URL="sourcebot.test:3000")
    with pytest.raises(ValidationError) as exc:
        SourcebotConfig()
    assert "must include scheme" in str(exc.value) or "url" in str(exc.value).lower()


def test_timeout_seconds_must_be_positive(monkeypatch: pytest.MonkeyPatch) -> None:
    _env_block(monkeypatch, CODEORACLE_SOURCEBOT__TIMEOUT_SECONDS="0")
    with pytest.raises(ValidationError):
        SourcebotConfig()


def test_default_repo_rejects_empty_string(monkeypatch: pytest.MonkeyPatch) -> None:
    _env_block(monkeypatch, CODEORACLE_SOURCEBOT__DEFAULT_REPO="")
    with pytest.raises(ValidationError):
        SourcebotConfig()
```

- [ ] **Step 2: Run to confirm failure**

```bash
uv run pytest tests/unit/adapters/sourcebot/test_config.py -v
```

Expected: `ImportError` — `codeoracle.adapters.sourcebot.config` does not exist yet.

- [ ] **Step 3: Implement `SourcebotConfig`**

Create `src/codeoracle/adapters/sourcebot/__init__.py` with placeholder re-exports (populated more in Task 4):

```python
"""Sourcebot adapter — HTTP wrapper for sourcebot.dev's /api/source endpoint.

Public surface:
  - SourcebotConfig (Task 3) — env-loaded settings
  - SourcebotClient (Task 4) — async client; `fetch_span(path, line_range, ref)`
  - SourcebotError / SourcebotTransientError / SourcebotTerminalError
    (re-exported from core.exceptions for adapter-import convenience —
    callers can `from codeoracle.adapters.sourcebot import SourcebotError`
    without crossing into core.exceptions directly).
"""

from codeoracle.adapters.sourcebot.config import SourcebotConfig
from codeoracle.core.exceptions import (
    SourcebotError,
    SourcebotTerminalError,
    SourcebotTransientError,
)

__all__ = [
    "SourcebotConfig",
    "SourcebotError",
    "SourcebotTerminalError",
    "SourcebotTransientError",
]
```

Create `src/codeoracle/adapters/sourcebot/config.py`:

```python
"""SourcebotConfig — env-loaded settings for the Sourcebot HTTP adapter.

Env prefix `CODEORACLE_SOURCEBOT__` (double underscore is the nested-delimiter
convention Plan 3 established; flat fields like base_url use the prefix
+ single name). All values are required at app start — no fallbacks.

The composition root (Plan 11 api/deps.py + Plan 5 workers/_base/wiring.py)
instantiates one SourcebotConfig per process and passes it into
SourcebotClient.
"""

from __future__ import annotations

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class SourcebotConfig(BaseSettings):
    """Sourcebot HTTP API configuration.

    Attributes:
      base_url: scheme://host[:port] of the Sourcebot instance.
                MUST include a scheme; the validator rejects bare host:port.
      api_key: Bearer token used in the Authorization header.
               SecretStr so the value never appears in log/repr.
      timeout_seconds: per-request HTTP timeout. Sourcebot occasionally
                       takes seconds to respond for cold repos; the default
                       (5s) is conservative — raise via env if a deployment
                       indexes very large repos.
      default_repo: the repo identifier passed as `?repo=` to /api/source.
                    Slice scope is single-repo (src/codeoracle/); multi-repo
                    is Plan 11+ composition-root work. Format follows
                    Sourcebot's own repo-naming convention, typically
                    "github.com/<org>/<repo>" — verifiable in the
                    Sourcebot UI's repo list.
    """

    model_config = SettingsConfigDict(
        env_prefix="CODEORACLE_SOURCEBOT__",
        env_nested_delimiter="__",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    base_url: str = Field(
        min_length=1,
        description="scheme://host[:port] for the Sourcebot HTTP API.",
    )
    api_key: SecretStr = Field(
        description="Bearer token for the Authorization header.",
    )
    timeout_seconds: float = Field(
        gt=0.0,
        le=120.0,
        description="Per-request HTTP timeout in seconds.",
    )
    default_repo: str = Field(
        min_length=1,
        description=(
            "Sourcebot's repo identifier, e.g. 'github.com/Sawmonabo/codeoracle'. "
            "Passed as the `repo` query param to /api/source."
        ),
    )

    @field_validator("base_url")
    @classmethod
    def _scheme_required(cls, v: str) -> str:
        if "://" not in v:
            raise ValueError("base_url must include scheme (http:// or https://)")
        # Strip trailing slash so URL composition doesn't double-slash.
        return v.rstrip("/")
```

- [ ] **Step 4: Run tests to confirm pass**

```bash
uv run pytest tests/unit/adapters/sourcebot/test_config.py -v
```

Expected: 5 passes.

- [ ] **Step 5: Add env defaults**

Modify `infra/config/defaults.env`. Append:

```bash
# Plan 8 — Sourcebot HTTP API
CODEORACLE_SOURCEBOT__BASE_URL=http://sourcebot:3000
CODEORACLE_SOURCEBOT__TIMEOUT_SECONDS=5.0
# CODEORACLE_SOURCEBOT__API_KEY is NOT defaulted — must be set per environment.
# CODEORACLE_SOURCEBOT__DEFAULT_REPO is environment-specific; set per deployment.
```

Modify `.env.example`. Append:

```bash
# Plan 8 — Sourcebot adapter
CODEORACLE_SOURCEBOT__BASE_URL=http://localhost:3000
CODEORACLE_SOURCEBOT__API_KEY=<set_me>
CODEORACLE_SOURCEBOT__TIMEOUT_SECONDS=5.0
CODEORACLE_SOURCEBOT__DEFAULT_REPO=github.com/Sawmonabo/codeoracle
```

- [ ] **Step 6: ruff + ty + commit**

```bash
uv run ruff check src/codeoracle/adapters/sourcebot/ tests/unit/adapters/sourcebot/
uv run ty check src/codeoracle/adapters/sourcebot/

git add src/codeoracle/adapters/sourcebot/__init__.py \
        src/codeoracle/adapters/sourcebot/config.py \
        tests/unit/adapters/sourcebot/__init__.py \
        tests/unit/adapters/sourcebot/test_config.py \
        infra/config/defaults.env .env.example
git commit -m "feat(plan-8): Task 3 — SourcebotConfig (env-loaded Pydantic Settings)"
```

---


## Task 3a: `adapters/sourcebot/contract.py` — `SourcebotSourceResponse` wire-contract model

**What this task delivers:** a Pydantic v2 model that pins the exact wire shape of Sourcebot's `GET /api/source` response. This model is the single source of truth for the response shape. Every call site (adapter live HTTP, respx mocked tests, live integration test) validates against it; mocked fixtures (Task 5) derive their JSON from `SourcebotSourceResponse(source=...).model_dump()`. Schema drift on the Sourcebot side surfaces here as `ValidationError` at runtime regardless of whether the test path is mocked or live (closes audit finding D-8.4). The adapter (Task 4) converts the `ValidationError` into `SourcebotTerminalError`.

**Files:**
- Create: `src/codeoracle/adapters/sourcebot/contract.py`
- Create: `tests/unit/adapters/sourcebot/test_contract.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/adapters/sourcebot/test_contract.py`:

```python
"""Plan 8 Task 3a — SourcebotSourceResponse wire-contract tests.

Pins:
  1. The model accepts the documented response shape ({"source": "<str>"}).
  2. The model accepts an empty source field (empty file is legitimate).
  3. The model rejects a missing source field.
  4. The model rejects extra unexpected fields (extra=forbid catches schema drift).
  5. The model rejects a wrong type for the source field.

This is the single source of truth for the /api/source response shape;
every call site (adapter live HTTP, respx mocked tests, live integration
test) validates against this model. Closes D-8.4.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from codeoracle.adapters.sourcebot.contract import SourcebotSourceResponse


def test_contract_accepts_valid_payload() -> None:
    model = SourcebotSourceResponse.model_validate({"source": "abc"})
    assert model.source == "abc"


def test_contract_accepts_empty_source_field() -> None:
    """An empty source string is a legitimate response (empty file)."""
    model = SourcebotSourceResponse.model_validate({"source": ""})
    assert model.source == ""


def test_contract_rejects_missing_source_field() -> None:
    with pytest.raises(ValidationError):
        SourcebotSourceResponse.model_validate({})


def test_contract_rejects_extra_unexpected_field() -> None:
    """extra=forbid catches additive-but-unverified schema changes upstream."""
    with pytest.raises(ValidationError):
        SourcebotSourceResponse.model_validate({"source": "abc", "unexpected": 1})


def test_contract_rejects_wrong_type_for_source() -> None:
    with pytest.raises(ValidationError):
        SourcebotSourceResponse.model_validate({"source": 123})
```

- [ ] **Step 2: Run to confirm failure**

```bash
uv run pytest tests/unit/adapters/sourcebot/test_contract.py -v
```

Expected: `ImportError` — `codeoracle.adapters.sourcebot.contract` does not exist yet (5 collection errors).

- [ ] **Step 3: Implement `SourcebotSourceResponse`**

Create `src/codeoracle/adapters/sourcebot/contract.py`:

```python
"""Pydantic wire-contract model for Sourcebot /api/source responses.

This model is the single source of truth for the response shape. Every call site
(adapter live HTTP, respx mocked tests, live integration test) validates against
it. Schema drift on the Sourcebot side surfaces here as ValidationError, which
the adapter converts into SourcebotTerminalError. The forbid extra-field policy
catches additive-but-unverified schema changes from upstream.

Closes audit finding D-8.4 (no preflight against unverified wire contract).
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class SourcebotSourceResponse(BaseModel):
    """Validated response body of GET /api/source.

    Sourcebot returns JSON of shape ``{"source": "<file body>"}``. Empty string
    is a legitimate value (empty file). Any other field, missing field, or
    non-str source value is rejected as schema drift.
    """

    model_config = ConfigDict(extra="forbid")

    source: str
```

- [ ] **Step 4: Confirm tests pass**

```bash
uv run pytest tests/unit/adapters/sourcebot/test_contract.py -v
```

Expected: 5 passes.

- [ ] **Step 5: Commit**

```bash
git add src/codeoracle/adapters/sourcebot/contract.py tests/unit/adapters/sourcebot/test_contract.py
git commit -m "feat(sourcebot): add SourcebotSourceResponse wire-contract model (D-8.4)"
```

---


## Task 4: `adapters/sourcebot/client.py` — `SourcebotClient.fetch_span()`

**What this task delivers:** the *only* place in the platform that speaks HTTP to Sourcebot. Wraps Sourcebot's `GET /api/source?repo=<repo>&path=<path>&ref=<ref>` endpoint. The adapter validates EVERY response against `SourcebotSourceResponse` (Task 3a) — schema enforcement is load-bearing on every call, not deferred to skip-by-default tests (closes D-8.4). The contract model treats `{"source": "<str>"}` as the only acceptable shape; any drift surfaces as `SourcebotTerminalError`. Client-side line-range slicing happens here because the endpoint has no line-range parameter (the MCP server's `read_file` tool does, but that's a separate transport — the HTTP API does not).

**Why client-side slicing:** Sourcebot's `/api/source` returns the full file body. The caller wants lines 42-58. We could:

(A) Fetch the whole file, slice in Python on `splitlines()`. **Chosen.**
(B) Run a separate Sourcebot search query that constrains to lines 42-58. Sourcebot has no such endpoint — search uses regex/glob over content, not line ranges.
(C) Mount the repo filesystem and read files directly, bypassing Sourcebot. Couples to deployment topology; abandons the abstraction. Rejected — defeats the point of having an adapter.

Option (A) costs bandwidth: a 200-line file shipped to fetch 17 lines. Sourcebot is a local LAN service in dev / a sidecar in prod, so the overhead is marginal. Caching the *file* body across line-range requests within one Verifier call would help, but is rejected by **Invariant 5** (stateless resolver — caching hides the drift the Verifier exists to detect). The Verifier's `asyncio.gather` over citations parallelizes the fetches.

**Wire-format gotchas:**

1. **Authorization header.** Sourcebot requires `Authorization: Bearer <api_key>` on every API call. The client injects it on construction; per-call code does not handle auth.
2. **Response shape.** Success → 200 with JSON body validated against `SourcebotSourceResponse` (Task 3a). The model is `{"source": "<str>"}` with `extra="forbid"` — any additional field, missing field, or wrong type surfaces as `ValidationError` which the adapter converts to `SourcebotTerminalError`. This is the load-bearing schema enforcement that closes D-8.4: validation runs on every call (mocked and live), so drift in the upstream wire shape fails the adapter immediately rather than silently passing through.
3. **404 path-not-found** is returned by Sourcebot when (a) `repo` is unknown, (b) `path` doesn't exist in `repo` at `ref`, OR (c) `ref` doesn't exist. All three are terminal — retrying won't help. The adapter does NOT branch on which sub-cause; the resolver will wrap as `ResolverError` and the Verifier renders UNKNOWN. **The error message in `SourcebotTerminalError` MUST include the path + ref so the operator can diagnose.**
4. **Empty `source` field.** If the file exists but is empty, Sourcebot returns `"source": ""`. The slicer must handle `splitlines() == []` for any line range gracefully — returns empty string. Drift detection at the Verifier level catches the surprise (the LLM's `excerpt` won't be empty).
5. **Final newline.** `splitlines()` strips line terminators; `"\n".join(lines[a:b])` reconstructs without the trailing newline of the last line. This is INTENDED behavior — see Invariant 3.

**Files:**
- Create: `src/codeoracle/adapters/sourcebot/client.py`
- Modify: `src/codeoracle/adapters/sourcebot/__init__.py` (add `SourcebotClient` to exports)

- [ ] **Step 1: Implement `SourcebotClient`**

Create `src/codeoracle/adapters/sourcebot/client.py`:

```python
"""SourcebotClient — async HTTP wrapper for Sourcebot's GET /api/source.

Lifecycle: the client holds one httpx.AsyncClient for its entire lifetime.
Callers MUST `await client.aclose()` on shutdown. The composition root
(workers/_base/wiring.py and api/deps.py) registers an aclose-on-stop
callback.

The class has exactly one public method: `fetch_span(path, line_range, ref)`.
A `repo` argument is intentionally absent: the client is constructed
with a single `default_repo` (per Invariant 6); multi-repo dispatch lives
above this layer, in composition root. Adding a `repo` kwarg here would
re-open that decision in every caller.
"""

from __future__ import annotations

import httpx
from pydantic import ValidationError

from codeoracle.adapters.sourcebot.config import SourcebotConfig
from codeoracle.adapters.sourcebot.contract import SourcebotSourceResponse
from codeoracle.core.exceptions import SourcebotTerminalError, SourcebotTransientError


class SourcebotClient:
    """Async client for Sourcebot's HTTP source-fetch API.

    Single endpoint wrapped:
        GET /api/source?repo=<repo>&path=<path>&ref=<ref>
            Authorization: Bearer <api_key>
            → 200 validated against SourcebotSourceResponse: {"source": "<full file>"}
            → 4xx terminal (path/ref not found, auth fail)
            → 5xx / 429 transient

    Every successful response body is validated against
    `SourcebotSourceResponse` (extra=forbid) so schema drift surfaces as
    `SourcebotTerminalError` on every call (closes D-8.4).

    Line-range slicing is client-side — the endpoint has no offset/limit
    on the HTTP API (only the MCP-server tool variant does, which we do
    not use here).
    """

    def __init__(self, config: SourcebotConfig) -> None:
        # Construct the underlying httpx client with the auth header
        # pre-baked so callers don't risk forgetting it.
        self._config = config
        self._client = httpx.AsyncClient(
            base_url=config.base_url,
            timeout=config.timeout_seconds,
            headers={
                "Authorization": f"Bearer {config.api_key.get_secret_value()}",
                "Accept": "application/json",
            },
        )

    async def fetch_span(
        self,
        *,
        path: str,
        line_range: tuple[int, int],
        ref: str,
    ) -> str:
        """Fetch lines [line_start, line_end] (1-indexed, inclusive) of `path`
        at git ref `ref` in the client's configured default_repo.

        Returns the joined-with-newline content of those lines. If the file
        is shorter than line_end, the slice is clamped to the file's length
        (matches Python's list-slicing semantics). If line_start is past
        EOF, returns "".

        Raises:
            ValueError: line_range is malformed (start < 1, end < start, etc.)
            SourcebotTerminalError: 4xx response, non-JSON body, or response
                that fails SourcebotSourceResponse contract validation
                (missing `source` field, extra unexpected fields, wrong type).
            SourcebotTransientError: 5xx / 429, timeout, or connect error.
        """
        line_start, line_end = line_range
        if line_start < 1:
            raise ValueError(f"line_start must be >= 1, got {line_start}")
        if line_end < line_start:
            raise ValueError(
                f"line_end ({line_end}) must be >= line_start ({line_start})"
            )
        if not path:
            raise ValueError("path must be non-empty")
        if not ref:
            raise ValueError("ref must be non-empty")

        payload = await self._get_source(path=path, ref=ref)
        source = payload.source

        lines = source.splitlines()
        # Convert 1-indexed inclusive → 0-indexed half-open.
        start_idx = line_start - 1
        end_idx = line_end  # half-open upper bound; Python clamps if > len
        return "\n".join(lines[start_idx:end_idx])

    async def aclose(self) -> None:
        """Close the underlying HTTP connection pool. Idempotent."""
        await self._client.aclose()

    # ----- Internal -----

    async def _get_source(self, *, path: str, ref: str) -> SourcebotSourceResponse:
        """Call GET /api/source. Returns the validated contract model or raises.

        The response body is validated against `SourcebotSourceResponse` —
        any drift in the wire shape (missing `source`, extra fields, wrong
        type) surfaces as `SourcebotTerminalError` (closes D-8.4).
        """
        params = {
            "repo": self._config.default_repo,
            "path": path,
            "ref": ref,
        }
        try:
            response = await self._client.get("/api/source", params=params)
        except httpx.TimeoutException as e:
            raise SourcebotTransientError(
                f"timeout calling Sourcebot /api/source (path={path!r} ref={ref!r}): {e!r}"
            ) from e
        except httpx.ConnectError as e:
            raise SourcebotTransientError(
                f"connect error calling Sourcebot /api/source (path={path!r} ref={ref!r}): {e!r}"
            ) from e
        except httpx.HTTPError as e:
            # Catches NetworkError + any other httpx error not enumerated above
            raise SourcebotTransientError(
                f"httpx error calling Sourcebot /api/source (path={path!r} ref={ref!r}): {e!r}"
            ) from e

        if response.status_code == 429 or 500 <= response.status_code < 600:
            raise SourcebotTransientError(
                f"Sourcebot /api/source returned {response.status_code} "
                f"(path={path!r} ref={ref!r}): {response.text[:200]}"
            )
        if 400 <= response.status_code < 500:
            raise SourcebotTerminalError(
                f"Sourcebot /api/source returned {response.status_code} "
                f"(path={path!r} ref={ref!r}): {response.text[:200]}"
            )
        try:
            raw = response.json()
        except ValueError as e:
            raise SourcebotTerminalError(
                f"Sourcebot /api/source returned non-JSON (path={path!r} ref={ref!r}): "
                f"{response.text[:200]}"
            ) from e
        try:
            return SourcebotSourceResponse.model_validate(raw)
        except ValidationError as e:
            raise SourcebotTerminalError(
                f"Sourcebot /api/source response failed contract validation "
                f"(path={path!r} ref={ref!r}): {e}"
            ) from e
```

- [ ] **Step 2: Update `__init__.py` re-exports**

Replace `src/codeoracle/adapters/sourcebot/__init__.py` with:

```python
"""Sourcebot adapter — HTTP wrapper for sourcebot.dev's /api/source endpoint.

Public surface:
  - SourcebotConfig — env-loaded settings
  - SourcebotClient — async client; `fetch_span(path, line_range, ref) -> str`
  - SourcebotError / SourcebotTransientError / SourcebotTerminalError
    (re-exported from core.exceptions for adapter-import convenience).
"""

from codeoracle.adapters.sourcebot.client import SourcebotClient
from codeoracle.adapters.sourcebot.config import SourcebotConfig
from codeoracle.core.exceptions import (
    SourcebotError,
    SourcebotTerminalError,
    SourcebotTransientError,
)

__all__ = [
    "SourcebotClient",
    "SourcebotConfig",
    "SourcebotError",
    "SourcebotTerminalError",
    "SourcebotTransientError",
]
```

- [ ] **Step 3: ruff + ty**

```bash
uv run ruff check src/codeoracle/adapters/sourcebot/
uv run ty check src/codeoracle/adapters/sourcebot/
```

Expected: no findings. (Tests for the client come in Task 5, so no pytest run here yet.)

- [ ] **Step 4: Commit**

```bash
git add src/codeoracle/adapters/sourcebot/client.py src/codeoracle/adapters/sourcebot/__init__.py
git commit -m "feat(plan-8): Task 4 — SourcebotClient (httpx /api/source wrapper, client-side line slice)"
```

---


## Task 5: SourcebotClient unit tests (`respx`-mocked)

**What this task delivers:** the regression-test net for every claim Task 4 made about the wire format, line-slice math, and error classification. Each Invariant from the plan header that depends on the client is pinned by a named test here.

**Test matrix (one test per row):**

| # | Test name | Pins |
|---|---|---|
| 1 | `test_fetch_span_returns_inclusive_one_indexed_slice` | Invariant 2 — `42-58` = 17 lines |
| 2 | `test_fetch_span_clamps_line_end_past_eof` | Slicing clamps gracefully when file is shorter |
| 3 | `test_fetch_span_returns_empty_when_line_start_past_eof` | Past-EOF start → "" |
| 4 | `test_fetch_span_handles_empty_source_field` | Empty file body (`"source": ""`) — any range → "" |
| 5 | `test_fetch_span_preserves_internal_newlines_no_trailing_newline` | Invariant 3 — `"\n".join(...)` semantics |
| 6 | `test_fetch_span_sends_bearer_auth_header` | Auth header attached every call |
| 7 | `test_fetch_span_sends_repo_path_ref_query_params` | Wire-format pin |
| 8 | `test_fetch_span_rejects_line_start_below_one` | Input validation |
| 9 | `test_fetch_span_rejects_line_end_below_line_start` | Input validation |
| 10 | `test_fetch_span_rejects_empty_path` | Input validation |
| 11 | `test_fetch_span_rejects_empty_ref` | Input validation |
| 12 | `test_fetch_span_404_raises_terminal` | 4xx error classification |
| 13 | `test_fetch_span_401_raises_terminal` | Auth fail = terminal |
| 14 | `test_fetch_span_500_raises_transient` | 5xx error classification |
| 15 | `test_fetch_span_429_raises_transient` | Rate-limit is transient |
| 16 | `test_fetch_span_timeout_raises_transient` | httpx.TimeoutException → Transient |
| 17 | `test_fetch_span_connect_error_raises_transient` | httpx.ConnectError → Transient |
| 18 | `test_fetch_span_non_json_body_raises_terminal` | Schema mismatch terminal |
| 19 | `test_fetch_span_missing_source_field_raises_terminal` | Schema mismatch terminal (contract validation) |
| 20 | `test_fetch_span_rejects_extra_unexpected_field_in_response` | Schema drift — extra=forbid catches additive upstream changes (D-8.4) |
| 21 | `test_fetch_span_passes_wt_suffixed_ref_through_unchanged` | Adapter is layer-pure: +wt suffix passes through; stripping is the resolver's job (G21/M-8.1) |
| 22 | `test_aclose_is_idempotent` | Lifecycle |

**Files:**
- Create: `tests/unit/adapters/sourcebot/test_client.py`

- [ ] **Step 1: Write the failing test file**

Create `tests/unit/adapters/sourcebot/test_client.py`:

```python
"""Plan 8 Task 5 — SourcebotClient unit tests (respx-mocked HTTP).

These tests pin the contracts in the plan-level Invariants and the
wire-format gotchas listed in Task 4. Each test below maps to one row
in the Task 5 test matrix.
"""

from __future__ import annotations

import httpx
import pytest
import respx
from pydantic import SecretStr

from codeoracle.adapters.sourcebot.client import SourcebotClient
from codeoracle.adapters.sourcebot.config import SourcebotConfig
from codeoracle.adapters.sourcebot.contract import SourcebotSourceResponse
from codeoracle.core.exceptions import SourcebotTerminalError, SourcebotTransientError


# -----------------------------------------------------------------------------
# Fixtures
# -----------------------------------------------------------------------------

SOURCEBOT_BASE = "http://sourcebot.test:3000"
DEFAULT_REPO = "github.com/Sawmonabo/codeoracle"
DEFAULT_REF = "abc1234"
DEFAULT_PATH = "src/codeoracle/services/verifier/service.py"
DEFAULT_API_KEY = "test-api-key-123"


def _make_config() -> SourcebotConfig:
    """Build SourcebotConfig with test values — bypasses env lookup."""
    return SourcebotConfig(
        base_url=SOURCEBOT_BASE,
        api_key=SecretStr(DEFAULT_API_KEY),
        timeout_seconds=2.0,
        default_repo=DEFAULT_REPO,
    )


@pytest.fixture
async def client():
    """Async-yield SourcebotClient — pytest-asyncio adapts plain @fixture
    on an async generator under `asyncio_mode = "auto"` (Plan 3 / Plan 7
    convention). No return annotation: this is an async *generator* (it
    yields, not returns), so `-> SourcebotClient` would be wrong and
    ty-strict flags it — matches Plan 7's TempoClient fixture."""
    c = SourcebotClient(_make_config())
    try:
        yield c
    finally:
        await c.aclose()


def make_source_payload(source: str) -> dict[str, object]:
    """Construct a valid GET /api/source response body via the contract model.

    Every mocked fixture derives from `SourcebotSourceResponse` so the
    mocked path validates exactly what the live path does (closes D-8.4).
    """
    return SourcebotSourceResponse(source=source).model_dump()


def _ten_line_file() -> str:
    """A 10-line file used by line-slice tests. No trailing newline so the
    splitlines semantics are unambiguous: `splitlines()` returns 10 items."""
    return "\n".join(f"line {i}" for i in range(1, 11))


# -----------------------------------------------------------------------------
# Happy-path slicing
# -----------------------------------------------------------------------------

@pytest.mark.asyncio
@respx.mock
async def test_fetch_span_returns_inclusive_one_indexed_slice(client: SourcebotClient) -> None:
    respx.get(f"{SOURCEBOT_BASE}/api/source").respond(json=make_source_payload(_ten_line_file()))
    result = await client.fetch_span(path=DEFAULT_PATH, line_range=(3, 5), ref=DEFAULT_REF)
    # Lines 3, 4, 5 inclusive — 3 lines.
    assert result == "line 3\nline 4\nline 5"


@pytest.mark.asyncio
@respx.mock
async def test_fetch_span_clamps_line_end_past_eof(client: SourcebotClient) -> None:
    respx.get(f"{SOURCEBOT_BASE}/api/source").respond(json=make_source_payload(_ten_line_file()))
    # File has 10 lines; ask for 8-20.
    result = await client.fetch_span(path=DEFAULT_PATH, line_range=(8, 20), ref=DEFAULT_REF)
    assert result == "line 8\nline 9\nline 10"


@pytest.mark.asyncio
@respx.mock
async def test_fetch_span_returns_empty_when_line_start_past_eof(client: SourcebotClient) -> None:
    respx.get(f"{SOURCEBOT_BASE}/api/source").respond(json=make_source_payload(_ten_line_file()))
    result = await client.fetch_span(path=DEFAULT_PATH, line_range=(50, 60), ref=DEFAULT_REF)
    assert result == ""


@pytest.mark.asyncio
@respx.mock
async def test_fetch_span_handles_empty_source_field(client: SourcebotClient) -> None:
    respx.get(f"{SOURCEBOT_BASE}/api/source").respond(json=make_source_payload(""))
    result = await client.fetch_span(path=DEFAULT_PATH, line_range=(1, 10), ref=DEFAULT_REF)
    assert result == ""


@pytest.mark.asyncio
@respx.mock
async def test_fetch_span_preserves_internal_newlines_no_trailing_newline(client: SourcebotClient) -> None:
    """splitlines / \"\\n\".join semantics: internal \\n preserved, trailing \\n
    on the file body is NOT re-added by the slicer. Invariant 3."""
    body = "a\nb\nc\n"  # trailing \n present in source
    respx.get(f"{SOURCEBOT_BASE}/api/source").respond(json=make_source_payload(body))
    result = await client.fetch_span(path=DEFAULT_PATH, line_range=(1, 3), ref=DEFAULT_REF)
    assert result == "a\nb\nc"  # NO trailing \n


# -----------------------------------------------------------------------------
# Wire format — headers + query params
# -----------------------------------------------------------------------------

@pytest.mark.asyncio
@respx.mock
async def test_fetch_span_sends_bearer_auth_header(client: SourcebotClient) -> None:
    route = respx.get(f"{SOURCEBOT_BASE}/api/source").respond(json=make_source_payload(_ten_line_file()))
    await client.fetch_span(path=DEFAULT_PATH, line_range=(1, 1), ref=DEFAULT_REF)
    assert route.called
    request = route.calls[0].request
    assert request.headers.get("authorization") == f"Bearer {DEFAULT_API_KEY}"


@pytest.mark.asyncio
@respx.mock
async def test_fetch_span_sends_repo_path_ref_query_params(client: SourcebotClient) -> None:
    route = respx.get(f"{SOURCEBOT_BASE}/api/source").respond(json=make_source_payload(_ten_line_file()))
    await client.fetch_span(path=DEFAULT_PATH, line_range=(1, 1), ref=DEFAULT_REF)
    assert route.called
    params = dict(route.calls[0].request.url.params)
    assert params["repo"] == DEFAULT_REPO
    assert params["path"] == DEFAULT_PATH
    assert params["ref"] == DEFAULT_REF


# -----------------------------------------------------------------------------
# Input validation — caller bugs
# -----------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_fetch_span_rejects_line_start_below_one(client: SourcebotClient) -> None:
    with pytest.raises(ValueError, match="line_start must be >= 1"):
        await client.fetch_span(path=DEFAULT_PATH, line_range=(0, 10), ref=DEFAULT_REF)


@pytest.mark.asyncio
async def test_fetch_span_rejects_line_end_below_line_start(client: SourcebotClient) -> None:
    with pytest.raises(ValueError, match="line_end .* must be >= line_start"):
        await client.fetch_span(path=DEFAULT_PATH, line_range=(10, 5), ref=DEFAULT_REF)


@pytest.mark.asyncio
async def test_fetch_span_rejects_empty_path(client: SourcebotClient) -> None:
    with pytest.raises(ValueError, match="path must be non-empty"):
        await client.fetch_span(path="", line_range=(1, 1), ref=DEFAULT_REF)


@pytest.mark.asyncio
async def test_fetch_span_rejects_empty_ref(client: SourcebotClient) -> None:
    with pytest.raises(ValueError, match="ref must be non-empty"):
        await client.fetch_span(path=DEFAULT_PATH, line_range=(1, 1), ref="")


# -----------------------------------------------------------------------------
# HTTP error classification
# -----------------------------------------------------------------------------

@pytest.mark.asyncio
@respx.mock
async def test_fetch_span_404_raises_terminal(client: SourcebotClient) -> None:
    respx.get(f"{SOURCEBOT_BASE}/api/source").respond(status_code=404, text="path not found")
    with pytest.raises(SourcebotTerminalError, match="404"):
        await client.fetch_span(path=DEFAULT_PATH, line_range=(1, 1), ref=DEFAULT_REF)


@pytest.mark.asyncio
@respx.mock
async def test_fetch_span_401_raises_terminal(client: SourcebotClient) -> None:
    respx.get(f"{SOURCEBOT_BASE}/api/source").respond(status_code=401, text="unauthorized")
    with pytest.raises(SourcebotTerminalError, match="401"):
        await client.fetch_span(path=DEFAULT_PATH, line_range=(1, 1), ref=DEFAULT_REF)


@pytest.mark.asyncio
@respx.mock
async def test_fetch_span_500_raises_transient(client: SourcebotClient) -> None:
    respx.get(f"{SOURCEBOT_BASE}/api/source").respond(status_code=500, text="boom")
    with pytest.raises(SourcebotTransientError, match="500"):
        await client.fetch_span(path=DEFAULT_PATH, line_range=(1, 1), ref=DEFAULT_REF)


@pytest.mark.asyncio
@respx.mock
async def test_fetch_span_429_raises_transient(client: SourcebotClient) -> None:
    respx.get(f"{SOURCEBOT_BASE}/api/source").respond(status_code=429, text="too many requests")
    with pytest.raises(SourcebotTransientError, match="429"):
        await client.fetch_span(path=DEFAULT_PATH, line_range=(1, 1), ref=DEFAULT_REF)


@pytest.mark.asyncio
@respx.mock
async def test_fetch_span_timeout_raises_transient(client: SourcebotClient) -> None:
    respx.get(f"{SOURCEBOT_BASE}/api/source").mock(
        side_effect=httpx.TimeoutException("timed out")
    )
    with pytest.raises(SourcebotTransientError, match="timeout"):
        await client.fetch_span(path=DEFAULT_PATH, line_range=(1, 1), ref=DEFAULT_REF)


@pytest.mark.asyncio
@respx.mock
async def test_fetch_span_connect_error_raises_transient(client: SourcebotClient) -> None:
    respx.get(f"{SOURCEBOT_BASE}/api/source").mock(
        side_effect=httpx.ConnectError("connection refused")
    )
    with pytest.raises(SourcebotTransientError, match="connect error"):
        await client.fetch_span(path=DEFAULT_PATH, line_range=(1, 1), ref=DEFAULT_REF)


@pytest.mark.asyncio
@respx.mock
async def test_fetch_span_non_json_body_raises_terminal(client: SourcebotClient) -> None:
    respx.get(f"{SOURCEBOT_BASE}/api/source").respond(status_code=200, text="<html>nope</html>")
    with pytest.raises(SourcebotTerminalError, match="non-JSON"):
        await client.fetch_span(path=DEFAULT_PATH, line_range=(1, 1), ref=DEFAULT_REF)


@pytest.mark.asyncio
@respx.mock
async def test_fetch_span_missing_source_field_raises_terminal(client: SourcebotClient) -> None:
    # Valid JSON but missing the `source` key — Sourcebot would never do this,
    # but if a deploy proxy strips fields we want a typed failure. Contract
    # validation surfaces the drift; extra=forbid additionally rejects the
    # `language`/`path` fields shown here.
    respx.get(f"{SOURCEBOT_BASE}/api/source").respond(
        status_code=200, json={"language": "Python", "path": DEFAULT_PATH}
    )
    with pytest.raises(SourcebotTerminalError, match="contract validation"):
        await client.fetch_span(path=DEFAULT_PATH, line_range=(1, 1), ref=DEFAULT_REF)


# -----------------------------------------------------------------------------
# Contract drift — D-8.4
# -----------------------------------------------------------------------------

@pytest.mark.asyncio
@respx.mock
async def test_fetch_span_rejects_extra_unexpected_field_in_response(client: SourcebotClient) -> None:
    """SourcebotSourceResponse uses extra=forbid — an unexpected upstream
    field surfaces as SourcebotTerminalError. The mocked-vs-live contract
    is the same model, so this drift fails identically in both paths
    (closes D-8.4)."""
    respx.get(f"{SOURCEBOT_BASE}/api/source").respond(
        status_code=200, json={"source": "x", "extra": 1}
    )
    with pytest.raises(SourcebotTerminalError, match="contract validation"):
        await client.fetch_span(path=DEFAULT_PATH, line_range=(1, 1), ref=DEFAULT_REF)


# -----------------------------------------------------------------------------
# Layer purity — adapter passes ref through unchanged (G21/M-8.1)
# -----------------------------------------------------------------------------

@pytest.mark.asyncio
@respx.mock
async def test_fetch_span_passes_wt_suffixed_ref_through_unchanged(client: SourcebotClient) -> None:
    """The adapter is layer-pure: it forwards the `ref` query param exactly
    as received. Stripping the CodeOracle ``+wt<12hex>`` dirty-tree
    suffix is the resolver's job (Task 8a / Task 9 / Task 10), NOT the
    adapter's. This test pins that the adapter does NOT silently rewrite
    the ref — if a caller passes the dirty-tree form, it goes out on the
    wire that way."""
    dirty_ref = "abc123+wt0123456789ab"
    route = respx.get(f"{SOURCEBOT_BASE}/api/source").respond(
        json=make_source_payload(_ten_line_file())
    )
    await client.fetch_span(path=DEFAULT_PATH, line_range=(1, 1), ref=dirty_ref)
    assert route.called
    params = dict(route.calls[0].request.url.params)
    assert params["ref"] == dirty_ref


# -----------------------------------------------------------------------------
# Lifecycle
# -----------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_aclose_is_idempotent() -> None:
    """Calling aclose twice MUST NOT raise — composition root may invoke it
    from both an atexit hook and an explicit shutdown path."""
    c = SourcebotClient(_make_config())
    await c.aclose()
    await c.aclose()  # second call: must be a no-op
```

- [ ] **Step 2: Run tests**

```bash
uv run pytest tests/unit/adapters/sourcebot/test_client.py -v
```

Expected: 22 passes (all rows in the test matrix). Any failure means the wire-format / error-classification assumption is wrong — STOP and reconcile against `src/codeoracle/adapters/sourcebot/client.py`.

- [ ] **Step 3: Coverage spot-check**

```bash
uv run pytest tests/unit/adapters/sourcebot/test_client.py \
    --cov=src/codeoracle/adapters/sourcebot/client \
    --cov-report=term-missing
```

Expected: ≥95% line coverage. Branches that may be uncovered:

- The `isinstance(payload, dict)` guard — Sourcebot would have to return a non-object JSON for that to fire. If coverage flags it, add a test where respx responds with `json=[1, 2, 3]`. (Don't add this defensively if coverage doesn't flag it — YAGNI.)

- [ ] **Step 4: ruff + commit**

```bash
uv run ruff check tests/unit/adapters/sourcebot/test_client.py
git add tests/unit/adapters/sourcebot/test_client.py
git commit -m "test(plan-8): Task 5 — SourcebotClient unit tests (22 cases, respx-mocked)"
```

---


## Task 6: SourcebotClient live integration test (gated by `--run-live-sourcebot`)

**What this task delivers:** one end-to-end test that proves SourcebotClient works against a real Sourcebot instance, plus the manual recipe to run it. Runs only when `--run-live-sourcebot` is passed (Task 1 wired the gate). Mirrors the live-test pattern from Plan 4 Task 14 (live-LLM gate) and Plan 7 Task 6 (live-Tempo gate).

**Live test scope (intentionally narrow):** against an *already-indexed* Sourcebot instance, verify `fetch_span` returns the actual bytes of a known file at a known commit. Not a behavior-coverage test — that's Task 5's respx matrix. This one proves the *wire shape* (the API actually exists at `/api/source`, accepts `repo`+`path`+`ref`, returns the documented JSON shape) hasn't drifted from what Task 4 + 5 assumed.

**Why a live test at all:** Plan 2 Task 8 verified Sourcebot's *image* boots; it did NOT exercise `/api/source`. If Sourcebot ever renames the endpoint, drops `source` from the response, or changes auth header expectations, the respx-mocked tests stay green and CI passes — until prod breaks. The live test is the canary.

**Why this task ships the test but NOT a CI workflow — read before implementing:** Sourcebot is *useless until a repo is indexed into it*. Indexing requires a Sourcebot `config.json` declaring connections (which repos, which host, which auth); and `/api/health` returns 200 the moment the HTTP server is up — *before* indexing finishes. A CI job that merely `docker run`s the image and polls `/api/health` would then call `/api/source` against an empty index and get a 404 on every run — a red build on day one, every night, forever.

Building the indexing pipeline (generate `config.json`, register `src/codeoracle/` as a connection, poll until the index is queryable) is **Plan 13's** job — the E2E vertical slice owns Sourcebot bring-up because the *whole platform* needs an indexed Sourcebot, not just this one test. Hand-writing a speculative `config.json` schema here — unverified against Sourcebot's current docs — would be exactly the kind of inaccurate scaffolding this plan series exists to avoid. So Plan 8 ships only what it can ship *correctly*:

1. The live test file (Step 1) — complete and correct: reads env, builds a client, fetches a known span, asserts byte-exact.
2. The manual run recipe (Step 2) — how an operator exercises it today against a hand-configured Sourcebot.

Plan 13 then wires `--run-live-sourcebot` into a nightly CI workflow once its indexing pipeline guarantees a reliably-indexed instance. Task 14's closure footer and the close-out checklist both record this as an explicit Plan 13 inheritance — not a forgotten gap.

**Files:**
- Create: `tests/integration/adapters/sourcebot/__init__.py`
- Create: `tests/integration/adapters/sourcebot/test_client_live.py`

- [ ] **Step 1: Write the live test**

Create `tests/integration/adapters/sourcebot/__init__.py` as empty.

Create `tests/integration/adapters/sourcebot/test_client_live.py`:

```python
"""Plan 8 Task 6 — SourcebotClient live integration test.

Runs only with `pytest --run-live-sourcebot` against an environment-
configured Sourcebot instance. Required env vars:

  CODEORACLE_SOURCEBOT__BASE_URL       — e.g. http://localhost:3000
  CODEORACLE_SOURCEBOT__API_KEY        — bearer token for the instance
  CODEORACLE_SOURCEBOT__DEFAULT_REPO   — repo identifier in Sourcebot
                                  (the instance MUST have this repo indexed)
  CODEORACLE_SOURCEBOT_LIVE_REF        — git ref to fetch from (commit / branch / tag)
  CODEORACLE_SOURCEBOT_LIVE_PATH       — known file path inside the repo
  CODEORACLE_SOURCEBOT_LIVE_LINES      — line range as "<start>-<end>", inclusive
  CODEORACLE_SOURCEBOT_LIVE_EXPECTED   — exact expected slice content (newline-joined)

The expected-content assertion is intentionally byte-exact: if the wire
format ever drifts (e.g., Sourcebot rewrites line endings, prepends BOM,
returns base64-encoded source), the assertion fires loudly with a diff
the operator can read.
"""

from __future__ import annotations

import os

import httpx
import pytest
from pydantic import SecretStr

from codeoracle.adapters.sourcebot.client import SourcebotClient
from codeoracle.adapters.sourcebot.config import SourcebotConfig
from codeoracle.adapters.sourcebot.contract import SourcebotSourceResponse


pytestmark = pytest.mark.live_sourcebot


def _require_env(name: str) -> str:
    val = os.environ.get(name)
    if not val:
        pytest.skip(f"live-sourcebot test requires env {name!r}")
    return val


@pytest.fixture
async def live_client():
    # async-generator fixture — no return annotation (see Task 5's `client`).
    cfg = SourcebotConfig(
        base_url=_require_env("CODEORACLE_SOURCEBOT__BASE_URL"),
        api_key=SecretStr(_require_env("CODEORACLE_SOURCEBOT__API_KEY")),
        timeout_seconds=10.0,
        default_repo=_require_env("CODEORACLE_SOURCEBOT__DEFAULT_REPO"),
    )
    c = SourcebotClient(cfg)
    try:
        yield c
    finally:
        await c.aclose()


async def test_fetch_span_against_live_sourcebot(live_client: SourcebotClient) -> None:
    ref = _require_env("CODEORACLE_SOURCEBOT_LIVE_REF")
    path = _require_env("CODEORACLE_SOURCEBOT_LIVE_PATH")
    line_spec = _require_env("CODEORACLE_SOURCEBOT_LIVE_LINES")
    try:
        start_str, end_str = line_spec.split("-", 1)
        line_range = (int(start_str), int(end_str))
    except ValueError as e:
        pytest.skip(f"CODEORACLE_SOURCEBOT_LIVE_LINES must be 'start-end', got {line_spec!r}: {e}")

    expected = _require_env("CODEORACLE_SOURCEBOT_LIVE_EXPECTED")

    actual = await live_client.fetch_span(path=path, line_range=line_range, ref=ref)
    assert actual == expected, (
        f"\nExpected:\n{expected!r}\n"
        f"\nActual:\n{actual!r}\n"
        f"\nDiff is a Sourcebot wire-format drift — investigate /api/source response shape."
    )

    # Explicit contract validation on the live wire shape — the live test
    # is no longer the only line of defense for D-8.4. The mocked tests
    # (Task 5) and this live test now share `SourcebotSourceResponse` as
    # their single source of truth; any drift fails BOTH simultaneously.
    base_url = _require_env("CODEORACLE_SOURCEBOT__BASE_URL").rstrip("/")
    api_key = _require_env("CODEORACLE_SOURCEBOT__API_KEY")
    repo = _require_env("CODEORACLE_SOURCEBOT__DEFAULT_REPO")
    async with httpx.AsyncClient(timeout=10.0) as raw:
        response = await raw.get(
            f"{base_url}/api/source",
            params={"repo": repo, "path": path, "ref": ref},
            headers={"Authorization": f"Bearer {api_key}", "Accept": "application/json"},
        )
    response.raise_for_status()
    # If Sourcebot's wire shape drifts (extra field, missing source, wrong
    # type), ValidationError fires here with a structured diff. The exact
    # same model gates the adapter (Task 4) and the mocked tests (Task 5),
    # so live and mocked paths cannot disagree on what "valid" means.
    SourcebotSourceResponse.model_validate(response.json())
```

- [ ] **Step 2: Verify the gate, and record the manual run recipe**

This step creates no file. It (a) confirms the `--run-live-sourcebot` gate works, and (b) records the recipe for running the live test by hand — the only way to run it until Plan 13 automates CI.

First, confirm a normal test run deselects the live test:

```bash
uv run pytest tests/integration/adapters/sourcebot/test_client_live.py -v
```

Expected: `1 deselected` (or `1 skipped`) — and never a connection attempt. Task 1's `--run-live-sourcebot` flag gates it; without the flag, pytest must not touch a live Sourcebot.

To actually run it, you need a Sourcebot instance reachable at `CODEORACLE_SOURCEBOT__BASE_URL` with `CODEORACLE_SOURCEBOT__DEFAULT_REPO` *already indexed and queryable*. A freshly-booted Sourcebot answers `/api/health` with 200 long before indexing finishes — so confirm the index directly first:

```bash
# Preflight — confirm the repo is indexed. 200 + a JSON `source` field = indexed;
# 404 = not indexed yet (wait for indexing, or fix the Sourcebot config.json).
curl -fsS -H "Authorization: Bearer $CODEORACLE_SOURCEBOT__API_KEY" \
  "$CODEORACLE_SOURCEBOT__BASE_URL/api/source?repo=$CODEORACLE_SOURCEBOT__DEFAULT_REPO&path=src/codeoracle/adapters/sourcebot/client.py&ref=$(git rev-parse HEAD)" \
  | head -c 200
```

Once the preflight returns JSON, run the gated test:

```bash
export CODEORACLE_SOURCEBOT__BASE_URL=http://localhost:3000
export CODEORACLE_SOURCEBOT__API_KEY=<your-key>
export CODEORACLE_SOURCEBOT__DEFAULT_REPO=github.com/Sawmonabo/codeoracle
export CODEORACLE_SOURCEBOT_LIVE_REF=$(git rev-parse HEAD)
export CODEORACLE_SOURCEBOT_LIVE_PATH=src/codeoracle/adapters/sourcebot/client.py
export CODEORACLE_SOURCEBOT_LIVE_LINES=1-3
export CODEORACLE_SOURCEBOT_LIVE_EXPECTED='"""SourcebotClient — async HTTP wrapper for Sourcebot'\''s GET /api/source.

Lifecycle: the client holds one httpx.AsyncClient for its entire lifetime.'

uv run pytest tests/integration/adapters/sourcebot/test_client_live.py --run-live-sourcebot -v
```

(Shell escaping for the docstring's `'s` uses the closing-quote trick shown above.) If no Sourcebot instance is available, record this step as "gate verified; live run skipped — no instance" and move on — the test ships either way.

**CI automation — deferred to Plan 13.** No `.github/workflows/live-sourcebot.yml` ships in Plan 8 (see this task's "Why this task ships the test but NOT a CI workflow" note above). A nightly workflow needs a *reliably-indexed* Sourcebot, and standing up that index — generate `config.json`, register the `src/codeoracle/` connection, poll until queryable — is Plan 13's E2E bring-up work. Plan 13 will add the workflow, reusing the exact `CODEORACLE_SOURCEBOT__*` / `CODEORACLE_SOURCEBOT_LIVE_*` env layout this test already reads.

- [ ] **Step 3: Commit**

```bash
git add tests/integration/adapters/sourcebot/__init__.py \
        tests/integration/adapters/sourcebot/test_client_live.py
git commit -m "test(plan-8): Task 6 — live-sourcebot integration test (gated)"
```

---


## Task 7: `services/handle_resolution/strategies/base.py` — ABC + locator parser

**What this task delivers:** the `HandleResolverStrategy` abstract base class that all four concrete resolvers inherit from, plus the shared `parse_file_locator(locator) -> (path, (line_start, line_end))` helper used by both `CodeSpanResolver` (Task 9) and `DocParagraphResolver` (Task 10).

**Why a shared parser:** the spec's CODE_SPAN and DOC_PARAGRAPH locators both follow `file://<path>:<start>-<end>` (per spec lines 1288-1290). Duplicating the parser in each strategy would risk drift; sharing it in `base.py` keeps the format definition single-sourced. The cypher:// parser (`GraphQueryResolver`) is different and stays local to Task 11.

**Why ABC + abstractmethod (not Protocol):** the spec at §6.3 mandates inheritance (`class CodeSpanResolver(HandleResolverStrategy)`). ABCs give us runtime `isinstance(s, HandleResolverStrategy)` checks if Task 8's service ever needs them; Protocols don't. Either would work; spec wins.

**Files:**
- Create: `src/codeoracle/services/handle_resolution/__init__.py` (placeholder — populated in Task 13)
- Create: `src/codeoracle/services/handle_resolution/strategies/__init__.py` (re-exports — populated incrementally as strategies land)
- Create: `src/codeoracle/services/handle_resolution/strategies/base.py`
- Create: `tests/unit/services/handle_resolution/__init__.py`
- Create: `tests/unit/services/handle_resolution/strategies/__init__.py`
- Create: `tests/unit/services/handle_resolution/strategies/test_base.py`

- [ ] **Step 1: Write the failing test**

Create the empty `__init__.py` files (4 of them, all empty).

Create `tests/unit/services/handle_resolution/strategies/test_base.py`:

```python
"""Plan 8 Task 7 — HandleResolverStrategy ABC + parse_file_locator helper.

Pins:
  1. HandleResolverStrategy is abstract — instantiating raises TypeError.
  2. Subclasses missing `resolve` cannot instantiate.
  3. parse_file_locator accepts well-formed `file://path:start-end`.
  4. parse_file_locator rejects: missing scheme, wrong scheme, no path,
     no line range, malformed line range, start > end, start < 1.
  5. parse_file_locator preserves the path verbatim including leading
     slashes and dots — Sourcebot expects paths relative to the repo
     root, no normalization.
"""

from __future__ import annotations

import pytest

from codeoracle.services.handle_resolution.strategies.base import (
    HandleResolverStrategy,
    parse_file_locator,
)


# -----------------------------------------------------------------------------
# ABC enforcement
# -----------------------------------------------------------------------------

def test_handle_resolver_strategy_cannot_be_instantiated_directly() -> None:
    with pytest.raises(TypeError, match="abstract"):
        HandleResolverStrategy()  # type: ignore[abstract]


def test_subclass_without_resolve_cannot_instantiate() -> None:
    class IncompleteResolver(HandleResolverStrategy):
        pass

    with pytest.raises(TypeError, match="abstract"):
        IncompleteResolver()  # type: ignore[abstract]


def test_subclass_with_resolve_instantiates_cleanly() -> None:
    class MinimalResolver(HandleResolverStrategy):
        async def resolve(self, handle):  # type: ignore[no-untyped-def]
            return "ok"

    instance = MinimalResolver()
    assert isinstance(instance, HandleResolverStrategy)


# -----------------------------------------------------------------------------
# parse_file_locator — happy path
# -----------------------------------------------------------------------------

def test_parse_file_locator_extracts_path_and_lines() -> None:
    path, (start, end) = parse_file_locator(
        "file://src/codeoracle/services/verifier/service.py:42-58"
    )
    assert path == "src/codeoracle/services/verifier/service.py"
    assert (start, end) == (42, 58)


def test_parse_file_locator_accepts_single_line_range() -> None:
    """`file://path:7-7` is a valid one-line range."""
    path, (start, end) = parse_file_locator("file://x.py:7-7")
    assert path == "x.py"
    assert (start, end) == (7, 7)


def test_parse_file_locator_preserves_path_verbatim_no_normalization() -> None:
    """Slashes, dots, and parent-dir tokens stay as-is — Sourcebot's repo
    is the source of truth on what paths are valid."""
    path, _ = parse_file_locator("file://./a/b/../c.py:1-2")
    assert path == "./a/b/../c.py"


# -----------------------------------------------------------------------------
# parse_file_locator — validation failures
# -----------------------------------------------------------------------------

def test_parse_file_locator_rejects_missing_scheme() -> None:
    with pytest.raises(ValueError, match="scheme"):
        parse_file_locator("src/codeoracle/foo.py:1-5")


def test_parse_file_locator_rejects_wrong_scheme() -> None:
    with pytest.raises(ValueError, match="scheme"):
        parse_file_locator("cypher://MATCH (n) RETURN n")


def test_parse_file_locator_rejects_no_line_range() -> None:
    with pytest.raises(ValueError, match="line range"):
        parse_file_locator("file://src/foo.py")


def test_parse_file_locator_rejects_malformed_line_range() -> None:
    with pytest.raises(ValueError, match="line range"):
        parse_file_locator("file://src/foo.py:abc")


def test_parse_file_locator_rejects_line_start_below_one() -> None:
    with pytest.raises(ValueError, match="line_start"):
        parse_file_locator("file://src/foo.py:0-5")


def test_parse_file_locator_rejects_end_before_start() -> None:
    with pytest.raises(ValueError, match="line_end"):
        parse_file_locator("file://src/foo.py:10-5")


def test_parse_file_locator_rejects_empty_path() -> None:
    with pytest.raises(ValueError, match="path"):
        parse_file_locator("file://:1-5")
```

- [ ] **Step 2: Run to confirm failure**

```bash
uv run pytest tests/unit/services/handle_resolution/strategies/test_base.py -v
```

Expected: `ImportError` — module does not exist.

- [ ] **Step 3: Implement the ABC + parser**

Create `src/codeoracle/services/handle_resolution/__init__.py` with a placeholder docstring (Task 13 populates the re-exports):

```python
"""Handle resolution service — dispatches an EvidenceHandle to the
correct per-type resolver strategy. Populated incrementally:

  - Task 7  base.py   — HandleResolverStrategy ABC + locator helpers
  - Task 8  service.py — HandleResolverService (dispatcher)
  - Tasks 9-12 — concrete strategies (CodeSpan / DocParagraph / GraphQuery / Stub)
  - Task 13 __init__.py — final public re-exports

For now this file is just a marker — see the strategies subpackage and
service.py for the actual classes.
"""
```

Create `src/codeoracle/services/handle_resolution/strategies/__init__.py` (also incrementally populated):

```python
"""Per-HandleType resolver strategies.

Public surface populated incrementally across Plan 8 tasks 7-12; the
final composite is exposed via codeoracle.services.handle_resolution (Task 13)
not from here directly. Internal imports between strategies should use
absolute paths (`from codeoracle.services.handle_resolution.strategies.base
import HandleResolverStrategy`).
"""
```

Create `src/codeoracle/services/handle_resolution/strategies/base.py`:

```python
"""HandleResolverStrategy ABC + shared locator parser.

Every concrete strategy (CodeSpan / DocParagraph / GraphQuery / Stub)
inherits from `HandleResolverStrategy` and implements `resolve(handle)`.
The shared `parse_file_locator` helper exists because the CODE_SPAN and
DOC_PARAGRAPH handle types use the same `file://<path>:<start>-<end>`
locator format per the spec at §6.1 / §6.3.

The cypher:// scheme for GRAPH_QUERY is parsed locally in
strategies/graph_query.py (it's structurally different — no path, no
line range — and folding it into a single parser would force a useless
union return type).
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from codeoracle.models.evidence import EvidenceHandle


class HandleResolverStrategy(ABC):
    """Resolve one EvidenceHandle to its current actual content.

    Implementations MUST raise either ResolverError (or subclass) on
    failure, or return the resolved content as a string. They MUST NOT
    return None or empty-on-failure — the Verifier interprets an empty
    string as "file existed but the requested lines are past EOF" (a
    legitimate REFUTED case if the LLM's excerpt was non-empty), not as
    "could not resolve".
    """

    @abstractmethod
    async def resolve(self, handle: EvidenceHandle) -> str:
        """Fetch and return the actual content the handle points to.

        Raises:
            ResolverError (or subclass) on resolution failure.
        """


# -----------------------------------------------------------------------------
# Shared locator parser — file://path:start-end
# -----------------------------------------------------------------------------

_FILE_SCHEME = "file://"


def parse_file_locator(locator: str) -> tuple[str, tuple[int, int]]:
    """Parse `file://<path>:<line_start>-<line_end>` into (path, (start, end)).

    Both line numbers are 1-indexed and inclusive of the endpoint.

    Raises:
        ValueError: if the locator is missing the file:// scheme, has no
            line range, has a malformed range, or violates 1 <= start <= end.
    """
    if not locator.startswith(_FILE_SCHEME):
        raise ValueError(
            f"locator missing 'file://' scheme: {locator!r}"
        )

    rest = locator[len(_FILE_SCHEME):]

    # Path and line range are separated by the LAST ':' — paths theoretically
    # can contain colons (rare, but Windows drive letters used to do this).
    # Using rsplit isolates the parsing right-to-left and avoids spurious
    # splits when the path contains a ':'.
    if ":" not in rest:
        raise ValueError(
            f"locator missing line range — expected 'file://<path>:<start>-<end>': {locator!r}"
        )
    path, range_part = rest.rsplit(":", 1)
    if not path:
        raise ValueError(f"locator path is empty: {locator!r}")

    if "-" not in range_part:
        raise ValueError(
            f"line range must be 'start-end', got {range_part!r} in {locator!r}"
        )
    start_str, end_str = range_part.split("-", 1)
    try:
        line_start = int(start_str)
        line_end = int(end_str)
    except ValueError as e:
        raise ValueError(
            f"line range must be integers, got {range_part!r} in {locator!r}: {e}"
        ) from e

    if line_start < 1:
        raise ValueError(
            f"line_start must be >= 1, got {line_start} in {locator!r}"
        )
    if line_end < line_start:
        raise ValueError(
            f"line_end ({line_end}) must be >= line_start ({line_start}) in {locator!r}"
        )

    return path, (line_start, line_end)
```

- [ ] **Step 4: Run tests**

```bash
uv run pytest tests/unit/services/handle_resolution/strategies/test_base.py -v
```

Expected: 12 passes.

- [ ] **Step 5: ruff + ty + commit**

```bash
uv run ruff check src/codeoracle/services/handle_resolution/ tests/unit/services/handle_resolution/
uv run ty check src/codeoracle/services/handle_resolution/

git add src/codeoracle/services/handle_resolution/__init__.py \
        src/codeoracle/services/handle_resolution/strategies/__init__.py \
        src/codeoracle/services/handle_resolution/strategies/base.py \
        tests/unit/services/handle_resolution/__init__.py \
        tests/unit/services/handle_resolution/strategies/__init__.py \
        tests/unit/services/handle_resolution/strategies/test_base.py
git commit -m "feat(plan-8): Task 7 — HandleResolverStrategy ABC + parse_file_locator"
```

---


## Task 8: `services/handle_resolution/service.py` — `HandleResolverService`

**What this task delivers:** the dispatcher that the Verifier (Plan 9) calls. Takes a `dict[HandleType, HandleResolverStrategy]` at construction. On `resolve(handle)`, looks up `strategies[handle.type]` and delegates. If the type is unmapped, raises `UnsupportedHandleTypeError`.

**Why a dict-of-strategies (not isinstance dispatch):** the spec at §6.3 mandates this shape. Mechanical benefits: composition root owns the mapping, swap a strategy by config change, test by injecting a `dict[HandleType, MagicMock]`. Mechanical drawback: the `KeyError → UnsupportedHandleTypeError` translation lives in the dispatcher, not in a subclass-based fallback chain. Invariant 4 acknowledges this.

**Why no caching:** Invariant 5 — caching would hide drift the Verifier exists to detect.

**Files:**
- Create: `src/codeoracle/services/handle_resolution/service.py`
- Create: `tests/unit/services/handle_resolution/test_service.py`

- [ ] **Step 1: Write the failing test**

Create `tests/unit/services/handle_resolution/test_service.py`:

```python
"""Plan 8 Task 8 — HandleResolverService dispatch tests.

The service has zero domain logic of its own — it's pure dispatch. Tests
pin:
  1. Constructor accepts dict[HandleType, HandleResolverStrategy].
  2. resolve() routes to the strategy whose key matches handle.type.
  3. Unmapped handle.type raises UnsupportedHandleTypeError.
  4. The strategy receives the original handle (not a copy / re-wrap).
  5. Exceptions from the strategy propagate unchanged (no wrapping).
  6. registered_handle_types() exposes the dispatch dict's keys.
"""

from __future__ import annotations

import pytest

from codeoracle.core.exceptions import ResolverError, UnsupportedHandleTypeError
from codeoracle.models.evidence import EvidenceHandle, HandleType
from codeoracle.services.handle_resolution.service import HandleResolverService
from codeoracle.services.handle_resolution.strategies.base import HandleResolverStrategy


class _RecordingStrategy(HandleResolverStrategy):
    """Test double — records every handle it was asked to resolve."""

    def __init__(self, return_value: str = "ok", raise_exc: Exception | None = None) -> None:
        self.calls: list[EvidenceHandle] = []
        self._return_value = return_value
        self._raise_exc = raise_exc

    async def resolve(self, handle: EvidenceHandle) -> str:
        self.calls.append(handle)
        if self._raise_exc is not None:
            raise self._raise_exc
        return self._return_value


def _handle(handle_type: HandleType, locator: str = "file://x.py:1-1") -> EvidenceHandle:
    return EvidenceHandle(type=handle_type, locator=locator, snapshot_sha="abc1234")


@pytest.mark.asyncio
async def test_resolve_dispatches_to_strategy_keyed_by_handle_type() -> None:
    code_span = _RecordingStrategy(return_value="code content")
    doc_para = _RecordingStrategy(return_value="doc content")
    svc = HandleResolverService(strategies={
        HandleType.CODE_SPAN: code_span,
        HandleType.DOC_PARAGRAPH: doc_para,
    })

    result_code = await svc.resolve(_handle(HandleType.CODE_SPAN))
    assert result_code == "code content"
    assert len(code_span.calls) == 1
    assert len(doc_para.calls) == 0

    result_doc = await svc.resolve(_handle(HandleType.DOC_PARAGRAPH))
    assert result_doc == "doc content"
    assert len(code_span.calls) == 1
    assert len(doc_para.calls) == 1


@pytest.mark.asyncio
async def test_resolve_passes_handle_through_unchanged() -> None:
    """The strategy MUST receive the original handle instance — no copy,
    no re-wrap. Mutating downstream is impossible (EvidenceHandle is
    frozen — see Plan 3 Task 4), but identity equality lets tests pin
    'no wrapper class in between'."""
    strategy = _RecordingStrategy()
    svc = HandleResolverService(strategies={HandleType.CODE_SPAN: strategy})
    original = _handle(HandleType.CODE_SPAN, locator="file://exact/path.py:7-9")

    await svc.resolve(original)
    assert strategy.calls[0] is original  # identity, not just equality


@pytest.mark.asyncio
async def test_resolve_raises_unsupported_for_unmapped_handle_type() -> None:
    """If the dispatch dict has no entry, UnsupportedHandleTypeError fires.
    This is the safety net for new HandleType values added in the enum
    but not yet wired into composition root."""
    svc = HandleResolverService(strategies={
        HandleType.CODE_SPAN: _RecordingStrategy(),
    })
    with pytest.raises(UnsupportedHandleTypeError) as exc:
        await svc.resolve(_handle(HandleType.GRAPH_QUERY))
    assert "graph_query" in str(exc.value)


@pytest.mark.asyncio
async def test_resolve_propagates_strategy_exceptions_unchanged() -> None:
    """If a strategy raises, the service MUST NOT wrap or swallow.
    The Verifier wants the real cause (so logs preserve it)."""
    original_exc = ResolverError("sourcebot returned 404")
    strategy = _RecordingStrategy(raise_exc=original_exc)
    svc = HandleResolverService(strategies={HandleType.CODE_SPAN: strategy})

    with pytest.raises(ResolverError) as exc:
        await svc.resolve(_handle(HandleType.CODE_SPAN))
    assert exc.value is original_exc


@pytest.mark.asyncio
async def test_resolve_propagates_non_resolver_exceptions_unchanged() -> None:
    """Strategy bugs (e.g., bare KeyError, TypeError) propagate too —
    the service doesn't try to "helpfully" mask them as ResolverError.
    Plan 9 VerifierService catches only ResolverError; anything else
    bubbles to the API layer's error handler (Plan 11)."""
    strategy = _RecordingStrategy(raise_exc=RuntimeError("bug"))
    svc = HandleResolverService(strategies={HandleType.CODE_SPAN: strategy})
    with pytest.raises(RuntimeError, match="bug"):
        await svc.resolve(_handle(HandleType.CODE_SPAN))


def test_constructor_accepts_empty_strategies_dict() -> None:
    """Edge case: an empty dict is a legal construction (every resolve()
    will raise UnsupportedHandleTypeError, which is the correct behavior
    for an unconfigured service)."""
    svc = HandleResolverService(strategies={})
    assert svc is not None


def test_registered_handle_types_reflects_constructor_dict() -> None:
    """`registered_handle_types()` exposes the dispatch dict's keys as a
    set — the public accessor the composition-root coverage test
    (Task 13) uses instead of reaching into private `_strategies`."""
    svc = HandleResolverService(strategies={
        HandleType.CODE_SPAN: _RecordingStrategy(),
        HandleType.DOC_PARAGRAPH: _RecordingStrategy(),
    })
    assert svc.registered_handle_types() == {HandleType.CODE_SPAN, HandleType.DOC_PARAGRAPH}
    assert HandleResolverService(strategies={}).registered_handle_types() == set()
```

- [ ] **Step 2: Run to confirm failure**

```bash
uv run pytest tests/unit/services/handle_resolution/test_service.py -v
```

Expected: `ImportError`.

- [ ] **Step 3: Implement `HandleResolverService`**

Create `src/codeoracle/services/handle_resolution/service.py`:

```python
"""HandleResolverService — dispatches an EvidenceHandle to the right strategy.

Construction:
    svc = HandleResolverService(strategies={
        HandleType.CODE_SPAN:     CodeSpanResolver(sourcebot),
        HandleType.DOC_PARAGRAPH: DocParagraphResolver(sourcebot),
        HandleType.GRAPH_QUERY:   GraphQueryResolver(graph_repo),
        HandleType.TRACE_SPAN:    StubResolver(HandleType.TRACE_SPAN),
        HandleType.DB_RECORD:     StubResolver(HandleType.DB_RECORD),
    })

The composition root (Plan 11 api/deps.py) is the only place this dict
is built. Tests construct their own subsets.
"""

from __future__ import annotations

from codeoracle.core.exceptions import UnsupportedHandleTypeError
from codeoracle.models.evidence import EvidenceHandle, HandleType
from codeoracle.services.handle_resolution.strategies.base import HandleResolverStrategy


class HandleResolverService:
    """Stateless dispatcher: handle.type -> registered strategy.

    Stateless per Invariant 5 (no caching, no batching). Every call hits
    the strategy's underlying I/O. Concurrency is the Verifier's job —
    it calls `asyncio.gather(*self._resolve_one(c) for c in citations)`.
    """

    def __init__(self, *, strategies: dict[HandleType, HandleResolverStrategy]) -> None:
        self._strategies = strategies

    async def resolve(self, handle: EvidenceHandle) -> str:
        """Route `handle` to its strategy and return the resolved content.

        Raises:
            UnsupportedHandleTypeError: no strategy registered for handle.type.
            ResolverError (or subclass): strategy could not fetch.
            Any other exception raised by the strategy: propagated as-is.
        """
        strategy = self._strategies.get(handle.type)
        if strategy is None:
            raise UnsupportedHandleTypeError(
                f"no resolver strategy registered for HandleType.{handle.type.name} "
                f"(value={handle.type.value!r}); composition root MUST wire all "
                f"HandleType members — see Plan 8 Invariant 8"
            )
        return await strategy.resolve(handle)

    def registered_handle_types(self) -> set[HandleType]:
        """Return the set of HandleType values with a registered strategy.

        The composition-root coverage test (Plan 8 Task 13) calls this to
        assert every HandleType is wired — without reaching into the
        private `_strategies` dict, which a rename would silently break.
        """
        return set(self._strategies)
```

- [ ] **Step 4: Run tests**

```bash
uv run pytest tests/unit/services/handle_resolution/test_service.py -v
```

Expected: 7 passes.

- [ ] **Step 5: ruff + ty + commit**

```bash
uv run ruff check src/codeoracle/services/handle_resolution/service.py
uv run ty check src/codeoracle/services/handle_resolution/service.py

git add src/codeoracle/services/handle_resolution/service.py \
        tests/unit/services/handle_resolution/test_service.py
git commit -m "feat(plan-8): Task 8 — HandleResolverService dispatcher"
```

---


## Task 8a: `services/handle_resolution/snapshot_ref.py` — `head_sha_only` helper

**What this task delivers:** the `head_sha_only` helper that strips the CodeOracle `+wt<12hex>` dirty-tree suffix from a snapshot SHA before it leaves the service layer for any Sourcebot call. CodeOracle's snapshot SHA includes the suffix when the working tree is dirty at extraction time (see `core/snapshot.py`); Sourcebot indexes only true git refs, so the suffix MUST be stripped before invoking `fetch_span`. This helper isolates that domain knowledge in `services/` — the adapter stays pure (Task 5 pins the layer-purity property). Closes audit finding G21 / M-8.1.

**Why a free function (not a class):** pure string transformation, no state, no I/O — explicit exception to the project OOP preference per `CLAUDE.md` and project memory.

**Files:**
- Create: `src/codeoracle/services/handle_resolution/snapshot_ref.py`
- Create: `tests/unit/services/handle_resolution/test_snapshot_ref.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/services/handle_resolution/test_snapshot_ref.py`:

```python
"""Plan 8 Task 8a — head_sha_only snapshot-ref helper tests.

Pins:
  1. The CodeOracle dirty-tree form `<head>+wt<12hex>` is stripped to `<head>`.
  2. A clean SHA (no suffix) passes through unchanged.
  3. A non-canonical suffix length (e.g. `+wt0123`) is NOT recognized and
     passes through unchanged — strict-match guards against accidental
     stripping of unrelated +wt-like substrings.
  4. The match is lowercase-only per the CodeOracle format; an uppercase
     `+WT...` does NOT match and passes through unchanged.
  5. Empty string passes through unchanged (no crash).
"""

from __future__ import annotations

from codeoracle.services.handle_resolution.snapshot_ref import head_sha_only


def test_head_sha_only_strips_dirty_tree_wt_suffix() -> None:
    assert head_sha_only("abc123+wt0123456789ab") == "abc123"


def test_head_sha_only_passes_clean_sha_unchanged() -> None:
    assert head_sha_only("abc123") == "abc123"


def test_head_sha_only_passes_short_wt_suffix_unchanged() -> None:
    """CodeOracle format is `+wt` + exactly 12 hex chars; anything else is
    not the CodeOracle format and must pass through verbatim."""
    assert head_sha_only("abc123+wt0123") == "abc123+wt0123"


def test_head_sha_only_passes_uppercase_wt_unchanged() -> None:
    """Strict-lowercase match — CodeOracle writes `+wt`, not `+WT`."""
    assert head_sha_only("abc123+WT0123456789ab") == "abc123+WT0123456789ab"


def test_head_sha_only_passes_empty_string_unchanged() -> None:
    assert head_sha_only("") == ""
```

- [ ] **Step 2: Run to confirm failure**

```bash
uv run pytest tests/unit/services/handle_resolution/test_snapshot_ref.py -v
```

Expected: `ImportError` — `codeoracle.services.handle_resolution.snapshot_ref` does not exist yet (5 collection errors).

- [ ] **Step 3: Implement `head_sha_only`**

Create `src/codeoracle/services/handle_resolution/snapshot_ref.py`:

```python
"""Snapshot-ref normalization for handle resolution.

CodeOracle's snapshot SHA encoding includes a ``+wt<12hex>`` suffix when
the working tree is dirty at extraction time (see core/snapshot.py). Sourcebot
indexes only true git refs, so resolvers must strip the suffix before invoking
the adapter. The underlying head SHA is what Sourcebot has indexed; dirty-tree
deltas are unresolvable from Sourcebot's view.

Free-function exception to the OOP preference: pure string transformation with
no state and no I/O. Documented in CLAUDE.md and project memory.

Closes audit finding G21 / M-8.1 (dirty-tree SHA produces 404 against Sourcebot).
"""

from __future__ import annotations

import re

_WT_SUFFIX_RE = re.compile(r"^([0-9a-f]+)\+wt[0-9a-f]{12}$")


def head_sha_only(snapshot_sha: str) -> str:
    """Strip the ``+wt<12hex>`` dirty-tree suffix from a CodeOracle snapshot SHA.

    Returns the underlying head SHA when the suffix matches exactly; otherwise
    returns the input unchanged. Match is strict-lowercase per CodeOracle format.

    Args:
        snapshot_sha: A CodeOracle snapshot SHA. May or may not carry the dirty-tree
            suffix.

    Returns:
        The head SHA suitable for use as a Sourcebot git ref.
    """
    match = _WT_SUFFIX_RE.match(snapshot_sha)
    return match.group(1) if match else snapshot_sha
```

- [ ] **Step 4: Confirm tests pass**

```bash
uv run pytest tests/unit/services/handle_resolution/test_snapshot_ref.py -v
```

Expected: 5 passes.

- [ ] **Step 5: Commit**

```bash
git add src/codeoracle/services/handle_resolution/snapshot_ref.py tests/unit/services/handle_resolution/test_snapshot_ref.py
git commit -m "feat(handle-resolution): add head_sha_only snapshot-ref helper (G21/M-8.1)"
```

---


## Task 9: `strategies/code_span.py` — `CodeSpanResolver`

**What this task delivers:** the strategy for `HandleType.CODE_SPAN`. Parses the `file://path:start-end` locator (via `parse_file_locator` from Task 7), delegates to `SourcebotClient.fetch_span`, wraps any `SourcebotError` as `ResolverError`. A pass-through layer, but each branch must be tested so the wrap-and-rethrow contract doesn't drift.

**Why wrap SourcebotError → ResolverError:** Invariant 7 / Cross-plan-deps table. The Verifier catches `ResolverError`; it does not (and should not) know about `SourcebotError`. The wrap is the boundary between adapter-layer and domain-layer error vocabularies.

**Files:**
- Create: `src/codeoracle/services/handle_resolution/strategies/code_span.py`
- Create: `tests/unit/services/handle_resolution/strategies/test_code_span.py`
- Modify: `src/codeoracle/services/handle_resolution/strategies/__init__.py` (export `CodeSpanResolver`)

- [ ] **Step 1: Write the failing test**

Create `tests/unit/services/handle_resolution/strategies/test_code_span.py`:

```python
"""Plan 8 Task 9 — CodeSpanResolver unit tests.

Pins:
  1. Parses file://path:start-end locator → SourcebotClient.fetch_span call.
  2. Passes handle.snapshot_sha (after head_sha_only stripping) as the
     `ref` arg to fetch_span.
  3. Returns the fetched content unchanged (no normalization at this layer
     — Plan 9 VerifierService owns normalization).
  4. Wraps SourcebotTerminalError as ResolverError (preserves cause chain).
  5. Wraps SourcebotTransientError as ResolverError too.
  6. Re-raises ValueError from parse_file_locator without wrapping (caller
     bug → bubble up; Verifier interprets ValueErrors as 500-class bugs).
  7. Strips the CodeOracle `+wt<12hex>` dirty-tree suffix from the snapshot
     SHA before calling fetch_span (G21 / M-8.1).
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from codeoracle.core.exceptions import (
    ResolverError,
    SourcebotTerminalError,
    SourcebotTransientError,
)
from codeoracle.models.evidence import EvidenceHandle, HandleType
from codeoracle.services.handle_resolution.strategies.code_span import CodeSpanResolver


def _handle(locator: str, snapshot_sha: str = "abc1234") -> EvidenceHandle:
    return EvidenceHandle(type=HandleType.CODE_SPAN, locator=locator, snapshot_sha=snapshot_sha)


@pytest.fixture
def mock_sourcebot() -> AsyncMock:
    m = AsyncMock()
    m.fetch_span = AsyncMock(return_value="resolved content")
    return m


@pytest.mark.asyncio
async def test_resolve_calls_sourcebot_with_parsed_path_lines_and_ref(mock_sourcebot: AsyncMock) -> None:
    resolver = CodeSpanResolver(sourcebot=mock_sourcebot)
    handle = _handle("file://src/codeoracle/foo.py:10-15", snapshot_sha="deadbee")

    result = await resolver.resolve(handle)

    assert result == "resolved content"
    mock_sourcebot.fetch_span.assert_awaited_once_with(
        path="src/codeoracle/foo.py",
        line_range=(10, 15),
        ref="deadbee",
    )


@pytest.mark.asyncio
async def test_resolve_wraps_sourcebot_terminal_as_resolver_error(mock_sourcebot: AsyncMock) -> None:
    mock_sourcebot.fetch_span.side_effect = SourcebotTerminalError("404 path not found")
    resolver = CodeSpanResolver(sourcebot=mock_sourcebot)

    with pytest.raises(ResolverError) as exc:
        await resolver.resolve(_handle("file://src/codeoracle/foo.py:10-15"))
    # Cause chain preserved — Verifier logs both layers
    assert isinstance(exc.value.__cause__, SourcebotTerminalError)
    assert "404 path not found" in str(exc.value.__cause__)


@pytest.mark.asyncio
async def test_resolve_wraps_sourcebot_transient_as_resolver_error(mock_sourcebot: AsyncMock) -> None:
    mock_sourcebot.fetch_span.side_effect = SourcebotTransientError("timeout")
    resolver = CodeSpanResolver(sourcebot=mock_sourcebot)

    with pytest.raises(ResolverError):
        await resolver.resolve(_handle("file://src/codeoracle/foo.py:10-15"))


@pytest.mark.asyncio
async def test_resolve_does_not_wrap_value_error_from_locator_parser(mock_sourcebot: AsyncMock) -> None:
    """parse_file_locator raises ValueError on a malformed locator.
    That's a caller-side bug (LLM emitted an invalid handle) — bubble it
    up; the Verifier surfaces it as 500-class so the agent learns its
    output schema is wrong."""
    resolver = CodeSpanResolver(sourcebot=mock_sourcebot)
    # Spec says EvidenceHandle.locator must include 'scheme://' — so a
    # bare 'src/foo.py:1-5' would fail Pydantic validation, not get here.
    # We test the next layer: a 'file://' locator with no line range.
    bad_handle = EvidenceHandle(
        type=HandleType.CODE_SPAN,
        locator="file://src/foo.py",  # Pydantic accepts (has '://'), parser rejects (no ':' for line range)
        snapshot_sha="abc1234",
    )

    with pytest.raises(ValueError, match="line range"):
        await resolver.resolve(bad_handle)

    # Confirm: fetch_span was NEVER called — parser short-circuits
    mock_sourcebot.fetch_span.assert_not_called()


@pytest.mark.asyncio
async def test_resolve_does_not_normalize_returned_content(mock_sourcebot: AsyncMock) -> None:
    """If Sourcebot returns 'line 1\\nline 2', the resolver MUST return
    that exact string. Plan 9 VerifierService applies NFC + EOL + rstrip
    normalization at COMPARISON time, on BOTH sides. If we normalized
    here, the excerpt-vs-actual diff would lose information."""
    mock_sourcebot.fetch_span.return_value = "   line with trailing spaces   \n\r\n"
    resolver = CodeSpanResolver(sourcebot=mock_sourcebot)

    result = await resolver.resolve(_handle("file://x.py:1-1"))
    assert result == "   line with trailing spaces   \n\r\n"


@pytest.mark.asyncio
async def test_code_span_resolver_strips_wt_suffix_before_fetch(mock_sourcebot: AsyncMock) -> None:
    """CodeOracle dirty-tree SHAs (`<head>+wt<12hex>`) are NOT valid git refs.
    The resolver MUST strip the `+wt` suffix via `head_sha_only` before
    invoking the adapter; Sourcebot indexes only the head SHA. The adapter
    itself stays pure (Task 5 pins this) — the strip is a service-layer
    concern. Closes G21 / M-8.1."""
    resolver = CodeSpanResolver(sourcebot=mock_sourcebot)
    handle = _handle("file://src/codeoracle/foo.py:10-15", snapshot_sha="abc123+wt0123456789ab")

    await resolver.resolve(handle)

    mock_sourcebot.fetch_span.assert_awaited_once_with(
        path="src/codeoracle/foo.py",
        line_range=(10, 15),
        ref="abc123",
    )
```

- [ ] **Step 2: Run to confirm failure**

```bash
uv run pytest tests/unit/services/handle_resolution/strategies/test_code_span.py -v
```

Expected: `ImportError`.

- [ ] **Step 3: Implement `CodeSpanResolver`**

Create `src/codeoracle/services/handle_resolution/strategies/code_span.py`:

```python
"""CodeSpanResolver — resolves file://path:start-end handles via Sourcebot.

Mapped to HandleType.CODE_SPAN by composition root. The DocParagraphResolver
(Task 10) uses the same locator format + same SourcebotClient; both classes
exist as distinct types so trust-matrix scoring (Plan 9) can differentiate
code vs doc citations.
"""

from __future__ import annotations

from codeoracle.adapters.sourcebot.client import SourcebotClient
from codeoracle.core.exceptions import ResolverError, SourcebotError
from codeoracle.models.evidence import EvidenceHandle
from codeoracle.services.handle_resolution.snapshot_ref import head_sha_only
from codeoracle.services.handle_resolution.strategies.base import (
    HandleResolverStrategy,
    parse_file_locator,
)


class CodeSpanResolver(HandleResolverStrategy):
    """Resolves CODE_SPAN handles by fetching the cited lines from Sourcebot.

    The resolver does NOT normalize returned content — the Verifier
    (Plan 9) normalizes at comparison time (NFC + LF + per-line rstrip)
    on BOTH the excerpt and the resolved content. Normalizing here
    would erase information the Verifier wants to see.

    Snapshot SHA normalization: the resolver strips the CodeOracle
    ``+wt<12hex>`` dirty-tree suffix via `head_sha_only` (Task 8a) before
    passing the ref to Sourcebot — Sourcebot indexes only true git refs
    (closes G21 / M-8.1).
    """

    def __init__(self, sourcebot: SourcebotClient) -> None:
        self._sourcebot = sourcebot

    async def resolve(self, handle: EvidenceHandle) -> str:
        path, line_range = parse_file_locator(handle.locator)
        try:
            return await self._sourcebot.fetch_span(
                path=path,
                line_range=line_range,
                ref=head_sha_only(handle.snapshot_sha),
            )
        except SourcebotError as e:
            raise ResolverError(
                f"CodeSpanResolver could not fetch {handle.locator!r} at "
                f"snapshot {handle.snapshot_sha!r}: {e}"
            ) from e
```

- [ ] **Step 4: Update strategies/__init__.py re-exports**

Replace `src/codeoracle/services/handle_resolution/strategies/__init__.py`:

```python
"""Per-HandleType resolver strategies.

Public surface populated incrementally across Plan 8 tasks 7-12.
"""

from codeoracle.services.handle_resolution.strategies.base import (
    HandleResolverStrategy,
    parse_file_locator,
)
from codeoracle.services.handle_resolution.strategies.code_span import CodeSpanResolver

__all__ = [
    "CodeSpanResolver",
    "HandleResolverStrategy",
    "parse_file_locator",
]
```

(Tasks 10-12 will append `DocParagraphResolver`, `GraphQueryResolver`, `StubResolver` to `__all__` and the import block.)

- [ ] **Step 5: Run tests**

```bash
uv run pytest tests/unit/services/handle_resolution/strategies/test_code_span.py -v
```

Expected: 6 passes.

- [ ] **Step 6: ruff + ty + commit**

```bash
uv run ruff check src/codeoracle/services/handle_resolution/strategies/code_span.py
uv run ty check src/codeoracle/services/handle_resolution/strategies/code_span.py

git add src/codeoracle/services/handle_resolution/strategies/code_span.py \
        src/codeoracle/services/handle_resolution/strategies/__init__.py \
        tests/unit/services/handle_resolution/strategies/test_code_span.py
git commit -m "feat(plan-8): Task 9 — CodeSpanResolver (file://path:lines -> Sourcebot)"
```

---


## Task 10: `strategies/doc_paragraph.py` — `DocParagraphResolver`

**What this task delivers:** the strategy for `HandleType.DOC_PARAGRAPH`. Mechanically identical to `CodeSpanResolver` (same `file://path:start-end` locator format per spec line 1290, same `SourcebotClient.fetch_span` call). The distinct class exists so TrustMatrixService (Plan 9) scores doc-paragraph citations at `3` and code-span at `10` — driven by `HandleType`, not by the resolution mechanism.

**Why not a single class with two HandleType registrations:** the dispatch dict keys on HandleType — both would route to the same instance fine. But spec §3.2 lists `doc_paragraph.py` and `code_span.py` as separate files, and the architecture's roadmap (§6 of architecture.md) anticipates DocParagraph eventually routing to a different store (e.g., a docs-only vector index or a wiki API). Keeping them as siblings preserves the extension seam.

**Why no path-extension whitelist:** spec example at line 1290 shows `doc_paragraph` pointing at `service.py` (a Python file's *docstring* range). Restricting DocParagraph to `*.md` would forbid a legitimate citation pattern. The HandleType signals **what kind of evidence this is** (documentation), not **what file extension it lives in** — the LLM picks the type when composing the claim.

**Files:**
- Create: `src/codeoracle/services/handle_resolution/strategies/doc_paragraph.py`
- Create: `tests/unit/services/handle_resolution/strategies/test_doc_paragraph.py`
- Modify: `src/codeoracle/services/handle_resolution/strategies/__init__.py` (add `DocParagraphResolver`)

- [ ] **Step 1: Write the failing test**

Create `tests/unit/services/handle_resolution/strategies/test_doc_paragraph.py`:

```python
"""Plan 8 Task 10 — DocParagraphResolver unit tests.

DocParagraph and CodeSpan resolvers share the same locator format and
underlying I/O — but they MUST be distinct classes (spec §3.2) so
TrustMatrixService can score by HandleType.

Tests pin:
  1. Same locator parsing + SourcebotClient call shape as CodeSpan.
  2. Accepts a `.py` path under `doc_paragraph` type — type does NOT
     constrain file extension (spec §6.1 example uses service.py for DOC_PARAGRAPH).
  3. Same SourcebotError → ResolverError wrapping.
  4. Resolver does NOT branch on HandleType internally — its sole purpose
     is the doc-flavored I/O path.
  5. Strips the CodeOracle `+wt<12hex>` dirty-tree suffix from the snapshot
     SHA before calling fetch_span (G21 / M-8.1) — same behavior as
     CodeSpanResolver.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from codeoracle.core.exceptions import ResolverError, SourcebotTerminalError
from codeoracle.models.evidence import EvidenceHandle, HandleType
from codeoracle.services.handle_resolution.strategies.doc_paragraph import (
    DocParagraphResolver,
)


def _handle(locator: str, snapshot_sha: str = "abc1234") -> EvidenceHandle:
    return EvidenceHandle(
        type=HandleType.DOC_PARAGRAPH, locator=locator, snapshot_sha=snapshot_sha
    )


@pytest.fixture
def mock_sourcebot() -> AsyncMock:
    m = AsyncMock()
    m.fetch_span = AsyncMock(return_value="doc content")
    return m


@pytest.mark.asyncio
async def test_resolve_calls_sourcebot_with_path_lines_and_ref(mock_sourcebot: AsyncMock) -> None:
    resolver = DocParagraphResolver(sourcebot=mock_sourcebot)
    handle = _handle("file://docs/architecture.md:42-50", snapshot_sha="abc1234")

    result = await resolver.resolve(handle)

    assert result == "doc content"
    mock_sourcebot.fetch_span.assert_awaited_once_with(
        path="docs/architecture.md",
        line_range=(42, 50),
        ref="abc1234",
    )


@pytest.mark.asyncio
async def test_resolve_accepts_python_path_under_doc_paragraph_type(mock_sourcebot: AsyncMock) -> None:
    """Spec line 1290 example uses 'file://src/.../service.py:15-20' under
    DOC_PARAGRAPH (the LLM is citing a docstring inside a code file).
    The resolver MUST NOT reject this."""
    resolver = DocParagraphResolver(sourcebot=mock_sourcebot)
    handle = _handle("file://src/codeoracle/services/verifier/service.py:15-20")
    result = await resolver.resolve(handle)
    assert result == "doc content"
    mock_sourcebot.fetch_span.assert_awaited_once()


@pytest.mark.asyncio
async def test_resolve_wraps_sourcebot_error_as_resolver_error(mock_sourcebot: AsyncMock) -> None:
    mock_sourcebot.fetch_span.side_effect = SourcebotTerminalError("404")
    resolver = DocParagraphResolver(sourcebot=mock_sourcebot)
    with pytest.raises(ResolverError) as exc:
        await resolver.resolve(_handle("file://docs/foo.md:1-2"))
    assert isinstance(exc.value.__cause__, SourcebotTerminalError)


@pytest.mark.asyncio
async def test_doc_paragraph_resolver_strips_wt_suffix_before_fetch(mock_sourcebot: AsyncMock) -> None:
    """Same +wt-stripping behavior as CodeSpanResolver — DocParagraph
    citations may carry the CodeOracle dirty-tree suffix and Sourcebot does
    not understand it. The strip happens in the resolver (via
    `head_sha_only`), keeping the adapter layer-pure. Closes G21 / M-8.1."""
    resolver = DocParagraphResolver(sourcebot=mock_sourcebot)
    handle = _handle("file://docs/architecture.md:42-50", snapshot_sha="abc123+wt0123456789ab")

    await resolver.resolve(handle)

    mock_sourcebot.fetch_span.assert_awaited_once_with(
        path="docs/architecture.md",
        line_range=(42, 50),
        ref="abc123",
    )
```

- [ ] **Step 2: Run to confirm failure**

```bash
uv run pytest tests/unit/services/handle_resolution/strategies/test_doc_paragraph.py -v
```

Expected: `ImportError`.

- [ ] **Step 3: Implement `DocParagraphResolver`**

Create `src/codeoracle/services/handle_resolution/strategies/doc_paragraph.py`:

```python
"""DocParagraphResolver — resolves file://path:start-end handles via Sourcebot.

Mapped to HandleType.DOC_PARAGRAPH. Mechanically identical to
CodeSpanResolver (Task 9); the distinction matters at TrustMatrix
scoring time (docs score 3, code scores 10 per architecture §16).

Future extension seam: when a docs-only vector index lands (architecture
§6 roadmap), DocParagraphResolver routes there; CodeSpanResolver stays
on Sourcebot. Separate classes today => zero refactoring later.
"""

from __future__ import annotations

from codeoracle.adapters.sourcebot.client import SourcebotClient
from codeoracle.core.exceptions import ResolverError, SourcebotError
from codeoracle.models.evidence import EvidenceHandle
from codeoracle.services.handle_resolution.snapshot_ref import head_sha_only
from codeoracle.services.handle_resolution.strategies.base import (
    HandleResolverStrategy,
    parse_file_locator,
)


class DocParagraphResolver(HandleResolverStrategy):
    """Resolves DOC_PARAGRAPH handles via Sourcebot (same I/O path as
    CodeSpanResolver). The type distinction drives downstream trust
    scoring, not the resolution mechanism.

    Snapshot SHA normalization: same as CodeSpanResolver — the
    ``+wt<12hex>`` dirty-tree suffix is stripped via `head_sha_only`
    (Task 8a) before the ref reaches Sourcebot (closes G21 / M-8.1).
    """

    def __init__(self, sourcebot: SourcebotClient) -> None:
        self._sourcebot = sourcebot

    async def resolve(self, handle: EvidenceHandle) -> str:
        path, line_range = parse_file_locator(handle.locator)
        try:
            return await self._sourcebot.fetch_span(
                path=path,
                line_range=line_range,
                ref=head_sha_only(handle.snapshot_sha),
            )
        except SourcebotError as e:
            raise ResolverError(
                f"DocParagraphResolver could not fetch {handle.locator!r} at "
                f"snapshot {handle.snapshot_sha!r}: {e}"
            ) from e
```

- [ ] **Step 4: Update strategies/__init__.py**

Update `src/codeoracle/services/handle_resolution/strategies/__init__.py`:

```python
"""Per-HandleType resolver strategies."""

from codeoracle.services.handle_resolution.strategies.base import (
    HandleResolverStrategy,
    parse_file_locator,
)
from codeoracle.services.handle_resolution.strategies.code_span import CodeSpanResolver
from codeoracle.services.handle_resolution.strategies.doc_paragraph import (
    DocParagraphResolver,
)

__all__ = [
    "CodeSpanResolver",
    "DocParagraphResolver",
    "HandleResolverStrategy",
    "parse_file_locator",
]
```

- [ ] **Step 5: Run tests + ruff + ty + commit**

```bash
uv run pytest tests/unit/services/handle_resolution/strategies/test_doc_paragraph.py -v
uv run ruff check src/codeoracle/services/handle_resolution/strategies/doc_paragraph.py
uv run ty check src/codeoracle/services/handle_resolution/strategies/doc_paragraph.py

git add src/codeoracle/services/handle_resolution/strategies/doc_paragraph.py \
        src/codeoracle/services/handle_resolution/strategies/__init__.py \
        tests/unit/services/handle_resolution/strategies/test_doc_paragraph.py
git commit -m "feat(plan-8): Task 10 — DocParagraphResolver (HandleType-distinct sibling of CodeSpan)"
```

---


## Task 11: `strategies/graph_query.py` — `GraphQueryResolver`

**What this task delivers:** the strategy for `HandleType.GRAPH_QUERY`. Parses `cypher://<cypher-body>` locator, runs the literal Cypher against `GraphRepository.execute_cypher` (Plan 5 Task 8), and converts the resulting rows to a deterministic string the Verifier can compare against the LLM's `excerpt`.

**Why a literal Cypher in the locator (per spec line 1289):** the locator IS the proof step. The LLM commits to the exact query the platform will re-run — there's no "I meant approximately X" gap. If the LLM emits `cypher://MATCH (c:Symbol {snapshot_sha:$snapshot_sha})-[:CALLS]->(t {fqn:'...verify'}) RETURN c.fqn AS result` and an excerpt of `["codeoracle.api.routes.query.handle_explain_symbol"]`, the resolver re-runs that exact Cypher — binding `$snapshot_sha` from the handle (see the snapshot-pin enforcement below) — and stringifies the result; the Verifier compares to the excerpt. Any change to either the query or the graph's content shows up as drift.

**Stringification policy:** AGE's `agtype` returns are JSON-shaped but quoted (per Plan 5 Task 8 — `str(rows[0]["v"]).strip('"')`). The resolver:

1. Runs `execute_cypher(cypher_body, column_spec="result agtype")`.
2. Receives `list[dict[str, object]]` — one dict per row, each carrying the single `result` column (declared `result agtype` in step 1).
3. Extracts `row["result"]` per row.
4. Strips one outer pair of agtype quotes from each value (AGE wraps strings in `"..."`).
5. JSON-serializes the value list with `separators=(", ", ": ")` so the comparison is byte-stable across runs. (No `sort_keys` is passed: the serialized value is always a flat list of scalars under the scalar-per-row constraint below, so row order alone fixes the output; key sorting would only matter for map values, which graph_query does not produce.)

**Scalar-per-row constraint — and why the resolver does not recurse.** `_unwrap_agtype_value` strips one outer pair of agtype quotes from a single value; it does *not* recurse into lists or maps. That is deliberate. Plan 3's `connect` listener loads the AGE *extension* (`LOAD 'age'` + `search_path`) so the `agtype` SQL type resolves — but it registers **no Python-side type codec**, and no later plan adds one. `agtype` values therefore arrive in whatever raw form psycopg yields for a type it has no adapter for. Plan 5's `GraphRepository` confirms this empirically: its `execute_cypher` docstring states AGE returns agtype values as quoted JSON strings, and *every* call site decodes defensively with `str(value).strip('"')` rather than trusting a typed shape. For **scalar** values that defensive decode is well-understood. For **non-scalar** agtype — does a map arrive as a Python `dict`? a JSON string? with inner string values quoted how? — the contract is pinned down **nowhere** in Plans 3–7: no plan returns or decodes a map. Writing recursive unwrap logic against that unverified contract would be speculative scaffolding — exactly the kind of code this project's quality bar (memory `project-code-quality-bar`) forbids.

So the slice constrains the citation instead: **the agent (Plan 10) MUST emit Cypher that returns a single column named `result`, one scalar value per row** — `RETURN c.fqn AS result` (a string per row, N rows), `RETURN count(c) AS result` (one int), `RETURN count(c) > 0 AS result` (one bool). Maps (`RETURN c` — a whole node) and collections (`RETURN collect(c.fqn)` — a list in one row) are **unsupported until a future plan verifies the psycopg+AGE decoding contract for non-scalar agtype against a real database** and, only then, makes `_unwrap_agtype_value` recurse. This is a scope boundary, stated plainly — not a latent bug. `GraphRepository` (Plan 5 Task 8) shares the same boundary.

**`column_spec="result agtype"` is passed explicitly even though it matches Plan 5's `execute_cypher` default.** This is a deliberate redundancy: the resolver's `stringify_cypher_rows` does `row.get("result")` — it is *coupled* to the column being named `result`. Passing the spec explicitly makes that coupling visible at the call site and survives a future change to Plan 5's default. `test_resolve_runs_cypher_body_verbatim` asserts the explicit value.

### Snapshot pin is enforced STRUCTURALLY, not via the prompt (closes D-8.1)

Snapshot isolation in the graph is a per-node `snapshot_sha` property — Plan 5's parser stamps it on every `:Symbol` / `:File`, and `GraphRepository.find_callers` / `link_cross_file_symbols` constrain on `{snapshot_sha:$snapshot_sha}`. The graph_query citation carries the cited content, but the **pin that scopes it to one snapshot must be honoured by the platform, not trusted to the LLM-authored Cypher.** The code-span and doc resolvers honour `handle.snapshot_sha` structurally (they pass it as the Sourcebot `ref`); graph evidence must match that guarantee or it is both an asymmetry and an injection surface — a mis-generated or adversarial Cypher with no snapshot constraint (or a *different* literal snapshot) would read across snapshots.

The resolver closes this in three steps, all before any cross-snapshot read can occur:

1. **Strip the dirty-tree suffix.** `head_sha_only(handle.snapshot_sha)` normalizes a `<head>+wt<12hex>` SHA to its head — the graph indexes nodes under the head `snapshot_sha`, exactly as Sourcebot indexes the head ref. This keeps G21 consistent across all three I/O-bearing resolvers (the `+wt` form is never resolvable; consumers accept that resolved evidence reflects the underlying head).
2. **Assert the pin CONSTRAINS, not merely appears (`_assert_snapshot_pinned`).** The Cypher body MUST bind the `$snapshot_sha` parameter inside a node-property map — `{... snapshot_sha:$snapshot_sha ...}` — exactly the shape the agent prompt mandates; a body that omits that constraint is rejected with `ResolverError` *before* execution. This is deliberately stronger than a token-presence test: a body that merely *mentions* `$snapshot_sha` without using it as a filter (e.g. `MATCH (c:Symbol) WHERE true OR c.snapshot_sha = $snapshot_sha RETURN c.fqn AS result`) reads every snapshot and is rejected. Comments are stripped first, so a `//`- or `/* */`-commented constraint cannot satisfy the check. The resolver fails **closed** — it does not silently run an unscoped query.
3. **Bind the value structurally.** `execute_cypher(cypher_body, {"snapshot_sha": head_sha}, column_spec="result agtype")` passes the pin as an AGE/Postgres parameter map (the `params` argument Plan 5 Task 8 already supports). The value originates from the handle, never from the query text, so the LLM-authored body **cannot substitute a different snapshot** — a query can only ever read the snapshot the handle names.

Why parameter-binding-plus-constraint-assertion rather than splicing a `WHERE n.snapshot_sha = '<sha>'` clause into the body programmatically: the LLM-authored Cypher is arbitrary (multiple `MATCH` clauses, `OPTIONAL MATCH`, `WITH` pipelines), and there is no Cypher AST parser in the stack. Textually splicing a clause into untrusted Cypher is itself brittle and injection-prone. Asserting that a required parameter is *used as a node-property constraint* and supplying its value structurally needs no parser, is fail-closed, and keeps value provenance with the platform, not the model. The agent (Plan 10) MUST therefore author every graph_query as `MATCH (... {snapshot_sha:$snapshot_sha}) ... RETURN <scalar> AS result` — the prompt names the placeholder, and the resolver enforces the constraint form. `test_resolve_rejects_cypher_missing_snapshot_param`, `test_resolve_rejects_cypher_mentioning_snapshot_param_without_constraint`, `test_resolve_rejects_cypher_with_commented_out_snapshot_constraint`, and `test_resolve_binds_head_snapshot_sha_as_param` pin the constraint-form check and the structural bind together.

**Residual the regex cannot close (accepted risk, owner named).** The check guarantees that *at least one* matched node carries the `$snapshot_sha` constraint; it does not — and cannot, without a parser — guarantee that *every independent traversal root* does. The guarantee is "≥1 pinned anchor." That suffices when the rest of the query reaches out from that anchor (a pattern like `MATCH (c:Symbol {snapshot_sha:$snapshot_sha})-[:CALLS]->(t)` is safe even though `t` is unpinned — `t` is reachable only from the already-scoped `c`). The residual is the case of an **additional, independent MATCH/UNION arm that introduces its own unanchored root** — e.g. a second `MATCH` or a `UNION` arm whose nodes are not reachable from a pinned anchor. A body whose first arm is pinned but whose second arm introduces an unpinned root would pass this check yet read across snapshots in that arm. Closing that requires a Cypher AST validator the stack does not have; it is owned by the **future graph-query-hardening plan** (the same owner that would make `_unwrap_agtype_value` recurse once the non-scalar agtype decoding contract is verified). The slice's agent prompt emits single-arm queries with a pinned root only, so the residual is not reachable in slice scope. This pass therefore **narrows** the D-8.1 injection surface to the structurally-enforceable cases and names the owner of the remainder — it does not claim a total close.

### Canonical excerpt contract — a hard constraint Plan 10 inherits

The Verifier (Plan 9 §6.2) compares the resolved content to the LLM's `excerpt` byte-exact, after `_normalize_for_comparison` (NFC + LF + per-line rstrip + outer strip). Normalization does **not** strip JSON quotes or reorder list elements. Therefore:

> **For `HandleType.GRAPH_QUERY` citations, the agent (Plan 10) MUST format the `excerpt` field as the exact string `stringify_cypher_rows` produces — a JSON array of scalars (one element per row), `separators=(", ", ": ")`, with agtype outer quotes already stripped from string values.** This is a HARD CONSTRAINT Plan 10's agent-prompt design inherits from Plan 8 — not a soft preference. Plan 10's `compose` node prompt MUST instruct the LLM accordingly, and Plan 10's tests MUST pin a graph_query claim round-trips to PROVEN.

The spec's §6 sample claim record (`"excerpt": "[codeoracle.api.routes.query.handle_explain_symbol]"`, no inner quotes) is **illustrative of shape, not byte-exact** — it was hand-written to read clearly. Its sketched Cypher (`RETURN c`) returns a whole node, which the scalar-per-row constraint above does not permit; the conforming form is `RETURN c.fqn AS result`. The byte-exact string the Verifier actually compares is the JSON `stringify_cypher_rows` emits (e.g. `["codeoracle.api.routes.query.handle_explain_symbol"]`, *with* inner quotes). If the agent emits the spec's no-inner-quotes form, the Verifier renders REFUTED and the agent retries — the intended feedback loop, but a needless one if Plan 10's prompt is written correctly from the start. Hence: hard constraint, surfaced here so Plan 10 cannot miss it.

**Why a single column named `result`:** it makes the resolver contract trivial — `stringify_cypher_rows` does `row.get("result")` and never reasons about column names or arity. A claim that naively wants several values is expressed as several rows of one scalar column (`RETURN c.fqn AS result`), or decomposed into several citations — never as a multi-column row or a map (see the scalar-per-row constraint above).

**Error handling (and the layer-boundary translation — closes D-8.6):** `GraphQueryError` (Plan 5 Task 8) is raised by `execute_cypher` on Cypher parse failures, AGE-not-loaded errors, etc. Crucially, `GraphQueryError` is a **worker-family** exception (Plan 5 defines it as `TransientWorkerError` → `WorkerError`). A `services/`-layer class — and `GraphQueryResolver` is one (it lives under `services/handle_resolution/strategies/`, per the layer-purity reminder) — must not let `workers/` vocabulary surface to its callers. So the resolver **catches `GraphQueryError` at this service boundary and re-raises it wrapped as `ResolverError`** (an `CodeOracleError`-derived service exception). Behaviour is unchanged — the Verifier (Plan 9) still sees an `CodeOracleError`/`ResolverError` and renders UNKNOWN — but the worker-family type never escapes the service layer. `test_resolve_wraps_graph_query_error_as_resolver_error` already pins the wrap; `test_resolve_does_not_leak_worker_family_exception` additionally asserts `GraphQueryError` is *not* what propagates. `ValueError` from locator parsing bubbles unchanged (caller-bug class — a malformed locator is a programming error, not a resolution failure).

**Files:**
- Create: `src/codeoracle/services/handle_resolution/strategies/graph_query.py`
- Create: `tests/unit/services/handle_resolution/strategies/test_graph_query.py`
- Modify: `src/codeoracle/services/handle_resolution/strategies/__init__.py` (add `GraphQueryResolver`)

- [ ] **Step 1: Write the failing test**

Create `tests/unit/services/handle_resolution/strategies/test_graph_query.py`:

```python
"""Plan 8 Task 11 — GraphQueryResolver unit tests.

Pins:
  1. Parses cypher:// locator → calls GraphRepository.execute_cypher with
     the exact Cypher body (no rewriting, no parameter extraction).
  2. Stringifies results deterministically: a JSON array of scalars, one
     per row, with agtype outer quotes stripped from string values.
  3. Empty result list → "[]" (a real "no matches" signal, NOT a failure).
  4. Wraps GraphQueryError as ResolverError.
  5. Rejects locator without cypher:// scheme.
  6. Rejects empty cypher body.
  7. Snapshot pin is enforced as a CONSTRAINT, not a token (D-8.1): rejects a
     body that omits $snapshot_sha entirely, one that mentions it without a
     node-property-map binding (e.g. behind `WHERE true OR ...`), and one whose
     only binding is inside a stripped comment; accepts a legitimate scoped
     query carrying extra map properties / whitespace.
  8. Binds the head snapshot_sha structurally via params (value from the handle,
     never from the query text), stripping any `+wt` dirty-tree suffix first.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from codeoracle.core.exceptions import GraphQueryError, ResolverError
from codeoracle.models.evidence import EvidenceHandle, HandleType
from codeoracle.services.handle_resolution.strategies.graph_query import (
    GraphQueryResolver,
    stringify_cypher_rows,
)


def _handle(locator: str, snapshot_sha: str = "abc1234") -> EvidenceHandle:
    return EvidenceHandle(
        type=HandleType.GRAPH_QUERY, locator=locator, snapshot_sha=snapshot_sha
    )


@pytest.fixture
def mock_graph_repo() -> AsyncMock:
    m = AsyncMock()
    m.execute_cypher = AsyncMock(return_value=[])
    return m


# -----------------------------------------------------------------------------
# Dispatch — Cypher body passed through unchanged
# -----------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_resolve_runs_cypher_body_verbatim(mock_graph_repo: AsyncMock) -> None:
    # The body MUST reference $snapshot_sha — the resolver fails closed otherwise
    # (D-8.1). The body itself is forwarded verbatim; the pin is BOUND, not spliced.
    cypher = (
        "MATCH (c:Symbol {snapshot_sha:$snapshot_sha})-[:CALLS]->"
        "(t {fqn:'codeoracle.verify'}) RETURN c.fqn AS result"
    )
    mock_graph_repo.execute_cypher.return_value = [{"result": '"codeoracle.handle_explain_symbol"'}]
    resolver = GraphQueryResolver(graph_repo=mock_graph_repo)

    await resolver.resolve(_handle(f"cypher://{cypher}"))

    mock_graph_repo.execute_cypher.assert_awaited_once()
    actual_cypher = mock_graph_repo.execute_cypher.call_args.args[0]
    assert actual_cypher == cypher  # body forwarded unchanged
    # Default column_spec is 'result agtype'
    assert mock_graph_repo.execute_cypher.call_args.kwargs.get("column_spec") == "result agtype"


@pytest.mark.asyncio
async def test_resolve_binds_head_snapshot_sha_as_param(mock_graph_repo: AsyncMock) -> None:
    """The snapshot pin is bound STRUCTURALLY (D-8.1): the resolver passes
    params={"snapshot_sha": <head sha>} as the second positional argument, so the
    value originates from the handle, never from the query text. A `+wt` dirty-tree
    suffix is stripped to its head first (G21 consistency)."""
    cypher = "MATCH (n:Symbol {snapshot_sha:$snapshot_sha}) RETURN n.fqn AS result"
    mock_graph_repo.execute_cypher.return_value = []
    resolver = GraphQueryResolver(graph_repo=mock_graph_repo)

    await resolver.resolve(_handle(f"cypher://{cypher}", snapshot_sha="abc123+wt0123456789ab"))

    params = mock_graph_repo.execute_cypher.call_args.args[1]
    assert params == {"snapshot_sha": "abc123"}  # +wt stripped, bound not spliced


@pytest.mark.asyncio
async def test_resolve_rejects_cypher_missing_snapshot_param(mock_graph_repo: AsyncMock) -> None:
    """A graph_query whose Cypher omits $snapshot_sha could read across
    snapshots. The resolver fails CLOSED before execution with ResolverError —
    it does not trust the agent prompt to have included the pin (D-8.1)."""
    resolver = GraphQueryResolver(graph_repo=mock_graph_repo)
    with pytest.raises(ResolverError, match="snapshot_sha"):
        await resolver.resolve(_handle("cypher://MATCH (n) RETURN n.fqn AS result"))
    mock_graph_repo.execute_cypher.assert_not_called()


@pytest.mark.asyncio
async def test_resolve_rejects_cypher_mentioning_snapshot_param_without_constraint(
    mock_graph_repo: AsyncMock,
) -> None:
    """The pin check is a CONSTRAINT check, not a token-presence check (D-8.1).
    A body that MENTIONS $snapshot_sha but does not bind it inside a node-property
    map — here behind a `WHERE true OR ...` short-circuit — reads every snapshot.
    A naive `"$snapshot_sha" in body` test would wrongly pass it; the resolver
    MUST reject it before execution."""
    resolver = GraphQueryResolver(graph_repo=mock_graph_repo)
    leaky = (
        "MATCH (c:Symbol) WHERE true OR c.snapshot_sha = $snapshot_sha "
        "RETURN c.fqn AS result"
    )
    with pytest.raises(ResolverError, match="snapshot_sha"):
        await resolver.resolve(_handle(f"cypher://{leaky}"))
    mock_graph_repo.execute_cypher.assert_not_called()


@pytest.mark.asyncio
async def test_resolve_rejects_cypher_with_commented_out_snapshot_constraint(
    mock_graph_repo: AsyncMock,
) -> None:
    """A `//`-commented constraint is not a real constraint. The check strips
    Cypher comments before matching, so a body whose only `{snapshot_sha:
    $snapshot_sha}` occurrence is inside a comment is rejected (D-8.1)."""
    resolver = GraphQueryResolver(graph_repo=mock_graph_repo)
    commented = (
        "MATCH (c:Symbol) // {snapshot_sha:$snapshot_sha}\n"
        "RETURN c.fqn AS result"
    )
    with pytest.raises(ResolverError, match="snapshot_sha"):
        await resolver.resolve(_handle(f"cypher://{commented}"))
    mock_graph_repo.execute_cypher.assert_not_called()


@pytest.mark.asyncio
async def test_resolve_accepts_constraint_with_extra_properties_in_map(
    mock_graph_repo: AsyncMock,
) -> None:
    """The constraint regex tolerates a node-property map that carries OTHER
    properties alongside the pin (e.g. `{fqn:'x', snapshot_sha:$snapshot_sha}`)
    and arbitrary intra-brace whitespace — a legitimate, scoped query is NOT
    falsely rejected (D-8.1 must narrow the surface without breaking valid
    citations)."""
    mock_graph_repo.execute_cypher.return_value = []
    resolver = GraphQueryResolver(graph_repo=mock_graph_repo)
    scoped = (
        "MATCH (c:Symbol { fqn:'codeoracle.verify',  snapshot_sha : $snapshot_sha }) "
        "RETURN c.fqn AS result"
    )
    out = await resolver.resolve(_handle(f"cypher://{scoped}"))
    assert out == "[]"
    mock_graph_repo.execute_cypher.assert_awaited_once()


@pytest.mark.asyncio
async def test_resolve_returns_deterministic_json_string(mock_graph_repo: AsyncMock) -> None:
    """Two rows; agtype outer-quote stripped from string values. Row order
    is preserved — the JSON array is never sorted (only map keys would be,
    and graph_query produces no maps). The Verifier's excerpt-comparison is
    byte-exact (after NFC + LF + rstrip), so the form MUST be stable."""
    mock_graph_repo.execute_cypher.return_value = [
        {"result": '"codeoracle.b"'},
        {"result": '"codeoracle.a"'},
    ]
    resolver = GraphQueryResolver(graph_repo=mock_graph_repo)
    out = await resolver.resolve(
        _handle("cypher://MATCH (n:Symbol {snapshot_sha:$snapshot_sha}) RETURN n.fqn AS result")
    )
    # Quotes stripped + serialized as JSON list
    assert out == '["codeoracle.b", "codeoracle.a"]'


@pytest.mark.asyncio
async def test_resolve_returns_empty_list_string_when_no_rows(mock_graph_repo: AsyncMock) -> None:
    """No matches is a legitimate result, not a failure. Verifier compares
    '[]' to whatever the LLM put in excerpt; if the LLM claimed 'no callers'
    by emitting excerpt='[]', it's PROVEN. If the LLM claimed callers but
    the graph has none, REFUTED."""
    mock_graph_repo.execute_cypher.return_value = []
    resolver = GraphQueryResolver(graph_repo=mock_graph_repo)
    out = await resolver.resolve(
        _handle(
            "cypher://MATCH (n:Symbol {fqn:'nope', snapshot_sha:$snapshot_sha}) "
            "RETURN n.fqn AS result"
        )
    )
    assert out == "[]"


# -----------------------------------------------------------------------------
# Error wrapping
# -----------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_resolve_wraps_graph_query_error_as_resolver_error(mock_graph_repo: AsyncMock) -> None:
    # The body MUST carry the snapshot constraint so it passes _assert_snapshot_pinned
    # and reaches execute_cypher — only then does the seeded GraphQueryError drive the
    # service-boundary wrap (D-8.6). A body lacking the constraint would be rejected
    # earlier by the D-8.1 check, with no GraphQueryError cause.
    mock_graph_repo.execute_cypher.side_effect = GraphQueryError("Cypher execution failed: ...")
    resolver = GraphQueryResolver(graph_repo=mock_graph_repo)
    with pytest.raises(ResolverError) as exc:
        await resolver.resolve(
            _handle("cypher://MATCH (n:Symbol {snapshot_sha:$snapshot_sha}) RETURN n.fqn AS result")
        )
    assert isinstance(exc.value.__cause__, GraphQueryError)


@pytest.mark.asyncio
async def test_resolve_does_not_leak_worker_family_exception(mock_graph_repo: AsyncMock) -> None:
    # Complement to the wrap test: the wrap test pins that the worker-family cause is
    # preserved as __cause__; this one pins the OTHER half of the D-8.6 contract — the
    # exception that actually PROPAGATES across the service boundary is a ResolverError
    # and is NOT a GraphQueryError. Catching `ResolverError` must NOT also catch the
    # raised type, which proves the worker-family vocabulary never escapes services/.
    mock_graph_repo.execute_cypher.side_effect = GraphQueryError("Cypher execution failed: ...")
    resolver = GraphQueryResolver(graph_repo=mock_graph_repo)
    with pytest.raises(ResolverError) as exc:
        await resolver.resolve(
            _handle("cypher://MATCH (n:Symbol {snapshot_sha:$snapshot_sha}) RETURN n.fqn AS result")
        )
    assert not isinstance(exc.value, GraphQueryError)


# -----------------------------------------------------------------------------
# Locator validation
# -----------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_resolve_rejects_wrong_scheme(mock_graph_repo: AsyncMock) -> None:
    resolver = GraphQueryResolver(graph_repo=mock_graph_repo)
    handle = EvidenceHandle(
        type=HandleType.GRAPH_QUERY,
        locator="file://foo.py:1-2",  # Pydantic accepts (has '://'); resolver rejects scheme
        snapshot_sha="abc1234",
    )
    with pytest.raises(ValueError, match="cypher://"):
        await resolver.resolve(handle)
    mock_graph_repo.execute_cypher.assert_not_called()


@pytest.mark.asyncio
async def test_resolve_rejects_empty_cypher_body(mock_graph_repo: AsyncMock) -> None:
    resolver = GraphQueryResolver(graph_repo=mock_graph_repo)
    handle = EvidenceHandle(
        type=HandleType.GRAPH_QUERY, locator="cypher://", snapshot_sha="abc1234"
    )
    with pytest.raises(ValueError, match="empty"):
        await resolver.resolve(handle)
    mock_graph_repo.execute_cypher.assert_not_called()


# -----------------------------------------------------------------------------
# stringify_cypher_rows helper — exposed for direct unit testing
# -----------------------------------------------------------------------------

def test_stringify_strips_agtype_quotes_from_string_values() -> None:
    rows = [{"result": '"alpha"'}, {"result": '"beta"'}]
    assert stringify_cypher_rows(rows) == '["alpha", "beta"]'


def test_stringify_preserves_int_values_unquoted() -> None:
    """AGE encodes ints without quotes; the resolver MUST keep them numeric."""
    rows = [{"result": 3}]
    assert stringify_cypher_rows(rows) == "[3]"


def test_stringify_handles_float_bool_and_null_scalars() -> None:
    """graph_query is constrained to single-column, scalar-per-row returns.
    String and int scalars are pinned above; this pins the remaining scalar
    agtype shapes — float, bool, null. Maps and lists are out of slice scope
    (see the Task 11 scalar-per-row constraint)."""
    rows = [{"result": 3.5}, {"result": True}, {"result": False}, {"result": None}]
    assert stringify_cypher_rows(rows) == "[3.5, true, false, null]"
```

- [ ] **Step 2: Run to confirm failure**

```bash
uv run pytest tests/unit/services/handle_resolution/strategies/test_graph_query.py -v
```

Expected: `ImportError`.

- [ ] **Step 3: Implement `GraphQueryResolver`**

Create `src/codeoracle/services/handle_resolution/strategies/graph_query.py`:

```python
"""GraphQueryResolver — resolves cypher://<query> handles via GraphRepository.

The locator IS the Cypher query. The resolver re-runs it at verify time
and stringifies the result deterministically so the Verifier can compare
byte-exact against the LLM's claimed excerpt.

AGE returns agtype values: string scalars appear JSON-quoted (`"foo"`);
ints, floats, and bools appear bare. graph_query citations are constrained
to single-column, scalar-per-row returns (see the plan's Task 11 prose),
so this module unwraps the outer quotes on string scalars and emits a
stable JSON array. Output order is the row order alone (a flat list of
scalars), so no key sorting is needed.
"""

from __future__ import annotations

import json
import re

from codeoracle.core.exceptions import GraphQueryError, ResolverError
from codeoracle.db.repositories.graph import GraphRepository
from codeoracle.models.evidence import EvidenceHandle
from codeoracle.services.handle_resolution.snapshot_ref import head_sha_only
from codeoracle.services.handle_resolution.strategies.base import HandleResolverStrategy


_CYPHER_SCHEME = "cypher://"

# The snapshot pin must appear as a node-property-map CONSTRAINT binding the
# AGE/Postgres `$snapshot_sha` parameter — `{... snapshot_sha: $snapshot_sha ...}`
# — not merely as a token somewhere in the body. A token-presence test (`"$snapshot_sha"
# in body`) is insufficient: a body like `MATCH (c:Symbol) WHERE true OR
# c.snapshot_sha = $snapshot_sha RETURN c.fqn AS result` mentions the parameter yet
# reads EVERY snapshot. Requiring the property-map form matches the exact shape the
# agent prompt mandates (`MATCH (... {snapshot_sha:$snapshot_sha}) ...`) and makes the
# pin a structural filter, not a decoration. See the Task 11 "snapshot pin is enforced
# structurally" prose and audit finding D-8.1.
#
# The pattern tolerates arbitrary intra-brace whitespace and is applied AFTER
# comment-stripping (a `//`- or `/* */`-commented mention must not satisfy the check).
_SNAPSHOT_CONSTRAINT_RE = re.compile(r"\{[^{}]*\bsnapshot_sha\s*:\s*\$snapshot_sha\b[^{}]*\}")

# Strip Cypher line comments (`// ... EOL`) and block comments (`/* ... */`) so a
# commented-out constraint cannot satisfy `_SNAPSHOT_CONSTRAINT_RE`. Cypher string
# literals could in principle contain `//`, but a graph_query citation never embeds
# the snapshot constraint inside a string literal, so this conservative strip is safe
# for the slice. (A full Cypher tokenizer is out of scope — see the residual note in
# the Task 11 prose.)
_CYPHER_LINE_COMMENT_RE = re.compile(r"//[^\n]*")
_CYPHER_BLOCK_COMMENT_RE = re.compile(r"/\*.*?\*/", re.DOTALL)


def _strip_cypher_comments(cypher_body: str) -> str:
    """Remove `//` line comments and `/* */` block comments from a Cypher body.

    Used only to harden `_assert_snapshot_pinned` against a commented-out snapshot
    constraint; the value-stripped body is NOT what gets executed (the original
    body is forwarded verbatim).
    """
    without_block = _CYPHER_BLOCK_COMMENT_RE.sub(" ", cypher_body)
    return _CYPHER_LINE_COMMENT_RE.sub(" ", without_block)


def _parse_cypher_locator(locator: str) -> str:
    """Extract the Cypher body from `cypher://<body>`.

    Raises:
        ValueError: if scheme is missing or body is empty.
    """
    if not locator.startswith(_CYPHER_SCHEME):
        raise ValueError(f"locator missing 'cypher://' scheme: {locator!r}")
    body = locator[len(_CYPHER_SCHEME):]
    if not body.strip():
        raise ValueError(f"cypher locator has empty body: {locator!r}")
    return body


def _assert_snapshot_pinned(cypher_body: str, locator: str) -> None:
    """Reject a Cypher body that does not CONSTRAIN on the `$snapshot_sha` param.

    Snapshot isolation in the graph is a per-node `snapshot_sha` property
    (Plan 5 — every :Symbol / :File carries it). A graph_query citation MUST
    constrain on that property via a node-property-map binding the
    `$snapshot_sha` parameter, e.g.
    `MATCH (c:Symbol {snapshot_sha:$snapshot_sha})-[:CALLS]->... RETURN c.fqn
    AS result`. The resolver fails CLOSED here — before execution — rather than
    trusting the agent prompt to have included the pin.

    This is a CONSTRAINT check, not a token-presence check. A body that merely
    mentions `$snapshot_sha` without binding it inside a node-property map
    (e.g. `MATCH (c:Symbol) WHERE true OR c.snapshot_sha = $snapshot_sha
    RETURN c.fqn AS result`) reads every snapshot and is REJECTED. Comments are
    stripped first so a `//`- or `/* */`-commented constraint does not satisfy
    the check.

    Residual (accepted risk): the regex guarantees >=1 matched node carries the
    constraint ("at least one pinned anchor"); it cannot prove that EVERY
    independent traversal root does. A node reachable from a pinned anchor is
    safe (it is already scoped); the residual is an ADDITIONAL independent
    MATCH/UNION arm that introduces its own unanchored root, which would pass
    this check yet read across snapshots in that arm. Closing that requires a
    Cypher AST validator, which the stack does not have; it is owned by the
    future graph-query-hardening plan (the same owner that would make
    `_unwrap_agtype_value` recurse for non-scalar agtype). The slice's agent
    prompt emits single-arm queries with a pinned root only, so the residual is
    not reachable in slice scope. This narrows — does not fully close — the
    D-8.1 injection surface; the prose above states the boundary.

    Raises:
        ResolverError: the Cypher body does not bind `$snapshot_sha` as a
            node-property-map constraint.
    """
    if not _SNAPSHOT_CONSTRAINT_RE.search(_strip_cypher_comments(cypher_body)):
        raise ResolverError(
            f"graph_query locator {locator!r} does not constrain on the required "
            "`$snapshot_sha` parameter via a node-property map "
            "(expected `{... snapshot_sha: $snapshot_sha ...}`); the snapshot pin "
            "must scope the query structurally so it cannot read across snapshots"
        )


def _unwrap_agtype_value(value: object) -> object:
    """Strip one outer pair of JSON quotes that AGE adds to a string scalar.

    Per Plan 5 Task 8: AGE returns `"foo"` (string with quotes), not `foo`.
    Numeric and bool scalars pass through unchanged. This helper does NOT
    recurse into maps or lists: graph_query is constrained to scalar-per-row
    returns (see Task 11 prose), so non-scalar agtype never reaches it.
    """
    if isinstance(value, str) and len(value) >= 2 and value[0] == '"' and value[-1] == '"':
        return value[1:-1]
    return value


def stringify_cypher_rows(rows: list[dict[str, object]]) -> str:
    """Convert single-column scalar rows into a deterministic JSON string.

    Each row's 'result' scalar is unwrapped (agtype-string → str), then the
    list of values is JSON-encoded with standard separators.
    """
    values = [_unwrap_agtype_value(row.get("result")) for row in rows]
    # `values` is always a flat list of scalars (graph_query is scalar-per-row),
    # so list order alone fixes the output — no `sort_keys` is needed or passed.
    return json.dumps(values, separators=(", ", ": "))


class GraphQueryResolver(HandleResolverStrategy):
    """Resolves GRAPH_QUERY handles by re-running the embedded Cypher and
    stringifying the result deterministically.

    The handle.snapshot_sha is enforced STRUCTURALLY, not via the prompt. The
    resolver (a) strips any `+wt<12hex>` dirty-tree suffix via `head_sha_only`
    (G21 consistency — the same normalization CodeSpanResolver / DocParagraph-
    Resolver apply before a Sourcebot call), (b) asserts the LLM-authored Cypher
    binds `$snapshot_sha` as a node-property-map CONSTRAINT (`_assert_snapshot_
    pinned` — a constraint check, not a mere token-presence check; comments are
    stripped first) and rejects it with ResolverError otherwise, and (c) passes
    `params={"snapshot_sha": <head sha>}` so AGE BINDS the value from the handle.
    The query text can therefore never substitute a different snapshot — the
    value does not come from the body — and a single-arm query that fails to
    filter on the pin is rejected before execution. This NARROWS the D-8.1
    injection surface to the structurally-enforceable cases (matching the
    structural, not prompt-only, guarantee the span / doc resolvers carry); the
    one residual a non-parser check cannot reach — an ADDITIONAL independent
    MATCH/UNION arm that introduces its own unanchored root (nodes reachable from
    a pinned anchor are already scoped) — is documented as accepted risk with a
    named owner (the future graph-query-hardening plan) in the
    `_assert_snapshot_pinned` docstring and the Task 11 prose. The slice agent
    emits single-arm queries with a pinned root only, so the residual is
    unreachable in slice scope.
    """

    def __init__(self, graph_repo: GraphRepository) -> None:
        self._graph_repo = graph_repo

    async def resolve(self, handle: EvidenceHandle) -> str:
        cypher_body = _parse_cypher_locator(handle.locator)
        _assert_snapshot_pinned(cypher_body, handle.locator)
        # Normalize a dirty-tree `+wt` SHA to its head before binding (G21): the
        # graph indexes nodes under the head snapshot_sha, exactly as Sourcebot
        # indexes the head ref. The value is BOUND via params — never spliced
        # into the body — so the pin is structural.
        head_sha = head_sha_only(handle.snapshot_sha)
        try:
            rows = await self._graph_repo.execute_cypher(
                cypher_body,
                {"snapshot_sha": head_sha},
                column_spec="result agtype",
            )
        except GraphQueryError as e:
            # Service-boundary translation: GraphQueryError is a worker-family
            # exception (Plan 5: TransientWorkerError -> WorkerError). It MUST NOT
            # escape the services/ layer in workers/ vocabulary, so it is wrapped
            # here into ResolverError (an CodeOracleError-derived service exception)
            # before reaching the Verifier (Plan 9). Closes D-8.6.
            raise ResolverError(
                f"GraphQueryResolver could not run cypher {handle.locator!r} at "
                f"snapshot {head_sha!r}: {e}"
            ) from e
        return stringify_cypher_rows(rows)
```

- [ ] **Step 4: Update strategies/__init__.py**

Update `src/codeoracle/services/handle_resolution/strategies/__init__.py`:

```python
"""Per-HandleType resolver strategies."""

from codeoracle.services.handle_resolution.strategies.base import (
    HandleResolverStrategy,
    parse_file_locator,
)
from codeoracle.services.handle_resolution.strategies.code_span import CodeSpanResolver
from codeoracle.services.handle_resolution.strategies.doc_paragraph import (
    DocParagraphResolver,
)
from codeoracle.services.handle_resolution.strategies.graph_query import (
    GraphQueryResolver,
)

__all__ = [
    "CodeSpanResolver",
    "DocParagraphResolver",
    "GraphQueryResolver",
    "HandleResolverStrategy",
    "parse_file_locator",
]
```

- [ ] **Step 5: Run tests + ruff + ty + commit**

```bash
uv run pytest tests/unit/services/handle_resolution/strategies/test_graph_query.py -v
uv run ruff check src/codeoracle/services/handle_resolution/strategies/graph_query.py
uv run ty check src/codeoracle/services/handle_resolution/strategies/graph_query.py

git add src/codeoracle/services/handle_resolution/strategies/graph_query.py \
        src/codeoracle/services/handle_resolution/strategies/__init__.py \
        tests/unit/services/handle_resolution/strategies/test_graph_query.py
git commit -m "feat(plan-8): Task 11 — GraphQueryResolver (cypher:// -> AGE Cypher)"
```

---


## Task 12: `strategies/stub.py` — `StubResolver`

**What this task delivers:** the strategy that's explicitly registered for `TRACE_SPAN` and `DB_RECORD` (per Invariant 4). It always raises `UnsupportedHandleTypeError`. Per the spec at §6.3: *"`StubResolver` raises `UnsupportedHandleTypeError` for TRACE_SPAN and DB_RECORD — these handle types are not produced by the slice's intent classifier (integration-tested at §8.5)."*

**Why register a Stub instead of leaving the type unmapped:** the dispatcher would raise the same error either way (per Task 8's `if strategy is None:` branch). But explicit registration:

1. **Documents the decision.** A reader of composition root sees `HandleType.TRACE_SPAN → StubResolver(...)` and immediately understands "intentionally not implemented" vs. "forgot to wire."
2. **Enables type-specific error messages.** The Stub knows its `HandleType` (passed at construction) and can include it in the error: `"TRACE_SPAN handles are not supported in slice scope"` is more actionable than the dispatcher's generic `"no resolver registered for HandleType.TRACE_SPAN"`.
3. **Survives `test_service_dispatch_covers_all_handle_types`** (Task 13) without code changes when a new HandleType is added — the test enumerates `HandleType` and asserts every member maps to *some* strategy. New types deliberately not yet implemented can route through `StubResolver` until their real strategy lands.

**Files:**
- Create: `src/codeoracle/services/handle_resolution/strategies/stub.py`
- Create: `tests/unit/services/handle_resolution/strategies/test_stub.py`
- Modify: `src/codeoracle/services/handle_resolution/strategies/__init__.py` (add `StubResolver`)

- [ ] **Step 1: Write the failing test**

Create `tests/unit/services/handle_resolution/strategies/test_stub.py`:

```python
"""Plan 8 Task 12 — StubResolver tests.

Pins:
  1. resolve() ALWAYS raises UnsupportedHandleTypeError.
  2. The error message names the configured HandleType.
  3. The stub does NOT branch on the actual handle.type — its sole job
     is to fail loudly with a typed exception. (Mismatched handle types
     are caught by the dispatcher; the stub doesn't double-check.)
"""

from __future__ import annotations

import pytest

from codeoracle.core.exceptions import UnsupportedHandleTypeError
from codeoracle.models.evidence import EvidenceHandle, HandleType
from codeoracle.services.handle_resolution.strategies.stub import StubResolver


@pytest.mark.asyncio
async def test_resolve_always_raises_unsupported() -> None:
    stub = StubResolver(handle_type=HandleType.TRACE_SPAN)
    handle = EvidenceHandle(
        type=HandleType.TRACE_SPAN,
        locator="trace://abc123",
        snapshot_sha="abc1234",
    )
    with pytest.raises(UnsupportedHandleTypeError) as exc:
        await stub.resolve(handle)
    assert "trace_span" in str(exc.value).lower()
    assert "slice" in str(exc.value).lower()


@pytest.mark.asyncio
async def test_resolve_for_db_record_names_db_record() -> None:
    stub = StubResolver(handle_type=HandleType.DB_RECORD)
    handle = EvidenceHandle(
        type=HandleType.DB_RECORD,
        locator="db://schema.table:42",
        snapshot_sha="abc1234",
    )
    with pytest.raises(UnsupportedHandleTypeError) as exc:
        await stub.resolve(handle)
    assert "db_record" in str(exc.value).lower()
```

- [ ] **Step 2: Run to confirm failure**

```bash
uv run pytest tests/unit/services/handle_resolution/strategies/test_stub.py -v
```

Expected: `ImportError`.

- [ ] **Step 3: Implement `StubResolver`**

Create `src/codeoracle/services/handle_resolution/strategies/stub.py`:

```python
"""StubResolver — explicit "not supported in slice scope" for TRACE_SPAN
and DB_RECORD handles.

Composition root registers two instances:
    HandleType.TRACE_SPAN: StubResolver(HandleType.TRACE_SPAN)
    HandleType.DB_RECORD:  StubResolver(HandleType.DB_RECORD)

Each instance carries its `handle_type` so the error message names what
the LLM asked for. Plan 13's E2E slice asserts that the intent classifier
never emits these types — i.e., the stubs should never fire in practice.
If one does, it's a regression worth investigating.
"""

from __future__ import annotations

from codeoracle.core.exceptions import UnsupportedHandleTypeError
from codeoracle.models.evidence import EvidenceHandle, HandleType
from codeoracle.services.handle_resolution.strategies.base import HandleResolverStrategy


class StubResolver(HandleResolverStrategy):
    """Always-fail resolver. Registered for HandleTypes that the slice
    intent classifier should never emit but that exist in the enum so
    Phase 2+ extensions can add real strategies without touching the
    HandleType enum or the dispatcher."""

    def __init__(self, handle_type: HandleType) -> None:
        self._handle_type = handle_type

    async def resolve(self, handle: EvidenceHandle) -> str:
        raise UnsupportedHandleTypeError(
            f"HandleType.{self._handle_type.name} ({self._handle_type.value!r}) is not "
            f"supported in slice scope — the agent should not emit this handle type. "
            f"locator={handle.locator!r}"
        )
```

- [ ] **Step 4: Update strategies/__init__.py**

Update `src/codeoracle/services/handle_resolution/strategies/__init__.py`:

```python
"""Per-HandleType resolver strategies."""

from codeoracle.services.handle_resolution.strategies.base import (
    HandleResolverStrategy,
    parse_file_locator,
)
from codeoracle.services.handle_resolution.strategies.code_span import CodeSpanResolver
from codeoracle.services.handle_resolution.strategies.doc_paragraph import (
    DocParagraphResolver,
)
from codeoracle.services.handle_resolution.strategies.graph_query import (
    GraphQueryResolver,
)
from codeoracle.services.handle_resolution.strategies.stub import StubResolver

__all__ = [
    "CodeSpanResolver",
    "DocParagraphResolver",
    "GraphQueryResolver",
    "HandleResolverStrategy",
    "StubResolver",
    "parse_file_locator",
]
```

- [ ] **Step 5: Run tests + ruff + ty + commit**

```bash
uv run pytest tests/unit/services/handle_resolution/strategies/test_stub.py -v
uv run ruff check src/codeoracle/services/handle_resolution/strategies/stub.py
uv run ty check src/codeoracle/services/handle_resolution/strategies/stub.py

git add src/codeoracle/services/handle_resolution/strategies/stub.py \
        src/codeoracle/services/handle_resolution/strategies/__init__.py \
        tests/unit/services/handle_resolution/strategies/test_stub.py
git commit -m "feat(plan-8): Task 12 — StubResolver (TRACE_SPAN + DB_RECORD intentional stubs)"
```

---


## Task 13: `services/handle_resolution/__init__.py` re-exports + full-wiring integration test

**What this task delivers:** the final public surface for `codeoracle.services.handle_resolution` plus a single integration test that wires *all* 4 strategies (with mocked adapters), instantiates the service with the canonical composition map, and asserts every `HandleType` enum member dispatches correctly. This is the test that catches "you added a new HandleType in Plan 9 but forgot to wire it."

**Why one integration test, not five unit tests:** the per-strategy unit tests (Tasks 9-12) already cover each resolver's mechanics. Task 13's job is the *wiring layer* — that the dict keys cover the enum and dispatch routes correctly. A single test that enumerates `HandleType` is denser and harder to skip-by-accident than five copies of a near-identical pattern.

**Files:**
- Modify: `src/codeoracle/services/handle_resolution/__init__.py` (final re-exports — replace the Task 7 placeholder)
- Create: `tests/unit/services/handle_resolution/test_wiring.py`

- [ ] **Step 1: Replace `__init__.py` with final re-exports**

Overwrite `src/codeoracle/services/handle_resolution/__init__.py`:

```python
"""Handle resolution service.

Public surface:
  - HandleResolverService — the dispatcher (Task 8). Callers (VerifierService
    in Plan 9) construct one of these from a strategy dict.
  - HandleResolverStrategy — the ABC (Task 7); subclass to add a new strategy.
  - The four concrete strategies: CodeSpanResolver (Task 9),
    DocParagraphResolver (Task 10), GraphQueryResolver (Task 11),
    StubResolver (Task 12).
  - parse_file_locator — exposed because Plan 9's tests construct synthetic
    handles and want the same parser the resolver uses.

The strategies subpackage is re-exported flat from this module so callers
can do `from codeoracle.services.handle_resolution import CodeSpanResolver`
without crossing into the subpackage. Composition root uses these
re-exports.
"""

from codeoracle.services.handle_resolution.service import HandleResolverService
from codeoracle.services.handle_resolution.strategies import (
    CodeSpanResolver,
    DocParagraphResolver,
    GraphQueryResolver,
    HandleResolverStrategy,
    StubResolver,
    parse_file_locator,
)

__all__ = [
    "CodeSpanResolver",
    "DocParagraphResolver",
    "GraphQueryResolver",
    "HandleResolverService",
    "HandleResolverStrategy",
    "StubResolver",
    "parse_file_locator",
]
```

- [ ] **Step 2: Write the failing integration test**

Create `tests/unit/services/handle_resolution/test_wiring.py`:

```python
"""Plan 8 Task 13 — HandleResolverService full-wiring integration test.

Wires all 4 concrete strategies (with mocked adapters) into the service
and asserts:
  1. The strategy dict covers EVERY HandleType enum member — the
     `test_service_dispatch_covers_all_handle_types` invariant.
  2. Each HandleType dispatches to its registered resolver class.
  3. TRACE_SPAN and DB_RECORD route through StubResolver, raising
     UnsupportedHandleTypeError.
  4. CodeSpan, DocParagraph, GraphQuery dispatch correctly.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from codeoracle.core.exceptions import UnsupportedHandleTypeError
from codeoracle.models.evidence import EvidenceHandle, HandleType
from codeoracle.services.handle_resolution import (
    CodeSpanResolver,
    DocParagraphResolver,
    GraphQueryResolver,
    HandleResolverService,
    StubResolver,
)


@pytest.fixture
def mock_sourcebot() -> AsyncMock:
    m = AsyncMock()
    m.fetch_span = AsyncMock(return_value="file content")
    return m


@pytest.fixture
def mock_graph_repo() -> AsyncMock:
    m = AsyncMock()
    m.execute_cypher = AsyncMock(return_value=[])
    return m


@pytest.fixture
def fully_wired_service(mock_sourcebot, mock_graph_repo) -> HandleResolverService:
    """Mirrors the canonical composition root wiring (see Invariant 8)."""
    return HandleResolverService(strategies={
        HandleType.CODE_SPAN:     CodeSpanResolver(sourcebot=mock_sourcebot),
        HandleType.DOC_PARAGRAPH: DocParagraphResolver(sourcebot=mock_sourcebot),
        HandleType.GRAPH_QUERY:   GraphQueryResolver(graph_repo=mock_graph_repo),
        HandleType.TRACE_SPAN:    StubResolver(handle_type=HandleType.TRACE_SPAN),
        HandleType.DB_RECORD:     StubResolver(handle_type=HandleType.DB_RECORD),
    })


def test_service_dispatch_covers_all_handle_types(fully_wired_service: HandleResolverService) -> None:
    """If a new HandleType is added in Plan 9+ but composition root forgets to
    wire it, this test fails. The 'why' for Invariant 8."""
    wired_types = fully_wired_service.registered_handle_types()
    enum_members = set(HandleType)
    missing = enum_members - wired_types
    assert not missing, (
        f"HandleResolverService is not wired for HandleType members: {missing}. "
        f"Either register a real strategy or a StubResolver for these types."
    )


@pytest.mark.asyncio
async def test_code_span_dispatches_to_sourcebot(
    fully_wired_service: HandleResolverService, mock_sourcebot: AsyncMock
) -> None:
    handle = EvidenceHandle(
        type=HandleType.CODE_SPAN,
        locator="file://src/codeoracle/foo.py:1-2",
        snapshot_sha="abc1234",
    )
    result = await fully_wired_service.resolve(handle)
    assert result == "file content"
    mock_sourcebot.fetch_span.assert_awaited_once_with(
        path="src/codeoracle/foo.py",
        line_range=(1, 2),
        ref="abc1234",
    )


@pytest.mark.asyncio
async def test_doc_paragraph_dispatches_to_sourcebot(
    fully_wired_service: HandleResolverService, mock_sourcebot: AsyncMock
) -> None:
    handle = EvidenceHandle(
        type=HandleType.DOC_PARAGRAPH,
        locator="file://docs/architecture.md:42-50",
        snapshot_sha="abc1234",
    )
    result = await fully_wired_service.resolve(handle)
    assert result == "file content"


@pytest.mark.asyncio
async def test_graph_query_dispatches_to_graph_repo(
    fully_wired_service: HandleResolverService, mock_graph_repo: AsyncMock
) -> None:
    handle = EvidenceHandle(
        type=HandleType.GRAPH_QUERY,
        locator="cypher://MATCH (n) RETURN n.fqn AS result",
        snapshot_sha="abc1234",
    )
    result = await fully_wired_service.resolve(handle)
    # No rows → "[]"
    assert result == "[]"
    mock_graph_repo.execute_cypher.assert_awaited_once()


@pytest.mark.asyncio
async def test_trace_span_routes_to_stub_resolver(
    fully_wired_service: HandleResolverService,
) -> None:
    handle = EvidenceHandle(
        type=HandleType.TRACE_SPAN,
        locator="trace://abc123",
        snapshot_sha="abc1234",
    )
    with pytest.raises(UnsupportedHandleTypeError, match="TRACE_SPAN"):
        await fully_wired_service.resolve(handle)


@pytest.mark.asyncio
async def test_db_record_routes_to_stub_resolver(
    fully_wired_service: HandleResolverService,
) -> None:
    handle = EvidenceHandle(
        type=HandleType.DB_RECORD,
        locator="db://schema.users:42",
        snapshot_sha="abc1234",
    )
    with pytest.raises(UnsupportedHandleTypeError, match="DB_RECORD"):
        await fully_wired_service.resolve(handle)


@pytest.mark.asyncio
async def test_no_handle_type_falls_through_to_dispatcher_default(
    fully_wired_service: HandleResolverService,
) -> None:
    """Belt-and-suspenders: if Invariant 8 ever drifts (someone removes
    StubResolver for DB_RECORD), the dispatcher's default branch still
    raises UnsupportedHandleTypeError. This test re-asserts that as a
    boundary against silent regressions."""
    # Construct a service intentionally missing GRAPH_QUERY.
    partial = HandleResolverService(strategies={
        HandleType.CODE_SPAN: CodeSpanResolver(sourcebot=AsyncMock(fetch_span=AsyncMock())),
    })
    handle = EvidenceHandle(
        type=HandleType.GRAPH_QUERY,
        locator="cypher://MATCH (n) RETURN n",
        snapshot_sha="abc1234",
    )
    with pytest.raises(UnsupportedHandleTypeError) as exc:
        await partial.resolve(handle)
    assert "GRAPH_QUERY" in str(exc.value)
```

- [ ] **Step 3: Run tests**

```bash
uv run pytest tests/unit/services/handle_resolution/ -v
```

Expected: all per-strategy tests + 7 wiring tests pass. Failure here usually means a strategy's external API changed and the wiring fixture is stale; check the per-strategy test files first.

- [ ] **Step 4: Full Plan-8 unit test sweep**

```bash
uv run pytest tests/unit/adapters/sourcebot/ tests/unit/services/handle_resolution/ tests/unit/core/test_exceptions.py -v
```

Expected: all green. Numbers to expect (rough): exceptions ≈4, sourcebot config ≈5, sourcebot contract ≈5, sourcebot client ≈22, base ≈12, service ≈6, snapshot_ref ≈5, code_span ≈6, doc_paragraph ≈4, graph_query ≈8, stub ≈2, wiring ≈7 — total ~86 tests in Plan 8's unit surface.

- [ ] **Step 5: ruff + ty + commit**

```bash
uv run ruff check src/codeoracle/services/handle_resolution/ tests/unit/services/handle_resolution/
uv run ty check src/codeoracle/services/handle_resolution/

git add src/codeoracle/services/handle_resolution/__init__.py \
        tests/unit/services/handle_resolution/test_wiring.py
git commit -m "feat(plan-8): Task 13 — services/handle_resolution/__init__.py + full-wiring test (covers all HandleType)"
```

---


## Task 14: Documentation updates + final Plan 8 commit

**What this task delivers:** the docs sweep that ensures Plan 8's surface is discoverable from `docs/architecture.md` and that the project-wide README points at the new module. Also: the final commit landing every Plan-8 artifact under a single "Plan 8 complete" tag, mirroring the pattern Plans 5/6/7 used.

**Files:**
- Modify: `docs/architecture.md` (§14 Handle Resolution — point to module path; add SourcebotClient to §10 adapters table)
- Modify: `src/codeoracle/README.md` if present (mention the new handle_resolution service)
- Modify: `docs/superpowers/plans/2026-05-21-handle-resolution-and-sourcebot.md` (this plan — append a "Plan 8 closure" footer for next-plan link discoverability)

- [ ] **Step 1: Confirm `docs/architecture.md` §14 already names handle resolution**

```bash
grep -nE "(handle_resolution|HandleResolverService)" /home/sabossedgh/dev/codeoracle/docs/architecture.md | head -5
```

Expected: one or more matches (the spec already references the module). If zero matches, append a short section under §14 like:

```markdown
### §14.3 Handle resolution module

Located at `src/codeoracle/services/handle_resolution/`. Dispatches an
`EvidenceHandle` to one of 4 concrete strategies:

| HandleType | Strategy | Underlying |
|---|---|---|
| `CODE_SPAN` | `CodeSpanResolver` | `SourcebotClient.fetch_span` |
| `DOC_PARAGRAPH` | `DocParagraphResolver` | `SourcebotClient.fetch_span` |
| `GRAPH_QUERY` | `GraphQueryResolver` | `GraphRepository.execute_cypher` |
| `TRACE_SPAN`, `DB_RECORD` | `StubResolver` | raises `UnsupportedHandleTypeError` |

Composition root: `api/deps.py` (Plan 11) wires the dispatch dict;
`workers/_base/wiring.py` (Plan 5) wires worker-side instance.
See Plan 8 (`docs/superpowers/plans/2026-05-21-handle-resolution-and-sourcebot.md`).
```

- [ ] **Step 2: Add SourcebotClient to architecture §10 adapters table**

Grep for the adapters table:

```bash
grep -nE "(JoernClient|TempoClient).*Apache" /home/sabossedgh/dev/codeoracle/docs/architecture.md | head -3
```

If the table exists, add a row matching the column structure of the surrounding `JoernClient` and `TempoClient` rows. If the section is markdown prose instead of a table, append a sentence: *"SourcebotClient (Plan 8) wraps Sourcebot's `/api/source` HTTP endpoint — see `src/codeoracle/adapters/sourcebot/`."*

- [ ] **Step 3: Update `src/codeoracle/README.md` if present**

```bash
ls /home/sabossedgh/dev/codeoracle/src/codeoracle/README.md 2>/dev/null && echo "exists" || echo "skip — file absent"
```

If present, append under the existing "services" section:

```markdown
### handle_resolution

`HandleResolverService` (Plan 8) — dispatches an `EvidenceHandle` to its
per-type strategy. The Verifier (Plan 9) calls this at verify time to
re-fetch every citation's actual content. Strategies: `CodeSpanResolver`,
`DocParagraphResolver`, `GraphQueryResolver`, `StubResolver`.
```

If absent, skip — Plan 1 didn't scaffold this file, and Plan 8 doesn't introduce it.

- [ ] **Step 4: Append Plan 8 closure footer**

Append to the bottom of THIS plan file (`2026-05-21-handle-resolution-and-sourcebot.md`):

```markdown
---

## Plan 8 closure

Status: every Task 1-13 unit + integration test green. Coverage ≥95% on
`src/codeoracle/adapters/sourcebot/` and `src/codeoracle/services/handle_resolution/`.
Live-sourcebot integration test shipped + gated by `--run-live-sourcebot`;
its CI automation is deferred to Plan 13, which owns Sourcebot indexing
bring-up (see Task 6).

**Hand-off to Plan 9 (VerifierService + TrustMatrixService):**
- `HandleResolverService` is the dep Plan 9 will inject into `VerifierService`.
- `ResolverError` is the exception Plan 9 will catch and convert to
  `ResolvedCitation(resolved=False)`.
- TrustMatrixService maps `HandleType` → integer trust score; Plan 8 did
  NOT touch trust scoring (that's Plan 9's responsibility).
- The evidence models (`Citation`, `Claim`, `Verdict`, etc.) were already
  shipped in Plan 3 Task 4 — no model changes needed in Plan 9.

**Next plan file:** `docs/superpowers/plans/2026-05-21-verifier-and-trust-matrix.md`.
```

- [ ] **Step 5: Full Plan 8 sweep — all tests + lint + type**

```bash
uv run pytest tests/unit/adapters/sourcebot/ tests/unit/services/handle_resolution/ tests/unit/core/test_exceptions.py -v
uv run ruff check src/codeoracle/adapters/sourcebot/ src/codeoracle/services/handle_resolution/ src/codeoracle/core/exceptions.py
uv run ty check src/codeoracle/adapters/sourcebot/ src/codeoracle/services/handle_resolution/ src/codeoracle/core/exceptions.py
```

Expected: 100% green. Anything red — STOP and reconcile against the earliest task that introduced the regression.

- [ ] **Step 6: Final commit + push**

```bash
git add docs/architecture.md src/codeoracle/README.md \
        docs/superpowers/plans/2026-05-21-handle-resolution-and-sourcebot.md
# Only stage README if it exists; otherwise drop it from the line above.
git commit -m "docs(plan-8): Task 14 — architecture.md cross-refs + Plan 8 closure"

git push origin main
```

---


## Section 6: Behavioral audit — every claim is pinned by a named test

This is the post-implementation review checklist. Each "MUST" / structural claim in this plan SHOULD map to one or more named tests. If a claim has no pin, either add a test or relax the claim — orphan claims rot.

| Claim source | Claim | Pinned by |
|---|---|---|
| Invariant 2 | `file://path:42-58` resolves to exactly 17 lines (1-indexed, both endpoints inclusive) | `test_fetch_span_returns_inclusive_one_indexed_slice` (Task 5) |
| Invariant 3 | The slicer preserves internal newlines and does NOT re-add a trailing newline | `test_fetch_span_preserves_internal_newlines_no_trailing_newline` (Task 5) |
| Invariant 4 | `TRACE_SPAN` and `DB_RECORD` are explicitly registered (not unmapped) and route through `StubResolver` | `test_trace_span_routes_to_stub_resolver`, `test_db_record_routes_to_stub_resolver` (Task 13) |
| Invariant 5 | The service is stateless — no caching at any layer | (Structural: no caching code in any file. Verified by code search at audit time: `grep -nE "(cache\|memo)" src/codeoracle/services/handle_resolution/`) |
| Invariant 6 | SourcebotClient is single-repo per instance — `fetch_span` does not take a `repo` kwarg | `test_fetch_span_sends_repo_path_ref_query_params` (Task 5) — asserts `repo` comes from config, not call args |
| Invariant 7 | 5xx/429/timeout → `SourcebotTransientError`; 4xx/non-JSON/missing field → `SourcebotTerminalError` | `test_fetch_span_*_raises_(transient\|terminal)` family (Task 5) — 8 tests |
| Invariant 8 | Composition root wires ALL 5 `HandleType` enum values; no fallback / defaultdict | `test_service_dispatch_covers_all_handle_types` (Task 13) |
| Task 4 / wire gotcha 3 | 404 from Sourcebot is terminal, MUST include path + ref in error message | `test_fetch_span_404_raises_terminal` (Task 5) — assertion message includes `"404"` AND path; verified by reading the error in the test output |
| Task 4 / wire gotcha 4 | Empty `source` field with any line range returns `""` | `test_fetch_span_handles_empty_source_field` (Task 5) |
| Task 3a / D-8.4 | `SourcebotSourceResponse` is the single source of truth for the `/api/source` wire contract; every adapter call validates against it; schema drift (missing field / unexpected extra field / wrong type) raises `SourcebotTerminalError` with `"contract validation"` prefix | `test_contract_*` family (Task 3a, 5 tests) + `test_fetch_span_rejects_extra_unexpected_field_in_response`, `test_fetch_span_missing_source_field_raises_terminal` (Task 5) |
| Task 5 / matrix row 21 | Adapter passes a `+wt`-suffixed ref through unchanged to the outbound HTTP query — the strip is a resolver-layer concern, NOT an adapter-layer concern (preserves layer purity) | `test_fetch_span_passes_wt_suffixed_ref_through_unchanged` (Task 5) |
| Task 8a / G21+M-8.1 | `head_sha_only` strips the `+wt<12hex>` dirty-tree suffix from a CodeOracle snapshot SHA when (and only when) it matches the strict-lowercase format; non-matching inputs pass through unchanged | 5 tests in `test_snapshot_ref.py` (Task 8a) — dirty-strip / clean-passthrough / short-suffix-passthrough / uppercase-passthrough / empty-passthrough |
| Task 9 / G21+M-8.1 | `CodeSpanResolver.resolve` calls `head_sha_only(handle.snapshot_sha)` before invoking `SourcebotClient.fetch_span`; a dirty-tree handle resolves against the underlying head SHA, not the working tree | `test_code_span_resolver_strips_wt_suffix_before_fetch` (Task 9) |
| Task 10 / G21+M-8.1 | `DocParagraphResolver.resolve` calls `head_sha_only(handle.snapshot_sha)` before invoking `SourcebotClient.fetch_span`; same dirty-tree convention as code-span | `test_doc_paragraph_resolver_strips_wt_suffix_before_fetch` (Task 10) |
| Task 5 / matrix row 6 | `Authorization: Bearer <api_key>` header on every call | `test_fetch_span_sends_bearer_auth_header` (Task 5) |
| Task 7 | `parse_file_locator` rejects missing scheme, missing line range, malformed line range, start < 1, end < start | 7 dedicated validation tests in `test_base.py` |
| Task 8 | `HandleResolverService.resolve` propagates exceptions from the strategy unchanged | `test_resolve_propagates_strategy_exceptions_unchanged`, `test_resolve_propagates_non_resolver_exceptions_unchanged` (Task 8) |
| Task 9 | `CodeSpanResolver` wraps `SourcebotError` as `ResolverError` preserving cause chain | `test_resolve_wraps_sourcebot_terminal_as_resolver_error`, `test_resolve_wraps_sourcebot_transient_as_resolver_error` (Task 9) |
| Task 9 | `CodeSpanResolver` does NOT normalize returned content | `test_resolve_does_not_normalize_returned_content` (Task 9) |
| Task 11 | `GraphQueryResolver` runs the Cypher body verbatim — no rewriting | `test_resolve_runs_cypher_body_verbatim` (Task 11) |
| Task 11 | Empty graph result → `"[]"` | `test_resolve_returns_empty_list_string_when_no_rows` (Task 11) |
| Task 11 | Output is a deterministic flat JSON array of scalars (float / bool / null included); maps and collections are out of slice scope | `test_stringify_handles_float_bool_and_null_scalars`, `test_resolve_returns_deterministic_json_string` (Task 11) |
| Task 11 | AGE outer quotes stripped on string values | `test_stringify_strips_agtype_quotes_from_string_values` (Task 11) |
| Task 12 | `StubResolver.resolve` ALWAYS raises `UnsupportedHandleTypeError` | `test_resolve_always_raises_unsupported`, `test_resolve_for_db_record_names_db_record` (Task 12) |

**Audit rule:** if this table grows past ~30 rows and any row reads "(Structural: no code exists)", convert it to an executable test. The structural-claim pattern is fine for absences (no caching, no normalization), but the moment a behavior depends on the absence — e.g., "the Verifier MUST see drift because there's no cache" — it stops being structural and needs a test that constructs the scenario and observes the absence.

---


## Section 7: Cross-plan symbol verification

Plan 8 imports from Plans 3, 5, and 7. If those plans' symbol names change (rename, move, or removal), Plan 8 breaks silently at import time only if it's imported by something — which by Plan 8 close, is just the unit tests. Plan 9 will then catch the break loudly. To prevent that, the implementer runs this verification grep before claiming Task 14 done:

**Symbols Plan 8 imports — must exist in prior-plan-merged code:**

| Symbol | Module | Source plan |
|---|---|---|
| `EvidenceHandle`, `HandleType` | `codeoracle.models.evidence` | Plan 3 Task 4 |
| `Citation`, `Claim`, `Verdict` (transitively, via VerifierService consumer) | `codeoracle.models.evidence` | Plan 3 Task 4 |
| `CodeOracleError` | `codeoracle.core.exceptions` | Plan 3 Task 2 |
| `GraphRepository`, `GraphQueryError` | `codeoracle.db.repositories.graph`, `codeoracle.core.exceptions` | Plan 5 Tasks 8 + 1 |
| `pydantic_settings.BaseSettings`, `SettingsConfigDict` | `pydantic_settings` | runtime dep (Plan 3) |
| `httpx.AsyncClient`, `httpx.TimeoutException`, `httpx.ConnectError`, `httpx.HTTPError` | `httpx` | runtime dep (Plan 7) |
| `respx.mock`, `respx.get` | `respx` | dev dep (Plan 7) |

**Run this grep right before Task 14 Step 6 commit:**

```bash
# Positive check — every symbol Plan 8 imports must exist somewhere.
for sym in EvidenceHandle HandleType Citation Claim Verdict CodeOracleError GraphRepository GraphQueryError; do
  hits=$(grep -rE "^class ${sym}\b|^${sym} = " src/codeoracle/ 2>/dev/null | wc -l)
  if [ "$hits" = "0" ]; then
    echo "MISSING: $sym is imported by Plan 8 but is not defined in src/codeoracle/"
  else
    echo "OK: $sym defined ($hits sites)"
  fi
done
```

Expected: every line prefixed `OK:`. Any `MISSING:` line means a prior plan's symbol drifted. STOP and reconcile against the prior plan's task that introduced the symbol — typically by checking git log on the source file:

```bash
git log --oneline -- src/codeoracle/models/evidence.py | head -5
git log --oneline -- src/codeoracle/db/repositories/graph.py | head -5
git log --oneline -- src/codeoracle/core/exceptions.py | head -5
```

**Why not negative grep:** prior plans (e.g., Plan 7) used negative-grep blocks in their cross-plan section to assert "Plan X does NOT import symbol Y" — but those reliably false-positive against prose and inheritance-table mentions. The positive-only grep above catches the case that actually matters (a symbol disappeared upstream) without false positives.

**Symbols Plan 8 provides — must be NEW (no name collision with prior plans):**

```bash
# Negative check — every symbol Plan 8 introduces must NOT already exist.
for sym in SourcebotClient SourcebotConfig SourcebotSourceResponse SourcebotError \
           SourcebotTransientError SourcebotTerminalError ResolverError \
           UnsupportedHandleTypeError HandleResolverService HandleResolverStrategy \
           parse_file_locator head_sha_only \
           CodeSpanResolver DocParagraphResolver GraphQueryResolver StubResolver \
           stringify_cypher_rows; do
  hits=$(grep -rE "^class ${sym}\b|^def ${sym}\b|^${sym} = " src/codeoracle/ 2>/dev/null | wc -l)
  case "$hits" in
    0) echo "MISSING: $sym is supposed to be defined by Plan 8 but isn't (forgot a task?)" ;;
    1) echo "OK: $sym (single definition)" ;;
    *) echo "DUPLICATE: $sym has $hits definitions — pick one" ;;
  esac
done
```

Expected: every line prefixed `OK:`. `MISSING:` means a task didn't actually create what it was supposed to. `DUPLICATE:` means a name accidentally exists in both `src/codeoracle/adapters/sourcebot/` and `src/codeoracle/core/exceptions.py` (or similar collision) — the negative case the test guards against.

---


## Plan 8 close-out checklist

Before the final commit + push (Task 14 Step 6), confirm every line below is checked. Failing items are NOT excuses to commit and "fix forward" — they're the operator's chance to catch a regression before it lands on `main`.

- [ ] Every checkbox in Tasks 1-14 marked done in the implementer's working copy.
- [ ] All unit tests in `tests/unit/adapters/sourcebot/`, `tests/unit/services/handle_resolution/`, and `tests/unit/core/test_exceptions.py` pass (Task 13 Step 4 sweep).
- [ ] `uv run ruff check src/codeoracle/` returns no new findings vs. pre-Plan-8 baseline.
- [ ] `uv run ty check src/codeoracle/` returns no new findings vs. baseline.
- [ ] Section 7's positive + negative grep blocks both show all `OK:` lines (no `MISSING:` or `DUPLICATE:`).
- [ ] Section 6's behavioral-audit table has zero orphan claims (every "MUST" in the plan body is in the table).
- [ ] Live-sourcebot CI automation is explicitly deferred to Plan 13 (no `.github/workflows/live-sourcebot.yml` in this plan — Plan 13 owns Sourcebot indexing bring-up). Task 6 ships the gated test + manual recipe only; confirm Task 14's closure footer records the Plan 13 inheritance.
- [ ] Coverage on `src/codeoracle/adapters/sourcebot/` and `src/codeoracle/services/handle_resolution/` ≥ 95% (Task 5 Step 3 spot-check; extend if needed).
- [ ] No `TODO`, `FIXME`, or `XXX` markers in any new source file or test (`grep -rE "TODO|FIXME|XXX" src/codeoracle/adapters/sourcebot/ src/codeoracle/services/handle_resolution/` returns empty).
- [ ] No commented-out code in any new file (per `project_code_quality_bar` memory). Visual scan + `grep -rE "^[[:space:]]*#[[:space:]]*(class|def|return|raise|import|from)" src/codeoracle/adapters/sourcebot/ src/codeoracle/services/handle_resolution/` returns empty.
- [ ] All new files ≤ 500-line soft target (≤ 700-line hard cap per `project_code_quality_bar`):
  ```bash
  find src/codeoracle/adapters/sourcebot/ src/codeoracle/services/handle_resolution/ -name '*.py' -exec wc -l {} +
  ```
- [ ] `docs/architecture.md` updated (Task 14 Step 1 + Step 2).
- [ ] Plan 8 closure footer added to this plan file (Task 14 Step 4).
- [ ] `git status` clean except for the staged Plan-8 close commit; no inadvertently-included orchestrator-state files (`.swarm/`, `docs/handoff.md`, etc.).
- [ ] `git push origin main` succeeded.

After all boxes check: Plan 8 is shipped. Mark Task #14 in the task list completed; mark Task #15 (Plan 9 — VerifierService + TrustMatrixService) in_progress and begin its plan-write loop.

---
