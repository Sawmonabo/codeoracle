# Plan 13 — E2E + Slice Acceptance Ceremony Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship the slice. Land the 9 cross-process integration tests that pin spec §8.5's functional + observability acceptance gates, the three §8.7 demo scripts (PROVEN / UNKNOWN / live-edit), the GitHub-Actions workflow that runs every §8.5 architectural-invariant + functional gate in one CI run, the §6 EDIT amending spec §8.5 gate 9 to drop the `traces.query` span (per P0-7 / D-10.1 — Plan 10 dropped the `traces_query` node), and the acceptance demo documentation plus tracked recording fixtures.

**Architecture:** Plan 13 introduces NO new architectural surface (no new services, adapters, routes, or processes). It is a *verification-only* plan: a docker-compose E2E stack that wires the production processes built by Plans 1-12 (`codeoracle-api`, `codeoracle-mcp`, `codeoracle-agent`, the worker pool, Postgres+AGE, Valkey, Sourcebot, Tempo, Prometheus) into one runnable graph; pytest integration tests that drive the live stack via HTTP / MCP clients; demo scripts that exercise the §8.7 ceremony paths and record reproducible asciinema artifacts; one GitHub-Actions workflow that reusable-calls Plan 1's `ci.yml` for the architectural-invariant gates (the SAST + image-scan gates run on the PR via their own triggers as required checks) + runs the nine integration tests + the three observability assertions; one §6 EDIT amending the spec; and one closure doc that hands Phase G the corpus-wide cross-plan-deps update. The integration tests use the real `MockLLMProvider` (Plan 4) + `tests/fixtures/llm_scripts/` so request-fingerprint drift is exercised end-to-end — not the per-test `_FakeLLM` Plan 10 unit tests use (per D-10.U3).

**Tech Stack:** Python 3.14 · pytest + pytest-asyncio (`asyncio_mode = "auto"`, inherited from Plan 3 Task 1) · `pytest-docker` 3.1 (manages `docker compose up -d` lifecycle per test session) · httpx 0.27 (drives `codeoracle-api`'s `POST /v1/query` + `POST /v1/reindex`) · `mcp` 1.12 SDK client (drives `codeoracle-mcp`'s streamable-HTTP endpoint via the SDK's `StreamableHTTPClient`) · asciinema 2.4 (records demo runs as `.cast` files committed under `tests/fixtures/acceptance-recordings/`) · Docker Compose v2 (orchestrates the E2E stack — same compose binary Plan 2 + Plan 1A use) · GitHub Actions (workflow runner — reusable-calls Plan 1's existing `ci.yml` CI surface; does NOT create a new runner image) · ruff + ty (every new file passes the same quality gates as the rest of the corpus).

---

## Invariants

The following invariants are load-bearing across the plan. They are restated in tasks where they apply but are catalogued here so any reader has a single reference.

