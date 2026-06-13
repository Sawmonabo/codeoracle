# Plan 9 — Verifier + Trust Matrix Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build `TrustMatrixService` (the only path that converts an LLM-produced `DraftClaim` into a Verifier-ready `Claim`, attaching platform-owned `source_trust`) and `VerifierService` (which resolves every citation on a `Claim` and byte-compares it against live content to render a `PROVEN` / `UNKNOWN` / `REFUTED` `Verdict`).

**Architecture:** Two pure-domain service classes in `src/codeoracle/services/`. `TrustMatrixService` is a stateless ranker — `(HandleType, source_age_days) → 0..10` — plus a `DraftClaim → Claim` converter. `VerifierService` takes a constructor-injected `HandleResolverService` (Plan 8), resolves all citations concurrently with `asyncio.gather`, normalizes both sides of each excerpt comparison, and renders a `Verdict`. Neither service touches an LLM, a database, the filesystem, or the network directly — every external effect is behind the injected resolver — so the entire plan is offline-unit-testable with fakes.

**Tech Stack:** Python 3.14 · `pydantic` v2 (models consumed from Plan 3) · `structlog` (per-citation structured logging) · `asyncio` + `unicodedata` (stdlib) · `pytest` + `pytest-asyncio` (`asyncio_mode = "auto"`).

---

## Cross-plan dependencies

Plan 9 creates **no new models, no new exceptions, no new dependencies, no new pytest markers, and no testcontainers**. It consumes the symbols below and provides two services to later plans.

### Consumes

| From | Symbol | Source file | Used as |
|---|---|---|---|
| Plan 3 | `HandleType`, `VerdictStatus`, `EvidenceHandle`, `DraftCitation`, `Citation`, `ResolvedCitation`, `DraftClaim`, `Claim`, `Verdict` | `src/codeoracle/models/evidence.py` | The entire type vocabulary. Plan 9 imports these; it defines none of them. **Relied-on constraint:** `DraftClaim.citations` and `Claim.citations` both carry `Field(min_length=1)` — Invariant 11 is load-bearing on this (it is what makes `verify()`'s `PROVEN` branch non-vacuous and lets `score_each()` produce a `min_length=1`-valid `Claim`). |
| Plan 3 | `get_audit_logger(name)` | `src/codeoracle/core/logging.py` | `_AUDIT = get_audit_logger(__name__)` in `verifier/service.py`. Returns a lazy `structlog` proxy whose wrapper floor is fixed at INFO independent of `settings.log_level` (so the spec §8.5 crit. 11 citation-resolution line survives at any prod verbosity) while still deferring processors + logger-factory to the global config — so `structlog.testing.capture_logs()` still captures its events in tests. Plan 3 owns this accessor (cross-plan edit applied alongside Plan 9; see Deviation 4). |
| Plan 8 | `ResolverError` | `src/codeoracle/core/exceptions.py` | `VerifierService._resolve_one` catches it → `ResolvedCitation(resolved=False)`. `UnsupportedHandleTypeError` is a subclass, so it is caught by the same `except`. |
| Plan 8 | `HandleResolverService` | `src/codeoracle/services/handle_resolution/__init__.py` | Constructor-injected into `VerifierService`. Plan 9 calls only `await resolver.resolve(handle)`. |
| Plan 8 | GRAPH_QUERY excerpt contract | `GraphQueryResolver` (`strategies/graph_query.py`) | `GraphQueryResolver.resolve()` returns `json.dumps(values, sort_keys=True, separators=(", ", ": "))` with agtype outer quotes stripped from string scalars. The Verifier byte-compares the LLM's claimed excerpt against this exact string; Task 4 pins the contract with round-trip tests. |

### Provides

| To | Symbol | Purpose |
|---|---|---|
| Plan 10 (Agent / LangGraph) | `TrustMatrixService.score_each()` | The agent's `score` node calls it to convert the LLM's `DraftClaim` into a `Claim`. |
| Plan 10 (Agent / LangGraph) | `VerifierService.verify()` | The agent's `verify` node calls it to obtain a `Verdict`. |
| Plan 11 (API / FastAPI) | `codeoracle.services.verifier.VerifierService`, `codeoracle.services.verifier.PROVEN_CONFIDENCE_FLOOR` | The composition root wires `VerifierService(resolver=...)`; the API may surface the floor value in responses. |
| Plan 13 (E2E) | `VerifierService.verify` + the verdict-classification rules + the per-resolution structured log line (criterion 11) | Consumed by Tasks 3-6 + Task 12 |

---

## Plan-level invariants

These hold across every task. A violation in any diff is a plan-execution failure.

1. **No new models.** Plan 3 Task 4 shipped `src/codeoracle/models/evidence.py` with every type Plan 9 needs. Plan 9 imports them and never re-defines them. "Trust Matrix" / "Verifier" in this plan's title name *subsystems*, not model deliverables.

2. **The LLM cannot forge `source_trust` — the type system enforces it.** `VerifierService.verify()` accepts only `Claim`. The only producer of a `Claim` from LLM output is `TrustMatrixService.score_each()`, which sets `source_trust` from `_BASE_RANKS`. `DraftClaim` (raw LLM output) has no `source_trust` field at all. The two-type split *is* the guarantee — no runtime vigilance is required.

3. **`source_trust` never gates the verdict.** The `PROVEN` / `UNKNOWN` / `REFUTED` decision depends only on (confidence floor, resolution success, excerpt match). `source_trust` flows untouched into `Verdict.resolved_citations[].citation.source_trust` for downstream ranking and UI display only. Coupling the verdict to `source_trust` would make Verifier behavior depend on TrustMatrix tuning — spec §6.4 forbids it.

4. **`UNKNOWN` strictly dominates `REFUTED`.** `verify()` checks unresolved citations *before* mismatched ones. `REFUTED` is the strong claim "this is disproven" and may only be made from content the platform actually fetched. Any unresolved citation means missing evidence — the platform cannot make the strong claim — so the verdict is `UNKNOWN` ("cannot prove"). The check order is load-bearing; Task 4 pins it with `test_verify_unresolved_dominates_mismatch`.

5. **`_resolve_one` catches only `ResolverError`.** A `ResolverError` (or its subclass `UnsupportedHandleTypeError`) is an *expected* resolution failure → `ResolvedCitation(resolved=False)`. Any other exception is a *bug* and must propagate uncaught — silently turning a `KeyError` into "unresolved" would hide defects. Plan 8's `HandleResolverService.resolve` docstring explicitly propagates non-`ResolverError` exceptions "as-is"; Plan 9 honors that contract. The `try` block wraps **only** the `await self._resolver.resolve(...)` call, so the `except` cannot swallow a bug in `_normalize_for_comparison` or in `ResolvedCitation` construction.

6. **Excerpt comparison is normalized and one-way.** `_normalize_for_comparison` applies NFC Unicode normalization + LF line endings + per-line trailing-whitespace strip + outer strip to *both* sides before `==`. This absorbs cosmetic drift (line-ending policy, trailing whitespace, Unicode form) so the Verifier is not flaky. It does **not** absorb meaningful drift — different characters or different code still mismatch and still produce `REFUTED`.

7. **Both services are pure-domain.** No LLM, no database, no network, no filesystem. All external I/O is behind the injected `HandleResolverService`. Consequence: every test in this plan is a fast offline unit test with fakes — no testcontainers, no `--run-live-*` gate, no new pytest markers, no new runtime dependencies.

8. **Constructor-injected dependencies are keyword-only.** `VerifierService.__init__(self, *, resolver: HandleResolverService)` mirrors Plan 8's `HandleResolverService.__init__(self, *, strategies=...)`. A required dependency is keyword-only so every call site reads `VerifierService(resolver=...)` and a positional mistake is impossible.

9. **`_BASE_RANKS` is a `ClassVar`.** ruff `RUF012` requires `typing.ClassVar` on a mutable class attribute. The spec §6.4 sketch omits the annotation; Plan 9 writes `ClassVar[dict[HandleType, int]]`.

10. **`trust()` output respects `Citation.source_trust`'s bounds by construction.** `Citation.source_trust` is `Field(ge=0, le=10)`. The maximum base rank is 10 and staleness only subtracts, so the upper bound holds. The `max(0, …)` floor guarantees the lower bound even if a future `HandleType` is added with a base rank ≤ 2 — it is defensive code, not dead code.

11. **No zero-citation claim ever reaches `verify()` or `score_each()`.** Plan 3 constrains both `DraftClaim.citations` and `Claim.citations` with `Field(min_length=1)`, so a citation-free claim cannot be constructed. This is load-bearing twice: `score_each()` maps `draft.citations` element-wise, so a non-empty `DraftClaim` always yields a `Claim` that satisfies its own `min_length=1`; and `verify()`'s `PROVEN` branch (`all N citations resolved and match`) is sound only because `N ≥ 1` — it is never vacuously true for an evidence-free claim. A future relaxation of either constraint would let `verify()` return `PROVEN` with no evidence; it must not be removed without revisiting this plan.

12. **An evidence-free citation forces `UNKNOWN` — at the Verifier layer, deliberately.** A `Citation` whose excerpt normalizes to `""` carries no checkable evidence: a byte-comparison against it would *match* any handle that happens to resolve to empty (or whitespace-only) content, so a confident claim citing such an excerpt would otherwise verify `PROVEN` from no evidence at all. `verify()` scans for an evidence-free citation — `not _normalize_for_comparison(c.excerpt)`, which catches the whitespace-only case too — *before* resolving anything, and returns `UNKNOWN` ("cannot prove"). This is the excerpt-level analogue of Invariant 11's zero-citation guard: Invariant 11 stops a claim with *no citations*; Invariant 12 stops a claim whose citations carry *no evidence*. Task 4 pins it with `test_verify_empty_excerpt_citation_returns_unknown`, `test_verify_whitespace_only_excerpt_returns_unknown`, and `test_verify_empty_excerpt_among_valid_citations_returns_unknown`.

    **Why this enforcement is *not* moved to a Plan 3 `field_validator`.** Plan 3's `DraftCitation.excerpt` / `Citation.excerpt` already carry `Annotated[str, StringConstraints(min_length=1)]`, which rejects the literally empty case at construction. Strengthening that to *also* reject whitespace-only excerpts looks attractive but breaks a legitimate cross-plan flow: Plan 8's `CodeSpanResolver` returns `""` when a source file resolves to empty bytes (Plan 8 L49 — index-time vs. query-time drift, an emptied or vanished file), and Plan 10's `resolve_to_citation` (`agent/runtime.py`, Plan 10 L917-937) builds every `DraftCitation` from `HandleResolverService.resolve(handle)` output, never LLM-authored text (Plan 10 Invariant 1). A model-layer rejection of the resolved empty would raise `ValidationError` at retrieval, which Plan 10's `@agent_node` catch-all (L906-910) would surface as a *terminal* query error — replacing the correct `UNKNOWN` verdict with a hard failure for the *entire* query, including its other (valid) candidates. The Verifier layer is the only place that has the right semantic: per-claim, not per-retrieval, with a structured `reason` and `resolved_citations=[]` so the trace shows the cause.

---

## Deviations from the spec §6 code sketch

Spec §6.2 and §6.4 are explicitly *code sketches*. This plan is the implementation contract; where it departs from the sketch, the reason is recorded here.

1. **`ResolverError` import location.** The §6.2 sketch imports `from codeoracle.services.handle_resolution.strategies.base import ResolverError`. Plan 8 Task 2 placed `ResolverError` in `codeoracle.core.exceptions` (it subclasses `CodeOracleError`). Plan 9 imports `from codeoracle.core.exceptions import ResolverError`. The sketch's path is stale.

2. **Keyword-only constructor.** The §6.2 sketch shows `def __init__(self, resolver: HandleResolverService):`. Plan 9 writes `def __init__(self, *, resolver: HandleResolverService) -> None:` to match Plan 8's `HandleResolverService` and the project's dependency-injection convention (Invariant 8).

3. **`ClassVar` on `_BASE_RANKS`.** The §6.4 sketch annotates `_BASE_RANKS: dict[HandleType, int]`. Plan 9 writes `ClassVar[dict[HandleType, int]]` for ruff `RUF012` (Invariant 9).

4. **Per-citation structured logging on a dedicated audit channel.** Spec §8.5 acceptance criterion 11 requires "one structured log line per citation resolution outcome (`info` on resolve, `warn` on unresolved, `error` on excerpt mismatch)". The §6.2 code sketch omits logging entirely. Plan 9 adds three `_AUDIT` calls inside `_resolve_one` and restructures the method so each outcome has a single clean log point (Task 4).

    **Why `_AUDIT`, not a `log_level`-filtered logger such as a plain `get_logger` (audit D-9.3).** Plan 3's production logger pins `wrapper_class=structlog.make_filtering_bound_logger(log_level)` (Plan 3 L959), where `log_level` is `settings.log_level` (default `INFO`, Plan 3 L516). `make_filtering_bound_logger` bakes the floor into the bound-logger methods at class-build time — a filtered level becomes a literal `return None`, with no per-call bypass. So a citation-resolution line emitted through a `log_level`-filtered logger is *silently dropped* whenever an operator raises prod verbosity: the `info` (resolve/match) line — the most common outcome — vanishes at `log_level=WARNING`, and the `warning` (unresolved) line also vanishes at `log_level=ERROR`. Criterion 11 would then be unsatisfiable for those outcomes in prod. Routing all three outcome lines through `get_audit_logger` (Plan 3, fixed-INFO floor, immune to `settings.log_level`) makes criterion 11 hold at *every* operational verbosity while preserving the `info`/`warning`/`error` gradient and still flowing through the global redaction + render pipeline. **Cross-plan dependency:** `get_audit_logger` is owned by Plan 3 (`core/logging.py`); Plan 9 consumes it. This deviation is *not* satisfiable by Plan 9 alone — a lazy `get_logger` proxy re-reads the *global* wrapper on each call, so it cannot manufacture a different floor; the floor must come from a Plan-3 accessor that fixes `wrapper_class` while leaving processors + factory deferred to the global config (which keeps it observable under `structlog.testing.capture_logs()`).

5. **Explicit `list[ResolvedCitation]` from `asyncio.gather`.** The §6.2 sketch writes bare `resolved = await asyncio.gather(...)`. Plan 9 writes `resolved: list[ResolvedCitation] = list(await asyncio.gather(...))`. The annotation documents the type for `ty` strict; the `list(...)` call is unconditionally correct at runtime regardless of how `asyncio.gather`'s overloaded typeshed stub resolves under a splatted generator expression.

6. **Named staleness constants.** The §6.4 sketch inlines the magic numbers `90` and `2` in `trust()`. Plan 9 lifts them to module constants `_STALE_AGE_DAYS = 90` and `_STALE_TRUST_PENALTY = 2`.

7. **`from __future__ import annotations`.** The spec sketches omit it; Plans 5–8 all use it and Plan 8's `service.py` opens with it. Plan 9's two new modules follow the established convention.

8. **An evidence-free-citation guard in `verify()`.** The §6.2 sketch's `verify()` performs no precondition check beyond the confidence floor. Plan 9 adds one: a citation whose excerpt normalizes to `""` forces `UNKNOWN` before any citation is resolved (plan Invariant 12). Under the bare sketch such a claim would reach the byte-comparison and verify `PROVEN` against empty resolved content; the guard closes that path. The check is intentionally Verifier-layer, not model-layer: Plan 3's `min_length=1` rejects literally empty LLM-authored statements, but a stricter `field_validator` that also rejected whitespace-only would refuse the legitimate Plan 8 L49 empty-resolution path (`CodeSpanResolver` returns `""` for a vanished / emptied file) and Plan 10 L906-910's `@agent_node` catch-all would convert that `ValidationError` into a *terminal* query error rather than the correct per-claim `UNKNOWN` verdict (see Invariant 12's "Why this enforcement is *not* moved" paragraph for the full cross-plan rationale).

