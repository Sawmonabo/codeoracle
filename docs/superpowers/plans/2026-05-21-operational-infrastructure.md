# Operational Infrastructure (Plan 1A) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Land every operational-infrastructure artifact that Plans 1-10 reference but no plan creates — Dockerfiles, the full CI workflow set, the observability stack, the bootstrap + seed scripts, the semgrep rules, the canonical `defaults.env`, the OTel SDK facade (`core/telemetry.py`), the `utils/` layer, the `CODEOWNERS` file, and the top-level `Makefile`.

**Architecture:** Co-locate every producer with its earliest consumer. `core/telemetry.py` (OTel SDK) ships in the same plan as `infra/observability/otel-collector.yaml` (its export target) so the end-to-end OTel pipeline can be verified in one acceptance test. Container images, CI workflows, and dev scripts are pure config — verified behaviorally (does the stack come up? does the workflow run green? does the span arrive at Tempo?) rather than via unit tests. Cross-cutting code modules (`core/telemetry.py`, `utils/async_helpers.py`) keep strict test-first discipline because they have observable behavior at the API surface.

**Tech Stack:** Docker (multi-stage builds), docker compose, OpenTelemetry SDK + OTLP gRPC exporter, Tempo, Prometheus, Grafana, semgrep, gitleaks, Trivy, GitHub Actions, uv (lockfile refresh).

**Spec source:** `docs/superpowers/specs/2026-05-21-platform-scaffolding-design.md`
- §3.2 — `core/telemetry.py` and `utils/async_helpers.py` placement under `src/codeoracle/`
- §3.4 — `infra/` operational tree (the WHAT of this plan)
- §7.1–§7.6 — CI workflow set; §7.5 already updated this pass (`requires-python = ">=3.14"`)
- §7.9 — secret-handling matrix (Dev / CI default / CI live-llm gate / Prod)
- §8.5 — acceptance gates that depend on this plan's artifacts (image-Trivy gate, OWASP semgrep gate, observability gate 9)

**Plan-audit findings closed:** P0-2 (`core/telemetry.py` orphan), P0-5 (~10 infra orphans), G1, G2, G3, G4, G5, G6, G7, G8, G9, G10, G11, G12. **Not closed by this plan** (handled elsewhere): G13 (`db/repositories/evidence.py` → Plan 3), D-1.2 (`check_file_length.py` shipped as `.sh` → Plan 1's mechanical EDIT), D-1.3 (renovate path drift → Plan 1's mechanical EDIT), D-2.2 (`preflight.sh` restoration → Plan 2 revisions, separate task).

**DAG position:** Executes after Plan 1 (consumes Plan 1's `pyproject.toml` + `ci.yml` skeleton + `.dockerignore`) and before Plan 2 (Plan 2's `preflight.sh` gate runner runs against Plan 1A's compose stack + container images). Plan 1A **creates** the initial `infra/compose/docker-compose.yml` with observability services (otel-collector, tempo, prometheus); Plan 3 later extends the same file with database services (postgres, valkey).

**Cross-plan reconciliation (separate tasks, not in this plan):**
- Plan 3 contains ×3 stale references to "Plan 7 adds `core/telemetry.py`" → re-cite as Plan 1A
- Plan 3 Task 7 currently says "Write `infra/compose/docker-compose.yml`" → rewrite as "EDIT `infra/compose/docker-compose.yml` (created in Plan 1A Task 4), appending postgres + valkey services under `services:`"
- Plan 10 contains "Plan 11/13 own `core/telemetry.py`" → re-cite as Plan 1A
- Plan 7 references `core/telemetry.py` as if it exists → no change needed (Plan 1A now creates it)
- Plan 8 references `infra/config/defaults.env` as the config source → no change needed (Plan 1A now creates it)
- CLI ownership (reconciled): Plan 1 ships the real Typer `app` scaffold (`src/codeoracle/cli/__init__.py` + `src/codeoracle/cli/__main__.py`) and runs BEFORE Plan 1A, so Plan 1A creates no stub and there is nothing to replace — recreating those files here would clobber Plan 1's real `app`. Plan 1A's `VARIANT=cli` Dockerfile smoke test imports the package against Plan 1's files; Plan 5 Task 14 lands the first subcommand `codeoracle index`.

---

## Cross-plan dependencies

Plan 1A is the operational-infrastructure plan: it creates artifacts (Dockerfiles, CI workflows, the observability stack, `core/telemetry.py`, `utils/async_helpers.py`, `infra/config/defaults.env`, the `Makefile`, semgrep rules) that Plans 1-10 reference but no other plan creates. Most cross-plan deps on 1A are *implicit* (env vars consumed via `BaseSettings`, container images consumed by gate runners, the compose stack consumed by integration tests); the explicit symbol-level imports/provides are enumerated below.

### Imports from

None at build time. Plan 1A is foundational — it creates artifacts (telemetry SDK facade, CI workflows, observability stack, unified Dockerfile) that Plans 1-13 consume, but imports no cross-plan symbols itself. `core.telemetry.init_telemetry(config)` reads `CODEORACLE_OTEL_*` env via its own `TelemetryConfig` and does not import Plan 3's `core/logging.py`; the prior row asserting that dependency was aspirational prose (no task-body code references it) and created a spurious Plan-1A↔Plan-3 build-order cycle, now removed.

### Provides

| Provides to | Identifier | Used by |
|---|---|---|
| Plan 3 | observability stack (OTel Collector + Tempo + Prometheus + Grafana) | `infra/observability/{otel-collector.yaml,tempo-config.yaml,prometheus.yml,grafana/dashboards/}` + `infra/compose/docker-compose.yml` services block (Tasks 4-7) — receives OTLP spans from any platform process that calls `core.telemetry.init_telemetry()`; Plan 3's `Task 3` Note and `Cross-plan reconciliation notes` both cite Plan 1A as the consumer-side owner of the telemetry SDK exporter infrastructure. (Loki is intentionally NOT part of this stack: spec §3.4's `infra/observability/` tree authors no Loki config, this plan's File Structure + Tasks 4-7 author none, and every corpus Loki reference — Plan 7 `logs_ingest`, Plan 10's `get_logs_by_trace` tool — is explicitly DEFERRED to a post-slice plan. Log aggregation is added when a `logs_ingest` worker actually lands, not speculatively here.) |
| Plan 11 | `init_telemetry()` | FastAPI server-side OTel instrumentor registered by Plan 11 Task 9; Plan 1A's Prometheus scrape job points at `:8080/metrics` exposed by Plan 11 Task 9 |
| Plan 13 | `infra/observability/*` (Tempo + Prometheus + Grafana) + `core/telemetry.py:init_telemetry()` exporter config; `infra/compose/docker-compose.yml` (the base compose file Plan 1A creates; Plan 2/3/8 extend it with Sourcebot + DB services) + the unified `infra/docker/Dockerfile` (Task 8 — `ARG VARIANT=cli` + `ENV CODEORACLE_VARIANT=${VARIANT}` + `ENTRYPOINT ["/entrypoint.sh"]`) + `entrypoint.sh` (Task 8 — `exec python -m "codeoracle.${CODEORACLE_VARIANT}" "$@"`) + the security + image-scan CI workflows (`security.yml` — Task 13: gitleaks/semgrep/dep-audit/SBOM; `image-build.yml` — Task 14: Trivy image ×5) | Plan 13's slice-acceptance overlay `infra/compose/docker-compose.e2e.yml` (Task 1) references Plan 1A's Tempo + Prometheus services for the observability gates and consumes the unified Dockerfile + entrypoint.sh **AS-COMMITTED except for a single curl install** in the runtime-base stage (Plan 13 amendment #49 — 2026-05-26 — added `RUN apt-get update && apt-get install -y --no-install-recommends curl && rm -rf /var/lib/apt/lists/*` for the slice-e2e HTTP healthchecks because `python:3.14-slim-bookworm` omits curl; api/mcp/agent services use `curl -fsS` probes). The unified Dockerfile + entrypoint.sh contract is otherwise unchanged (VARIANT build-arg + `ENV CODEORACLE_VARIANT=${VARIANT}` default + `ENTRYPOINT ["/entrypoint.sh"]` + `exec python -m "codeoracle.${CODEORACLE_VARIANT}" "$@"` all preserved). 8 slice services share ONE image (`codeoracle:slice-e2e`) via a shared `x-codeoracle-build:` YAML anchor; per-service variant dispatch happens at runtime via `environment.CODEORACLE_VARIANT` overriding the Dockerfile's `ENV` default (compose `environment:` takes precedence over image `ENV` — Plan 13 DECIDE-W "Pattern Y"). entrypoint.sh's shell parameter expansion accepts the dotted module paths Plan 5/6 require for nested worker namespaces (`workers.joern_indexer`, `workers.scip_indexer`, `workers.opengrep_runner`, `workers.embed_indexer`, `workers.reindex_orchestrator`) as cleanly as the flat `api` / `agent` / `mcp` namespaces. Plan 13's `slice-acceptance.yml` reusable-calls only Plan 1's `ci.yml` (the structural/quality invariant gates) via `workflow_call`; `security.yml` + `image-build.yml` run on the slice PR via their own `pull_request`/`push` triggers as required status checks (they are not `workflow_call`-able), per spec §8.7. |

---

## File Structure

```
.github/
├── workflows/
│   ├── ci.yml                       # MODIFIED — remove security steps (now in security.yml)
│   ├── image-build.yml              # NEW — matrix Docker build + Trivy ×5 image variants
│   ├── security.yml                 # NEW — gitleaks (full-history) + semgrep + dep audit + SBOM
│   └── lockfile-refresh.yml         # NEW — weekly cron: uv lock --upgrade + PR
└── CODEOWNERS                       # NEW — team-routed review requirements

infra/
├── docker/
│   ├── Dockerfile                   # NEW — unified multi-stage; VARIANT build arg
│   ├── Dockerfile.model-server      # NEW — CUDA base for vLLM
│   └── entrypoint.sh                # NEW — dispatch on $CODEORACLE_VARIANT
├── compose/
│   └── docker-compose.yml           # NEW — initial stack (otel-collector + tempo + prometheus); Plan 3 extends with postgres + valkey
├── observability/
│   ├── otel-collector.yaml          # NEW — receivers (OTLP) → processors → exporters (Tempo + Prom)
│   ├── tempo-config.yaml            # NEW — local Tempo with OTLP receiver
│   ├── prometheus.yml               # NEW — scrape CodeOracle services + otel-collector self-metrics
│   └── grafana/dashboards/.gitkeep  # NEW — placeholder; dashboards land post-slice
├── scripts/
│   ├── bootstrap_dev.sh             # NEW — fresh-clone-to-running idempotent setup
│   └── seed_test_corpus.sh          # NEW — initial indexing of src/codeoracle/ as the slice corpus
├── security/semgrep/codeoracle-custom.yml  # NEW — custom SAST rules (LLM-direct-import ban, etc.)
└── config/defaults.env              # NEW — canonical default env values

src/codeoracle/
├── core/
│   └── telemetry.py                 # NEW — OTel SDK facade: init_telemetry() + get_tracer()
└── utils/
    ├── __init__.py                  # NEW
    └── async_helpers.py             # NEW — gather_with_concurrency() + wait_for_with_default()

tests/
├── unit/
│   ├── core/test_telemetry.py       # NEW — in-memory exporter, span attribute assertions
│   └── utils/test_async_helpers.py  # NEW — concurrency bound, timeout behavior
└── integration/infra/
    ├── test_compose_stack.py        # NEW — `compose up` brings up all observability services
    ├── test_dockerfile_builds.py    # NEW — each Dockerfile builds + container runs `import codeoracle`
    └── test_otel_pipeline.py        # NEW — Python span → otel-collector → Tempo query roundtrip

Makefile                             # NEW — thin uv/compose/docker wrapper
```

---

## Execution Notes

**Hybrid TDD shape.** Code artifacts (`telemetry.py`, `async_helpers.py`) use strict test-first: failing unit test → minimal implementation → passing test. Config artifacts (YAML, Dockerfile, workflows) use behavioral verification: the "test" is "the workflow runs green" or "the compose stack is healthy" or "the span flows end-to-end." Each task explicitly names which mode it uses.

**Test corpus.** Plan 1A's seed corpus is `src/codeoracle/` itself (the platform's own source) — dogfood property. `seed_test_corpus.sh` initializes the test corpus directory; downstream plans index it.

**Cross-plan commit hygiene.** Same as all prior plans in this series: each task commits its own files; never `git add -A`; never stage generated orchestration artifacts.

**Snapshot-path discipline.** Plan 1A touches no external target-repo working trees or audit snapshot directories — those inputs are outside the scope of every platform-side plan unless a plan explicitly names a controlled corpus.

---

### Task 1: `utils/` layer scaffolding — async helpers

**Why first:** No external dependencies, smallest blast radius, used by Task 2's tests (telemetry's async export uses bounded concurrency). Closes G12.

**Files:**
- Create: `src/codeoracle/utils/__init__.py`
- Create: `src/codeoracle/utils/async_helpers.py`
- Create: `tests/unit/utils/__init__.py`
- Create: `tests/unit/utils/test_async_helpers.py`

**Mode:** Strict TDD (code artifact).

- [ ] **Step 1: Write the failing test**

Create `tests/unit/utils/__init__.py` as empty file. Then write `tests/unit/utils/test_async_helpers.py`:

```python
"""Tests for codeoracle.utils.async_helpers."""

from __future__ import annotations

import asyncio

import pytest

from codeoracle.utils.async_helpers import (
    gather_with_concurrency,
    wait_for_with_default,
)


class TestGatherWithConcurrency:
    @pytest.mark.asyncio
    async def test_returns_results_in_input_order(self) -> None:
        async def slow(i: int, delay: float) -> int:
            await asyncio.sleep(delay)
            return i

        results = await gather_with_concurrency(
            2,
            slow(0, 0.02),
            slow(1, 0.01),
            slow(2, 0.0),
        )
        assert results == [0, 1, 2]

    @pytest.mark.asyncio
    async def test_caps_concurrent_executions(self) -> None:
        current = 0
        peak = 0
        lock = asyncio.Lock()

        async def track() -> None:
            nonlocal current, peak
            async with lock:
                current += 1
                peak = max(peak, current)
            await asyncio.sleep(0.01)
            async with lock:
                current -= 1

        await gather_with_concurrency(2, *(track() for _ in range(10)))
        assert peak <= 2

    @pytest.mark.asyncio
    async def test_rejects_zero_or_negative_limit(self) -> None:
        with pytest.raises(ValueError, match="limit must be >= 1"):
            await gather_with_concurrency(0, asyncio.sleep(0))

        with pytest.raises(ValueError, match="limit must be >= 1"):
            await gather_with_concurrency(-1, asyncio.sleep(0))


class TestWaitForWithDefault:
    @pytest.mark.asyncio
    async def test_returns_value_when_completes(self) -> None:
        async def fast() -> str:
            return "ok"

        result = await wait_for_with_default(fast(), timeout_s=0.1, default="fallback")
        assert result == "ok"

    @pytest.mark.asyncio
    async def test_returns_default_on_timeout(self) -> None:
        async def slow() -> str:
            await asyncio.sleep(0.1)
            return "never"

        result = await wait_for_with_default(slow(), timeout_s=0.01, default="fallback")
        assert result == "fallback"
```

- [ ] **Step 2: Run test to verify it fails**

```
uv run pytest tests/unit/utils/test_async_helpers.py -v
```

Expected: `ModuleNotFoundError: No module named 'codeoracle.utils.async_helpers'`

- [ ] **Step 3: Create the `utils/` package**

Create `src/codeoracle/utils/__init__.py` as empty file (Python package marker).

- [ ] **Step 4: Implement `async_helpers.py`**

Create `src/codeoracle/utils/async_helpers.py`:

```python
"""Small async helpers used across the platform.

Utility-shaped: no business logic, no I/O wrappers, no service-layer
dependencies. Safe to import from any layer.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Coroutine
from typing import TypeVar

T = TypeVar("T")


async def gather_with_concurrency(
    limit: int,
    *coros: Coroutine[None, None, T],
) -> list[T]:
    """Run coroutines concurrently with a bound on parallelism.

    `asyncio.gather` has no concurrency limit; this wraps it with a
    `Semaphore` for parallel-but-bounded execution. Results return in
    input order regardless of completion order.
    """
    if limit < 1:
        raise ValueError(f"limit must be >= 1, got {limit}")

    semaphore = asyncio.Semaphore(limit)

    async def _bounded(coro: Coroutine[None, None, T]) -> T:
        async with semaphore:
            return await coro

    return await asyncio.gather(*[_bounded(c) for c in coros])


async def wait_for_with_default(
    awaitable: Awaitable[T],
    timeout_s: float,
    default: T,
) -> T:
    """Like `asyncio.wait_for`, but returns `default` on timeout instead of raising.

    Used at adapter boundaries where a missing upstream response should
    degrade gracefully rather than cascade an exception. Callers that
    need the raising behavior should call `asyncio.wait_for` directly.
    """
    try:
        return await asyncio.wait_for(awaitable, timeout=timeout_s)
    except asyncio.TimeoutError:
        return default
```

- [ ] **Step 5: Run tests to verify they pass**

```
uv run pytest tests/unit/utils/test_async_helpers.py -v
```

Expected: 5 passed.

- [ ] **Step 6: Commit**

```bash
git add src/codeoracle/utils/__init__.py \
        src/codeoracle/utils/async_helpers.py \
        tests/unit/utils/__init__.py \
        tests/unit/utils/test_async_helpers.py
git commit -m "feat(utils): add async_helpers — gather_with_concurrency + wait_for_with_default

Closes G12 (utils/ layer never scaffolded). First module of the utils/
layer described in spec §3.2. Both helpers are pure async utilities
with no service-layer or adapter-layer dependencies.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

### Task 2: `core/telemetry.py` — OTel SDK facade

**Why:** Closes P0-2 / G11. Provides `init_telemetry()` (process entry) + `get_tracer(name)` (anywhere). Plans 7, 9, 10 currently import `from codeoracle.core.telemetry import get_tracer` without a creator; this task ships the module.

**Files:**
- Create: `src/codeoracle/core/telemetry.py`
- Create: `tests/unit/core/__init__.py`
- Create: `tests/unit/core/test_telemetry.py`

**Mode:** Strict TDD (code artifact). End-to-end OTLP export is verified in Task 18; this task only verifies the facade contract and the in-memory exporter path used by tests.

**Dependencies added to `pyproject.toml`:**
- `opentelemetry-api ~= 1.27`
- `opentelemetry-sdk ~= 1.27`
- `opentelemetry-exporter-otlp-proto-grpc ~= 1.27`

- [ ] **Step 1: Write the failing test**

Create `tests/unit/core/__init__.py` as empty file. Then write `tests/unit/core/test_telemetry.py`:

```python
"""Tests for codeoracle.core.telemetry."""

from __future__ import annotations

import pytest
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
    InMemorySpanExporter,
)

from codeoracle.core.telemetry import (
    TelemetryConfig,
    TelemetryNotInitializedError,
    _reset_for_testing,
    get_tracer,
    init_telemetry,
)


@pytest.fixture(autouse=True)
def _reset_telemetry() -> None:
    _reset_for_testing()
    yield
    _reset_for_testing()


class TestInitTelemetry:
    def test_idempotent(self) -> None:
        init_telemetry(TelemetryConfig(enabled=False))
        first = trace.get_tracer_provider()
        init_telemetry(TelemetryConfig(enabled=False))
        second = trace.get_tracer_provider()
        assert first is second

    def test_disabled_emits_noop_tracer(self) -> None:
        init_telemetry(TelemetryConfig(enabled=False))
        tracer = get_tracer("test")
        with tracer.start_as_current_span("noop") as span:
            assert not span.is_recording()


class TestGetTracer:
    def test_returns_real_tracer_after_init(self) -> None:
        provider = TracerProvider()
        exporter = InMemorySpanExporter()
        provider.add_span_processor(SimpleSpanProcessor(exporter))
        trace.set_tracer_provider(provider)

        tracer = get_tracer("test")
        with tracer.start_as_current_span("unit-test-span") as span:
            span.set_attribute("test.key", "value")

        spans = exporter.get_finished_spans()
        assert len(spans) == 1
        assert spans[0].name == "unit-test-span"
        assert spans[0].attributes["test.key"] == "value"