1. **Plan 13 ships no new production architectural surface.** No new production services, adapters, routes, processes, models, or domain logic. Test orchestration runs strictly from the host-side test process via (a) direct `asyncpg` connections to the compose Postgres (host-port 5432) for DB reset / graph-state snapshotting / trace-row counting, and (b) direct host-filesystem writes to the bind-mounted `src/codeoracle/` tree for edit-staging + tampering (the overlay mounts `src/codeoracle/` `:ro` from the *container's* perspective; the host process retains read-write access to the same inode). Every Python module Plan 13 creates lives under `tests/`; demo scripts live under `scripts/demos/`; demo documentation lives under `docs/acceptance/`; demo recordings live under `tests/fixtures/acceptance-recordings/`. Files under `src/codeoracle/` are NOT touched by this plan. The single config-file mutation is `pyproject.toml` (Task 14 — adds the test-only `pytest-docker` + `asciinema` + `asyncpg` dev-deps + the `slice` pytest marker). See DECIDE-U for the explicit rejection of test-only admin endpoints (which would have violated this invariant).

2. **Real provider, no test doubles in integration tests.** Every Plan 13 integration test runs against the live `MockLLMProvider` configured via `CODEORACLE_LLM_PROVIDER=mock` + `CODEORACLE_LLM_MOCK__SCRIPTS_DIR=tests/fixtures/llm_scripts` (Plan 4 wiring). Plan 10's per-test `_FakeLLM` is unit-test-scope only; Plan 13's integration tests exercise the real fingerprint pipeline so cosmetic prompt-template drift fails the slice CI (per D-10.U3). No `respx` HTTP mocking inside Plan 13 tests — they speak real HTTP to the live API / MCP processes inside the compose stack.

3. **Test corpus is `src/codeoracle/` at HEAD.** Per spec §8.3, the slice's only test corpus is the platform's own source tree. Plan 13 fixtures resolve `settings.repo_root` to the actual HEAD checkout inside the compose stack. No synthetic fixture trees, no checked-in toy corpora. If `src/codeoracle/` cannot explain itself, the slice has not shipped. This is also the dogfood gate.

4. **Byte-equal parity is modulo `trace_id` only.** The §8.5 functional gate #4 (`test_mcp_explain_symbol_matches_api_response`) asserts the `QueryResult` returned via MCP `explain_symbol` is byte-equal to the `QueryResult` returned via HTTP `POST /v1/query` for the same `Query`, after rewriting `trace_id` to a sentinel on both sides. Every other field — `verdict`, `claim.statement`, `claim.citations`, `claim.confidence`, `claim.contradictions`, `claim.resolved_citations` — must match exactly under `model_dump(mode='json')` JSON canonicalisation (sorted keys, no whitespace). Plan 11 invariant 3 + Plan 12 invariant 3 jointly own the production-side guarantee; Plan 13 verifies it.

5. **Spec §6 EDIT — gate 9 amendment is the only spec mutation.** Plan 13 amends spec §8.5 observability gate 9 to drop `traces.query` from the required child-span list (per P0-7 / D-10.1). The mutation is a single §6 EDIT inside this plan, applied via `Edit` to `docs/superpowers/specs/2026-05-21-platform-scaffolding-design.md`. No other spec section is touched. The §6 EDIT diff is reciprocally cited from Plan 10's Deviation 1.

6. **Performance metrics are MEASURED, not GATED.** Per spec §8.5 "Performance (measured, not gated) … Recorded as Codecov-like CI artifacts; not gate blockers at slice stage." Plan 13 emits the four perf metrics (full reindex p95, incremental p95, no-op p95, query median + p99) as JSON artifacts uploaded to the workflow run, parsed by a `slice-performance-report` job that posts a summary comment to the PR — but the workflow does NOT fail on perf regressions during the slice. Hard gating lands Slice+1.

7. **Asciinema artifacts are checked in, not regenerated per CI.** The three demo `.cast` files (`tests/fixtures/acceptance-recordings/proven.cast`, `tests/fixtures/acceptance-recordings/unknown.cast`, `tests/fixtures/acceptance-recordings/live-edit.cast`) are produced ONCE by a human running `asciinema rec` against the live compose stack, then committed under git LFS (or as binary blobs — file-size budget < 200KB each). CI's slice-acceptance workflow validates the `.cast` files PARSE as valid asciinema v2 + the embedded transcript contains the canonical `verdict.status` line, but does NOT re-record them. Re-recording requires a manual step + a fresh PR per spec §8.7.

8. **The slice-acceptance workflow is one workflow.** All §8.5 gates run inside `.github/workflows/slice-acceptance.yml` — one file, one workflow, one set of compose-stack startups (pinned to one job, shared across steps via service-container reuse where possible). Splitting per-gate would multiply compose-startup cost (~30s + DB schema load + Sourcebot indexing pass each ≈ 45s × 8 gates) without adding signal. The architectural invariants (ruff, format, ty, file-length, pytest-coverage — via Plan 1's `ci.yml` reusable-call, which **also carries the `ci.yml` `security` job's Trivy-fs + OSV-Scanner steps**: Plan 1A Task 13 moved only gitleaks + semgrep out of `ci.yml`, leaving trivy-fs/osv in place pending the slice-readiness scanner-topology disposition; gitleaks + semgrep run via Plan 1A's `security.yml`, and the Trivy **image** scan via `image-build.yml`, both as own-trigger required PR checks) run as parallel jobs that do NOT need the compose stack; the integration / observability / performance gates run sequentially inside the compose-up job.

9. **All Plan 13 paths stay in test, script, fixture, acceptance-doc, workflow, and compose trees.** No file lives under `tests/unit/` (Plan 13 doesn't ship unit tests — every Plan-13 test crosses two or more deployed-process boundaries by definition). The integration tree layout: `tests/integration/slice/test_*.py` for the 9 §8.5 tests + `tests/integration/slice/conftest.py` for the compose-stack fixtures + `tests/integration/slice/_helpers.py` for shared assertion utilities. Demo scripts live in `scripts/demos/`; checked-in recordings live in `tests/fixtures/acceptance-recordings/`; run instructions live in `docs/acceptance/demos.md`.

10. **Closure evidence stays out of deleted state-doc paths.** Closure notes record verification logs from running all 9 integration tests against a green compose stack, the recording log, and the Phase G handoff prompt that triggers the corpus-wide cross-plan-deps refresh across all 14 plans (1, 1A, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13).

11. **`pytest-docker` owns compose lifecycle; tests own the data via direct asyncpg.** `pytest-docker` brings `docker compose up -d` up at session start and tears it down at session end (`docker_compose_command` + `docker_setup` / `docker_teardown` hooks). The session-scoped `compose_stack` fixture yields once the stack's health gates (API `/v1/health`, MCP `/mcp` POST returns 200, Postgres `pg_isready`, Sourcebot HTTP 200 on `/healthz`) all pass. A function-scoped `clean_snapshot` fixture truncates the `snapshots`, `claims`, `citations`, `traces`, and `ag_label_vertex`/`ag_label_edge` tables between tests via a session-scoped `asyncpg.Connection` (NOT via an admin HTTP endpoint — see DECIDE-U) so test-ordering doesn't matter. Truncation failure (connection refused, missing table) raises hard, never silent-skips. The compose stack itself is NOT recreated between tests.

12. **Compose-stack base is Plan 1A's `docker-compose.yml` (extended by Plan 2/3/8).** Plan 13's `infra/compose/docker-compose.e2e.yml` is a Compose v2 overlay file (used as `docker compose -f docker-compose.yml -f docker-compose.e2e.yml`) that ADDS 8 containers — three application processes (`api`, `mcp`, `agent`) and five queue consumers (`joern_indexer`, `scip_indexer`, `opengrep_runner`, `embed_indexer`, `reindex_orchestrator` — one PID-1 per consumer per the Plan 5/6 worker-framework contract; `reindex_orchestrator` added by Plan 5 Task 13a amendment `40885bf` and consumed by Plan 11 amendment `fc0894a`'s async POST + poll contract) — plus a deterministic `src/codeoracle/` bind-mount, on top of Plan 1A's base compose (Postgres, Valkey, Sourcebot, Tempo, Prometheus, OTel collector). Plan 13 does NOT redefine the base services. (Pre-#49 wording named a single generic `worker:` container; corrected in amendment #49 — see L3291 §6 EDIT row + DECIDE-W.)

13. **Pattern Y dispatch — one shared image + per-service `CODEORACLE_VARIANT` env override.** Every slice-e2e service consumes Plan 1A Task 8's unified `infra/docker/Dockerfile` via a YAML anchor (`x-codeoracle-build:`) and runs the same image tag (`codeoracle:slice-e2e`). The 8 services dispatch to their respective Python entry modules at runtime via `environment.CODEORACLE_VARIANT` overriding the Dockerfile's `ENV CODEORACLE_VARIANT=cli` default; Plan 1A's `entrypoint.sh` then expands `exec python -m "codeoracle.${CODEORACLE_VARIANT}" "$@"` against the env var. Shell parameter expansion in entrypoint.sh accepts ANY dotted module path under `codeoracle/`, so the 5 nested `codeoracle.workers.<consumer>` namespaces dispatch as cleanly as the flat `codeoracle.api` / `codeoracle.agent` / `codeoracle.mcp` namespaces. No service declares its own Dockerfile path; the per-variant Dockerfiles named in the pre-#49 draft (`Dockerfile.api`, `Dockerfile.mcp`, `Dockerfile.agent`, `Dockerfile.worker`) never existed in any plan and are NOT created. This invariant is scoped to `docker-compose.e2e.yml` only — production Helm charts in Slice+1 may choose per-deployment image tags (Pattern X) without re-litigating the slice's choice. Rationale + steel-manned alternative in DECIDE-W. Added 2026-05-26 in amendment #49.

---

## File structure

The plan creates files under integration-test, demo-script, fixture, acceptance-doc, compose, and workflow trees. It modifies one pre-existing file (`pyproject.toml` — Task 14) and applies one §6 EDIT to the spec (Task 11).

```
tests/integration/slice/                                   # NEW package
├── __init__.py                                            (NEW — empty)
├── conftest.py                                            (NEW — compose-stack fixtures; Task 2)
├── _helpers.py                                            (NEW — assertion utilities; Task 2)
├── test_slice_explain_symbol.py                           (NEW — 3 functional tests; Tasks 3, 4, 6)
├── test_slice_verifier_refuted.py                         (NEW — §8.5 gate 3 verifier-integration; Task 5)
├── test_slice_reindex.py                                  (NEW — 1 functional test; Task 7)
├── test_slice_incremental.py                              (NEW — 3 incremental tests; Task 8)
└── test_intent_classifier_handle_types.py                 (NEW — 1 integration test; Task 9)

scripts/demos/                                             # demo scripts
├── proven.sh                                              (PROVEN demo; Task 10)
├── unknown.sh                                             (UNKNOWN demo; Task 10)
└── live-edit.sh                                           (live-edit demo; Task 10)

docs/acceptance/
└── demos.md                                               (how to run / re-record demos; Task 10)

tests/fixtures/acceptance-recordings/
├── proven.cast                                            (asciinema recording, manual; Task 10)
├── unknown.cast                                           (asciinema recording, manual; Task 10)
└── live-edit.cast                                         (asciinema recording, manual; Task 10)

tests/fixtures/llm_scripts/                                # Plan 13's MockLLMProvider fixtures
├── slice_explain_verifier.yaml                            (PROVEN canonical fixture; Task 3)
├── slice_explain_deleted.yaml                             (UNKNOWN canonical fixture; Task 4)
└── slice_intent_classifier.yaml                           (classifier canonical fixture; Task 9)
# NB: no REFUTED fixture — Task 5 is verifier-integration only (no LLM call); see DECIDE-U and Task 5 prose.

infra/compose/                                             # extends Plan 1A's compose tree
└── docker-compose.e2e.yml                                 (NEW — application-process overlay; Task 1)

.github/workflows/
└── slice-acceptance.yml                                   (NEW — §8.5 CI gates; Task 12)

docs/superpowers/specs/
└── 2026-05-21-platform-scaffolding-design.md              (M — §6 EDIT: gate 9 amendment; Task 11)

pyproject.toml                                             (M — Task 14 adds pytest-docker + asciinema dev-deps + slice marker)
```

**Total: 21 new files, 2 modified** (8 under `tests/integration/slice/`, 3 under `scripts/demos/`, 3 under `tests/fixtures/acceptance-recordings/`, 3 under `tests/fixtures/llm_scripts/`, 1 under `docs/acceptance/`, 1 under `infra/compose/`, 1 under `.github/workflows/`; `pyproject.toml` + `docs/superpowers/specs/2026-05-21-platform-scaffolding-design.md` modified). No REFUTED fixture is needed — gate 3 is verifier-integration, no LLM call (see Task 5 prose + DECIDE-U + advisor round-2 finding on Sourcebot index propagation).

---

## Task list

| # | What it delivers | Key files |
|---|---|---|
| 0 | Package layout — empty `__init__.py` for the new test package | `tests/integration/slice/__init__.py` |
| 1 | `docker-compose.e2e.yml` overlay — wires `codeoracle-api`, `codeoracle-mcp`, `codeoracle-agent`, the worker container, the deterministic `src/codeoracle/` bind-mount on top of Plan 1A's base compose | `infra/compose/docker-compose.e2e.yml` |
| 2 | E2E conftest + helpers — `pytest-docker` session-scoped compose lifecycle + per-test `clean_snapshot` + shared assertion utilities (`assert_query_results_equal_modulo_trace_id`, `extract_resolved_handle_types`) | `tests/integration/slice/conftest.py`, `tests/integration/slice/_helpers.py` |
| 3 | §8.5 functional gate 1 — `test_explain_existing_symbol_returns_proven_with_citations`; plus the canonical `MockLLMProvider` fixture for the PROVEN path | `tests/integration/slice/test_slice_explain_symbol.py`, `tests/fixtures/llm_scripts/slice_explain_verifier.yaml` |
| 4 | §8.5 functional gate 2 — `test_explain_deleted_symbol_returns_unknown`; plus the UNKNOWN fixture | `tests/integration/slice/test_slice_explain_symbol.py` (M), `tests/fixtures/llm_scripts/slice_explain_deleted.yaml` |
| 5 | §8.5 functional gate 3 — `test_verifier_returns_refuted_when_excerpt_mismatches_source` (verifier-integration; constructs a Citation with a deliberately-wrong excerpt and calls `VerifierService.verify` directly against the compose-deployed Sourcebot — no API/agent layer, no fixture YAML, no host-filesystem mutation). See Task 5 prose for why this is the only deterministic discrimination mechanism. | `tests/integration/slice/test_slice_verifier_refuted.py` |
| 6 | §8.5 functional gate 4 — `test_mcp_explain_symbol_matches_api_response`; the byte-equal parity gate that pins Plan 11 invariant 3 + Plan 12 invariant 3 jointly across two transports | `tests/integration/slice/test_slice_explain_symbol.py` (M) |
| 7 | §8.5 functional gate 5 — `test_reindex_endpoint_produces_new_snapshot` | `tests/integration/slice/test_slice_reindex.py` |
| 8 | §8.5 incremental gates 6-8 — `test_incremental_reindex_matches_full_reindex`, `test_incremental_reindex_invalidates_dependents`, `test_incremental_reindex_performance` | `tests/integration/slice/test_slice_incremental.py` |
| 9 | §8.5 intent-classifier gate 9 — `test_intent_classifier_never_produces_unsupported_handle_types`; integration analogue of Plan 10's unit-test version (D-10.U3 calls this out) | `tests/integration/slice/test_intent_classifier_handle_types.py`, `tests/fixtures/llm_scripts/slice_intent_classifier.yaml` |
| 10 | §8.7 demo scripts + recording workflow — `proven.sh`, `unknown.sh`, `live-edit.sh`, `demos.md`, plus the three checked-in `.cast` files | `scripts/demos/*`, `docs/acceptance/demos.md`, `tests/fixtures/acceptance-recordings/*` |
| 11 | §6 EDIT to spec — drops `traces.query` from §8.5 gate 9's required child-span list per P0-7 / D-10.1; reciprocally cited from Plan 10 Deviation 1 | `docs/superpowers/specs/2026-05-21-platform-scaffolding-design.md` (M) |
| 12 | `.github/workflows/slice-acceptance.yml` — reusable-calls Plan 1's `ci.yml` for the architectural-invariant gates (its child jobs run in parallel) + the compose-up job that hosts the 9 integration tests + the 3 observability assertions + the perf measurement step | `.github/workflows/slice-acceptance.yml` |
| 13 | Acceptance demo documentation — run and re-recording instructions for the checked-in `.cast` recordings | `docs/acceptance/demos.md` |
| 14 | `pyproject.toml` — adds `pytest-docker` 3.1, `asciinema` 2.4 as dev-deps; adds `[tool.pytest.ini_options] markers = ["slice: cross-process E2E tests, require docker compose"]` | `pyproject.toml` (M) |
| 15 | Cross-plan dependencies + §6 EDIT table + closure + Phase G handoff prompt | This plan document |

Tasks 0 → 1 → 2 form the prerequisite chain (package, stack, fixtures). Tasks 3-9 are independent once 2 lands (can be parallelised by an executor). Task 10 depends on 3, 4, 7 (the ceremony scripts run the same queries the integration tests cover). Task 11 (spec §6 EDIT) is sequenced AFTER Task 9 — the spec mutation only commits once we have a passing observability test that confirms the dropped span is genuinely absent. Task 12 depends on 1 + 3-9 + 14. Task 13 depends on 10. Task 15 depends on every earlier task.

---

## Cross-plan dependencies

### Imports from earlier plans

| Plan | Artifact | Used in Plan 13 by |
|---|---|---|
| **Plan 1** | `ruff` + `ty` + `pytest` quality gates; `requires-python = ">=3.14"`; `check_file_length.py` (file-length gate) | Task 12 — the slice-acceptance workflow runs these as the architectural-invariant jobs (gates 1-5 of §8.5) |
| **Plan 1A** | `infra/observability/` Prometheus + Tempo + Grafana provisioning + `core/telemetry.py:init_telemetry()` exporter configuration; `infra/compose/docker-compose.yml` — the base compose file Plan 1A creates (Plan 2/3/8 extend it with Sourcebot + DB services); the unified `infra/docker/Dockerfile` (Plan 1A Task 8: `ARG VARIANT=cli`, `ENV CODEORACLE_VARIANT=${VARIANT}`, `ENTRYPOINT ["/entrypoint.sh"]`) + `entrypoint.sh` (Plan 1A Task 8: `exec python -m "codeoracle.${CODEORACLE_VARIANT}" "$@"`); the security + image-scan CI workflows (`security.yml` — Task 13: gitleaks/semgrep/dep-audit/SBOM; `image-build.yml` — Task 14: Trivy image ×5), which run on the slice PR via their own `pull_request`/`push` triggers as required status checks (they are NOT `workflow_call`-able) | Task 1 (compose overlay references Plan 1A's Tempo + Prometheus services for the observability gates; the overlay also consumes Plan 1A's unified Dockerfile via the `x-codeoracle-build:` YAML anchor + dispatches per-service variants at runtime via `environment.CODEORACLE_VARIANT` overriding the Dockerfile's `ENV` default — Plan 13 DECIDE-W "Pattern Y"; entrypoint.sh's shell parameter expansion accepts the dotted module paths Plan 5/6 require for nested worker namespaces); Task 12 (workflow's observability-assertion step queries Tempo via the OTLP HTTP API for the required child-span set; `slice-acceptance.yml` reusable-calls only Plan 1's `ci.yml` for the structural/quality gates — Plan 1A's `security.yml` + `image-build.yml` run on the slice PR via their own triggers as required checks, not re-invoked here). **Plan 13 amendment #49 micro-change to Plan 1A's Dockerfile** — the runtime-base stage now installs curl (`RUN apt-get update && apt-get install -y --no-install-recommends curl && rm -rf /var/lib/apt/lists/*`) for the slice-e2e HTTP healthchecks (api/mcp/agent use `curl -fsS` probes; `python:3.14-slim-bookworm` omits curl by default). The rest of Plan 1A's Task 8 contract is consumed as-committed: VARIANT build-arg, `ENV CODEORACLE_VARIANT=${VARIANT}` default, `ENTRYPOINT ["/entrypoint.sh"]`, and `exec python -m "codeoracle.${CODEORACLE_VARIANT}" "$@"` are all preserved unchanged. |
| **Plan 2** | _No direct artifact import._ `slice-acceptance.yml` reusable-calls only Plan 1's `ci.yml` (the structural/quality invariant gates — §8.5 gates 1-5, plus `ci.yml`'s own `verification-pins` (Plan 2 Task 13) + migration-smoke jobs that ride along in the call) via `workflow_call`; the SAST + image-scan gates run on the slice PR via Plan 1A's `security.yml` (Task 13: gitleaks/semgrep/dep-audit/SBOM) + `image-build.yml` (Task 14: Trivy image ×5) own triggers as required checks (those workflows are not `workflow_call`-able) — none of these are Plan 2's; the base `infra/compose/docker-compose.yml`, the unified `infra/docker/Dockerfile`, and `entrypoint.sh` are Plan 1A's (Task 8); `infra/scripts/preflight.sh` (Plan 2 Task 12) is not consumed by any Plan 13 task; the Sourcebot image Plan 2 Task 8 verifies underpins the `sourcebot` base-compose service already tracked in the Plan 8 row. | — (no reciprocal needed — see the Plan 2 catalogue note below) |
| **Plan 3** | `core/config.py:Settings` + nested `ApiConfig` + `DBConfig` (E2E reads them via `CODEORACLE_*` env in compose); `models/query.py:Query` + `QueryResult`; `models/evidence.py:VerdictStatus`; `models/snapshot.py:Snapshot` + `SnapshotStatus`; `models/handle.py:HandleType` + `Handle` | Task 2 helpers cast HTTP / MCP responses to `QueryResult`; Task 3-9 construct `Query` payloads + assert `QueryResult` shape; Task 8 reads `Snapshot.applied_at` for perf timing |
| **Plan 4** | `adapters/llm/providers/mock.py:MockLLMProvider` + `adapters/llm/fingerprint.py:request_fingerprint`; `MockLLMProviderConfig.scripts_dir` (defaults to `tests/fixtures/llm_scripts`); the YAML script format from Task 7 | Every Plan 13 integration test runs against `CODEORACLE_LLM_PROVIDER=mock` + `CODEORACLE_LLM_MOCK__SCRIPTS_DIR=tests/fixtures/llm_scripts`. The four new fixture YAMLs (Tasks 3, 4, 5, 9) live in the same directory and follow Plan 4's `template_id` + `request_fingerprint` keying scheme (per D-10.U3) |
| **Plan 5** | `services/indexing/service.py:IndexingService.enqueue_reindex(*, base_sha)` + `get_snapshot(snapshot_sha)` (Plan 5 amendment `40885bf` 2026-05-26 — sync `IndexingService.reindex(*, base_sha)` retained for the CLI / vertical integration test only); the advisory-lock semantics held by the `ReindexOrchestratorConsumer` worker process (no longer the API process); the per-file extraction pipeline (joern + scip + opengrep + embed); `models/snapshot.py:Snapshot` lifecycle (PENDING → APPLYING → APPLIED \| FAILED); `workers/reindex_orchestrator/consumer.py:ReindexOrchestratorConsumer` consuming `STREAM_REINDEX_REQUESTS`; the two Plan 5 worker module entrypoints — `workers/joern_indexer/__main__.py` (Plan 5 Task 7) and `workers/reindex_orchestrator/__main__.py` (Plan 5 Task 13a amendment `40885bf`) | Task 7 drives the POST + poll round-trip via `_helpers.drive_reindex_to_applied` (Plan 13 amendment 2026-05-26, DECIDE-V) and asserts the terminal `Snapshot.status == APPLIED` + 4-extractor outcome; Task 8 drives the incremental path via the same helper. Task 1 compose overlay invokes the Plan 5 worker entrypoints directly as `python -m codeoracle.workers.joern_indexer` (one PID-1 per `joern_indexer:` service) and `python -m codeoracle.workers.reindex_orchestrator` (one PID-1 in the `reindex_orchestrator:` service that depends_on all 4 per-extractor consumers being healthy first). Dispatch is via Plan 1A's entrypoint.sh + per-service `environment.CODEORACLE_VARIANT: workers.<name>` runtime override — see DECIDE-W. |
| **Plan 6** | All four extractors (joern, scip, opengrep, embed) running inside the worker containers — Plan 13 verifies the four-extractor fan-out as a live E2E (per D-6.5); the three Plan 6 worker module entrypoints — `workers/scip_indexer/__main__.py` (Plan 6 Task 4), `workers/opengrep_runner/__main__.py` (Plan 6 Task 5), `workers/embed_indexer/__main__.py` (Plan 6 Task 6) | Task 7 asserts `Snapshot.extractor_results` contains all 4 extractor keys with `ExtractorOutcome.SUCCESS`; Task 8 incremental tests assert the same set on the new snapshot. Task 1 compose overlay invokes the three Plan 6 worker entrypoints directly — `python -m codeoracle.workers.scip_indexer`, `python -m codeoracle.workers.opengrep_runner`, `python -m codeoracle.workers.embed_indexer` (one PID-1 each in the `scip_indexer:`, `opengrep_runner:`, `embed_indexer:` services). Dispatch is via Plan 1A's entrypoint.sh + per-service `environment.CODEORACLE_VARIANT: workers.<name>` runtime override — see DECIDE-W. |
| **Plan 7** | `workers/traces_ingest.py` — the worker that pulls platform self-traces back from Tempo (observability gate 10 from §8.5); `db/repositories/traces.py:TracesRepository` | Task 12 (workflow's observability-assertion step queries the platform's own ingested-trace table after the integration tests run; gate 10 passes if the table contains spans from the test run) |
| **Plan 8** | `services/handle_resolution/service.py:HandleResolverService`; `adapters/sourcebot/client.py:SourcebotClient`; the four handle types (CODE_SPAN, GRAPH_QUERY, DOC_PARAGRAPH, TRACE_SPAN) with TRACE_SPAN + DB_RECORD wired as `StubResolver` per §8.2; the `+wt` SHA stripping fix (G21 / M-8.1) | Task 3-6 assert resolved citations span ≥2 handle types per §8.5 gate 1; Task 5 imports `HandleResolverService` + `SourcebotClient` directly (no API/agent layer) and constructs a mismatched-excerpt Citation to drive REFUTED (host-filesystem mutation cannot drive REFUTED because `CodeSpanResolver` reads via Sourcebot's HTTP-served Zoekt index, not the host filesystem — advisor round-2 finding) |
| **Plan 9** | `services/verifier/service.py:VerifierService.verify`; the verdict-classification rules (PROVEN / UNKNOWN / REFUTED); the per-resolution structured log line (criterion 11 in §8.5) | Tasks 3-6 assert the produced `verdict.status` against the canonical expected value; Task 12 grep-asserts the verifier's per-resolution log line is present in the API container's stdout |
| **Plan 10** | `codeoracle-agent` process binary + `agent/__main__.py` (Plan 10 amendment #45 — uvicorn entrypoint) + the LangGraph agent's 6-node graph (`intent_parse` → `graph_query` → `embed_query` → `compose` → `verify`); the `_FakeLLM` private class is EXPLICITLY NOT consumed by Plan 13 (D-10.U3); the deterministic intent node's codified handle-type closure (Plan 10 invariant 8 — `TRACE_SPAN`/`DB_RECORD` structurally impossible to emit) | Task 1 compose runs the agent process via `python -m codeoracle.agent` (one PID-1 in the `agent:` service; dispatch via Plan 1A entrypoint.sh + `CODEORACLE_VARIANT: agent` per DECIDE-W; Plan 10 #45's `agent/__main__.py` is the load-bearing entry module); Tasks 3-9 drive it via the API; Task 9 verifies the handle-type closure (invariant 8) at the integration boundary |
| **Plan 11** | `codeoracle-api` process binary + `api/__main__.py` (Plan 11 Task — uvicorn entrypoint) + `POST /v1/query` + `POST /v1/reindex` (202 + `Snapshot(PENDING)`) + `GET /v1/reindex/{snapshot_sha}` (polling endpoint) + `GET /v1/health`; `Query` → `QueryResult` wire contract (invariant 3); idempotent retry on duplicate reindex POST (invariant 4 — Plan 11 amendment `fc0894a` 2026-05-26, sync→async pivot); 503 on agent unreachable (invariant 5) | Task 1 compose runs the API via `python -m codeoracle.api` (one PID-1 in the `api:` service; dispatch via Plan 1A entrypoint.sh + `CODEORACLE_VARIANT: api` per DECIDE-W); Tasks 3-7 + 9 drive `POST /v1/query` + the POST + poll reindex contract via `_helpers.drive_reindex_to_applied` (Plan 13 amendment 2026-05-26, DECIDE-V); Task 6 covers the parity gate by querying both API + MCP for the same `Query` |
| **Plan 12** | `codeoracle-mcp` process binary + `mcp/__main__.py` (Plan 12 Task — argv-driven `--http` parses inside `main()` to select streamable HTTP vs stdio transport per DECIDE-I) + `transports.run_streamable_http` (streamable HTTP at `/mcp`) + `MCPServer.explain_symbol` tool + byte-equal `QueryResult` parity with API (invariant 3) | Task 1 compose runs the MCP via `python -m codeoracle.mcp --http` (one PID-1 in the `mcp:` service; dispatch via Plan 1A entrypoint.sh + `CODEORACLE_VARIANT: mcp` per DECIDE-W; compose `command: ["--http"]` is the argv tail that entrypoint.sh forwards via `"$@"` — entrypoint.sh supplies the `python -m codeoracle.mcp` prefix); Task 6 drives `codeoracle-mcp`'s `explain_symbol` tool via the SDK's `StreamableHTTPClient` against the same `Query` Task 6 sends to the API and asserts byte-equal `QueryResult` |
| **specs/2026-05-21-platform-scaffolding-design.md** | §8.1 the slice query; §8.3 corpus is `src/codeoracle/` at HEAD; §8.4 incremental reindex contract; §8.5 acceptance criteria (5 functional + 3 incremental + 1 intent-classifier + 8 architectural invariants + 3 observability + 4 perf measurements); §8.6 explicit non-goals (auth deferred, FS-watcher deferred); §8.7 acceptance ceremony (3 demos, all recorded + committed); §9 open questions (referenced in closure) | Every Plan 13 task pins one or more §8.5 row; Task 11 amends §8.5 gate 9; Task 13 records the §8.7 ceremony |
| **docs/superpowers/plan-audit-findings.md** | D-6.5 (4-extractor live E2E pushed here); D-9.3 / M-9.1 (criterion-11 log line at the verbose level the gate runs under); D-10.U3 (use real MockLLMProvider, not `_FakeLLM`); P0-7 / D-10.1 (gate 9 amendment); the §6 EDIT obligations route table | Every Plan 13 invariant + decision cites the corresponding row |

### Provides to later plans

| Consumer | Artifact | Used by |
|---|---|---|
| **Phase G (Task #41 — corpus-wide cross-plan-deps refresh)** | Plan 13's own dep-table is the canonical row to insert into Plans 1, 1A, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12 — for each Plan-13-imports-from-Plan-X row, Phase G amends Plan X's "Provides to later plans" / "Provides to Plan 13" entry | Phase G mechanical reciprocal-row sweep |
| **Phase I (Task #42 — full-corpus crosscheck D1/D2/D3/D4)** | The 14-plan corpus is now complete; crosscheck pass runs against the full set with no remaining "Plans 11-13 are unwritten" caveat | Phase I crosscheck dispatch |
| **D4 audit (Task #18 — §8.5 acceptance-gate coverage)** | This plan owns every §8.5 row that is testable at integration scale; D4 audit confirms 100% gate coverage across the 14-plan corpus | Task #18 |
| **Phase 7 (Slice+1 — auth + rate-limit + production deploy)** | The compose overlay (`docker-compose.e2e.yml`) + the demo scripts + acceptance demo recordings become the baseline against which Phase 7 amendments are diffed | Phase 7 plan(s) |
| **Slice+1 plans 14-18 (orchestrator + primitive catalog)** | The MockLLMProvider fixture set Plan 13 creates is the input data for Plan 14's orchestrator-loop integration tests | Plans 14-18 |

### Reciprocal-row catalogue (for D2-style mutual-consistency audits)

Per the D2 audit pattern, every "Plan 13 imports from Plan X" row above MUST be matched by a "Plan X provides to Plan 13" row inside Plan X's own cross-plan-deps section. The reciprocity table for Phase G (Task #41) to apply across the corpus:

| Plan to amend | Section | Add row |
|---|---|---|
| Plan 1 | "Provides to later plans" | "Plan 13 — `ruff` / `ty` / `pytest` / `check_file_length.py` consumed by `slice-acceptance.yml` as the architectural-invariant jobs (gates 1-5 of §8.5)" |
| Plan 1A | "Provides to later plans" | "Plan 13 — `infra/observability/*` (Tempo + Prometheus + Grafana) referenced by the E2E compose overlay + `core/telemetry.py:init_telemetry()` consumed by every process in the overlay; `infra/compose/docker-compose.yml` (the base compose file Plan 1A creates; Plan 2/3/8 extend it) + the unified `infra/docker/Dockerfile` (Plan 1A Task 8: `ARG VARIANT=cli`, `ENV CODEORACLE_VARIANT=${VARIANT}`, `ENTRYPOINT [\"/entrypoint.sh\"]`) + `entrypoint.sh` (`exec python -m \"codeoracle.${CODEORACLE_VARIANT}\" \"$@\"`) + the security + image-scan CI workflows (`security.yml` — Task 13: gitleaks/semgrep/dep-audit/SBOM; `image-build.yml` — Task 14: Trivy image ×5), which run on the slice PR via their own `pull_request`/`push` triggers as required status checks (they are NOT `workflow_call`-able); `slice-acceptance.yml` reusable-calls only Plan 1's `ci.yml`. The Dockerfile + entrypoint.sh are consumed AS-COMMITTED by Plan 13's `docker-compose.e2e.yml` via a shared `x-codeoracle-build:` YAML anchor + per-service `environment.CODEORACLE_VARIANT` runtime override (Plan 13 DECIDE-W \"Pattern Y\"); shell parameter expansion is general enough for the 8 slice services as-is. Plan 13 amendment #49 micro-change to Plan 1A's Dockerfile: a single curl install added to the runtime-base stage (`RUN apt-get install -y --no-install-recommends curl`) for the slice-e2e HTTP healthchecks (api/mcp/agent use `curl -fsS` probes; `python:3.14-slim-bookworm` omits curl by default). The unified-Dockerfile + entrypoint.sh dispatch contract is otherwise preserved." **ALREADY APPLIED at operational-infrastructure.md L47 (#49 pre-applied the Dockerfile half; this Phase G pass folds in the observability half + corrects the CI attribution there) — Phase G adds NO further Plan 1A row.** |
| Plan 2 | (none) | **Plan 13 imports nothing directly from Plan 2** — `slice-acceptance.yml` reusable-calls only Plan 1's `ci.yml` (the structural/quality invariant gates), and the SAST + image-scan gates run on the slice PR via Plan 1A's `security.yml` + `image-build.yml` own triggers; the Dockerfile/entrypoint/base-compose are Plan 1A's (Task 8); `verification-pins` (Plan 2 Task 13) rides along inside the `ci.yml` reusable-call (it is one of `ci.yml`'s jobs) but is not separately imported by any Plan 13 task; the Sourcebot image Plan 2 Task 8 verifies is carried by the Plan 8 reciprocal. **Phase G adds NO Plan 2→Plan 13 provides row.** |
| Plan 3 | "Provides to later plans" | "Plan 13 — `Settings` + `ApiConfig` + `DBConfig` consumed via env; `Query` / `QueryResult` / `Snapshot` / `Handle` / `HandleType` consumed by every integration test as the assertion-target Pydantic models" |
| Plan 4 | "Provides to later plans" | "Plan 13 — `MockLLMProvider` + `request_fingerprint` + `MockLLMProviderConfig.scripts_dir` consumed by every integration test; Plan 13 adds 4 new YAML scripts under `tests/fixtures/llm_scripts/` keyed by Plan 4's fingerprint scheme" |
| Plan 5 | "Provides to later plans" | "Plan 13 — `IndexingService.enqueue_reindex(*, base_sha)` + `get_snapshot(snapshot_sha)` (Plan 5 amendment `40885bf`) consumed via the POST + poll round-trip; `ReindexOrchestratorConsumer` + `STREAM_REINDEX_REQUESTS` consumed by the slice compose overlay's `reindex_orchestrator:` worker service; worker-side advisory-lock contract verified end-to-end by Task 7; per-file extraction pipeline verified by Task 8. The Plan 5 worker module entrypoints `workers/joern_indexer/__main__.py` (Plan 5 Task 7) and `workers/reindex_orchestrator/__main__.py` (Plan 5 Task 13a amendment `40885bf`) are invoked by `docker-compose.e2e.yml` as `python -m codeoracle.workers.joern_indexer` (PID 1 in the `joern_indexer:` service) and `python -m codeoracle.workers.reindex_orchestrator` (PID 1 in the `reindex_orchestrator:` service); dispatch is via Plan 1A entrypoint.sh + `environment.CODEORACLE_VARIANT: workers.<name>` runtime override per Plan 13 DECIDE-W. Sync `IndexingService.reindex(*, base_sha)` still consumed by Plan 5's own CLI / vertical integration test only — NOT by Plan 13." |
| Plan 6 | "Provides to later plans" | "Plan 13 — all four extractors (joern, scip, opengrep, embed) verified as a live E2E in Task 7 + Task 8 per D-6.5. The three Plan 6 worker module entrypoints `workers/scip_indexer/__main__.py` (Plan 6 Task 4), `workers/opengrep_runner/__main__.py` (Plan 6 Task 5), `workers/embed_indexer/__main__.py` (Plan 6 Task 6) are invoked by `docker-compose.e2e.yml` as `python -m codeoracle.workers.scip_indexer`, `python -m codeoracle.workers.opengrep_runner`, `python -m codeoracle.workers.embed_indexer` (one PID 1 per `scip_indexer:` / `opengrep_runner:` / `embed_indexer:` service); dispatch is via Plan 1A entrypoint.sh + `environment.CODEORACLE_VARIANT: workers.<name>` runtime override per Plan 13 DECIDE-W." |
| Plan 7 | "Provides to later plans" | "Plan 13 — `workers/traces_ingest.py` + `db/repositories/traces.py:TracesRepository` consumed by Task 12's observability-gate-10 assertion" |
| Plan 8 | "Provides to later plans" | "Plan 13 — `HandleResolverService` + `SourcebotClient` + the four handle-type strategies consumed by Tasks 3-6; `+wt`-SHA stripping verified at integration scale by Task 8" |
| Plan 9 | "Provides to later plans" | "Plan 13 — `VerifierService.verify` + the verdict-classification rules + the per-resolution structured log line (criterion 11) consumed by Tasks 3-6 + Task 12" |
| Plan 10 | "Provides to later plans" | "Plan 13 — `codeoracle-agent` process binary + `agent/__main__.py` (Plan 10 amendment #45 — uvicorn entrypoint) + the LangGraph 6-node graph; intent-classifier handle-type filter verified by Task 9. `docker-compose.e2e.yml` invokes `python -m codeoracle.agent` as PID 1 in the `agent:` service; dispatch is via Plan 1A entrypoint.sh + `environment.CODEORACLE_VARIANT: agent` runtime override per Plan 13 DECIDE-W. **NOT consumed:** Plan 10's private `_FakeLLM` (Plan 13 uses real `MockLLMProvider` per D-10.U3)" |
| Plan 11 | "Provides to later plans" | "Plan 13 — `codeoracle-api` process binary + `api/__main__.py` (uvicorn entrypoint) + `POST /v1/query` + `POST /v1/reindex` (202 + Snapshot(PENDING)) + `GET /v1/reindex/{snapshot_sha}` (polling) + `GET /v1/health`; invariants 3 (QueryResult parity) / 4 (idempotent retry — Plan 11 amendment `fc0894a`) / 5 (503 on agent unreachable) verified at integration scale by Tasks 3-7 + 9. `docker-compose.e2e.yml` invokes `python -m codeoracle.api` as PID 1 in the `api:` service; dispatch is via Plan 1A entrypoint.sh + `environment.CODEORACLE_VARIANT: api` runtime override per Plan 13 DECIDE-W." |
| Plan 12 | "Provides to later plans" | "Plan 13 — `codeoracle-mcp` process binary + `mcp/__main__.py` (argv-driven `--http` per Plan 12 DECIDE-I) + `transports.run_streamable_http` + `MCPServer.explain_symbol` tool; byte-equal parity (invariant 3) verified jointly with Plan 11 invariant 3 by Task 6. `docker-compose.e2e.yml` invokes `python -m codeoracle.mcp --http` as PID 1 in the `mcp:` service via `command: [\"--http\"]` argv tail; dispatch is via Plan 1A entrypoint.sh + `environment.CODEORACLE_VARIANT: mcp` runtime override per Plan 13 DECIDE-W (entrypoint.sh supplies the `python -m codeoracle.mcp` prefix)." |

(Plan 13 author: leave the Phase G task — #41 — to apply these reciprocal rows across the corpus. This plan's own Cross-plan dependencies section is canonical here.)

---

## Pre-implementation reading list

Before starting Task 0, an implementer should have skimmed each of these (5-10 minutes total):

1. **Spec §8** — `docs/superpowers/specs/2026-05-21-platform-scaffolding-design.md` lines 1255-1484. The slice definition + 9 integration tests + acceptance ceremony. This plan is the operationalisation of §8.
2. **Spec §8.5 gate 9** — lines 1442. Currently lists `traces.query` as a required child span; Plan 13 Task 11 §6-EDIT-drops it per P0-7 / D-10.1.
3. **Plan 10's Deviation 1** — Plan 10 dropped the `traces_query` LangGraph node. Read its justification to understand why the spec gate must be amended.
4. **Plan 4 Task 7** — `MockLLMProvider` YAML script format + the `request_fingerprint` keying scheme. Plan 13 Tasks 3 / 4 / 5 / 9 add new YAML scripts that must conform to this format.
5. **Plan 11 invariant 3 + Plan 12 invariant 3** — the byte-equal parity contract Plan 13 Task 6 verifies. These two invariants are the production-side guarantee; Plan 13 Task 6 is the proof.
6. **Plan 1A's `docker-compose.yml`** — the base compose file. Plan 13 Task 1 produces an overlay; understanding the base is required to write a non-redundant overlay.
7. **Plan 1's `.github/workflows/ci.yml`** — the structural/quality invariant jobs. Plan 13 Task 12's workflow reuses the whole `ci.yml` via one `workflow_call` (its child jobs surface for branch protection).
8. **Audit findings rows D-6.5 + D-9.3 / M-9.1 + D-10.U3 + P0-7 / D-10.1** — the four pre-routed Plan-13 obligations.

After reading: read this plan's §Decisions section to understand the rationale for the seven decisions (DECIDE-N through DECIDE-T) before writing tests.

---

## Decisions

### DECIDE-N: E2E test harness — `pytest-docker` plugin (NOT custom asyncio orchestration)

**Decision:** Use the `pytest-docker` 3.1 plugin to manage `docker compose up -d` / `down` lifecycle around the integration tests. Session-scoped `docker_services` fixture brings the stack up at session start; `docker_compose_command` set to `docker compose -f infra/compose/docker-compose.yml -f infra/compose/docker-compose.e2e.yml` so the overlay layers on top of Plan 1A's base.

**Alternative considered:** Custom conftest fixture that calls `subprocess.run(["docker", "compose", "up", "-d"])` + `subprocess.run(["docker", "compose", "down"])` in a try/finally. More control, no extra dep.

**Why recommendation wins:** `pytest-docker` solves two non-trivial concerns we'd otherwise reimplement: (a) the "wait for service to be healthy before yielding to test" gate (built-in `docker_setup` + `docker_teardown` hooks + a `wait_until_responsive` helper) and (b) parallel-test-run isolation (each pytest session gets a unique compose-project name, derived from PID, so concurrent test runs don't collide on container names). Custom orchestration ends up reimplementing these in ~150 lines of conftest code with no abstraction win.

**Trade-off accepted:** One more test-only dep in `pyproject.toml`. `pytest-docker` is actively maintained (last release 2024-11), MIT-licensed, ~500 LOC of well-tested code. Cost of adoption is bounded; cost of reinventing it is unbounded.

### DECIDE-O: Demo artifact locations — `scripts/demos/`, `docs/acceptance/`, and `tests/fixtures/acceptance-recordings/`

**Decision:** The three §8.7 ceremony scripts (`proven.sh`, `unknown.sh`, `live-edit.sh`) live under `scripts/demos/`; their run instructions live in `docs/acceptance/demos.md`; their `.cast` recordings live under `tests/fixtures/acceptance-recordings/`. Bash + curl + a tiny Python one-liner for JSON formatting — no separate Python module.

**Alternative considered:** A `src/codeoracle/demos/__main__.py` Python module with subcommands (`codeoracle demos proven`, `codeoracle demos unknown`, `codeoracle demos live-edit`). Lives under `src/codeoracle/`, follows the OOP pattern, gets covered by the file-length gate.

**Why recommendation wins:** Scripts are executable developer utilities, recordings are test fixtures, and the durable prose belongs in the acceptance docs. A Python module would put demo orchestration under production source, which is not needed for acceptance evidence.

**Trade-off accepted:** The demo scripts are Bash, which means they're not covered by `ty` strict-mode or by the file-length gate. They are covered by ShellCheck (Plan 2 Task 5 added ShellCheck to the CI invariant set). Cost of typing them is offset by the lower barrier-to-entry for a developer reading them in the ceremony.

### DECIDE-P: PROVEN target symbol — `codeoracle.services.verifier.service.VerifierService.verify` (NOT something else)

**Decision:** The PROVEN ceremony queries `codeoracle.services.verifier.service.VerifierService.verify`. The UNKNOWN ceremony queries `codeoracle.services.deleted.NotARealService`. The live-edit ceremony edits one line in `src/codeoracle/services/verifier/service.py`.

**Alternative considered:** Pick a different canonical symbol — `codeoracle.services.indexing.service.IndexingService.reindex` (also a slice-critical function). Or pick a non-service symbol like a model or a pure helper to demonstrate breadth.

**Why recommendation wins:** Spec §8.7 names `VerifierService.verify` explicitly in the PROVEN demo step + `NotARealService` in the UNKNOWN step + `services/verifier/service.py` in the live-edit step. The spec pinned the targets; Plan 13 honors them. Any other choice forces a reciprocal spec amendment. The symbol is also a good choice on the merits: it's load-bearing (every query goes through it), it has clear `:CALLS` and `:IMPORTS` neighbors (so citations should be rich), and it has a docstring (so DOC_PARAGRAPH citations work).

**Trade-off accepted:** The ceremony is somewhat self-referential ("the platform's verifier explains the platform's verifier"). Spec §8.3's "dogfood property" embraces this — it's the strongest demonstration of the slice working.

### DECIDE-Q: CI workflow shape — one `slice-acceptance.yml` (NOT per-gate workflows)

**Decision:** All §8.5 gates run inside one file: `.github/workflows/slice-acceptance.yml`. One reusable-workflow call of Plan 1's `ci.yml` runs the architectural-invariant suite (its child jobs surface individually for branch protection); one sequential job for the compose-stack + integration tests + observability + perf measurement. The SAST + image-scan gates (`security.yml`, `image-build.yml`) are not `workflow_call`-able and run on the slice PR via their own triggers as required checks (per spec §8.7), not inside this workflow.

**Alternative considered:** A workflow per gate group: `slice-functional.yml` (the 9 integration tests), `slice-observability.yml` (the 3 observability assertions), `slice-perf.yml` (the 4 perf measurements). Cleaner per-gate ownership; finer-grained failure attribution.

**Why recommendation wins:** Splitting per-gate would multiply compose-stack startup cost (~45s × 3 = ~2.3 min wasted). The compose stack is the heavy part of the workflow; running it once and sharing across gate groups is the right optimisation. Failure attribution is still per-step (GitHub Actions UI lists each step) — the only loss is per-workflow rerun granularity, which is a minor convenience cost vs. the duplicated-startup cost.

**Trade-off accepted:** If one gate's container crashes mid-suite, the remaining gates inside the same compose-up job don't run. Acceptable: every gate runs sequentially, and a crash earlier in the chain is a meaningful signal for the entire stack. The architectural-invariant gates run as the reusable `ci.yml` call's parallel child jobs (and the SAST/image-scan gates as own-trigger PR checks), so they're unaffected by the compose-up job's state.

### DECIDE-R: §8.5 gate 9 amendment mechanism — §6 EDIT to spec (NOT a Plan-13 Deviation)

**Decision:** Plan 13 Task 11 applies a §6 EDIT to `docs/superpowers/specs/2026-05-21-platform-scaffolding-design.md` directly. The edit removes `traces.query` from the gate-9 required-child-span list. The §6 EDIT is recorded in this plan's §6 EDIT table + cross-referenced from Plan 10 Deviation 1.

**Alternative considered:** Record the gate-9 deviation as a Plan 13 Deviation (matching Plan 10's pattern for the dropped `traces_query` node) without amending the spec. The spec stays canonical; deviations are recorded per-plan.

**Why recommendation wins:** The spec is the source of truth for the slice acceptance gates. If the gate is genuinely dropped, the source of truth must reflect that — otherwise CI engineers building the slice-acceptance workflow read the spec and write a check that asserts `traces.query` span exists, which will fail every test run forever. The cost of NOT amending the spec is a permanent latent mismatch between two documents. The cost of amending it is one §6 EDIT in Plan 13's §6 table (the same surface Plans 3 / 4 / 7 / 8 / 9 / 10 already use).

**Trade-off accepted:** The spec is no longer immutable — Plan 13 mutates it. Mitigated by: (a) the §6 EDIT table records the mutation with citation back to the audit finding; (b) Plan 10's Deviation 1 keeps the rationale chain visible; (c) the edit is mechanical (one bullet removal), not semantic. The alternative (leaving the spec wrong) is strictly worse.

### DECIDE-S: PROVEN citation-count floor — `>= 3 citations spanning >= 2 handle types` (per spec §8.5 exactly)

**Decision:** The §8.5 gate 1 test asserts `len(claim.citations) >= 3` AND `len({c.handle.type for c in claim.citations}) >= 2`. Spec text: "PROVEN with ≥3 citations spanning ≥2 handle types."

**Alternative considered:** Use the illustrative response example in spec §8.1 (4 citations, 3 handle types) as the floor. More demanding; more deterministic.

**Why recommendation wins:** Spec §8.1 explicitly says "Response (illustrative — exact citations, snapshot SHA, and excerpts depend on the current state of `src/codeoracle/` at slice time; do not match this byte-for-byte in tests)." The illustrative example is NOT the assertion target. Spec §8.5 is. Hard-coding `>= 4` would break the moment the platform's own source tree gets one less imports or one less docstring on `VerifierService.verify`.

**Trade-off accepted:** A regression where the platform suddenly returns only 3 citations spanning only 2 handle types still passes the gate (whereas under the stricter rule it would fail). Acceptable: the gate is at slice-scope; finer-grained citation-quality gating lands Slice+1 (a "test the platform produces the same citations across consecutive runs" stability gate).

### DECIDE-U: Test orchestration — direct asyncpg + host filesystem, NOT admin HTTP endpoints

**Decision:** Plan 13 integration tests perform all test-side orchestration via (a) direct `asyncpg` connections to the compose Postgres at host port 5432 and (b) direct host-filesystem writes to the bind-mounted `src/codeoracle/` tree. The plan does NOT add admin / test-only HTTP routes (no `POST /v1/_test/reset`, no `POST /v1/_test/stage_edit`, no `POST /v1/_test/graph_snapshot`, no `POST /v1/_test/set_sourcebot_url`, no `POST /v1/_test/count_traces`, no `CODEORACLE_API__ENABLE_TEST_RESET` settings flag).

**Alternative considered (Option 1 from advisor round 1):** Amend Plan 11 via a §6 EDIT to add a test-only admin router (`/v1/_test/*`) gated behind `CODEORACLE_API__ENABLE_TEST_RESET=true`. Plan 13 then calls those endpoints from its fixtures.

**Why recommendation wins:** Plan 13's invariant 1 commits the plan to "no new production architectural surface." Adding routes to Plan 11 even gated behind a feature flag produces (i) Plan 11 §6 EDIT churn that re-opens a finalized plan to advisor review, (ii) production-runtime code paths that exist solely to support tests (a maintenance burden), and (iii) a feature-flag misconfig risk in CI where unset/false silently skips tests and signs the slice off green against zero coverage (the silent-skip vacuous-pass advisor finding). Direct asyncpg + filesystem writes do all the same work from the test side with no production-code surface: the test process can read DB credentials from the compose env (or hardcode `postgresql+asyncpg://codeoracle:codeoracle@127.0.0.1:5432/codeoracle`, the published host-port), and the bind-mount semantics let the host process mutate files the containers see live (`:ro` on the *container's* perspective doesn't bind the host).

**Why the REFUTED gate (Task 5) still works without an admin URL switch or host-filesystem tampering** (advisor round 2 corrected an earlier mistaken premise here): `CodeSpanResolver` (Plan 8 Task 9) delegates to `SourcebotClient.fetch_span()` — an HTTP call to Sourcebot's `/api/source`. Sourcebot serves from its own Zoekt index, NOT directly from the bind-mounted filesystem on each query (Zoekt's filesystem-watcher reindex is deployment-dependent and non-deterministic at test time). Host-side `src/codeoracle/` mutations therefore do not propagate to Sourcebot's view in test-relevant time, so a host-filesystem-mutation approach to REFUTED would be a coin flip masquerading as a gate.

Independently, by Plan 10 Invariant 1 + Plan 9's contract, `citation.excerpt` == `HandleResolverService.resolve(handle)` output **by construction** under any API/agent flow — there is no architecturally-reachable path through the API that produces a mismatched-excerpt citation. The REFUTED branch is therefore unreachable through the API in steady state, by design. To exercise REFUTED deterministically, Task 5 imports `VerifierService` + `HandleResolverService` + `SourcebotClient` and calls `VerifierService.verify` directly against the live compose-deployed Sourcebot with a hand-constructed mismatched `Citation`. This is a verifier-integration test (not end-to-end through the API) — and that is the correct test level for this gate.

**Why the incremental gates (Task 8) still use host-filesystem `_swap_file_contents`:** the extractors (Joern + SCIP + opengrep + embed — Plan 5/6) read source directly from the bind-mounted filesystem; they do NOT go through Sourcebot. A host-side mutation propagates into the next reindex call's input. Task 8 tests the extractor pipeline, not the verifier, so host-filesystem mutation is the correct mechanism there.

**Trade-off accepted:** Tests are slightly more coupled to internal schemas (the asyncpg TRUNCATE statement lists table names from Plan 3's migration 0001/0002/0003). Mitigated by: (a) a single `_helpers.truncate_all()` function with the explicit table list — schema changes touch one place; (b) `LANGUAGE plpgsql ... TRUNCATE ag_catalog.<all> CASCADE` for the AGE labels so the per-label name doesn't appear in test code; (c) the alternative (admin endpoint coupled to the FastAPI router) is *more* coupling, not less — it just hides the coupling behind a wire format.

### DECIDE-T: Perf measurement — single-run JSON, no statistical sampling

**Decision:** Tasks 7 + 8's perf measurements record a single observed wall-clock duration per metric (e.g., `full_reindex_duration_s = 142.3`), serialise to `perf-report.json`, upload as a workflow artifact, and post a one-line summary comment on the PR. No p95 / p99 statistical sampling at the slice — that requires N>>1 runs per metric, which inflates CI cost by ~5x.

**Alternative considered:** Run each perf-measured task 10× and emit p50 / p95 / p99 — matches the spec's "p95" language exactly.

**Why recommendation wins:** Spec §8.5 says "Recorded as Codecov-like CI artifacts; not gate blockers at slice stage." The recording purpose is *trending* — engineers want to see "did p95 regress?" — but at single-snapshot scale the metric is noise-dominated regardless. A single-run measurement is honest about that and avoids fake-precision (10-run p95 of 10 cold-cache + warm-cache mixed observations is meaningless). Real p95 measurement requires a perf-bench environment + warm-up runs + statistical analysis — properly Slice+1 work.

**Trade-off accepted:** The PR comment says "full reindex duration: 142.3s (target: <180s, headroom: 37.7s)" instead of "full reindex p95: 142.3s." Honest about the measurement's nature; no false-precision claim. Engineers reading the comment can mentally compute "we've got 21% headroom" without believing it's a population-statistic.

### DECIDE-V: Reindex is async (POST 202 + poll); shared helper lives in `_helpers.py`

**Decision (added 2026-05-26 amendment):** Plan 13's reindex-driving tests speak the async POST + poll contract introduced by the Plan 11 amendment 2026-05-26 (`fc0894a`). The POST returns 202 + `Snapshot(status=PENDING|APPLYING)` and the wait surface is `GET /v1/reindex/{snapshot_sha}` polled until `status` is terminal (APPLIED or FAILED). The polling driver `drive_reindex_to_applied(api_client, *, base_sha)` lives in `tests/integration/slice/_helpers.py` (Task 2 Step 1) — gate 5 (Task 7), incremental gates 6-8 (Task 8), the gate-1/2/4 setup hook `_ensure_indexed` (Task 3), and the intent-classifier gate (Task 9) all consume it. Polling budget is `_REINDEX_POLL_BUDGET_SECONDS = 180.0` (matches the spec §8.5 cold-3K-LOC p95 target); interval is 1.0s. The §8.7 ceremony scripts (Task 10) inline the equivalent shell POST + poll loop so the asciinema viewer sees the state transitions explicitly.

**Alternative considered:** Keep the helper inlined per-test file (one copy in `test_slice_reindex.py`, one in `test_slice_incremental.py`, one in `test_slice_explain_symbol.py` for `_ensure_indexed`, one in `test_intent_classifier_handle_types.py`). Each is ~20 lines so the duplication cost is bounded.

**Why recommendation wins:** Four copies of the same 20-line polling loop would be 80 lines of intra-Plan-13 duplication AND four places where the polling budget or interval could drift inside this plan. A single helper in `_helpers.py` lets the within-plan-13 polling budget and interval change in one place. The Plan 11 amendment commit (`fc0894a`) explicitly flagged this as the planned consolidation point — keeping the helper in `_helpers.py` follows through on that.

**Cross-plan duplication acknowledged (not consolidated in this amendment).** Three copies of the polling driver currently exist in the corpus, with three different cadences chosen for three different audiences:

1. Plan 11's committed inline `_drive_reindex_to_applied` in `tests/integration/api/test_reindex_endpoint.py` (verified against `fc0894a` L2988) — interval `_POLL_INTERVAL_SECONDS = 0.5` (L2912), budget `_POLL_BUDGET_SECONDS = 180.0` (L2913) (fine-grained because the API integration test is single-process via `httpx.AsyncClient(transport=httpx.ASGITransport(app=api_app))` and the loop hot-spins cheap — no container round-trip per iteration).
2. Plan 13's `_helpers.drive_reindex_to_applied` — interval `_REINDEX_POLL_INTERVAL_SECONDS = 1.0`, budget `_REINDEX_POLL_BUDGET_SECONDS = 180.0` (1s is coarse enough for the compose-stack test where every request crosses container boundaries; finer would just burn CPU on serialization).
3. The §8.7 ceremony shell scripts (Task 10) — `sleep 2` between polls (coarser still because the asciinema viewer needs ≥1s between state transitions for the human eye to track them).

The 180s budget IS unified across (1) and (2). Only the interval diverges, and the divergence is intentional. Consolidation (move the helper to a shared `tests/_shared/reindex.py` module imported by both Plan 11's `tests/integration/api/test_reindex_endpoint.py` and Plan 13's `tests/integration/slice/_helpers.py`) is deferred to Slice+1; doing it now would re-amend Plan 11's already-committed `fc0894a` for a refactor with zero behavioral impact.

**Trade-off accepted:** `_helpers.py` is no longer a pure-assertion module; it now also contains a driver (an action helper that performs HTTP I/O). The file's module docstring renames itself "Assertion + driver utilities" to reflect this. A purist would split `_drivers.py` from `_helpers.py`; we'd accept that refactor at Slice+1 if `_helpers.py` grows further. Cross-plan helper consolidation also Slice+1 (per the bullets above).

### DECIDE-W: `docker-compose.e2e.yml` uses Pattern Y (single shared image + per-service `CODEORACLE_VARIANT` env override)

**Decision (added 2026-05-26 amendment #49):** The slice-acceptance overlay defines a single YAML build anchor (`x-codeoracle-build:` → `image: codeoracle:slice-e2e` + `build.context: ../..` + `build.dockerfile: infra/docker/Dockerfile`) consumed by all 8 services via `<<: *codeoracle-build`. Per-service variant dispatch happens at runtime via `environment.CODEORACLE_VARIANT` (e.g., `CODEORACLE_VARIANT: api` for the API service, `CODEORACLE_VARIANT: workers.joern_indexer` for the Joern consumer). Compose's `environment:` directive overrides the Dockerfile's `ENV CODEORACLE_VARIANT=cli` default at container start; Plan 1A's `entrypoint.sh` then expands `exec python -m "codeoracle.${CODEORACLE_VARIANT}" "$@"` against the env var. Result: ONE image build per session serves all 8 slice services.

**Alternative considered (Pattern X — per-variant images via `args: VARIANT: <name>`):** Eight separate `build:` blocks, each declaring `args: VARIANT: <variant>`. Each builds a distinct image tag (`codeoracle-api:slice-e2e`, `codeoracle-mcp:slice-e2e`, ..., `codeoracle-workers-reindex-orchestrator:slice-e2e`). The Dockerfile's `ENV CODEORACLE_VARIANT=${VARIANT}` line bakes the variant value into the image at build time; no compose `environment:` override needed. Each image is self-describing under standalone `docker run`. This mirrors the per-deployment image-tag convention used by Kubernetes/Helm production charts.

**Why recommendation wins:**

1. **Build amortization.** The Dockerfile's `RUN uv sync` step (Plan 1A Task 8) + `COPY src/ /app/src` step are identical across variants — only the `ENV` value differs. Pattern X invokes the builder 8 times (Docker's layer cache deduplicates the work, but the orchestration overhead is still 8 build-context-tar uploads + 8 image-tag commits). Pattern Y builds the image once. On a cold CI runner the difference is ~80s vs ~10s wall-clock for the build phase alone.

2. **Tag-surface minimization.** Pattern X creates 8 image tags that the local Docker registry tracks. Pattern Y has one tag. Easier to inspect (`docker images | grep codeoracle`), easier to `docker rmi` between runs, easier for Plan 1A's Trivy/Grype scan to certify the image used by all services in one scan pass.

3. **Source-of-truth respected.** Plan 1A Task 8's entrypoint.sh IS the dispatcher. Pattern Y uses it as designed (env-driven, runtime dispatch). Pattern X bakes the dispatch decision into the image build, which technically works but ignores the entrypoint.sh contract — every Pattern-X image is a thin wrapper around an env value that entrypoint.sh would otherwise read at runtime.

4. **Pattern X's strongest claim isn't load-bearing for the slice.** "The image self-describes under standalone `docker run`" only matters when something other than `docker compose` brings the container up — production Helm charts, ad-hoc local debugging, etc. The slice-acceptance ceremony always uses compose, where `environment.CODEORACLE_VARIANT` is set. Production Helm work in Slice+1 + Plan 14 can choose Pattern X (per-deployment image tag) without re-litigating this decision.

**Trade-off accepted:** A reader of `docker-compose.e2e.yml` must know that `environment.CODEORACLE_VARIANT` is the variant selector — Pattern Y is less self-describing in the compose file than Pattern X's `args: VARIANT: <name>`. Mitigation: the file-header docstring explicitly names the convention and cites Plan 1A's entrypoint.sh dispatch contract. The same docstring also calls out Pattern Y as the chosen approach by reference to this DECIDE-W. Future engineers reading the overlay see the convention named at the top of the file before they see any service.

**Pre-#49 (broken) state:** The initial Plan 13 draft (`fd4945b`) named four non-existent per-variant Dockerfiles (`Dockerfile.api`, `Dockerfile.mcp`, `Dockerfile.agent`, `Dockerfile.worker`) — none of those paths are created by any plan in the corpus; the only Dockerfile is Plan 1A's unified `infra/docker/Dockerfile`. The pre-#49 draft also defined a single generic `worker:` service that could not host the 5 PID-1 consumer processes the worker-framework contract requires. The §6 EDIT row L2912 documented this as KNOWN-PRE-EXISTING with disposition "follow-up amendment"; #49 IS that amendment.

---

## Task 0: Package layout — empty `__init__.py`

**Files:**
- Create: `tests/integration/slice/__init__.py` (empty file)

**Goal:** Mark `tests/integration/slice/` as a Python package so pytest discovery + `_helpers.py` relative imports work.

- [ ] **Step 1: Create the empty `__init__.py`**

```bash
mkdir -p tests/integration/slice
touch tests/integration/slice/__init__.py
```

- [ ] **Step 2: Verify pytest discovers the package**

```bash
uv run pytest tests/integration/slice/ --collect-only 2>&1 | head -5
```

Expected: `no tests ran` (no test files yet) — but no `ImportError`.

- [ ] **Step 3: Commit**

```bash
git add tests/integration/slice/__init__.py
git commit -m "feat(slice): scaffold tests/integration/slice package

Empty __init__.py to make the slice E2E tree a Python package. Tasks 2-9 land the conftest + the 9 integration tests inside this package.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

## Task 1: `docker-compose.e2e.yml` — application-process overlay

**Files:**
- Create: `infra/compose/docker-compose.e2e.yml` (~160 lines — Compose v2 overlay on top of Plan 1A's base)

**Goal:** Compose-overlay file that brings up `codeoracle-api` (port 8080), `codeoracle-mcp` (port 8082), `codeoracle-agent` (port 8081), and the worker container, all wired to Plan 1A's base compose (Postgres, Valkey, Sourcebot, Tempo, Prometheus, OTel collector). Mounts `src/codeoracle/` read-only into every container as the deterministic test corpus per §8.3. Sets `CODEORACLE_LLM_PROVIDER=mock` everywhere so the slice runs against `MockLLMProvider` + `tests/fixtures/llm_scripts/`.

- [ ] **Step 1: Read Plan 1A's base compose for the service names + network**

```bash
grep -nE "^  [a-z_]+:" infra/compose/docker-compose.yml | head -20
```

Expected: lists the base services (e.g., `postgres:`, `valkey:`, `sourcebot:`, `tempo:`, `prometheus:`, `otel_collector:`). The overlay references these by name on the same default network.

- [ ] **Step 2: Write `infra/compose/docker-compose.e2e.yml`**

```yaml
# infra/compose/docker-compose.e2e.yml
#
# E2E application-process overlay — extends infra/compose/docker-compose.yml.
# Run as:
#
#   docker compose -f infra/compose/docker-compose.yml \
#                  -f infra/compose/docker-compose.e2e.yml \
#                  up -d
#
# Brings up 8 application/worker services consuming Plan 1A's unified
# Dockerfile + entrypoint.sh dispatch alongside the base-compose services:
#
#   Application processes (3) — api (8080), mcp (8082), agent (8081)
#   Queue consumers (5) — joern_indexer, scip_indexer, opengrep_runner,
#       embed_indexer, reindex_orchestrator (one PID-1 per consumer
#       per the worker-framework contract; Plan 5 Task 13a amendment
#       40885bf added reindex_orchestrator as a NEW required process).
#
# All 8 services share ONE image (codeoracle:slice-e2e) built from
# infra/docker/Dockerfile via the x-codeoracle-build anchor below; per-
# service variant dispatch happens at runtime via
# environment.CODEORACLE_VARIANT overriding the Dockerfile's
# ENV CODEORACLE_VARIANT default (compose `environment:` takes precedence
# over image `ENV` at container start). Plan 1A's entrypoint.sh then
# expands `exec python -m "codeoracle.${CODEORACLE_VARIANT}" "$@"` against the
# env var, so any dotted module path under codeoracle/ can be the PID-1
# process (api / agent / mcp / workers.joern_indexer / etc.). This is
# "Pattern Y" per Plan 13 DECIDE-W.
#
# Source tree is bind-mounted read-only into every container as the
# deterministic slice test corpus per spec §8.3.
#
# All processes are configured to use MockLLMProvider via env so the
# slice runs deterministically without external API keys. The
# fixtures live at tests/fixtures/llm_scripts/ (see Plan 4 Task 7).

x-codeoracle-build: &codeoracle-build
  image: codeoracle:slice-e2e
  build:
    context: ../..
    dockerfile: infra/docker/Dockerfile

# Shared worker liveness probe. Queue consumers don't expose HTTP, and
# the slim Python base (python:3.14-slim-bookworm — Plan 1A Dockerfile
# L1401) omits procps so `pgrep` is unavailable. Probe instead verifies
# PID 1 is python via /proc/1/comm — entrypoint.sh execs
# `python -m codeoracle.<variant>` so PID 1 IS the worker process. Grep
# prefix-matches python|python3|python3.14 (slim-bookworm symlink
# resolution varies). The probe catches misconfigured-entrypoint cases;
# crash/exit cases are caught by compose's container-state monitor
# (container `exited`, not `unhealthy`).
x-worker-healthcheck: &worker-hc
  test: ["CMD-SHELL", "test -f /proc/1/comm && grep -q '^python' /proc/1/comm"]
  interval: 5s
  timeout: 3s
  retries: 10
  start_period: 15s

# Shared Valkey queue-client env, merged into every queue-touching service via
# `<<: *valkey-env`. ValkeyConfig requires all six fields (no defaults — it fails
# fast at process start, never silently at first publish), so a service that sets
# only CODEORACLE_VALKEY__URL would crash on construction; the anchor supplies the full
# set in one place. STREAM_MAXLEN must exceed a single reindex's peak unprocessed
# backlog (= the fixture's file count); held at the field-doc headroom value so
# approximate trimming can never evict a never-delivered entry and hang the barrier.
x-valkey-env: &valkey-env
  CODEORACLE_VALKEY__URL: redis://valkey:6379/0
  CODEORACLE_VALKEY__POOL_MIN: "1"
  CODEORACLE_VALKEY__POOL_MAX: "10"
  CODEORACLE_VALKEY__BLOCK_MS_DEFAULT: "5000"
  CODEORACLE_VALKEY__MAX_IN_FLIGHT_PER_CONSUMER: "10"
  CODEORACLE_VALKEY__STREAM_MAXLEN: "1000000"

services:

  api:
    <<: *codeoracle-build
    depends_on:
      postgres:
        condition: service_healthy
      valkey:
        condition: service_healthy
      agent:
        condition: service_healthy
    environment:
      CODEORACLE_VARIANT: api
      CODEORACLE_DB__URL: postgresql+asyncpg://codeoracle:codeoracle@postgres:5432/codeoracle
      <<: *valkey-env
      CODEORACLE_LLM_PROVIDER: mock
      CODEORACLE_LLM_MOCK__SCRIPTS_DIR: /workspace/tests/fixtures/llm_scripts
      CODEORACLE_AGENT__BASE_URL: http://agent:8081
      CODEORACLE_REPO_ROOT: /workspace
      CODEORACLE_API__HOST: 0.0.0.0
      CODEORACLE_API__PORT: "8080"
      CODEORACLE_OTEL_EXPORTER_OTLP_ENDPOINT: http://otel_collector:4318
      CODEORACLE_OTEL_SERVICE_NAME: codeoracle-api
    volumes:
      - ../..:/workspace:ro
      - ../../tests/fixtures/llm_scripts:/workspace/tests/fixtures/llm_scripts:ro
    ports:
      - "8080:8080"
    healthcheck:
      test: ["CMD", "curl", "-fsS", "http://localhost:8080/v1/health"]
      interval: 5s
      timeout: 3s
      retries: 10
      start_period: 15s

  mcp:
    <<: *codeoracle-build
    depends_on:
      postgres:
        condition: service_healthy
      valkey:
        condition: service_healthy
      agent:
        condition: service_healthy
    environment:
      CODEORACLE_VARIANT: mcp
      CODEORACLE_DB__URL: postgresql+asyncpg://codeoracle:codeoracle@postgres:5432/codeoracle
      <<: *valkey-env
      CODEORACLE_LLM_PROVIDER: mock
      CODEORACLE_LLM_MOCK__SCRIPTS_DIR: /workspace/tests/fixtures/llm_scripts
      CODEORACLE_AGENT__BASE_URL: http://agent:8081
      CODEORACLE_REPO_ROOT: /workspace
      CODEORACLE_MCP__HOST: 0.0.0.0
      CODEORACLE_MCP__PORT: "8082"
      CODEORACLE_OTEL_EXPORTER_OTLP_ENDPOINT: http://otel_collector:4318
      CODEORACLE_OTEL_SERVICE_NAME: codeoracle-mcp
    # entrypoint.sh prefixes `python -m codeoracle.mcp`; `command:` is the argv tail.
    # Result PID 1: `python -m codeoracle.mcp --http` (Plan 12 L165 streamable-HTTP transport).
    command: ["--http"]
    volumes:
      - ../..:/workspace:ro
      - ../../tests/fixtures/llm_scripts:/workspace/tests/fixtures/llm_scripts:ro
    ports:
      - "8082:8082"
    healthcheck:
      # POST to /mcp with the SDK's initialize handshake; a 200 means MCP is reachable.
      test: ["CMD-SHELL", "curl -fsS -X POST -H 'Content-Type: application/json' --data '{\"jsonrpc\":\"2.0\",\"id\":0,\"method\":\"initialize\",\"params\":{\"protocolVersion\":\"2024-11-05\",\"capabilities\":{},\"clientInfo\":{\"name\":\"healthcheck\",\"version\":\"0\"}}}' http://localhost:8082/mcp"]
      interval: 5s
      timeout: 3s
      retries: 10
      start_period: 20s

  agent:
    <<: *codeoracle-build
    depends_on:
      postgres:
        condition: service_healthy
      sourcebot:
        condition: service_healthy
    environment:
      CODEORACLE_VARIANT: agent
      CODEORACLE_DB__URL: postgresql+asyncpg://codeoracle:codeoracle@postgres:5432/codeoracle
      CODEORACLE_LLM_PROVIDER: mock
      CODEORACLE_LLM_MOCK__SCRIPTS_DIR: /workspace/tests/fixtures/llm_scripts
      CODEORACLE_SOURCEBOT__BASE_URL: http://sourcebot:8080
      CODEORACLE_REPO_ROOT: /workspace
      CODEORACLE_AGENT__HOST: 0.0.0.0
      CODEORACLE_AGENT__PORT: "8081"
      CODEORACLE_OTEL_EXPORTER_OTLP_ENDPOINT: http://otel_collector:4318
      CODEORACLE_OTEL_SERVICE_NAME: codeoracle-agent
    volumes:
      - ../..:/workspace:ro
      - ../../tests/fixtures/llm_scripts:/workspace/tests/fixtures/llm_scripts:ro
    ports:
      - "8081:8081"
    healthcheck:
      test: ["CMD", "curl", "-fsS", "http://localhost:8081/v1/health"]
      interval: 5s
      timeout: 3s
      retries: 10
      start_period: 15s

  # Per-extractor queue consumer (Plan 5 Task 7 — joern_indexer)
  joern_indexer:
    <<: *codeoracle-build
    depends_on:
      postgres:
        condition: service_healthy
      valkey:
        condition: service_healthy
    environment:
      CODEORACLE_VARIANT: workers.joern_indexer
      CODEORACLE_DB__URL: postgresql+asyncpg://codeoracle:codeoracle@postgres:5432/codeoracle
      <<: *valkey-env
      CODEORACLE_LLM_PROVIDER: mock
      CODEORACLE_LLM_MOCK__SCRIPTS_DIR: /workspace/tests/fixtures/llm_scripts
      CODEORACLE_REPO_ROOT: /workspace
      CODEORACLE_OTEL_EXPORTER_OTLP_ENDPOINT: http://otel_collector:4318
      CODEORACLE_OTEL_SERVICE_NAME: codeoracle-joern-indexer
    volumes:
      - ../..:/workspace:ro
      - ../../tests/fixtures/llm_scripts:/workspace/tests/fixtures/llm_scripts:ro
    healthcheck: *worker-hc

  # Per-extractor queue consumer (Plan 6 Task 4 — scip_indexer)
  scip_indexer:
    <<: *codeoracle-build
    depends_on:
      postgres:
        condition: service_healthy
      valkey:
        condition: service_healthy
    environment:
      CODEORACLE_VARIANT: workers.scip_indexer
      CODEORACLE_DB__URL: postgresql+asyncpg://codeoracle:codeoracle@postgres:5432/codeoracle
      <<: *valkey-env
      CODEORACLE_LLM_PROVIDER: mock
      CODEORACLE_LLM_MOCK__SCRIPTS_DIR: /workspace/tests/fixtures/llm_scripts
      CODEORACLE_REPO_ROOT: /workspace
      CODEORACLE_OTEL_EXPORTER_OTLP_ENDPOINT: http://otel_collector:4318
      CODEORACLE_OTEL_SERVICE_NAME: codeoracle-scip-indexer
    volumes:
      - ../..:/workspace:ro
      - ../../tests/fixtures/llm_scripts:/workspace/tests/fixtures/llm_scripts:ro
    healthcheck: *worker-hc

  # Per-extractor queue consumer (Plan 6 Task 5 — opengrep_runner)
  opengrep_runner:
    <<: *codeoracle-build
    depends_on:
      postgres:
        condition: service_healthy
      valkey:
        condition: service_healthy
    environment:
      CODEORACLE_VARIANT: workers.opengrep_runner
      CODEORACLE_DB__URL: postgresql+asyncpg://codeoracle:codeoracle@postgres:5432/codeoracle
      <<: *valkey-env
      CODEORACLE_LLM_PROVIDER: mock
      CODEORACLE_LLM_MOCK__SCRIPTS_DIR: /workspace/tests/fixtures/llm_scripts
      CODEORACLE_REPO_ROOT: /workspace
      CODEORACLE_OTEL_EXPORTER_OTLP_ENDPOINT: http://otel_collector:4318
      CODEORACLE_OTEL_SERVICE_NAME: codeoracle-opengrep-runner
    volumes:
      - ../..:/workspace:ro
      - ../../tests/fixtures/llm_scripts:/workspace/tests/fixtures/llm_scripts:ro
    healthcheck: *worker-hc

  # Per-extractor queue consumer (Plan 6 Task 6 — embed_indexer)
  embed_indexer:
    <<: *codeoracle-build
    depends_on:
      postgres:
        condition: service_healthy
      valkey:
        condition: service_healthy
    environment:
      CODEORACLE_VARIANT: workers.embed_indexer
      CODEORACLE_DB__URL: postgresql+asyncpg://codeoracle:codeoracle@postgres:5432/codeoracle
      <<: *valkey-env
      CODEORACLE_LLM_PROVIDER: mock
      CODEORACLE_LLM_MOCK__SCRIPTS_DIR: /workspace/tests/fixtures/llm_scripts
      CODEORACLE_REPO_ROOT: /workspace
      CODEORACLE_OTEL_EXPORTER_OTLP_ENDPOINT: http://otel_collector:4318
      CODEORACLE_OTEL_SERVICE_NAME: codeoracle-embed-indexer
    volumes:
      - ../..:/workspace:ro
      - ../../tests/fixtures/llm_scripts:/workspace/tests/fixtures/llm_scripts:ro
    healthcheck: *worker-hc

  # Reindex orchestrator (Plan 5 Task 13a amendment 40885bf) — holds
  # pg_advisory_lock(0x46AE0001); dispatches per-extractor reindex work
  # to the 4 consumers above via Valkey XADD. MUST start AFTER all 4
  # extractor consumers are healthy or the XADD will succeed but the
  # queue won't drain. Drives the Plan 11 sync→async POST /v1/reindex
  # contract (fc0894a) — the API process enqueues; this consumer owns
  # the work.
  reindex_orchestrator:
    <<: *codeoracle-build
    depends_on:
      postgres:
        condition: service_healthy
      valkey:
        condition: service_healthy
      joern_indexer:
        condition: service_healthy
      scip_indexer:
        condition: service_healthy
      opengrep_runner:
        condition: service_healthy
      embed_indexer:
        condition: service_healthy
    environment:
      CODEORACLE_VARIANT: workers.reindex_orchestrator
      CODEORACLE_DB__URL: postgresql+asyncpg://codeoracle:codeoracle@postgres:5432/codeoracle
      <<: *valkey-env
      CODEORACLE_LLM_PROVIDER: mock
      CODEORACLE_LLM_MOCK__SCRIPTS_DIR: /workspace/tests/fixtures/llm_scripts
      CODEORACLE_REPO_ROOT: /workspace
      CODEORACLE_OTEL_EXPORTER_OTLP_ENDPOINT: http://otel_collector:4318
      CODEORACLE_OTEL_SERVICE_NAME: codeoracle-reindex-orchestrator
    volumes:
      - ../..:/workspace:ro
      - ../../tests/fixtures/llm_scripts:/workspace/tests/fixtures/llm_scripts:ro
    healthcheck: *worker-hc
```

- [ ] **Step 3: Validate the overlay parses + composes cleanly**

```bash
docker compose -f infra/compose/docker-compose.yml -f infra/compose/docker-compose.e2e.yml config > /dev/null
```

Expected: exits 0; no warnings.

- [ ] **Step 4: Bring the stack up manually (sanity check; not yet a test gate)**

```bash
docker compose -f infra/compose/docker-compose.yml -f infra/compose/docker-compose.e2e.yml up -d
docker compose -f infra/compose/docker-compose.yml -f infra/compose/docker-compose.e2e.yml ps
```

Expected: every service shows `(healthy)` after ~60s. If any service is `(unhealthy)`, inspect with `docker compose logs <service>`.

- [ ] **Step 5: Tear down**

```bash
docker compose -f infra/compose/docker-compose.yml -f infra/compose/docker-compose.e2e.yml down -v
```

- [ ] **Step 6: Commit**

```bash
git add infra/compose/docker-compose.e2e.yml
git commit -m "feat(slice): docker-compose overlay for E2E

Brings up 8 services on top of Plan 1A's base compose (Postgres, Valkey, Sourcebot, Tempo, Prometheus, OTel collector): the three application processes codeoracle-api (8080), codeoracle-mcp (8082), codeoracle-agent (8081), and five queue consumers (joern_indexer, scip_indexer, opengrep_runner, embed_indexer, reindex_orchestrator — Plan 5/6 worker namespaces; one PID-1 per consumer per the worker-framework contract). All 8 services share ONE image (codeoracle:slice-e2e) built from Plan 1A's unified infra/docker/Dockerfile via a shared x-codeoracle-build anchor; per-service variant dispatch is runtime via environment.CODEORACLE_VARIANT overriding the Dockerfile's ENV default (Plan 13 DECIDE-W \"Pattern Y\"). Worker liveness uses a shared x-worker-healthcheck anchor with a procps-free /proc/1/comm probe (slim Python base omits procps). All processes use MockLLMProvider via CODEORACLE_LLM_PROVIDER=mock for deterministic slice runs. Source tree is bind-mounted read-only into every container as the slice test corpus per spec §8.3.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

## Task 2: E2E conftest + helpers

**Files:**
- Create: `tests/integration/slice/conftest.py` (~140 lines — `pytest-docker` integration + per-test fixtures)
- Create: `tests/integration/slice/_helpers.py` (~155 lines — shared assertion utilities + reindex polling driver; amended 2026-05-26 to add `drive_reindex_to_applied` per Plan 11 sync→async pivot)

**Goal:** Land the pytest fixtures every Plan 13 integration test depends on. Three classes of fixture: (1) **session-scoped compose lifecycle** — `pytest-docker` brings up the stack at session start, waits for every service's healthcheck to pass, yields, tears down at session end; (2) **session-scoped HTTP clients** — `api_client` (httpx.AsyncClient pointed at the compose api service), `mcp_client` (the MCP SDK's streamable-HTTP client); (3) **function-scoped data cleanup** — `clean_snapshot` truncates the `snapshots` table + the AGE graph between tests so test ordering doesn't matter.

- [ ] **Step 1: Write `tests/integration/slice/_helpers.py`**

```python
# tests/integration/slice/_helpers.py
"""Assertion + driver utilities shared across the slice integration suite.

Three responsibilities:
1. `assert_query_results_equal_modulo_trace_id` — pins §8.5 gate 4
   (byte-equal QueryResult across MCP + HTTP transports modulo trace_id).
2. `extract_handle_types` — small accessor for citation handle-type sets
   used by gate 1 (≥ 2 handle types per PROVEN) and gate 9 (no
   TRACE_SPAN / DB_RECORD).
3. `drive_reindex_to_applied` — POST /v1/reindex then poll GET
   /v1/reindex/{sha} until status is terminal (APPLIED or FAILED).
   Amended 2026-05-26 per the Plan 11 sync→async pivot (POST returns
   202 + Snapshot(PENDING); polling is the wait surface). DECIDE-V
   pins the helper here so every reindex caller in Plan 13 shares the
   same polling contract (gate 5 + gates 6-8 incremental + the gate-1
   / gate-2 / gate-4 `_ensure_indexed` setup hook in Task 3).

Lives in `_helpers.py` (leading underscore) so pytest treats it as a
private module, not a test file.
"""

from __future__ import annotations

import asyncio
import json
import time
from typing import Any

import httpx

from codeoracle.models.query import QueryResult
from codeoracle.models.snapshot import Snapshot, SnapshotStatus


_TRACE_ID_SENTINEL = "<TRACE_ID_REWRITTEN_FOR_PARITY>"

# Reindex polling budget (matches spec §8.5 cold-3K-LOC p95 target).
# The full reindex of src/codeoracle/ inside the compose stack takes
# ~60-90s wall-clock against the 4-extractor pipeline (joern + scip +
# opengrep + embed); the budget includes the queue-XADD → consumer-XREADGROUP
# round-trip plus the indexing time itself. Tightening this below
# the spec target would surface as a flaky test on cold CI runners; the
# §8.5 perf gate emits the actual observation to perf-report.json.
_REINDEX_POLL_INTERVAL_SECONDS = 1.0
_REINDEX_POLL_BUDGET_SECONDS = 180.0


def assert_query_results_equal_modulo_trace_id(
    *, api_result: QueryResult, mcp_result: QueryResult
) -> None:
    """Assert two QueryResult instances are byte-equal modulo trace_id.

    Both results are serialised via model_dump(mode='json'),
    trace_id is rewritten to a fixed sentinel on both sides, and the
    resulting dicts are compared via canonical JSON (sorted keys, no
    whitespace).

    Raises AssertionError with a unified-diff on mismatch.
    """
    api_dict = api_result.model_dump(mode="json")
    mcp_dict = mcp_result.model_dump(mode="json")
    api_dict["trace_id"] = _TRACE_ID_SENTINEL
    mcp_dict["trace_id"] = _TRACE_ID_SENTINEL

    api_json = json.dumps(api_dict, sort_keys=True, separators=(",", ":"))
    mcp_json = json.dumps(mcp_dict, sort_keys=True, separators=(",", ":"))

    if api_json != mcp_json:
        # Surface a diff that names the differing field for fast debug.
        for key in sorted(set(api_dict) | set(mcp_dict)):
            if api_dict.get(key) != mcp_dict.get(key):
                msg = (
                    f"§8.5 gate 4: QueryResult divergence at field {key!r}\n"
                    f"  api:  {json.dumps(api_dict.get(key), sort_keys=True)[:300]}\n"
                    f"  mcp:  {json.dumps(mcp_dict.get(key), sort_keys=True)[:300]}\n"
                )
                raise AssertionError(msg)
        # Fallback if structural sets matched but JSON didn't (shouldn't happen).
        raise AssertionError("QueryResult JSON serialisations diverge.")


def extract_handle_types(result: QueryResult) -> set[str]:
    """Return the set of handle-type names across all citations.

    `result.claim` is non-None only when `verdict.status == "PROVEN"` or
    "REFUTED"; for UNKNOWN there are no citations to extract from.
    """
    if result.claim is None:
        return set()
    return {c.handle.type for c in result.claim.citations}


async def drive_reindex_to_applied(
    api_client: httpx.AsyncClient, *, base_sha: str | None
) -> Snapshot:
    """POST /v1/reindex then poll GET /v1/reindex/{sha} until terminal.

    Amended 2026-05-26 per Plan 11 sync→async pivot:
    - POST returns 202 + Snapshot(status=PENDING|APPLYING)
    - GET polls until status is APPLIED (success) or FAILED (raise)
    - Budget is `_REINDEX_POLL_BUDGET_SECONDS` (180s, spec §8.5 cold p95)
    - Interval is `_REINDEX_POLL_INTERVAL_SECONDS` (1.0s)

    The producer (POST) is idempotent on retry per Plan 11 invariant 4
    (post-amendment): duplicate POST against the same effective tree
    returns the existing PENDING/APPLYING/APPLIED row instead of
    XADDing a second enqueue. Callers that wish to drive a fresh
    reindex MUST mutate the tree first (or rely on `clean_snapshot`).

    Raises:
        AssertionError: budget exceeded before terminal status reached.
        AssertionError: terminal status is FAILED (carries error message).
    """
    post_response = await api_client.post(
        "/v1/reindex", json={"base_sha": base_sha}
    )
    assert post_response.status_code == 202, (
        f"POST /v1/reindex expected 202, got "
        f"{post_response.status_code} {post_response.text}"
    )
    enqueued = Snapshot.model_validate(post_response.json())
    assert enqueued.status in {
        SnapshotStatus.PENDING,
        SnapshotStatus.APPLYING,
        SnapshotStatus.APPLIED,
    }, (
        f"POST /v1/reindex returned unexpected status "
        f"{enqueued.status!r} (expected PENDING|APPLYING|APPLIED)"
    )
    snapshot_sha = enqueued.sha

    deadline = time.monotonic() + _REINDEX_POLL_BUDGET_SECONDS
    final: Snapshot | None = None
    while time.monotonic() < deadline:
        get_response = await api_client.get(f"/v1/reindex/{snapshot_sha}")
        assert get_response.status_code == 200, (
            f"GET /v1/reindex/{snapshot_sha} expected 200, got "
            f"{get_response.status_code} {get_response.text}"
        )
        current = Snapshot.model_validate(get_response.json())
        if current.status in {SnapshotStatus.APPLIED, SnapshotStatus.FAILED}:
            final = current
            break
        await asyncio.sleep(_REINDEX_POLL_INTERVAL_SECONDS)

    assert final is not None, (
        f"Reindex {snapshot_sha} did not reach terminal status within "
        f"{_REINDEX_POLL_BUDGET_SECONDS}s budget"
    )
    if final.status == SnapshotStatus.FAILED:
        raise AssertionError(
            f"Reindex {snapshot_sha} terminated FAILED: {final.error!r}"
        )
    return final
```

- [ ] **Step 2: Write `tests/integration/slice/conftest.py`**

```python
# tests/integration/slice/conftest.py
"""Session-scoped fixtures for the slice integration suite.

Four layers:

1. `pytest-docker` plugin owns compose lifecycle. The plugin reads
   `docker_compose_file` / `docker_compose_command` to find the overlay
   and brings the stack up at session start; tears down at session end.
2. `compose_stack` session-scoped fixture yields once every service's
   healthcheck has passed (`pytest-docker.wait_until_responsive`).
3. `api_client` (httpx.AsyncClient @ host:8080) + `mcp_base_url`
   (URL string for the MCP SDK client) — both pinned at the overlay's
   published host ports.
4. `db_conn` session-scoped `asyncpg.Connection` bound to the compose
   Postgres at host port 5432. `clean_snapshot` function-scoped fixture
   uses it to TRUNCATE the `snapshots`, `claims`, `citations`, and
   `traces` tables + AGE graph state between tests. Per DECIDE-U the cleanup
   path is direct DB I/O, NOT an admin HTTP endpoint — admin routes
   would violate invariant 1 and create silent-skip risk in CI.

The host ports (8080 / 8082 / 5432) are stable per the overlay —
`pytest-docker`'s `get_docker_services` fixture exposes container IPs,
but the overlay publishes fixed host ports for simplicity (slice scope;
no concurrent test runs on the same host).
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from pathlib import Path

import asyncpg
import httpx
import pytest
import pytest_asyncio

from codeoracle.core.config import Settings, get_settings


_REPO_ROOT = Path(__file__).resolve().parents[3]
_API_BASE_URL = "http://127.0.0.1:8080"
_MCP_BASE_URL = "http://127.0.0.1:8082"

# Compose Postgres is published on host port 5432 by the base compose
# (Plan 2). The credentials are the dev defaults the overlay sets in
# the api/mcp/agent/worker env (CODEORACLE_DB__URL=postgresql+asyncpg://codeoracle:
# codeoracle@postgres:5432/codeoracle).
_DB_HOST = "127.0.0.1"
_DB_PORT = 5432
_DB_USER = "codeoracle"
_DB_PASS = "codeoracle"
_DB_NAME = "codeoracle"


@pytest.fixture(scope="session")
def docker_compose_file() -> list[str]:
    """`pytest-docker` reads this to find the compose stack(s) to bring up."""
    return [
        str(_REPO_ROOT / "infra" / "compose" / "docker-compose.yml"),
        str(_REPO_ROOT / "infra" / "compose" / "docker-compose.e2e.yml"),
    ]


@pytest.fixture(scope="session")
def docker_compose_project_name() -> str:
    """Stable project name so logs are inspectable post-run."""
    return "codeoracle-slice-e2e"


@pytest.fixture(scope="session")
def compose_stack(docker_services) -> Iterator[None]:
    """Yield once every service is responsive.

    `docker_services` is the `pytest-docker` fixture that brings up the
    stack. We add per-service health probes here (in addition to the
    docker-level healthchecks in the overlay) so the test session never
    yields against a half-ready stack.
    """
    docker_services.wait_until_responsive(
        timeout=180,
        pause=2,
        check=_all_endpoints_healthy,
    )
    yield


def _all_endpoints_healthy() -> bool:
    """Check all three host-side service surfaces are reachable."""
    try:
        with httpx.Client(timeout=2.0) as c:
            api_ok = c.get(f"{_API_BASE_URL}/v1/health").status_code == 200
        return api_ok and _mcp_alive() and _db_alive()
    except (httpx.HTTPError, OSError):
        return False


def _mcp_alive() -> bool:
    """Send an MCP `initialize` handshake; 200 means MCP is reachable."""
    body = {
        "jsonrpc": "2.0",
        "id": 0,
        "method": "initialize",
        "params": {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "healthcheck", "version": "0"},
        },
    }
    try:
        r = httpx.post(
            f"{_MCP_BASE_URL}/mcp",
            json=body,
            headers={"Content-Type": "application/json"},
            timeout=3.0,
        )
        return r.status_code == 200
    except (httpx.HTTPError, OSError):
        return False


def _db_alive() -> bool:
    """Probe Postgres host-port; succeeds when the DB accepts a connect."""
    import socket
    try:
        with socket.create_connection((_DB_HOST, _DB_PORT), timeout=2.0):
            return True
    except OSError:
        return False


@pytest_asyncio.fixture(scope="session", loop_scope="session")
async def api_client(compose_stack: None) -> AsyncIterator[httpx.AsyncClient]:
    """httpx.AsyncClient bound to the compose API service.

    `loop_scope="session"` is REQUIRED — pytest-asyncio's default fixture
    loop scope is `function`, so a session-scoped async fixture without
    explicit `loop_scope` would bind the httpx client to function-1's
    event loop and then raise
    `RuntimeError: got Future <...> attached to a different loop`
    on every subsequent function's first call. Requires pytest-asyncio
    >= 0.24 (the version that introduced `loop_scope=`); pinned in
    Task 14.
    """
    async with httpx.AsyncClient(base_url=_API_BASE_URL, timeout=60.0) as client:
        yield client


@pytest.fixture(scope="session")
def mcp_base_url(compose_stack: None) -> str:
    """Base URL for the MCP streamable-HTTP endpoint. Tests using the MCP SDK
    pass this into `streamablehttp_client(url)` directly per the SDK pattern.
    """
    return f"{_MCP_BASE_URL}/mcp"


@pytest_asyncio.fixture(scope="session", loop_scope="session")
async def db_conn(compose_stack: None) -> AsyncIterator[asyncpg.Connection]:
    """Session-scoped asyncpg connection to the compose Postgres.

    Used by clean_snapshot for TRUNCATE between tests and by the
    incremental tests for direct graph-state introspection. Bound to
    the host-published port 5432.

    Fails hard if the connection cannot be established — silent-skip
    here would let CI green a vacuous test run (per advisor finding).

    `loop_scope="session"` is REQUIRED for the same reason api_client
    needs it (see that fixture's docstring). An asyncpg connection is
    bound to the loop on which it was opened; if the loop scope is
    function-default, every test after the first sees
    `RuntimeError: got Future <...> attached to a different loop`.
    """
    conn = await asyncpg.connect(
        host=_DB_HOST,
        port=_DB_PORT,
        user=_DB_USER,
        password=_DB_PASS,
        database=_DB_NAME,
    )
    try:
        yield conn
    finally:
        await conn.close()


# Tables truncated between tests. Lifted from Plan 3 migrations 0001
# (snapshots), 0002 (embeddings), 0003 (traces). Schema changes touch
# this single tuple — keep it in sync with the latest migration head.
_TRUNCATABLE_TABLES: tuple[str, ...] = (
    "snapshots",
    "snapshot_file_outcomes",
    "claims",
    "citations",
    "traces",
    "embeddings",
)


@pytest.fixture
async def clean_snapshot(db_conn: asyncpg.Connection) -> None:
    """Truncate the `snapshots`, `claims`, `citations`, and `traces`
    tables + AGE graph state between tests.

    Implemented via direct asyncpg (NOT via an admin HTTP endpoint —
    see DECIDE-U). Any connection or truncate failure is HARD-FAIL,
    never silent-skip: a vacuous test run that signs off the slice
    green is the worst possible CI outcome.

    Truncates BEFORE the test yields (so each test starts clean); the
    next test's clean_snapshot resets again. No after-yield cleanup
    needed.
    """
    # TRUNCATE the catalogue tables.
    table_list = ", ".join(_TRUNCATABLE_TABLES)
    await db_conn.execute(f"TRUNCATE {table_list} RESTART IDENTITY CASCADE")

    # Drop & recreate the AGE graph so vertex/edge tables are empty.
    # AGE persists graph state in dynamically-named tables under the
    # ag_label_vertex / ag_label_edge prefix per `codeoracle` graph; the
    # safest reset is to drop_graph + create_graph the whole graph.
    await db_conn.execute(
        "SELECT * FROM ag_catalog.drop_graph('codeoracle', true)"
    )
    await db_conn.execute(
        "SELECT * FROM ag_catalog.create_graph('codeoracle')"
    )


@pytest.fixture(scope="session")
def settings() -> Settings:
    """Test-side Settings (NOT the same Settings the compose containers use).

    Used by tests that need to read configuration values for assertion-time
    decisions (e.g. polling-budget alignment with `_REINDEX_POLL_BUDGET_SECONDS`
    in `_helpers.py`, or stack-side defaults during ceremony recording).
    """
    return get_settings()
```

- [ ] **Step 3: Run pytest collection against the new fixtures (no tests yet — just verify collection)**

```bash
uv run pytest tests/integration/slice/ --collect-only -q 2>&1 | tail -10
```

Expected: `no tests collected` or `2 errors during collection` for the missing test files. No `ImportError` on the conftest itself.

- [ ] **Step 4: Lint + type check the new files**

```bash
uv run ruff check tests/integration/slice/conftest.py tests/integration/slice/_helpers.py
uv run ty check tests/integration/slice/conftest.py tests/integration/slice/_helpers.py
```

Expected: zero findings.

- [ ] **Step 5: Commit**

```bash
git add tests/integration/slice/conftest.py tests/integration/slice/_helpers.py
git commit -m "feat(slice): conftest + helpers for E2E integration suite

pytest-docker drives compose lifecycle (overlay = base + E2E). Session-scoped api_client + mcp_base_url + db_conn fixtures pin the published host ports (API:8080, MCP:8082, Postgres:5432). Function-scoped clean_snapshot fixture truncates between tests via direct asyncpg (see DECIDE-U) — admin HTTP endpoints would have violated invariant 1 and created silent-skip CI risk.

_helpers.assert_query_results_equal_modulo_trace_id pins §8.5 gate 4 (byte-equal QueryResult across MCP + HTTP). _helpers.extract_handle_types is the per-test accessor for gate-1 (≥ 2 handle types) and gate-9 (no TRACE_SPAN / DB_RECORD).

Connection failures or TRUNCATE failures HARD-fail (never silent-skip) — a vacuous green test run is the worst possible CI outcome.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

## Task 3: §8.5 functional gate 1 — `test_explain_existing_symbol_returns_proven_with_citations`

**Files:**
- Create: `tests/integration/slice/test_slice_explain_symbol.py` (~100 lines initially — Tasks 4-6 append)
- Create: `tests/fixtures/llm_scripts/slice_explain_verifier.yaml` (~80 lines — canonical PROVEN fixture)

**Goal:** Implement the first §8.5 functional gate. Query `codeoracle.services.verifier.service.VerifierService.verify` via `POST /v1/query`; assert the response is a `QueryResult` with `verdict.status == "PROVEN"`, `len(claim.citations) >= 3`, `len(extract_handle_types(result)) >= 2`. Land the canonical `MockLLMProvider` fixture that drives this path.

- [ ] **Step 1: Write the canonical MockLLM fixture**

```yaml
# tests/fixtures/llm_scripts/slice_explain_verifier.yaml
#
# §8.5 gate 1 fixture — PROVEN path for the slice's canonical query
# (codeoracle.services.verifier.service.VerifierService.verify).
#
# Keyed by request_fingerprint(messages, tools, *, model, temperature,
# max_tokens, structured_output) per Plan 4 Task 5. The fingerprint
# captures the spec §5.7 "key parameters" + the messages + tools, so
# template-text edits that don't change the fingerprint reuse this
# fixture, but a fingerprint shift (different model, different
# structured-output schema) forces a new entry.
#
# One LLM call expected per query on the explain_symbol path: agent.compose.
# The intent node is deterministic — no LLM call (Plan 10 Deviation 2) — so the
# codified plan_handle_types for explain_symbol are fixed at {CODE_SPAN,
# DOC_PARAGRAPH, GRAPH_QUERY} in code, not chosen by the LLM; there is no
# agent.intent_parse fingerprint to key. Only compose is scripted here.

- template_id: agent.compose
  fingerprint: "PLACEHOLDER_compose_verifier_verify_proven"
  # Composed by the agent's compose node; LLM authors prose + cites by index.
  response:
    role: assistant
    content: |
      VerifierService.verify takes a Claim, resolves each citation's
      handle to its current content via HandleResolverService, and
      returns a Verdict — PROVEN if all resolve and match, UNKNOWN if
      any fail to resolve, REFUTED if any resolved-but-mismatched [1] [2] [3].
    structured_output:
      statement: |
        VerifierService.verify takes a Claim, resolves each citation's
        handle to its current content via HandleResolverService, and
        returns a Verdict — PROVEN if all resolve and match, UNKNOWN if
        any fail to resolve, REFUTED if any resolved-but-mismatched.
      cited_indices: [0, 1, 2]
      confidence: 0.88
```

**NOTE on fingerprint placeholders:** the `PLACEHOLDER_*` fingerprint values must be replaced with real `request_fingerprint(...)` outputs computed from the actual messages + tools + decoding params at slice time. Plan 4 Task 7's `MockLLMProvider` raises `MockLLMScriptMissingError` on lookup failure — this is the maintenance signal. Implementation step: after Task 1's compose stack is up, run the test once with the placeholders, capture the missing-fingerprint error message (which includes the actual fingerprint computed from the live messages), paste those into the YAML, re-run. See `tests/fixtures/llm_scripts/README.md` for the full fixture-recording recipe.

- [ ] **Step 2: Write the failing test**

```python
# tests/integration/slice/test_slice_explain_symbol.py
"""§8.5 functional gates 1, 2, 4 — explain_symbol integration tests.

Every test in this file runs against a live compose stack (see conftest).
This file covers three of the §8.5 functional gates:

  1. test_explain_existing_symbol_returns_proven_with_citations  (gate 1)
  2. test_explain_deleted_symbol_returns_unknown                 (gate 2)
  3. test_mcp_explain_symbol_matches_api_response                (gate 4)

§8.5 gate 3 (REFUTED) lives in the sibling file
test_slice_verifier_refuted.py — that test is verifier-integration only
(no API/agent layer; constructs a Citation with a mismatched excerpt
and calls VerifierService.verify directly) because the REFUTED branch
is architecturally unreachable through the API/agent flow by Plan 10
Invariant 1 + Plan 9's contract. See Task 5 prose + DECIDE-U for the
full reasoning.

§8.5 gate 5 (test_reindex_endpoint_produces_new_snapshot) lives in
test_slice_reindex.py and the §8.5 incremental gates (6-8) in
test_slice_incremental.py.

All tests in this file use the `clean_snapshot` fixture so each starts
from a fresh DB + freshly-indexed src/codeoracle/ tree.
"""

from __future__ import annotations

import httpx
import pytest

from codeoracle.models.evidence import VerdictStatus
from codeoracle.models.query import Query, QueryResult
from codeoracle.models.snapshot import SnapshotStatus

from ._helpers import (
    assert_query_results_equal_modulo_trace_id,
    drive_reindex_to_applied,
    extract_handle_types,
)


pytestmark = [pytest.mark.slice, pytest.mark.asyncio]


CANONICAL_PROVEN_SYMBOL = "codeoracle.services.verifier.service.VerifierService.verify"


async def _post_query_and_parse(
    api_client: httpx.AsyncClient, payload: dict[str, object]
) -> QueryResult:
    """POST a Query body to /v1/query; assert 200; parse into QueryResult."""
    response = await api_client.post("/v1/query", json=payload)
    assert response.status_code == 200, (
        f"POST /v1/query failed: {response.status_code} {response.text}"
    )
    return QueryResult.model_validate(response.json())


async def _ensure_indexed(api_client: httpx.AsyncClient) -> None:
    """Drive a full reindex so subsequent queries have a graph to query.

    Must run AFTER `clean_snapshot` (which truncates). Amended
    2026-05-26 per Plan 11 sync→async pivot: POST returns 202 +
    PENDING; delegates to `drive_reindex_to_applied` which polls
    GET /v1/reindex/{sha} until APPLIED. Returns once the snapshot
    is APPLIED (or raises on FAILED / budget).
    """
    snapshot = await drive_reindex_to_applied(api_client, base_sha=None)
    assert snapshot.status == SnapshotStatus.APPLIED, (
        f"Expected snapshot status APPLIED, got {snapshot.status!r}"
    )


async def test_explain_existing_symbol_returns_proven_with_citations(
    api_client: httpx.AsyncClient,
    clean_snapshot: None,
) -> None:
    """§8.5 gate 1 — PROVEN with ≥3 citations spanning ≥2 handle types."""
    await _ensure_indexed(api_client)

    result = await _post_query_and_parse(
        api_client,
        {
            "type": "explain_symbol",
            "symbol": CANONICAL_PROVEN_SYMBOL,
            "corpus": "self",
            "snapshot_sha": None,
        },
    )

    assert result.verdict.status == VerdictStatus.PROVEN, (
        f"Expected PROVEN, got {result.verdict.status}: "
        f"reason={result.verdict.reason!r}"
    )
    assert result.claim is not None, "PROVEN result must include claim"

    cite_count = len(result.claim.citations)
    handle_types = extract_handle_types(result)

    # This PROVEN path is the slice's real-CodeSpanResolver round-trip
    # coverage point (closes Plan 10's U9 cross-file note): the LLM is the
    # only mocked collaborator here — every CODE_SPAN citation's excerpt was
    # resolved end-to-end by the live CodeSpanResolver -> SourcebotClient
    # -> Sourcebot/Zoekt path and round-tripped to PROVEN by construction.
    assert cite_count >= 3, (
        f"§8.5 gate 1: expected ≥3 citations, got {cite_count}"
    )
    assert len(handle_types) >= 2, (
        f"§8.5 gate 1: expected ≥2 handle types, got "
        f"{len(handle_types)} ({handle_types!r})"
    )
```

- [ ] **Step 3: Run the test against the live compose stack; verify it passes**

```bash
docker compose -f infra/compose/docker-compose.yml -f infra/compose/docker-compose.e2e.yml up -d
uv run pytest tests/integration/slice/test_slice_explain_symbol.py::test_explain_existing_symbol_returns_proven_with_citations -v
```

Expected: PASS. If FAIL with `MockLLMScriptMissingError` listing actual fingerprints, paste the listed values back into `slice_explain_verifier.yaml` and rerun.

- [ ] **Step 4: Lint + type check**

```bash
uv run ruff check tests/integration/slice/test_slice_explain_symbol.py
uv run ty check tests/integration/slice/test_slice_explain_symbol.py
```

Expected: zero findings.

- [ ] **Step 5: Commit**

```bash
git add tests/integration/slice/test_slice_explain_symbol.py tests/fixtures/llm_scripts/slice_explain_verifier.yaml
git commit -m "feat(slice): §8.5 gate 1 — explain existing symbol returns PROVEN

Drives the canonical query (codeoracle.services.verifier.service.VerifierService.verify) through the live compose stack via POST /v1/query; asserts verdict.status == PROVEN, ≥3 citations spanning ≥2 handle types per spec §8.5 gate 1.

Bundles the canonical MockLLMProvider fixture (slice_explain_verifier.yaml) keyed by request_fingerprint per Plan 4 Task 5. One LLM call per query on the explain_symbol path (agent.compose) — the intent node is deterministic, no LLM call (Plan 10 Deviation 2), so only compose is keyed.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

## Task 4: §8.5 functional gate 2 — `test_explain_deleted_symbol_returns_unknown`

**Files:**
- Modify: `tests/integration/slice/test_slice_explain_symbol.py` (append test)
- Create: `tests/fixtures/llm_scripts/slice_explain_deleted.yaml`

**Goal:** Query a symbol that genuinely doesn't exist in `src/codeoracle/` (`codeoracle.services.deleted.NotARealService`). Assert `verdict.status == "UNKNOWN"` with `reason` matching `"symbol not found"` (case-insensitive substring).

- [ ] **Step 1: Write the MockLLM fixture for the UNKNOWN path**

```yaml
# tests/fixtures/llm_scripts/slice_explain_deleted.yaml
#
# No scripted LLM call on this path — the fixture is intentionally empty:
#   1. The intent node is deterministic (Plan 10 Deviation 2) — no LLM call,
#      so there is no agent.intent_parse fingerprint to key.
#   2. The graph-query node returns empty results for a non-existent symbol,
#      so the agent short-circuits to UNKNOWN BEFORE the compose node — the
#      lone explain_symbol LLM call (agent.compose) is never reached either.
# MockLLMProvider is therefore never consulted on the UNKNOWN path; if it
# ever were, MockLLMScriptMissingError would surface the architecture drift.
```

- [ ] **Step 2: Append the failing test**

```python
# Append to tests/integration/slice/test_slice_explain_symbol.py

async def test_explain_deleted_symbol_returns_unknown(
    api_client: httpx.AsyncClient,
    clean_snapshot: None,
) -> None:
    """§8.5 gate 2 — UNKNOWN with reason='symbol not found'."""
    await _ensure_indexed(api_client)

    result = await _post_query_and_parse(
        api_client,
        {
            "type": "explain_symbol",
            "symbol": "codeoracle.services.deleted.NotARealService",
            "corpus": "self",
            "snapshot_sha": None,
        },
    )

    assert result.verdict.status == VerdictStatus.UNKNOWN, (
        f"Expected UNKNOWN, got {result.verdict.status}"
    )
    assert "symbol not found" in result.verdict.reason.lower(), (
        f"§8.5 gate 2: expected reason matching 'symbol not found', "
        f"got {result.verdict.reason!r}"
    )
    # UNKNOWN responses must NOT include a claim (Plan 9 invariant).
    assert result.claim is None, (
        f"UNKNOWN result must not include claim, got claim={result.claim!r}"
    )
```

- [ ] **Step 3: Run, fingerprint-paste if needed, commit**

```bash
uv run pytest tests/integration/slice/test_slice_explain_symbol.py::test_explain_deleted_symbol_returns_unknown -v
```

Expected: PASS (after fingerprint paste, same as Task 3).

- [ ] **Step 4: Commit**

```bash
git add tests/integration/slice/test_slice_explain_symbol.py tests/fixtures/llm_scripts/slice_explain_deleted.yaml
git commit -m "feat(slice): §8.5 gate 2 — deleted symbol returns UNKNOWN

Drives codeoracle.services.deleted.NotARealService (a symbol that does not exist in src/codeoracle/) through the live compose stack; asserts verdict.status == UNKNOWN with reason matching 'symbol not found'. UNKNOWN responses must omit claim per Plan 9 invariant.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

## Task 5: §8.5 functional gate 3 — `test_verifier_returns_refuted_when_excerpt_mismatches_source`

**Files:**
- Create: `tests/integration/slice/test_slice_verifier_refuted.py` (~80 lines — sibling file to the explain-symbol tests; verifier-integration scope is distinct enough to keep its own file)

**Goal:** Drive the REFUTED verdict path deterministically by exercising Plan 9's `VerifierService.verify` directly against the compose-deployed Sourcebot, with a hand-constructed `Citation` whose `excerpt` is deliberately set to a string that does NOT appear at the cited handle. Plan 9's `_resolve_one` calls `HandleResolverService.resolve(handle)` → `SourcebotClient.fetch_span(...)` (HTTP to Sourcebot), compares the returned bytes to `citation.excerpt`, and emits `ResolvedCitation(resolved=True, matches_excerpt=False)` on mismatch; verdict aggregation then classifies the verdict as `REFUTED`.

**Why no host-filesystem mutation, no admin endpoints, and no `_swap_file_contents` here** (DECIDE-U + advisor round 2 finding):

1. `CodeSpanResolver` (Plan 8 Task 9 — L2284 + L2284-2330) delegates to `SourcebotClient.fetch_span()`, which is an HTTP call to Sourcebot's `/api/source`. Sourcebot serves from its own Zoekt index, not the host filesystem; host-side `src/codeoracle/` mutations do not propagate into Sourcebot's view in test-relevant time (Zoekt's filesystem-watcher reindex is deployment-dependent and intentionally non-deterministic for our slice). A host-mutation test would PROVEN-pass against stale indexed bytes — a coin flip masquerading as a gate.
2. The Plan 10 agent + Plan 9 verifier always build / re-resolve citations from `HandleResolverService.resolve()` output (Plan 10 Invariant 1 + Plan 9 L65 reasoning). Under any normal API/agent flow, `citation.excerpt` == `resolver_output` *by construction* — there is no architecturally-reachable path through the API that produces a `citation.excerpt` ≠ live resolver output. To exercise the REFUTED branch deterministically, we must construct the mismatched `Citation` ourselves and call `VerifierService.verify` directly. This is a verifier-integration test, not an end-to-end API test; that is the correct test level for this gate.
3. Admin HTTP endpoints (`/v1/_test/...`) are rejected at the slice level — see DECIDE-U.

**Why this test still belongs in the slice integration suite** (not the unit-test directory): the test exercises the *real* `VerifierService` against the *real* `HandleResolverService` against the *real* `SourcebotClient` against the *real* Sourcebot container in the compose stack. Mocking any of those layers would defeat the gate's purpose (proving REFUTED is reachable through the slice's actual code paths). The only synthetic input is the deliberately-mismatched `Citation` — and that synthesis is required because no API/agent flow can produce a mismatched citation by design.

**Why the verifier detects this as REFUTED** (Plan 9 contract): when Plan 9's `VerifierService._resolve_one` resolves a `Citation`, it (a) calls `HandleResolverService.resolve(handle)` to fetch the current excerpt from Sourcebot, then (b) compares that to the excerpt stored on the `Citation`. Equal → `ResolvedCitation(resolved=True, matches_excerpt=True)`; unequal → `ResolvedCitation(resolved=True, matches_excerpt=False)`. The latter triggers REFUTED at the verdict-aggregation step. Plan 9 has NO `outcome` field and no `MISMATCH` literal — REFUTED is detected via `resolved is True AND matches_excerpt is False`.

- [ ] **Step 1: Add the verifier-integration test**

Create `tests/integration/slice/test_slice_verifier_refuted.py`:

```python
"""§8.5 gate 3 — VerifierService.verify returns REFUTED when a citation's
excerpt does not match what the resolver fetches from Sourcebot.

Why this test bypasses the API/agent surface: under Plan 10 Invariant 1
and Plan 9's contract, citations are always constructed from
HandleResolverService.resolve() output. Any API-driven query produces
citation.excerpt == resolver_output by construction, so the REFUTED
branch is unreachable through the API in steady state — that is by
design. Driving REFUTED therefore requires hand-constructing a
mismatched Citation and calling VerifierService.verify directly against
the live compose-deployed Sourcebot. The test imports VerifierService,
HandleResolverService, CodeSpanResolver, and SourcebotClient from the
in-tree src/codeoracle/ package; the compose stack is up (compose_stack
fixture); HTTP traffic goes to the compose Sourcebot at host-port 3000.
"""

from __future__ import annotations

import subprocess

import pytest

from codeoracle.adapters.sourcebot.client import SourcebotClient
from codeoracle.adapters.sourcebot.config import SourcebotConfig
from codeoracle.models.evidence import (
    Citation,
    Claim,
    EvidenceHandle,
    HandleType,
    VerdictStatus,
)
from codeoracle.services.handle_resolution import HandleResolverService
from codeoracle.services.handle_resolution.strategies.code_span import (
    CodeSpanResolver,
)
from codeoracle.services.verifier.service import VerifierService


# A real, in-tree file with content stable enough that a fixture
# excerpt would be brittle — we don't bundle a "matching" excerpt; we
# only need to know the file EXISTS at a real handle so the resolver
# call succeeds (resolved=True) and we can compare against a
# DELIBERATE non-match. codeoracle/__init__.py is < 50 lines and changes
# infrequently; if it is renamed/removed, this assertion surfaces
# the regression as resolved=False on the first run.
_SLICE_ANCHOR_LOCATOR = "file://src/codeoracle/__init__.py:1-3"
_DELIBERATE_WRONG_EXCERPT = (
    "DELIBERATE_NON_MATCHING_EXCERPT_ZZZ_NOT_PRESENT_IN_REPO_xyz123"
)


def _current_snapshot_sha() -> str:
    """Return host repo HEAD — Sourcebot indexes the host bind-mount at
    compose-up, so the host HEAD is the SHA Sourcebot has indexed for
    the duration of the slice session (pytest-docker holds the compose
    stack session-wide, and the workspace HEAD does not change while
    the session is running). We resolve this at test time rather than
    via a fixture so the test module is fully self-contained.
    """
    return subprocess.check_output(
        ["git", "rev-parse", "HEAD"], text=True
    ).strip()


@pytest.mark.slice
async def test_verifier_returns_refuted_when_excerpt_mismatches_source(
    compose_stack: None,
) -> None:
    """§8.5 gate 3 — Verifier emits REFUTED when citation excerpt
    mismatches resolved source.

    Path: a hand-built Citation with a deliberately wrong `excerpt`
    against a real, indexable handle. VerifierService.verify resolves
    via SourcebotClient (HTTP to compose Sourcebot @ :3000), compares
    returned bytes to the wrong excerpt, and produces:

        ResolvedCitation(resolved=True, matches_excerpt=False)

    which Plan 9's verdict aggregation classifies as REFUTED.

    Signature pins (advisor round-3 grep verification, 2026-05-26):
      * EvidenceHandle has fields {type, locator, snapshot_sha}
        (Plan 3 L1253-1260). Citation embeds an EvidenceHandle;
        HandleType lives on the handle, NOT on Citation.
      * Citation has fields {handle, excerpt, source_trust}
        (Plan 3 L1277-1286). Constructing with a `handle_type=` kwarg
        is a TypeError.
      * HandleResolverService takes `strategies: dict[HandleType, ...]`
        (Plan 8 L7 + L2047). There is no `resolvers=` kwarg and no
        `build_resolver_map` helper — strategies dict is constructed
        inline at each call site, matching the unit tests in Plan 9.
      * VerifierService takes `*, resolver: HandleResolverService`
        (Plan 9 L1411; ALL Plan 9 tests use `VerifierService(resolver=...)`).
        `handle_resolver=` is a TypeError.
      * Claim has fields {statement, citations, confidence,
        contradictions} (Plan 3 L1314-1320). Constructing with `text=`
        is a TypeError; the field is `statement`.
      * Verdict.status, NOT .kind (Plan 3 L1333 + Plan 9 ~25 call sites
        all using `verdict.status`).
    """
    snapshot_sha = _current_snapshot_sha()

    # Build the production resolver layer against the compose Sourcebot,
    # then wrap it in the production VerifierService — no mocks.
    #
    # Composition note: `Settings` (Plan 3 L503) does NOT carry a `sourcebot:
    # SourcebotConfig` field — `SourcebotConfig` (Plan 8 L529) is a standalone
    # BaseSettings each composition root constructs directly. Production sites
    # (Plan 8 L434/444/454/461/467) all use `SourcebotConfig()` with no args,
    # letting Pydantic BaseSettings read `CODEORACLE_SOURCEBOT__BASE_URL`,
    # `CODEORACLE_SOURCEBOT__API_KEY`, `CODEORACLE_SOURCEBOT__DEFAULT_REPO` from env.
    # The `compose_stack` fixture (Task 2) ensures Plan 2's `.env` is exported
    # to the host pytest process before any slice test runs, so this no-arg
    # construction resolves against the live compose Sourcebot at
    # `http://127.0.0.1:3000`. SourcebotClient takes the config positionally
    # (Plan 8 L827: `def __init__(self, config: SourcebotConfig) -> None`).
    sourcebot_client = SourcebotClient(SourcebotConfig())
    handle_resolver = HandleResolverService(
        strategies={
            HandleType.CODE_SPAN: CodeSpanResolver(sourcebot=sourcebot_client),
        },
    )
    verifier = VerifierService(resolver=handle_resolver)

    handle = EvidenceHandle(
        type=HandleType.CODE_SPAN,
        locator=_SLICE_ANCHOR_LOCATOR,
        snapshot_sha=snapshot_sha,
    )
    citation = Citation(
        handle=handle,
        excerpt=_DELIBERATE_WRONG_EXCERPT,
        source_trust=5,
    )
    claim = Claim(
        statement="(slice gate 3) verifier should REFUTE this claim",
        citations=[citation],
        confidence=0.9,
    )

    verdict = await verifier.verify(claim)

    assert verdict.status == VerdictStatus.REFUTED, (
        f"§8.5 gate 3: expected REFUTED, got {verdict.status} "
        f"(reason={verdict.reason!r})"
    )
    # Plan 9 contract — REFUTED <=> >=1 ResolvedCitation has
    # resolved=True AND matches_excerpt=False (Plan 9 L1459, L1462).
    assert len(verdict.resolved_citations) == 1, (
        f"Expected exactly one resolved citation; got "
        f"{len(verdict.resolved_citations)}"
    )
    rc = verdict.resolved_citations[0]
    assert rc.resolved is True, (
        "Resolver should have succeeded (the handle is valid); "
        f"resolved={rc.resolved}"
    )
    assert rc.matches_excerpt is False, (
        "The excerpt was deliberately wrong; matches_excerpt should "
        f"be False, got {rc.matches_excerpt}"
    )
```

- [ ] **Step 2: Run the test against the compose stack**

```bash
uv run pytest tests/integration/slice/test_slice_verifier_refuted.py -v
```

Expected: PASS — REFUTED returned, `resolved=True`, `matches_excerpt=False`.

If FAIL with `resolved=False` instead, the Sourcebot index does not contain `src/codeoracle/__init__.py` (Plan 6's `sourcebot_indexer` did not complete or the file is excluded from indexing). Surface and fix Plan 6's Sourcebot indexing scope; do NOT change the test to skip the resolved=True assertion (that would silently bypass the gate, exactly the failure mode advisor round 1 flagged for `clean_snapshot`).

- [ ] **Step 3: Commit**

```bash
git add tests/integration/slice/test_slice_verifier_refuted.py
git commit -m "feat(slice): §8.5 gate 3 — verifier REFUTES when excerpt mismatches resolver output

Verifier-integration test against the compose-deployed Sourcebot (no mocks).
The test bypasses the API/agent layer because Plan 10 Invariant 1 + Plan 9's
contract make a mismatched-excerpt Citation unreachable through any
API-driven query in steady state — citations are always re-built from
HandleResolverService.resolve() output. The REFUTED branch is therefore
exercised by constructing the mismatched Citation directly and calling
VerifierService.verify against the production resolver layer.

Host-filesystem mutation is rejected as the discrimination mechanism:
CodeSpanResolver reads via SourcebotClient.fetch_span() → HTTP to
Sourcebot's Zoekt index, not the host filesystem; host-side mutation
does not propagate to Sourcebot in test-relevant time. See Task 5 prose
+ DECIDE-U for the full reasoning.

Plan 9 contract (L1459, L1462): REFUTED ⇔ ≥1 ResolvedCitation with
resolved=True AND matches_excerpt=False.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

## Task 6: §8.5 functional gate 4 — `test_mcp_explain_symbol_matches_api_response`

**Files:**
- Modify: `tests/integration/slice/test_slice_explain_symbol.py` (append test)

**Goal:** THE byte-equal parity gate. Query the same `Query` payload through both transports (POST /v1/query against the API + `explain_symbol` MCP tool against the MCP); assert the resulting `QueryResult` is byte-equal modulo `trace_id` per Plan 11 invariant 3 + Plan 12 invariant 3 jointly. Uses `mcp.client.streamable_http.streamablehttp_client` directly per the SDK's canonical client pattern (no abstraction layer).

> **Same-named test, two layers (§7.4 #9).** A `test_mcp_explain_symbol_matches_api_response` of the same name also lives in **Plan 12 Task 8** (`tests/integration/mcp/test_parity_with_api.py`) — that copy is the IN-PROCESS layer (`MCPServer` + a minimal FastAPI app in one process, `ASGITransport`, mocked deps). THIS copy is the FULL-STACK layer (real `codeoracle-mcp` + `codeoracle-api` over compose, `streamablehttp_client`). Intentional two-layer coverage of the one §8.5 gate-4 parity contract. Not a duplication.

- [ ] **Step 1: Append the failing test**

```python
# Append to tests/integration/slice/test_slice_explain_symbol.py

from mcp.client.session import ClientSession
from mcp.client.streamable_http import streamablehttp_client


def _extract_query_result_from_tool_result(tool_result) -> QueryResult:
    """Parse a QueryResult from an MCP CallToolResult, robust to both
    `structuredContent` (SDK marshals dict-returning handlers + outputSchema
    this way) and `content[0].text` JSON-string fallback paths.

    Plan 12 commits to the handler returning `QueryResult.model_dump(mode="json")`
    as a dict (Plan 12 L712); the SDK's wire-shape decision (structured
    vs textual) is downstream of Plan 12 and we don't pin it from Plan 13.
    """
    import json

    if tool_result.structuredContent is not None:
        return QueryResult.model_validate(tool_result.structuredContent)

    # Fallback: content[0] is a TextContent carrying JSON. Plan 12's
    # handler returns the dict; the SDK serialises to JSON text when
    # structuredContent isn't populated.
    content = tool_result.content
    if not content:
        raise AssertionError(
            "MCP explain_symbol tool result is empty (no structuredContent, no content)."
        )
    first = content[0]
    text_value = getattr(first, "text", None)
    if text_value is None:
        raise AssertionError(
            f"MCP explain_symbol returned non-text content[0]={first!r}; "
            f"expected TextContent with JSON-serialised QueryResult."
        )
    return QueryResult.model_validate(json.loads(text_value))


async def test_mcp_explain_symbol_matches_api_response(
    api_client: httpx.AsyncClient,
    mcp_base_url: str,
    clean_snapshot: None,
) -> None:
    """§8.5 gate 4 — MCP explain_symbol byte-equal to API POST /v1/query (modulo trace_id)."""
    await _ensure_indexed(api_client)

    query_payload = {
        "type": "explain_symbol",
        "symbol": CANONICAL_PROVEN_SYMBOL,
        "corpus": "self",
        "snapshot_sha": None,
    }

    # Path 1: HTTP API
    api_result = await _post_query_and_parse(api_client, query_payload)

    # Path 2: MCP streamable HTTP
    async with streamablehttp_client(mcp_base_url) as (read_stream, write_stream, _):
        async with ClientSession(read_stream, write_stream) as session:
            await session.initialize()
            tool_result = await session.call_tool(
                name="explain_symbol",
                arguments={"query": query_payload},
            )
            assert not tool_result.isError, (
                f"MCP explain_symbol returned isError=True: "
                f"{tool_result.content!r}"
            )
            mcp_result = _extract_query_result_from_tool_result(tool_result)

    # The §8.5 gate 4 parity assertion.
    assert_query_results_equal_modulo_trace_id(
        api_result=api_result, mcp_result=mcp_result,
    )
```

- [ ] **Step 2: Run + commit**

```bash
uv run pytest tests/integration/slice/test_slice_explain_symbol.py::test_mcp_explain_symbol_matches_api_response -v
```

Expected: PASS.

```bash
git add tests/integration/slice/test_slice_explain_symbol.py
git commit -m "feat(slice): §8.5 gate 4 — byte-equal parity across MCP + HTTP

Drives the same Query payload through POST /v1/query (API) and MCP explain_symbol (streamable HTTP); asserts QueryResult byte-equality modulo trace_id via _helpers.assert_query_results_equal_modulo_trace_id.

This is the joint enforcement of Plan 11 invariant 3 + Plan 12 invariant 3: both transports must return the same QueryResult Pydantic instance for the same input. Spec §8.5 gate 4 says 'Same query via MCP and HTTP API → byte-equal Claim payloads (modulo trace_id).'

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

## Task 7: §8.5 functional gate 5 — `test_reindex_endpoint_produces_new_snapshot`

**Files:**
- Create: `tests/integration/slice/test_slice_reindex.py` (~110 lines)

**Goal:** Drive `POST /v1/reindex` and the GET poll surface; assert the round-trip terminates with `Snapshot.status == APPLIED` plus `extractor_results` containing all four extractor keys (joern, scip, opengrep, embed) with `SUCCESS` outcomes per D-6.5 (the 4-extractor live E2E). The POST returns 202 + `Snapshot(PENDING|APPLYING)` per the Plan 11 amendment 2026-05-26 sync→async pivot; the wait happens in the `drive_reindex_to_applied` helper which polls `GET /v1/reindex/{snapshot_sha}` until terminal.

- [ ] **Step 1: Write the failing test**

```python
# tests/integration/slice/test_slice_reindex.py
"""§8.5 functional gate 5 — reindex endpoint enqueues + polls to APPLIED.

Pins (amended 2026-05-26 per Plan 11 sync→async pivot):
- POST /v1/reindex returns 202 + Snapshot(status=PENDING|APPLYING)
- GET /v1/reindex/{sha} polls until APPLIED|FAILED within 180s budget
- Final Snapshot.extractor_results contains all 4 expected extractors
  (joern, scip, opengrep, embed) each with ExtractorOutcome.SUCCESS
  (D-6.5: 4-extractor live E2E was pushed to Plan 13)
- Duplicate POST against an unchanged tree is idempotent — returns the
  same snapshot_sha (Plan 11 invariant 4 post-amendment).
"""

from __future__ import annotations

import httpx
import pytest
from mcp.client.session import ClientSession
from mcp.client.streamable_http import streamablehttp_client

from codeoracle.models.snapshot import (
    ExtractorOutcome,
    Snapshot,
    SnapshotStatus,
)

from ._helpers import drive_reindex_to_applied


pytestmark = [pytest.mark.slice, pytest.mark.asyncio]


EXPECTED_EXTRACTORS = frozenset({"joern", "scip", "opengrep", "embed"})


async def test_reindex_endpoint_produces_new_snapshot(
    api_client: httpx.AsyncClient,
    clean_snapshot: None,
) -> None:
    """§8.5 gate 5 (amended 2026-05-26) — POST/poll → APPLIED + 4 extractors.

    Driven by `drive_reindex_to_applied` (Plan 13 `_helpers.py`) which
    POSTs then polls. The first call must produce a fresh APPLIED
    snapshot whose `extractor_results` carries all 4 expected extractor
    keys with SUCCESS outcomes (D-6.5).

    A second POST against the same byte-equal tree is idempotent per
    Plan 11 invariant 4 (post-amendment): the route returns 202 +
    the EXISTING snapshot row (status APPLIED, same sha). We assert
    that contract directly rather than re-polling — polling a second
    time would still terminate at APPLIED but would also pass for the
    (now-impossible) non-idempotent shape, masking a regression.
    """
    first = await drive_reindex_to_applied(api_client, base_sha=None)
    assert first.status == SnapshotStatus.APPLIED
    first_sha = first.sha
    assert first_sha, "First snapshot SHA must be non-empty"

    # Verify all 4 extractors ran successfully (D-6.5).
    assert set(first.extractor_results.keys()) == EXPECTED_EXTRACTORS, (
        f"§8.5 / D-6.5: expected extractors {EXPECTED_EXTRACTORS}, "
        f"got {set(first.extractor_results.keys())}"
    )
    for extractor, outcome in first.extractor_results.items():
        assert outcome == ExtractorOutcome.SUCCESS, (
            f"Extractor {extractor!r} reported {outcome!r}, expected SUCCESS"
        )

    # Idempotent duplicate POST contract (Plan 11 invariant 4 amended):
    # against the same byte-equal tree, the route returns 202 + the
    # existing APPLIED row — same sha, no second XADD, no new snapshot.
    second_response = await api_client.post(
        "/v1/reindex", json={"base_sha": None}
    )
    assert second_response.status_code == 202, (
        f"Idempotent retry expected 202, got {second_response.status_code}"
    )
    second = Snapshot.model_validate(second_response.json())
    assert second.sha == first_sha, (
        f"Idempotent retry against unchanged tree must return the same "
        f"snapshot_sha; first={first_sha!r} second={second.sha!r}"
    )
    assert second.status == SnapshotStatus.APPLIED, (
        f"Idempotent retry must surface terminal status, got {second.status!r}"
    )
```

- [ ] **Step 1b: Append the MCP↔API reindex-parity gate**

THE reindex-parity gate — the coherence test for the 2026-05-26 sync→async cascade (Plan 11 amendment `fc0894a` + Plan 12 DECIDE-G amended). The existing `explain_symbol` parity gate (Task 6) covers query parity; this gate proves the `reindex` tool and `POST /v1/reindex` agree, so a future MCP/API reindex divergence is caught. Because `reindex` MUTATES and is async, the assertion is NOT byte-equal-modulo-`trace_id` (the `Snapshot` drifts PENDING→APPLYING→APPLIED as the worker runs): instead it pins that both transports route to the same `IndexingService.enqueue_reindex` (Plan 5 amendment `40885bf`), so the same `base_sha` against the same clean tree yields the SAME `snapshot_sha` and an enqueue-shaped `Snapshot`. Mirrors the Task 6 structure (`mcp.client.streamable_http.streamablehttp_client` + `ClientSession`, no abstraction layer).

The two MCP-client imports (`mcp.client.session.ClientSession`,
`mcp.client.streamable_http.streamablehttp_client`) are added to the
file's existing top-of-module import block (alongside `httpx` / `pytest`
/ `codeoracle.models.snapshot`) — NOT appended inline, which would be a ruff
`E402` (module-level import after code). The rest of this block appends
to the body of `test_slice_reindex.py`.

```python
# Appended to the body of tests/integration/slice/test_slice_reindex.py
# (imports above already hoisted into the module header).


def _extract_snapshot_from_tool_result(tool_result) -> Snapshot:
    """Parse a Snapshot from an MCP CallToolResult, robust to both
    `structuredContent` (the SDK marshals dict-returning handlers +
    outputSchema this way) and the `content[0].text` JSON-string fallback.

    Plan 12's reindex handler returns `Snapshot.model_dump(mode="json")`
    as a dict (Plan 12 Task 3); the SDK's wire-shape decision (structured
    vs textual) is downstream of Plan 12 and we don't pin it from Plan 13.
    Mirrors `_extract_query_result_from_tool_result` in Task 6.
    """
    import json

    if tool_result.structuredContent is not None:
        return Snapshot.model_validate(tool_result.structuredContent)

    content = tool_result.content
    if not content:
        raise AssertionError(
            "MCP reindex tool result is empty (no structuredContent, no content)."
        )
    first = content[0]
    text_value = getattr(first, "text", None)
    if text_value is None:
        raise AssertionError(
            f"MCP reindex returned non-text content[0]={first!r}; "
            f"expected TextContent with a JSON-serialised Snapshot."
        )
    return Snapshot.model_validate(json.loads(text_value))


_ENQUEUE_STATUSES = frozenset(
    {SnapshotStatus.PENDING, SnapshotStatus.APPLYING, SnapshotStatus.APPLIED}
)


async def test_mcp_reindex_matches_api_reindex(
    api_client: httpx.AsyncClient,
    mcp_base_url: str,
    clean_snapshot: None,
) -> None:
    """Reindex-parity gate — MCP `reindex` enqueues the SAME snapshot as
    `POST /v1/reindex` for the same `base_sha`.

    Post the 2026-05-26 async pivot both transports delegate to
    `IndexingService.enqueue_reindex` and return the enqueue receipt
    (202 + `Snapshot(PENDING|APPLYING|APPLIED)` for HTTP; the same
    `Snapshot` dict via MCP). The two calls run against the same clean
    tree back-to-back, so `enqueue_reindex`'s idempotency on
    `SnapshotConflictError` (Plan 11 invariant 4) makes the second call
    return the existing row — the parity contract is `sha` equality, NOT
    byte-equality (status / applied_at / extractor_results legitimately
    advance as the worker runs).
    """
    # Path 1: HTTP API — POST /v1/reindex (full reindex, base_sha=None).
    api_response = await api_client.post("/v1/reindex", json={"base_sha": None})
    assert api_response.status_code == 202, (
        f"POST /v1/reindex expected 202, got "
        f"{api_response.status_code} {api_response.text}"
    )
    api_snapshot = Snapshot.model_validate(api_response.json())
    assert api_snapshot.status in _ENQUEUE_STATUSES, (
        f"API reindex returned non-enqueue status {api_snapshot.status!r}"
    )

    # Path 2: MCP streamable HTTP — the `reindex` tool with the same base_sha.
    async with streamablehttp_client(mcp_base_url) as (read_stream, write_stream, _):
        async with ClientSession(read_stream, write_stream) as session:
            await session.initialize()
            tool_result = await session.call_tool(
                name="reindex",
                arguments={"base_sha": None},
            )
            assert not tool_result.isError, (
                f"MCP reindex returned isError=True: {tool_result.content!r}"
            )
            mcp_snapshot = _extract_snapshot_from_tool_result(tool_result)

    assert mcp_snapshot.status in _ENQUEUE_STATUSES, (
        f"MCP reindex returned non-enqueue status {mcp_snapshot.status!r}"
    )

    # The parity assertion: same tree state → same snapshot_sha through
    # either transport (sha is a pure function of tree content; both routes
    # call the same enqueue_reindex). This is the coherence proof for the
    # whole sync→async cascade.
    assert mcp_snapshot.sha == api_snapshot.sha, (
        f"MCP reindex enqueued snapshot_sha={mcp_snapshot.sha!r} but the API "
        f"enqueued {api_snapshot.sha!r} for the same base_sha — the reindex "
        f"contracts have diverged across transports."
    )
    assert mcp_snapshot.parent_sha == api_snapshot.parent_sha, (
        f"Parent-sha mismatch across transports: MCP={mcp_snapshot.parent_sha!r} "
        f"API={api_snapshot.parent_sha!r}"
    )
```

- [ ] **Step 2: Run + lint + commit**

```bash
uv run pytest tests/integration/slice/test_slice_reindex.py -v
uv run ruff check tests/integration/slice/test_slice_reindex.py
uv run ty check tests/integration/slice/test_slice_reindex.py
```

Expected: PASS (both gate 5 + the reindex-parity gate), zero findings.

```bash
git add tests/integration/slice/test_slice_reindex.py
git commit -m "feat(slice): §8.5 gate 5 — POST+poll reindex with 4-extractor outcomes

Drives the POST /v1/reindex (202 + PENDING) + GET /v1/reindex/{sha} (poll-to-terminal) round trip via _helpers.drive_reindex_to_applied. Asserts the final Snapshot.status == APPLIED and Snapshot.extractor_results contains all 4 expected extractor keys (joern, scip, opengrep, embed) with SUCCESS outcomes. The 4-extractor live E2E was pushed to Plan 13 per audit row D-6.5; the sync→async pivot was pushed by Plan 11 amendment 2026-05-26 (DECIDE-V).

Also covers Plan 11 invariant 4 (post-amendment) idempotent retry: a duplicate POST against the same byte-equal tree returns 202 with the EXISTING snapshot_sha and APPLIED status, no second XADD, no new row.

Adds the MCP<->API reindex-parity gate (test_mcp_reindex_matches_api_reindex): the reindex tool and POST /v1/reindex must enqueue the SAME snapshot_sha for the same base_sha (both delegate to IndexingService.enqueue_reindex). This is the coherence test for the sync->async cascade — the explain_symbol parity gate (Task 6) covers query parity but NOT reindex; this closes that gap. Assertion is sha-equality, not byte-equality, because the async Snapshot mutates as the worker runs.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

## Task 8: §8.5 incremental gates 6-8

**Files:**
- Create: `tests/integration/slice/test_slice_incremental.py` (~180 lines — three tests)

**Goal:** Land the three incremental-reindex tests from spec §8.5:

- `test_incremental_reindex_matches_full_reindex` — characterisation: after the same edit sequence, incremental and full reindex produce byte-equal graph state under sorted-canonical comparison.
- `test_incremental_reindex_invalidates_dependents` — rename a symbol in file A; reindex incrementally; callers in file B show the reference as unresolved (not still pointing to the old name).
- `test_incremental_reindex_performance` — single-file edit completes in <10s; no-op (whitespace-only) edit completes in <2s. Per DECIDE-T this is a measurement step, not a hard gate; perf metrics emitted to `perf-report.json`.

- [ ] **Step 1: Write the three tests**

```python
# tests/integration/slice/test_slice_incremental.py
"""§8.5 incremental gates 6-8 — three incremental-reindex integration tests.

The tests drive edits-then-reindex against the bind-mounted src/codeoracle/
tree. Edits are written from the host process via direct binary writes
to the source file; restored in a try/finally so a test crash still
un-mutates. Graph-state snapshots for the byte-equal comparison are
read via direct asyncpg from the AGE graph tables — NOT via an admin
HTTP endpoint (see DECIDE-U).

The overlay bind-mounts src/codeoracle/ :ro inside containers but the host
process retains write access to the same inode, so the agent + worker
see post-mutation content live without container restarts.
"""

from __future__ import annotations

import json
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import asyncpg
import httpx
import pytest

from codeoracle.models.snapshot import Snapshot, SnapshotStatus

from ._helpers import drive_reindex_to_applied


pytestmark = [pytest.mark.slice, pytest.mark.asyncio]


_PERF_REPORT_PATH = Path("perf-report.json")
_REPO_ROOT = Path(__file__).resolve().parents[3]
# File B — the dependent. VerifierService.verify (Plan 9) calls
# self._resolver.resolve(...) on its constructor-injected
# HandleResolverService, so it carries a cross-file reference into File A.
_VERIFIER_FILE = (
    _REPO_ROOT / "src" / "codeoracle" / "services" / "verifier" / "service.py"
)
# File A — the definition. HandleResolverService.resolve (Plan 8) is the
# symbol the gate-7 cross-file test renames; the rename leaves File B's
# reference dangling so the dependent (verify) must resolve UNKNOWN.
_HANDLE_RESOLVER_FILE = (
    _REPO_ROOT / "src" / "codeoracle" / "services" / "handle_resolution" / "service.py"
)


@contextmanager
def _swap_file_contents(path: Path, *, find: bytes, replace: bytes) -> Iterator[None]:
    """Replace bytes for the duration of the block; restore in finally."""
    original = path.read_bytes()
    if find not in original:
        raise AssertionError(
            f"_swap_file_contents: pattern {find!r} not found in {path}"
        )
    path.write_bytes(original.replace(find, replace, 1))
    try:
        yield
    finally:
        path.write_bytes(original)


def _record_perf_metric(name: str, value_s: float, target_s: float) -> None:
    """Append a perf observation to perf-report.json (idempotent on key)."""
    existing: dict[str, dict[str, float]] = {}
    if _PERF_REPORT_PATH.exists():
        existing = json.loads(_PERF_REPORT_PATH.read_text())
    existing[name] = {"observed_s": value_s, "target_s": target_s}
    _PERF_REPORT_PATH.write_text(json.dumps(existing, indent=2, sort_keys=True))


async def _full_reindex(api_client: httpx.AsyncClient) -> Snapshot:
    """Drive a full reindex (base_sha=None) to APPLIED via POST + polling.

    Thin wrapper around `drive_reindex_to_applied` (Plan 13 `_helpers.py`).
    Amended 2026-05-26 per Plan 11 sync→async pivot — the POST returns
    202 + PENDING, the wait happens in the poll loop. See DECIDE-V.
    """
    return await drive_reindex_to_applied(api_client, base_sha=None)


async def _incremental_reindex(
    api_client: httpx.AsyncClient, *, base_sha: str
) -> Snapshot:
    """Drive an incremental reindex (base_sha != None) to APPLIED.

    Same async pattern as `_full_reindex` — POST + poll. The
    `base_sha` argument propagates through `enqueue_reindex` into the
    worker's `IndexingService._reindex_locked` Plan 5 amendment Task 6.
    """
    return await drive_reindex_to_applied(api_client, base_sha=base_sha)


async def _canonical_graph_state(db_conn: asyncpg.Connection) -> str:
    """Read every vertex + every edge in the `codeoracle` AGE graph and emit
    a deterministic, canonical-JSON serialisation suitable for byte-equal
    comparison across reindex runs.

    Critical: AGE assigns sequential auto-IDs to vertices + edges at
    insertion time. After this fixture's `drop_graph + create_graph`
    reset, the next reindex re-allocates IDs from 0, and the extractor
    fan-out (Plan 6: joern + scip + opengrep + embed run concurrently
    in IndexingService) does NOT guarantee insertion order is
    deterministic. The auto-id therefore cannot participate in equality
    comparison — `RETURN n` serialises the full {id, label, properties}
    triple, whose `id` would shift between runs, breaking the test even
    when the source tree is unchanged. We project label + properties
    explicitly for vertices, and (rel_type, rel_props, src_label,
    src_props, dst_label, dst_props) for edges; the projection is
    closed under the auto-id renaming.

    Each vertex/edge MUST carry a stable natural identifier in its
    properties (e.g. File.file_path, Symbol.fqn) — Plan 5/6 schemas ensure
    this. The projection collapses true duplicates (same label + same
    properties), which would be a Plan 5/6 schema bug; that's covered
    by Plan 5/6's own contract tests, not here.

    NOT an admin HTTP endpoint per DECIDE-U.
    """
    # Vertices: project label + properties (drop auto-id).
    vertex_rows = await db_conn.fetch(
        "SELECT ag_catalog.agtype_to_json(lbl) AS lbl, "
        "       ag_catalog.agtype_to_json(props) AS props "
        "FROM cypher('codeoracle', $$ "
        "    MATCH (n) "
        "    RETURN label(n) AS lbl, properties(n) AS props "
        "$$) AS (lbl ag_catalog.agtype, props ag_catalog.agtype)"
    )
    vertex_tuples = sorted(
        (row["lbl"], row["props"]) for row in vertex_rows
    )

    # Edges: project type + properties + endpoint labels/properties
    # (drop auto-id on the edge AND on each endpoint).
    edge_rows = await db_conn.fetch(
        "SELECT ag_catalog.agtype_to_json(typ)       AS typ, "
        "       ag_catalog.agtype_to_json(props)     AS props, "
        "       ag_catalog.agtype_to_json(src_lbl)   AS src_lbl, "
        "       ag_catalog.agtype_to_json(src_props) AS src_props, "
        "       ag_catalog.agtype_to_json(dst_lbl)   AS dst_lbl, "
        "       ag_catalog.agtype_to_json(dst_props) AS dst_props "
        "FROM cypher('codeoracle', $$ "
        "    MATCH (a)-[r]->(b) "
        "    RETURN type(r)        AS typ, "
        "           properties(r)  AS props, "
        "           label(a)       AS src_lbl, "
        "           properties(a)  AS src_props, "
        "           label(b)       AS dst_lbl, "
        "           properties(b)  AS dst_props "
        "$$) AS (typ ag_catalog.agtype, props ag_catalog.agtype, "
        "        src_lbl ag_catalog.agtype, src_props ag_catalog.agtype, "
        "        dst_lbl ag_catalog.agtype, dst_props ag_catalog.agtype)"
    )
    edge_tuples = sorted(
        (
            row["typ"],
            row["props"],
            row["src_lbl"],
            row["src_props"],
            row["dst_lbl"],
            row["dst_props"],
        )
        for row in edge_rows
    )

    return json.dumps(
        {"vertices": vertex_tuples, "edges": edge_tuples},
        sort_keys=True, separators=(",", ":"),
    )


async def test_incremental_reindex_matches_full_reindex(
    api_client: httpx.AsyncClient,
    db_conn: asyncpg.Connection,
    clean_snapshot: None,
) -> None:
    """§8.5 gate 6 — incremental and full reindex produce byte-equal graph state.

    The property under test is *incremental ≡ full on the same mutated tree*,
    NOT full-reindex determinism. We establish a baseline, mutate one file,
    then drive BOTH paths against the identical post-edit tree: an
    INCREMENTAL reindex (diffing against the baseline sha) and, after
    resetting the graph, a FULL reindex (base_sha=None). If incremental's
    partial re-extraction diverges from full's from-scratch extraction, the
    two canonical graph states differ and the gate fails.
    """
    baseline = await _full_reindex(api_client)

    # Mutate one file; both reindexes below run while the edit is in place,
    # so they extract the *same* tree. The baseline sha is only the
    # incremental path's diff base.
    with _swap_file_contents(
        _VERIFIER_FILE,
        find=b"def verify(",
        replace=b"def verify(  # slice gate-6 marker\n        ",
    ):
        # Path 1 — INCREMENTAL against the baseline (re-extracts only the
        # changed file + its dependents per Plan 5 IndexingService).
        incremental = await _incremental_reindex(api_client, base_sha=baseline.sha)
        assert incremental.status == SnapshotStatus.APPLIED
        canonical_after_incremental = await _canonical_graph_state(db_conn)

        # Reset the graph so the next reindex is a true from-scratch build
        # against the SAME (still-mutated) tree. base_sha=None on a clean DB
        # is by contract a full extraction (Plan 5 IndexingService.reindex).
        table_list = (
            "snapshots, snapshot_file_outcomes, claims, citations, traces, embeddings"
        )
        await db_conn.execute(f"TRUNCATE {table_list} RESTART IDENTITY CASCADE")
        await db_conn.execute("SELECT * FROM ag_catalog.drop_graph('codeoracle', true)")
        await db_conn.execute("SELECT * FROM ag_catalog.create_graph('codeoracle')")

        # Path 2 — FULL against the same mutated tree.
        full = await _full_reindex(api_client)
        assert full.status == SnapshotStatus.APPLIED
        canonical_after_full = await _canonical_graph_state(db_conn)

    assert canonical_after_incremental == canonical_after_full, (
        "§8.5 gate 6: incremental reindex (diffed against the baseline) and "
        "full reindex of the identical mutated tree must produce byte-equal "
        "canonical graph state — divergence means the incremental path "
        "under- or over-extracted relative to a from-scratch build"
    )


async def test_incremental_reindex_invalidates_dependents(
    api_client: httpx.AsyncClient,
    clean_snapshot: None,
) -> None:
    """§8.5 gate 7 — a rename in file A invalidates a dependent in file B.

    This is the genuine CROSS-FILE staleness test. File A
    (`handle_resolution/service.py`) defines `HandleResolverService.resolve`;
    File B (`verifier/service.py`) defines the *dependent* `VerifierService.verify`,
    whose body calls `self._resolver.resolve(...)` — a reference into File A.

    We rename `resolve` in File A only, drive an INCREMENTAL reindex against
    the post-edit tree, then query the DEPENDENT (`verify` in File B, which we
    did NOT edit) and assert UNKNOWN. Querying the dependent — not the renamed
    symbol — is what proves the incremental path re-examined File B's edges
    into File A: a query on the renamed symbol itself would pass even if a
    stale B→A edge survived, because the renamed node is simply gone. An
    UNKNOWN verdict on the untouched dependent can only mean its now-dangling
    reference failed to resolve.

    Vacuity guard (mirrors the gate-1 non-emptiness precondition): this gate
    presumes `explain_symbol(verify)` surfaces a GRAPH_QUERY citation that
    traverses the verify->resolve edge into File A. If the live citation set
    instead carries only CODE_SPAN excerpts of `verify`'s own (unchanged)
    body, the renamed callee never enters the citation set, the gate
    vacuously PROVENs, and the symbol pair must be revisited. The assertion
    has signal only under that presumption.
    """
    from codeoracle.models.evidence import VerdictStatus

    baseline = await _full_reindex(api_client)

    # Rename the DEFINITION in File A; File B's call site (`self._resolver
    # .resolve(...)`) is left untouched and now dangles.
    with _swap_file_contents(
        _HANDLE_RESOLVER_FILE,
        find=b"def resolve(",
        replace=b"def resolve_renamed(",
    ):
        snapshot = await _incremental_reindex(api_client, base_sha=baseline.sha)
        assert snapshot.status == SnapshotStatus.APPLIED

        # Query the DEPENDENT in File B (not the renamed symbol in File A).
        response = await api_client.post(
            "/v1/query",
            json={
                "type": "explain_symbol",
                "symbol": "codeoracle.services.verifier.service.VerifierService.verify",
                "corpus": "self",
                "snapshot_sha": None,
            },
        )
        result = response.json()
        assert result["verdict"]["status"] == VerdictStatus.UNKNOWN.value, (
            f"After renaming HandleResolverService.resolve in File A, the "
            f"dependent VerifierService.verify in File B has a dangling "
            f"reference and must resolve UNKNOWN, got "
            f"{result['verdict']['status']}"
        )


async def test_incremental_reindex_performance(
    api_client: httpx.AsyncClient,
    clean_snapshot: None,
) -> None:
    """§8.5 gate 8 — single-file edit reindex < 10s; no-op < 2s.

    Per DECIDE-T this is a measurement step, not a hard gate. We emit
    the observed durations to perf-report.json and post the summary to
    the PR; we do NOT fail the test on regression (Codecov-like
    behaviour per spec §8.5).
    """
    baseline = await _full_reindex(api_client)

    # Single-file edit — measured reindex.
    with _swap_file_contents(
        _VERIFIER_FILE,
        find=b"def verify(",
        replace=b"def verify (",  # add a single space — semantic no-op, content changes
    ):
        t0 = time.perf_counter()
        edit_snapshot = await _incremental_reindex(api_client, base_sha=baseline.sha)
        edit_duration = time.perf_counter() - t0
        _record_perf_metric("incremental_reindex_single_file_s", edit_duration, 10.0)
        assert edit_snapshot.status == SnapshotStatus.APPLIED

    # No-op (post-restore): reindex from the restored (== baseline) tree.
    # base_sha is the just-applied edit_snapshot; on the restored tree
    # there are no changed files so the incremental path is the cheapest
    # possible code path.
    t0 = time.perf_counter()
    noop_snapshot = await _incremental_reindex(api_client, base_sha=edit_snapshot.sha)
    noop_duration = time.perf_counter() - t0
    _record_perf_metric("incremental_reindex_noop_s", noop_duration, 2.0)
    assert noop_snapshot.status == SnapshotStatus.APPLIED
```

- [ ] **Step 2: Run + commit**

```bash
uv run pytest tests/integration/slice/test_slice_incremental.py -v
uv run ruff check tests/integration/slice/test_slice_incremental.py
uv run ty check tests/integration/slice/test_slice_incremental.py
```

Expected: 3 tests PASS, zero lint/type findings. `perf-report.json` exists with two metrics.

```bash
git add tests/integration/slice/test_slice_incremental.py
git commit -m "feat(slice): §8.5 incremental gates 6-8

Three integration tests:
- gate 6: incremental and full reindex produce byte-equal graph state under sorted-canonical comparison
- gate 7: rename in file A invalidates dependent references in file B
- gate 8: single-file edit reindex completes in <10s; no-op edit in <2s (measured, not gated per DECIDE-T)

Edits are staged via direct host-filesystem mutation of the bind-mounted src/codeoracle/ tree (the `_swap_file_contents` context manager, which auto-restores on exit) — no admin HTTP endpoint, per DECIDE-U (invariant 1: no new production architectural surface). Perf metrics serialised to perf-report.json for the CI PR-comment step.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

## Task 9: §8.5 intent-classifier gate — `test_intent_classifier_never_produces_unsupported_handle_types`

**Files:**
- Create: `tests/integration/slice/test_intent_classifier_handle_types.py` (~80 lines)
- Create: `tests/fixtures/llm_scripts/slice_intent_classifier.yaml` (canonical fixture)

**Goal:** The integration analogue of Plan 10's unit-test version (`test_intent_never_plans_stub_handle_types` / `test_explain_symbol_handle_types_constant`). Plan 10's intent node is DETERMINISTIC — no LLM call (Deviation 2) — and codifies the explain_symbol plan's handle types as the constant `EXPLAIN_SYMBOL_HANDLE_TYPES = [CODE_SPAN, DOC_PARAGRAPH, GRAPH_QUERY]`; `TRACE_SPAN`/`DB_RECORD` (which would `StubResolver`-raise per §8.2) are structurally impossible to emit at compose (Plan 10 invariant 8), not filtered after the fact. Per D-10.U3 this drives a known-resolvable symbol end-to-end through the deployed stack and asserts every citation the live pipeline returns carries a handle type from the codified set — the only HTTP-boundary manifestation of `plan_handle_types`. A PROVEN result with citations is required so the assertion is not vacuous.

- [ ] **Step 1: Write the fixture + test**

```yaml
# tests/fixtures/llm_scripts/slice_intent_classifier.yaml
#
# §8.5 intent-classifier gate fixture. The intent node is DETERMINISTIC
# (Plan 10 Deviation 2) — it makes NO LLM call — so there is no
# `agent.intent_parse` entry to script: the lone LLM call on the
# explain_symbol path is `agent.compose`. We reuse the canonical
# resolvable symbol so the query resolves to PROVEN and the gate's
# handle-type assertion has real citations to inspect. (This fixture
# mirrors the compose entry of Task 3's slice_explain_verifier.yaml;
# the codified plan_handle_types — CODE_SPAN / DOC_PARAGRAPH /
# GRAPH_QUERY — are fixed by the deterministic node, not by anything the
# LLM returns, so the compose response cites only those handle types.)
- template_id: agent.compose
  fingerprint: "PLACEHOLDER_compose_classifier_verifier_verify_proven"
  # Composed by the agent's compose node; LLM authors prose + cites by index.
  response:
    role: assistant
    content: |
      VerifierService.verify resolves each citation's handle to its
      current content via HandleResolverService and returns a Verdict
      — PROVEN if all resolve and match [1] [2] [3].
    structured_output:
      statement: |
        VerifierService.verify resolves each citation's handle to its
        current content via HandleResolverService and returns a Verdict.
      cited_indices: [0, 1, 2]
      confidence: 0.88
```

```python
# tests/integration/slice/test_intent_classifier_handle_types.py
"""§8.5 gate — intent classifier never emits TRACE_SPAN or DB_RECORD."""

from __future__ import annotations

import httpx
import pytest

from codeoracle.models.evidence import VerdictStatus
from codeoracle.models.query import QueryResult
from codeoracle.models.snapshot import SnapshotStatus

from ._helpers import drive_reindex_to_applied, extract_handle_types


pytestmark = [pytest.mark.slice, pytest.mark.asyncio]


# The codified handle types the explain_symbol plan emits — Plan 10's
# EXPLAIN_SYMBOL_HANDLE_TYPES constant (invariant 8). TRACE_SPAN and
# DB_RECORD are NOT in this set, which is the property under test.
_CODIFIED_HANDLE_TYPES = frozenset({"CODE_SPAN", "DOC_PARAGRAPH", "GRAPH_QUERY"})

# The canonical resolvable symbol (shared with §8.5 gate 1 / Task 3) — a
# real symbol in src/codeoracle/ so the query resolves to a PROVEN claim with
# citations, giving the gate actual signal instead of a vacuous pass.
CANONICAL_PROVEN_SYMBOL = "codeoracle.services.verifier.service.VerifierService.verify"


async def test_intent_classifier_never_produces_unsupported_handle_types(
    api_client: httpx.AsyncClient,
    clean_snapshot: None,
) -> None:
    """The deployed agent's intent node plans only the codified handle types.

    Plan 10's intent node is DETERMINISTIC — it makes NO LLM call (Plan 10
    Deviation 2); it codifies the explain_symbol plan's handle types as the
    constant EXPLAIN_SYMBOL_HANDLE_TYPES = [CODE_SPAN, DOC_PARAGRAPH,
    GRAPH_QUERY]. TRACE_SPAN and DB_RECORD are therefore structurally
    impossible to emit at the compose boundary (Plan 10 invariant 8), not
    merely filtered after the fact. Per spec §8.2 those two handle types'
    resolvers are StubResolvers that raise, so emitting them would crash the
    verify path — invariant 8 is what keeps them out by construction.

    This is the integration analogue of Plan 10's unit tests
    `test_intent_never_plans_stub_handle_types` /
    `test_explain_symbol_handle_types_constant` (per D-10.U3): rather than
    re-asserting the constant in-process, it drives a known-resolvable
    symbol end-to-end through the deployed compose stack and pins that every
    citation the live pipeline returns carries a handle type from the
    codified set — the only HTTP-boundary manifestation of
    `plan_handle_types`. A PROVEN result with citations is required so the
    assertion has signal (an UNKNOWN/empty result would be vacuous).

    The setup reindex is driven via `drive_reindex_to_applied`
    (Plan 13 `_helpers.py`) — amended 2026-05-26 per the Plan 11
    sync→async pivot; POSTing and immediately querying would race
    against an unbuilt graph.
    """
    snapshot = await drive_reindex_to_applied(api_client, base_sha=None)
    assert snapshot.status == SnapshotStatus.APPLIED

    query_response = await api_client.post(
        "/v1/query",
        json={
            "type": "explain_symbol",
            "symbol": CANONICAL_PROVEN_SYMBOL,
            "corpus": "self",
            "snapshot_sha": None,
        },
    )
    assert query_response.status_code == 200, (
        f"POST /v1/query failed: {query_response.status_code} "
        f"{query_response.text}"
    )

    result = QueryResult.model_validate(query_response.json())

    # Hard-require a resolved claim with citations — otherwise the handle-type
    # assertion below would pass vacuously over an empty citation set.
    assert result.verdict.status == VerdictStatus.PROVEN, (
        f"§8.5 intent-classifier gate needs a PROVEN result for signal; got "
        f"{result.verdict.status} for {CANONICAL_PROVEN_SYMBOL}"
    )
    assert result.claim is not None, "PROVEN verdict must carry a claim"
    assert result.claim.citations, "PROVEN claim must carry ≥1 citation"

    citation_handle_types = extract_handle_types(result.claim)
    unexpected = citation_handle_types - _CODIFIED_HANDLE_TYPES
    assert not unexpected, (
        f"§8.5 intent-classifier gate: claim citations carried handle types "
        f"{unexpected} outside the codified set {sorted(_CODIFIED_HANDLE_TYPES)}. "
        f"Plan 10's deterministic intent node (invariant 8) must plan only "
        f"CODE_SPAN / DOC_PARAGRAPH / GRAPH_QUERY — never TRACE_SPAN / DB_RECORD."
    )
```

- [ ] **Step 2: Run + commit**

```bash
uv run pytest tests/integration/slice/test_intent_classifier_handle_types.py -v
```

Expected: PASS — the query resolves to PROVEN and every returned citation carries a codified handle type (CODE_SPAN / DOC_PARAGRAPH / GRAPH_QUERY); the deterministic intent node never plans TRACE_SPAN / DB_RECORD. If FAIL with `MockLLMScriptMissingError` listing the actual `agent.compose` fingerprint, paste it into `slice_intent_classifier.yaml` and rerun (same recording recipe as Task 3).

```bash
git add tests/integration/slice/test_intent_classifier_handle_types.py tests/fixtures/llm_scripts/slice_intent_classifier.yaml
git commit -m "feat(slice): §8.5 intent classifier gate — no TRACE_SPAN/DB_RECORD

Integration analogue of Plan 10's unit-test version (per D-10.U3). The intent node is deterministic — no LLM call (Plan 10 Deviation 2) — so the codified plan_handle_types for explain_symbol are fixed at {CODE_SPAN, DOC_PARAGRAPH, GRAPH_QUERY} and TRACE_SPAN/DB_RECORD are structurally impossible to emit (Plan 10 invariant 8). This test drives a known-resolvable symbol end-to-end through the deployed compose stack and asserts every returned citation carries a handle type from the codified set, pinning invariant 8 at the HTTP boundary.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

## Task 10: §8.7 ceremony scripts + asciinema recordings

**Files:**
- Create: `scripts/demos/proven.sh`, `unknown.sh`, `live-edit.sh`
- Create: `docs/acceptance/demos.md`
- Create: `tests/fixtures/acceptance-recordings/proven.cast`, `unknown.cast`, `live-edit.cast` (recorded manually — see `docs/acceptance/demos.md`)

**Goal:** Ship the §8.7 acceptance ceremony scripts + their checked-in asciinema recordings. Each script drives one ceremony path against the live compose stack. The `.cast` files are produced by a one-time human run of `asciinema rec` against the local stack at slice-acceptance time.

- [ ] **Step 1: Write `docs/acceptance/demos.md`**

```markdown
# Slice Acceptance Demos

Three demos verify the §8.7 acceptance ceremony paths. Each `.sh` script
exercises one ceremony path against the live compose stack; the `.cast`
file is the asciinema recording from the slice-acceptance ceremony run.

| Script | Verdict | Subject |
|--------|---------|---------|
| `proven.sh` | PROVEN | `codeoracle.services.verifier.service.VerifierService.verify` |
| `unknown.sh` | UNKNOWN | `codeoracle.services.deleted.NotARealService` |
| `live-edit.sh` | (changing) | Edit one line, reindex, requery |

## Running locally

```bash
docker compose -f infra/compose/docker-compose.yml \
               -f infra/compose/docker-compose.e2e.yml up -d
bash scripts/demos/proven.sh
bash scripts/demos/unknown.sh
bash scripts/demos/live-edit.sh
```

## Re-recording (manual; produces new `.cast` files)

```bash
asciinema rec tests/fixtures/acceptance-recordings/proven.cast -c "bash scripts/demos/proven.sh"
asciinema rec tests/fixtures/acceptance-recordings/unknown.cast -c "bash scripts/demos/unknown.sh"
asciinema rec tests/fixtures/acceptance-recordings/live-edit.cast -c "bash scripts/demos/live-edit.sh"
```

The recording is a one-shot ceremony step — commit the resulting `.cast`
files and update `docs/acceptance/demos.md` only when the run commands or
recording paths change. CI does NOT re-record.
```

- [ ] **Step 2: Write the three ceremony scripts**

```bash
# scripts/demos/proven.sh
#!/usr/bin/env bash
# Amended 2026-05-26 per Plan 11 sync→async pivot: POST /v1/reindex
# returns 202 + Snapshot(PENDING); poll GET /v1/reindex/{sha} until
# status terminal (applied|failed).
set -euo pipefail

echo "=== Slice acceptance ceremony: PROVEN path ==="
echo "Query: explain_symbol codeoracle.services.verifier.service.VerifierService.verify"
echo

echo "Step 1: enqueue reindex (POST returns 202 + PENDING)"
SNAPSHOT_SHA=$(curl -fsS -X POST http://127.0.0.1:8080/v1/reindex \
    -H "Content-Type: application/json" \
    -d '{"base_sha": null}' \
    | python -c "import sys,json;print(json.load(sys.stdin)['sha'])")
echo "  enqueued snapshot: $SNAPSHOT_SHA"
echo

echo "Step 2: poll GET /v1/reindex/{sha} until APPLIED"
while true; do
    STATUS=$(curl -fsS "http://127.0.0.1:8080/v1/reindex/$SNAPSHOT_SHA" \
        | python -c "import sys,json;print(json.load(sys.stdin)['status'])")
    echo "  status: $STATUS"
    case "$STATUS" in
        applied) break ;;
        failed)
            echo "  reindex FAILED — aborting ceremony" >&2
            exit 1
            ;;
    esac
    sleep 2
done
echo "  reindex APPLIED."
echo

echo "Step 3: query via POST /v1/query"
curl -fsS -X POST http://127.0.0.1:8080/v1/query \
    -H "Content-Type: application/json" \
    -d '{"type":"explain_symbol","symbol":"codeoracle.services.verifier.service.VerifierService.verify","corpus":"self","snapshot_sha":null}' \
    | python -m json.tool

echo
echo "=== Expected: verdict.status == PROVEN, >=3 citations spanning >=2 handle types ==="
```

```bash
# scripts/demos/unknown.sh
#!/usr/bin/env bash
# Amended 2026-05-26 per Plan 11 sync→async pivot — see proven.sh.
set -euo pipefail

echo "=== Slice acceptance ceremony: UNKNOWN path ==="
echo "Query: explain_symbol codeoracle.services.deleted.NotARealService"
echo

echo "Step 1: enqueue + poll reindex to APPLIED"
SNAPSHOT_SHA=$(curl -fsS -X POST http://127.0.0.1:8080/v1/reindex \
    -H "Content-Type: application/json" \
    -d '{"base_sha": null}' \
    | python -c "import sys,json;print(json.load(sys.stdin)['sha'])")
echo "  enqueued snapshot: $SNAPSHOT_SHA"
while true; do
    STATUS=$(curl -fsS "http://127.0.0.1:8080/v1/reindex/$SNAPSHOT_SHA" \
        | python -c "import sys,json;print(json.load(sys.stdin)['status'])")
    case "$STATUS" in
        applied) break ;;
        failed)
            echo "  reindex FAILED — aborting ceremony" >&2
            exit 1
            ;;
    esac
    sleep 2
done
echo "  done."
echo

echo "Step 2: query a symbol that does NOT exist in src/codeoracle/"
curl -fsS -X POST http://127.0.0.1:8080/v1/query \
    -H "Content-Type: application/json" \
    -d '{"type":"explain_symbol","symbol":"codeoracle.services.deleted.NotARealService","corpus":"self","snapshot_sha":null}' \
    | python -m json.tool

echo
echo "=== Expected: verdict.status == UNKNOWN, reason contains 'symbol not found' ==="
```

```bash
# scripts/demos/live-edit.sh
#!/usr/bin/env bash
# Amended 2026-05-26 per Plan 11 sync→async pivot — see proven.sh.
set -euo pipefail

reindex_and_wait() {
    # POST /v1/reindex with the given base_sha, then poll until APPLIED.
    # Echoes the terminal snapshot_sha on stdout; aborts on FAILED.
    local base_sha_json="$1"
    local sha
    sha=$(curl -fsS -X POST http://127.0.0.1:8080/v1/reindex \
        -H "Content-Type: application/json" \
        -d "{\"base_sha\": $base_sha_json}" \
        | python -c "import sys,json;print(json.load(sys.stdin)['sha'])")
    while true; do
        local status
        status=$(curl -fsS "http://127.0.0.1:8080/v1/reindex/$sha" \
            | python -c "import sys,json;print(json.load(sys.stdin)['status'])")
        case "$status" in
            applied) break ;;
            failed)
                echo "Reindex $sha FAILED" >&2
                return 1
                ;;
        esac
        sleep 2
    done
    echo "$sha"
}

echo "=== Slice acceptance ceremony: live-edit path ==="
echo

echo "Step 1: full reindex from current HEAD (POST + poll)"
SHA_BEFORE=$(reindex_and_wait null)
echo "  baseline snapshot: $SHA_BEFORE"
echo

echo "Step 2: edit src/codeoracle/services/verifier/service.py (single-line whitespace tweak)"
sed -i.bak 's/def verify/def verify  /' src/codeoracle/services/verifier/service.py
echo "  edit applied."
echo

echo "Step 3: incremental reindex against new HEAD (POST + poll)"
SHA_AFTER=$(reindex_and_wait "\"$SHA_BEFORE\"")
echo "  new snapshot: $SHA_AFTER"
echo

echo "Step 4: query at the new snapshot"
curl -fsS -X POST http://127.0.0.1:8080/v1/query \
    -H "Content-Type: application/json" \
    -d "{\"type\":\"explain_symbol\",\"symbol\":\"codeoracle.services.verifier.service.VerifierService.verify\",\"corpus\":\"self\",\"snapshot_sha\":\"$SHA_AFTER\"}" \
    | python -m json.tool

echo
echo "Step 5: revert the edit (restore .bak)"
mv src/codeoracle/services/verifier/service.py.bak src/codeoracle/services/verifier/service.py
echo "  reverted."
echo

echo "=== Expected: snapshot_sha in response == $SHA_AFTER (reflects post-edit state) ==="
```

- [ ] **Step 3: Run each script against the live stack to confirm it works**

```bash
chmod +x scripts/demos/*.sh
bash scripts/demos/proven.sh
bash scripts/demos/unknown.sh
bash scripts/demos/live-edit.sh
```

Expected: each script completes with the expected verdict + a clean stdout.

- [ ] **Step 4: Record the asciinema casts (manual one-shot step)**

```bash
asciinema rec tests/fixtures/acceptance-recordings/proven.cast -c "bash scripts/demos/proven.sh"
asciinema rec tests/fixtures/acceptance-recordings/unknown.cast -c "bash scripts/demos/unknown.sh"
asciinema rec tests/fixtures/acceptance-recordings/live-edit.cast -c "bash scripts/demos/live-edit.sh"
```

- [ ] **Step 5: ShellCheck the scripts**

```bash
shellcheck scripts/demos/*.sh
```

Expected: zero findings.

- [ ] **Step 6: Commit**

```bash
git add scripts/demos/*.sh tests/fixtures/acceptance-recordings/*.cast docs/acceptance/demos.md
git commit -m "feat(slice): §8.7 ceremony scripts + asciinema recordings

Three demo scripts drive the PROVEN / UNKNOWN / live-edit ceremony paths against the live compose stack. The .cast files are one-shot human-recorded asciinema artifacts committed alongside the scripts; CI validates they parse + contain the canonical verdict line but does NOT re-record.

docs/acceptance/demos.md explains how to run the demos locally and how to re-record (manual ceremony step).

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

## Task 11: §6 EDIT — drop `traces.query` from spec §8.5 gate 9

**Files:**
- Modify: `docs/superpowers/specs/2026-05-21-platform-scaffolding-design.md` (single bullet edit)

**Goal:** Apply the §6 EDIT amending spec §8.5 observability gate 9 to drop the `traces.query` span from the required child-span list. Per P0-7 / D-10.1, Plan 10 dropped the `traces_query` LangGraph node; the gate must mirror that or fail every test run.

- [ ] **Step 1: Locate the current gate 9 line by content (NOT by hardcoded line number)**

```bash
grep -nE "traces\.query.*llm\.compose|llm\.compose.*traces\.query" \
    docs/superpowers/specs/2026-05-21-platform-scaffolding-design.md
```

Expected: exactly one match — gate 9 of §8.5 — surfacing the actual line number for this checkout. Hardcoding a line number is brittle because the spec has been edited multiple times since the corpus-wide audit; the content match is stable across edits.

If the grep returns zero matches, the gate 9 amendment has already been applied OR the spec text has materially diverged from the audit's expected shape. STOP and surface to the user before proceeding.

If the grep returns more than one match, the spec contains multiple `traces.query` references — open the file at each hit and identify which one is gate 9 of §8.5. Apply the §6 EDIT only to that line.

- [ ] **Step 2: Apply the §6 EDIT (drop `traces.query,` from the gate-9 list)**

Use the `Edit` tool with the verified `old_string` matching the actual current text (capture the exact bullet from Step 1's output). The replacement removes the `traces.query,` token and appends the amendment-citation prose:

Old (verbatim from the grep hit, including the leading list-marker and indentation):
```
9. Every `/v1/query` emits one OTel root span with child spans: `intent.parse`, `graph.query`, `embed.query`, `traces.query`, `llm.compose`, `verifier.verify`, `resolve.<handle_type>` (per citation).
```

New:
```
9. Every `/v1/query` emits one OTel root span with child spans: `intent.parse`, `graph.query`, `embed.query`, `llm.compose`, `verifier.verify`, `resolve.<handle_type>` (per citation). _Amended Plan 13 (2026-05-26): `traces.query` removed because Plan 10 Deviation 1 dropped the `traces_query` LangGraph node — the agent no longer emits a `traces.query` span, so requiring it would fail every test run forever. See plan-audit-findings P0-7 / D-10.1._
```

- [ ] **Step 3: Verify the spec mutation parses + lints cleanly**

```bash
npx --yes markdownlint-cli2 docs/superpowers/specs/2026-05-21-platform-scaffolding-design.md 2>&1 | head -5
```

Expected: no NEW markdownlint findings; existing pre-existing MD013 long-line warnings are unchanged.

- [ ] **Step 4: Commit**

```bash
git add docs/superpowers/specs/2026-05-21-platform-scaffolding-design.md
git commit -m "docs(spec): §8.5 gate 9 amendment — drop traces.query span

Plan 10 Deviation 1 dropped the traces_query LangGraph node so the agent no longer emits a traces.query OTel child span. The §8.5 gate 9 required-child-span list must mirror that or fail every test run forever. Per audit row P0-7 / D-10.1; cross-referenced from Plan 13 invariant 5 + decision DECIDE-R.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

## Task 12: `.github/workflows/slice-acceptance.yml`

**Files:**
- Create: `.github/workflows/slice-acceptance.yml` (~140 lines — one workflow, one reusable-workflow gate call + one compose-up/integration job)

**Goal:** Single GitHub-Actions workflow that runs every §8.5 gate. Triggered on every PR + on push to main. The architectural-invariant suite runs via one reusable-workflow call of Plan 1's `ci.yml` (all architectural-invariant gates surface as individual child jobs for branch protection). One sequential `slice-integration` job brings up the compose stack, runs the nine integration tests, asserts the three observability gates, and emits the perf-report.json artifact.

- [ ] **Step 1: Write the workflow**

```yaml
# .github/workflows/slice-acceptance.yml
#
# Slice acceptance — verifies every §8.5 gate the corpus pins. Runs on
# every PR and on push to main. See spec §8.5 + Plan 13 Task 12 for
# the per-gate provenance.

name: slice-acceptance

on:
  pull_request:
    branches: [main]
  push:
    branches: [main]

jobs:

  # Reusable-call Plan 1's ci.yml — the structural / quality invariant suite
  # (lint, type, test, security, file-length-gate, + Plan 2's verification-pins +
  # Plan 3's migration smoke-test; 7 jobs corpus-wide). Invoking the whole workflow
  # keeps the gate-set DRY, and its child jobs surface individually for branch
  # protection. The SAST + image-scan gates (security.yml: gitleaks/semgrep/dep-
  # audit/SBOM; image-build.yml: Trivy image ×5) are NOT re-invoked here — they are
  # not workflow_call-able (pull_request/push triggers only) and run on the slice PR
  # as required status checks via their own triggers, per spec §8.7's "CI green on
  # the slice PR (all §8.5 gates)". (ci.yml also runs standalone on PRs per Plan 1's
  # invariant; the duplicate fast-gate run is the accepted cost of a self-contained
  # acceptance ceremony — optimize later via concurrency groups if needed.)
  architectural-invariants:
    uses: ./.github/workflows/ci.yml

  # ---- One sequential compose-up + integration job ----

  slice-integration:
    runs-on: ubuntu-latest
    needs: [architectural-invariants]
    timeout-minutes: 25
    steps:
      - uses: actions/checkout@v4

      - name: Install uv + Python deps
        run: |
          curl -LsSf https://astral.sh/uv/install.sh | sh
          ~/.local/bin/uv sync --frozen

      - name: Build E2E images
        run: |
          docker compose -f infra/compose/docker-compose.yml \
                         -f infra/compose/docker-compose.e2e.yml \
                         build

      - name: Bring up the E2E stack
        run: |
          docker compose -f infra/compose/docker-compose.yml \
                         -f infra/compose/docker-compose.e2e.yml \
                         up -d

      - name: Wait for stack health
        run: |
          for i in $(seq 1 60); do
            if curl -fsS http://127.0.0.1:8080/v1/health \
               && curl -fsS -X POST -H 'Content-Type: application/json' \
                    --data '{"jsonrpc":"2.0","id":0,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"healthcheck","version":"0"}}}' \
                    http://127.0.0.1:8082/mcp; then
              echo "Stack healthy."
              exit 0
            fi
            sleep 2
          done
          echo "Stack failed to become healthy within 120s."
          docker compose -f infra/compose/docker-compose.yml \
                         -f infra/compose/docker-compose.e2e.yml \
                         logs --tail=200
          exit 1

      - name: Run the 9 slice integration tests
        run: ~/.local/bin/uv run pytest tests/integration/slice/ -v -m slice

      - name: Assert observability gate 9 — required child spans present
        run: |
          ~/.local/bin/uv run python scripts/assert_slice_spans.py \
            --required intent.parse,graph.query,embed.query,llm.compose,verifier.verify

      - name: Assert observability gate 10 — self-traces ingested
        run: |
          ~/.local/bin/uv run python scripts/assert_self_traces_ingested.py

      - name: Assert observability gate 11 — verifier per-resolution log
        run: |
          docker compose -f infra/compose/docker-compose.yml \
                         -f infra/compose/docker-compose.e2e.yml \
                         logs api | \
            grep -c '"event":"verifier.citation_' | \
            awk '{if($1<1){print "FAIL"; exit 1} else {print "OK ("$1" log lines)"}}'

      - name: Validate ceremony .cast files
        run: |
          for cast in tests/fixtures/acceptance-recordings/*.cast; do
            head -1 "$cast" | grep -q '"version": 2' || {
              echo "FAIL: $cast is not a valid asciinema v2 recording."
              exit 1
            }
          done

      - name: Upload perf-report.json
        if: always()
        uses: actions/upload-artifact@v4
        with:
          name: perf-report
          path: perf-report.json
          if-no-files-found: ignore

      - name: Post perf summary to PR
        if: github.event_name == 'pull_request'
        run: |
          if [ -f perf-report.json ]; then
            ~/.local/bin/uv run python scripts/post_perf_comment.py \
              --report perf-report.json \
              --pr ${{ github.event.pull_request.number }}
          fi

      - name: Tear down compose stack
        if: always()
        run: |
          docker compose -f infra/compose/docker-compose.yml \
                         -f infra/compose/docker-compose.e2e.yml \
                         down -v
```

- [ ] **Step 2: Add the supporting scripts**

The workflow references three small Python scripts. Create stubs that the executor fills in:

- `scripts/assert_slice_spans.py` — queries Tempo's HTTP API for the most recent root span and asserts every name in `--required` appears as a child. Implementation: ~40 lines, uses `httpx` + Tempo's `/api/traces/{trace_id}` endpoint.
- `scripts/assert_self_traces_ingested.py` — opens an `asyncpg` connection to the compose Postgres at host-port 5432 and runs `SELECT COUNT(*) FROM traces` and `SELECT COUNT(*) FROM trace_spans` (Plan 7 schema), asserting both counts are > 0. NOT an admin HTTP endpoint per DECIDE-U. Implementation: ~25 lines.
- `scripts/post_perf_comment.py` — reads `perf-report.json`, formats a Markdown comment, posts via `gh pr comment`. Implementation: ~30 lines.

Their full implementations are pinned in Task 12's commit; this plan delegates the implementation per-script to the executor (they're CI glue, not load-bearing platform code).

- [ ] **Step 3: Lint the workflow YAML**

```bash
npx --yes yaml-lint .github/workflows/slice-acceptance.yml
# Or: actionlint if installed
actionlint .github/workflows/slice-acceptance.yml
```

Expected: zero findings.

- [ ] **Step 4: Commit**

```bash
git add .github/workflows/slice-acceptance.yml scripts/assert_slice_spans.py scripts/assert_self_traces_ingested.py scripts/post_perf_comment.py
git commit -m "feat(slice): slice-acceptance CI workflow

One workflow runs every §8.5 gate: the architectural-invariant suite via one reusable-workflow call of Plan 1's ci.yml (its child jobs surface individually for branch protection) + 1 sequential compose-up job that hosts the 9 integration tests + 3 observability assertions + the perf measurement. Stack is brought up once per workflow run (DECIDE-Q: avoid per-gate compose-startup cost). The SAST + image-scan gates (security.yml, image-build.yml) are NOT workflow_call-able (pull_request/push triggers only) and run on the slice PR as required status checks via their own triggers, per spec §8.7.

Three supporting scripts: assert_slice_spans.py queries Tempo for the required child-span set; assert_self_traces_ingested.py asserts the platform's own traces table is populated (gate 10); post_perf_comment.py emits the per-PR perf summary from perf-report.json (DECIDE-T: single-run measurement, not gated).

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

## Task 13: Acceptance demo documentation

**Files:**
- Update: `docs/acceptance/demos.md`

**Goal:** Keep the runnable demo commands and recording paths current. This file is not a sign-off ledger.

- [ ] **Step 1: Verify the document names the active locations**

```text
scripts/demos/proven.sh
scripts/demos/unknown.sh
scripts/demos/live-edit.sh
tests/fixtures/acceptance-recordings/proven.cast
tests/fixtures/acceptance-recordings/unknown.cast
tests/fixtures/acceptance-recordings/live-edit.cast
```

- [ ] **Step 2: Commit**

```bash
git add docs/acceptance/demos.md
git commit -m "docs(slice): acceptance demo instructions"
```

---

## Task 15: Closure notes + Phase G handoff prompt

**Files:**
- Update: this plan document

**Goal:** Keep closure notes and cross-plan dependency handoff details in this plan, without recreating a tracked state directory.

- [ ] **Step 1: Record verification evidence in the plan body**

Capture the slice workflow run, test disposition, demo recording validation, and any Phase G handoff notes in the relevant sections of this plan.

- [ ] **Step 2: Commit**

```bash
git add docs/superpowers/plans/2026-05-21-e2e-and-slice-acceptance.md
git commit -m "docs(plan-13): closure notes and handoff"
```

---

## §8.5 acceptance gate

Plan 13's own §8.5 row: this plan ships ONLY if every gate in spec §8.5 passes against the live compose stack at the time of the ceremony. Demo procedure documentation is recorded in `docs/acceptance/demos.md`; closure notes stay in this plan.

| Gate (spec §8.5) | Verified by | Plan 13 task |
|------------------|-------------|--------------|
| Functional 1 | `test_explain_existing_symbol_returns_proven_with_citations` | Task 3 |
| Functional 2 | `test_explain_deleted_symbol_returns_unknown` | Task 4 |
| Functional 3 | `test_verifier_returns_refuted_when_excerpt_mismatches_source` (verifier-integration, sibling file `test_slice_verifier_refuted.py`) | Task 5 |
| Functional 4 | `test_mcp_explain_symbol_matches_api_response` | Task 6 |
| Functional 5 | `test_reindex_endpoint_produces_new_snapshot` | Task 7 |
| Incremental 6 | `test_incremental_reindex_matches_full_reindex` | Task 8 |
| Incremental 7 | `test_incremental_reindex_invalidates_dependents` | Task 8 |
| Incremental 8 | `test_incremental_reindex_performance` (MEASURED) | Task 8 |
| Intent-classifier | `test_intent_classifier_never_produces_unsupported_handle_types` | Task 9 |
| Architectural 1-8 | `slice-acceptance.yml` parallel jobs | Task 12 |
| Observability 9 (amended) | `scripts/assert_slice_spans.py` | Task 12 |
| Observability 10 | `scripts/assert_self_traces_ingested.py` | Task 12 |
| Observability 11 | `docker logs api \| grep verifier.citation_` | Task 12 |
| Performance (measured) | `perf-report.json` artifact + PR comment | Tasks 8 + 12 |
| §8.7 ceremony | 3 `.cast` files + acceptance demo documentation | Tasks 10 + 13 |

---

## §6 EDIT table — pre-pivot misattributions + Plan 13 modifications

| ID | Target | Edit | Rationale |
|----|--------|------|-----------|
| P0-7 / D-10.1 | `docs/superpowers/specs/2026-05-21-platform-scaffolding-design.md` L1442 | Drop `traces.query` from the gate-9 required-child-span list; append the amendment-citation prose | Plan 10 Deviation 1 dropped the `traces_query` LangGraph node. Without this edit, gate 9 fails every test run forever. Applied in Plan 13 Task 11 |
| D-6.5 | (in-plan) Plan 13 Tasks 7 + 8 cover all 4 extractors | Task 7's `EXPECTED_EXTRACTORS = {"joern", "scip", "opengrep", "embed"}` set + per-extractor SUCCESS assertion is the 4-extractor live E2E that audit row D-6.5 pushed here. No spec mutation required | |
| D-9.3 / M-9.1 | (in-plan) Plan 13 Task 12 observability gate 11 | Plan 13's gate 11 step grep-asserts a `verifier.citation_` outcome event (Plan 9 emits `verifier.citation_resolved` / `verifier.citation_unresolved` / `verifier.citation_excerpt_mismatch` — the prefix match catches whichever fires) in api-container logs. The check runs under the workflow's configured log level — the gate-11 acceptance hinges on Plan 9 emitting at a level visible at the test-run verbosity. Plan 9's existing structured log line at `.info` is sufficient because the slice-acceptance workflow runs the API process with `CODEORACLE_LOG_LEVEL=INFO` (set via the compose overlay) | |
| D-10.U3 | (in-plan) Every Plan 13 integration test runs against `MockLLMProvider` + `tests/fixtures/llm_scripts/` | Plan 13 invariant 2 enforces this. The four new fixture YAMLs are checked in alongside their tests | |
| Plan-13-amendment-2026-05-26 (DECIDE-V) | (in-plan) Tasks 2 (`_helpers.py`), 3 (`_ensure_indexed`), 7 (gate 5), 8 (gates 6-8 helpers), 9 (intent-classifier setup), 10 (§8.7 ceremony scripts); §Cross-plan-deps Plan 5 + Plan 11 rows + reciprocal-row catalogue Plan 5 + Plan 11 entries; §Decisions DECIDE-V added; this row added; §Invariants conftest fixture docstring at Task 2 Step 2 (Settings fixture); §Goal of Task 7 + §Goal of Task 8 amended | Plan 11 amendment `fc0894a` 2026-05-26 pivoted POST /v1/reindex from sync (200 + body) to async (202 + Snapshot(PENDING) + GET /v1/reindex/{sha} polling). Plan 13's gates that drive reindex (5, 6, 7, 8 incremental, intent-classifier setup) MUST speak the new POST + poll contract or the cold-3K-LOC reindex (~60-90s) will 504 inside uvicorn's keep-alive window. The shared driver `drive_reindex_to_applied` lives in `_helpers.py` so the 180s polling budget and 1.0s interval change in one place INSIDE PLAN 13. Cross-plan polling-driver duplication (Plan 11's committed inline 0.5s/180s helper + Plan 13's 1.0s/180s helper + §8.7 ceremony shell 2s sleep) is documented in DECIDE-V "Cross-plan duplication acknowledged" — consolidation deferred to Slice+1. DAG-order amendment chain: Plan 3 amendment `7a82190` (IndexingConfig nested Settings) → Plan 5 amendment `40885bf` (enqueue_reindex + ReindexOrchestratorConsumer) → Plan 11 amendment `fc0894a` (sync→async route pivot) → Plan 13 (this amendment). Drives entirely from user P0 (2026-05-26 binding): the small-repository assumption was a gap because the platform is intended to work with large real-world repositories, making synchronous reindexing inappropriate. | |
| KNOWN-PRE-EXISTING (Plan 13 Task 1 Dockerfile gap) — **RESOLVED in Plan 13 amendment #49 (2026-05-26)** | (in-plan) Task 1 Step 2 — pre-#49 `infra/compose/docker-compose.e2e.yml` referenced `infra/docker/Dockerfile.api` (L368), `Dockerfile.mcp` (L403), `Dockerfile.agent` (L440), `Dockerfile.worker` (L472), AND defined a single generic `worker:` service with no `command:` override | **Predated the 2026-05-26 sync→async amendment** — this gap was present at Plan 13's initial draft. (a) The four per-variant Dockerfiles were created by NO plan in the corpus — only the unified `infra/docker/Dockerfile` (Plan 1A Task 8) exists. (b) The single generic `worker:` service container could not run 5 distinct consumer processes (joern_indexer + scip_indexer + opengrep_runner + embed_indexer + reindex_orchestrator from Plan 5 Task 13a amendment `40885bf`); one container = one PID 1. The 2026-05-26 sync→async amendment compounded the gap by adding the `reindex_orchestrator` consumer as a NEW required process. **Disposition:** RESOLVED in amendment #49 (see new row immediately below + DECIDE-W). #49 rewrote the overlay to consume Plan 1A's unified `infra/docker/Dockerfile` via a shared `x-codeoracle-build:` YAML anchor (Pattern Y) and split the generic `worker:` service into 5 process-distinct services. **Correction of pre-#49 stale claim:** an earlier draft of this row asserted that Plan 1A's `entrypoint.sh` executes `python -m "codeoracle.${CODEORACLE_VARIANT}"` against a "flat namespace incompatible with Plan 5/6's nested `codeoracle.workers.<consumer>` modules"; that claim was FALSE — shell parameter expansion in entrypoint.sh accepts ANY dotted module path, so `CODEORACLE_VARIANT=workers.joern_indexer` produces `python -m codeoracle.workers.joern_indexer` entirely as designed. No Plan 1A amendment was needed; the contract committed in Plan 1A Task 8 is general enough as-is. The earlier "decide whether to amend entrypoint.sh to accept a WORKER_MODULE env var" speculation is moot under Pattern Y (CODEORACLE_VARIANT IS the env var). | |
| Plan-13-amendment-#49-2026-05-26 (Task 1 Dockerfile + worker-services resolution; DECIDE-W) | (in-plan) Task 1 Step 2 — `infra/compose/docker-compose.e2e.yml` rewritten to 8 services consuming Plan 1A's unified `infra/docker/Dockerfile` via a shared `x-codeoracle-build:` YAML anchor (Pattern Y dispatch — single shared image + per-service `environment.CODEORACLE_VARIANT` runtime override); MCP service's `command:` corrected from `["codeoracle-mcp", "--http"]` to argv-tail-only `["--http"]` (entrypoint.sh supplies the `python -m codeoracle.mcp` prefix); `reindex_orchestrator:` service `depends_on` the 4 per-extractor consumers being healthy first (Plan 5 Task 13a amendment `40885bf` semantics); worker healthchecks share a `x-worker-healthcheck:` YAML anchor with a procps-free PID-1 probe (`test -f /proc/1/comm && grep -q '^python' /proc/1/comm`) since the slim base `python:3.14-slim-bookworm` (Plan 1A Dockerfile L1401) omits procps; §Decisions DECIDE-W added documenting Pattern Y vs Pattern X choice + steel-manned alternative; §Invariants Invariant 12 amended (8 services, not 4) + Invariant 13 added (Pattern Y dispatch scoped to `docker-compose.e2e.yml`); §Cross-plan-deps rows for Plans 1A / 5 / 6 / 10 / 11 / 12 + reciprocal-row catalogue entries for the same plans updated to name the variant-dispatch language (dispatch correctly cites Plan 1A's `entrypoint.sh`); the curl micro-change to Plan 1A's Dockerfile is recorded in the cross-plan-deps import + reciprocal rows (the rest of Plan 1A's Task 8 contract is consumed as-committed); pre-#49 §6 EDIT row above marked RESOLVED. Step 6 commit-message preview updated for the 8-service shape. | Resolves the KNOWN-PRE-EXISTING gap surfaced at Plan 13 initial draft `fd4945b`. (a) The four non-existent Dockerfile paths replaced by Plan 1A's unified Dockerfile via build anchor. (b) The single `worker:` service split into 5 process-distinct services (joern_indexer + scip_indexer + opengrep_runner + embed_indexer + reindex_orchestrator) — one PID 1 per consumer per the worker-framework contract. (c) Pattern Y dispatch (per-service env override) wins over Pattern X (per-variant build args) on build amortization (1 vs 8 image builds per session) + tag-surface minimization (1 vs 8 image tags) + entrypoint.sh contract respect (Plan 1A's `exec python -m "codeoracle.${CODEORACLE_VARIANT}" "$@"` IS the dispatcher, used as designed). Full reasoning in DECIDE-W. (d) The MCP `command:` correction restores the intended `python -m codeoracle.mcp --http` PID-1 — entrypoint.sh supplies the executable prefix; `command:` is argv tail only. (e) Cross-plan-deps + reciprocal-row catalogue entries added for Plans 1A / 5 / 6 / 10 / 11 / 12 naming the variant-dispatch language, for the Phase G (Task #41) mechanical sweep to apply across the corpus. (f) Worker healthchecks switched from `pgrep -f` (would have failed at compose-up — procps not in slim base) to a procps-free `/proc/1/comm` probe via shared `x-worker-healthcheck:` YAML anchor; chose the kernel-procfs probe over installing procps in Plan 1A's Dockerfile because workers don't need any external utility (PID 1 = python is structurally guaranteed by entrypoint.sh's `exec` pattern; kernel /proc exposes it free). Surfaced by advisor pre-commit review (second-pass finding). (g) HTTP healthchecks (api/mcp/agent) would also have failed at compose-up — `python:3.14-slim-bookworm` omits curl. Folded a curl install into Plan 1A's runtime-base stage (single `RUN apt-get install` line, ~1.5 MB layer cost) rather than rewriting 3 healthchecks to use a curl-free Python probe — for HTTP services curl is the idiomatic probe (especially the MCP JSON-RPC body, which is much cleaner in curl than via `python -c "import http.client; ..."`), so installing it earns its layer cost. Surfaced by advisor pre-commit review (third-pass finding — same class of bug as (f), opposite resolution because HTTP services genuinely need an HTTP client while workers can use kernel /proc). **Scope honesty:** the curl install lives in `runtime-base`, so `cli`/`agent`/`workers` images also ship curl — accepted as unified-image dependency-creep cost (variant-specific runtime stages would violate Plan 1A Task 8's one-Dockerfile invariant; the ~1.5 MB layer cost is paid once at image build, not per-variant, because Pattern Y collapses all 8 services onto the shared `codeoracle:slice-e2e` tag). (h) Open Plan 12 dependency: the MCP healthcheck POSTs `initialize` every 5s, which creates a fresh JSON-RPC session per probe. Plan 12 owns the verdict — the HTTP transport must handle repeated stateless initializes without leaking per-connection session state, otherwise the probe should be reshaped to a single `initialize` + `tools/list` round-trip held inside one HTTP connection. Not blocking slice ceremony (~360 abandoned sessions over a 30-minute ceremony is acceptable), but flagged here for Slice+1 long-running staging where the leakage could compound. Drives entirely from the Plan 13 initial draft's deferred gap + the Plan 11 sync→async amendment's addition of `reindex_orchestrator` as a NEW required process — both surfaced through the generalization finding that small-repository assumptions are invalid for a platform intended to work with large real-world repositories. DAG-order amendment chain (extended): Plan 3 amendment `7a82190` (IndexingConfig nested Settings) → Plan 5 amendment `40885bf` (enqueue_reindex + ReindexOrchestratorConsumer) → Plan 11 amendment `fc0894a` (sync→async route pivot) → Plan 13 initial draft `fd4945b` → Plan 11 micro-amendment `2c72735` (RetryPolicy import path) → Plan 13 amendment #49 (this commit). | |
| Plan-13-Phase-G-G-0-2026-05-30 (cross-plan-deps re-attribution) | (in-plan) §Cross-plan-deps — the **Plan 1A** import row + reciprocal-row catalogue Plan 1A entry absorbed the unified `infra/docker/Dockerfile` + `entrypoint.sh` + base `infra/compose/docker-compose.yml` + the three image/SAST CI jobs (trivy/osv/semgrep) + the #49 curl micro-change; the **Plan 2** import + catalogue rows rewritten as explicit documented non-imports (Plan 13 imports nothing directly from Plan 2). `operational-infrastructure.md` (Plan 1A) L47 Provides row converged to the corrected catalogue (observability half folded in; `preflight.sh` dropped; CI attribution corrected to Plan 1 gates 1-5 + Plan 1A trivy/osv/semgrep). ~20 inline Dockerfile/entrypoint owner relabels Plan 2 → Plan 1A across Invariant 13, DECIDE-W, the compose-header comments, and the two §6 rows above. | Amendment #49's cross-plan-deps draft filed Plan-1A-owned artifacts (the unified Dockerfile, `entrypoint.sh`, base compose, and the 3 image/SAST CI jobs) under a 'Plan 2' import + reciprocal row — a semantic collision, since 'Plan 2 Task 8' canonically = 'Verify Sourcebot image' (Plan 2 L784), NOT the Dockerfile (Plan 1A Task 8). Phase G (Task #41) is a corpus-wide cross-plan-deps refresh; correcting this source-of-truth catalogue BEFORE propagation prevents Phase G from broadcasting the mis-attribution into every target plan. Verified ownership by cross-corpus grep: unified Dockerfile + entrypoint.sh + base compose = Plan 1A Task 8; trivy/osv/semgrep = Plan 1A Task 12; ruff/ty/pytest/check_file_length (gates 1-5) = Plan 1; `preflight.sh` (Plan 2 Task 12) + verification-pins (Plan 2 Task 13, the 9th CI job) are NOT consumed by any Plan 13 task. Pre-existing base-compose/CI body-prose mis-attribution (distinct from these cross-plan-deps rows) + 6 corpus-wide MD056 table-render breaks (Plan 8 L3588/3590, Plan 12 L70/71/73, Plan 13 L3057 — unescaped pipe characters inside code-span table cells) deferred to Phase I (Task #42) full-corpus crosscheck as a uniform sweep. | |

---

## Self-review

A nine-pass self-review per the writing-plans skill (extended past the standard five to cover the advisor-round-1 findings: forward-ref resolution, invariant-vs-implementation, runtime networking, fixture failure modes):

1. **Spec coverage:** Every §8.5 row maps to a task: 9 functional tests → Tasks 3-9; the architectural-invariant gates → Task 12's reusable-workflow call of Plan 1's `ci.yml` (its child jobs cover spec §8.5 invariants 1-5 directly; the SAST + image-scan gates run on the slice PR via `security.yml` + `image-build.yml`'s own triggers as required checks); 3 observability → Task 12 steps; 4 perf measurements → Task 8 + Task 12 artifact. §8.7 ceremony → Tasks 10 + 13. §6 EDIT for gate 9 → Task 11. No gap identified.

2. **Placeholder scan:** `PLACEHOLDER_*` fingerprint strings in the YAML fixtures are intentional and documented (Task 3 Step 1's NOTE explains the fingerprint-paste recipe). All other code blocks contain executable content.

3. **Type consistency:** `Snapshot.status`, `SnapshotStatus.PENDING / APPLYING / APPLIED / FAILED`, `Snapshot.error: str | None`, `QueryResult.verdict.status`, `VerdictStatus.PROVEN / UNKNOWN / REFUTED`, `Handle.type`, `ExtractorOutcome.SUCCESS` — every type reference matches Plan 3's canonical definitions (verified against Plan 3 L1883 `class SnapshotStatus(str, Enum)` + L1887 `FAILED = "failed"` + L1923 `error: str | None = None` and against Plan 3 L1890 + L1922 — `class ExtractorOutcome(str, Enum)` + `extractor_results: dict[str, ExtractorOutcome] = Field(default_factory=dict)`). The async helper `drive_reindex_to_applied` exercises the full lifecycle (POST returns one of PENDING|APPLYING|APPLIED; poll loop sees APPLYING transitions; terminal branches on APPLIED-vs-FAILED with `final.error!r` rendering on FAILED).

4. **Cross-plan inheritance:** Every "Plan 13 imports from Plan X" row in the dep table has a real artifact citation. No "Plan X provides Y" line is invented.

5. **DECIDE coverage:** Nine decisions documented (DECIDE-N through DECIDE-V — DECIDE-V added 2026-05-26 per Plan 11 sync→async pivot). Every load-bearing choice (test harness, demo location, target symbol, CI shape, spec amendment mechanism, citation floor, perf measurement, test-orchestration mechanism, reindex async POST + poll contract + shared `_helpers.drive_reindex_to_applied` placement) has a stated rationale + steel-manned alternative.

6. **Forward-reference resolution against Plans 9, 11, 12:**
   - **Plan 9 `Verdict` shape** (`docs/superpowers/plans/2026-05-21-verifier-and-trust-matrix.md`): `Verdict.resolved_citations: list[ResolvedCitation]`; `ResolvedCitation.resolved: bool`; `ResolvedCitation.matches_excerpt: bool`. There is NO `outcome` field and NO `MISMATCH` literal. REFUTED ⇔ at least one `ResolvedCitation` with `resolved is True AND matches_excerpt is False` (verified L1035-1041). Task 5 asserts that compound predicate, not a fictional `outcome == "MISMATCH"`.
   - **Plan 11 `POST /v1/query` + reindex contract**: `POST /v1/query` returns `QueryResult` synchronously (Plan 11 invariant 3 — model-validate the response body). `POST /v1/reindex` returns 202 + `Snapshot(status=PENDING|APPLYING|APPLIED)` per Plan 11 amendment `fc0894a` 2026-05-26; `GET /v1/reindex/{snapshot_sha}` returns 200 + `Snapshot` for polling. Plan 13 Tasks 3-9 round-trip query payloads via `Query.model_validate(payload)` + `QueryResult.model_validate(response.json())`; Tasks 7, 8, 9, and `_ensure_indexed` drive reindex via `_helpers.drive_reindex_to_applied` (POST + poll).
   - **Plan 12 explain_symbol handler return shape**: returns `QueryResult.model_dump(mode="json")` as a dict (Plan 12 L712: `assert result == canonical_query_result.model_dump(mode="json")`). Plan 12 does NOT pin the SDK's wire-marshalling decision (structuredContent vs content+TextContent). Task 6's `_extract_query_result_from_tool_result` handles both shapes so Plan 13 doesn't become brittle to SDK version drift.
   - **Plan 12 `--http` CLI flag**: Plan 12 L1981-1990 confirm `codeoracle-mcp --http` is the service-mode entrypoint flag.

7. **Invariant-vs-implementation contradiction check:** Invariant 1 commits Plan 13 to "no new production architectural surface." This rules out the test-only admin endpoints (`/v1/_test/reset`, `/v1/_test/stage_edit`, `/v1/_test/graph_snapshot`, `/v1/_test/set_sourcebot_url`, `/v1/_test/count_traces`) + the `CODEORACLE_API__ENABLE_TEST_RESET` settings flag that an earlier draft of the plan tacitly required. DECIDE-U documents the rejection and routes every test-orchestration concern to direct asyncpg (cleanup, graph-state snapshotting) or direct host-filesystem mutation (extractor-pipeline tests only — see pass 8). Task 5 (REFUTED gate) bypasses the API/agent layer entirely and calls `VerifierService.verify` against the live Sourcebot — that is the only deterministic way to drive REFUTED because Plan 10 Invariant 1 + Plan 9's contract make a mismatched-excerpt citation unreachable through the API/agent flow by construction. Tasks 2, 5, 8, 12 + the supporting scripts are aligned with the invariant.

8. **Runtime networking + I/O-source check:** Every fixture / test address pair is reachable from where it runs, and every "mutate X → observe Y" pairing must trace through a real I/O path that propagates X → Y in time.
   - `api_client` runs in the test process (on the host) → `127.0.0.1:8080` → reaches the overlay's published API port. OK.
   - `mcp_base_url` runs in the test process → `127.0.0.1:8082` → reaches the overlay's published MCP port. OK.
   - `db_conn` runs in the test process → `127.0.0.1:5432` → reaches the overlay's published Postgres port. OK.
   - Task 5 SourcebotClient runs in the test process → `127.0.0.1:3000` → reaches the overlay's published Sourcebot port. OK.
   - **Host-filesystem mutation propagation** (Task 8 incremental tests): `_swap_file_contents` writes to the bind-mounted `src/codeoracle/` tree. The Plan 5/6 extractors (Joern + SCIP + opengrep + embed) read source directly from that tree on each `IndexingService.reindex` call, so the mutation is observed on the next reindex. OK.
   - **Anti-pattern actively rejected** (advisor round-2 finding): host-filesystem mutation does NOT reach Plan 9's verifier in time. The verifier resolves citations via `CodeSpanResolver` → `SourcebotClient.fetch_span()` → HTTP to Sourcebot's Zoekt index, not the host filesystem. Zoekt's filesystem-watcher reindex is deployment-dependent and non-deterministic, so an earlier-draft Task 5 that mutated source-on-disk + queried via API would have been a coin flip masquerading as a gate. The current Task 5 calls `VerifierService.verify` directly with a hand-constructed Citation (no I/O propagation chain at all) — eliminates the failure mode.
   - No fixture binds to `127.0.0.1` and then expects a *container* to reach it — the earlier draft's tampering proxy made that mistake; DECIDE-U eliminates the proxy entirely.

9. **Fixture failure-mode check:**
   - `compose_stack` waits for every service to become healthy (`_all_endpoints_healthy`); times out hard after 180s. No silent-skip.
   - `db_conn` connection failure raises (asyncpg's `ConnectionDoesNotExistError` / `PostgresConnectionError`) — no silent-skip. Test session aborts.
   - `clean_snapshot` TRUNCATE / `drop_graph` failures raise — no silent-skip. The earlier draft's `pytest.skip()` on 404 was the silent-skip vacuous-pass bug; the new direct-asyncpg path has no skip surface.
   - `_swap_file_contents` (Task 8 only; Task 5 no longer uses it — see pass 8) raises `AssertionError` if the find-pattern isn't present (the file mutated unexpectedly before the test) — no silent-skip; restore-on-exception is guaranteed by the `try/finally`.
   - Task 5's verifier-integration assertions hard-fail on `resolved=False` (means Sourcebot did not index the cited file — a real bug to surface, never a skip) and on `matches_excerpt=True` (the deliberately-wrong excerpt should never match — a Plan-9 / Plan-8 contract bug to surface).
   - Asciinema validation (Task 12 final-step) `grep -c` runs with `awk` that fails-hard on count=0 — no silent-skip. (Note: per spec §8.5 "performance metrics are measured, not gated" — that's a deliberate non-gate, NOT a silent skip; PR comments still get posted.)
   - `drive_reindex_to_applied` (Task 2 `_helpers.py`, added in 2026-05-26 amendment) hard-fails three distinct ways — no silent-skip surface: (i) POST returning non-202 → `AssertionError(f"POST /v1/reindex expected 202, got {post_response.status_code} {post_response.text}")` immediately on the request thread; (ii) polling-budget exhaustion (180s with no terminal status reached) → `AssertionError` with the last observed status + the snapshot_sha; (iii) terminal status of FAILED → `AssertionError(f"Reindex {snapshot_sha} terminated FAILED: {final.error!r}")` with the worker-recorded error message. Cases (ii) and (iii) are explicit hard-fails — the helper never returns silently on an un-applied snapshot. The 180s budget exhausting is itself a real bug signal in slice scope (cold-3K-LOC reindex should complete in 60-90s per spec §8.5); a budget-exhaust failure means the worker is wedged or the extractors are non-trivially slower than the contract allows, both of which deserve a hard test failure.

10. **Event-loop-scope alignment for session-scoped async fixtures AND their consumers** (advisor round-2 + round-3 findings): pytest-asyncio (>= 0.21) defaults BOTH the fixture event-loop scope AND the test event-loop scope to `function`, even when `asyncio_mode = "auto"`. Round 2 caught the fixture-side default (`@pytest_asyncio.fixture(scope="session")` alone binds the connection to function-1's loop); round 3 caught the cascading consumer-side default — even if `db_conn` is decorated with `loop_scope="session"`, the *tests* and *function-scoped fixtures* that consume it (e.g., `clean_snapshot(db_conn)` in Task 2 and every async test under `tests/integration/slice/`) still default to the function loop, and the second function's first `await db_conn.execute(...)` raises `RuntimeError: got Future <...> attached to a different loop`. The fix lives at two layers, intentionally redundant:
    - **Corpus-wide default (path 1, primary)** — Task 14 sets `asyncio_default_fixture_loop_scope = "session"` AND `asyncio_default_test_loop_scope = "session"` in `[tool.pytest.ini_options]`. This is the source-of-truth: every async fixture and every async test in the project runs on the session loop by default. As a side effect, **Plan 11 L2987's latent same-bug** (`@pytest_asyncio.fixture(scope="session")` without explicit `loop_scope=`) is also resolved by the corpus-wide setting — no Plan 11 amendment required. Phase G should verify by `grep -nE 'asyncio_default_(fixture|test)_loop_scope' pyproject.toml` that both keys landed.
    - **Explicit local decoration (defense-in-depth)** — Task 2's `api_client` and `db_conn` keep the `@pytest_asyncio.fixture(scope="session", loop_scope="session")` decoration. If the corpus-wide defaults are ever reverted, the fixtures still work. The two-layer choice is deliberate: corpus default for any future async fixture/test the engineer forgets to decorate, local decoration for the two load-bearing slice fixtures so a reviewer reading the conftest sees the loop-scope claim inline.
    - Task 14 pins `pytest-asyncio >= 0.24, < 0.25` so `loop_scope=` is available AND the `asyncio_default_test_loop_scope` config key is recognized (this key landed in 0.23+; the conjoint `asyncio_default_fixture_loop_scope` landed in 0.21; pinning >= 0.24 floors both).

11. **AGE auto-id projection for byte-equal graph-state comparison** (advisor round-2 finding): AGE assigns sequential auto-IDs to vertices + edges at insertion time. The fixture's `drop_graph + create_graph` reset re-allocates IDs from 0; Plan 6's concurrent extractor fan-out does NOT guarantee deterministic insertion order. A `RETURN n` serialization of a vertex includes the auto-id inside the JSON string, so `sorted(json_strings)` would not normalize away the id-drift — the test would fail every run, even with a byte-identical source tree. Task 8's `_canonical_graph_state` therefore projects label + properties (vertices) and (type, props, src_label, src_props, dst_label, dst_props) (edges) via Cypher RETURN clauses; the projection is closed under the auto-id renaming. Vertex/edge natural identifiers (File.file_path, Symbol.fqn) live in the projected properties — Plan 5/6 schemas are load-bearing on this.

If any pass surfaces additional findings during the next advisor round, they are applied inline and the loop re-runs per `feedback_advisor_exhaustive_review_plans`.

---

## Plan 14 / Slice+1 handoff

After Plan 13 ships, Phase G (corpus-wide cross-plan-deps refresh) → Phase I (full-corpus crosscheck) → Phase J (resolve findings) → Phase K (corpus certification). Once certified, Slice+1 work begins:

- **Plan 14 — Orchestrator + PrimitiveRegistry promotion** (spec §10.6). Promotes Plan 10's hardcoded plan to a registry entry; adds the LLM orchestrator loop driver. The Plan 13 integration suite becomes the regression baseline.
- **Plans 15-18** — additional primitives (`find_specs_for`, `find_db_schema`, `find_traces_for`, `find_writers_of`, `trace_upstream`, etc.). Each gets its own integration test that mirrors Plan 13 Task 3's shape against a new symbol.

---

**End of Plan 13. Plans 1-13 form a shippable vertical slice once `slice-acceptance.yml` runs green at HEAD.**