---

## Task index

| Task | Deliverable | Files |
|---|---|---|
| 1 | `TrustMatrixService.trust()` + `_BASE_RANKS` | Create `src/codeoracle/services/trust_matrix.py`; create `tests/unit/services/test_trust_matrix.py` |
| 2 | `TrustMatrixService.score_each()` + `_score()` | Overwrite `src/codeoracle/services/trust_matrix.py`; overwrite `tests/unit/services/test_trust_matrix.py` |
| 3 | `_normalize_for_comparison()` + `PROVEN_CONFIDENCE_FLOOR` | Create `src/codeoracle/services/verifier/__init__.py` (placeholder); create `src/codeoracle/services/verifier/service.py`; create `tests/unit/services/verifier/__init__.py`; create `tests/unit/services/verifier/test_normalize.py` |
| 4 | `VerifierService` — `__init__` + `verify()` + `_resolve_one` + per-citation logging | Overwrite `src/codeoracle/services/verifier/service.py`; create `tests/unit/services/verifier/test_service.py` |
| 5 | `verifier/__init__.py` re-exports + service-wiring integration test | Overwrite `src/codeoracle/services/verifier/__init__.py`; create `tests/unit/services/verifier/test_wiring.py` |
| 6 | Documentation update + full-suite green + Plan 9 closure commit | Modify `docs/architecture.md`; this plan file |

`VerifierService.verify()` and its private helper `_resolve_one()` ship in a single task (Task 4): `_resolve_one` is private, so its behavior — the `ResolverError` catch, the three log outcomes, the normalized comparison — is exercised through the public `verify()`, not by reaching into the method directly. Splitting them across tasks would force either implementation-coupled tests or a commit of untested code.

**No dependency-installation task.** Plan 9 introduces no new packages — `structlog` and `pydantic` were locked by Plan 3; `pytest` and `pytest-asyncio` were locked by Plan 1; `asyncio` and `unicodedata` are stdlib.

**How files grow.** When a later task extends a file an earlier task created, the task shows the **complete** new file contents and the engineer overwrites the file. There are no partial appends. This keeps every task self-contained for out-of-order reading and removes any ordering hazard with the module-level `from __future__ import annotations` statement.

**Test helpers are local per file.** Each test file defines its own small builders (`_handle`, `_citation`, …) and its own fake resolver, mirroring Plan 8's per-file `_RecordingStrategy` convention. The duplication is a few trivial lines and keeps each test file independently readable; there is no shared `conftest.py` for these helpers.

---

## Task 1: `TrustMatrixService.trust()` + `_BASE_RANKS`

**Files:**
- Create: `src/codeoracle/services/trust_matrix.py`
- Test: `tests/unit/services/test_trust_matrix.py`

`tests/unit/services/__init__.py` already exists (Plan 4) — do not create it.

- [ ] **Step 1: Pre-flight — confirm the upstream models are importable**

Run:

```bash
uv run python -c "from codeoracle.models.evidence import Citation, Claim, DraftCitation, DraftClaim, EvidenceHandle, HandleType; print('upstream evidence models OK')"
```

Expected: `upstream evidence models OK`. If this fails, Plan 3 was not executed — stop and resolve before continuing.

- [ ] **Step 2: Write the failing test**

Create `tests/unit/services/test_trust_matrix.py`:

```python
"""Unit tests for TrustMatrixService — trust() ranking and staleness decay."""

import pytest

from codeoracle.models.evidence import HandleType
from codeoracle.services.trust_matrix import TrustMatrixService


@pytest.fixture
def svc() -> TrustMatrixService:
    return TrustMatrixService()


@pytest.mark.parametrize(
    ("handle_type", "expected"),
    [
        (HandleType.CODE_SPAN, 10),
        (HandleType.GRAPH_QUERY, 9),
        (HandleType.TRACE_SPAN, 9),
        (HandleType.DB_RECORD, 7),
        (HandleType.DOC_PARAGRAPH, 3),
    ],
)
def test_trust_fresh_source_uses_base_rank(
    svc: TrustMatrixService, handle_type: HandleType, expected: int
) -> None:
    """A source aged 0 days scores its full base rank."""
    assert svc.trust(handle_type, source_age_days=0) == expected


@pytest.mark.parametrize(
    ("handle_type", "expected"),
    [
        (HandleType.CODE_SPAN, 8),
        (HandleType.GRAPH_QUERY, 7),
        (HandleType.TRACE_SPAN, 7),
        (HandleType.DB_RECORD, 5),
        (HandleType.DOC_PARAGRAPH, 1),
    ],
)
def test_trust_stale_source_decays_by_two(
    svc: TrustMatrixService, handle_type: HandleType, expected: int
) -> None:
    """A source older than 90 days loses _STALE_TRUST_PENALTY (2) points."""
    assert svc.trust(handle_type, source_age_days=120) == expected


def test_trust_staleness_boundary_is_strict_greater_than(svc: TrustMatrixService) -> None:
    """Exactly 90 days is NOT stale; 91 days is. The check is `> 90`, not `>= 90`."""
    assert svc.trust(HandleType.CODE_SPAN, source_age_days=90) == 10
    assert svc.trust(HandleType.CODE_SPAN, source_age_days=91) == 8


def test_trust_never_returns_negative(svc: TrustMatrixService) -> None:
    """trust() must satisfy Citation.source_trust's `ge=0` bound for every
    HandleType, even at an extreme age. The `max(0, ...)` floor guards this."""
    for handle_type in HandleType:
        assert svc.trust(handle_type, source_age_days=10_000) >= 0


def test_every_handle_type_has_a_base_rank(svc: TrustMatrixService) -> None:
    """Adding a HandleType without a _BASE_RANKS entry must fail loudly.
    trust() does a dict lookup keyed by handle type; a missing key raises
    KeyError. Iterating the full enum here pins that coverage without
    reaching into the private _BASE_RANKS table."""
    for handle_type in HandleType:
        assert isinstance(svc.trust(handle_type, source_age_days=0), int)
```

- [ ] **Step 3: Run the test, verify it fails**

Run:

```bash
uv run pytest tests/unit/services/test_trust_matrix.py -v
```

Expected: collection error — `ModuleNotFoundError: No module named 'codeoracle.services.trust_matrix'`.

- [ ] **Step 4: Write the implementation**

Create `src/codeoracle/services/trust_matrix.py`:

```python
"""TrustMatrixService — the platform's source-trust ranker.

`trust(handle_type, source_age_days)` maps an evidence kind and age to a
0..10 integer trust score (architecture §16): a fresh source scores its base
rank; a source older than 90 days decays by 2.

Task 2 of Plan 9 adds `score_each()` — the DraftClaim -> Claim converter.
"""

from __future__ import annotations

from typing import ClassVar

from codeoracle.models.evidence import HandleType

_STALE_AGE_DAYS = 90
_STALE_TRUST_PENALTY = 2


class TrustMatrixService:
    """Maps (HandleType, source_age_days) to a 0..10 integer trust score.

    Stateless: no constructor, no injected dependencies. The composition root
    may share a single instance freely.
    """

    _BASE_RANKS: ClassVar[dict[HandleType, int]] = {
        HandleType.CODE_SPAN: 10,  # current code at the snapshot SHA
        HandleType.GRAPH_QUERY: 9,  # the platform's own extracted graph
        HandleType.TRACE_SPAN: 9,  # observed runtime behavior
        HandleType.DB_RECORD: 7,  # schema-bound
        HandleType.DOC_PARAGRAPH: 3,  # docs decay
    }

    def trust(self, handle_type: HandleType, source_age_days: int) -> int:
        """Return the 0..10 trust score for `handle_type` at `source_age_days`.

        A source older than `_STALE_AGE_DAYS` decays by `_STALE_TRUST_PENALTY`.
        The `max(0, ...)` floor keeps the result within `Citation.source_trust`'s
        `ge=0` bound even for a future low-rank HandleType (plan Invariant 10).
        """
        base = self._BASE_RANKS[handle_type]
        if source_age_days > _STALE_AGE_DAYS:
            return max(0, base - _STALE_TRUST_PENALTY)
        return base
```