class TestTelemetryConfig:
    def test_defaults(self) -> None:
        cfg = TelemetryConfig()
        assert cfg.endpoint == "http://localhost:4317"
        assert cfg.enabled is True

    def test_env_override(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("CODEORACLE_OTEL_ENDPOINT", "http://otel:4317")
        monkeypatch.setenv("CODEORACLE_OTEL_ENABLED", "false")
        cfg = TelemetryConfig()
        assert cfg.endpoint == "http://otel:4317"
        assert cfg.enabled is False
```

- [ ] **Step 2: Run test to verify it fails**

```
uv run pytest tests/unit/core/test_telemetry.py -v
```

Expected: `ModuleNotFoundError: No module named 'codeoracle.core.telemetry'`.

- [ ] **Step 3: Add OTel dependencies to `pyproject.toml`**

In `pyproject.toml`, append to `[project] dependencies`:

```toml
"opentelemetry-api ~= 1.27",
"opentelemetry-sdk ~= 1.27",
"opentelemetry-exporter-otlp-proto-grpc ~= 1.27",
```

Run `uv lock` to update `uv.lock`.

- [ ] **Step 4: Implement `core/telemetry.py`**

Create `src/codeoracle/core/telemetry.py`:

```python
"""OpenTelemetry SDK wiring for CodeOracle.

Provides `get_tracer(__name__)` for emitting spans across the platform.
Designed to be safe to import from any layer (no service-layer or
db-layer dependencies). The actual OTLP exporter is wired to the
otel-collector defined in `infra/observability/otel-collector.yaml`.

Service-layer code emitting a span:

    from codeoracle.core.telemetry import get_tracer
    tracer = get_tracer(__name__)
    with tracer.start_as_current_span("verifier.verify") as span:
        span.set_attribute("claim.id", claim.id)
        ...

Process-entry code (api/main.py, worker startup, CLI entry) calls
`init_telemetry()` once at startup. Subsequent calls are no-ops.
"""

from __future__ import annotations

from typing import Final

from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

_SERVICE_NAME: Final[str] = "codeoracle"
_SERVICE_VERSION: Final[str] = "0.1.0"


class TelemetryConfig(BaseSettings):
    """Telemetry configuration sourced from environment."""

    model_config = SettingsConfigDict(
        env_prefix="CODEORACLE_OTEL_",
        env_file=".env",
        extra="ignore",
    )

    endpoint: str = Field(
        default="http://localhost:4317",
        description="OTLP gRPC endpoint for the otel-collector.",
    )
    enabled: bool = Field(
        default=True,
        description=(
            "When False, the SDK uses a no-op TracerProvider — used in "
            "tests and in environments without an otel-collector."
        ),
    )


class TelemetryNotInitializedError(RuntimeError):
    """Raised when telemetry is used before init_telemetry() in strict mode."""


_INITIALIZED: bool = False


def init_telemetry(config: TelemetryConfig | None = None) -> None:
    """Wire up the global TracerProvider. Idempotent.

    Call once at process entry. Multiple calls are no-ops so tests and
    repeated module imports do not duplicate the provider.
    """
    global _INITIALIZED
    if _INITIALIZED:
        return

    cfg = config or TelemetryConfig()
    resource = Resource.create({
        "service.name": _SERVICE_NAME,
        "service.version": _SERVICE_VERSION,
    })
    provider = TracerProvider(resource=resource)

    if cfg.enabled:
        exporter = OTLPSpanExporter(endpoint=cfg.endpoint, insecure=True)
        provider.add_span_processor(BatchSpanProcessor(exporter))

    trace.set_tracer_provider(provider)
    _INITIALIZED = True


def get_tracer(name: str) -> trace.Tracer:
    """Return a Tracer instance scoped to the given module name.

    Convention: callers use `get_tracer(__name__)` to scope spans by
    Python module path.
    """
    return trace.get_tracer(name)


def _reset_for_testing() -> None:
    """Test-only hook to allow re-init in pytest fixtures."""
    global _INITIALIZED
    _INITIALIZED = False
```

- [ ] **Step 5: Run tests to verify they pass**

```
uv run pytest tests/unit/core/test_telemetry.py -v
```

Expected: 5 passed.

- [ ] **Step 6: Commit**

```bash
git add src/codeoracle/core/telemetry.py \
        tests/unit/core/__init__.py \
        tests/unit/core/test_telemetry.py \
        pyproject.toml \
        uv.lock
git commit -m "feat(core): add telemetry SDK facade (init_telemetry + get_tracer)

Closes P0-2 / G11. Plans 7, 9, 10 import codeoracle.core.telemetry but no
plan created it. Wires the OpenTelemetry SDK with an OTLP gRPC exporter
targeted at the otel-collector defined in infra/observability/
(Task 4 of this plan). The export target itself ships in this same plan
so the full pipeline can be verified end-to-end in Task 18.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

### Task 3: `infra/config/defaults.env` — canonical default env values

**Why:** Closes G10. Plan 8 consumes `defaults.env` without a creator. Plan 3 reads it indirectly via `BaseSettings(env_file=)`. This file is the single source of truth for default env values that the platform reads at startup.

**Files:**
- Create: `infra/config/defaults.env`
- Modify: `tests/unit/infra/__init__.py` (create as empty)
- Create: `tests/unit/infra/test_defaults_env.py`

**Mode:** Behavioral verification — the test parses the file and asserts the required keys are present.

- [ ] **Step 1: Write the failing test**

Create `tests/unit/infra/__init__.py` as empty file. Then write `tests/unit/infra/test_defaults_env.py`:

```python
"""Tests for infra/config/defaults.env."""

from __future__ import annotations

from pathlib import Path

import pytest

DEFAULTS_ENV = Path(__file__).resolve().parents[3] / "infra" / "config" / "defaults.env"


REQUIRED_KEYS = {
    # OTel telemetry (consumed by core/telemetry.py)
    "CODEORACLE_OTEL_ENDPOINT",
    "CODEORACLE_OTEL_ENABLED",
    # LLM provider abstraction (consumed by adapters/llm/factory.py — Plan 4)
    "CODEORACLE_LLM_PROVIDER",
    "CODEORACLE_LLM_MODEL",
    "CODEORACLE_EMBEDDING_MODEL",
    "CODEORACLE_EMBEDDING_DIM",
    # Database (consumed by db/session.py — Plan 3)
    "CODEORACLE_PG_HOST",
    "CODEORACLE_PG_PORT",
    "CODEORACLE_PG_DB",
    "CODEORACLE_PG_USER",
    # Trace store (consumed by adapters/tempo — Plan 7)
    "CODEORACLE_TEMPO_ENDPOINT",
    # Sourcebot handle resolution (consumed by adapters/sourcebot — Plan 8)
    "CODEORACLE_SOURCEBOT_ENDPOINT",
}


def _parse_env(path: Path) -> dict[str, str]:
    result: dict[str, str] = {}
    for raw_line in path.read_text().splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            pytest.fail(f"defaults.env line is not KEY=VALUE: {line!r}")
        key, value = line.split("=", 1)
        result[key.strip()] = value.strip()
    return result


def test_defaults_env_exists() -> None:
    assert DEFAULTS_ENV.exists(), f"defaults.env not found at {DEFAULTS_ENV}"


def test_defaults_env_parses_as_keyvalue() -> None:
    parsed = _parse_env(DEFAULTS_ENV)
    assert len(parsed) > 0


def test_defaults_env_contains_required_keys() -> None:
    parsed = _parse_env(DEFAULTS_ENV)
    missing = REQUIRED_KEYS - set(parsed.keys())
    assert not missing, f"defaults.env missing keys: {sorted(missing)}"


def test_defaults_env_embedding_dim_matches_spec() -> None:
    parsed = _parse_env(DEFAULTS_ENV)
    # Spec §9.3 #3 pinned 1536 (OpenAI text-embedding-3-small)
    assert parsed["CODEORACLE_EMBEDDING_DIM"] == "1536"


def test_defaults_env_llm_provider_is_openai() -> None:
    parsed = _parse_env(DEFAULTS_ENV)
    # Memory project-dev-llm-provider-openai: OpenAI is the dev default
    assert parsed["CODEORACLE_LLM_PROVIDER"] == "openai"


def test_defaults_env_otel_endpoint_matches_collector() -> None:
    parsed = _parse_env(DEFAULTS_ENV)
    # otel-collector exposes OTLP gRPC on 4317 (Task 4 of this plan)
    assert parsed["CODEORACLE_OTEL_ENDPOINT"] == "http://otel-collector:4317"
```

- [ ] **Step 2: Run test to verify it fails**

```
uv run pytest tests/unit/infra/test_defaults_env.py -v
```

Expected: `AssertionError: defaults.env not found at .../infra/config/defaults.env`

- [ ] **Step 3: Create `infra/config/defaults.env`**

```bash
mkdir -p infra/config
```

Create `infra/config/defaults.env`:

```
# CodeOracle — canonical default environment values
#
# Loaded by Pydantic BaseSettings classes (core/config.py — Plan 3,
# adapters/llm/factory.py — Plan 4, etc.). Override per-environment
# via .env or process environment variables. Production secret values
# come from the secret manager (§7.9), not this file.

# --- OTel telemetry (core/telemetry.py) ---
CODEORACLE_OTEL_ENDPOINT=http://otel-collector:4317
CODEORACLE_OTEL_ENABLED=true

# --- LLM provider abstraction (adapters/llm/factory.py — Plan 4) ---
# Spec memory project-dev-llm-provider-openai: OpenAI is the dev default.
CODEORACLE_LLM_PROVIDER=openai
CODEORACLE_LLM_MODEL=gpt-4o-mini
CODEORACLE_EMBEDDING_MODEL=text-embedding-3-small
# Spec §9.3 #3: pinned 1536 (one-way door — re-indexing every vector is
# the cost of changing this).
CODEORACLE_EMBEDDING_DIM=1536

# --- Database (db/session.py — Plan 3) ---
CODEORACLE_PG_HOST=postgres
CODEORACLE_PG_PORT=5432
CODEORACLE_PG_DB=codeoracle
CODEORACLE_PG_USER=codeoracle

# --- Trace store (adapters/tempo — Plan 7) ---
CODEORACLE_TEMPO_ENDPOINT=http://tempo:3200

# --- Sourcebot handle resolution (adapters/sourcebot — Plan 8) ---
CODEORACLE_SOURCEBOT_ENDPOINT=http://sourcebot:8080
```

- [ ] **Step 4: Run tests to verify they pass**

```
uv run pytest tests/unit/infra/test_defaults_env.py -v
```

Expected: 6 passed.

- [ ] **Step 5: Commit**

```bash
git add infra/config/defaults.env \
        tests/unit/infra/__init__.py \
        tests/unit/infra/test_defaults_env.py
git commit -m "feat(infra): add canonical defaults.env

Closes G10. defaults.env is consumed by Plan 8's adapter, by Plan 3's
core/config.py via env_file=, and (after Plan 1A's Task 2) by core/
telemetry.py for the OTel endpoint. Pinned values: OpenAI as LLM
provider (memory project-dev-llm-provider-openai), 1536-dim embedding
(spec §9.3 #3 — one-way door).

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

### Task 4: `infra/observability/otel-collector.yaml` + compose service

**Why:** Part 1 of G7 — the otel-collector is the export target of `core/telemetry.py`. Without it, every span is a silent network failure.

**Files:**
- Create: `infra/observability/otel-collector.yaml`
- Create: `infra/compose/docker-compose.yml` — initial stack with `otel-collector` service (Plan 3 later extends with postgres + valkey via a separate task)
- Create: `tests/integration/infra/__init__.py`
- Create: `tests/integration/infra/test_compose_stack.py` (initial — extended in Tasks 5+6)

**Mode:** Behavioral verification — `docker compose up -d otel-collector` brings the service to healthy, the health-check endpoint returns 200.

- [ ] **Step 1: Write the initial integration test**

Create `tests/integration/infra/__init__.py` as empty file. Then write `tests/integration/infra/test_compose_stack.py`:

```python
"""Integration tests: docker compose stack health.

These tests REQUIRE the compose stack to be running. CI runs:

    docker compose -f infra/compose/docker-compose.yml up -d \
        otel-collector tempo prometheus
    uv run pytest tests/integration/infra/test_compose_stack.py -v
    docker compose -f infra/compose/docker-compose.yml down -v

Locally, `make compose-up` does the same.
"""

from __future__ import annotations

import socket
import time

import httpx
import pytest


def _wait_for_port(host: str, port: int, timeout_s: float = 30.0) -> None:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        try:
            with socket.create_connection((host, port), timeout=1.0):
                return
        except OSError:
            time.sleep(0.5)
    pytest.fail(f"{host}:{port} not reachable within {timeout_s}s")


@pytest.mark.integration
class TestOtelCollector:
    def test_health_endpoint_returns_200(self) -> None:
        _wait_for_port("localhost", 13133)
        response = httpx.get("http://localhost:13133/", timeout=5.0)
        assert response.status_code == 200

    def test_otlp_grpc_port_listening(self) -> None:
        _wait_for_port("localhost", 4317)
```

- [ ] **Step 2: Run test to verify it fails (no compose stack yet)**

```
uv run pytest tests/integration/infra/test_compose_stack.py -v -m integration
```

Expected: 2 failed (port not reachable). The `integration` marker is already registered in `pyproject.toml` by Plan 1 — no marker change needed here.

- [ ] **Step 3: Create `infra/observability/otel-collector.yaml`**

```bash
mkdir -p infra/observability
```

Create `infra/observability/otel-collector.yaml`:

```yaml
# OTel Collector config for CodeOracle local + CI stack.
#
# Receives:
#   - OTLP/gRPC on 4317 from in-process SDK (core/telemetry.py)
#   - OTLP/HTTP on 4318 (optional, for browser clients later)
#
# Exports:
#   - traces → Tempo (otlp/tempo)
#   - collector self-metrics → Prometheus (prometheus exporter on 8888)
#
# Health endpoint: 13133

receivers:
  otlp:
    protocols:
      grpc:
        endpoint: 0.0.0.0:4317
      http:
        endpoint: 0.0.0.0:4318

processors:
  batch:
    timeout: 1s
    send_batch_size: 1024
  memory_limiter:
    check_interval: 1s
    limit_mib: 512
    spike_limit_mib: 128

exporters:
  otlp/tempo:
    endpoint: tempo:4317
    tls:
      insecure: true
  prometheus:
    endpoint: 0.0.0.0:8888
  debug:
    verbosity: basic

extensions:
  health_check:
    endpoint: 0.0.0.0:13133

service:
  extensions: [health_check]
  pipelines:
    traces:
      receivers: [otlp]
      processors: [memory_limiter, batch]
      exporters: [otlp/tempo, debug]
  telemetry:
    logs:
      level: info
    metrics:
      address: 0.0.0.0:8889
```

- [ ] **Step 4: Create `infra/compose/docker-compose.yml`**

Plan 1A owns the initial creation of `infra/compose/docker-compose.yml`. Plan 3 later extends the same file by appending postgres + valkey services under `services:` (see Plan 3 Task 7's revised step, listed in this plan's cross-plan reconciliation block).

Create `infra/compose/docker-compose.yml`:

```yaml
# infra/compose/docker-compose.yml — CodeOracle local + CI stack.
# Plan 1A creates this file with observability services.
# Plan 3 extends it with database services (postgres, valkey).

services:
  otel-collector:
    image: otel/opentelemetry-collector-contrib:0.106.1
    container_name: codeoracle-otel-collector
    command: ["--config=/etc/otelcol-contrib/config.yaml"]
    volumes:
      - ../observability/otel-collector.yaml:/etc/otelcol-contrib/config.yaml:ro
    ports:
      - "4317:4317"    # OTLP gRPC
      - "4318:4318"    # OTLP HTTP
      - "8888:8888"    # Prometheus metrics
      - "13133:13133"  # Health check
    healthcheck:
      test: ["CMD", "wget", "-qO-", "http://localhost:13133/"]
      interval: 5s
      timeout: 2s
      retries: 5
      start_period: 5s
```

- [ ] **Step 5: Run the test against the live compose stack**

```bash
docker compose -f infra/compose/docker-compose.yml up -d otel-collector
uv run pytest tests/integration/infra/test_compose_stack.py::TestOtelCollector -v -m integration
docker compose -f infra/compose/docker-compose.yml down -v
```

Expected: 2 passed.

- [ ] **Step 6: Commit**

```bash
git add infra/observability/otel-collector.yaml \
        infra/compose/docker-compose.yml \
        tests/integration/infra/__init__.py \
        tests/integration/infra/test_compose_stack.py
git commit -m "feat(infra): add otel-collector config + compose service

Part 1 of G7. otel-collector is the export target for core/telemetry.py's
OTLP gRPC exporter (added in Task 2 of this plan). Receives traces and
forwards to Tempo (added in Task 5). Health endpoint on 13133 verified
by the integration test.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

### Task 5: `infra/observability/tempo-config.yaml` + compose service

**Why:** Part 2 of G7 — Tempo is the trace storage backend. otel-collector forwards traces to Tempo; downstream plans (Plan 7's traces_ingest) query Tempo.

**Cross-plan live-test + version-pin note (D-7.3):** Plan 7 is the sole consumer of Tempo and relies on Plan 1A as its sole owner (Plan 7 removed its duplicate Tempo creation). Two interfaces matter:
- *OTLP-HTTP `:4318` ingress for Plan 7's gated live test.* Plan 7 ships a default-skipped `--run-live-tempo` test that pushes a synthetic span over OTLP-HTTP to host `:4318`. That receiver lives on the **otel-collector** (the ingress), not on Tempo — and Task 4's otel-collector compose service already maps `"4318:4318"` to the host (see Task 4 Step 4), so the gated test can land with no change here.
- *Tempo image pin.* This task pins `grafana/tempo:2.5.0` (search/query responses use the `batches` span envelope). Plan 7 was authored against Tempo ≥ 2.6.1 (`resourceSpans` envelope); Plan 7's trace-ingest adapter dual-handles **both** envelope shapes, so the 2.5.0 pin is NOT a known break. If a future task bumps this pin to ≥ 2.6.1, the dual-handling adapter still parses correctly — no reciprocal change is forced on Plan 7.

**Files:**
- Create: `infra/observability/tempo-config.yaml`
- Modify: `infra/compose/docker-compose.yml` — add `tempo` service
- Modify: `tests/integration/infra/test_compose_stack.py` — add Tempo health test

**Mode:** Behavioral verification.

- [ ] **Step 1: Extend the integration test for Tempo**

Append to `tests/integration/infra/test_compose_stack.py`:

```python
@pytest.mark.integration
class TestTempo:
    def test_ready_endpoint_returns_200(self) -> None:
        _wait_for_port("localhost", 3200)
        # Tempo's ready endpoint
        for _ in range(20):
            response = httpx.get("http://localhost:3200/ready", timeout=5.0)
            if response.status_code == 200:
                return
            time.sleep(1.0)
        pytest.fail("Tempo /ready did not return 200 within 20s")

    def test_otlp_receiver_port_listening(self) -> None:
        # Tempo internal OTLP receiver
        _wait_for_port("localhost", 14317)
```

- [ ] **Step 2: Run test to verify it fails**

```bash
docker compose -f infra/compose/docker-compose.yml up -d otel-collector
uv run pytest tests/integration/infra/test_compose_stack.py::TestTempo -v -m integration
```

Expected: 2 failed.

- [ ] **Step 3: Create `infra/observability/tempo-config.yaml`**

```yaml
# Tempo config for CodeOracle local + CI stack.
#
# Receives:
#   - OTLP/gRPC on 4317 (internal-only — exposed as 14317 on host)
#
# Storage: local block storage under /var/tempo (compose volume).
# Production swaps for S3/GCS — not in scope for slice.

server:
  http_listen_port: 3200
  grpc_listen_port: 9095

distributor:
  receivers:
    otlp:
      protocols:
        grpc:
          endpoint: 0.0.0.0:4317

ingester:
  trace_idle_period: 10s
  max_block_duration: 5m

compactor:
  compaction:
    block_retention: 24h

storage:
  trace:
    backend: local
    local:
      path: /var/tempo/blocks
    wal:
      path: /var/tempo/wal

metrics_generator:
  registry:
    external_labels:
      source: tempo
  storage:
    path: /var/tempo/generator/wal

overrides:
  defaults:
    metrics_generator:
      processors: [service-graphs, span-metrics]
```

- [ ] **Step 4: Add tempo service to compose**

Append to `infra/compose/docker-compose.yml` under `services:`:

```yaml
  tempo:
    image: grafana/tempo:2.5.0
    container_name: codeoracle-tempo
    command: ["-config.file=/etc/tempo/config.yaml"]
    volumes:
      - ../observability/tempo-config.yaml:/etc/tempo/config.yaml:ro
      - tempo-data:/var/tempo
    ports:
      - "3200:3200"    # HTTP API
      - "14317:4317"   # OTLP gRPC (renamed to avoid conflict with otel-collector)
    healthcheck:
      test: ["CMD", "wget", "-qO-", "http://localhost:3200/ready"]
      interval: 5s
      timeout: 2s
      retries: 10
      start_period: 10s
```

Add to the `volumes:` block at the bottom of compose.yml:

```yaml
volumes:
  tempo-data:
```

- [ ] **Step 5: Update otel-collector → tempo endpoint**

In `infra/observability/otel-collector.yaml`, the `otlp/tempo` exporter already points at `tempo:4317` (matches compose service name + internal container port). No change needed.

- [ ] **Step 6: Run the test against the live compose stack**

```bash
docker compose -f infra/compose/docker-compose.yml up -d otel-collector tempo
uv run pytest tests/integration/infra/test_compose_stack.py::TestTempo -v -m integration
docker compose -f infra/compose/docker-compose.yml down -v
```

Expected: 2 passed.

- [ ] **Step 7: Commit**

```bash
git add infra/observability/tempo-config.yaml \
        infra/compose/docker-compose.yml \
        tests/integration/infra/test_compose_stack.py
git commit -m "feat(infra): add Tempo config + compose service

Part 2 of G7. Tempo stores traces forwarded from otel-collector;
downstream plans (Plan 7's traces_ingest) query Tempo via its HTTP API.
Local block storage for slice; production swaps for S3/GCS post-slice.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

### Task 6: `infra/observability/prometheus.yml` + compose service

**Why:** Part 3 of G7 — Prometheus scrapes service metrics (FastAPI middleware, otel-collector self-metrics). Slice does not strictly require Prometheus for the §8.5 gates, but the spec §3.4 lists it; skipping it leaves a spec deviation.

**Files:**
- Create: `infra/observability/prometheus.yml`
- Modify: `infra/compose/docker-compose.yml` — add `prometheus` service
- Modify: `tests/integration/infra/test_compose_stack.py` — add Prometheus health test

**Mode:** Behavioral verification.

- [ ] **Step 1: Extend the integration test for Prometheus**

Append to `tests/integration/infra/test_compose_stack.py`:

```python
@pytest.mark.integration
class TestPrometheus:
    def test_ready_endpoint_returns_200(self) -> None:
        _wait_for_port("localhost", 9090)
        for _ in range(20):
            response = httpx.get("http://localhost:9090/-/ready", timeout=5.0)
            if response.status_code == 200:
                return
            time.sleep(1.0)
        pytest.fail("Prometheus /-/ready did not return 200 within 20s")

    def test_scrapes_otel_collector_self_metrics(self) -> None:
        # otel-collector exposes self-metrics on :8888; Prometheus is configured
        # to scrape that endpoint. The targets API confirms the scrape config.
        response = httpx.get(
            "http://localhost:9090/api/v1/targets",
            timeout=5.0,
        )
        assert response.status_code == 200
        targets = response.json()["data"]["activeTargets"]
        target_urls = {t["scrapeUrl"] for t in targets}
        assert any("otel-collector:8888" in u for u in target_urls)
```

- [ ] **Step 2: Run test to verify it fails**

Expected: 2 failed (no Prometheus service yet).

- [ ] **Step 3: Create `infra/observability/prometheus.yml`**

```yaml
# Prometheus config for CodeOracle local + CI stack.
#
# Scrapes:
#   - otel-collector self-metrics (8888)
#   - otel-collector pipeline metrics (8889)
#   - CodeOracle services (api, workers — adapters add /metrics endpoints per Plan 11+)

global:
  scrape_interval: 15s
  evaluation_interval: 15s

scrape_configs:
  - job_name: otel-collector
    static_configs:
      - targets: ["otel-collector:8888"]

  - job_name: otel-collector-pipeline
    static_configs:
      - targets: ["otel-collector:8889"]

  - job_name: tempo
    static_configs:
      - targets: ["tempo:3200"]
    metrics_path: /metrics
```

- [ ] **Step 4: Add prometheus service to compose**

Append to `infra/compose/docker-compose.yml` under `services:`:

```yaml
  prometheus:
    image: prom/prometheus:v2.54.1
    container_name: codeoracle-prometheus
    command:
      - "--config.file=/etc/prometheus/prometheus.yml"
      - "--storage.tsdb.path=/prometheus"
      - "--web.console.libraries=/usr/share/prometheus/console_libraries"
      - "--web.console.templates=/usr/share/prometheus/consoles"
    volumes:
      - ../observability/prometheus.yml:/etc/prometheus/prometheus.yml:ro
      - prometheus-data:/prometheus
    ports:
      - "9090:9090"
    healthcheck:
      test: ["CMD", "wget", "-qO-", "http://localhost:9090/-/ready"]
      interval: 5s
      timeout: 2s
      retries: 10
      start_period: 10s
```

Add to `volumes:`:

```yaml
volumes:
  prometheus-data:
```

- [ ] **Step 5: Run the test**

```bash
docker compose -f infra/compose/docker-compose.yml up -d otel-collector tempo prometheus
uv run pytest tests/integration/infra/test_compose_stack.py::TestPrometheus -v -m integration
docker compose -f infra/compose/docker-compose.yml down -v
```

Expected: 2 passed.

- [ ] **Step 6: Commit**

```bash
git add infra/observability/prometheus.yml \
        infra/compose/docker-compose.yml \
        tests/integration/infra/test_compose_stack.py
git commit -m "feat(infra): add Prometheus config + compose service

Part 3 of G7. Scrapes otel-collector self-metrics + pipeline metrics +
Tempo metrics. CodeOracle service /metrics endpoints land with their respective
plans (api in Plan 11, workers in Plan 6/7). Slice does not require
Prometheus for any §8.5 gate, but spec §3.4 names the file — skipping
it would leave an undeclared deviation.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

### Task 7: Grafana dashboards placeholder

**Why:** Part 4 of G7 — spec §3.4 names `grafana/dashboards/`. Slice does not require dashboards (§8.5 has no Grafana gate); a `.gitkeep` declares the directory's existence without YAGNI-violating content.

**Files:**
- Create: `infra/observability/grafana/dashboards/.gitkeep`

**Mode:** Trivial — no test beyond file existence.

- [ ] **Step 1: Create the placeholder**

```bash
mkdir -p infra/observability/grafana/dashboards
touch infra/observability/grafana/dashboards/.gitkeep
```

- [ ] **Step 2: Commit**

```bash
git add infra/observability/grafana/dashboards/.gitkeep
git commit -m "feat(infra): scaffold grafana/dashboards directory

Closes G7 fully. Empty .gitkeep declares the directory; dashboards
themselves are deferred until there is span/metric data worth
visualizing (post-slice). Spec §3.4 names the path; leaving it absent
would surface as an undeclared deviation in the next audit.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

### Task 8: `infra/docker/Dockerfile` + `entrypoint.sh` — unified multi-stage image

**Why:** Part 1 of G6 — the base image for every CodeOracle process (api, mcp, workers, cli, agent). One Dockerfile with a `VARIANT` build arg + an entrypoint shim that dispatches.

**Files:**
- Create: `infra/docker/Dockerfile`
- Create: `infra/docker/entrypoint.sh`
- Consume (from Plan 1, not created here): `src/codeoracle/cli/__init__.py` + `src/codeoracle/cli/__main__.py` — Plan 1 ships both the package marker and the Typer `app` scaffold; Plan 1A's `VARIANT=cli` Dockerfile smoke test imports the package against Plan 1's files (Plan 5 Task 14 lands the first subcommand `codeoracle index`). Plan 1A creates neither — recreating them here would clobber Plan 1's real Typer `app`, since Plan 1A runs after Plan 1.
- Create: `tests/integration/infra/test_dockerfile_builds.py`

**Preconditions:** Plan 1 ships `.dockerignore` at repo root; Plan 1A consumes it but does not re-create it.

**Mode:** Behavioral verification — `docker build` succeeds; container's Python entrypoint runs `import codeoracle` cleanly.

- [ ] **Step 1: Write the build/run test**

Create `tests/integration/infra/test_dockerfile_builds.py`:

```python
"""Integration tests: Dockerfile builds and resulting containers run.

These tests require Docker daemon access. CI runs them on a runner with
the docker-in-docker buildkit driver.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]


def _docker_build(variant: str, tag: str) -> None:
    result = subprocess.run(
        [
            "docker", "build",
            "--build-arg", f"VARIANT={variant}",
            "-f", str(REPO_ROOT / "infra" / "docker" / "Dockerfile"),
            "-t", tag,
            str(REPO_ROOT),
        ],
        capture_output=True,
        text=True,
        timeout=600,
    )
    if result.returncode != 0:
        pytest.fail(f"docker build failed:\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}")


def _docker_run(
    tag: str,
    cmd: list[str],
    *,
    entrypoint: str | None = None,
) -> subprocess.CompletedProcess[str]:
    # The unified Dockerfile sets ENTRYPOINT to entrypoint.sh, which execs
    # `python -m codeoracle.$CODEORACLE_VARIANT "$@"`. Any cmd we pass without an
    # entrypoint override becomes argv to codeoracle.cli, not a standalone
    # command. Pass `entrypoint=` for diagnostic checks (python, id) that
    # must run directly.
    args = ["docker", "run", "--rm"]
    if entrypoint is not None:
        args += ["--entrypoint", entrypoint]
    args += [tag, *cmd]
    return subprocess.run(
        args,
        capture_output=True,
        text=True,
        timeout=60,
    )


@pytest.mark.integration
class TestUnifiedDockerfile:
    def test_build_with_cli_variant(self) -> None:
        _docker_build(variant="cli", tag="codeoracle:test-cli")

    def test_container_imports_oracle(self) -> None:
        # _docker_build was a side effect of the prior test; re-run to be safe
        _docker_build(variant="cli", tag="codeoracle:test-cli")
        result = _docker_run(
            "codeoracle:test-cli",
            ["-c", "import codeoracle; print('ok')"],
            entrypoint="python",
        )
        assert result.returncode == 0, result.stderr
        assert "ok" in result.stdout

    def test_container_runs_as_non_root(self) -> None:
        _docker_build(variant="cli", tag="codeoracle:test-cli")
        result = _docker_run("codeoracle:test-cli", ["-u"], entrypoint="id")
        assert result.returncode == 0
        assert result.stdout.strip() == "1000"
```

- [ ] **Step 2: Run test to verify it fails**

```
uv run pytest tests/integration/infra/test_dockerfile_builds.py::TestUnifiedDockerfile -v -m integration
```

Expected: 3 failed (Dockerfile does not exist).

- [ ] **Step 3: Create the entrypoint shim**

```bash
mkdir -p infra/docker
```

Create `infra/docker/entrypoint.sh`:

```bash
#!/bin/sh
# entrypoint.sh — variant dispatcher for unified Dockerfile.
#
# The Dockerfile sets $CODEORACLE_VARIANT from the VARIANT build arg.
# This script execs `python -m codeoracle.$CODEORACLE_VARIANT` so each variant
# runs as the container's PID 1 with proper signal handling.

set -e

if [ -z "${CODEORACLE_VARIANT}" ]; then
    echo "entrypoint.sh: CODEORACLE_VARIANT is not set" >&2
    exit 1
fi

exec python -m "codeoracle.${CODEORACLE_VARIANT}" "$@"
```

Make executable:

```bash
chmod +x infra/docker/entrypoint.sh
```

- [ ] **Step 4: Create the unified Dockerfile**

Create `infra/docker/Dockerfile`:

```dockerfile
# syntax=docker/dockerfile:1.7
#
# Unified Dockerfile for CodeOracle process variants.
#
# Build args:
#   VARIANT — api | worker | cli | mcp | agent (default: cli)
#
# Each variant module (codeoracle.api, codeoracle.worker, etc.) provides a
# __main__.py per the Letta `__main__.py-per-process` pattern. Variant
# modules ship with their owning plan (api → Plan 11, mcp → Plan 12,
# workers → Plans 5/6/7, cli → Plan 1, agent → Plan 10). Plan 1A only
# requires `codeoracle.cli` to exist for the smoke test; Plan 1 already
# creates `src/codeoracle/cli/__main__.py` (the Typer `app` scaffold) before this plan runs.

ARG PYTHON_VERSION=3.14-slim-bookworm

# --- Stage 1: build dependencies via uv ---
FROM python:${PYTHON_VERSION} AS deps

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_NO_CACHE=1

RUN pip install --no-cache-dir uv==0.5.5

WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

# --- Stage 2: runtime base ---
FROM python:${PYTHON_VERSION} AS runtime-base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/app/.venv/bin:$PATH" \
    PYTHONPATH=/app/src

WORKDIR /app

# Install curl for the slice-e2e HTTP healthchecks. The slim base
# (python:3.14-slim-bookworm) omits curl; Plan 13's api/mcp/agent services
# probe via `curl -fsS ...` and would otherwise wedge `unhealthy` at
# compose-up. Added by Plan 13 amendment #49 (2026-05-26). The
# --no-install-recommends + apt cache cleanup keeps the layer minimal
# (~1.5 MB).
RUN apt-get update && \
    apt-get install -y --no-install-recommends curl && \
    rm -rf /var/lib/apt/lists/*

# Copy uv-managed venv from deps stage
COPY --from=deps /app/.venv /app/.venv

# Copy source + install the project (no deps — already in venv)
COPY src /app/src
COPY pyproject.toml /app/pyproject.toml
RUN pip install --no-cache-dir -e . --no-deps

# Copy the entrypoint shim
COPY infra/docker/entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

# Drop privileges (CIS Docker Benchmark 4.1)
RUN useradd --create-home --uid 1000 codeoracle && \
    chown -R codeoracle:codeoracle /app
USER codeoracle

# --- Stage 3: final per-variant image ---
FROM runtime-base AS final

ARG VARIANT=cli
ENV CODEORACLE_VARIANT=${VARIANT}

ENTRYPOINT ["/entrypoint.sh"]
```

- [ ] **Step 5: Run the integration test**

```
uv run pytest tests/integration/infra/test_dockerfile_builds.py::TestUnifiedDockerfile -v -m integration
```

Expected: 3 passed.

- [ ] **Step 6: Commit**

```bash
git add infra/docker/Dockerfile \
        infra/docker/entrypoint.sh \
        tests/integration/infra/test_dockerfile_builds.py

git commit -m "feat(infra): add unified multi-stage Dockerfile + entrypoint shim

Part 1 of G6. One Dockerfile with VARIANT build arg dispatches via
entrypoint.sh to codeoracle.\$CODEORACLE_VARIANT. Non-root user (CIS Docker
Benchmark 4.1). uv-managed lockfile-pinned dependencies in stage 1;
source copy + editable install in stage 2; per-variant final stage.
Smoke test: VARIANT=cli builds + runs + imports codeoracle cleanly.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

### Task 9: `infra/docker/Dockerfile.model-server` — CUDA base for vLLM

**Why:** Part 2 of G6 — separate Dockerfile because the CUDA base + vLLM dependency footprint is incompatible with the slim runtime base. Used by the (post-slice) production self-hosted-LLM deployment. Slice ships the file; runtime is deferred until vLLM is actually wired up.

**Files:**
- Create: `infra/docker/Dockerfile.model-server`
- Modify: `tests/integration/infra/test_dockerfile_builds.py` — add build-only test

**Mode:** Behavioral verification — `docker build` succeeds. Runtime test deferred (CUDA not available on CI runners; slice uses OpenAI dev default, not vLLM).

- [ ] **Step 1: Extend test with build-only check**

Append to `tests/integration/infra/test_dockerfile_builds.py`:

```python
@pytest.mark.integration
@pytest.mark.slow
class TestModelServerDockerfile:
    def test_build_succeeds(self) -> None:
        result = subprocess.run(
            [
                "docker", "build",
                "-f", str(REPO_ROOT / "infra" / "docker" / "Dockerfile.model-server"),
                "-t", "codeoracle-model-server:test",
                str(REPO_ROOT),
            ],
            capture_output=True,
            text=True,
            timeout=1800,  # 30 min — CUDA base is large
        )
        if result.returncode != 0:
            pytest.fail(f"model-server build failed:\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}")
```

EDIT the existing `markers = [...]` list under `[tool.pytest.ini_options]` in `pyproject.toml` to append the `slow` marker. Plan 1 already registered `integration`, `characterization`, and `live_llm` — do not redeclare them or you will overwrite Plan 1's entries.

Before (Plan 1's existing list):

```toml
markers = [
    "integration: requires external services (Postgres, Valkey, etc.); skipped by default in CI's unit-only matrix",
    "characterization: pins existing behavior; failure means behavior drifted, may be intentional",
    "live_llm: requires real LLM-provider credentials; skipped by default, gated by 'run-live-llm' PR label",
]
```

After (one new entry appended):

```toml
markers = [
    "integration: requires external services (Postgres, Valkey, etc.); skipped by default in CI's unit-only matrix",
    "characterization: pins existing behavior; failure means behavior drifted, may be intentional",
    "live_llm: requires real LLM-provider credentials; skipped by default, gated by 'run-live-llm' PR label",
    "slow: takes >5 minutes; skipped by default in CI fast lane",
]
```

- [ ] **Step 2: Run test to verify it fails**

```
uv run pytest tests/integration/infra/test_dockerfile_builds.py::TestModelServerDockerfile -v -m "integration and slow"
```

Expected: 1 failed (file does not exist).

- [ ] **Step 3: Create `Dockerfile.model-server`**

Create `infra/docker/Dockerfile.model-server`:

```dockerfile
# syntax=docker/dockerfile:1.7
#
# Dockerfile.model-server — vLLM CUDA runtime for self-hosted LLM serving.
#
# Used post-slice (production self-hosted target — spec §9 open question
# vLLM-vs-Ollama). Slice ships the file so the image-build CI workflow
# can scan it via Trivy as a slice-adjacent acceptance gate.

ARG CUDA_VERSION=12.4.1
ARG CUDNN_VERSION=8
ARG UBUNTU_VERSION=22.04

FROM nvidia/cuda:${CUDA_VERSION}-cudnn${CUDNN_VERSION}-runtime-ubuntu${UBUNTU_VERSION}

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    DEBIAN_FRONTEND=noninteractive

# Python 3.14 from deadsnakes
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        software-properties-common \
        ca-certificates \
        curl && \
    add-apt-repository -y ppa:deadsnakes/ppa && \
    apt-get update && \
    apt-get install -y --no-install-recommends \
        python3.14 \
        python3.14-venv \
        python3.14-dev && \
    rm -rf /var/lib/apt/lists/* && \
    ln -sf /usr/bin/python3.14 /usr/local/bin/python

# vLLM via pip (pre-built CUDA wheels)
RUN curl -fsSL https://astral.sh/uv/install.sh | sh && \
    /root/.local/bin/uv pip install --system --no-cache-dir \
        "vllm == 0.6.3"

WORKDIR /app

# Non-root user
RUN useradd --create-home --uid 1000 vllm && \
    chown -R vllm:vllm /app
USER vllm

EXPOSE 8000

# Default command — overridden by orchestration platform
CMD ["python", "-m", "vllm.entrypoints.openai.api_server", \
     "--host", "0.0.0.0", \
     "--port", "8000"]
```

- [ ] **Step 4: Run the build test**

```
uv run pytest tests/integration/infra/test_dockerfile_builds.py::TestModelServerDockerfile -v -m "integration and slow"
```

Expected: 1 passed. (Note: this is a 5-30 minute build depending on network speed.)

- [ ] **Step 5: Commit**

```bash
git add infra/docker/Dockerfile.model-server \
        tests/integration/infra/test_dockerfile_builds.py \
        pyproject.toml
git commit -m "feat(infra): add CUDA model-server Dockerfile for vLLM

Part 2 of G6. CUDA 12.4 + Python 3.14 + vLLM 0.6.3 — production
self-hosted LLM runtime. Slice uses OpenAI as dev default (memory
project-dev-llm-provider-openai); this image scans through Trivy as
a slice-adjacent gate so post-slice deployment is not blocked on CVE
review. Build-only test (no runtime test — CUDA absent on CI runners).

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

### Task 10: `infra/scripts/bootstrap_dev.sh` — fresh-clone-to-running setup

**Why:** Part 1 of G8 — one command takes a fresh clone to a working dev env. Idempotent: re-running is a no-op if everything is already set up.

**Files:**
- Create: `infra/scripts/bootstrap_dev.sh`
- Create: `tests/integration/infra/test_bootstrap_dev.py`

**Mode:** Behavioral verification — running the script in a clean temp dir produces a working `.venv` + green test suite.

- [ ] **Step 1: Write the integration test**

Create `tests/integration/infra/test_bootstrap_dev.py`:

```python
"""Integration test: bootstrap_dev.sh produces a working environment."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
BOOTSTRAP = REPO_ROOT / "infra" / "scripts" / "bootstrap_dev.sh"


@pytest.mark.integration
class TestBootstrapDev:
    def test_script_exists_and_is_executable(self) -> None:
        assert BOOTSTRAP.exists(), f"{BOOTSTRAP} not found"
        assert os.access(BOOTSTRAP, os.X_OK), f"{BOOTSTRAP} is not executable"

    def test_idempotent_on_already_bootstrapped_repo(self) -> None:
        # The current repo IS bootstrapped (CI runs after the bootstrap step).
        # Running again must succeed and not destroy state.
        result = subprocess.run(
            ["bash", str(BOOTSTRAP)],
            cwd=str(REPO_ROOT),
            capture_output=True,
            text=True,
            timeout=300,
        )
        assert result.returncode == 0, f"stderr:\n{result.stderr}"

    def test_produces_runnable_python(self) -> None:
        # After bootstrap, `uv run python --version` reports 3.14.x
        result = subprocess.run(
            ["uv", "run", "python", "--version"],
            cwd=str(REPO_ROOT),
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert result.returncode == 0
        # uv routes "python --version" to stdout
        version_str = (result.stdout + result.stderr).strip()
        assert "Python 3.14" in version_str
```

- [ ] **Step 2: Run test to verify it fails**

```
uv run pytest tests/integration/infra/test_bootstrap_dev.py -v -m integration
```

Expected: 3 failed (script does not exist).

- [ ] **Step 3: Create `bootstrap_dev.sh`**

```bash
mkdir -p infra/scripts
```

Create `infra/scripts/bootstrap_dev.sh`:

```bash
#!/usr/bin/env bash
# bootstrap_dev.sh — fresh-clone to working dev env.
#
# Idempotent: safe to re-run. Each step checks whether the work was
# already done and skips if so.
#
# Usage:
#   bash infra/scripts/bootstrap_dev.sh
#
# What it does:
#   1. Verify Docker daemon is reachable (required for compose stack)
#   2. Install uv if missing
#   3. uv sync (creates .venv + installs deps from uv.lock)
#   4. Install pre-commit hooks
#   5. Print next-step guidance

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "${REPO_ROOT}"

log() {
    echo "[bootstrap] $*"
}

# --- Step 1: Docker daemon reachable ---
if ! docker info >/dev/null 2>&1; then
    log "WARNING: Docker daemon not reachable. Compose stack will be unavailable."
    log "         Install Docker Desktop (macOS/Windows) or docker.io (Linux)."
fi

# --- Step 2: Install uv if missing ---
if ! command -v uv >/dev/null 2>&1; then
    log "Installing uv..."
    curl -fsSL https://astral.sh/uv/install.sh | sh
    # Source the new PATH; subsequent commands need uv on PATH
    if [ -f "$HOME/.local/bin/env" ]; then
        # shellcheck disable=SC1091
        . "$HOME/.local/bin/env"
    fi
    export PATH="$HOME/.local/bin:$PATH"
fi
log "uv version: $(uv --version)"

# --- Step 3: Sync dependencies ---
log "Syncing dependencies (uv sync)..."
uv sync --frozen

# --- Step 4: Install pre-commit hooks ---
if [ -f ".pre-commit-config.yaml" ]; then
    log "Installing pre-commit hooks..."
    uv run pre-commit install
fi

# --- Step 5: Next-step guidance ---
cat <<EOF
[bootstrap] Done.

Next steps:
  make test           # run the full test suite
  make compose-up     # bring up the observability + DB stack
  make help           # see all available targets

EOF
```

Make executable:

```bash
chmod +x infra/scripts/bootstrap_dev.sh
```

- [ ] **Step 4: Run the test**

```
uv run pytest tests/integration/infra/test_bootstrap_dev.py -v -m integration
```

Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add infra/scripts/bootstrap_dev.sh \
        tests/integration/infra/test_bootstrap_dev.py
git commit -m "feat(infra): add bootstrap_dev.sh — fresh-clone-to-running setup

Part 1 of G8. Idempotent script: verifies Docker, installs uv, syncs
dependencies via uv.lock, installs pre-commit hooks. CI's first step
runs this so every PR runs against a freshly-bootstrapped env.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

### Task 11: `infra/scripts/seed_test_corpus.sh` — initial corpus for slice tests

**Why:** Part 2 of G8 — the slice's corpus is `src/codeoracle/` itself (dogfood property). This script populates `tests/fixtures/corpus/` with a frozen snapshot of `src/codeoracle/` so slice integration tests have a deterministic input.

**Files:**
- Create: `infra/scripts/seed_test_corpus.sh`
- Create: `tests/integration/infra/test_seed_test_corpus.py`

**Mode:** Behavioral verification — script produces an expected directory layout in `tests/fixtures/corpus/`.

- [ ] **Step 1: Write the test**

Create `tests/integration/infra/test_seed_test_corpus.py`:

```python
"""Integration test: seed_test_corpus.sh populates tests/fixtures/corpus/."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
SEED = REPO_ROOT / "infra" / "scripts" / "seed_test_corpus.sh"
CORPUS_DIR = REPO_ROOT / "tests" / "fixtures" / "corpus"


@pytest.mark.integration
class TestSeedTestCorpus:
    def setup_method(self) -> None:
        if CORPUS_DIR.exists():
            shutil.rmtree(CORPUS_DIR)

    def test_script_exists_and_is_executable(self) -> None:
        assert SEED.exists()
        assert os.access(SEED, os.X_OK)

    def test_creates_corpus_directory(self) -> None:
        subprocess.run(["bash", str(SEED)], check=True, cwd=str(REPO_ROOT))
        assert CORPUS_DIR.exists()
        assert CORPUS_DIR.is_dir()

    def test_copies_oracle_source(self) -> None:
        subprocess.run(["bash", str(SEED)], check=True, cwd=str(REPO_ROOT))
        # Should include core/, utils/, etc.
        assert (CORPUS_DIR / "codeoracle" / "core").is_dir()
        assert (CORPUS_DIR / "codeoracle" / "utils" / "async_helpers.py").is_file()

    def test_excludes_pycache(self) -> None:
        subprocess.run(["bash", str(SEED)], check=True, cwd=str(REPO_ROOT))
        for pycache in CORPUS_DIR.rglob("__pycache__"):
            pytest.fail(f"corpus contains __pycache__: {pycache}")

    def test_idempotent(self) -> None:
        subprocess.run(["bash", str(SEED)], check=True, cwd=str(REPO_ROOT))
        first_files = sorted(p.relative_to(CORPUS_DIR) for p in CORPUS_DIR.rglob("*") if p.is_file())
        subprocess.run(["bash", str(SEED)], check=True, cwd=str(REPO_ROOT))
        second_files = sorted(p.relative_to(CORPUS_DIR) for p in CORPUS_DIR.rglob("*") if p.is_file())
        assert first_files == second_files
```

- [ ] **Step 2: Run test to verify it fails**

```
uv run pytest tests/integration/infra/test_seed_test_corpus.py -v -m integration
```

Expected: 5 failed (script does not exist).

- [ ] **Step 3: Create `seed_test_corpus.sh`**

Create `infra/scripts/seed_test_corpus.sh`:

```bash
#!/usr/bin/env bash
# seed_test_corpus.sh — populate tests/fixtures/corpus/ from src/codeoracle/.
#
# The slice's corpus is the platform's own source code (dogfood
# property). This script freezes a copy under tests/fixtures/corpus/
# so slice integration tests have a deterministic input that is
# independent of in-flight edits to src/codeoracle/.
#
# Idempotent: removes the corpus dir first, then re-populates.
#
# Usage:
#   bash infra/scripts/seed_test_corpus.sh

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
SRC="${REPO_ROOT}/src"
CORPUS="${REPO_ROOT}/tests/fixtures/corpus"

log() {
    echo "[seed_test_corpus] $*"
}

log "Cleaning corpus dir: ${CORPUS}"
rm -rf "${CORPUS}"
mkdir -p "${CORPUS}"

log "Copying ${SRC}/codeoracle → ${CORPUS}/codeoracle"
# rsync excludes __pycache__ and *.pyc — keep corpus deterministic
rsync -a --quiet \
    --exclude="__pycache__" \
    --exclude="*.pyc" \
    --exclude="*.pyo" \
    --exclude=".mypy_cache" \
    --exclude=".ruff_cache" \
    "${SRC}/codeoracle/" "${CORPUS}/codeoracle/"

log "Done. Corpus contents:"
find "${CORPUS}" -maxdepth 2 -type d | sort
```

Make executable:

```bash
chmod +x infra/scripts/seed_test_corpus.sh
```

- [ ] **Step 4: Run tests**

```
uv run pytest tests/integration/infra/test_seed_test_corpus.py -v -m integration
```

Expected: 5 passed.

- [ ] **Step 5: Commit**

```bash
git add infra/scripts/seed_test_corpus.sh \
        tests/integration/infra/test_seed_test_corpus.py
git commit -m "feat(infra): add seed_test_corpus.sh — freeze src/codeoracle/ to tests/fixtures/corpus/

Part 2 of G8. Slice's corpus is the platform's own source (dogfood
property). Freezing it under tests/fixtures/corpus/ makes slice
integration tests deterministic — they index the frozen copy, not
the in-flight src/codeoracle/ tree. rsync excludes __pycache__ and lint
caches so the corpus is reproducible across runs.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

### Task 12: `infra/security/semgrep/codeoracle-custom.yml` — custom SAST rules

**Why:** Closes G9. Semgrep custom rules enforce architectural invariants that ruff/ty cannot catch — most importantly, the LLM-direct-import ban (memory `project-llm-provider-agnostic`).

**Files:**
- Create: `infra/security/semgrep/codeoracle-custom.yml`
- Create: `tests/unit/infra/test_semgrep_rules.py`

**Mode:** Behavioral verification — running semgrep against a clean tree exits 0; running against a planted violation exits 1.

- [ ] **Step 1: Write the test**

Create `tests/unit/infra/test_semgrep_rules.py`:

```python
"""Tests for infra/security/semgrep/codeoracle-custom.yml — custom SAST rules."""

from __future__ import annotations

import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
RULES_FILE = REPO_ROOT / "infra" / "security" / "semgrep" / "codeoracle-custom.yml"


def _has_semgrep() -> bool:
    return shutil.which("semgrep") is not None


def _run_semgrep(target_dir: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            "semgrep",
            "--config", str(RULES_FILE),
            "--quiet",
            "--error",
            str(target_dir),
        ],
        capture_output=True,
        text=True,
    )


@pytest.mark.skipif(not _has_semgrep(), reason="semgrep not installed")
class TestSemgrepRules:
    def test_rules_file_exists(self) -> None:
        assert RULES_FILE.exists()

    def test_clean_code_passes(self, tmp_path: Path) -> None:
        # A file that imports nothing forbidden
        (tmp_path / "clean.py").write_text(
            "from codeoracle.adapters.llm import LLMProvider\n"
            "def f(p: LLMProvider) -> None:\n"
            "    pass\n"
        )
        result = _run_semgrep(tmp_path)
        assert result.returncode == 0, result.stdout

    def test_direct_anthropic_import_outside_providers_fails(self, tmp_path: Path) -> None:
        # Simulate a service trying to import anthropic directly — banned by
        # memory project-llm-provider-agnostic.
        svc_dir = tmp_path / "services"
        svc_dir.mkdir()
        (svc_dir / "bad.py").write_text("import anthropic\n")
        result = _run_semgrep(tmp_path)
        assert result.returncode != 0
        assert "codeoracle-llm-direct-import" in result.stdout

    def test_subprocess_shell_true_flagged(self, tmp_path: Path) -> None:
        (tmp_path / "shell_bad.py").write_text(
            "import subprocess\n"
            "subprocess.run('ls', shell=True)\n"
        )
        result = _run_semgrep(tmp_path)
        assert result.returncode != 0
        assert "codeoracle-shell-true" in result.stdout
```

- [ ] **Step 2: Run test to verify it fails**

```
uv run pytest tests/unit/infra/test_semgrep_rules.py -v
```

Expected: SKIPPED if semgrep not installed, else 4 failed (rules file does not exist).

- [ ] **Step 3: Install semgrep if absent (dev tool)**

Add to `[dependency-groups]` in `pyproject.toml`:

```toml
[dependency-groups]
dev = [
    # ... existing dev deps ...
    "semgrep ~= 1.92",
]
```

Run `uv lock` + `uv sync`.

- [ ] **Step 4: Create the rules file**

```bash
mkdir -p infra/security/semgrep
```

Create `infra/security/semgrep/codeoracle-custom.yml`:

```yaml
# Custom Semgrep rules for CodeOracle.
#
# These complement ruff (which handles syntactic violations) by catching
# architectural violations and security anti-patterns. CI runs these in
# .github/workflows/security.yml. Pre-commit runs them locally.

rules:
  - id: codeoracle-llm-direct-import
    message: >
      Direct LLM-provider imports are forbidden outside adapters/llm/providers/.
      Use the LLMProvider interface from codeoracle.adapters.llm instead.
      See memory project-llm-provider-agnostic.
    severity: ERROR
    languages: [python]
    pattern-either:
      - pattern: import anthropic
      - pattern: import openai
      - pattern: from anthropic import ...
      - pattern: from openai import ...
      - pattern: import litellm
      - pattern: from litellm import ...
      - pattern: import ollama
      - pattern: from ollama import ...
    paths:
      exclude:
        - "adapters/llm/providers/**"
        - "tests/**"

  - id: codeoracle-shell-true
    message: >
      subprocess.run/Popen with shell=True invites shell injection.
      Use a list of args instead.
    severity: ERROR
    languages: [python]
    pattern-either:
      - pattern: subprocess.run(..., shell=True, ...)
      - pattern: subprocess.Popen(..., shell=True, ...)
      - pattern: subprocess.call(..., shell=True, ...)
      - pattern: subprocess.check_output(..., shell=True, ...)

  - id: codeoracle-sql-string-concat
    message: >
      String concatenation for SQL is a SQL-injection vector. Use parameterized
      queries (psycopg, SQLAlchemy bound parameters, etc.).
    severity: ERROR
    languages: [python]
    pattern-either:
      - pattern: |
          $CONN.execute("..." + $X)
      - pattern: |
          $CONN.execute("..." % $X)
      - pattern: |
          $CONN.execute(f"...{$X}...")
    paths:
      exclude:
        - "tests/**"

  - id: codeoracle-pickle-load
    message: >
      pickle.load() on untrusted input is a code-execution vector. Use json
      or a typed schema (Pydantic) for evidence persistence.
    severity: ERROR
    languages: [python]
    pattern-either:
      - pattern: pickle.load(...)
      - pattern: pickle.loads(...)

  - id: codeoracle-cite-bytes-required
    message: >
      Citation objects MUST carry the bytes from the source. Constructing a
      Citation without 'bytes_excerpt=' is a verifier-bypass anti-pattern.
    severity: WARNING
    languages: [python]
    pattern: Citation(...)
    pattern-not: Citation(..., bytes_excerpt=$X, ...)
    paths:
      exclude:
        - "tests/**"
        - "models/**"
```

- [ ] **Step 5: Run tests**

```
uv run pytest tests/unit/infra/test_semgrep_rules.py -v
```

Expected: 4 passed.

- [ ] **Step 6: Commit**

```bash
git add infra/security/semgrep/codeoracle-custom.yml \
        tests/unit/infra/test_semgrep_rules.py \
        pyproject.toml \
        uv.lock
git commit -m "feat(infra): add custom semgrep rules — LLM ban + injection guards

Closes G9. Five rules: (1) direct LLM-provider imports banned outside
adapters/llm/providers/ (memory project-llm-provider-agnostic),
(2) subprocess shell=True, (3) SQL string concat, (4) pickle.load,
(5) Citation without bytes_excerpt. CI security workflow runs these
on every PR; pre-commit runs them locally.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

### Task 13: `.github/workflows/security.yml` + ci.yml refactor

**Why:** Closes G3. Spec §7.4 names `security.yml` as a discrete workflow. Plan 1 folded gitleaks + semgrep into ci.yml; this task creates the dedicated file and removes the duplicated steps from ci.yml.

**Files:**
- Create: `.github/workflows/security.yml`
- Modify: `.github/workflows/ci.yml` — remove gitleaks + semgrep steps (now in security.yml)
- Create: `tests/integration/infra/test_workflow_security.py` (uses `act` if installed, else linting only)

**Mode:** Behavioral verification — `act` runs the workflow locally; YAML lints clean.

- [ ] **Step 1: Write the workflow lint test**

Create `tests/integration/infra/test_workflow_security.py`:

```python
"""Tests for .github/workflows/security.yml."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[3]
SECURITY_WF = REPO_ROOT / ".github" / "workflows" / "security.yml"
CI_WF = REPO_ROOT / ".github" / "workflows" / "ci.yml"


def test_security_workflow_exists() -> None:
    assert SECURITY_WF.exists()


def test_security_workflow_is_valid_yaml() -> None:
    with SECURITY_WF.open() as f:
        data = yaml.safe_load(f)
    # GitHub Actions parses `on:` as True due to YAML 1.1 truthiness — check both keys.
    assert "jobs" in data
    assert "on" in data or True in data


def test_security_workflow_runs_gitleaks() -> None:
    with SECURITY_WF.open() as f:
        data = yaml.safe_load(f)
    text = SECURITY_WF.read_text()
    assert "gitleaks" in text.lower()


def test_security_workflow_runs_semgrep() -> None:
    text = SECURITY_WF.read_text()
    assert "semgrep" in text.lower()


def test_security_workflow_uploads_sarif() -> None:
    text = SECURITY_WF.read_text()
    assert "sarif" in text.lower()


def test_ci_workflow_no_longer_runs_gitleaks() -> None:
    # gitleaks moved out of ci.yml — security.yml owns it now.
    text = CI_WF.read_text()
    assert "gitleaks" not in text.lower(), (
        "ci.yml still references gitleaks — should have been removed when "
        "security.yml took ownership"
    )


def test_ci_workflow_no_longer_runs_semgrep() -> None:
    text = CI_WF.read_text()
    assert "semgrep" not in text.lower(), (
        "ci.yml still references semgrep — should have been removed when "
        "security.yml took ownership"
    )
```

- [ ] **Step 2: Run test to verify it fails**

```
uv run pytest tests/integration/infra/test_workflow_security.py -v
```

Expected: most tests fail (file doesn't exist; ci.yml may still reference gitleaks).

- [ ] **Step 3: Create `security.yml`**

```bash
mkdir -p .github/workflows
```

Create `.github/workflows/security.yml`:

```yaml
name: security

on:
  pull_request:
  push:
    branches: [main]
  schedule:
    # Daily at 03:00 UTC — catches new CVEs in pinned deps
    - cron: "0 3 * * *"

permissions:
  contents: read
  security-events: write  # for uploading SARIF to code-scanning

jobs:
  gitleaks:
    name: gitleaks (secret scan, full history)
    runs-on: ubuntu-latest
    steps:
      - name: Checkout (full history)
        uses: actions/checkout@v4
        with:
          fetch-depth: 0

      - name: Run gitleaks
        uses: gitleaks/gitleaks-action@v2
        env:
          GITHUB_TOKEN: ${{ secrets.GITHUB_TOKEN }}

  semgrep:
    name: semgrep (custom SAST + OWASP top 10)
    runs-on: ubuntu-latest
    container:
      image: semgrep/semgrep:1.92
    steps:
      - uses: actions/checkout@v4

      - name: Run custom rules
        run: |
          semgrep ci \
            --config=infra/security/semgrep/codeoracle-custom.yml \
            --sarif --output=semgrep-custom.sarif

      - name: Run OWASP rules
        run: |
          semgrep ci \
            --config=p/owasp-top-ten \
            --config=p/python \
            --config=p/secrets \
            --sarif --output=semgrep-owasp.sarif

      - name: Upload custom SARIF
        if: always()
        uses: github/codeql-action/upload-sarif@v3
        with:
          sarif_file: semgrep-custom.sarif
          category: semgrep-custom

      - name: Upload OWASP SARIF
        if: always()
        uses: github/codeql-action/upload-sarif@v3
        with:
          sarif_file: semgrep-owasp.sarif
          category: semgrep-owasp

  dependency-audit:
    name: dependency CVE audit (osv-scanner)
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4

      - name: Install uv
        uses: astral-sh/setup-uv@v3
        with:
          version: "0.5.5"

      - name: Sync deps
        run: uv sync --frozen

      - name: Install osv-scanner
        uses: actions/setup-go@v5
        with:
          go-version: "1.22"
      - name: Install osv-scanner binary
        run: |
          go install github.com/google/osv-scanner/v2/cmd/osv-scanner@v2
          echo "$(go env GOPATH)/bin" >> "$GITHUB_PATH"

      - name: osv-scanner
        run: osv-scanner --lockfile=uv.lock

  sbom:
    name: SBOM (cyclonedx-py)
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4

      - name: Install uv
        uses: astral-sh/setup-uv@v3
        with:
          version: "0.5.5"

      - name: Sync deps
        run: uv sync --frozen

      - name: Generate SBOM
        run: |
          uv run cyclonedx-py environment --output-format JSON \
            --output-file sbom.json
          uv run cyclonedx-py environment --output-format XML \
            --output-file sbom.xml

      - name: Upload SBOM
        uses: actions/upload-artifact@v4
        with:
          name: sbom
          path: |
            sbom.json
            sbom.xml
          retention-days: 90
```

- [ ] **Step 4: Add `cyclonedx-py` to dev deps**

In `pyproject.toml` (osv-scanner is a Go binary installed in-job, not a Python dev-dep — see Step 3's `dependency-audit` job):

```toml
[dependency-groups]
dev = [
    # ... existing dev deps ...
    "cyclonedx-bom ~= 5.1",  # provides cyclonedx-py CLI
]
```

Run `uv lock`.

- [ ] **Step 5: Remove gitleaks + semgrep from ci.yml**

Read `.github/workflows/ci.yml` (from Plan 1). Identify any `gitleaks` step + any `semgrep` step. Remove the entire step blocks (preserve other steps). If those steps were their own jobs, remove the entire job stanzas.

Example removal pattern (delete from ci.yml, do not paste back):

```yaml
      - name: gitleaks
        uses: gitleaks/gitleaks-action@v2
        ...

      - name: semgrep
        run: semgrep ci --config=...
```

Replace the now-empty CI sections if needed with a comment:

```yaml
      # Security scanning lives in .github/workflows/security.yml (gitleaks,
      # semgrep custom + OWASP, osv-scanner, cyclonedx SBOM).
```

- [ ] **Step 6: Run the workflow-lint tests**

```
uv run pytest tests/integration/infra/test_workflow_security.py -v
```

Expected: 7 passed.

- [ ] **Step 7: Commit**

```bash
git add .github/workflows/security.yml \
        .github/workflows/ci.yml \
        tests/integration/infra/test_workflow_security.py \
        pyproject.toml \
        uv.lock
git commit -m "feat(ci): split security checks into dedicated security.yml workflow

Closes G3. Spec §7.4 names security.yml as a discrete workflow; Plan 1
folded gitleaks + semgrep into ci.yml. This task creates the dedicated
file (gitleaks full-history + semgrep custom + OWASP + osv-scanner +
cyclonedx SBOM, all uploading SARIF to code-scanning) and removes the
duplicated steps from ci.yml.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

### Task 14: `.github/workflows/image-build.yml` — Docker build + Trivy

**Why:** Closes G2. Spec §8.5 gate "image-Trivy scan" depends on this workflow. Matrix builds all 5 image variants + the model-server, scans each with Trivy, fails on HIGH/CRITICAL CVEs.

**Files:**
- Create: `.github/workflows/image-build.yml`
- Create: `tests/integration/infra/test_workflow_image_build.py`

**Mode:** Behavioral verification — YAML lints clean; workflow structure asserts pass.

- [ ] **Step 1: Write the workflow test**

Create `tests/integration/infra/test_workflow_image_build.py`:

```python
"""Tests for .github/workflows/image-build.yml."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[3]
IMAGE_BUILD_WF = REPO_ROOT / ".github" / "workflows" / "image-build.yml"


def test_workflow_exists() -> None:
    assert IMAGE_BUILD_WF.exists()


def test_workflow_is_valid_yaml() -> None:
    with IMAGE_BUILD_WF.open() as f:
        yaml.safe_load(f)


def test_workflow_uses_matrix_over_variants() -> None:
    with IMAGE_BUILD_WF.open() as f:
        data = yaml.safe_load(f)
    jobs = data["jobs"]
    # Find the build job (likely named build or image-build)
    build_job = None
    for job in jobs.values():
        if "strategy" in job and "matrix" in job["strategy"]:
            build_job = job
            break
    assert build_job is not None, "No matrix-strategy job found"

    matrix = build_job["strategy"]["matrix"]
    variants = matrix.get("variant") or matrix.get("VARIANT") or []
    expected = {"api", "worker", "cli", "mcp", "agent"}
    assert expected.issubset(set(variants)), f"Missing variants: {expected - set(variants)}"


def test_workflow_runs_trivy() -> None:
    text = IMAGE_BUILD_WF.read_text()
    assert "trivy" in text.lower()


def test_workflow_fails_on_high_severity() -> None:
    text = IMAGE_BUILD_WF.read_text().lower()
    assert "high" in text or "critical" in text
    # Reasonable check: severity threshold appears in the same workflow
    assert "severity" in text or "exit-code" in text
```

- [ ] **Step 2: Run test to verify it fails**

Expected: 5 failed.

- [ ] **Step 3: Create `image-build.yml`**

Create `.github/workflows/image-build.yml`:

```yaml
name: image-build

on:
  pull_request:
    paths:
      - "src/**"
      - "infra/docker/**"
      - "pyproject.toml"
      - "uv.lock"
      - ".github/workflows/image-build.yml"
  push:
    branches: [main]
  workflow_dispatch:

permissions:
  contents: read
  packages: write   # for pushing to GHCR on main
  security-events: write  # for Trivy SARIF upload

jobs:
  build-and-scan:
    name: build + Trivy
    runs-on: ubuntu-latest
    strategy:
      fail-fast: false
      matrix:
        variant: [api, worker, cli, mcp, agent]

    steps:
      - uses: actions/checkout@v4

      - name: Set up Docker Buildx
        uses: docker/setup-buildx-action@v3

      - name: Build image
        uses: docker/build-push-action@v6
        with:
          context: .
          file: infra/docker/Dockerfile
          build-args: |
            VARIANT=${{ matrix.variant }}
          tags: codeoracle:${{ matrix.variant }}-pr-${{ github.event.pull_request.number || github.sha }}
          load: true
          cache-from: type=gha,scope=codeoracle-${{ matrix.variant }}
          cache-to: type=gha,scope=codeoracle-${{ matrix.variant }},mode=max

      - name: Trivy scan
        uses: aquasecurity/trivy-action@0.28.0
        with:
          image-ref: codeoracle:${{ matrix.variant }}-pr-${{ github.event.pull_request.number || github.sha }}
          format: sarif
          output: trivy-${{ matrix.variant }}.sarif
          severity: HIGH,CRITICAL
          exit-code: "1"
          ignore-unfixed: true

      - name: Upload Trivy SARIF
        if: always()
        uses: github/codeql-action/upload-sarif@v3
        with:
          sarif_file: trivy-${{ matrix.variant }}.sarif
          category: trivy-${{ matrix.variant }}

  build-model-server:
    name: build model-server (build-only — CUDA absent on runners)
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4

      - name: Set up Docker Buildx
        uses: docker/setup-buildx-action@v3

      - name: Build model-server image
        uses: docker/build-push-action@v6
        with:
          context: .
          file: infra/docker/Dockerfile.model-server
          tags: codeoracle-model-server:pr-${{ github.event.pull_request.number || github.sha }}
          load: true
          cache-from: type=gha,scope=codeoracle-model-server
          cache-to: type=gha,scope=codeoracle-model-server,mode=max

      - name: Trivy scan model-server
        uses: aquasecurity/trivy-action@0.28.0
        with:
          image-ref: codeoracle-model-server:pr-${{ github.event.pull_request.number || github.sha }}
          format: sarif
          output: trivy-model-server.sarif
          severity: HIGH,CRITICAL
          exit-code: "1"
          ignore-unfixed: true

      - name: Upload Trivy SARIF
        if: always()
        uses: github/codeql-action/upload-sarif@v3
        with:
          sarif_file: trivy-model-server.sarif
          category: trivy-model-server
```

- [ ] **Step 4: Run tests**

```
uv run pytest tests/integration/infra/test_workflow_image_build.py -v
```

Expected: 5 passed.

- [ ] **Step 5: Commit**

```bash
git add .github/workflows/image-build.yml \
        tests/integration/infra/test_workflow_image_build.py
git commit -m "feat(ci): add image-build workflow — matrix build + Trivy on 5 variants + model-server

Closes G2. Spec §8.5 'image-Trivy scan' gate depends on this workflow.
Matrix strategy builds all 5 process variants (api/worker/cli/mcp/agent)
via the unified Dockerfile, scans each with Trivy (HIGH/CRITICAL fail
threshold), uploads SARIF to code-scanning. Separate job builds the
CUDA model-server image (build-only — CUDA absent on runners).

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

### Task 15: `.github/workflows/lockfile-refresh.yml` — weekly uv.lock PR

**Why:** Closes G4. Weekly cron runs `uv lock --upgrade`, opens a PR if the lockfile diffs, runs full CI on the PR.

**Files:**
- Create: `.github/workflows/lockfile-refresh.yml`
- Create: `tests/integration/infra/test_workflow_lockfile_refresh.py`

**Mode:** Behavioral verification — YAML lints clean; expected steps present.

- [ ] **Step 1: Write the test**

Create `tests/integration/infra/test_workflow_lockfile_refresh.py`:

```python
"""Tests for .github/workflows/lockfile-refresh.yml."""

from __future__ import annotations

from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[3]
WF = REPO_ROOT / ".github" / "workflows" / "lockfile-refresh.yml"


def test_workflow_exists() -> None:
    assert WF.exists()


def test_workflow_is_valid_yaml() -> None:
    with WF.open() as f:
        yaml.safe_load(f)


def test_workflow_runs_on_schedule() -> None:
    with WF.open() as f:
        data = yaml.safe_load(f)
    on_block = data.get("on") or data.get(True)
    assert "schedule" in on_block, f"on: must include schedule, got {on_block}"


def test_workflow_runs_uv_lock_upgrade() -> None:
    text = WF.read_text()
    assert "uv lock --upgrade" in text


def test_workflow_opens_pr() -> None:
    text = WF.read_text().lower()
    assert "create-pull-request" in text or "pull-request" in text
```

- [ ] **Step 2: Run test to verify it fails**

Expected: 5 failed.

- [ ] **Step 3: Create `lockfile-refresh.yml`**

Create `.github/workflows/lockfile-refresh.yml`:

```yaml
name: lockfile-refresh

on:
  schedule:
    # Weekly: Mondays at 06:00 UTC
    - cron: "0 6 * * 1"
  workflow_dispatch:

permissions:
  contents: write
  pull-requests: write

jobs:
  refresh:
    name: uv lock --upgrade
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4

      - name: Install uv
        uses: astral-sh/setup-uv@v3
        with:
          version: "0.5.5"

      - name: uv lock --upgrade
        run: uv lock --upgrade

      - name: Check for changes
        id: diff
        run: |
          if git diff --quiet uv.lock; then
            echo "changed=false" >> "$GITHUB_OUTPUT"
          else
            echo "changed=true" >> "$GITHUB_OUTPUT"
          fi

      - name: Create pull request
        if: steps.diff.outputs.changed == 'true'
        uses: peter-evans/create-pull-request@v7
        with:
          token: ${{ secrets.GITHUB_TOKEN }}
          branch: chore/lockfile-refresh
          delete-branch: true
          title: "chore(deps): weekly uv.lock refresh"
          commit-message: "chore(deps): refresh uv.lock"
          body: |
            Automated weekly lockfile refresh via `uv lock --upgrade`.

            CI runs against this PR via the standard ci.yml + security.yml
            workflows. Merge if CI is green.
          labels: |
            dependencies
            automated
```

- [ ] **Step 4: Run tests**

```
uv run pytest tests/integration/infra/test_workflow_lockfile_refresh.py -v
```

Expected: 5 passed.

- [ ] **Step 5: Commit**

```bash
git add .github/workflows/lockfile-refresh.yml \
        tests/integration/infra/test_workflow_lockfile_refresh.py
git commit -m "feat(ci): add weekly lockfile-refresh workflow

Closes G4. Monday 06:00 UTC cron runs uv lock --upgrade, opens a PR if
the lockfile changed. Full CI runs against the PR; humans merge after
review. Complements Renovate (per-dep PRs) by catching whole-tree
re-resolutions that don't surface as individual deps.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

### Task 16: `.github/CODEOWNERS`

**Why:** Closes G5. Routes review requirements for critical paths (security configs, CI workflows, evidence-binding code).

**Files:**
- Create: `.github/CODEOWNERS`
- Create: `tests/unit/infra/test_codeowners.py`

**Mode:** Behavioral verification — file parses; required path patterns present.

- [ ] **Step 1: Write the test**

Create `tests/unit/infra/test_codeowners.py`:

```python
"""Tests for .github/CODEOWNERS."""

from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
CODEOWNERS = REPO_ROOT / ".github" / "CODEOWNERS"


def _parse_codeowners(path: Path) -> list[tuple[str, list[str]]]:
    entries: list[tuple[str, list[str]]] = []
    for raw in path.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split()
        if len(parts) < 2:
            continue
        pattern, *owners = parts
        entries.append((pattern, owners))
    return entries


def test_codeowners_exists() -> None:
    assert CODEOWNERS.exists()


def test_codeowners_has_default_owner() -> None:
    entries = _parse_codeowners(CODEOWNERS)
    assert any(pattern == "*" for pattern, _ in entries), \
        "CODEOWNERS must define a default-owner rule with '*'"


def test_codeowners_protects_security_paths() -> None:
    entries = _parse_codeowners(CODEOWNERS)
    patterns = [p for p, _ in entries]
    required = [
        "/.github/workflows/",
        "/infra/security/",
        "/src/codeoracle/services/verifier/",
    ]
    for r in required:
        assert any(p.startswith(r) for p in patterns), f"Missing protection for {r}"


def test_codeowners_owners_use_team_or_user_prefix() -> None:
    entries = _parse_codeowners(CODEOWNERS)
    for pattern, owners in entries:
        for owner in owners:
            assert owner.startswith("@"), \
                f"Pattern {pattern!r} owner {owner!r} must start with '@'"
```

- [ ] **Step 2: Run test to verify it fails**

Expected: 4 failed.

- [ ] **Step 3: Create `.github/CODEOWNERS`**

```bash
mkdir -p .github
```

Create `.github/CODEOWNERS`:

```
# CODEOWNERS — review routing for CodeOracle.
#
# Syntax: glob pattern + space-separated @owners.
# Last match wins; specific patterns override general ones.

# --- Default owner: everything has at least one reviewer ---
*  @CodeOracle/platform

# --- Critical paths require a security reviewer ---
/.github/workflows/        @CodeOracle/platform @CodeOracle/security
/infra/security/           @CodeOracle/platform @CodeOracle/security
/infra/docker/             @CodeOracle/platform @CodeOracle/security
/src/codeoracle/services/verifier/   @CodeOracle/platform @CodeOracle/security
/src/codeoracle/services/trust_matrix.py  @CodeOracle/platform @CodeOracle/security
/src/codeoracle/adapters/llm/  @CodeOracle/platform @CodeOracle/security

# --- Design specs + plans: require platform-architect review ---
/docs/superpowers/specs/   @CodeOracle/platform @CodeOracle/architecture
/docs/superpowers/plans/   @CodeOracle/platform @CodeOracle/architecture

# --- Orchestrator state: owned by the active orchestrator session ---
# Audit-side artifacts that mutate every session — relaxed review.
/docs/handoff.md           @CodeOracle/platform
/docs/orchestrator-state.md @CodeOracle/platform
/.swarm/                   @CodeOracle/platform
```

- [ ] **Step 4: Run tests**

```
uv run pytest tests/unit/infra/test_codeowners.py -v
```

Expected: 4 passed.

- [ ] **Step 5: Commit**

```bash
git add .github/CODEOWNERS \
        tests/unit/infra/test_codeowners.py
git commit -m "feat(repo): add CODEOWNERS — security-team review on critical paths

Closes G5. Default owner @CodeOracle/platform reviews everything;
security-critical paths (workflows, infra/security/, infra/docker/,
verifier, trust_matrix, LLM adapters) additionally require
@CodeOracle/security. Specs + plans require
@CodeOracle/architecture. Orchestrator-state files keep relaxed
review (mutate every session).

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

### Task 17: `Makefile` — thin uv/compose/docker wrapper

**Why:** Closes G1. One file at repo root with `make <target>` shortcuts. Wraps `uv`, `docker compose`, and `docker build` — does NOT re-implement logic in the underlying scripts (DRY per memory `project-code-quality-bar`).

**Files:**
- Create: `Makefile`
- Create: `tests/unit/infra/test_makefile.py`

**Mode:** Behavioral verification — `make help` lists targets; `make test` invokes pytest.

- [ ] **Step 1: Write the test**

Create `tests/unit/infra/test_makefile.py`:

```python
"""Tests for the top-level Makefile."""

from __future__ import annotations

import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
MAKEFILE = REPO_ROOT / "Makefile"


def test_makefile_exists() -> None:
    assert MAKEFILE.exists()


def test_make_help_lists_targets() -> None:
    result = subprocess.run(
        ["make", "help"],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 0
    expected_targets = ["bootstrap", "test", "lint", "type-check", "compose-up", "compose-down"]
    for t in expected_targets:
        assert t in result.stdout, f"`make help` does not list {t!r}"


def test_make_lint_runs_ruff() -> None:
    result = subprocess.run(
        ["make", "--dry-run", "lint"],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 0
    assert "ruff" in result.stdout


def test_make_test_runs_pytest() -> None:
    result = subprocess.run(
        ["make", "--dry-run", "test"],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 0
    assert "pytest" in result.stdout
```

- [ ] **Step 2: Run test to verify it fails**

Expected: 4 failed.

- [ ] **Step 3: Create `Makefile`**

Create `Makefile`:

```makefile
# CodeOracle — top-level Makefile.
#
# Thin wrapper around uv, docker compose, docker. Does NOT re-implement
# logic that already lives in infra/scripts/. If you want to know what
# `make bootstrap` does, read infra/scripts/bootstrap_dev.sh.

.PHONY: help bootstrap test test-unit test-integration lint format type-check \
        compose-up compose-down compose-logs docker-build trivy-scan seed-corpus clean

COMPOSE := docker compose -f infra/compose/docker-compose.yml

help:  ## Show this help
	@awk 'BEGIN {FS = ":.*?## "} /^[a-zA-Z_-]+:.*?## / {printf "  %-20s %s\n", $$1, $$2}' $(MAKEFILE_LIST)

bootstrap:  ## Set up dev env (uv, deps, pre-commit) — fresh-clone safe
	@bash infra/scripts/bootstrap_dev.sh

test:  ## Run the full test suite
	@uv run pytest

test-unit:  ## Run only unit tests (fast)
	@uv run pytest tests/unit -v

test-integration:  ## Run only integration tests (requires compose stack up)
	@uv run pytest tests/integration -v -m integration

lint:  ## Run ruff (lint + format check + import-boundary)
	@uv run ruff check .
	@uv run ruff format --check .

format:  ## Apply ruff format + lint --fix
	@uv run ruff format .
	@uv run ruff check --fix .

type-check:  ## Run ty in strict mode
	@uv run ty check src tests

compose-up:  ## Bring up the full observability + DB stack
	@$(COMPOSE) up -d

compose-down:  ## Tear down the compose stack (preserves volumes)
	@$(COMPOSE) down

compose-logs:  ## Tail logs from the compose stack
	@$(COMPOSE) logs -f

docker-build:  ## Build all image variants (VARIANT=api|worker|cli|mcp|agent ALL=1)
ifdef ALL
	@for v in api worker cli mcp agent; do \
		echo "==> Building codeoracle:$$v"; \
		docker build --build-arg VARIANT=$$v -t codeoracle:$$v -f infra/docker/Dockerfile .; \
	done
else
	@docker build --build-arg VARIANT=$(VARIANT) -t codeoracle:$(VARIANT) -f infra/docker/Dockerfile .
endif

trivy-scan:  ## Trivy scan a built image (VARIANT=api|...)
	@trivy image --severity HIGH,CRITICAL --exit-code 1 codeoracle:$(VARIANT)

seed-corpus:  ## Populate tests/fixtures/corpus/ from src/codeoracle/
	@bash infra/scripts/seed_test_corpus.sh

clean:  ## Remove caches and build artifacts (preserves .venv)
	@find . -type d -name __pycache__ -prune -exec rm -rf {} + 2>/dev/null || true
	@find . -type d -name .pytest_cache -prune -exec rm -rf {} + 2>/dev/null || true
	@find . -type d -name .ruff_cache -prune -exec rm -rf {} + 2>/dev/null || true
	@find . -type d -name .mypy_cache -prune -exec rm -rf {} + 2>/dev/null || true
	@rm -rf dist build *.egg-info
```

- [ ] **Step 4: Run tests**

```
uv run pytest tests/unit/infra/test_makefile.py -v
```

Expected: 4 passed.

- [ ] **Step 5: Commit**

```bash
git add Makefile tests/unit/infra/test_makefile.py
git commit -m "feat(repo): add Makefile — thin uv/compose/docker wrapper

Closes G1. Targets: bootstrap (→ bootstrap_dev.sh), test, test-unit,
test-integration, lint, format, type-check, compose-up, compose-down,
compose-logs, docker-build, trivy-scan, seed-corpus (→
seed_test_corpus.sh), clean. Pure routing — no logic. The underlying
scripts are the source of truth (DRY per project-code-quality-bar).

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

### Task 18: End-to-end acceptance — OTel span flow

**Why:** Final acceptance gate. Proves the whole observability chain works: a Python process emits a span via `core/telemetry.py` → otel-collector receives it → Tempo stores it → Tempo's HTTP API returns it. If this passes, every observability-dependent gate in §8.5 (gate 9, "OTel spans") has working infrastructure.

**Files:**
- Create: `tests/integration/infra/test_otel_pipeline.py`

**Mode:** Behavioral verification — full stack up + emit + query.

- [ ] **Step 1: Write the end-to-end test**

Create `tests/integration/infra/test_otel_pipeline.py`:

```python
"""End-to-end test: Python span → otel-collector → Tempo → query.

This is the canonical 'observability works' gate referenced by spec
§8.5 gate 9. It runs against a live compose stack:

    make compose-up
    uv run pytest tests/integration/infra/test_otel_pipeline.py -v -m integration
    make compose-down
"""

from __future__ import annotations

import time
import uuid

import httpx
import pytest

from codeoracle.core.telemetry import TelemetryConfig, _reset_for_testing, get_tracer, init_telemetry


@pytest.fixture
def _telemetry_to_collector() -> None:
    _reset_for_testing()
    init_telemetry(TelemetryConfig(endpoint="http://localhost:4317", enabled=True))
    yield
    _reset_for_testing()


@pytest.mark.integration
def test_span_flows_end_to_end(_telemetry_to_collector: None) -> None:
    # Unique span name so Tempo search returns only this run's span.
    span_name = f"plan-1a-smoke-{uuid.uuid4().hex[:8]}"

    tracer = get_tracer("plan-1a.integration")
    with tracer.start_as_current_span(span_name) as span:
        span.set_attribute("test.kind", "smoke")
        span.set_attribute("test.run_id", span_name)

    # BatchSpanProcessor flushes on a timer (~5s by default). Poll Tempo.
    deadline = time.monotonic() + 60
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        try:
            response = httpx.get(
                "http://localhost:3200/api/search",
                params={
                    "tags": f"test.run_id={span_name}",
                    "limit": "10",
                },
                timeout=5.0,
            )
            response.raise_for_status()
            traces = response.json().get("traces", [])
            if traces:
                # Found the span — pipeline works
                assert any(t.get("rootServiceName") == "codeoracle" for t in traces), (
                    f"Tempo returned traces but none with service.name=codeoracle: {traces}"
                )
                return
        except Exception as exc:  # noqa: BLE001 — we poll, capture last error for failure msg
            last_error = exc
        time.sleep(2.0)

    pytest.fail(
        f"Tempo did not return span tagged test.run_id={span_name} within 60s. "
        f"Last error: {last_error}"
    )
```

- [ ] **Step 2: Run test to verify it fails (stack not up)**

```
uv run pytest tests/integration/infra/test_otel_pipeline.py -v -m integration
```

Expected: 1 failed (Tempo not reachable, or no spans returned).

- [ ] **Step 3: Run test against the live stack**

```bash
make compose-up
sleep 10  # let healthchecks settle
uv run pytest tests/integration/infra/test_otel_pipeline.py -v -m integration
make compose-down
```

Expected: 1 passed (the span flowed end-to-end).

- [ ] **Step 4: Commit**

```bash
git add tests/integration/infra/test_otel_pipeline.py
git commit -m "test(infra): end-to-end OTel pipeline acceptance

Proves the full observability chain: Python emits via core/telemetry.py
→ otel-collector receives on 4317 → forwards to Tempo → Tempo's search
API returns the span. This is the canonical 'observability works' gate
for §8.5 gate 9. Closes Plan 1A's acceptance ceremony.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Self-Review

**1. Spec coverage:**

| Spec section | Plan 1A coverage |
|---|---|
| §3.2 (`core/telemetry.py`, `utils/async_helpers.py`) | Tasks 1, 2 |
| §3.4 (`infra/` tree, complete) | Tasks 3, 4, 5, 6, 7, 8, 9, 10, 11, 12 |
| §7.1 (workflow files) | Tasks 13, 14, 15 |
| §7.2 (`ci.yml` primary PR gate) | Task 13 modifies it |
| §7.3 (`image-build.yml`) | Task 14 |
| §7.4 (`security.yml`) | Task 13 |
| §7.5 (Astral toolchain configs in pyproject.toml) | Already pinned in current pass (`requires-python = ">=3.14"`) |
| §7.6 (file-length enforcement) | Not in Plan 1A — `check_file_length.py` is Plan 1's (D-1.2 is a separate mechanical EDIT) |
| §7.9 (secret-handling matrix) | Task 13's `security.yml` implements Dev + CI default tiers; live-llm tier is Plan 4; prod tier is Phase 7+ |
| §8.5 image-Trivy gate | Task 14 |
| §8.5 OWASP semgrep gate | Tasks 12, 13 |
| §8.5 observability gate (gate 9) | Task 18 |

No spec gaps that this plan should cover.

**2. Placeholder scan:**

- No "TBD" / "TODO" / "implement later".
- No "add error handling" / "similar to Task N" / "fill in details".
- Every code step shows the actual code; every commit step shows the actual commit message.

**3. Type consistency:**

- `TelemetryConfig` (Task 2) — `endpoint: str`, `enabled: bool` — referenced consistently in `test_otel_pipeline.py` (Task 18).
- `get_tracer(name: str) -> trace.Tracer` (Task 2) — called with `__name__` convention in Task 18.
- `gather_with_concurrency(limit: int, *coros) -> list[T]` (Task 1) — limit is positional first arg consistently.
- `wait_for_with_default(awaitable, timeout_s, default)` — kwargs match across test and implementation.
- Env var names (`CODEORACLE_OTEL_ENDPOINT`, `CODEORACLE_LLM_PROVIDER`, etc.) — consistent across `defaults.env` (Task 3), `telemetry.py` (Task 2), `defaults.env` test (Task 3).
- Variant list `[api, worker, cli, mcp, agent]` — consistent across Dockerfile (Task 8), image-build.yml matrix (Task 14), Makefile docker-build target (Task 17).

No inconsistencies found.

---

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-05-21-operational-infrastructure.md`. Two execution options:

**1. Subagent-Driven (recommended)** — I dispatch a fresh subagent per task, review between tasks, fast iteration.

**2. Inline Execution** — Execute tasks in this session using executing-plans, batch execution with checkpoints.

**Which approach?**