- [ ] **Step 5: Run the test, verify it passes**

Run:

```bash
uv run pytest tests/unit/services/test_trust_matrix.py -v
```

Expected: PASS — 13 tests pass (5 + 5 parametrized cases, plus 3 standalone).

- [ ] **Step 6: Lint and type-check**

Run:

```bash
uv run ruff format src/codeoracle/services/trust_matrix.py tests/unit/services/test_trust_matrix.py
uv run ruff check src/codeoracle/services/trust_matrix.py tests/unit/services/test_trust_matrix.py
uv run ty check
```

Expected: `ruff format` applies formatting in place (it is the authority on layout — commit whatever it produces); `ruff check` reports `All checks passed!`; `ty check` reports no errors.

- [ ] **Step 7: Commit**

```bash
git add src/codeoracle/services/trust_matrix.py tests/unit/services/test_trust_matrix.py
git commit -m "feat(plan-9): Task 1 — TrustMatrixService.trust() + _BASE_RANKS"
```

---

## Task 2: `TrustMatrixService.score_each()` + `_score()`

**Files:**
- Modify (overwrite): `src/codeoracle/services/trust_matrix.py`
- Test (overwrite): `tests/unit/services/test_trust_matrix.py`

`score_each()` is the `DraftClaim → Claim` converter — the single path that attaches platform-owned `source_trust` (plan Invariant 2). `_score()` converts one citation.

- [ ] **Step 1: Write the failing test**

Overwrite `tests/unit/services/test_trust_matrix.py` with the complete file (Task 1's `trust` tests plus the new `score_each` tests):

```python
"""Unit tests for TrustMatrixService — trust() ranking and DraftClaim -> Claim."""

import pytest

from codeoracle.models.evidence import (
    Claim,
    DraftCitation,
    DraftClaim,
    EvidenceHandle,
    HandleType,
)
from codeoracle.services.trust_matrix import TrustMatrixService


@pytest.fixture
def svc() -> TrustMatrixService:
    return TrustMatrixService()


# --- local builders ---------------------------------------------------------


def _handle(handle_type=HandleType.CODE_SPAN):
    return EvidenceHandle(
        type=handle_type, locator="file://x.py:1-2", snapshot_sha="abcd1234"
    )


def _draft_citation(handle_type=HandleType.CODE_SPAN, excerpt="x = 1"):
    return DraftCitation(handle=_handle(handle_type), excerpt=excerpt)


# --- trust() ----------------------------------------------------------------


@pytest.mark.parametrize(
    ("handle_type", "expected"),
    [
        (HandleType.CODE_SPAN, 10),
        (HandleType.GRAPH_QUERY, 9),
        (HandleType.TRACE_SPAN, 9),
        (HandleType.DB_RECORD, 7),
        (HandleType.DOC_PARAGRAPH, 3),
    ],
)
def test_trust_fresh_source_uses_base_rank(
    svc: TrustMatrixService, handle_type: HandleType, expected: int
) -> None:
    """A source aged 0 days scores its full base rank."""
    assert svc.trust(handle_type, source_age_days=0) == expected


@pytest.mark.parametrize(
    ("handle_type", "expected"),
    [
        (HandleType.CODE_SPAN, 8),
        (HandleType.GRAPH_QUERY, 7),
        (HandleType.TRACE_SPAN, 7),
        (HandleType.DB_RECORD, 5),
        (HandleType.DOC_PARAGRAPH, 1),
    ],
)
def test_trust_stale_source_decays_by_two(
    svc: TrustMatrixService, handle_type: HandleType, expected: int
) -> None:
    """A source older than 90 days loses _STALE_TRUST_PENALTY (2) points."""
    assert svc.trust(handle_type, source_age_days=120) == expected


def test_trust_staleness_boundary_is_strict_greater_than(svc: TrustMatrixService) -> None:
    """Exactly 90 days is NOT stale; 91 days is. The check is `> 90`, not `>= 90`."""
    assert svc.trust(HandleType.CODE_SPAN, source_age_days=90) == 10
    assert svc.trust(HandleType.CODE_SPAN, source_age_days=91) == 8


def test_trust_never_returns_negative(svc: TrustMatrixService) -> None:
    """trust() must satisfy Citation.source_trust's `ge=0` bound for every
    HandleType, even at an extreme age. The `max(0, ...)` floor guards this."""
    for handle_type in HandleType:
        assert svc.trust(handle_type, source_age_days=10_000) >= 0


def test_every_handle_type_has_a_base_rank(svc: TrustMatrixService) -> None:
    """Adding a HandleType without a _BASE_RANKS entry must fail loudly.
    trust() does a dict lookup keyed by handle type; a missing key raises
    KeyError. Iterating the full enum here pins that coverage without
    reaching into the private _BASE_RANKS table."""
    for handle_type in HandleType:
        assert isinstance(svc.trust(handle_type, source_age_days=0), int)


# --- score_each() / DraftClaim -> Claim -------------------------------------


def test_score_each_returns_a_claim(svc: TrustMatrixService) -> None:
    """score_each produces a Claim — the only type VerifierService accepts."""
    draft = DraftClaim(
        statement="the answer", citations=[_draft_citation()], confidence=0.9
    )
    assert isinstance(svc.score_each(draft), Claim)


def test_score_each_attaches_source_trust_to_citations(svc: TrustMatrixService) -> None:
    """Each citation gets the platform-computed trust for its handle type."""
    draft = DraftClaim(
        statement="the answer",
        citations=[_draft_citation(HandleType.CODE_SPAN)],
        confidence=0.9,
    )
    claim = svc.score_each(draft)
    assert [c.source_trust for c in claim.citations] == [10]


def test_score_each_passes_statement_and_confidence_through(
    svc: TrustMatrixService,
) -> None:
    """statement and confidence are copied verbatim — TrustMatrix only adds trust."""
    draft = DraftClaim(
        statement="unchanged statement", citations=[_draft_citation()], confidence=0.73
    )
    claim = svc.score_each(draft)
    assert claim.statement == "unchanged statement"
    assert claim.confidence == 0.73


def test_score_each_scores_contradictions_too(svc: TrustMatrixService) -> None:
    """contradictions is a separate list — score_each MUST score it, not drop it."""
    draft = DraftClaim(
        statement="claimed",
        citations=[_draft_citation(HandleType.CODE_SPAN)],
        confidence=0.8,
        contradictions=[_draft_citation(HandleType.DOC_PARAGRAPH)],
    )
    claim = svc.score_each(draft)
    assert [c.source_trust for c in claim.contradictions] == [3]


def test_score_each_default_source_age_is_zero(svc: TrustMatrixService) -> None:
    """With no source_age_days argument, citations are scored as fresh."""
    draft = DraftClaim(
        statement="fresh",
        citations=[_draft_citation(HandleType.DOC_PARAGRAPH)],
        confidence=0.9,
    )
    assert svc.score_each(draft).citations[0].source_trust == 3


def test_score_each_applies_staleness_to_citations_and_contradictions(
    svc: TrustMatrixService,
) -> None:
    """source_age_days decays every scored citation, on both lists."""
    draft = DraftClaim(
        statement="old",
        citations=[_draft_citation(HandleType.CODE_SPAN)],
        confidence=0.9,
        contradictions=[_draft_citation(HandleType.GRAPH_QUERY)],
    )
    claim = svc.score_each(draft, source_age_days=200)
    assert claim.citations[0].source_trust == 8  # 10 - 2
    assert claim.contradictions[0].source_trust == 7  # 9 - 2


def test_score_each_preserves_handle_and_excerpt(svc: TrustMatrixService) -> None:
    """_score copies handle and excerpt verbatim; only source_trust is new."""
    draft = DraftClaim(
        statement="verbatim",
        citations=[_draft_citation(HandleType.CODE_SPAN, excerpt="def f(): pass")],
        confidence=0.9,
    )
    citation = svc.score_each(draft).citations[0]
    assert citation.excerpt == "def f(): pass"
    assert citation.handle.type == HandleType.CODE_SPAN
```

- [ ] **Step 2: Run the test, verify the new tests fail**

Run:

```bash
uv run pytest tests/unit/services/test_trust_matrix.py -v
```

Expected: the `trust` tests still PASS; the seven `score_each` tests FAIL with `AttributeError: 'TrustMatrixService' object has no attribute 'score_each'`.

- [ ] **Step 3: Write the implementation**

Overwrite `src/codeoracle/services/trust_matrix.py` with the complete file:

```python
"""TrustMatrixService — the platform's source-trust ranker.

Two responsibilities, both pure (no I/O):

1. `trust(handle_type, source_age_days)` — map an evidence kind and age to a
   0..10 integer trust score (architecture §16). A fresh source scores its
   base rank; a source older than 90 days decays by 2.
2. `score_each(draft)` — convert an LLM-produced `DraftClaim` into a
   Verifier-ready `Claim` by attaching the platform-computed `source_trust`
   to every citation and every contradiction. This is the *only* path from
   `DraftClaim` to `Claim`; `VerifierService.verify()` accepts only `Claim`,
   so the LLM can never forge a trust score (plan Invariant 2).
"""

from __future__ import annotations

from typing import ClassVar

from codeoracle.models.evidence import Citation, Claim, DraftCitation, DraftClaim, HandleType

_STALE_AGE_DAYS = 90
_STALE_TRUST_PENALTY = 2


class TrustMatrixService:
    """Maps (HandleType, age) to 0..10 trust, and converts DraftClaim to Claim.

    Stateless: no constructor, no injected dependencies. The composition root
    may share a single instance freely.
    """

    _BASE_RANKS: ClassVar[dict[HandleType, int]] = {
        HandleType.CODE_SPAN: 10,  # current code at the snapshot SHA
        HandleType.GRAPH_QUERY: 9,  # the platform's own extracted graph
        HandleType.TRACE_SPAN: 9,  # observed runtime behavior
        HandleType.DB_RECORD: 7,  # schema-bound
        HandleType.DOC_PARAGRAPH: 3,  # docs decay
    }

    def trust(self, handle_type: HandleType, source_age_days: int) -> int:
        """Return the 0..10 trust score for `handle_type` at `source_age_days`.

        A source older than `_STALE_AGE_DAYS` decays by `_STALE_TRUST_PENALTY`.
        The `max(0, ...)` floor keeps the result within `Citation.source_trust`'s
        `ge=0` bound even for a future low-rank HandleType (plan Invariant 10).
        """
        base = self._BASE_RANKS[handle_type]
        if source_age_days > _STALE_AGE_DAYS:
            return max(0, base - _STALE_TRUST_PENALTY)
        return base

    def score_each(self, draft: DraftClaim, *, source_age_days: int = 0) -> Claim:
        """Convert an LLM `DraftClaim` into a Verifier-ready `Claim`.

        Attaches platform-computed `source_trust` to every citation and every
        contradiction. `statement` and `confidence` pass through unchanged.

        `source_age_days` defaults to 0 (current snapshot only) for the slice;
        multi-snapshot history (Phase 7+) will pass per-citation ages.
        """
        return Claim(
            statement=draft.statement,
            citations=[self._score(dc, source_age_days) for dc in draft.citations],
            confidence=draft.confidence,
            contradictions=[
                self._score(dc, source_age_days) for dc in draft.contradictions
            ],
        )

    def _score(self, draft_citation: DraftCitation, source_age_days: int) -> Citation:
        """Pair one `DraftCitation` with its platform-computed `source_trust`."""
        return Citation(
            handle=draft_citation.handle,
            excerpt=draft_citation.excerpt,
            source_trust=self.trust(draft_citation.handle.type, source_age_days),
        )
```

- [ ] **Step 4: Run the test, verify it passes**

Run:

```bash
uv run pytest tests/unit/services/test_trust_matrix.py -v
```

Expected: PASS — 20 test cases pass (13 `trust` + 7 `score_each`).

- [ ] **Step 5: Lint and type-check**

Run:

```bash
uv run ruff format src/codeoracle/services/trust_matrix.py tests/unit/services/test_trust_matrix.py
uv run ruff check src/codeoracle/services/trust_matrix.py tests/unit/services/test_trust_matrix.py
uv run ty check
```

Expected: `ruff format` applies formatting in place (it is the authority on layout — commit whatever it produces); `ruff check` reports `All checks passed!`; `ty check` reports no errors.

- [ ] **Step 6: Commit**

```bash
git add src/codeoracle/services/trust_matrix.py tests/unit/services/test_trust_matrix.py
git commit -m "feat(plan-9): Task 2 — TrustMatrixService.score_each() — DraftClaim to Claim"
```

---

## Task 3: `_normalize_for_comparison()` + `PROVEN_CONFIDENCE_FLOOR`

**Files:**
- Create: `src/codeoracle/services/verifier/__init__.py` (placeholder — Task 5 populates the re-exports)
- Create: `src/codeoracle/services/verifier/service.py`
- Create: `tests/unit/services/verifier/__init__.py` (empty package marker)
- Test: `tests/unit/services/verifier/test_normalize.py`

This task lands the verifier package skeleton plus its two pure pieces: the `PROVEN_CONFIDENCE_FLOOR` constant and the `_normalize_for_comparison` helper. The `VerifierService` class itself lands in Task 4.

- [ ] **Step 1: Pre-flight — confirm the Verifier's upstream symbols are importable**

Run:

```bash
uv run python -c "from codeoracle.core.exceptions import ResolverError, UnsupportedHandleTypeError; from codeoracle.core.logging import get_audit_logger, get_logger; from codeoracle.services.handle_resolution import HandleResolverService; from codeoracle.models.evidence import Citation, Claim, ResolvedCitation, Verdict, VerdictStatus; print('verifier upstream OK')"
```

Expected: `verifier upstream OK`. A failure means Plan 8 (or Plan 3) was not executed — stop and resolve before continuing.

- [ ] **Step 2: Write the failing test**

Create `tests/unit/services/verifier/__init__.py` as an **empty file** (the package marker pytest needs to import the test module).

Create `tests/unit/services/verifier/test_normalize.py`:

```python
"""Unit tests for the Verifier's excerpt-normalization helper + confidence floor."""

import pytest

from codeoracle.services.verifier.service import (
    PROVEN_CONFIDENCE_FLOOR,
    _normalize_for_comparison,
)


def test_proven_confidence_floor_value() -> None:
    """The floor is 0.6 — pinned so a silent retune is caught in review."""
    assert PROVEN_CONFIDENCE_FLOOR == 0.6


def test_normalize_strips_trailing_whitespace_per_line() -> None:
    assert _normalize_for_comparison("a   \nb\t\nc") == "a\nb\nc"


def test_normalize_crlf_becomes_lf() -> None:
    assert _normalize_for_comparison("a\r\nb\r\nc") == "a\nb\nc"


def test_normalize_lone_cr_becomes_lf() -> None:
    """An old-Mac lone CR is also folded to LF."""
    assert _normalize_for_comparison("a\rb\rc") == "a\nb\nc"


def test_normalize_strips_outer_whitespace() -> None:
    assert _normalize_for_comparison("\n  payload  \n\n") == "payload"


def test_normalize_nfc_nfd_equivalence() -> None:
    """A combining-accent (NFD) string and its precomposed (NFC) twin are
    different code points but normalize equal. Sourcebot and the LLM may
    disagree on Unicode form without that being real content drift."""
    nfd = "cafe\u0301"  # NFD: 'e' + U+0301 combining acute accent (5 code points)
    nfc = "ca" + "f" + "\u00e9"  # NFC: precomposed U+00E9 e-acute (4 code points)
    assert nfd != nfc
    assert _normalize_for_comparison(nfd) == _normalize_for_comparison(nfc)


def test_normalize_is_idempotent() -> None:
    """Normalizing an already-normalized string changes nothing."""
    once = _normalize_for_comparison("  a \r\n b\t \n")
    assert _normalize_for_comparison(once) == once


def test_normalize_preserves_meaningful_difference() -> None:
    """Normalization must NOT collapse genuinely different content — otherwise
    REFUTED could never fire."""
    assert _normalize_for_comparison("return 1") != _normalize_for_comparison("return 2")


def test_normalize_keeps_internal_blank_lines() -> None:
    """A blank line *between* content is structural. Only per-line trailing
    whitespace and the outer edges are stripped — interior blank lines stay."""
    assert _normalize_for_comparison("a\n\nb") == "a\n\nb"


def test_normalize_whitespace_only_line_becomes_blank() -> None:
    """An interior line that is *only* whitespace is rstripped to empty — it
    stays as a structural blank line, it is not deleted. This is also what lets
    the verify() evidence-free-citation guard catch a whitespace-only excerpt:
    such an excerpt normalizes all the way to `""` (plan Invariant 12)."""
    assert _normalize_for_comparison("a\n   \nb") == "a\n\nb"


@pytest.mark.parametrize(
    "graph_query_excerpt",
    ['["alpha", "beta"]', "[3]", "[3.5, true, false, null]", "[]"],
)
def test_normalize_leaves_graph_query_json_untouched(graph_query_excerpt: str) -> None:
    """A GRAPH_QUERY citation excerpt is the exact string GraphQueryResolver
    emits (Plan 8: json.dumps(values, sort_keys=True, separators=(", ", ": "))).
    Normalization must pass it through byte-identical — otherwise every
    GRAPH_QUERY citation would REFUTE against its own resolver output."""
    assert _normalize_for_comparison(graph_query_excerpt) == graph_query_excerpt
```

- [ ] **Step 3: Run the test, verify it fails**

Run:

```bash
uv run pytest tests/unit/services/verifier/test_normalize.py -v
```

Expected: collection error — `ModuleNotFoundError: No module named 'codeoracle.services.verifier'`.

- [ ] **Step 4: Write the implementation**

Create `src/codeoracle/services/verifier/__init__.py` (placeholder — Task 5 replaces it):

```python
"""The evidence Verifier. Public re-exports are populated by Plan 9 Task 5."""
```

Create `src/codeoracle/services/verifier/service.py`:

```python
"""VerifierService — converts a trust-scored Claim into a PROVEN / UNKNOWN /
REFUTED Verdict.

This module lands across two Plan 9 tasks:
  - Task 3: the `PROVEN_CONFIDENCE_FLOOR` constant and `_normalize_for_comparison`.
  - Task 4: the `VerifierService` class (`__init__`, `verify`, `_resolve_one`).
"""

from __future__ import annotations

import unicodedata

# Below this confidence, verify() returns UNKNOWN regardless of how citations
# resolve. Without a floor, an LLM that returned confidence=0.05 with one
# accidentally resolvable citation would be reported PROVEN — a structural
# lie. Initial value 0.6; raise later from slice calibration data (spec §6.2).
PROVEN_CONFIDENCE_FLOOR = 0.6


def _normalize_for_comparison(text: str) -> str:
    """Return `text` in a canonical form for excerpt comparison.

    Applies, in order: NFC Unicode normalization, CRLF/CR -> LF line endings,
    per-line trailing-whitespace strip, and an outer strip. Applied to *both*
    sides of an excerpt comparison so the Verifier does not flake on cosmetic
    drift. It does NOT absorb meaningful drift — different characters still
    compare unequal, so REFUTED still fires for real tampering (Invariant 6).
    """
    nfc = unicodedata.normalize("NFC", text)
    lf = nfc.replace("\r\n", "\n").replace("\r", "\n")
    return "\n".join(line.rstrip() for line in lf.split("\n")).strip()
```

- [ ] **Step 5: Run the test, verify it passes**

Run:

```bash
uv run pytest tests/unit/services/verifier/test_normalize.py -v
```

Expected: PASS — 14 test cases pass (10 standalone + 4 parametrized).

- [ ] **Step 6: Lint and type-check**

Run:

```bash
uv run ruff format src/codeoracle/services/verifier/ tests/unit/services/verifier/
uv run ruff check src/codeoracle/services/verifier/ tests/unit/services/verifier/
uv run ty check
```

Expected: `ruff format` applies formatting in place (it is the authority on layout — commit whatever it produces); `ruff check` reports `All checks passed!`; `ty check` reports no errors.

- [ ] **Step 7: Commit**

```bash
git add src/codeoracle/services/verifier/__init__.py src/codeoracle/services/verifier/service.py tests/unit/services/verifier/__init__.py tests/unit/services/verifier/test_normalize.py
git commit -m "feat(plan-9): Task 3 — verifier package + _normalize_for_comparison + PROVEN_CONFIDENCE_FLOOR"
```

---

## Task 4: `VerifierService` — `__init__` + `verify()` + `_resolve_one`

**Files:**
- Modify (overwrite): `src/codeoracle/services/verifier/service.py`
- Test: `tests/unit/services/verifier/test_service.py`

This task lands the whole `VerifierService` class and its full behavior — the confidence-floor abstention, the evidence-free-citation guard, concurrent citation resolution, the `UNKNOWN`-before-`REFUTED` ordering, and the three per-citation log lines. Every test drives the public `verify()`; the private `_resolve_one` is exercised through it (see the note under the Task index).

- [ ] **Step 1: Write the failing test**

Create `tests/unit/services/verifier/test_service.py`:

```python
"""Unit tests for VerifierService — the PROVEN / UNKNOWN / REFUTED decision."""

import asyncio
import logging

import pytest
import structlog
from structlog.testing import capture_logs

from codeoracle.core.exceptions import ResolverError, UnsupportedHandleTypeError
from codeoracle.core.logging import get_logger
from codeoracle.models.evidence import (
    Citation,
    Claim,
    EvidenceHandle,
    HandleType,
    VerdictStatus,
)
from codeoracle.services.handle_resolution.strategies.graph_query import (
    stringify_cypher_rows,
)
from codeoracle.services.verifier.service import PROVEN_CONFIDENCE_FLOOR, VerifierService


@pytest.fixture(autouse=True)
def _prod_representative_structlog():
    """Pin a *prod-representative* filtering wrapper for each test, then restore.

    `structlog.testing.capture_logs()` swaps the processor chain but leaves
    the wrapper class untouched. A filtering wrapper therefore decides — at the
    bound-logger method, *before* the swapped capture processor runs — whether
    an event is emitted at all. So the wrapper this fixture installs is exactly
    what determines which levels of a plain `get_logger` are observable in tests
    — and, by contrast, makes the `_AUDIT` channel's fixed-INFO floor visible.

    Pinning the global wrapper to `make_filtering_bound_logger(logging.WARNING)`
    makes the test environment match a real operator who has raised prod
    verbosity to WARNING — the representative setting that exposes audit D-9.3.
    Under it, a `log_level`-filtered logger's `info`/`warning` lines are dropped
    (see `test_plain_logger_info_is_filtered_at_prod_level`, the anti-mask
    control), while `VerifierService`'s `_AUDIT` channel — built with a
    fixed-INFO floor independent of `settings.log_level` — still emits all three
    citation-resolution outcomes. A non-filtering `BoundLogger` here would mask
    D-9.3: every level would pass, so the prod-filtered logger's dropped lines
    would look emitted and criterion-11's prod gap would be invisible.

    structlog configuration is process-global mutable state. The fixture
    snapshots it on entry and restores that exact snapshot on exit, rather
    than calling `reset_defaults()` on teardown — a bare reset would leave
    structlog at library defaults and silently discard whatever configuration
    another test module had installed. Snapshot-and-restore leaves global
    state exactly as the test found it, in both directions."""
    saved = structlog.get_config()
    structlog.reset_defaults()
    structlog.configure(
        wrapper_class=structlog.make_filtering_bound_logger(logging.WARNING)
    )
    yield
    structlog.configure(**saved)


# --- local builders ---------------------------------------------------------


def _handle(handle_type=HandleType.CODE_SPAN, locator="file://src/codeoracle/x.py:1-2"):
    return EvidenceHandle(type=handle_type, locator=locator, snapshot_sha="abcd1234")


def _citation(
    excerpt="x = 1",
    *,
    handle_type=HandleType.CODE_SPAN,
    locator="file://src/codeoracle/x.py:1-2",
    source_trust=10,
):
    return Citation(
        handle=_handle(handle_type, locator), excerpt=excerpt, source_trust=source_trust
    )


def _claim(*, citations=None, confidence=0.9, contradictions=None):
    return Claim(
        statement="the function returns 1",
        citations=[_citation()] if citations is None else citations,
        confidence=confidence,
        contradictions=[] if contradictions is None else contradictions,
    )


class _FakeResolver:
    """Test double for HandleResolverService. Duck-types `.resolve()` — tests
    live outside `ty`'s configured `src`, so structural compatibility suffices.

    `by_locator` maps each handle locator to either the string to return or an
    exception instance to raise."""

    def __init__(self, by_locator: dict[str, str | Exception]) -> None:
        self._by_locator = by_locator
        self.calls: list[str] = []

    async def resolve(self, handle: EvidenceHandle) -> str:
        await asyncio.sleep(0)  # behave like a real coroutine; yields the loop
        self.calls.append(handle.locator)
        outcome = self._by_locator[handle.locator]
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


# --- the confidence floor ---------------------------------------------------


@pytest.mark.asyncio
async def test_verify_below_floor_returns_unknown_without_resolving() -> None:
    """Confidence below the floor short-circuits to UNKNOWN — no citation is
    even resolved, and resolved_citations is empty."""
    resolver = _FakeResolver({})
    verdict = await VerifierService(resolver=resolver).verify(_claim(confidence=0.3))
    assert verdict.status == VerdictStatus.UNKNOWN
    assert verdict.resolved_citations == []
    assert resolver.calls == []


@pytest.mark.asyncio
async def test_verify_confidence_at_floor_is_not_abstained() -> None:
    """Confidence exactly at the floor is NOT below it — the check is `<`, not
    `<=` — so verify proceeds to resolution."""
    resolver = _FakeResolver({"file://src/codeoracle/x.py:1-2": "x = 1"})
    verdict = await VerifierService(resolver=resolver).verify(
        _claim(confidence=PROVEN_CONFIDENCE_FLOOR)
    )
    assert verdict.status == VerdictStatus.PROVEN


@pytest.mark.asyncio
async def test_verify_confidence_just_below_floor_is_unknown() -> None:
    resolver = _FakeResolver({"file://src/codeoracle/x.py:1-2": "x = 1"})
    verdict = await VerifierService(resolver=resolver).verify(
        _claim(confidence=PROVEN_CONFIDENCE_FLOOR - 0.01)
    )
    assert verdict.status == VerdictStatus.UNKNOWN


# --- the evidence-free-citation guard ---------------------------------------


@pytest.mark.asyncio
async def test_verify_empty_excerpt_citation_returns_unknown() -> None:
    """A citation whose excerpt is the empty string carries no checkable
    evidence. verify() must return UNKNOWN *before resolving anything* — a
    byte-comparison against `""` would otherwise PROVEN-match any handle that
    resolves to empty content (plan Invariant 12). The resolver is seeded so
    the handle *would* resolve to matching empty content, then asserted never
    to be called — proving the guard fires pre-resolution, not post-match."""
    resolver = _FakeResolver({"file://src/codeoracle/x.py:1-2": ""})
    verdict = await VerifierService(resolver=resolver).verify(
        _claim(citations=[_citation(excerpt="")])
    )
    assert verdict.status == VerdictStatus.UNKNOWN
    assert verdict.resolved_citations == []
    assert resolver.calls == []


@pytest.mark.asyncio
async def test_verify_whitespace_only_excerpt_returns_unknown() -> None:
    """A whitespace-only excerpt normalizes to `""`, so it is evidence-free by
    the same reasoning as a literally empty one. The guard runs the excerpt
    through `_normalize_for_comparison`, so it catches both (plan Invariant 12)."""
    resolver = _FakeResolver({"file://src/codeoracle/x.py:1-2": "   \n\t  "})
    verdict = await VerifierService(resolver=resolver).verify(
        _claim(citations=[_citation(excerpt="   \n\t  ")])
    )
    assert verdict.status == VerdictStatus.UNKNOWN
    assert resolver.calls == []


@pytest.mark.asyncio
async def test_verify_empty_excerpt_among_valid_citations_returns_unknown() -> None:
    """One evidence-free citation among otherwise-valid ones forces UNKNOWN for
    the whole claim, and short-circuits before *any* citation is resolved — so
    the valid sibling is never fetched either. The guard is claim-level."""
    resolver = _FakeResolver({"file://a.py:1-1": "a", "file://b.py:1-1": ""})
    claim = _claim(
        citations=[
            _citation(excerpt="a", locator="file://a.py:1-1"),
            _citation(excerpt="", locator="file://b.py:1-1"),
        ]
    )
    verdict = await VerifierService(resolver=resolver).verify(claim)
    assert verdict.status == VerdictStatus.UNKNOWN
    assert verdict.resolved_citations == []
    assert resolver.calls == []


# --- the three-way verdict --------------------------------------------------


@pytest.mark.asyncio
async def test_verify_all_citations_resolve_and_match_returns_proven() -> None:
    resolver = _FakeResolver({"file://src/codeoracle/x.py:1-2": "x = 1"})
    verdict = await VerifierService(resolver=resolver).verify(_claim())
    assert verdict.status == VerdictStatus.PROVEN
    assert len(verdict.resolved_citations) == 1
    assert verdict.resolved_citations[0].resolved is True
    assert verdict.resolved_citations[0].matches_excerpt is True


@pytest.mark.asyncio
async def test_verify_unresolved_citation_returns_unknown() -> None:
    """A citation that raises ResolverError makes the verdict UNKNOWN — the
    platform cannot prove a claim it cannot fetch evidence for."""
    resolver = _FakeResolver({"file://src/codeoracle/x.py:1-2": ResolverError("sourcebot 404")})
    verdict = await VerifierService(resolver=resolver).verify(_claim())
    assert verdict.status == VerdictStatus.UNKNOWN
    assert "failed to resolve" in verdict.reason
    assert verdict.resolved_citations[0].resolved is False


@pytest.mark.asyncio
async def test_verify_content_drift_returns_refuted() -> None:
    """A citation that resolves but whose live content differs from the cited
    excerpt makes the verdict REFUTED."""
    resolver = _FakeResolver({"file://src/codeoracle/x.py:1-2": "x = 999  # tampered"})
    verdict = await VerifierService(resolver=resolver).verify(_claim())
    assert verdict.status == VerdictStatus.REFUTED
    assert verdict.resolved_citations[0].matches_excerpt is False


@pytest.mark.asyncio
async def test_verify_unresolved_dominates_mismatch() -> None:
    """When one citation is unresolved AND another has drifted content, the
    verdict is UNKNOWN, not REFUTED (plan Invariant 4). REFUTED is the strong
    'disproven' claim; a missing citation means the platform lacks the
    evidence to make it."""
    resolver = _FakeResolver(
        {
            "file://a.py:1-1": ResolverError("a is gone"),  # unresolved
            "file://b.py:1-1": "COMPLETELY DIFFERENT",  # resolved but drifted
        }
    )
    claim = _claim(
        citations=[
            _citation(excerpt="a", locator="file://a.py:1-1"),
            _citation(excerpt="b", locator="file://b.py:1-1"),
        ]
    )
    verdict = await VerifierService(resolver=resolver).verify(claim)
    assert verdict.status == VerdictStatus.UNKNOWN


# --- resolution behavior ----------------------------------------------------


@pytest.mark.asyncio
async def test_verify_cosmetic_drift_is_tolerated() -> None:
    """Content that differs only by trailing whitespace and line endings still
    matches — normalization absorbs cosmetic drift (plan Invariant 6)."""
    resolver = _FakeResolver({"file://src/codeoracle/x.py:1-2": "x = 1   \r\n"})
    verdict = await VerifierService(resolver=resolver).verify(
        _claim(citations=[_citation(excerpt="x = 1")])
    )
    assert verdict.status == VerdictStatus.PROVEN


@pytest.mark.asyncio
async def test_verify_resolves_every_citation() -> None:
    """Every citation on the claim is resolved — not just the first."""
    resolver = _FakeResolver(
        {
            "file://a.py:1-1": "a",
            "file://b.py:1-1": "b",
            "file://c.py:1-1": "c",
        }
    )
    claim = _claim(
        citations=[
            _citation(excerpt="a", locator="file://a.py:1-1"),
            _citation(excerpt="b", locator="file://b.py:1-1"),
            _citation(excerpt="c", locator="file://c.py:1-1"),
        ]
    )
    verdict = await VerifierService(resolver=resolver).verify(claim)
    assert verdict.status == VerdictStatus.PROVEN
    assert sorted(resolver.calls) == ["file://a.py:1-1", "file://b.py:1-1", "file://c.py:1-1"]


@pytest.mark.asyncio
async def test_verify_does_not_resolve_contradictions() -> None:
    """contradictions are scored evidence for downstream display — verify()
    resolves `citations` only, never `contradictions`."""
    resolver = _FakeResolver({"file://src/codeoracle/x.py:1-2": "x = 1"})
    claim = _claim(
        citations=[_citation(excerpt="x = 1", locator="file://src/codeoracle/x.py:1-2")],
        contradictions=[_citation(excerpt="never fetched", locator="file://other.py:9-9")],
    )
    verdict = await VerifierService(resolver=resolver).verify(claim)
    assert verdict.status == VerdictStatus.PROVEN
    assert resolver.calls == ["file://src/codeoracle/x.py:1-2"]  # the contradiction was NOT resolved


@pytest.mark.asyncio
async def test_verify_verdict_independent_of_source_trust() -> None:
    """source_trust never gates the verdict (plan Invariant 3): two claims that
    differ ONLY in source_trust get the same status. The value still flows
    through, untouched, into the resolved citations."""
    resolver = _FakeResolver({"file://src/codeoracle/x.py:1-2": "x = 1"})
    verdict_high = await VerifierService(resolver=resolver).verify(
        _claim(citations=[_citation(source_trust=10)])
    )
    verdict_low = await VerifierService(resolver=resolver).verify(
        _claim(citations=[_citation(source_trust=0)])
    )
    assert verdict_high.status == verdict_low.status == VerdictStatus.PROVEN
    assert verdict_high.resolved_citations[0].citation.source_trust == 10
    assert verdict_low.resolved_citations[0].citation.source_trust == 0


# --- exception handling -----------------------------------------------------


@pytest.mark.asyncio
async def test_verify_propagates_non_resolver_error() -> None:
    """Only ResolverError is an expected failure. Any other exception is a bug
    and must propagate uncaught (plan Invariant 5) — it is NOT silently turned
    into an unresolved citation."""
    resolver = _FakeResolver({"file://src/codeoracle/x.py:1-2": RuntimeError("strategy bug")})
    with pytest.raises(RuntimeError, match="strategy bug"):
        await VerifierService(resolver=resolver).verify(_claim())


@pytest.mark.asyncio
async def test_verify_unsupported_handle_type_is_treated_as_unresolved() -> None:
    """UnsupportedHandleTypeError is a ResolverError subclass, so it is caught
    by the same `except` and produces UNKNOWN — not an uncaught crash."""
    resolver = _FakeResolver(
        {"file://src/codeoracle/x.py:1-2": UnsupportedHandleTypeError("trace_span")}
    )
    verdict = await VerifierService(resolver=resolver).verify(_claim())
    assert verdict.status == VerdictStatus.UNKNOWN
    assert verdict.resolved_citations[0].resolved is False


# --- per-citation structured logging (spec §8.5 criterion 11) ---------------
#
# These tests run under the autouse `_prod_representative_structlog` fixture,
# which pins a WARNING-floor global wrapper. That makes them a real prod-level
# check: an `info`/`warning` line emitted through the global wrapper is dropped,
# so the only reason the assertions below see all three outcomes is that
# `VerifierService` logs them on the fixed-INFO `_AUDIT` channel. The control
# test directly below pins down the anti-mask: it proves the fixture filters a
# plain logger, so if Plan 9 ever regressed `_AUDIT` back to a `log_level`-
# filtered logger, every `info`/`warning` assertion here would start failing.


@pytest.mark.asyncio
async def test_plain_logger_info_is_filtered_at_prod_level() -> None:
    """Anti-mask control for audit D-9.3.

    Under the prod-representative WARNING-floor fixture, a plain `get_logger`
    `info` (the channel Plan 9 deliberately does NOT use for the audit line) is
    dropped, while the `VerifierService` `_AUDIT` channel's `info` survives in
    the same capture. This is what guarantees the four outcome tests below are
    testing the prod path, not a non-filtering test stand-in: if the audit line
    were emitted on a `log_level`-filtered logger, this capture would be empty.
    """
    audit = structlog.wrap_logger(
        None, wrapper_class=structlog.make_filtering_bound_logger(logging.INFO)
    )
    with capture_logs() as logs:
        get_logger("plain").info("verifier.plain_info_should_be_dropped")
        audit.info("verifier.audit_info_should_survive")
    events = [entry["event"] for entry in logs]
    assert "verifier.plain_info_should_be_dropped" not in events
    assert events == ["verifier.audit_info_should_survive"]


@pytest.mark.asyncio
async def test_verify_logs_info_on_resolved_match() -> None:
    resolver = _FakeResolver({"file://src/codeoracle/x.py:1-2": "x = 1"})
    with capture_logs() as logs:
        await VerifierService(resolver=resolver).verify(_claim())
    assert len(logs) == 1
    assert logs[0]["event"] == "verifier.citation_resolved"
    assert logs[0]["log_level"] == "info"
    assert logs[0]["handle_type"] == "code_span"
    assert logs[0]["locator"] == "file://src/codeoracle/x.py:1-2"


@pytest.mark.asyncio
async def test_verify_logs_warning_on_unresolved() -> None:
    resolver = _FakeResolver({"file://src/codeoracle/x.py:1-2": ResolverError("sourcebot 404")})
    with capture_logs() as logs:
        await VerifierService(resolver=resolver).verify(_claim())
    assert len(logs) == 1
    assert logs[0]["event"] == "verifier.citation_unresolved"
    assert logs[0]["log_level"] == "warning"
    assert "sourcebot 404" in logs[0]["error"]


@pytest.mark.asyncio
async def test_verify_logs_error_on_excerpt_mismatch() -> None:
    resolver = _FakeResolver({"file://src/codeoracle/x.py:1-2": "x = 999"})
    with capture_logs() as logs:
        await VerifierService(resolver=resolver).verify(_claim())
    assert len(logs) == 1
    assert logs[0]["event"] == "verifier.citation_excerpt_mismatch"
    assert logs[0]["log_level"] == "error"


@pytest.mark.asyncio
async def test_verify_emits_one_log_line_per_citation() -> None:
    """Spec §8.5 criterion 11: exactly one structured log line per citation
    resolution outcome — here, three citations with three distinct outcomes."""
    resolver = _FakeResolver(
        {
            "file://a.py:1-1": "a",  # -> info
            "file://b.py:1-1": "DRIFTED",  # -> error
            "file://c.py:1-1": ResolverError("gone"),  # -> warning
        }
    )
    claim = _claim(
        citations=[
            _citation(excerpt="a", locator="file://a.py:1-1"),
            _citation(excerpt="b", locator="file://b.py:1-1"),
            _citation(excerpt="c", locator="file://c.py:1-1"),
        ]
    )
    with capture_logs() as logs:
        await VerifierService(resolver=resolver).verify(claim)
    assert len(logs) == 3
    assert {log["log_level"] for log in logs} == {"info", "error", "warning"}


# --- the GRAPH_QUERY excerpt contract (Plan 8) ------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "rows",
    [
        [{"result": '"alpha"'}, {"result": '"beta"'}],  # two agtype-quoted strings
        [{"result": 3}],                                # one int
        [{"result": 3.5}, {"result": True}, {"result": False}, {"result": None}],
        [],                                              # empty result set
    ],
)
async def test_verify_graph_query_excerpt_round_trips_to_proven(
    rows: list[dict[str, object]],
) -> None:
    """A GRAPH_QUERY citation whose excerpt is exactly what GraphQueryResolver
    emits verifies as PROVEN against that same output.

    The expected excerpt is **not hand-written** here — it is produced by
    Plan 8's real `stringify_cypher_rows` helper (imported at the top of this
    file) on both sides of the comparison. If Plan 8 ever changes
    `separators=(", ", ": ")`, its `sort_keys` default, or its agtype-unwrap
    rule, this test breaks immediately at the Plan-9 gate *because* the
    helper output changed — D-9.2 fix: no Plan-9-side assumption of the
    Plan 8 contract is encoded here, the helper *is* the contract."""
    expected_excerpt = stringify_cypher_rows(rows)
    locator = "cypher://q/42"
    resolver = _FakeResolver({locator: expected_excerpt})
    claim = _claim(
        citations=[
            _citation(
                excerpt=expected_excerpt,
                handle_type=HandleType.GRAPH_QUERY,
                locator=locator,
                source_trust=9,
            )
        ]
    )
    verdict = await VerifierService(resolver=resolver).verify(claim)
    assert verdict.status == VerdictStatus.PROVEN


@pytest.mark.asyncio
async def test_verify_graph_query_spacing_drift_refutes() -> None:
    """An LLM that emits compact JSON (`["alpha","beta"]`) does NOT match the
    real `stringify_cypher_rows` output (which uses `separators=(", ", ": ")`)
    and is correctly REFUTED. Normalization does not paper over a structural
    formatting difference.

    The *contract* side calls Plan 8's helper directly (D-9.2 fix), so if
    Plan 8 ever switched to compact separators the helper output would equal
    `compact_excerpt`, this test's `!=` assertion would fail, and Plan 9's
    suite would surface the breaking change before the runtime suite ever
    saw a spurious PROVEN verdict."""
    rows: list[dict[str, object]] = [{"result": '"alpha"'}, {"result": '"beta"'}]
    contract_excerpt = stringify_cypher_rows(rows)  # Plan 8's real output
    compact_excerpt = '["alpha","beta"]'            # what a naive LLM emits
    assert contract_excerpt != compact_excerpt, (
        "Plan 8's stringify_cypher_rows output collides with compact JSON — "
        "the separator contract changed; this test no longer pins drift."
    )

    locator = "cypher://q/7"
    resolver = _FakeResolver({locator: contract_excerpt})
    claim = _claim(
        citations=[
            _citation(
                excerpt=compact_excerpt,
                handle_type=HandleType.GRAPH_QUERY,
                locator=locator,
                source_trust=9,
            )
        ]
    )
    verdict = await VerifierService(resolver=resolver).verify(claim)
    assert verdict.status == VerdictStatus.REFUTED
```

- [ ] **Step 2: Run the test, verify it fails**

Run:

```bash
uv run pytest tests/unit/services/verifier/test_service.py -v
```

Expected: collection error — `ImportError: cannot import name 'VerifierService' from 'codeoracle.services.verifier.service'`.

- [ ] **Step 3: Write the implementation**

Overwrite `src/codeoracle/services/verifier/service.py` with the complete file:

```python
"""VerifierService — converts a trust-scored Claim into a PROVEN / UNKNOWN /
REFUTED Verdict.

The Verifier is the platform's "prove, don't guess" mechanism. It takes a
`Claim` whose citations already carry platform-owned `source_trust` (attached
by `TrustMatrixService.score_each()`), resolves every citation's handle to
live content via the injected `HandleResolverService`, and — after
normalization — byte-compares the live content against the LLM's claimed
excerpt:

  - confidence below `PROVEN_CONFIDENCE_FLOOR`  -> UNKNOWN  (abstain)
  - any citation has an empty excerpt           -> UNKNOWN  (no evidence)
  - any citation fails to resolve               -> UNKNOWN  (missing evidence)
  - any resolved citation's content has drifted -> REFUTED  (drift / tampering)
  - otherwise                                   -> PROVEN

UNKNOWN is checked before REFUTED: a missing citation means the platform lacks
the evidence to make the strong "disproven" claim (plan Invariant 4).
"""

from __future__ import annotations

import asyncio
import unicodedata

from codeoracle.core.exceptions import ResolverError
from codeoracle.core.logging import get_audit_logger
from codeoracle.models.evidence import Citation, Claim, ResolvedCitation, Verdict, VerdictStatus
from codeoracle.services.handle_resolution import HandleResolverService

# The citation-resolution outcome line is an *audit* record (spec §8.5 crit. 11),
# not routine debug. It must survive at any operational verbosity — including a
# production `log_level` of WARNING/ERROR, at which the global filtering wrapper
# (`make_filtering_bound_logger(log_level)`, Plan 3 L959) would drop a plain
# `info`/`warning` line. `get_audit_logger` returns a logger whose floor is fixed
# at INFO independent of `settings.log_level`, so all three outcomes (info /
# warning / error) are always emitted while still flowing through the global
# processor chain (`_redact_secrets`, renderer) and factory. See Deviation 4.
_AUDIT = get_audit_logger(__name__)

# Below this confidence, verify() returns UNKNOWN regardless of how citations
# resolve. Without a floor, an LLM that returned confidence=0.05 with one
# accidentally resolvable citation would be reported PROVEN — a structural
# lie. Initial value 0.6; raise later from slice calibration data (spec §6.2).
PROVEN_CONFIDENCE_FLOOR = 0.6


def _normalize_for_comparison(text: str) -> str:
    """Return `text` in a canonical form for excerpt comparison.

    Applies, in order: NFC Unicode normalization, CRLF/CR -> LF line endings,
    per-line trailing-whitespace strip, and an outer strip. Applied to *both*
    sides of an excerpt comparison so the Verifier does not flake on cosmetic
    drift. It does NOT absorb meaningful drift — different characters still
    compare unequal, so REFUTED still fires for real tampering (Invariant 6).
    """
    nfc = unicodedata.normalize("NFC", text)
    lf = nfc.replace("\r\n", "\n").replace("\r", "\n")
    return "\n".join(line.rstrip() for line in lf.split("\n")).strip()


class VerifierService:
    """Validates a `Claim`, returning a PROVEN / UNKNOWN / REFUTED `Verdict`.

    Pure-domain: the only external effect is the injected `HandleResolverService`.
    The service is stateless — construct once at the composition root and reuse.
    """

    def __init__(self, *, resolver: HandleResolverService) -> None:
        self._resolver = resolver

    async def verify(self, claim: Claim) -> Verdict:
        """Resolve every citation on `claim` and render a `Verdict`.

        Two pre-resolution short-circuits return `UNKNOWN` without resolving
        anything — confidence below the floor, and any empty-excerpt citation;
        the module docstring lists the full verdict precedence. Otherwise,
        citations are resolved concurrently. `claim.contradictions` are NOT
        resolved here — they are scored evidence for downstream display, not
        inputs to this claim's verdict.
        """
        if claim.confidence < PROVEN_CONFIDENCE_FLOOR:
            return Verdict(
                status=VerdictStatus.UNKNOWN,
                reason=(
                    f"claim confidence {claim.confidence:.2f} is below the "
                    f"PROVEN floor {PROVEN_CONFIDENCE_FLOOR}"
                ),
                resolved_citations=[],
            )

        evidence_free = [
            c for c in claim.citations if not _normalize_for_comparison(c.excerpt)
        ]
        if evidence_free:
            return Verdict(
                status=VerdictStatus.UNKNOWN,
                reason=(
                    f"{len(evidence_free)} of {len(claim.citations)} citations "
                    f"carry an empty excerpt — no checkable evidence"
                ),
                resolved_citations=[],
            )

        resolved: list[ResolvedCitation] = list(
            await asyncio.gather(*(self._resolve_one(c) for c in claim.citations))
        )

        unresolved = [rc for rc in resolved if not rc.resolved]
        if unresolved:
            return Verdict(
                status=VerdictStatus.UNKNOWN,
                reason=f"{len(unresolved)} of {len(resolved)} citations failed to resolve",
                resolved_citations=resolved,
            )

        mismatched = [rc for rc in resolved if not rc.matches_excerpt]
        if mismatched:
            return Verdict(
                status=VerdictStatus.REFUTED,
                reason=(
                    f"{len(mismatched)} of {len(resolved)} citations have content "
                    f"that drifted from the cited excerpt"
                ),
                resolved_citations=resolved,
            )

        return Verdict(
            status=VerdictStatus.PROVEN,
            reason=f"all {len(resolved)} citations resolved and match their excerpts",
            resolved_citations=resolved,
        )

    async def _resolve_one(self, citation: Citation) -> ResolvedCitation:
        """Resolve one citation's handle and compare live content to its excerpt.

        Only `ResolverError` (and its subclasses) is caught — that is an
        expected resolution failure. Any other exception is a bug and
        propagates uncaught (plan Invariant 5). The `try` wraps only the
        resolver call, so the `except` cannot mask a defect in normalization
        or in `ResolvedCitation` construction.

        Emits exactly one structured log line per call (spec §8.5 crit. 11) on
        the dedicated `_AUDIT` channel (fixed-INFO floor, immune to the global
        `log_level` filter): `info` when resolved and matching, `error` when
        resolved but drifted, `warning` when unresolved. Using `_AUDIT` rather
        than a `log_level`-filtered logger keeps the audit line satisfiable at
        every prod verbosity — including `log_level=WARNING` (drops `info`) and
        `log_level=ERROR` (also drops `warning`) — for all three outcomes.
        """
        try:
            actual = await self._resolver.resolve(citation.handle)
        except ResolverError as exc:
            _AUDIT.warning(
                "verifier.citation_unresolved",
                locator=citation.handle.locator,
                handle_type=citation.handle.type.value,
                error=str(exc),
            )
            return ResolvedCitation(
                citation=citation,
                resolved=False,
                matches_excerpt=False,
                actual_content=None,
            )

        matches = _normalize_for_comparison(actual) == _normalize_for_comparison(
            citation.excerpt
        )
        if matches:
            _AUDIT.info(
                "verifier.citation_resolved",
                locator=citation.handle.locator,
                handle_type=citation.handle.type.value,
            )
        else:
            _AUDIT.error(
                "verifier.citation_excerpt_mismatch",
                locator=citation.handle.locator,
                handle_type=citation.handle.type.value,
            )
        return ResolvedCitation(
            citation=citation,
            resolved=True,
            matches_excerpt=matches,
            actual_content=actual,
        )
```

- [ ] **Step 4: Run the test, verify it passes**

Run:

```bash
uv run pytest tests/unit/services/verifier/test_service.py -v
```

Expected: PASS — 26 test cases pass (22 standalone + 4 parametrized).

- [ ] **Step 5: Lint and type-check**

Run:

```bash
uv run ruff format src/codeoracle/services/verifier/ tests/unit/services/verifier/
uv run ruff check src/codeoracle/services/verifier/ tests/unit/services/verifier/
uv run ty check
```

Expected: `ruff format` applies formatting in place (it is the authority on layout — commit whatever it produces); `ruff check` reports `All checks passed!`; `ty check` reports no errors.

- [ ] **Step 6: Commit**

```bash
git add src/codeoracle/services/verifier/service.py tests/unit/services/verifier/test_service.py
git commit -m "feat(plan-9): Task 4 — VerifierService.verify() — PROVEN/UNKNOWN/REFUTED + per-citation logging"
```

---

## Task 5: `verifier/__init__.py` re-exports + service-wiring integration test

**Files:**
- Modify (overwrite): `src/codeoracle/services/verifier/__init__.py`
- Test: `tests/unit/services/verifier/test_wiring.py`

This task replaces the Task 3 placeholder `__init__.py` with the public re-exports, and adds an integration test that composes a **real** `HandleResolverService` with `VerifierService` — only the leaf resolver strategy is faked.

- [ ] **Step 1: Write the failing test**

Create `tests/unit/services/verifier/test_wiring.py`:

```python
"""Integration test — VerifierService wired to a real HandleResolverService.

Confirms the public re-export path (`codeoracle.services.verifier`) and that the
two services compose: real HandleResolverService dispatch + real
VerifierService verdict logic, with only the leaf resolver strategy faked.
"""

import asyncio

import pytest

from codeoracle.models.evidence import (
    Citation,
    Claim,
    EvidenceHandle,
    HandleType,
    VerdictStatus,
)
from codeoracle.services.handle_resolution import HandleResolverService
from codeoracle.services.verifier import PROVEN_CONFIDENCE_FLOOR, VerifierService


class _StubStrategy:
    """Duck-types HandleResolverStrategy — returns a fixed string for any handle."""

    def __init__(self, content: str) -> None:
        self._content = content

    async def resolve(self, handle: EvidenceHandle) -> str:
        await asyncio.sleep(0)
        return self._content


def _claim(handle: EvidenceHandle, excerpt: str) -> Claim:
    return Claim(
        statement="a statement under test",
        citations=[Citation(handle=handle, excerpt=excerpt, source_trust=10)],
        confidence=0.9,
    )


def test_public_reexports_are_importable() -> None:
    """VerifierService and PROVEN_CONFIDENCE_FLOOR are reachable from the
    package root — not only from the .service submodule."""
    assert PROVEN_CONFIDENCE_FLOOR == 0.6
    assert VerifierService.__name__ == "VerifierService"


@pytest.mark.asyncio
async def test_verifier_composes_with_handle_resolver_service() -> None:
    """End-to-end: a real HandleResolverService dispatches a CODE_SPAN handle
    to a stub strategy, and VerifierService.verify renders PROVEN."""
    resolver = HandleResolverService(
        strategies={HandleType.CODE_SPAN: _StubStrategy("x = 1")}
    )
    handle = EvidenceHandle(
        type=HandleType.CODE_SPAN,
        locator="file://src/codeoracle/x.py:1-2",
        snapshot_sha="abcd1234",
    )
    verdict = await VerifierService(resolver=resolver).verify(_claim(handle, "x = 1"))
    assert verdict.status == VerdictStatus.PROVEN


@pytest.mark.asyncio
async def test_verifier_renders_unknown_for_unsupported_handle_type() -> None:
    """A real HandleResolverService raises UnsupportedHandleTypeError for an
    unregistered handle type; VerifierService catches it (it is a
    ResolverError subclass) and renders UNKNOWN — not an uncaught crash. This
    exercises the Plan 8 / Plan 9 exception contract with real classes."""
    resolver = HandleResolverService(
        strategies={HandleType.CODE_SPAN: _StubStrategy("x = 1")}
    )
    handle = EvidenceHandle(
        type=HandleType.DOC_PARAGRAPH,  # NOT registered in `strategies`
        locator="doc://readme.md#intro",
        snapshot_sha="abcd1234",
    )
    verdict = await VerifierService(resolver=resolver).verify(_claim(handle, "hello"))
    assert verdict.status == VerdictStatus.UNKNOWN
```

- [ ] **Step 2: Run the test, verify it fails**

Run:

```bash
uv run pytest tests/unit/services/verifier/test_wiring.py -v
```

Expected: collection error — an `ImportError` raised from `codeoracle.services.verifier` (the Task 3 placeholder `__init__.py` re-exports nothing yet, so neither `PROVEN_CONFIDENCE_FLOOR` nor `VerifierService` can be imported from the package).

- [ ] **Step 3: Write the implementation**

Overwrite `src/codeoracle/services/verifier/__init__.py`:

```python
"""The evidence Verifier — converts a trust-scored Claim into a Verdict."""

from codeoracle.services.verifier.service import PROVEN_CONFIDENCE_FLOOR, VerifierService

__all__ = ["PROVEN_CONFIDENCE_FLOOR", "VerifierService"]
```

- [ ] **Step 4: Run the test, verify it passes**

Run:

```bash
uv run pytest tests/unit/services/verifier/test_wiring.py -v
```

Expected: PASS — 3 tests pass.

- [ ] **Step 5: Lint and type-check**

Run:

```bash
uv run ruff format src/codeoracle/services/verifier/ tests/unit/services/verifier/
uv run ruff check src/codeoracle/services/verifier/ tests/unit/services/verifier/
uv run ty check
```

Expected: `ruff format` applies formatting in place; `ruff check` reports `All checks passed!` (the two names in `__init__.py` are exempt from `F401` because they appear in `__all__`); `ty check` reports no errors.

- [ ] **Step 6: Commit**

```bash
git add src/codeoracle/services/verifier/__init__.py tests/unit/services/verifier/test_wiring.py
git commit -m "feat(plan-9): Task 5 — verifier package re-exports + service-wiring integration test"
```

---

## Task 6: Documentation update + full-suite green + Plan 9 closure

**Files:**
- Modify: `docs/architecture.md`
- This plan file (no edit — the closure section is reference material)

This task records the implemented modules in the architecture doc, confirms the whole repository is green, and pushes Plan 9.

- [ ] **Step 1: Update `docs/architecture.md`**

Locate the Evidence Verifier section (§12). Insert the following `###` subsection at the very end of §12's content — after the last existing `###` subheading under §12 (or, if §12 has no subheadings, after its body text) and before the next `##` heading:

```markdown
### Implementation status (Plan 9)

`VerifierService` (`src/codeoracle/services/verifier/`) and `TrustMatrixService`
(`src/codeoracle/services/trust_matrix.py`) were implemented by Plan 9. The
three-valued verdict (`PROVEN` / `UNKNOWN` / `REFUTED`), the
`PROVEN_CONFIDENCE_FLOOR` abstention gate, the normalized excerpt comparison,
and the `DraftClaim` → `Claim` trust-scoring path are live and unit-tested.
`source_trust` is informational metadata only — it never gates the verdict.
```

- [ ] **Step 2: Run the full test suite**

Run:

```bash
uv run pytest
```

Expected: PASS — the entire suite is green. Plan 9 only *adds* files (it modifies no existing `src/` module), so no pre-existing test can regress; this run confirms it.

- [ ] **Step 3: Run the repo-wide quality gates**

Run:

```bash
uv run ruff check
uv run ruff format --check
uv run ty check
```

Expected: all three exit 0 — no lint findings, all files already formatted, no type errors.

- [ ] **Step 4: Walk the closure sections**

Read the **Behavioral audit** and **Close-out checklist** sections below. Confirm every audit row maps to a test that just passed in Step 2, and tick every checklist item. If any item fails, fix it before committing.

- [ ] **Step 5: Commit and push**

```bash
git add docs/architecture.md
git commit -m "docs(plan-9): Task 6 — architecture.md verifier status; Plan 9 complete"
git push origin main
```

This push delivers Tasks 1–6. Plan 9 is complete.

---

## Plan 9 closure

Plan 9 delivers the two services that turn an LLM's claim into a trust-bound verdict.

- **`TrustMatrixService`** (`src/codeoracle/services/trust_matrix.py`) — `trust()` ranks evidence 0..10 by kind and age; `score_each()` is the single converter from a raw `DraftClaim` to a `Claim`. Because `VerifierService.verify()` accepts only `Claim`, and `score_each()` is the only producer of one from LLM output, the LLM structurally cannot forge `source_trust`.

- **`VerifierService`** (`src/codeoracle/services/verifier/`) — `verify()` resolves every citation concurrently through the injected `HandleResolverService`, normalizes both sides of each excerpt comparison, and renders `PROVEN` / `UNKNOWN` / `REFUTED`. It abstains (`UNKNOWN`) below the confidence floor, on an evidence-free citation, or on any unresolved citation, and declares `REFUTED` only from content it actually fetched. `Verdict.reason` is opaque human-readable diagnostic text — downstream consumers (Plan 11's API) may display it but must not parse it; its wording is asserted only by substring in tests and is not a stable contract.

**What Plan 10 (the LangGraph agent) inherits:**
- The agent's `score` node calls `TrustMatrixService.score_each(draft_claim)` to produce a `Claim`.
- The agent's `verify` node calls `await VerifierService.verify(claim)` to get a `Verdict`.
- Both services are constructed once at the composition root (Plan 11's `api/deps.py`): `TrustMatrixService()` takes no arguments; `VerifierService(resolver=...)` takes the Plan 8 `HandleResolverService` built there.

**What Plan 9 deliberately did NOT do:**
- Added no models — Plan 3's `models/evidence.py` already defines every type used here.
- Added no exceptions — Plan 8's `ResolverError` tree is reused unchanged.
- Wired no composition root — `api/deps.py` is Plan 11's responsibility.
- Did not raise the confidence floor from its initial `0.6` — that is a calibration decision for after the slice produces real usage data.

---

## Behavioral audit — every claim is pinned by a named test

Every behavior Plan 9 introduces maps to a test written in the task that introduces the behavior. Task 6 Step 4 walks this table.

| Behavior | Test | Task |
|---|---|---|
| A fresh source scores its full base rank | `test_trust_fresh_source_uses_base_rank` | 1 |
| A source older than 90 days decays by 2 | `test_trust_stale_source_decays_by_two` | 1 |
| The staleness boundary is strict `> 90` (90 is not stale) | `test_trust_staleness_boundary_is_strict_greater_than` | 1 |
| `trust()` never returns a negative score | `test_trust_never_returns_negative` | 1 |
| Every `HandleType` has a base rank (no `KeyError`) | `test_every_handle_type_has_a_base_rank` | 1 |
| `score_each()` produces a `Claim` | `test_score_each_returns_a_claim` | 2 |
| Each citation receives platform-computed `source_trust` | `test_score_each_attaches_source_trust_to_citations` | 2 |
| `statement` and `confidence` pass through unchanged | `test_score_each_passes_statement_and_confidence_through` | 2 |
| `contradictions` are scored, not dropped | `test_score_each_scores_contradictions_too` | 2 |
| `source_age_days` defaults to 0 (fresh) | `test_score_each_default_source_age_is_zero` | 2 |
| Staleness applies to citations and contradictions alike | `test_score_each_applies_staleness_to_citations_and_contradictions` | 2 |
| `handle` and `excerpt` are copied verbatim | `test_score_each_preserves_handle_and_excerpt` | 2 |
| `PROVEN_CONFIDENCE_FLOOR` is `0.6` | `test_proven_confidence_floor_value` | 3 |
| Normalization strips per-line trailing whitespace | `test_normalize_strips_trailing_whitespace_per_line` | 3 |
| Normalization folds CRLF to LF | `test_normalize_crlf_becomes_lf` | 3 |
| Normalization folds a lone CR to LF | `test_normalize_lone_cr_becomes_lf` | 3 |
| Normalization strips outer whitespace | `test_normalize_strips_outer_whitespace` | 3 |
| NFD and NFC forms normalize equal | `test_normalize_nfc_nfd_equivalence` | 3 |
| Normalization is idempotent | `test_normalize_is_idempotent` | 3 |
| Normalization preserves meaningful differences | `test_normalize_preserves_meaningful_difference` | 3 |
| Normalization keeps interior blank lines | `test_normalize_keeps_internal_blank_lines` | 3 |
| A whitespace-only line normalizes to a blank line | `test_normalize_whitespace_only_line_becomes_blank` | 3 |
| Normalization leaves GRAPH_QUERY JSON byte-identical | `test_normalize_leaves_graph_query_json_untouched` | 3 |
| Confidence below the floor → UNKNOWN, no resolution attempted | `test_verify_below_floor_returns_unknown_without_resolving` | 4 |
| Confidence exactly at the floor is not abstained | `test_verify_confidence_at_floor_is_not_abstained` | 4 |
| Confidence just below the floor → UNKNOWN | `test_verify_confidence_just_below_floor_is_unknown` | 4 |
| An empty-excerpt citation → UNKNOWN, no resolution attempted | `test_verify_empty_excerpt_citation_returns_unknown` | 4 |
| A whitespace-only excerpt → UNKNOWN | `test_verify_whitespace_only_excerpt_returns_unknown` | 4 |
| One empty excerpt among valid citations → UNKNOWN for the whole claim | `test_verify_empty_excerpt_among_valid_citations_returns_unknown` | 4 |
| All citations resolve and match → PROVEN | `test_verify_all_citations_resolve_and_match_returns_proven` | 4 |
| Any unresolved citation → UNKNOWN | `test_verify_unresolved_citation_returns_unknown` | 4 |
| Resolved-but-drifted content → REFUTED | `test_verify_content_drift_returns_refuted` | 4 |
| UNKNOWN dominates REFUTED when both apply | `test_verify_unresolved_dominates_mismatch` | 4 |
| Cosmetic-only drift is tolerated → PROVEN | `test_verify_cosmetic_drift_is_tolerated` | 4 |
| Every citation on the claim is resolved | `test_verify_resolves_every_citation` | 4 |
| `contradictions` are not resolved by `verify()` | `test_verify_does_not_resolve_contradictions` | 4 |
| The verdict is independent of `source_trust` | `test_verify_verdict_independent_of_source_trust` | 4 |
| A non-`ResolverError` exception propagates uncaught | `test_verify_propagates_non_resolver_error` | 4 |
| `UnsupportedHandleTypeError` is caught (it is a `ResolverError`) → UNKNOWN | `test_verify_unsupported_handle_type_is_treated_as_unresolved` | 4 |
| `info` audit line on a resolved+matching citation, surviving the prod-representative WARNING floor | `test_verify_logs_info_on_resolved_match` | 4 |
| `warning` audit line on an unresolved citation, surviving the prod-representative WARNING floor | `test_verify_logs_warning_on_unresolved` | 4 |
| `error` audit line on an excerpt mismatch | `test_verify_logs_error_on_excerpt_mismatch` | 4 |
| Exactly one audit line per citation, all surviving the prod-representative WARNING floor | `test_verify_emits_one_log_line_per_citation` | 4 |
| Anti-mask (D-9.3): under the prod WARNING floor a plain `get_logger` `info` is dropped while the `_AUDIT` fixed-INFO `info` survives — so the four rows above test the prod path, not a non-filtering stand-in | `test_plain_logger_info_is_filtered_at_prod_level` | 4 |
| A correct GRAPH_QUERY excerpt round-trips → PROVEN | `test_verify_graph_query_excerpt_round_trips_to_proven` | 4 |
| GRAPH_QUERY spacing drift (compact JSON) → REFUTED | `test_verify_graph_query_spacing_drift_refutes` | 4 |
| `VerifierService` and `PROVEN_CONFIDENCE_FLOOR` are re-exported from the package | `test_public_reexports_are_importable` | 5 |
| `VerifierService` composes with a real `HandleResolverService` | `test_verifier_composes_with_handle_resolver_service` | 5 |
| An unsupported handle type yields UNKNOWN through the real services | `test_verifier_renders_unknown_for_unsupported_handle_type` | 5 |

---

## Cross-plan symbol verification

Plan 9 imports the symbols below and defines none of them. The pre-flight steps (Task 1 Step 1 for the models; Task 3 Step 1 for the resolver, logging, and exception symbols) execute the verification commands; this table is the authority for what those checks confirm.

| Symbol(s) | Defined by | Source file |
|---|---|---|
| `HandleType`, `VerdictStatus`, `EvidenceHandle`, `DraftCitation`, `Citation`, `ResolvedCitation`, `DraftClaim`, `Claim`, `Verdict` | Plan 3, Task 4 | `src/codeoracle/models/evidence.py` |
| `get_audit_logger` (used by `verifier/service.py`), `get_logger` (used by `test_service.py`'s anti-mask control) | Plan 3, Task 3 | `src/codeoracle/core/logging.py` |
| `ResolverError`, `UnsupportedHandleTypeError` | Plan 8, Task 2 | `src/codeoracle/core/exceptions.py` |
| `HandleResolverService` | Plan 8, Task 8 (re-exported by Task 13) | `src/codeoracle/services/handle_resolution/__init__.py` |

The GRAPH_QUERY excerpt format (`json.dumps(values, sort_keys=True, separators=(", ", ": "))`, agtype outer quotes stripped) is a Plan 8 *behavioral* contract carried by `GraphQueryResolver`. Plan 9's two consumer-side round-trip tests (`test_verify_graph_query_excerpt_round_trips_to_proven` and `test_verify_graph_query_spacing_drift_refutes`) pin it by **importing the real helper** — `stringify_cypher_rows` from `codeoracle.services.handle_resolution.strategies.graph_query` (Plan 8 Task 11, "exposed for direct unit testing") — and computing the expected excerpt from raw `rows` input on both sides. The helper *is* the contract: if Plan 8's separator policy, sort-keys default, or agtype-unwrap rule ever changes, the helper output changes and these tests fail at Plan 9 immediately, with no Plan-9-author-encoded assumption of the Plan 8 contract able to drift unnoticed (D-9.2 fix).

Plan 10's `@agent_node` exception-to-terminal-error policy (`agent/runtime.py` L906-910 — any `Exception` from a node body becomes a terminal `error` state, never re-raised into LangGraph) is load-bearing for Invariant 12's layer choice: it is the reason a Plan-3 `field_validator` rejection of platform-resolved empties would degrade a per-claim `UNKNOWN` into a per-query terminal failure. A change to that policy — e.g., translating `ValidationError` specifically into a per-citation `UNKNOWN` rather than a terminal error — would invalidate the cross-plan rationale in the Invariant 12 "Why this enforcement is *not* moved" paragraph and must trigger a re-evaluation of where the evidence-free guard lives.

---

## Plan 9 close-out checklist

- [ ] Tasks 1–6 are each committed with the `feat(plan-9):` / `docs(plan-9):` message shown in the task.
- [ ] `uv run pytest` is green for the whole repository (Task 6 Step 2).
- [ ] `uv run ruff check`, `uv run ruff format --check`, and `uv run ty check` all exit 0 (Task 6 Step 3).
- [ ] `git push origin main` succeeded (Task 6 Step 5), delivering Tasks 1–6.
- [ ] `docs/architecture.md` §12 carries the "Implementation status (Plan 9)" subsection.
- [ ] No new model, exception, runtime dependency, pytest marker, or testcontainer was introduced (Invariants 1 and 7).
- [ ] `src/codeoracle/services/trust_matrix.py` and `src/codeoracle/services/verifier/service.py` are each well under the 700-line ceiling.
- [ ] `_BASE_RANKS` is annotated `ClassVar[dict[HandleType, int]]` (Invariant 9); `VerifierService.__init__` is keyword-only `*, resolver` (Invariant 8).
- [ ] Every Plan 9 commit touches only files under `src/codeoracle/services/`, `tests/unit/services/`, and — for Task 6 — `docs/architecture.md`. Nothing else is staged.
