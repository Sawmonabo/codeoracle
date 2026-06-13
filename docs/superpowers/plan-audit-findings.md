# Plan Audit Findings — CodeOracle Platform Plans 1–10

**Status (original, 2026-05-22):** Pre-coding review. **No plan or code has been changed.** This document is a findings
catalogue only — it exists to be read and acted on *before* implementation of any plan begins.
This file is intentionally **not committed** (working-tree artifact).

**Status (2026-06-01, RECONCILED — supersedes the lines above):** The body (§0–§6) is the original 2026-05-22
pre-coding audit, **preserved verbatim as a historical snapshot**. Its "no plan or code has been changed", its
"not committed" note, and §6's "nothing here has been actioned" were accurate on 2026-05-22 and are now **superseded**:
the findings were subsequently resolved *at HEAD* by parallel workstreams (NFR remediation #50–68, the Phase G–K
cross-plan sweep, cite-hygiene passes) plus the new Plan 1A — **not** by working this catalogue as a checklist.
Current per-finding state is reconciled against HEAD `d26f0ef` in **§7** (the authority); the frozen body below no
longer describes the plans accurately. This file **is** now committed (the "not committed" note is itself superseded).

**Date:** 2026-05-22
**Audited:** the 10 written implementation plans in `docs/superpowers/plans/2026-05-21-*.md`
(36,716 lines total) against the design spec `docs/superpowers/specs/2026-05-21-platform-scaffolding-design.md`
(1,343 lines) and the project conventions in `CLAUDE.md`.

---

## 0. Method & caveats

- **One adversarial sub-agent per plan** (10 × Opus), each read its assigned plan in full plus
  the full spec, and scored it against a fixed 72-item spec-coverage checklist. Tone was
  adversarial by instruction: *find* deviations, do not reassure.
- **Cross-plan reconciliation** (which plan owns each of the 72 items; whether deferral chains
  terminate) was done centrally from the 10 reports.
- **Every P0 orphan claim was re-verified directly** with `grep` against the plan files — those
  findings are first-hand, not sub-agent-relayed.
- **Within-plan findings** (OOP shape, file sizes, slop) are sub-agent-derived. They are
  high-confidence but were not independently re-read line-by-line; spot-check before acting on
  any single P2 item.
- **Plans 11–13 are not yet written.** Their intended scope is API (11), MCP (12), E2E slice +
  janitors (13). Any item deferred to 11/13 is marked **❓ unverifiable** — it *may* be covered,
  but no artifact exists to confirm it.

**Classification legend**

| Icon | Meaning |
|---|---|
| ✅ | Built and owned cleanly, or correctly absent per a spec non-goal. |
| ⚠️ | Deviation is *defensible*, but the **spec is now stale** or the **plan needs an explicit edit** — a phantom gap that must be reconciled on paper, not silently. |
| ❓ | Deferral *claimed* to a plan **not yet written** (11/12/13). Cannot be confirmed; must be checked when that plan is authored. |
| ❌ | **Silent / lazy drop.** No owner, a broken deferral chain, or an undeclared rename. A genuine gap. |

### Re-look re-verification pass (2026-05-22)

After the initial 10-agent audit, a second wave of 10 adversarial sub-agents re-hunted each
plan for gaps and diffed their findings against this document. **Every claimed-missed finding
was then re-verified by the orchestrator against the actual plan and spec text before being
folded in** — the re-look reports were treated as *where to look*, never as a source of truth.
A finding folded below is one whose line-cites the orchestrator independently confirmed; a
finding the re-look raised but the plan text contradicts was rejected and is *not* folded.
New IDs added by this pass carry a `[re-look]` tag in §1.

This discipline mattered. It caught **one wholesale confabulation**: re-look-04's entire
diff section was written against a fabricated 1,420-line audit doc with an `F4.x` / `X-n`
finding scheme that does not exist (this doc is far shorter and uses `D-/G-/P0-` IDs), and it
declared a finding "entirely absent" that is in fact cited three times. re-look-04's *diff*
was discarded; its independent fresh hunt was salvageable and was re-verified item-by-item.
Re-verification also overturned ~10 individual over-statements from otherwise-genuine reports:
`SnapshotStatus.READY` and `core.clock.utc_now` were graded P0 "code does not run" when both
are prose-only mentions with no code call-site (real defects, but P2); `M-10.1` claimed an AGE
column-name mismatch that AGE's positional `cypher()` wrapper makes harmless; and claims of a
missing `Base` import, an undeclared public helper, a missing lock registry, and an
uncommented constant were each contradicted outright by the plan text and rejected. Trusting
any of these unverified would have put fabrications into the very document meant to prevent
them.

---

## 1. Executive summary

Ten plans, 26 numbered tasks median each, ~37k lines. The plans are individually disciplined —
TDD throughout, full embedded code, explicit task DoDs. **The problem is not within any one plan;
it is in the seams between them**, and in the fact that only Plans 9 and 10 carry a formal
`Deviations from the spec` section — Plans 1–8 present every divergence as if it conforms.

The re-look pass (§0) confirmed and sharpened this: it found **nine more P0 cross-plan breaks**
— plans that do not compile or run against each other — and not one new defect *internal* to a
single plan's logic. The seams are the problem.

### The user's flagged example — "embedding the docs" — is a real gap

The instinct was correct. The spec's knowledge graph spans "frontend → API → backend → DB →
traces → **docs**" (§1.2); `HandleType.DOC_PARAGRAPH` is a first-class evidence type (§6.1); the
embed worker must "embed **every** file" (§8.2 item 11). What the plans actually deliver:

- **Plan 6's embed worker is file-type-blind** — it embeds whatever file path it is handed, with
  no doc/markdown awareness and only line-range chunking (no paragraph or docstring unit). It
  *could* embed a `.md` file, but **nothing in Plan 6 ensures docs are in the indexed file set** —
  that is silently delegated upstream to Plan 5's `DeltaComputer`, with no assertion and no test.
  Plan 6 never says the word "deviation." Grep-verified 2026-05-22: the embed worker's `LineChunker`
  is pure line-range, `DeltaComputer` applies no extension filter, and every test fixture across
  Plans 5–6 is a `.py` file — no doc file is exercised end-to-end anywhere.
- **Plan 10 explicitly drops `DOC_PARAGRAPH` citations** (its Deviation 6) — the slice agent emits
  `CODE_SPAN` + `GRAPH_QUERY` only.
- Net: `DocParagraphResolver` (Plan 8) is a **reader with no producer**. The "docs" leg of the
  evidence graph is not exercised end-to-end in the slice — half-declared (Plan 10), half-silent
  (Plan 6). See **G14**.

This is the single clearest instance of the pattern the audit was asked to hunt: a spec capability
quietly distributed into non-existence across plan boundaries.

### P0 — resolve before any coding starts

| # | Finding | Why P0 |
|---|---|---|
| **P0-1** | **Embedding dimensionality conflict.** Plan 2 pins `OPENAI_EMBEDDING_DIM=1536` (OpenAI `text-embedding-3-small`); spec §9.3 recommends a 1024-dim `bge-large` model. Plan 3's pgvector column reads the pin via `sed`-sync. (**D-2.1**) | One-way door. Once Plan 3 lands the `vector(N)` migration, changing the dimension means re-indexing every embedding. Must be decided before Plan 3 codes. |
| **P0-2** | **`core/telemetry.py` is an orphan.** Plan 3 says (×3) "Plan 7 adds it"; Plan 7 builds nothing; Plan 10 says "Plan 11/13 own it". No plan builds it. (**G11**) | Observability gate 9 (§8.5) needs an OTel SDK + exporter. With no `telemetry.py`, every span emitted by Plans 9/10 is a silent no-op. |
| **P0-3** | **`db/repositories/evidence.py` (`EvidenceRepository`) is an orphan.** `grep` across all 10 plans: **zero hits.** Spec §3.2 lists it. (**G13**) | No plan builds, mentions, or defers it. If the slice needs to persist `Claim`/`Verdict` rows, nothing does. Confirm whether it is slice-critical or genuinely Slice+1. |
| **P0-4** | **Plan 3's — and Plan 4's — cross-plan references are systematically stale.** Plan 3 calls Plan 7 "API + MCP + agent" (Plan 7 is traces-ingest) and says Plan 6 builds 3 repositories (Plan 5 builds 2 of them); Plan 4 inherits the identical stale map, deferring `api/deps.py` composition to "Plan 7" at five sites. (**D-3.0**, **D-4.7**) | Both plans were written against an earlier plan map. *Every* forward-reference in Plan 3 and Plan 4 — including all deferral targets — is suspect and must be re-checked. |
| **P0-5** | **~10 spec-required infra/CI artifacts have no owner.** `Makefile`, `image-build.yml`, `security.yml` (as a file), `lockfile-refresh.yml`, `CODEOWNERS`, `infra/observability/`, `infra/security/semgrep/codeoracle-custom.yml`, `infra/config/defaults.env` (consumed by Plan 8, created by nobody), `infra/docker/Dockerfile*`, the `utils/` layer. (**G1–G12**) | These are Plan 1 / infra scope. Plans 11–13 are API/MCP/slice — none would absorb a CI workflow. Several §8.5 acceptance gates (image Trivy scan, OWASP semgrep) depend on the missing files. |
| **P0-6** | **§3.6 preflight is renamed and scope-shifted, undeclared.** Plan 2 ships `infra/scripts/check_pinned_env.sh` (an env-file linter) instead of the spec's `infra/scripts/preflight.sh` (a gate runner), and `docs/verification-report.md` instead of `.cache/preflight/results.md`. The four named §3.6 gate commands are not run as written; the spec's pre-decided fallbacks are discarded. (**D-2.2**, **G-2.3**) | §3.6 is the load-bearing pre-implementation contract. A renamed, re-scoped substitute that does a *different job* means the four "load-bearing assumptions" the platform rests on are not actually proven. |
| **P0-7** | **`traces.query` observability span can never fire.** Plan 10 drops the `traces_query` node (its Deviation 1); spec §8.5 gate 9 lists `traces.query` as a required child span. Plan 10 *demands* Plan 13 amend gate 9. (**D-10.1**) | If Plan 13 does not amend gate 9, the slice fails CI acceptance (§8.7). This is a declared deviation with a hard downstream-gate dependency on an unwritten plan. |
| **P0-8** | **Graph FQN scheme is incoherent across plans.** Plan 5's Joern parser emits node FQNs as `<file>::<name>` (grep-verified — Plan 5 Task 12's parser test asserts `fqn:'src/example.py::foo'`); Plan 8's graph-evidence resolver and the slice's `explain_symbol` query assume dotted Python FQNs. Plan 10 Deviation 7 works around the mismatch with embedding-kNN symbol lookup and itself flags that as accuracy-risky. No plan pins the FQN / `:Definition` line-range contract. (**G15**, **D-5.1**) | The slice's whole purpose is `explain_symbol`. A broken symbol→graph-node resolution degrades the core query to a workaround Plan 10 itself calls unreliable. The FQN scheme is baked into the graph when Plan 5's parser runs — changing it later means re-indexing the entire graph (one-way door, like P0-1). Decide before Plan 5 codes. |
| **P0-9** `[re-look]` | **Plan 2 ↔ Plan 3 pgvector-precondition key mismatch.** Plan 3's preconditions hard-gate on a `pinned.env` line matching `^IMAGE_PGVECTOR=` or `^PGVECTOR_INSTALL_METHOD=`; Plan 2 Task 3 records the install path under the key `PGVECTOR_SOURCE`, and in two of its three branches (`preinstalled`, `custom`) emits neither key the gate accepts. (**D-2.6**) | Plan 3 aborts even when Plan 2 ran correctly; re-running Plan 2 reproduces the rejected output — an unbreakable loop with no code defect. A literal cross-plan string contract. Distinct from P0-1 (the embedding-*dimension* conflict). |
| **P0-10** `[re-look]` | **`banned-api` TOML table defined twice.** Plan 1 Task 4 writes `[tool.ruff.lint.flake8-tidy-imports.banned-api]` into `pyproject.toml`; Plan 4 Task 1 Step 4 *appends* the same table header again. TOML forbids a duplicate table header — `pyproject.toml` no longer parses. (**D-4.6**) | The first `uv run ruff check` after Plan 4 Task 1 fails on a config parse error, and every subsequent `uv run` in Plans 4–10 inherits it. Plan 4 must *edit* Plan 1's block, not append. |
| **P0-11** `[re-look]` | **`make_async_engine` / `make_async_session_factory` called with the wrong arguments.** Plan 3 defines `make_async_engine()` (no args) and `make_async_session_factory(engine)`; Plan 5 calls both passing a `Settings` object (L1776, L5180–5181). Plan 7 calls them correctly — Plan 5 is the outlier. (**D-5.9**) | `ty` strict flags it; at runtime the first boot of the worker context / CLI raises `TypeError`. The Joern vertical cannot start. |
| **P0-12** `[re-look]` | **Worker-framework package path mismatch.** Plan 5 (and Plan 7, and spec §3.2) build the shared worker base at `workers/_base/`; Plan 6 imports it from `workers/base/` (no underscore) and expects `RetryPolicy` in a separate `retry.py` when Plan 5 defines it inside `_base/message.py`. (**D-6.6**) | Every `from codeoracle.workers.base… import …` in Plan 6 is an `ImportError`. Plan 6 is the lone outlier; its import map is wrong. |
| **P0-13** `[re-look]` | **`codeoracle.db.age_driver` is imported but never built.** Plan 6 Task 10 imports `exec_cypher` / `exec_cypher_returning` from `codeoracle.db.age_driver`; no plan creates `codeoracle/db/age_driver.py`. Plan 5's AGE access is the `GraphRepository` class at `db/repositories/graph.py`. (**D-6.8**) | Plan 6 Task 10 (`scip_graph.py`) does not compile. The module path is fictional. |
| **P0-14** `[re-look]` | **Plan 6 writes to an AGE graph that is never created.** Plan 6 Task 10 targets graph `codeoracle_code_graph`; Plan 3's bootstrap runs `create_graph('codeoracle')` only, and Plan 5's `GraphRepository` pins `_GRAPH_NAME = "codeoracle"` with an in-code comment that it matches the bootstrap. (**D-6.9**) | Plan 6 Task 10's Cypher fails at runtime against a non-existent graph, and directly contradicts Plan 5's code. |
| **P0-15** `[re-look]` | **alembic revision chain is broken in two places.** Plan 5 migration `0004` ← Plan 6 `0005_code_references` (whose `down_revision` is `0004_snapshot_file_outcomes`, ≠ `0004`) ← Plan 7 `0006` (whose `down_revision` is `0005`, ≠ `0005_code_references`). (**D-7.5**) | `alembic upgrade head` fails — the DB cannot be migrated. Three plans use two incompatible revision-ID conventions (bare-numeric vs `NNNN_slug`). |
| **P0-16** `[re-look]` | **Plan 7 imports `workers/_base/streams.py`; the file is at `workers/streams.py`.** Plan 5/6 build `streams.py` directly under `workers/`, not under `_base/`. Plan 7 references the `_base/` path at 9 sites. (**D-7.6**) | Mechanical `ImportError` on every Plan 7 module that touches streams. Distinct from P0-12 (the `base`-vs-`_base` package-name break). |
| **P0-17** `[re-look]` | **`EmbeddingsRepository.knn` called positionally; Plan 6 defines it keyword-only.** Plan 6 declares `async def knn(self, *, query_embedding, snapshot_sha, k)` — the `*` makes all three keyword-only; Plan 10's `embed_query` node calls `knn(vectors[0], sha, KNN_K)` positionally. (**D-10.U6**) | `TypeError` at runtime. Plan 10's own conftest fake declares `knn` *without* the `*`, so Plan 10's unit tests pass green against a fake whose signature does not match the real repository — the bug is masked, not caught. |

**The shape of the new P0s.** All nine re-look P0s (P0-9…P0-17) are one defect class: a plan
does not compile or run against the plans it depends on. Eight are two plans disagreeing on an
import path, a function signature, or an identifier; one (P0-13) imports a module no plan
builds. None is a design flaw — each is a literal cross-plan-reference error that a single
pre-coding sweep (re-verify every `import`, every cross-plan key / graph / revision name,
against the plan that owns it) would surface and fix. They are P0 not because any one is hard,
but because the plans were written in isolation and never compiled against each other on
paper. Contrast the **design-gap** flagged in §6 (Joern per-file extraction vs cross-file
query): that one is P0-*severity* but is not a transcription error — it depends on Joern's
runtime graph semantics, which a plan-text audit cannot settle. The table above is mechanical
certainties only.

### P1 — undeclared deviations, individually survivable, collectively a pattern

14 undeclared spec divergences catalogued for Plan 1 alone (§4); ~35 across all plans on the
initial pass. None blocks a build on its own; together they mean the spec and the plans have
silently drifted apart. Highlights: coverage
floor 80 vs spec 85; `mcp_gateway/` vs spec `mcp/`; `check_file_length.sh` vs spec's `.py`
(referenced as `.py` in 4 spec locations); AGE search-path order reversed vs spec §3.5;
`request_fingerprint` drops the spec-mandated "key parameters". Full list in §4.

The re-look pass adds ~11 more P1s: Plan 2's verification floor silently omits Tempo + the
OpenTelemetry Collector (D-2.8); `EmbeddingsProviderFactory` forces a `core → adapters` layer
inversion (D-6.10); Plan 4 defers `api/deps.py` to a mis-numbered plan (D-4.7) and ships a
non-advisory live-LLM gate (D-4.10); Plan 9's filtering production logger makes spec §8.5
criterion 11 unsatisfiable (D-9.3); the `uv-lock` pre-commit hook is missing (G20); and a
dirty-tree `+wt` SHA flows unhandled into the Sourcebot resolver (G21).

### P2 — slop / debt

Mostly minor. The one hard CLAUDE.md violation: **Plan 5 ships committed commented-out code**
(`workers/streams.py`, `services/indexing/orchestrator.py`) — "no commented-out blocks" is an
explicit project rule. Plus Plan 7's self-review cites a non-existent test. The re-look pass
adds ~22 more P2 items (§4) — a fabricated spec citation (D-2.9), dead lint config for a
non-existent file (D-6.15), schema-width and field-casing inconsistencies — individually
trivial, collectively the texture of plans written fast and never cross-checked. Full list
in §4.

### The good news

- **File-size discipline is clean.** Across all 10 plans, **zero source files exceed the 500-line
  soft target**, let alone the 700-line hard ceiling. The largest projected file is Plan 7's
  `tempo_client.py` at ~360 lines. The 500/700 rule is not at risk.
- **OOP is broadly honored** — every service/adapter/factory is a constructor-injected class,
  *except* Plan 10's agent nodes (see §2 / D-10.U2) and a few borderline factory shapes.
- **No half-finished implementations, no `TODO`-without-task** in committed code.

---

## 2. Convention adherence per plan

Against CLAUDE.md: OOP-over-functional · 500-soft/700-hard file cap · no slop/redundancy/debt.

| Plan | OOP | File size | Slop / debt | Notes |
|---|---|---|---|---|
| 1 — repo skeleton + CI/CD | ✅ n/a (config only) | ✅ all <500 | ⚠️ dual version source (`pyproject` `0.1.0` + `core/version.py` — file claims "single source of truth"); `.mypy_cache` ignored though project uses `ty` | Real issues are omissions (§5), not convention. |
| 2 — verification gates | ✅ n/a (no Python) | ✅ n/a | ⚠️ `JOERN_RELEASE_URL` pinned to `…/latest/…` while a static `JOERN_VERSION` is recorded — pin not reproducible; contradicts its own "no 'latest' surprises" claim | Builds shell + docs only. |
| 3 — core + db bootstrap | ⚠️ `db/session.py` is all free functions (`make_async_engine`, …); spec §3.5 precedents the listener, but the `make_*` factories have no stated reason for not being a class | ✅ all <500 | ✅ clean; `Vector(1)` placeholder + `password="codeoracle"` default are accepted-but-fragile | Model strengthenings (frozen configs, added fields) undeclared — D-3.x. |
| 4 — LLM abstraction | ✅ all providers/factory are constructor-injected classes | ✅ all <500 | ⚠️ `count_tokens` heuristic duplicated vLLM↔Ollama; OpenAI-compat tool dict hand-built 3× | `structured_output` is a no-op param on all 5 providers. |
| 5 — worker + Joern | ⚠️ `advisory_lock.py` free `@asynccontextmanager` in `services/` (no reason given); empty `@dataclass(frozen=True) JoernCpgParser` | ✅ all <500 | ❌ **commented-out future code shipped** in `streams.py` + `orchestrator.py` — direct CLAUDE.md violation; `find_callers` is dead code in this plan | TDD discipline otherwise strong. |
| 6 — extractors + embeddings | ⚠️ `EmbeddingsProviderFactory` is static-method-only (no DI, unlike sibling `LLMProviderFactory`) | ✅ all <500 (`scip_graph.py` ~230 — watch) | ⚠️ failure-write boilerplate duplicated 5–7× inside `embed_indexer/tasks.py`; `CodeReferenceKind` enum restated in 4 places | Worker `tasks.py` free functions are spec-sanctioned. |
| 7 — consumer + traces | ✅ strongly OOP-compliant | ✅ all <500 (`tempo_client.py` ~360 — SHOULD-SPLIT preemptively) | ❌ **self-review cites a non-existent test** (`test_ingest_stats_rejects_regressing_cursor`); ⚠️ test misattributed to wrong task | Otherwise clean. |
| 8 — handle resolution | ✅ ABC + 4 strategy classes, constructor-injected | ✅ all <500 | ⚠️ dormant `sort_keys=True` (unreachable under scalar-only constraint); speculative `sourcebot_sdk` ban for a package that does not exist | Tightly scoped; but commits to an unverified Sourcebot HTTP contract — D-8.4. |
| 9 — verifier + trust matrix | ✅ both services are classes; `_normalize_for_comparison` correctly a free pure-fn | ✅ all <500 (`test_service.py` ~440 — watch) | ✅ clean | Evidence-free-citation guard placed in service, not model — see D-9.x. |
| 10 — agent orchestration | ❌ **all 6 nodes are decorated free functions, not classes — undeclared convention drift, no written justification** | ✅ all <500 (`compose.py` ~207) | ✅ clean (minor: span-error idiom duplicated 2×) | See D-10.U2 — the headline OOP finding. |

**OOP headline (D-10.U2):** Plan 10 builds agent nodes as `async def` + `@agent_node` decorator +
`_with_deps` closures. Each node composes 5–7 injected collaborators, does I/O, applies domain
rules — by CLAUDE.md's own test these are **not** "pure transformations / one-line helpers" and
must be classes. The convention-faithful design — a `NodeBase` ABC with an `async __call__`
template method (short-circuit + span + error-trap) and one class per node — is *functionally
equivalent* to `@agent_node` and is what the project mandates. Plan 10 gives **no written reason**
for the functional choice and does not list it in its own Deviations section. `graph.py`'s wiring
is legitimately function-friendly (composition root); the **node modules are not**. The re-look
widens this (D-10.U2): `resolve_to_citation` — a module-level free function that opens a span,
calls a resolver, and constructs a `DraftCitation` — is the same violation, and is not even a
LangGraph node, so the framework-idiom defence does not reach it.

---

## 3. Spec coverage matrix

All 72 checklist items. **Owner** = the plan that builds it. IDs (D-/G-) point to §4/§5.

| # | Spec item | Owner | Status | Note |
|---|---|---|---|---|
| 1 | `pyproject.toml` (single config root) | Plan 1 | ✅ | extended by 3,4,5,6,10 |
| 2 | `.pre-commit-config.yaml` | Plan 1 | ✅ | |
| 3a | `README.md` | Plan 1 | ✅ | |
| 3b | **`Makefile`** | — | ❌ | **G1** — orphan |
| 3c | `.env.example` | Plan 1 | ✅ | extended by 3,4,6,8 |
| 4 | `.github/workflows/ci.yml` | Plan 1 | ⚠️ | D-1.4, D-1.5, D-1.6 |
| 5 | **`image-build.yml`** (Docker build + Trivy ×5) | — | ❌ | **G2** — orphan |
| 6 | **`security.yml`** (discrete file) | — | ❌ | **G3** — checks folded into `ci.yml`; file absent |
| 7 | **`lockfile-refresh.yml`** | — | ❌ | **G4** — orphan |
| 8a | `renovate` config | Plan 1 | ⚠️ | D-1.3 — `renovate.json` at root vs spec `.github/renovate.json5` |
| 8b | **`CODEOWNERS`** | — | ❌ | **G5** — orphan |
| 9 | **`infra/docker/Dockerfile` + `Dockerfile.model-server`** | — | ❌ | **G6** — orphan |
| 10 | `infra/compose/docker-compose.yml` | Plan 3 | ⚠️ | D-7.3 — Plan 7 modifies at drifted path `infra/docker-compose.dev.yml` |
| 11 | **`infra/observability/`** | — | ❌ | **G7** — orphan |
| 12 | `check_file_length.py` | Plan 1 | ⚠️ | D-1.2 — shipped as `.sh`; spec names `.py` in 4 places |
| 13a | `bootstrap_db.sh` | Plan 3 | ✅ | |
| 13b | **`bootstrap_dev.sh`** | — | ❌ | **G8** — orphan |
| 13c | **`seed_test_corpus.sh`** | — | ❌ | **G8** — orphan |
| 14 | `infra/scripts/preflight.sh` | Plan 2 | ❌ | **D-2.2** — renamed to `check_pinned_env.sh`, different job |
| 15 | **`semgrep/codeoracle-custom.yml`** | — | ❌ | **G9** — orphan |
| 16 | **`infra/config/defaults.env`** | — (Plan 8 *modifies*) | ❌ | **G10** — consumed, never created |
| 17 | `core/config.py` | Plan 3 | ✅ | extended by 4,6,7 |
| 18 | `core/logging.py` | Plan 3 | ✅ | D-5.7 — Plan 5 worker structlog omits §7.9 secret redaction |
| 19 | **`core/telemetry.py`** | — | ❌ | **G11 / P0-2** — orphan |
| 20 | `core/exceptions.py` | Plan 3 | ✅ | extended by 4,5,8,10 |
| 21 | **`utils/async_helpers.py`** (+ `utils/` layer) | — | ❌ | **G12** — layer never scaffolded |
| 22 | `models/evidence.py` | Plan 3 | ✅ | D-3.x — undeclared model strengthenings |
| 23 | `models/llm.py` | Plan 3 | ✅ | Plan 4 adds `template_id` |
| 24 | `models/query.py` | Plan 3 | ✅ | |
| 25 | `models/snapshot.py` | Plan 3 | ✅ | |
| 26 | `models/trust.py` | Plan 3 | ✅ | |
| 27 | `db/session.py` + AGE listener | Plan 3 | ⚠️ | D-3.1 — search-path order reversed vs spec §3.5 |
| 28 | `db/migrations/` + `bootstrap_db.sql` | Plan 3 | ✅ | +0004 (P5), +0005 (P6), +0006 (P7) |
| 29 | **`db/repositories/evidence.py`** | — | ❌ | **G13 / P0-3** — orphan, zero mentions |
| 30 | `db/repositories/graph.py` | Plan 5 | ✅ | D-3.0 — Plan 3 mis-points this to Plan 6 |
| 31 | `db/repositories/embeddings.py` | Plan 6 | ✅ | |
| 32 | `db/repositories/snapshots.py` | Plan 5 | ✅ | D-3.0 — Plan 3 mis-points this to Plan 6 |
| 33 | `db/repositories/traces.py` | Plan 7 | ⚠️ | D-7.1 — built at `db/traces.py`, not `db/repositories/` |
| 34 | `adapters/joern/` | Plan 5 | ✅ | |
| 35 | `adapters/opengrep/` | Plan 6 | ✅ | |
| 36 | `adapters/sourcebot/` | Plan 8 | ✅ | |
| 37 | `adapters/traces/` | Plan 7 | ✅ | |
| 38 | `adapters/valkey/` | Plan 5 | ✅ | |
| 39 | `adapters/mongo/` + `adapters/postgres/` | — | ✅ | spec non-goal for the slice — correctly absent |
| 40 | `adapters/llm/` | Plan 4 + Plan 6 | ✅ | chat (4) + embeddings provider (6) |
| 41 | `services/verifier/service.py` | Plan 9 | ✅ | |
| 42 | `services/handle_resolution/` | Plan 8 | ✅ | |
| 43 | `services/indexing/` | Plan 5 | ✅ | extended by 6 |
| 44 | `services/context_budget.py` | Plan 4 | ✅ | correct layer |
| 45 | `services/trust_matrix.py` | Plan 9 | ✅ | |
| 46 | `services/query_planner.py` | — (dropped by Plan 10) | ⚠️ | D-10.3 — Plan 5 still references it as live |
| 47 | `api/` | Plan 11 | ❓ | unwritten |
| 48 | `cli/` | Plan 5 (partial) | ❌ | D-5.8 — `index` cmd only; `cli/__main__.py` claimed from Plan 1, which never scaffolded `cli/` |
| 49 | `mcp/` | Plan 12 | ❓ | unwritten; Plan 1 scaffolded as `mcp_gateway/` — D-1.1 |
| 50 | `agent/` | Plan 10 | ⚠️ | **P0-17 / D-10.U6** — positional `knn` call breaks at runtime; D-10.U2 — OOP shape; D-10.U1 — no `__main__.py` |
| 51 | `workers/_base/` | Plan 5 | ✅ | Plan 7 adds `ContinuousConsumer` sibling ABC |
| 52 | `workers/joern_indexer` | Plan 5 | ✅ | |
| 53 | `workers/scip_indexer` | Plan 6 | ✅ | |
| 54 | `workers/opengrep_runner` | Plan 6 | ✅ | |
| 55 | `workers/sourcebot_indexer` | — (dropped by Plan 6) | ⚠️ | D-6.1 — declared removal, spec §3.2/§4.1/§8.2 still list it |
| 56 | `workers/embed_indexer` | Plan 6 | ✅ | G14 — embeds, but docs-in-fileset unguaranteed |
| 57 | `workers/traces_ingest` | Plan 7 | ⚠️ | D-7.2 — no `tasks.py`; poll-not-stream |
| 58 | `codeoracle index --since` / `POST /v1/reindex` | Plan 5 (partial) | ❓ | CLI built; `POST /v1/reindex` → Plan 11 |
| 59 | changed-file list + dirty-tree SHA | Plan 3 + Plan 5 | ✅ | |
| 60 | dependent discovery via Cypher | Plan 5 | ✅ | D-5.x — query duplicated (`find_callers` vs inline) |
| 61 | atomic per-changeset AGE mutation | Plan 5 | ⚠️ | D-5.6 — transaction boundary left implicit |
| 62 | Snapshot tracking | Plan 3 + Plan 5 | ✅ | |
| 63 | single-flight `pg_advisory_lock` | Plan 5 | ✅ | |
| 64 | 5 functional integration tests | Plan 13 | ❓ | unwritten |
| 65 | 3 incremental tests | Plan 13 | ❓ | unwritten |
| 66 | `test_intent_classifier_never_…` | Plan 10 + Plan 13 | ❓ | unit analogue in 10; §8.5 integration → 13 |
| 67 | 8 CI architectural-invariant gates | Plan 1 (partial) | ⚠️ | D-1.x — several gates deviate; image-scan gate depends on G2 |
| 68 | observability gate 9 (OTel spans) | Plan 10 (partial) | ⚠️ | D-10.1 — child spans only, no SDK/root; `traces.query` impossible |
| 69 | observability gate 10 (self-traces) | Plan 7 mechanism | ❓ | gate assertion → Plan 13 |
| 70 | observability gate 11 (verifier log) | Plan 9 | ✅ | the one observability criterion fully owned |
| 71 | §3.6 preflight + `.cache/preflight/results.md` | Plan 2 | ❌ | D-2.2 — renamed/repathed/scope-shifted |
| 72 | `MockLLMProvider` + `llm_scripts/` | Plan 4 + Plan 7 | ✅ | D-10.U3 — Plan 10's own tests use a private `_FakeLLM` instead |

**Tally** (77 matrix rows — 72 spec items, items 3 / 8 / 13 each split into sub-rows): ✅ 40 · ⚠️ 13 · ❓ 7 (all to unwritten 11–13) · ❌ 17 (orphans + undeclared renames). *(Item 50 `agent/` moved ✅→⚠️ on the re-look pass — P0-17.)*

---

## 4. Deviation catalogue

Substantive deviations only, by plan. Each row: what · declared in-plan? · classification · verdict.
Trivial lint-config diffs (e.g. Plan 1 ignoring `E501`, adding `SIM`) are recorded in the per-plan
sub-agent reports and omitted here as non-substantive — *except* the `banned-api` / per-file-ignore
enumeration, which is substantive and is catalogued (D-1.10, D-4.6, D-6.15).
Gaps in the `D-` numbering (D-5.5, D-10.2) are inherited from the per-plan sub-agent
report indices — a skipped number is not a missing row.

### Plan 1 — repo skeleton + CI/CD  *(no Deviations section; all undeclared)*

| ID | Deviation | Declared | Class | Verdict |
|---|---|---|---|---|
| D-1.1 | `mcp/` scaffolded as `mcp_gateway/` | No | ⚠️ | Every later plan inherits the rename. Decide: rename to spec `mcp/`, or amend spec §3.2. |
| D-1.2 | `check_file_length.py` shipped as `.sh` | No | ⚠️ | Spec references `.py` in §3.4, §7.2, §7.6, §8.5 gate 4. A reader/CI grepping `.py` finds nothing. |
| D-1.3 | `renovate.json` at repo root vs spec `.github/renovate.json5` | No | ⚠️ | Wrong name, extension, location. |
| D-1.4 | Coverage floor `80` vs spec §8.5 gate 5 / §7.2 `85` | No | ⚠️ | A quality-gate *loosening* presented as the target. Decide the real number. |
| D-1.5 | Security checks folded into `ci.yml` as a job; no `security.yml` file; header reframes it as "one workflow" the intended design | No | ⚠️ | Spec §7.1 enumerates 4 discrete workflow files. Function present, structure diverges. |
| D-1.6 | semgrep ruleset `p/security-audit` vs spec §8.5 gate 8 `p/owasp-top-ten` | No | ⚠️ | §8.5 gate explicitly checks for `p/owasp-top-ten`. |
| D-1.7 | `[tool.ty]` block diverges from spec §7.5 on three counts: strict approximated by 4 hand-listed rules vs `rules.strict = true`; `src = ["src/codeoracle"]` vs spec `src.root = "src"`; spec's `src.respect-ignore-files = true` dropped | No | ⚠️ | "Strict-equivalent" is the plan's term; 4 rules ≠ provably the blanket strict set. The `src`-root + dropped `respect-ignore-files` have small blast radius (the project is an installed package, so `import codeoracle.X` resolves via package metadata) — but all three are uncited. |
| D-1.8 | ruff `select` drops `ANN` + `C90`/mccabe vs spec §7.5 | No | ⚠️ | Drops the annotation gate and complexity gate. Loosening. |
| D-1.9 | `tests/` scaffolded flat (`tests/__init__.py` only); spec §3.1 names `tests/{unit,integration,characterization}/` | No | ⚠️ | Task 7 ships `tests/unit/test_smoke.py` at the root; the 3 spec-named subdirs are never created — yet Plan 8 already writes `tests/unit/...`, so the layout is incoherent across plans. |
| D-1.10 | `[tool.ruff.lint.per-file-ignores]` for `TID251` enumerates 4 provider files explicitly; spec §5.6 / §7.5 uses the glob `providers/*.py` | No | ⚠️ | `mock.py` is a real provider file (spec §5.3) and is omitted — a direct SDK import added to `mock.py` later is silently un-ignored, and the hand-list rots as providers are added. |
| D-1.11 | `ci.yml` is an early / partial form of spec §7.2 — ~6 undeclared shape divergences: `setup-uv@v4` vs `@v3` (benign — newer), `runs-on: ubuntu-latest` vs `ubuntu-24.04`, coverage via `upload-artifact` vs `codecov/codecov-action@v4`, `cache-dependency-glob` omitted, divergent `concurrency`, test job has no Postgres service / `bootstrap_db.sh` | No | ⚠️ | `ubuntu-latest` loses build reproducibility; Codecov→artifact loses PR coverage comments; the `concurrency` change drops stale-run cancellation on `main`. The missing Postgres service is defensible early sequencing (Plan 1 runs `-m "not integration"`) but undeclared. |
| D-1.12 | `[tool.uv] package = true` vs spec §7.5 `managed = true` | No | ⚠️ | `package = true` is arguably *more* correct for the installable `src/` layout — but it is an undeclared silent change of a spec-named key. |
| D-1.13 | Python runtime policy resolved to `requires-python = ">=3.14"` and Ruff `py314` | Yes | ✅ | Current policy is Python 3.14 or newer across project metadata, tooling, CI, and runtime images. |
| D-1.14 | `.env.example` sets `CODEORACLE_LLM_ANTHROPIC__MODEL=claude-opus-4-7`; spec §5.4 and Plan 4 both use `claude-sonnet-4-6` | No | ⚠️ | Undeclared inter-plan disagreement on the default model identity. `.env.example` is a template (not a one-way door) → P2. |

### Plan 2 — verification gates  *(no Deviations section; all undeclared)*

| ID | Deviation | Declared | Class | Verdict |
|---|---|---|---|---|
| D-2.1 | `OPENAI_EMBEDDING_DIM=1536` pinned; spec §9.3 recommends 1024-dim `bge-large` | No | ❌ **P0-1** | Resolves a spec open question silently, hard-wiring a pgvector dimension. One-way door — see §6. |
| D-2.2 | `preflight.sh` → `check_pinned_env.sh` (env-linter, not gate runner); `.cache/preflight/results.md` → `docs/verification-report.md` | No | ❌ **P0-6** | Spec-named §3.6 artifacts never created; substitute does a different job. |
| D-2.3 | §3.6 Joern gate reduced to parsing one empty `.py` file; SCIP gate reduced to indexing one empty file; vLLM `/v1/embeddings` contract test absent | No | ⚠️ | Empty-file substitution is defensible (verifier service doesn't exist pre-impl) but never declared/deferred. |
| D-2.4 | §3.6's pre-decided per-gate fallbacks (`postgres:17`, SCIP-only, pyright, `vllm_embed.py`) replaced with generic "STOP / surface to user" | No | ⚠️ | The spec already chose the fallbacks; the plan discards that decision. |
| ~~D-2.5~~ | ~~Task 12 appends a CI job after a `file-length-gate:` block that spec §7.2's `ci.yml` does not contain~~ | — | — | **Withdrawn 2026-05-22 (re-look re-verification).** The observation is true but the conclusion is false: Plan 2 Task 12 appends to *Plan 1's* `ci.yml`, and Plan 1 Task 11 *does* create the `file-length-gate:` job — the anchor resolves cleanly. D-2.5 judged the anchor against the spec's illustrative §7.2 YAML, the wrong artifact. Residual (Plan 1's `ci.yml` ≠ spec §7.2) is covered by D-1.5 + D-1.11. |
| D-2.6 | pgvector-precondition key mismatch — Plan 3 gates on `^IMAGE_PGVECTOR=` / `^PGVECTOR_INSTALL_METHOD=`; Plan 2 Task 3 records the path under `PGVECTOR_SOURCE` and emits neither key in its `preinstalled` / `custom` branches | No | ❌ **P0-9** | A literal cross-plan string contract. Plan 3 aborts though Plan 2 ran correctly; re-running Plan 2 reproduces the rejected output. Distinct from P0-1 / D-2.1 (the embedding-*dimension* conflict). See §6. |
| D-2.7 | `pinned.env` header claims the pins are "tracked via Renovate regex managers (Plan 1 Task 13)" — Renovate is Plan 1 *Task 12*, and Plan 1's `renovate.json` has no regex / custom managers | No | ⚠️ | Misleading provenance comment; drift is actually caught by the `verification-pins` CI job, so no functional blast radius → P2. |
| D-2.8 | Plan 2's verification floor omits Tempo + the OpenTelemetry Collector; Task 11 declares `traces_ingest` "has no external-tool dep" | Partly | ❌ | Spec §3.3 lists both as committed infra and §3.2 says `traces_ingest` pulls OTel traces *from Tempo*. Plan 2's mandate is to prove *every* external dep — it omits two, rationalised by a factually-wrong sentence. Survivable (verifiable before Plan 7) → P1. Cross-ref G7. |
| D-2.9 | "Apache AGE 1.5" asserted throughout Plan 2; the spec names no AGE version, and the report template attributes "1.5" to spec §3.5 | No | ⚠️ | A fabricated spec citation committed into a verification report whose entire value is provenance. |
| D-2.10 | Report-append templates hard-code spec sections `§4.x` / `§5.x` / `§6.x` with a literal `.x` and no substitution instruction | No | ⚠️ | The placeholder is unresolvable as written, and the real deps trace to §3.6 / §3.2 — a corrupted evidence trail in the report. |
| D-2.11 | `pinned.env` header + README (3 sites, incl. "What Plan 3 inherits") document compose consumption via service-level `env_file:` paired with `image: ${IMAGE_*}` | No | ⚠️ | `env_file:` injects container env vars; it does **not** drive compose-file `${VAR}` interpolation (only a root `.env` / `--env-file` does). Plan 3 itself uses `--env-file` correctly → P2. |
| D-2.12 | SCIP maintenance-staleness STOP gate keys on `npm view … time.modified` | No | ⚠️ | `time.modified` updates on any registry metadata mutation (deprecate, owner change), not on last publish — the gate can false-negative an abandoned package. |
| D-2.13 | Plan 2 verifies Joern with `--language pythonsrc`; Plan 5's `JoernClient` invokes `--language PYTHONSRC` | No | ⚠️ | A verification gate should exercise the byte-identical invocation its consumer uses. Joern's selector is case-insensitive in current releases → likely benign → P2. |

### Plan 3 — core + db bootstrap  *(no Deviations section; all undeclared)*

| ID | Deviation | Declared | Class | Verdict |
|---|---|---|---|---|
| D-3.0 | **Forward-references written against a stale plan map** — "Plan 7 (API+MCP+agent)", "Plan 6 writes 3 repositories" | No | ❌ **P0-4** | Plan 7 is traces-ingest; Plan 5 builds 2 of those repos. Re-verify *every* "Plan N" in Plan 3. |
| D-3.1 | AGE per-connection `search_path` order `"$user",public,ag_catalog` vs spec §3.5 `ag_catalog,"$user",public` | No | ⚠️ | Plan 3's order is correct — an unqualified `CREATE TABLE` lands in the first *existing* schema, so the spec's order pollutes `ag_catalog`. Spec §3.5's order **is** a latent bug; amend the spec, do not leave the disagreement in a code comment. |
| D-3.2 | `ChatMessage` gains a `tool_calls` field absent from spec §5.2 | No | ⚠️ | Plausibly needed; spec didn't ask, plan didn't say. |
| D-3.3 | Model strengthenings: `EvidenceHandle`/`TokenUsage` frozen + length-constrained + `total_tokens` property; spec models are plain/mutable | No | ⚠️ | Defensive, but a frozen-vs-mutable change is behavioural. None labelled. |
| D-3.4 | `.env.example` writes `CODEORACLE_LLM__PROVIDER` (double underscore) — collides with `Settings` `CODEORACLE_` prefix; spec §5.4 uses `CODEORACLE_LLM_` | No | ⚠️ | Real env-contract bug that will mislead Plan 4 — and a regression: Plan 3 Task 7 `cat`s Plan 1's spec-correct `.env.example`, then overwrites it with the `CODEORACLE_LLM__` form. |
| D-3.5 | Plan 3 L15 prose says `IMAGE_SOURCEBOT` is "checked here for presence only"; the Preconditions Step C `required=()` array (and the pgvector grep-gate, and the prints) omit it | No | ⚠️ | Plan 3 also says SourceBot is "not used until Plan 5" — so it is ambiguous which side is the error (the prose, or the array correctly not gating an unused pin). One-line reconcile either way → P2. |

### Plan 4 — LLM abstraction  *(has a "Deferred" block; no Deviations section)*

| ID | Deviation | Declared | Class | Verdict |
|---|---|---|---|---|
| D-4.1 | `request_fingerprint` hashes (messages, tools) only — drops spec §5.7's "key parameters" (temperature, `max_tokens`, `structured_output`, model) | No | ❌ | Strongest silent narrowing in Plan 4. Two requests differing only in temperature collide on one Mock response. |
| D-4.2 | `AnthropicProvider` hard-codes `max_tokens=4096`, not config-driven | No | ⚠️ | Output >4096 tokens silently truncated. |
| D-4.3 | `count_tokens` for Anthropic uses `cl100k_base` (OpenAI's encoding) | Partly | ✅ | **RESOLVED at HEAD (§7.2):** `count_tokens` now calls Anthropic's real `client.messages.count_tokens(...)` API with a labeled/logged `len(text)//4` last-resort fallback on API error; the `cl100k_base` approximation is removed. |
| D-4.4 | ruff `env_prefix` `CODEORACLE_LLM__` vs spec §5.4 `CODEORACLE_LLM_`; banned-api adds `ollama` (spec lists 3) | No | ⚠️ | Env-contract drift (ties to D-3.4); `ollama` ban is a reasonable strengthening, still undeclared. |
| D-4.5 | `structured_output` accepted by the ABC + all 5 providers but implemented by none | Partly | ⚠️ | Half-fulfilled interface; OpenAI case declared-deferred, other 3 silent. |
| D-4.6 | `[tool.ruff.lint.flake8-tidy-imports.banned-api]` table defined twice — Plan 1 Task 4 writes it, Plan 4 Task 1 Step 4 *appends* the header again → duplicate TOML table → `pyproject.toml` stops parsing | No | ❌ **P0-10** | The first `ruff check` after Plan 4 Task 1 fails on a config parse error; every later `uv run` in Plans 4–10 inherits it. The two blocks also diverge (8 entries / message-A vs 4 / message-B; spec §5.6 is a third shape). Plan 4 must EDIT, not append. See §6. |
| D-4.7 | Plan 4 attributes the `api/deps.py` composition root to "Plan 7" (L23, L325, L2453, L3019, L3030) | No | ❌ | The identical stale-plan-map string P0-4 / D-3.0 flags in Plan 3 — spec §5.5 gives no plan number, and Plans 8/9 attribute `api/deps.py` to Plan 11. **Widens P0-4 from "Plan 3" to "Plans 3 + 4."** P1. |
| D-4.8 | `LLMProvider.aclose()` deferral names two owners — Plan 4's ABC docstring says "Plan 7", Plan 5 L5697 says "Plan 8" | Yes (both, inconsistently) | ❌ | An incoherent deferral chain (kin to G16); `aclose()` belongs to the real API plan (11). A clean-shutdown nicety, not slice-critical → P2. |
| D-4.9 | `_to_openai_message` / `_to_anthropic_message` drop `ChatMessage.tool_calls` on outbound serialization | Yes (both docstrings) | ⚠️ | Thoroughly declared in both docstrings (the omission, the needed wire shape, the consequence) — ⚠️-declared, not a silent break; kin to D-4.5. Not slice-blocking (Plan 10 is single-shot). The "Plan 5/6" deferral target is itself dubious — multi-turn tool flow is API / agent territory. |
| D-4.10 | `test-live-llm.yml`'s `live-llm-smoke` job runs `pytest -m live_llm` as an ordinary step — no `continue-on-error`, no non-required-check note; spec §5.7 says the live-LLM gate "does NOT block the PR (advisory)" | No | ⚠️ | Undeclared deviation from an explicit spec contract — a live-LLM hiccup would block an unrelated PR. EDIT-fixable → P1. |
| D-4.11 | Undeclared spec §5.4 config-field additions — `VLLMProviderConfig.api_key="EMPTY"` (spec lists only `base_url` + `model`); a `max_context_tokens` field on all 4 provider configs (spec §5.4 carries no such field; §5.2 makes it an ABC *method*) | No | ⚠️ | Both are sound design (vLLM needs a bearer placeholder; per-deployment window override) — but undeclared additions to the spec's config-class field lists. |
| D-4.12 | vLLM / Ollama `count_tokens` use a `len(text.split())` word-split heuristic | Yes (L2240-2246 / L2448-2452) | ⚠️ | Plan 4 explicitly accepts the imprecision — ⚠️-declared (kin to D-4.3). Residual: the "a future plan may wire `/tokenize`" pointer is not a tracked deferral. Refines the §2 "duplicated heuristic" note. |

### Plan 5 — worker + Joern  *(structured Inherits/Defers blocks; no Deviations section)*

| ID | Deviation | Declared | Class | Verdict |
|---|---|---|---|---|
| D-5.1 | Joern node FQN scheme is `<file>::<name>`, not a dotted Python FQN | No | ❌ | The slice query + Plan 7/8 graph queries assume dotted FQNs. Latent cross-plan defect — see §5 G15. |
| D-5.2 | `JoernClient` boots a JVM per file (cold-start each invocation) | Yes (comment) | ⚠️ | Perf trade-off vs §8.5 "<3 min cold reindex" — never reconciled with the budget. |
| D-5.3 | Joern parser silently skips unknown node/edge labels | Yes (docstring) | ⚠️ | Data-completeness narrowing vs a full CPG. |
| D-5.4 | New `snapshot_file_outcomes` table + `db/schemas.py` + `advisory_lock.py` — net-new architecture absent from spec §3.2 | No | ⚠️ | Well-justified completion barrier; still a spec-layout addition never flagged. |
| D-5.6 | `replace_file_subgraph` (atomic AGE mutation, §8.4) does not own its transaction; caller uses no explicit `session.begin()` | Yes (docstring) | ⚠️ | "commit-or-rollback" honoured only by accident of the context manager. |
| D-5.7 | Worker structlog config omits the §7.9 secret-redaction processor | No | ⚠️ | Worker logs can emit secrets. |
| D-5.8 | `cli/__main__.py` "inherited from Plan 1" — Plan 1 never scaffolded `cli/`; Click-vs-argparse left to "adapt" | No | ✅ | **RESOLVED at HEAD (§7.2):** Plan 1 scaffolds `cli/__main__.py` as a Typer app; Plan 5 Task 14's `index` subcommand is a typed `@app.command` (Typer, not Click). Both the cli-scaffold leg [Plan 1] and the CLI-framework-hedge leg [Plan 5] are closed. |
| D-5.9 | `make_async_engine` / `make_async_session_factory` called with a `Settings` argument — Plan 3 defines them as `make_async_engine()` (no args) / `make_async_session_factory(engine)` (broken calls at L1776, L5180–5181) | No | ❌ **P0-11** | `ty` strict flags it; first boot of the worker context / CLI raises `TypeError`. Plan 7 calls them correctly — Plan 5 is the outlier. See §6. |
| D-5.10 | Joern vertical extracts per-file (`JoernClient.extract(file_path)`, `replace_file_subgraph` writes one file) but `find_callers` / `DeltaComputer` / Task 15 require cross-file `CALLS` edges | No | ❌ / ❓ | **P0-tier design gap — see §6 DECIDE.** A per-file extraction cannot resolve a reference whose target lives in another file (certain — a property of static analysis); the sharper claim that Joern emits no such edge at all rests on Joern's edge vocabulary and is not plan-text-verifiable. Not in the §1 P0 table (which is mechanical certainties only). |
| D-5.11 | `SnapshotFileOutcome.snapshot_sha` is `String(64)`; Plan 3's `Snapshot.sha` / `snapshot_sha` are `String(80)` | No | ⚠️ | Same logical key, two widths. A dirty-tree SHA (`<40>+wt<12>` = 55 chars) fits 64 → latent, not immediate. |
| D-5.12 | Extractor identity is `"joern"` in Plan 5 (column + map key, internally consistent) but `"joern_indexer"` in a Plan 3 test fixture | No | ⚠️ | The reindex barrier polls the `extractor` *column* (`"joern"` throughout Plan 5) → the load-bearing path is unaffected; the mismatch is the `extractor_results` *map key*. See §6 (one canonical key). |
| D-5.13 | `import asyncio  # noqa: E402` placed mid-file in the Task 12 consumer module | No | ⚠️ | A `noqa`-suppressed stdlib import, kept mid-file so the public class reads first; the `noqa` carries a one-line rationale, but a suppressed stdlib import is still slop — move it to the top. |
| D-5.14 | `GraphRepository.execute_cypher` docstring (L2654-2657) asserts Plan 3's `connect` listener registers a psycopg agtype codec; Plan 3's listener does `LOAD 'age'` + `SET search_path` only — and Plan 8 L2325 correctly states the opposite | No | ⚠️ | A stale docstring, not a runtime break (both plans' code already does defensive `str(value).strip('"')`). Fix Plan 5's docstring. |

### Plan 6 — extractors + embeddings  *(no Deviations section; "deviat" appears 0×)*

| ID | Deviation | Declared | Class | Verdict |
|---|---|---|---|---|
| D-6.1 | `workers/sourcebot_indexer` **removed from scope** — placed in a "Defers To" section that defers to no plan | No (framed as deferral) | ⚠️ | Rationale (Git-pull indexer, SHA-pinned, indexes once) is plausible. Decide: amend spec §3.2/§4.1/§8.2, or restore. |
| D-6.2 | Embedding scope — file-type-blind worker; no guarantee docs are in the indexed set; no doc-paragraph/docstring-aware chunking | No | ❌ **G14** | The user's flagged example. See §5 G14. |
| D-6.3 | string-literal/comment-mention reference rules use a 3-char-minimum identifier filter | Yes (inline) | ⚠️ | Silent coverage cut vs the project's 100%-coverage doctrine; 2-char real symbols dropped. |
| D-6.4 | `ScipGraphRepository` builds Cypher by regex-allowlist + f-string interpolation, not parameter binding | Yes (long rationale) | ⚠️ | Sound for slice (all values CODEORACLE-internal); flagged for the security reviewer. |
| D-6.5 | Plan 6 integration test covers 3 extractors, not the §8.5 4-extractor fan-out | Yes (prose) | ❓ | 4-kind live E2E pushed to Plan 13. |
| D-6.6 | Plan 6 imports the worker base as `workers/base/…` (no underscore) with `RetryPolicy` in a separate `retry.py`; Plan 5 + Plan 7 + spec §3.2 all build `workers/_base/` with `RetryPolicy` inside `_base/message.py` | No | ❌ **P0-12** | Every `from codeoracle.workers.base… import …` in Plan 6 is an `ImportError`. Plan 6 is the lone outlier. Part of the Plan-6 import-map cluster — see §6. |
| D-6.7 | Plan 6 prose calls the terminal snapshot state `READY` (5 sites); Plan 3's enum is `PENDING/APPLYING/APPLIED/FAILED` and Plan 5 uses `APPLIED` | No | ⚠️ | `READY` is prose-only — no `SnapshotStatus.READY` code token exists, so no `AttributeError` site (the re-look's "code does not run" was overstated). A prose↔enum naming inconsistency. |
| D-6.8 | Plan 6 Task 10 imports `exec_cypher` / `exec_cypher_returning` from `codeoracle.db.age_driver` — no plan creates `codeoracle/db/age_driver.py` | No | ❌ **P0-13** | Task 10 (`scip_graph.py`) does not compile. Plan 5's AGE access is the `GraphRepository` class at `db/repositories/graph.py`; the `age_driver` module path is fictional. |
| D-6.9 | Plan 6 Task 10 writes AGE graph `codeoracle_code_graph`; Plan 3 bootstraps `create_graph('codeoracle')` only, and Plan 5's `GraphRepository` pins `_GRAPH_NAME = "codeoracle"` | No | ❌ **P0-14** | Plan 6 Task 10's Cypher fails at runtime against a graph that does not exist, and contradicts Plan 5's in-code comment. |
| D-6.10 | `EmbeddingsProviderFactory` placed in `core/config.py` — to construct providers it must import `adapters/llm/providers/*`, an upward `core → adapters` edge | Yes (prose rationale) | ❌ | Spec §3.3 forbids domain / adapter knowledge in `core/`; the sibling chat-LLM factory lives in `adapters/llm/`. A declared-but-wrong layer inversion → P1. |
| D-6.11 | The vLLM `/v1/embeddings` API contract (a spec §3.6 scaffolding-day-1 preflight gate) is demoted to a live smoke test gated behind an opt-in `--run-live-embeddings` flag, CI-off by default | No | ⚠️ | A blocking preflight silently downgraded to advisory. Distinct from P0-6 (the §3.6 *runner shape* + 4 named gates). |
| D-6.12 | `EmbeddingConfig` mixes `UPPER_CASE` fields (`EMBED_CHUNK_LINES`, `EMBED_CHUNK_OVERLAP`) with lowercase siblings (`model`, `dim`, …) | No | ⚠️ | A field-casing inconsistency within one env-overridable model. (The re-look's "ruff `N815` CI failure" is uncertain — `N815` targets mixedCase, not CONSTANT_CASE — and is not folded.) |
| D-6.13 | `_validate_embedding_config` rejects only `overlap >= chunk_size`; `chunk_lines=2, overlap=1` is accepted → 1-line stride → O(file) chunks | No | ⚠️ | The validator is presented as the forward-progress safety net but admits a degenerate config. Low real-world likelihood (defaults 100 / 20). |
| D-6.14 | Plan 6's "Inherits from" header mislocates 3 Plan-4-owned symbols — `LLMProvider` ABC at `adapters/llm/provider.py` (real: `interfaces.py`); `LLMConfig` + `LLMProviderFactory` in `core/config.py` (real: `adapters/llm/config.py` / `factory.py`) | No | ❌ | Spec, Plan 5 wiring, and Plan 10 all agree with Plan 4 — Plan 6 is the lone outlier. Three broken cross-references → P1. Part of the Plan-6 import-map cluster (D-6.6/6.8/6.9) — see §6. |
| D-6.15 | Plan 6 Task 10 adds `"src/codeoracle/adapters/llm/providers/litellm.py" = ["TID251"]` to ruff per-file-ignores — no plan creates `providers/litellm.py` | No | ⚠️ | Dead config: a lint-suppression for a file that does not exist, paired with the spec / Plan-4 `litellm` ban that guards a provider nobody writes. Slop on the Plan 4 ↔ Plan 6 edge. |

### Plan 7 — consumer + traces  *(no Deviations section)*

| ID | Deviation | Declared | Class | Verdict |
|---|---|---|---|---|
| D-7.1 | `TracesRepository` built at `db/traces.py`, not spec §3.2 `db/repositories/traces.py` | No | ❌ | Silent path drift. |
| D-7.2 | `workers/traces_ingest/` has no `tasks.py`; ships an unexplained `wiring.py`; polls Tempo HTTP instead of consuming a `traces.ingest` Valkey stream | Partly | ⚠️ | Poll-vs-stream is declared (sibling `ContinuousConsumer` ABC); the missing `tasks.py` is not. Spec §3.2 line "consumes `traces.ingest`" is now stale. |
| D-7.3 | Compose edits `infra/docker-compose.dev.yml` vs spec §3.4 `infra/compose/docker-compose.yml` | No | ⚠️ | Path mismatch (may be inherited from Plan 1/3's actual layout). |
| D-7.4 | `PGCursorStore` (SQL I/O) placed in `workers/_base/`, not `db/` | Yes | ⚠️ | Spec §3.3 layer rule is about *where DB I/O lives*; "not a domain entity" sidesteps it. Reviewer to rule. |
| D-7.5 | alembic revision chain broken in two places — Plan 6 `0005_code_references` has `down_revision="0004_snapshot_file_outcomes"` ≠ Plan 5's `revision="0004"`; Plan 7 `0006` has `down_revision="0005"` ≠ Plan 6's `revision="0005_code_references"` | No | ❌ **P0-15** | `alembic upgrade head` fails. Plans 5/6/7 use two incompatible revision-ID conventions (bare-numeric vs `NNNN_slug`). See §6. |
| D-7.6 | Plan 7 references `workers/_base/streams.py` at 9 sites; Plan 5/6 build `streams.py` directly under `workers/` | No | ❌ **P0-16** | Mechanical `ImportError`. Distinct from P0-12 (the `base`-vs-`_base` package-name break) — here the file is not under `_base/` at all. The advisory-lock-id registry rides in this same mis-pathed file. See §6. |
| D-7.7 | Plan 7 prose (L116, L1002) describes a `codeoracle.core.clock.utc_now` dependency attributed to Plan 3; Plan 3 builds no `core/clock.py`, and Plan 7's own code uses a local `_now_unix_nano` | No | ⚠️ | Prose-only — there are no `utc_now` call sites, so the code runs (the re-look's "ModuleNotFoundError at every call site" was overstated). Plan 7 mis-describes its own clock mechanism. |
| D-7.8 | Plan 7's Goal says `HandleResolverService` (Plan 8) "can enumerate observed services from `traces.spans`"; Plan 8 builds no `list_observed_services` method or `TracesRepository` consumer | No | ⚠️ | Plan 7 builds `TracesRepository` ahead for a consumer Plan 8 never wires — an undeclared cross-plan gap → P1. |
| D-7.9 | `ContinuousConsumer`'s exception family (`ContinuousConsumerError`, `TransientFetchError`, …) is defined in `continuous_consumer.py`; spec §3.2 designates `core/exceptions.py`, and Plan 5 puts its worker exceptions there | No | ⚠️ | A placement deviation from spec §3.2 + the sibling pattern; no functional break. Kin to D-7.4. |

### Plan 8 — handle resolution  *(no Deviations section; tightly scoped)*

| ID | Deviation | Declared | Class | Verdict |
|---|---|---|---|---|
| D-8.1 | `GraphQueryResolver` ignores `handle.snapshot_sha` — pushes the snapshot pin onto the LLM-authored Cypher | Yes (docstring) | ⚠️ | Asymmetry: code-span / doc resolvers honour `snapshot_sha` structurally; graph evidence only prompt-enforced. And the structurally-honoured `snapshot_sha` can itself be an unresolvable dirty-tree `+wt` SHA (G21) — so the structural reassurance is partial. |
| D-8.2 | Spec §6 worked graph_query example (`RETURN c`) declared non-conforming to Plan 8's scalar-only constraint; fix shifted to Plan 10 as an "inherited hard constraint" | Yes (labelled "hard constraint") | ⚠️ | Effectively a deviation from the spec's only concrete example; reconcile in spec §6. |
| D-8.3 | Resolver path does zero retry on transient Sourcebot errors | Yes (docstring) | ✅ | One-shot verify call — defensible; noted only. |
| D-8.4 | `SourcebotClient.fetch_span` hard-commits a `GET /api/source` contract (params, JSON body, Bearer auth); Plan 2 Task 8 verifies only that SourceBot's image resolves, the container runs, and health returns 200 — explicitly "NOT … SourceBot's full indexing pipeline" | No | ⚠️ | An entire external HTTP contract with no preflight. All 20 Task-5 `respx` tests mock the unverified shape → green by construction; only the skip-by-default live test would catch a mismatch → P1. |
| D-8.5 | Plan 8's Section-7 cross-plan dependency table — presented as the authoritative dep ledger — labels `respx` a "runtime + dev" dependency; Plan 7 adds it under `[project.optional-dependencies].test` (test-only) | No | ⚠️ | Doc-accuracy defect in the artifact other plans cite for dependency truth. |
| D-8.6 | `HandleResolverService` (a `services/`-layer class) catches / propagates `GraphQueryError`, which Plan 5 defines as a `TransientWorkerError` → `WorkerError` | No | ⚠️ | A worker-family exception surfacing in a non-worker service — a layer-vocabulary smell; functionally fine (resolves to `CodeOracleError`). |

### Plan 9 — verifier + trust matrix  *(has a "Deviations from the spec §6 code sketch" section — 8 items)*

All 8 declared items are sound mechanical reconciliations (import paths, `ClassVar`, named constants,
`from __future__ import annotations`) **except D-9.1 / D-9.2 below**; the re-look pass adds one
further undeclared finding, **D-9.3**:

| ID | Deviation | Declared | Class | Verdict |
|---|---|---|---|---|
| D-9.1 | Evidence-free-citation guard placed in `VerifierService.verify()` | Yes | ⚠️ | The bug it closes is real; the *placement* is wrong. Plan 9 itself calls the model-level fix (`Field(min_length=1)` on `DraftCitation.excerpt`/`Citation.excerpt`) "structurally tighter," then declines it "to avoid reopening a committed plan." This is pre-coding review — Plan 3 is a *document*. Fix belongs in Plan 3's model; drop the runtime guard. |
| D-9.2 | Plan 9 claims its unit tests pin Plan 8's `GRAPH_QUERY` separator contract | — | ⚠️ | The diagnosis holds — the round-trip test feeds a `_FakeResolver` seeded with Plan 9's *own assumption* of Plan 8's separator contract. But the fix is **in-plan and cheap**: Plan 8's `stringify_cypher_rows` is a module-level helper "exposed for direct unit testing" — Plan 9's Task-4 unit test can import it and use its real output on both sides, failing the instant Plan 8 changes `separators`. Not deferred to Plan 13. See §6. |
| D-9.3 | Production logging uses `structlog.make_filtering_bound_logger(log_level)` (Plan 3 L959); Plan 9 emits the citation-resolve outcome at `.info`; spec §8.5 criterion 11 requires one info-level structured log line per resolution. At a production `log_level ≥ WARNING` the info line is filtered — and Plan 9's own test fixture pins the *non-filtering* `BoundLogger`, masking it | No | ⚠️ | A genuine three-way seam: spec §8.5 wants it logged, Plan 3's logger filters it, Plan 9's test hides it. Criterion 11 is unsatisfiable for the most common outcome in prod → P1. See §6. |

### Plan 10 — agent orchestration  *(has a "Deviations from the spec §4 agent sketch" section — 9 items)*

The 9 declared items (deterministic intent, one LLM model, compose-selects, single-shot compose, …)
are sound. Additional findings:

| ID | Deviation | Declared | Class | Verdict |
|---|---|---|---|---|
| D-10.1 | `traces_query` node dropped → 6 nodes; §8.5 gate 9 `traces.query` span can never fire | Yes (Deviation 1) | ⚠️ **P0-7** | Plan 10 *demands* Plan 13 amend gate 9. Hard dependency on an unwritten plan. |
| D-10.3 | `services/query_planner.py` (spec §3.2) removed — "reactivation trigger" named, **no owner plan** | Yes (Deviation 3) | ⚠️ | Plan 5 still defers `query_planner.py` integration to Plan 8 as if the file is live — contradiction. Amend spec §3.2 + Plan 5. |
| D-10.U1 | `agent/__main__.py` not built; spec §3.2 + §3.3 make per-process `__main__.py` a convention | No (closure says "see Deviations" — but it isn't there) | ❌ | Half-declared. `agent/` left with no entrypoint. |
| D-10.U2 | All 6 nodes — *and the module-level `resolve_to_citation` helper* — are decorated / plain free functions, not constructor-injected classes | No | ❌ | The headline OOP drift — see §2. No written justification; not in the Deviations section. `resolve_to_citation` opens a span, calls `resolver.resolve`, and builds a `DraftCitation` — not a pure transformation and not even a LangGraph node, so the framework-idiom defence does not reach it (re-look M-10.3). |
| D-10.U3 | Plan 10's tests use a private `_FakeLLM`, not the spec-§5.7 `MockLLMProvider` + `llm_scripts/` | Partly | ⚠️ | Fine for isolated unit tests, but `template_id`/request-fingerprint is never exercised. Confirm Plan 13 integration tests use the real `MockLLMProvider`. |
| D-10.U4 | Architecture summary (line 7) says nodes bound "via `functools.partial`"; Task 9 explicitly rejects `functools.partial` | No | ⚠️ | Internal inconsistency; the code is authoritative, the summary is stale. |
| D-10.U5 | Plan 8 states a "HARD CONSTRAINT" that the agent's Cypher emit `RETURN … AS result`; Plan 10's `build_callers_cypher` emits `AS caller_path` | No | ⚠️ | Harmless at runtime — AGE's `cypher()` SQL wrapper names the result column positionally (`AS (result agtype)`), so `row.get("result")` works regardless of the openCypher alias. But the stated constraint and the emitter disagree on paper — align them. (re-look-10 graded this a P0 "[null] excerpt"; that is mechanically wrong — rejected as P0, kept as a P2 consistency wart.) |
| D-10.U6 | `embed_query` calls `EmbeddingsRepository.knn(vectors[0], sha, KNN_K)` positionally; Plan 6 declares `knn(self, *, query_embedding, snapshot_sha, k)` — keyword-only | No | ❌ **P0-17** | `TypeError` at runtime. Plan 10's conftest fake declares `knn` *without* the `*` → unit tests pass green against a fake whose signature contradicts the real repository. See §6. |
| D-10.U7 | `intent_node` uses `query.snapshot_sha` verbatim on the pinned path with no APPLIED-snapshot check; the unpinned path correctly uses `get_latest_applied()` | No | ⚠️ | A query pinning a non-APPLIED / un-indexed SHA yields a misleading "symbol not found" UNKNOWN rather than a clear "snapshot not indexed" — wrong reason, not a crash. |
| D-10.U8 | No end-to-end test that an LLM confidence below `PROVEN_CONFIDENCE_FLOOR` (0.6) makes the agent surface UNKNOWN | No | ⚠️ / ❓ | The floor logic is Plan-9-owned and Plan-9-tested at the unit level; the agent-level path is unexercised. A test-depth gap. |
| D-10.U9 | `embed_query` builds `file://` CODE_SPAN handles from `EmbeddingHit.source_path` with no test pinning the path-format contract between Plan 6 (writes `source_path`) and Plan 8's `CodeSpanResolver` (reads it → Sourcebot) | No | ⚠️ | Format drift (relative vs absolute, snapshot prefix) → silent CODE_SPAN resolution miss. Clusters with G21 as "Sourcebot resolution-input hygiene." |
| D-10.U10 | `KNN_K = 4`; spec §4.2's worked agent flow calls `knn(query_vec, k=5)` | No | ⚠️ | An undeclared divergence from a spec parameter (grep-verified: spec L346 `k=5`, Plan 10 L1674 `KNN_K = 4`). |

---

## 5. Undeclared gaps — silent drops

**The most important section.** Each item below is a spec-required artifact or capability with
**no owner among Plans 1–10 and no deferral that names a real owner.** Unlike §4 deviations
(something built differently), these are things **not built at all** and **not acknowledged**.

### Infra / CI orphans (Plan 1 / infra scope)

Verified by direct `grep` across all 10 plan files — the strings below return **zero or
consume-only hits**.

| ID | Missing artifact | Spec ref | Evidence | Impact |
|---|---|---|---|---|
| G1 | `Makefile` | §3.1 | `grep -i makefile` → 0 hits in any plan | Spec calls it "thin wrappers around `uv run`". No dev/CI entrypoint. |
| G2 | `.github/workflows/image-build.yml` | §7.1, §7.3 | `grep image-build` → 0 hits | §8.5 gate 6 (`trivy image` ×5 variants) cannot run. |
| G3 | `.github/workflows/security.yml` (discrete file) | §7.1 | folded into `ci.yml` (D-1.5) | Checks exist; the spec's 4-file workflow layout does not. |
| G4 | `.github/workflows/lockfile-refresh.yml` | §7.1 | `grep lockfile-refresh` → 0 hits | Weekly `uv lock --upgrade` never scheduled. |
| G5 | `CODEOWNERS` | §3.1, §7.8 | `grep CODEOWNERS` → 0 hits | No review routing. |
| G6 | `infra/docker/Dockerfile` + `Dockerfile.model-server` | §3.4 | only `Dockerfile.joern` *referenced* (Plan 5), never created | No image to build or scan; blocks G2. |
| G7 | `infra/observability/` | §3.4 | 0 hits | OTel collector / Tempo / Grafana config absent — ties to P0-2. |
| G8 | `bootstrap_dev.sh`, `seed_test_corpus.sh` | §3.4 | only `bootstrap_db.sh` built (Plan 3) | Dev bring-up + test-corpus seeding unowned. |
| G9 | `infra/security/semgrep/codeoracle-custom.yml` | §7.4 | 0 hits | Custom semgrep rules never written; CI runs only registry rulesets. |
| G10 | `infra/config/defaults.env` | §3.4 | Plan 8 **modifies** it (×3); **no plan creates it** | Plan 8 appends to a file that does not exist. |
| G12 | `utils/` layer + `utils/async_helpers.py` | §3.2 | Plan 1 omits `utils/` from the scaffold loop; 0 hits elsewhere | A spec layer absent from the skeleton. |
| G20 | `astral-sh/uv-pre-commit` / `uv-lock` pre-commit hook | §7.7 | `.pre-commit-config.yaml` ships ruff / gitleaks / pre-commit-hooks / ty only | The spec-named hook that keeps `uv.lock` in sync on every commit is absent — lock drift goes uncaught locally. P1. |

**These will not be absorbed by Plans 11–13** (API / MCP / slice). They are Plan 1 / infra scope.
Either Plan 1 is expanded, or a dedicated infra plan is inserted. See §6.

### Broken deferral chains

| ID | Capability | The broken chain | Class |
|---|---|---|---|
| G11 | `core/telemetry.py` | Plan 3 → "Plan 7 owns it" (×3); Plan 7 builds nothing; Plan 10 → "Plan 11/13". | ❌ **P0-2** |
| G13 | `db/repositories/evidence.py` (`EvidenceRepository`) | Spec §3.2 lists it. Zero mentions in any plan. Plan 3's repository-handoff list (D-3.0) omits it entirely. | ❌ **P0-3** |
| G16 | stale-`APPLYING` janitor | Plan 5 defers it to "Plan 8 or Plan 7, whichever lands the API service first." Neither is the API plan; neither builds it. Real owner is Plan 11+. | ❌ |

### Capability gaps

| ID | Capability | Detail | Class |
|---|---|---|---|
| G14 | **Docs as evidence ("embedding the docs")** | Spec §1.2 graph includes "docs"; §6.1 defines `DOC_PARAGRAPH`; §8.2 item 11 says "embed every file". Plan 6's worker is file-type-blind but **nothing guarantees `docs/`/`*.md` are in the indexed set** (silently delegated to Plan 5, no assertion, no test). No paragraph/docstring-aware chunking — a doc that *is* embedded is split into 100-line blocks, so `DOC_PARAGRAPH` has no paragraph-granular producer. Conversely, the orchestrator fans every changeset file to *every* extractor, so a doc file that does reach the pipeline is also routed to joern/scip/opengrep, which cannot parse it. Plan 10 Deviation 6 **declares** it drops `DOC_PARAGRAPH` citations. `DocParagraphResolver` (Plan 8) is a reader with no producer. Doc-file handling is unspecified and untested in both directions (grep-verified 2026-05-22). | ❌ half-silent (Plan 6), half-declared (Plan 10) |
| G15 | **Graph FQN scheme is incoherent across plans** | Plan 5's Joern parser emits node FQNs as `<file>::<name>` — **grep-verified**: Plan 5 Task 12's parser test asserts `fqn:'src/example.py::foo'` (D-5.1). The slice query + Plan 8 graph queries assume dotted Python FQNs. Plan 10 Deviation 7 *works around this* by locating symbols via embed-knn instead of a precise FQN graph query — and flags that workaround as accuracy-risky ("reliability depends on FQN density"). The `:Definition` line-range schema is "underspecified across Plans 5/6". | ❌ **P0-8** — no plan pins the FQN/`:Definition` contract |
| G17 | `POST /v1/reindex` | `codeoracle index --since` built (Plan 5); the HTTP reindex endpoint is deferred to Plan 11 (unwritten). | ❓ |
| G21 | **Dirty-tree SHA unhandled in resolution** | Plan 3's `compute_dirty_tree_sha` yields `<head>+wt<12hex>`; it flows into `EvidenceHandle.snapshot_sha`, and Plan 8's `CodeSpanResolver` / `SourcebotClient.fetch_span` pass it verbatim as Sourcebot's git `ref`. No plan strips `+wt`; Sourcebot resolves `ref` as a git ref → a `+wt` value 404s → dirty-tree CODE_SPAN resolution (spec §8.7's live-edit demo) breaks. Task 5's 20 `respx` cases and the Task 6 live test never exercise a `+wt` ref — unhandled **and** untested. | ❌ — see §6 DECIDE |

### Plan-document defects (not spec gaps, but fix before executing)

| ID | Defect | Detail |
|---|---|---|
| G18 | Plan 7 self-review cites a non-existent test | `test_ingest_stats_rejects_regressing_cursor` "in Task 4" — verified absent; the real tests are `…_rejects_persisted_greater_than_fetched` / `…_rejects_negative_elapsed` in Task 2. TDD red-green would target a test that does not exist. |
| G19 | Plan 5 ships committed commented-out code | `workers/streams.py` + `services/indexing/orchestrator.py` carry commented-out future constants. Direct CLAUDE.md "no commented-out blocks" violation. |

---

## 6. Pre-coding action list

Two kinds of action. **DECIDE** = a human/project judgement call with no obvious default —
needs an answer before the affected plan codes. **EDIT** = a mechanical plan-or-spec change once
the decision (if any) is made. **Nothing here has been actioned.**

### DECIDE — judgement calls, ordered by blocking urgency

| ID | Decision needed | Blocks | Recommendation |
|---|---|---|---|
| P0-1 / D-2.1 | Embedding model + dimensionality: OpenAI `text-embedding-3-small` (1536) as pinned, or spec §9.3's `bge-large` (1024)? | **Plan 3** (pgvector migration) | OpenAI 1536 is consistent with the OpenAI dev-default already chosen. Close spec §9.3's open question to match, or consciously keep `bge`. Decide before Plan 3 — re-indexing later is a one-way door. |
| P0-8 / G15 / D-5.1 | Graph FQN scheme — keep Joern's native `<file>::<name>`, or normalize to a dotted, language-aware FQN at parse time? Also pin the `:Definition` line-range schema. | **Plan 5** (Joern parser); Plan 8 / Plan 10 (consumers) | Normalize in `JoernCpgParser` so graph queries and `explain_symbol` resolve precisely — embed-kNN as the primary symbol locator (Plan 10 Deviation 7) is explicitly flagged unreliable. Pin the contract in the spec before Plan 5 codes the parser; re-emitting FQNs later forces a full graph re-index. |
| P0-3 / G13 | Is `EvidenceRepository` slice-critical, or genuinely Slice+1? | Plan 9 / Plan 11 | If the slice persists `Claim`/`Verdict` rows, it is needed and must be assigned an owner. If the slice is query-only (in-memory), amend spec §3.2 to mark it Slice+1. |
| P0-5 / G1–G12 | Who owns the ~10 infra/CI orphans — expand Plan 1, or insert a dedicated infra plan? | Plans 11–13, CI gates | Insert one infra plan (or expand Plan 1's scope explicitly). Plans 11–13 are API/MCP/slice and cannot absorb CI workflows. |
| P0-6 / D-2.2 | Should §3.6 verification keep the spec's `preflight.sh` runner shape, or is Plan 2's env-linter the new intent? | Plan 2 re-exec, §3.6 contract | Restore a real `preflight.sh` gate runner under `.cache/preflight/results.md`, OR amend spec §3.6 to match Plan 2 — but the 4 named gate commands must run *somewhere* as written. |
| P0-7 / D-10.1 | Will Plan 13 amend §8.5 gate 9 to drop the `traces.query` span? | Plan 13, slice acceptance | Confirm explicitly when Plan 13 is written, or restore a `traces_query` node. An unconfirmed cross-plan demand = a latent acceptance failure. |
| D-1.4 | CI coverage floor — 80 or 85? | Plan 1 | Spec §8.5 says 85. Decide and make it consistent across `pyproject`, `ci.yml`, README. |
| D-3.1 | AGE `search_path` order — spec §3.5's order is the latent bug (an unqualified `CREATE TABLE` lands in the first *existing* schema → spec's order pollutes `ag_catalog`; Plan 3's order is correct). | Plan 3, spec §3.5 | Amend spec §3.5 to Plan 3's order (`"$user",public,ag_catalog`); do not leave the disagreement in a code comment. |
| D-6.1 / G14 | Are `sourcebot_indexer` and docs-as-evidence intentionally Slice+1? | Plans 6/8/10, spec §3.2/§8.2 | If yes — amend spec §3.2/§4.1/§8.2 and state it in Plan 6. If no — the docs leg needs a producer and the agent needs to emit `DOC_PARAGRAPH`. |
| D-10.U2 | Accept Plan 10's functional `@agent_node` nodes, or require the `NodeBase` ABC + node classes CLAUDE.md mandates? | Plan 10 | Either refactor to node classes, or add an explicit, reasoned exception to Plan 10's Deviations section. Silence is not an option. |
| D-5.10 / Joern | **Joern vertical: per-file extraction vs cross-file call graph.** `JoernClient` extracts one file at a time and `replace_file_subgraph` writes one file's nodes; but `find_callers` / `DeltaComputer` / Task 15 need `CALLS` edges that cross files. A per-file pass cannot resolve a reference into another file. Does the parser need a second symbol-linking pass (MERGE by FQN), or a whole-snapshot CPG? | **Plan 5** (parser) | **P0-severity — but a design question, not a transcription error, so deliberately *not* in the §1 P0 table.** Decide before Plan 5 codes: a per-file pass plus a cross-file FQN-resolution pass is the lighter fix; pin the linking contract first. The sharper claim that Joern emits no `File`-rooted `CALLS` edge needs a Joern run to confirm — but the per-file⇏cross-file gap alone already proves the vertical is incomplete. |
| G21 / M-8.1 | **Dirty-tree `+wt` SHA → Sourcebot `ref`.** A `<head>+wt<12hex>` snapshot SHA cannot be a git ref. Strip `+wt` before the Sourcebot call, resolve it to the underlying head SHA, or accept that dirty-tree CODE_SPANs are unresolvable and surface UNKNOWN. | Plan 8 (resolver) | Strip-to-head is the pragmatic choice for the slice (the dirty-tree delta is small). Whichever is chosen, add a `+wt`-ref test — the case is currently unhandled *and* untested. |
| D-9.3 / M-9.1 | **Spec §8.5 criterion 11 vs the filtering logger.** Criterion 11 wants an info-level structured log per citation-resolution; Plan 3's production logger is `make_filtering_bound_logger`, which drops `info` below a `WARNING` floor. | Plan 9, slice acceptance | Either log the resolve outcome at a level criterion 11's check accepts at production verbosity, or amend criterion 11 to assert the resolve log only at the verbosity its own gate runs under. Do not leave Plan 9's test pinning a non-filtering logger — that masks the gap. |
| D-1.13 | **`requires-python`.** Runtime policy is `>=3.14`; Ruff target is `py314`; local and CI interpreters are Python 3.14. | Plan 1 | Resolved by the 2026-06-13 Python 3.14+ decision. Keep project metadata, tool configs, CI, and runtime images aligned to 3.14+. |

### EDIT — mechanical fixes once the above are settled

| ID | Edit | Target |
|---|---|---|
| D-3.0 / D-4.7 / P0-4 | Re-verify and correct **every** "Plan N" forward-reference in Plan 3 *and Plan 4* (telemetry → real owner; repositories → Plan 5; `api/deps.py` + `aclose()` owner → Plan 11). | Plan 3, Plan 4 |
| G11 / P0-2 | Assign `core/telemetry.py` a real owner and fix the Plan 3 / Plan 10 deferral text. | Plan 3, Plan 10, owner plan |
| G16 | Re-target the stale-`APPLYING` janitor deferral to the actual API plan (11). | Plan 5 |
| D-10.3 | Reconcile `query_planner.py`: drop from spec §3.2, and remove Plan 5's "defers to Plan 8 … `query_planner.py`" line. | spec §3.2, Plan 5 |
| D-1.1 | `mcp_gateway/` → `mcp/` (or amend spec §3.2). | Plan 1 + all consumers |
| D-1.2 | `check_file_length.sh` → `.py` (or amend the 4 spec references). | Plan 1 |
| D-1.3 | `renovate.json` (root) → `.github/renovate.json5`. | Plan 1 |
| D-7.1 | `db/traces.py` → `db/repositories/traces.py`. | Plan 7 |
| D-3.4 / D-4.4 | Fix `CODEORACLE_LLM__` → `CODEORACLE_LLM_` env prefix consistently. | Plan 3, Plan 4 |
| D-4.1 | `request_fingerprint` must hash the spec §5.7 "key parameters" (temperature, `max_tokens`, model, `structured_output`). | Plan 4 |
| D-9.1 / M-9.2 | Move the evidence-free-citation check to `Field(min_length=1)` on Plan 3's `DraftCitation`/`Citation` excerpt; drop the `verify()` runtime guard. Plan 9 L63 itself names this the "structurally tighter" fix but defers it as "interim" with no owner — assign it to Plan 3. | Plan 3, Plan 9 |
| D-9.2 | Plan 9's Task-4 separator round-trip test must import Plan 8's real module-level `stringify_cypher_rows` helper and assert against its actual output on both sides — replacing the `_FakeResolver` seeded with Plan 9's own assumption of the separator contract, so the test fails the instant Plan 8 changes `separators`. | Plan 9 |
| D-10.U1 | Add `agent/__main__.py`, or add a real Deviations entry justifying its absence. | Plan 10 |
| D-10.U4 | Fix Plan 10's line-7 Architecture summary (`functools.partial` → the actual closure mechanism). | Plan 10 |
| G18 | Correct Plan 7's self-review test name + task attribution. | Plan 7 |
| G19 | Delete the commented-out future constants from `streams.py` / `orchestrator.py`. | Plan 5 |
| G20 | Add the `astral-sh/uv-pre-commit` `uv-lock` hook to `.pre-commit-config.yaml` so `uv.lock` drift is caught at commit time (spec §7.7). | Plan 1 |
| D-5.8 | Resolve `cli/__main__.py` ownership — Plan 1 must scaffold `cli/`, or Plan 5 must create it. | Plan 1, Plan 5 |
| D-2.6 / P0-9 | Align the pgvector install-path key across Plan 2 and Plan 3 — one key (recommend `PGVECTOR_INSTALL_METHOD` ∈ {`preinstalled`, `separate-image`, `custom-dockerfile`}); Plan 3's gate must accept all valid values. | Plan 2, Plan 3 |
| D-4.6 / P0-10 | Plan 4 Task 1 Step 4 must **edit** Plan 1's `banned-api` block, not append a second table header; reconcile the two on one module list + message. | Plan 4 |
| D-4.10 | `test-live-llm.yml` — add `continue-on-error: true` to the live-LLM job, or document it as a non-required check, per spec §5.7's "advisory" contract. | Plan 4 |
| D-5.9 / P0-11 | Fix Plan 5's three `make_async_engine` / `make_async_session_factory` call sites (L1776, L5180–5181) to Plan 3's signatures — `make_async_engine()` no-arg, `make_async_session_factory(engine)`. Plan 7's call sites are already correct; Plan 5 is the lone outlier. | Plan 5 |
| D-6.6 / 6.8 / 6.9 / 6.14 | **Plan 6 — full cross-plan-reference re-audit before coding.** Four Plan-6 defects (P0-12/13/14 + the LLM-symbol paths) share one root cause: a stale "Inherits from" map. Re-verify every `from codeoracle.X import Y` and every cited path in Plan 6 against the plan that owns X — the same sweep P0-4 / D-3.0 prescribes for Plan 3. | Plan 6 |
| D-7.5 / P0-15 | Adopt one alembic revision-ID convention (bare-numeric or `NNNN_slug`) across Plans 5/6/7 and fix both broken `down_revision` links. | Plans 5, 6, 7 |
| D-7.6 / P0-16 | Repath all 9 Plan 7 references from `workers/_base/streams.py` to `workers/streams.py` (the path Plans 5/6 build). The advisory-lock-id registry rides in that file — confirm it resolves at the corrected path. | Plan 7 |
| D-10.U6 / P0-17 | Convert Plan 10 `embed_query`'s positional `knn(vectors[0], sha, KNN_K)` to keyword args matching Plan 6's `knn(*, query_embedding, snapshot_sha, k)` — **and** add the `*` to Plan 10's conftest `knn` fake, so a future signature drift fails the unit tests instead of passing green. | Plan 10 |
| D-5.12 / M-5.8 | Pick one canonical extractor key (`"joern"` or `"joern_indexer"`) and use it in the `extractor` column, the `extractor_results` map, and every test fixture across Plans 3/5/6. | Plans 3, 5, 6 |

---

## 7. Reconciliation against HEAD (`d26f0ef`, 2026-06-01)

This section is the **current-state layer**. Everything above (§0–§6) is the frozen 2026-05-22 audit; this
section reconciles each finding against the plans **as they actually stand at HEAD `d26f0ef`** and is the
authority wherever the two disagree. Method: an anchor pass over the commit log (finding-IDs named in resolver
messages) + Phase-K certification, then **13 read-only by-plan Verifier agents** each reading one plan at HEAD,
aggregated here. Governing principle: **a commit message is a POINTER, not a VERDICT** — a resolver SHA only
says *where to look*; the verdict comes only from the plan's current text satisfying the finding's required edit.
Error asymmetry (a buried defect is worse than a re-check) → **any doubt resolves to OPEN**, and every
RESOLVED/DECIDED cites a specific current line.

**Three frozen-body claims this section supersedes (quoted verbatim):**

- §-header / §0 — *"No plan or code has been changed"* and *"intentionally not committed"* → **SUPERSEDED.**
  122 of 123 reconciled finding-legs are RESOLVED or DECIDED at HEAD (the lone remainder is **D-4.12, MOVED** —
  a cite-only drift fixed in place); the file is now committed. *(The G16 pass took this 83→86 — §7.4 #15;
  D-10.U2's flip then took it to 87 — §7.2 D-10.U2 row / task #74; the close-out pass this session flipped the
  remaining 34 OPEN + 1 DANGLING legs to RESOLVED, taking 87 → 122.)*
- §0 (line ~26) — *"Plans 11–13 are not yet written."* → **SUPERSEDED.** All three exist. The seven ❓ rows in
  §3's coverage matrix (items **47, 49, 58, 64, 65, 66, 69**) all flip ❓→✅ — owners Plan 11 (×2), Plan 12 (×1),
  Plan 13 (×4); see §7.2 "Plans 11–13".
- §6 (line ~518) — *"Nothing here has been actioned."* → **SUPERSEDED.** The §6 DECIDE/EDIT action-list is mapped
  item-by-item to HEAD status in §7.5; the large majority are already done.

**The three canaries the reconciliation was opened to check — all RESOLVED at HEAD:**

- **P0-11 / D-5.9** (the named canary) — **airtight.** Every call site passes `make_async_engine()` /
  `make_async_session_factory(engine)` (Plan 5 L2194-95, L6627-28, L6487-88); no `Settings` object anywhere; the
  ledger-cited broken lines L1776 / L5180-81 no longer exist.
- **D-4.7** (api/deps.py ownership) — RESOLVED. `api/deps.py` attributed to Plan 11 at every site (Plan 4
  L3325/L3373); no "Plan 7" attribution remains (resolver 903052d).
- **D-5.14** (psycopg agtype-codec docstring) — RESOLVED. Docstring now correctly states "no psycopg agtype codec"
  (Plan 5 L3231-34); the ledger's L2654-57 cite is historical (the finding is resolved — cite left unchanged).

### 7.1 P0 rollup (all 17)

| P0 | Verdict | Canonical ID | Evidence at HEAD / resolver |
|---|---|---|---|
| P0-1 | DECIDED | D-2.1 | embedding dim → 1536 (`text-embedding-3-small`); bge-large superseded. Spec changelog L20 + §9.3 L1494; Plan 2 pins 1536 (L1079/1095) |
| P0-2 | RESOLVED | G11 | telemetry owner corrected — "Plan 1A owns `core/telemetry.py`" (Plan 3 L863/L4344); resolver 2703942 |
| P0-3 | DECIDED | G13 | EvidenceRepository → Plan 3 L4343 "Slice +1" deferral (empirically verified); Plan 11 confirmed never to consume it |
| P0-4 | RESOLVED | D-3.0 / D-4.7 | Plan 7 = "traces-ingest" (L21); repos attributed to Plans 5–7; `api/deps.py` → Plan 11 (Plan 4 L3823/L3871); resolver 903052d |
| P0-5 | RESOLVED | G1–G12 (umbrella) | Plan 1A is the one infra plan (goal L5, ownership backstop L18); resolver f686f04 |
| P0-6 | RESOLVED | D-2.2 | `preflight.sh` + `.cache/preflight/results.md` restored at spec paths (Plan 2 L1279/1385/1406); resolver bfa7731 |
| P0-7 | RESOLVED | D-10.1 | Deviation 1 + blocking Plan-13 gate-9 intact (Plan 10 L120/3057); Plan 13 Task 11 drops `traces.query` (L2774→2779); resolver 493730e |
| P0-8 | DECIDED (FQN half) · RESOLVED (link half) | D-5.1 / D-5.10 | dotted-FQN scheme pinned + regression test (Plan 5 L39/4396/4582-96). **The cross-file-symbol-linking half of the same §3.7 contract — numbered separately as D-5.10/G15 — is now RESOLVED (this pass, Option B): Plan 5 adopted the linking pass + conformed `find_callers`/`DeltaComputer`; the spec's `:Definition` adoption was reverted to `:Symbol` (§7.3 / §7.4 #11).** |
| P0-9 | RESOLVED | D-2.6 | `PGVECTOR_INSTALL_METHOD` ∈ {preinstalled, separate-image, custom-dockerfile} all 3 branches (Plan 2 L373/380/388); resolver e5cfcfb |
| P0-10 | RESOLVED | D-4.6 | Task 1 Step 4 now VERIFIES Plan 1's banned-api block, "must NOT append" (Plan 4 L145-166); resolver f1b4ca4 |
| P0-11 | **RESOLVED (canary — airtight)** | D-5.9 | every site `make_async_engine()` / `make_async_session_factory(engine)` (Plan 5 L2194-95/6627-28/6487-88); no `Settings`; ledger-cited L1776/L5180-81 gone |
| P0-12 | RESOLVED | D-6.6 | imports `workers/_base/message` (Plan 6 L4855); no `workers/base`, no `retry.py` |
| P0-13 | RESOLVED | D-6.8 | composes `db/repositories/graph` (Plan 6 L4605/4652); no `age_driver` import |
| P0-14 | RESOLVED | D-6.9 | graph `cypher('codeoracle', …)` (Plan 6 L4565); no `codeoracle_code_graph` |
| P0-15 | RESOLVED | D-7.5 | bare-numeric alembic chain 0006→0005→0004 unified (Plan 7 L3528-29/387-88); resolvers c4b6a3e/d5fb4f1 |
| P0-16 | RESOLVED | D-7.6 | all stream refs → `core/streams.py` post-relocation (NFR #66); no `workers/_base/streams`; resolver d5fb4f1 |
| P0-17 | RESOLVED | D-10.U6 | keyword `knn(*, …)` call (Plan 10 L1917-21) + `*` in conftest fake (L1006-12); resolver 2703942 |

**P0 tally: 14 RESOLVED · 3 DECIDED (P0-1, P0-3, P0-8) · 0 open at the P0-table level.** The cross-file-linking
obligation under P0-8's §3.7 contract — numbered separately as **D-5.10 / G15** — was the last P0-*severity*
item open; it is **RESOLVED this pass (Option B — Joern keeps `:Symbol`, linking pass adopted; §7.3 / §7.4 #11).**
**No P0-severity work remains.**

### 7.2 Per-finding reconciliation (the authority)

Verdict tokens: **RESOLVED** (current text satisfies the edit) · **DECIDED** (closed by an explicit
design decision / spec amendment, not the ledger's prescribed edit) · **OPEN** · **MOVED** (finding stands;
only the cite drifted) · **DANGLING** (the described defect no longer exists / was removed, not corrected in
place). "R-except-residual" = resolved but one leftover instance survives (recorded in §7.4 — as of this
pass every such residual, D-3.1 / D-3.4 / D-7.1, is now fully RESOLVED; the token is retired).

**Plan 1 — repo-skeleton-and-cicd** — 14 R · 2 DECIDED · 0 OPEN

| ID | Verdict | Resolver | Evidence at HEAD / note |
|---|---|---|---|
| D-1.1 | RESOLVED | 1d384b2 | Task 2 scaffolds `mcp/` (L223/234/287); no `mcp_gateway` token remains |
| D-1.2 | RESOLVED | 1d384b2 | `check_file_length.py` present + invoked (L1111/1186/1212/1236) |
| D-1.3 | RESOLVED | 1d384b2 | `.github/renovate.json5` (L1277/1280/1346); no root `renovate.json` |
| D-1.4 | DECIDED → 85 | — | `fail_under=85` consistent across ci/README/Task 14 (L696); zero stray 80 |
| D-1.5 | RESOLVED | — | Plan 1 `ci.yml` ships four jobs (lint/type/test/file-length-gate); the security surface is deferred to Plan 1A's discrete `security.yml` per spec §7.1. No folded security job remains (live Plan-1 L5/L998-1012/L1477; Plan-1A confirms `security.yml` owns it) |
| D-1.6 | RESOLVED | — | the `p/security-audit` vs `p/owasp-top-ten` divergence is moot: Plan 1 no longer runs semgrep; the canonical `p/owasp-top-ten` invocation lives in Plan 1A's `security.yml` (Plan-1A L2333). No `p/security-audit` anywhere in Plan 1 or 1A at HEAD |
| D-1.7 | RESOLVED (PLAN-WINS) | — | spec §7.5's `[tool.ty]` block is invalid (`rules.strict`/`src.root` are not ty keys); Plan 1 expresses strict intent via ty's real schema (`environment.root`, `src.respect-ignore-files`, `rules.all = error`). Spec §7.5 amendment applied (§A1) |
| D-1.8 | RESOLVED | — | `ANN`+`C90`+mccabe max-complexity=10 now in select (L385-393) |
| D-1.9 | RESOLVED | — | Task 2 scaffolds the three-way `tests/{unit,integration,characterization}/` split matching spec §7.5 |
| D-1.10 | RESOLVED | — | Plan 1 uses the `providers/*.py` glob per spec §5.6. Cross-plan edit applied: Plan 4 Task 1 Step 5 import-boundary check flips to verify the glob (drops the "rejects wildcard" assertion) — §B1; gates this flip |
| D-1.11 | RESOLVED | — | `ci.yml` conforms to spec §7.2 shape (ubuntu-24.04, concurrency cancel-in-progress, cache-dependency-glob, codecov). The Postgres service is a documented deferral to Plan 3 (owns DB bootstrap + integration-test infra) |
| D-1.12 | RESOLVED (PLAN-WINS) | — | `package = true` is required for the project to be importable/installable, orthogonal to `managed` (default true). Spec §7.5 amendment applied (§A1) |
| D-1.13 | DECIDED → >=3.14 | — | spec §7.5 amended to `>=3.14` (spec L1149, changelog L19); plan + spec agree |
| D-1.14 | RESOLVED | — | `.env.example` pins `claude-opus-4-8` per decided MODEL PIN (Q1). Spec §5.4 amendment applied (`claude-sonnet-4-6`→`claude-opus-4-8`; §A1). (The prior §7.2 cite said `claude-opus-4-7`; the conformed pin is `claude-opus-4-8` per Plan-1/Plan-4 reports) |
| G20 | RESOLVED | 1d384b2 | `astral-sh/uv-pre-commit` uv-lock hook present (L807-810) |
| D-5.8 (P1 leg) | RESOLVED | 1d384b2 | Plan 1 now scaffolds `cli/__main__.py` as a Typer app (Typer `app`, not Click) (L227/301-322, DoD L1471) |

**Plan 1A — operational-infrastructure** — 12 R (the orphaned infra is now owned)

| ID | Verdict | Resolver | Evidence at HEAD |
|---|---|---|---|
| P0-5 | RESOLVED | f686f04 | "the one infra plan" — goal L5 + ownership backstop L18 |
| G1 | RESOLVED | f686f04 | Task 17 `Makefile` (L2977/3051) |
| G2 | RESOLVED | f686f04 | Task 14 `image-build.yml` + Trivy matrix (L2497/2570) |
| G3 | RESOLVED | f686f04 | Task 13 discrete `security.yml` (L2223/2315) |
| G4 | RESOLVED | f686f04 | Task 15 `lockfile-refresh.yml` weekly cron (L2699/2758) |
| G5 | RESOLVED | f686f04 | Task 16 `CODEOWNERS` (L2841/2920) |
| G6 | RESOLVED | 3b3a57b | Task 8 Dockerfile + entrypoint (L1240/1381) + Task 9 Dockerfile.model-server (L1523/1588) |
| G7 | RESOLVED | f686f04 | Tasks 4-7 observability; Task 7 "Closes G7 fully" (L1230) |
| G8 | RESOLVED | — | Task 10 `bootstrap_dev.sh` (L1673) + Task 11 `seed_test_corpus.sh` (L1849) |
| G9 | RESOLVED | f686f04 | Task 12 semgrep `codeoracle-custom.yml` 5 rules (L2000/2111) |
| G10 | RESOLVED | — | Task 3 `defaults.env` (L554/662) |
| G12 | RESOLVED | — | Task 1 `utils/async_helpers.py` (L112/117-118/212) |

**Plan 2 — pre-implementation-verification-gates** — 11 R · 1 DECIDED · 0 OPEN

| ID | Verdict | Resolver | Evidence at HEAD / note |
|---|---|---|---|
| D-2.1 (P0-1) | DECIDED → 1536 | spec | text-embedding-3-small 1536; bge superseded (spec changelog L20, §9.3 L1494; Plan 2 L1079/1095) |
| D-2.2 (P0-6) | RESOLVED | bfa7731 | `preflight.sh` + `.cache/preflight/results.md` at spec paths (L1279/1385/1406) |
| D-2.3 | RESOLVED | bfa7731 | gates run real fixture (L1478/1506-08); vLLM /v1/embeddings test (L1520-23) |
| D-2.4 | RESOLVED | bfa7731 | 4 verbatim fallbacks restored (L1463/1475/1502/1516) |
| D-2.6 (P0-9) | RESOLVED | e5cfcfb | `PGVECTOR_INSTALL_METHOD` all 3 branches (L373/380/388); no `PGVECTOR_SOURCE` |
| D-2.7 | RESOLVED | — | Plan 2 header (L74-75/L1832) cites Renovate Task 12 + states `.github/renovate.json5` has no custom/regex manager; verified vs Plan 1 (Task 12, packageRules only). Residual contradiction lived in Plan 1 L34 + Plan 8 L32 — now registered as net-new finding NN-1 + corrected (§7.4) |
| D-2.8 | RESOLVED | — | false "no external-tool dep" gone; Tempo+OTel assigned to Plan 1A (L44) |
| D-2.9 | RESOLVED | — | no "AGE 1.5" spec citation; "1.5" now an adjustable pin (L266/269) |
| D-2.10 | RESOLVED | — | literal `.x` placeholders replaced with concrete §refs |
| D-2.11 | RESOLVED | — | all 4 sites (L35/L65-68/L125/L1782) describe `docker compose --env-file` / root-`.env` as the `${VAR}` driver and state a service-level `env_file:` does NOT substitute `${VAR}`. Stale §7.2 cites L35/65/1766 → current L35/L65/L125/L1782 |
| D-2.12 | RESOLVED (PLAN-WINS) | aea80fd (pointer only) | Plan 2 Task 6 Step 1 + report template + Deviation #7 key on `time[<latest-version>]` (last-publish), explicitly rejecting `time.modified`. Spec §3.6 L360 amendment applied to the same last-publish signal (§A2); grep confirms L343 was the sole live flawed occurrence. Plan-2 ripple: Plan 2 L1776 Deviation #7 prose refreshed to "resolved 2026-06-02 — spec §3.6 L360 amended; spec and plan agree" |
| D-2.13 | RESOLVED | — | Plan 2 (L539/L571/L1494) and Plan 5 (L4421, "byte-for-byte") both use lowercase `--language pythonsrc`; zero `PYTHONSRC` uppercase in either plan. No edit required either side — the §7.2 "mismatch in Plan 5 → OPEN" verdict was itself stale |

**Plan 3 — core-foundation-and-db-bootstrap** — 7 R · 1 DECIDED · 0 OPEN

| ID | Verdict | Resolver | Evidence at HEAD / note |
|---|---|---|---|
| D-3.0 (P0-4) | RESOLVED | d36847e/903052d | Plan 7 = "traces-ingest" (L21); repos attributed to Plans 5-7 (L4221/4241/4343) |
| D-3.1 | DECIDED | 261c6e8 | spec §3.5 AMENDED to `"$user",public,ag_catalog` (spec L18/310); matches code. **Residual #1 RESOLVED (261c6e8):** Plan 3 L2537/L4281 now `"$user",public,ag_catalog` |
| D-3.2 | RESOLVED (PLAN-WINS) | — | Plan KEEPS `ChatMessage.tool_calls` (Q3). Spec §5.2 amendment applied — add `tool_calls: list["ToolCall"] = []` to `ChatMessage` (§A3) |
| D-3.3 | RESOLVED (PLAN-WINS) | aea80fd | `EvidenceHandle` already reconciled (#44); spec §5.2 amendment applied — constrain `TokenUsage.input_tokens`/`output_tokens` with `Field(ge=0)` + add `Field` import + derived `total_tokens` property (§A3), completing the partial reconcile |
| D-3.4 | RESOLVED | d50e32e | `.env.example` `CODEORACLE_LLM_PROVIDER` single-underscore correct (L2184/2207-08). **Residual #2 RESOLVED + re-graded P1:** the `CODEORACLE_LLM__` defect spanned 29 lines/4 files (broke Plan 13 e2e slice) — fixed corpus-wide; see §7.4 #2 |
| D-3.5 | RESOLVED | — | L15 prose conformed to the (correct) `required=()` array — `IMAGE_SOURCEBOT` is recorded/pinned but deliberately NOT presence-gated; first consumer corrected to Plan 8. The array was correct; the prose was the error |
| G11 (P0-2) | RESOLVED | 2703942 | "Plan 1A owns `core/telemetry.py`" (L863/4344); no "Plan 7 adds" remains |
| D-9.1 (P3 model leg) | RESOLVED | — | `excerpt: Annotated[str, StringConstraints(min_length=1)]` on DraftCitation (L1274) + Citation (L1285) |

**Plan 4 — llm-provider-abstraction** — 11 R · 0 OPEN · 1 MOVED

| ID | Verdict | Resolver | Evidence at HEAD / note |
|---|---|---|---|
| D-4.1 | RESOLVED | f1b4ca4 | `request_fingerprint` hashes model/temp/max_tokens/structured_output (L42/698-705) |
| D-4.2 | RESOLVED | aea80fd | `max_output_tokens` config field default 4096 (L1001/L2109) |
| D-4.3 | RESOLVED (by verification) | — | at HEAD the AnthropicProvider's `count_tokens` calls `client.messages.count_tokens(...)` (Anthropic's real token-count API via a sync client), with a labeled/logged `len(text)//4` last-resort fallback used only on API error; the prior `cl100k_base` approximation is removed (live Plan-4 L2010/L2016, method body ~L2010-2048; L580-594 docstring). The §7.2 "L1887 cl100k_base" cite predated Plan-4's +500-line drift and no longer points at the method |
| D-4.4 | RESOLVED | f1b4ca4 | `env_prefix="CODEORACLE_LLM_"` (L1051); no `CODEORACLE_LLM__`. ollama-ban superset is deliberate |
| D-4.5 | RESOLVED (declared-deviation) | — | OpenAI now fully implements `structured_output` (strict `response_format` json_schema, all-properties-required, +2 tests); Anthropic/vLLM/Ollama raise an explicit declared-deferral naming the native mechanism (Anthropic forced tool-use / vLLM `guided_json` / Ollama `format`) + the Plan 13 go-live gate. No provider silently drops the constraint. (Owner of non-OpenAI go-live timing: Plan 13) |
| D-4.6 (P0-10) | RESOLVED | f1b4ca4 | Task 1 Step 4 now VERIFIES Plan 1's block, "must NOT append" (L145-166) |
| D-4.7 (P0-4) | RESOLVED | 903052d | `api/deps.py` → Plan 11 every site (L3823/3871); no "Plan 7" attribution |
| D-4.8 | RESOLVED | 4ef53b6 | `aclose` single owner Plan 10 Task 11 lifespan (L554-555); no Plan 7/8 ambiguity |
| D-4.9 | RESOLVED (declared-deviation) | — | converters drop `tool_calls` by explicit design; deferral now names owner = Plan 10 (multi-turn agent loop, not the dubious "Plan 5/6"); omission pinned by `test_to_{openai,anthropic}_message_omits_tool_calls` |
| D-4.10 | RESOLVED | f1b4ca4 | `continue-on-error: true` on live-llm-smoke + non-required note (L3520-3537) |
| D-4.11 | RESOLVED (PLAN-WINS) | — | spec §5.2 L523 declares `max_context_tokens()` abstractly but §5.4 had no feeding field; plan correctly adds `max_context_tokens` (+`request_timeout_seconds`) to all four configs. Spec §5.4 amendments applied (max_context_tokens + request_timeout_seconds; §A4). Folds NN-2 (the spec-§5.4 missing-fields net-new finding) |
| D-4.12 | **MOVED** | — | heuristic + untracked `/tokenize` deferral persist; cite repointed in §4: L1991/L2008 → **vLLM L2240-2246, Ollama L2448-2452** (verified) |

**Plan 5 — worker-framework-and-joern** — 15 R · 2 DECIDED · 0 OPEN · 0 DANGLING

| ID | Verdict | Resolver | Evidence at HEAD / note |
|---|---|---|---|
| D-5.1 (P0-8/G15) | DECIDED (FQN half) | — | dotted FQN pinned + regression test (L39/4396/4582-96). Chosen scheme is dotted (opposite of the finding's `<file>::<name>` premise) |
| D-5.2 | RESOLVED (declared-deviation) | — | spec §8.5 amendment applied — footnote the cold-reindex budget acknowledging per-file JVM boot + N-worker-replica parallelism as the throughput mechanism, persistent-Joern-server deferred post-slice (§A5). Reconciles the documented Plan 5 JVM-per-file trade-off (L~4339-4370) with the budget on the spec side |
| D-5.3 | RESOLVED | — | unknown labels now counted + logged (DEBUG), not silent (L4319-62) |
| D-5.4 | RESOLVED | aea80fd | spec §3.2 amendment applied — add `db/schemas.py` (+ sibling `db/orm.py`) to the `db/` tree: declarative ORM module created by Plan 3 (Task 7), extended by Plans 5/6/7 (§A6). Closes the last D-5.4 sub-item |
| D-5.6 | DECIDED | — | commit-as-you-go contract documented (L3257-67); §8.4 atomicity met via implicit txn |
| D-5.7 | RESOLVED | — | worker routes through Plan 3 `configure_logging` w/ §7.9 redaction + guard (L2211-18) |
| D-5.8 (P5 leg) | RESOLVED | — | the user's platform-wide Typer decision selects Option B: de-hedge Plan 5 Task 14 to Typer (typed `@app.command`/`CliRunner` from `typer.testing`), coherent with the Plan-1 Typer scaffold migrating this session. Evidence: user Typer decision + Typer migration this session + `cli-entrypoint-inventory.md` (entry #1/#2, mechanical Click→Typer, Typer wraps Click). The Click-vs-argparse hedge is removed. (NB: overrides the Plan-5 resolver, which had recommended Option A / KEEP CLICK) |
| **D-5.9 (P0-11)** | **RESOLVED (canary)** | — | all sites `make_async_engine()`/`(engine)` (L2194-95/6627-28/6487-88); no Settings; L1776/L5180-81 gone |
| D-5.10 (P0-8/G15) | **RESOLVED** | this pass (Option B, task #71) | Joern conformed to amended spec §3.7/§8.4. `GraphRepository.link_cross_file_symbols(snapshot_sha)` added (the cross-file FQN-MERGE pass) + wired into `_reindex_locked` after the per-file barrier drains, before APPLIED, inside the advisory-lock single-flight. `find_callers` + `DeltaComputer` rewritten to §8.4's valid topology (Symbol→Symbol `:CALLS`, File→Symbol `:DEFINES`); the impossible `File-[:CALLS]→Symbol` and dead `:USES`/`:IMPORTS`-to-Symbol tokens removed corpus-wide — both Plan 5's parser emit-map and Plan 10's `build_callers_cypher` GRAPH_QUERY citation locator (the agent-layer consumer of the same contract; §7.4 #13). Task 15's cross-file CALLS edge now materializes via the linking pass. **Label stays `:Symbol`** — the spec's 2026-05-22 `:Definition` adoption was the actual defect (collided with Plan 6 Invariant 4's extractor-scoped namespaces); reverted in spec changelog 2026-06-01. See §7.4 #11. |
| D-5.11 | RESOLVED | NFR #44 | `snapshot_sha` now `String(80)` (L2404-06/2484); L45 canonicalizes |
| D-5.12 | RESOLVED | c8e27c7 | extractor key `"joern"` uniform (Plan 5 side; `KNOWN_EXTRACTORS` construct is Plan 3) |
| D-5.13 | RESOLVED | — | `import asyncio` at top, no E402 noqa (L4906) |
| D-5.14 | RESOLVED | — | docstring correct "no psycopg agtype codec" (L3231-34); cite drift L2654→L3231 (historical) |
| G15 (P0-8) | **RESOLVED** | this pass (Option B, task #71) | Same root as D-5.10 — now reconciled. Spec §3.7/§8.4 reverted to `:Symbol`; Plan 5 adopted the linking-pass contract; FQN scheme + graph shape coherent across Plans 5 (`:Symbol`) / 6 (SCIP `:Definition`+`:Reference`) / 9 (cross-label FQN correlation). |
| G16 | **RESOLVED** | 0184fc8 (DECIDE-D) | **prior OPEN was a reconciliation miss — verdict corrected this pass (task #72); see §7.4 #15.** The stale-`APPLYING` **snapshot** reaper IS built + owned by **Plan 5**: `SnapshotRepository.mark_stale_applying_failed()` (L2779-2845) — one atomic `UPDATE snapshots SET status='failed' … WHERE status='applying'` — swept **unconditionally check-on-use** at the top of `_reindex_locked` once the advisory lock is held (L5694-97, own committed session), clearing any SIGKILL/OOM-orphaned `APPLYING` row from a dead prior process before new work begins. Owned via **DECIDE-D** (L29/L31, *"reversed the Plan 11 deferral"*); unit-tested L3035/L3051. The earlier *"unowned across Plans 11/12/13"* verdict followed the **superseded** "route to Plan 11+" trail (frozen §6 L545) and checked only Plan 5's *cited* L5558-70 — the **advisory-lock** janitor, a different object — never grepping Plan 5 for the reaper, which had been in the plan since 2026-05-26 (0184fc8), six days before the 2026-06-01 reconciliation that called it unowned. |
| G19 | RESOLVED | — | `core/streams.py` + `orchestrator.py` carry no commented-out blocks at HEAD |
| D-4.8 (P5 leg) | RESOLVED | d355f11 | the false "Plan 8 aclose" deferral line was removed from Plan 5 (resolver d355f11); the real owner is Plan 10 (Task 11 lifespan, resolver 4ef53b6), already RESOLVED on the Plan-4 leg. The described defect (incoherent two-owner deferral) no longer exists and the owner is now coherent corpus-wide — DANGLING promoted to RESOLVED |

**Plan 6 — snapshot-extractors-and-embeddings** — 12 R · 2 DECIDED · 0 OPEN

| ID | Verdict | Resolver | Evidence at HEAD / note |
|---|---|---|---|
| D-6.1 | RESOLVED | 9dc72b4 | sourcebot_indexer removal cites spec amendment (§3.2/§4.1/§8.2); query-side handed to Plan 8 |
| D-6.2 (G14) | RESOLVED | — | `ParagraphChunker` doc producer wired (L158/539/782-86); `is_doc_path` exported |
| D-6.3 | DECIDED | — | 3-char filter now an accepted-deviation in the numbered ledger (L8042) |
| D-6.5 | DECIDED | — | 3-not-4 extractors; 4-kind E2E → Plan 13 by ID (L7507/119) |
| D-6.6 (P0-12) | RESOLVED | — | imports `workers/_base/message` (L4855); RetryPolicy in `_base/message.py`; no `workers/base`, no `retry.py` |
| D-6.7 | RESOLVED | — | terminal state `APPLIED`; all `READY` sites disavow the name (L5/29) |
| D-6.8 (P0-13) | RESOLVED | — | composes `db/repositories/graph` (L4605/4652); no `age_driver` import |
| D-6.9 (P0-14) | RESOLVED | — | graph `cypher('codeoracle', …)` (L4565); no `codeoracle_code_graph` |
| D-6.10 | RESOLVED | — | factory relocated to `adapters/llm/embeddings.py` (L42); core→adapters edge gone |
| D-6.11 | RESOLVED | — | no blocking gate was downgraded. Spec §3.6 already specifies the vLLM `/v1/embeddings` check as a one-shot scaffolding-day preflight (`preflight.sh`), distinct from CI-recurring checks (CI cannot run GPU vLLM at scaffold time). Plan 6 reframes §3.6 as the owned blocking preflight and Task 8's live smoke as a complementary CI-off regression check. Optional spec §3.6 add-on (§A7) available but NOT required for the flip |
| D-6.12 | RESOLVED | git -S | fields renamed snake_case `embed_chunk_lines`/`overlap` (L1408-09); ledger-era c4b6a3e had CONSTANT_CASE |
| D-6.13 | RESOLVED | — | `_validate_embedding_config` now rejects degenerate strides via `embed_chunk_lines - embed_chunk_overlap >= _MIN_CHUNK_STRIDE` (10) in addition to the `overlap >= chunk_lines` guard; `test_degenerate_stride_rejected` pins it (one boundary enforcement at `chunker_for`). Orphan #6 retired (the self-review §6 entry now describes the stride floor) |
| D-6.14 | RESOLVED | — | 3 Plan-4 symbol paths corrected w/ "(NOT …)" notes (L40-42) |
| D-6.15 | RESOLVED | — | dead `litellm.py` ignore gone; glob + "do NOT re-add" (L2935/2948) |

**Plan 7 — continuous-consumer-and-traces-ingest** — 9 R · 1 DECIDED · 0 OPEN

| ID | Verdict | Resolver | Evidence at HEAD / note |
|---|---|---|---|
| D-7.1 | RESOLVED | d5fb4f1 + 261c6e8 | repathed `db/repositories/traces.py` everywhere incl. the line-80 ASCII tree (261c6e8 inserted the missing `repositories/` node) |
| D-7.2 | RESOLVED | — | the poll-vs-stream choice was already declared; the undeclared piece (why no `tasks.py`) is now explicit in Task 11. Spec §3.2 L234 amendment applied — `traces_ingest` annotated as ContinuousConsumer (HTTP poll of Tempo, no stream/tasks.py); the stale self-contradictory "consumes traces.ingest" line was the remaining open surface (§A8). (`wiring.py` was already declared — not part of the defect) |
| D-7.3 | RESOLVED | — | Plan 7 collapsed to the single canonical compose file (no `.dev.yml`); three independent sources agree. Cross-plan dependency SATISFIED: Plan 1A exposes OTLP-HTTP `:4318` to host (otel-collector Task 4, `4318:4318`) so the gated `--run-live-tempo` test can land; Tempo image-pin delta documented as non-breaking (Plan 7 adapter dual-handles `resourceSpans`/`batches`). See §B2 |
| D-7.4 | DECIDED | — | PGCursorStore stays in `workers/_base/` with explicit new justification (L1827) |
| D-7.5 (P0-15) | RESOLVED | c4b6a3e/d5fb4f1 | bare-numeric chain 0006→0005→0004 unified (L3528-29/387-88) |
| D-7.6 (P0-16) | RESOLVED | d5fb4f1 + NFR #66 | all stream refs → `core/streams.py` (post-relocation); no `workers/_base/streams` |
| D-7.7 | RESOLVED | 8f0d7f5 | `utc_now` removed (git-pickaxe); local `_now_unix_nano` (L1043/140) |
| D-7.8 | RESOLVED | 8f0d7f5 | "enumerate observed services" claim removed (git-pickaxe) |
| D-7.9 | RESOLVED | — | all 4 exceptions relocated to `core/exceptions.py`, reparented to `CodeOracleError` (Plan 3); 3 mixed imports split; F401-clean; corpus grep confirms zero external importers — fully self-contained, no spec or cross-plan dependency |
| G18 | RESOLVED | d5fb4f1 | test `..._rejects_regressing_cursor` now exists in Task 2 (L729); attribution corrected |

**Plans 8 + 9 — handle-resolution / verifier** — 9 R · 1 DECIDED · 0 OPEN

| ID | Verdict | Resolver | Evidence at HEAD / note |
|---|---|---|---|
| D-8.1 | RESOLVED | — | resolved jointly with the §6 amendment: the worked `graph_query` example is now snapshot-anchored (`(c:Symbol {snapshot_sha:$snapshot_sha})`) — structural pin coherence. Spec §6 amendment applied (§A9). New post-edit anchors: `_assert_snapshot_pinned` prose L2732, def L3127 |
| D-8.2 | RESOLVED (PLAN-WINS) | — | spec §6 amendment applied — worked `graph_query` example becomes scalar-returning + snapshot-anchored (`RETURN c` → `RETURN c.fqn AS result`; `c` → `c:Symbol {snapshot_sha:$snapshot_sha}`), conforming spec to Plan 8's scalar-only + structural-pin constraints (§A9). (`:Symbol` per D-5.10/G15) |
| D-8.3 | DECIDED | — | one-shot no-retry intentional, documented scope boundary (L338-40) |
| D-8.4 | RESOLVED | 4a083ef/ffb173b | `SourcebotSourceResponse` contract model `extra=forbid`; Invariant 9 "closes D-8.4" (L88-89) |
| D-8.5 | RESOLVED | 86f13da | `respx` now test-only in dep table (L28) |
| D-8.6 | RESOLVED | — | `GraphQueryError` (worker-family) is caught at the `GraphQueryResolver` service boundary and re-raised as `ResolverError` (`CodeOracleError`-derived); both the wrap (`__cause__` preserved) and leak-prevention are pinned by `test_resolve_wraps_graph_query_error_as_resolver_error` + `test_resolve_does_not_leak_worker_family_exception`. New anchors: prose L2749, catch L3239 |
| G21 | RESOLVED | 4a083ef | `head_sha_only()` strips `+wt` at resolver layer; Invariant 10 (L90/2188-2202/2422-34) |
| D-9.1 | RESOLVED | — | model-fix landed in Plan 3 (`StringConstraints min_length=1`); Plan 9 *additionally* retained its Verifier-layer guard by reasoned decision (Invariant 12, L66/88) — both legs satisfied |
| D-9.2 | RESOLVED | 2a7a21e | test imports the real `stringify_cypher_rows` both sides (L830-32) |
| D-9.3 | RESOLVED | — | citation-resolution outcomes now emit on a dedicated `get_audit_logger` channel (fixed-INFO floor, immune to `settings.log_level`) so crit 11 holds at every prod verbosity (`_AUDIT` L1380/1495/1512/1518); fixture pins prod-representative `make_filtering_bound_logger(WARNING)` (L840-872) + anti-mask control `test_plain_logger_info_is_filtered_at_prod_level` (L1169). Spec §8.5 crit 11 amendment applied (audit channel; §A10). Cross-plan edit applied: Plan 3 `core/logging.py` adds the `get_audit_logger` accessor (Plan 9 hard-imports it) — gates this flip (§B3) |

**Plan 10 — agent-orchestration** — 12 R · 0 OPEN

| ID | Verdict | Resolver | Evidence at HEAD / note |
|---|---|---|---|
| D-10.1 (P0-7) | RESOLVED | 493730e | Deviation 1 + blocking Plan-13 gate-9 demand intact (L120/3057) |
| D-10.3 | RESOLVED (spec half) | — | spec §3.2 `query_planner` dropped (changelog L17). Plan-5 "still defers as live" half is out of scope (Plan-5 check needed) |
| D-10.U1 | RESOLVED | 493730e | `agent/__main__.py` built Task 11 (L3667); old Deviation 9 replaced (L3805) |
| D-10.U2 | **RESOLVED (convention)** | task #74 | **Written justification added** — the 6 nodes + `resolve_to_citation` stay decorated free functions (the `@agent_node` framework idiom), now with the CLAUDE.md-required reasoned exception in Plan 10 Deviations entry 11: `@agent_node` is the LangGraph idiom; ABCs are reserved for swappable layers and a graph node is not a test-swap point; `resolve_to_citation` is a stateless single-method bridge whose wrapping adds ceremony for zero encapsulation gain; and it belongs at the agent-runtime layer (not on `HandleResolverService`) on concern-coupling grounds — `DraftCitation` ships from `codeoracle/models/`, so the bar is concern-separation, not layering. "Silence is not an option" (§6 DECIDE L541) satisfied; no `NodeBase` ABC needed |
| D-10.U3 | RESOLVED (declared-deviation) | — | Plan 10 Task 3 documents the unit-vs-integration LLM-testing split; the real spec-§5.7 `MockLLMProvider` + `llm_scripts/` are exercised at the Plan 13 integration layer (Inv 2 / Task 9), confirmed cross-plan. `_FakeLLM` at the unit layer is a declared, defensible choice — not a coverage gap. No spec edit (spec does not contradict the split) |
| D-10.U4 | RESOLVED | 2703942 | summary L7 now `async def _with_deps … not functools.partial` |
| D-10.U5 | RESOLVED | 8f0d7f5 | `build_callers_cypher` now `AS result` (L1623), matches Plan 8 constraint |
| D-10.U6 (P0-17) | RESOLVED | 2703942 | keyword call (L1917-21) + `*` in conftest fake (L1006-12) |
| D-10.U7 | RESOLVED | 8f0d7f5 | pinned path now gates `status is not APPLIED` (L1422-29) |
| D-10.U8 | RESOLVED | — | `test_graph::test_low_confidence_surfaces_unknown_through_the_real_floor` pins the floor end-to-end against the real `VerifierService`, wired to the imported `PROVEN_CONFIDENCE_FLOOR` (not a literal); asserts claim-present + verdict-UNKNOWN (abstain-after-score path) |
| D-10.U9 | RESOLVED | — | `test_embed_query_node::test_embed_query_locator_round_trips_through_plan8_parser` pins the `file://` locator contract between Plan 6 (`source_path` writer) and Plan 8 (`parse_file_locator` reader) via a true round-trip; format drift now fails a Plan-10 unit |
| D-10.U10 | RESOLVED | aea80fd | spec §4.2 k=4 (L469) + Plan `KNN_K=4` (L1892) agree; no stale k=5 |

**Plans 11–13 — api-service / mcp-server / e2e-slice** (the 7 ❓ §3-matrix rows all flip ❓→✅; cross-plan findings)

| Concern / ID | Verdict | Evidence at HEAD |
|---|---|---|
| §3 row 47 — `api/` FastAPI process | ✅ (Plan 11) | Task 4 `api/deps.py` (L112/1113); Task 6/7 routes; lifespan Task 9 |
| §3 row 49 — `mcp/` process (vs D-1.1 rename) | ✅ (Plan 12) | builds `src/codeoracle/mcp/` (L33/99/257/782); zero `mcp_gateway` |
| §3 row 58 — `POST /v1/reindex` | ✅ (Plan 11) | Task 7 async-job reindex + GET status (L115; amended fc0894a) |
| §3 row 64 — 5 functional integration tests | ✅ (Plan 13) | Tasks 3-7 = functional gates 1-5 (L102-105) |
| §3 row 65 — 3 incremental tests | ✅ (Plan 13) | Task 8 incremental reindex, 4-extractor SUCCESS (L131-132) |
| §3 row 66 — intent-classifier closure gate | ✅ (Plan 13) | Task 9 integration analogue (L108) |
| §3 row 69 — gate-10 self-traces | ✅ (Plan 13) | Task 12 `assert_self_traces_ingested.py` (L2895-97); ingestion is Plan 7 |
| G17 (`POST /v1/reindex` deferral) | RESOLVED | Plan 11 Task 7 realizes the deferral target |
| D-6.5 (4-extractor live E2E) | RESOLVED | Plan 13 Task 7 (L1753) + L132 assert all 4 extractor keys SUCCESS; corroborates the Plan 6 DECIDED row (3-not-4 extractors decided there; the 4-kind E2E leg lands here) |
| D-10.U3 (real MockLLMProvider) | RESOLVED (e2e leg) | Plan 13 L7/19/130/136 real `MockLLMProvider`; see Plan 10 row for the unit-leg caveat |
| **G16 (stale-APPLYING snapshot janitor)** | **RESOLVED (owner Plan 5)** | correctly *absent* from Plans 11/12/13 — **DECIDE-D re-homed it to Plan 5** (`mark_stale_applying_failed`, check-on-use in `_reindex_locked`; L2779/L5694-97). The "Plan 11+" routing (frozen §6 L545) is a stale pointer superseded 2026-05-26; counted once in the Plan 5 row (§7.2), audit-quality note §7.4 #15 |
| D-4.8 (`LLMProvider.aclose` owner) | RESOLVED (owner Plan 10) | Plan 11 L137 disclaims any LLM path; the ledger's aclose→Plan-11 routing is a stale pointer (residual #10) |

**Count basis.** §7.2 enumerates **123 finding-legs** — every D-x.y and G-n row plus the P0-5 infra umbrella. It
is larger than the audit's headline "~95 distinct findings" because the 17 P0s are severity-tags counted in §7.1
under their canonical D/G ID (not re-listed here), and a few findings carry two legs (e.g. D-5.8 = cli-scaffold
leg [Plan 1] + CLI-framework-hedge leg [Plan 5]; D-4.8 = Plan-4 resolution + Plan-5 dangling line). §7.2 is the per-leg
authority; §7.1 is the P0 authority; neither double-counts within itself. The **Plans 11–13**
table above is **corroborative, not additive** — its rows are the seven §3-matrix flips, G17
(RESOLVED), and re-listings of findings counted in their home plans (D-6.5 → Plan 6, D-10.U3 →
Plan 10, D-4.8 → Plans 4/5, G16 → Plan 5); none of them add to the 123.
**Totals: 112 RESOLVED · 10 DECIDED · 0 OPEN · 1 MOVED · 0 DANGLING.**

### 7.3 Still-open after reconciliation (**0 OPEN legs — fully closed this pass**)

- **P0-severity (0 remaining):** the lone P0-tier root (D-5.10 ≡ G15) was **RESOLVED this pass** (Option B,
  task #71): Plan 5 adopted spec §3.7's cross-file symbol-linking pass (`GraphRepository.link_cross_file_symbols`,
  wired into `_reindex_locked`) + conformed `find_callers`/`DeltaComputer` to §8.4, and the spec's mislabel was
  reverted (`:Definition`→`:Symbol`, keeping Plan 6 Invariant 4 intact). **No P0-severity items remain open.**
  Audit-quality note on the original (inverted) framing: §7.4 #11.
- **Availability gap (0 remaining):** the stale-`APPLYING` **snapshot** janitor (crash-recovery reaper) is built
  and owned by Plan 5 — `SnapshotRepository.mark_stale_applying_failed()` sweeps SIGKILL/OOM-orphaned `APPLYING`
  rows check-on-use at the top of `_reindex_locked` under the advisory lock (L2779-2845 / L5694-97; **DECIDE-D**,
  L29/L31). The "unowned → Plan 11+" framing (§6 EDIT L545) was a reconciliation miss superseded 2026-05-26;
  audit-quality note §7.4 #15. *(Distinct from the Joern advisory-lock janitor Plan 5 also eliminated via
  check-on-use, L5558-70 — the two were conflated, which is exactly what the original verdict tripped on.)* Banked
  opt-in liveness items #67/#68 (extractor-subprocess timeout / all-failed last-good fallback) were separate from this gap and are now **actioned this pass** (§7.4 #17).
- **OOP convention (0 remaining — D-10.U2 RESOLVED this pass, task #74):** Plan 10's 6 graph nodes +
  `resolve_to_citation` remain decorated free functions, but the missing piece was never the refactor — it was the
  *written justification* CLAUDE.md requires for the deviation. That justification now exists in Plan 10's
  Deviations entry 11 (framework-idiom + ABCs-reserved-for-swappable-layers + stateless-bridge + concern-coupling-
  not-layering, since `DraftCitation` ships from `codeoracle/models/`). No CLAUDE.md-convention finding remains open.
- **P1 — declared-deviation / spec-drift (0 remaining):** all 34 survivable-P1 legs were closed this pass —
  plan-side conformances, spec amendments (§A1-A10), cross-plan edits (§B1-B3), and the Typer/D-5.8 decision.
  Per-leg evidence in §7.2.
- **MOVED cite (fixed in place this pass):** D-4.12 declaration cite `L1991 / L2008` → `L2240-2246 / L2448-2452`
  (vLLM / Ollama `count_tokens`; both ranges verified at HEAD). D-5.14's cite also drifted (L2654-57 → L3231-34)
  but that finding is RESOLVED, so its cite is historical and left unchanged.
- **DANGLING (0 remaining):** the D-4.8 Plan-5 dangling line was promoted to RESOLVED — defect removed *and*
  owner (Plan 10) coherent corpus-wide; see §7.2 Plan-5 row.

### 7.4 New residuals surfaced during reconciliation (not in the original ledger)

**Ledger-ID-attached (all three RESOLVED this session — D-3.1 + D-7.1 in 261c6e8; D-3.4 in d50e32e — the CODEORACLE_LLM__ pass, task #70):**

1. **D-3.1 — RESOLVED (261c6e8).** Plan 3 L2521 / L4265 had stated the OLD `ag_catalog`-first `search_path`
   order, contradicting Plan 3's own code (L2234/2616), test (L2862-63), and the amended spec §3.5; both lines
   now read `"$user", public, ag_catalog`.
2. **D-3.4 — RESOLVED, re-graded P2 → P1 (CODEORACLE_LLM__ corpus pass — d50e32e; task #70).** What the ledger filed as a
   2-line cross-plan-deps cite nit was a **29-line / 4-file** defect — `CODEORACLE_LLM__` (double-underscore) in Plan 13
   e2e compose, Plan 5 worker, agent-orchestration, and cross-plan-deps. `LLMConfig` is a **standalone**
   `BaseSettings(env_prefix="CODEORACLE_LLM_")` (spec L635; the parent `Settings` has no `llm` field), so
   `CODEORACLE_LLM__PROVIDER=mock` bound to nothing → the Plan 13 e2e slice silently ran against the default
   `provider="openai"` (broken acceptance gate). Fixed corpus-wide to canonical `CODEORACLE_LLM_*` (single — LLM
   standalone), leaving `CODEORACLE_EMBEDDING__*` / `CODEORACLE_DB__*` correctly **double** (nested under `Settings`).
   Verified `grep CODEORACLE_LLM__` → 0; 29 insertions / 29 deletions, all LLM renames.
3. **D-7.1 — RESOLVED (261c6e8).** Plan 7 line 80 File-Structure ASCII tree had shown `db/traces.py`; the
   missing `repositories/` node was inserted → `db/repositories/traces.py`, matching the rest of the plan.

**Net-new orphans (no ledger ID):**

4. **Plan 1A** Provides table L44 names "Loki" but no task authors a Loki config. **RESOLVED** — L44 no longer
   claims Loki; the stack string now matches spec §3.4 + Tasks 4-7 (OTel Collector + Tempo + Prometheus +
   Grafana), with an inline note recording Loki deferred to a future `logs_ingest` plan. Cross-plan ripple
   applied: Plan 3 drops "Loki" from 4 prose sites (L863/L2137/L2343/L4327); Plan 3 L2349 presence-check
   alternation left as-is (defensive guard, future-proof).
5. **Spec changelog gap** — commit 9dc72b4 amended spec §3.2/§4.1/§8.2 for the SourceBot drop but added no
   changelog-table row. **REGISTERED + RESOLVED** — the missing spec changelog-table row for the 9dc72b4
   SourceBot-drop amendment (§3.2/§4.1/§8.2) is added; #5 marked RESOLVED.
6. **D-6.13 self-review trap** — Plan 6 self-review restates D-6.13 in a weaker form the current text satisfies,
   masking the still-open ledger finding. **RESOLVED — retired into D-6.13.** Plan 6 self-review §6 now describes
   the stride floor (matches the hardened validator); folded into the D-6.13 RESOLVED row.
7. **D-6.12 self-review mis-numbering** — Plan 6 self-review's "D-6.12" describes a `_GRAPH` constant rename,
   unrelated to the ledger's field-casing D-6.12. **RESOLVED — retired into D-6.12.** Self-review mis-numbering
   corrected; the two items disambiguated; folded into the D-6.12 RESOLVED row.
8. **D-6.4** — a Plan-6 per-report item (Cypher built by f-string, L4554-64), **not a numbered §4 ledger
   finding** and carrying no security-reviewer flag — rationale lives only in a docstring. Recorded for
   completeness; closed-by-rationale but un-tracked. **RESOLVED — retired into D-6.4 (declared-deviation).** The
   ScipGraphRepository regex-allowlist + f-string AGE interpolation is now a TRACKED "Declared deviation,
   security-review-required" note in Plan 6 self-review §6 (rationale, in-CODEORACLE-only provenance, owned
   security-reviewer hand-off + future-untrusted-input mandate). No longer docstring-only.
9. **gate-4 parity-test name collision** — `test_mcp_explain_symbol_matches_api_response` is defined in BOTH
   Plan 12 Task 8 (in-process, ASGITransport) and Plan 13 Task 6 (full-stack, streamablehttp). Legitimate
   two-layer coverage, but neither plan cross-references the other. **REGISTERED + RESOLVED (low-priority)** — a
   reciprocal one-line cross-reference note added in each plan; the two-layer coverage is intentional.
10. **D-4.8 ledger-routing self-contradiction** — the ledger routes the `LLMProvider.aclose` owner to Plan 11,
    but Plan 11 L137 disclaims any LLM-call path; the real owner is Plan 10 (resolver 4ef53b6). Stale pointer.
    **RESOLVED — retired into D-4.8.** The D-4.8 P4/P5 legs + the §7.2 Plans-11-13 D-4.8 row already name Plan 10
    as owner; folded into the D-4.8 RESOLVED close-out (consistent with the DANGLING→R promotion).

> Residuals 1-3 were in-place cite/prose fixes in the **plan files** (not this ledger). **All applied** —
> D-3.1 + D-7.1 in 261c6e8; D-3.4, on inspection, was not a cite nit but the full `CODEORACLE_LLM__` corpus defect
> (29 lines / 4 files, re-graded P1, broke the Plan 13 e2e slice) and was remediated in its own pass (task #70).

**This pass (D-5.10 / G15 — Option B, task #71):**

11. **Audit-quality correction — the ledger inherited the spec's mislabel.** The original D-5.10/G15 framing
    (frozen §1/§5/§6 + the §7.2 OPEN row) characterized the gap as "Plan 5 never adopted spec §3.7's `:Definition`
    label — still `:Symbol`," i.e. it treated `:Symbol` as the defect and implicitly prescribed migrating Joern to
    `:Definition`. That prescription was **inverted**: `:Symbol` is correct (Plan 6 Architectural Invariant 4
    reserves `:Definition`+`:Reference` for SCIP and keeps AGE label namespaces extractor-scoped; a Joern→`:Definition`
    rename would have collided §3.7's linking-pass MERGE — which carries no extractor discriminator — onto SCIP's
    nodes, silently fusing two independent evidence sources). The real gap was the **missing linking pass + the
    impossible-edge queries**, not the label. Root cause: spec §3.7's 2026-05-22 `:Definition` adoption was itself
    an unreconciled oversight (negative ADR grep — no decision ever unified symbol nodes under `:Definition`).
    **Lesson: a one-sided "Plan X didn't adopt spec Y" finding must verify spec Y is itself correct before
    prescribing conformance.** Resolved via Option B (revert spec to `:Symbol`; Plan 5 adopts the linking pass).
12. **DeltaComputer integration test — RESOLVED this pass (task #73), and it earned its keep immediately.**
    `tests/integration/services/indexing/test_delta_integration.py` now has a full body: a real git repo + a
    real AGE-seeded subgraph driving `DeltaComputer.compute()` end-to-end across spec §8.4's two-pattern
    dependent-discovery (`:CALLS`-via-`:Symbol` UNION `:IMPORTS` File→File), asserting the `NOT … IN
    $changed_files` exclusion (alsochanged.py suppressed) and UNION DISTINCT dedup (both.py returned once),
    including the list-valued `$changed_files` bind the mocked unit suite never reaches. Writing it **surfaced #16**
    — the File-node `.path`/`.file_path` property defect the mocked unit tests and the topology-only string
    assertions had both masked — and the test now drives the *fixed* query. (The linking pass had already gotten
    its own real-AGE integration test in the D-5.10 pass.)
13. **agent-orch `build_callers_cypher` — RESOLVED this pass (re-graded from banked).** Plan 10 L1622 built the
    GRAPH_QUERY citation locator as `(f:File)-[:CALLS|USES|IMPORTS]->(s:Symbol {fqn})` — a File→Symbol topology
    **no extractor emits** (the impossible `File-[:CALLS]→Symbol` shape; `:USES` out of scope; `:IMPORTS` is
    File→File per §8.4), so the query matched nothing and every GRAPH_QUERY citation resolved to an empty excerpt
    — a silent-correctness bug, not the cosmetic "dead token" the first pass graded it. It is the **same Joern
    `:Symbol`+`:CALLS` contract D-5.10/G15 governs**, not unrelated debt: the B amendment is exactly what makes
    the corpus-wide `:CALLS`-only Symbol→Symbol contract authoritative, so L1622's deviation is a live cross-plan
    contradiction the amendment drives. Conformed to the canonical `:CALLS` Symbol→Symbol **edge** (spec L409
    linking pass + L1253 citation example + L1333 dependent-discovery prose all traverse Symbol→Symbol `:CALLS`,
    never File→Symbol): `(caller:Symbol)-[:CALLS]->(s:Symbol {fqn, snapshot_sha})` joined to the caller's File via
    `(f:File)-[:DEFINES]->(caller)`. The `DISTINCT f.file_path` **return** mirrors Plan 5's `find_callers` (L1253's
    example returns the caller node `c` instead — an excerpt-shape divergence out of D-5.10/G15 scope; see #14);
    the L1543
    test assertion updated to pin `:CALLS]->(s:Symbol` + `:DEFINES]->(caller)` and assert `:USES`/`:IMPORTS`
    absent. Literal interpolation + `AS result` (D-10.U5) + `ORDER BY result` + `LIMIT` (NFR D3-3) all preserved.
    Distinct from D-10.U5 (the already-done `AS result` RETURN-alias fix).
14. **spec↔Plan-10 `graph_query` node divergence (method shape + excerpt return) — banked, out of D-5.10/G15
    scope.** Two spec signals describe the `graph_query` node differently than Plan 10 (Task 5) implements it,
    both in the spec's *informal* regions, not the §3.7/§8.4 hard contract: **(a)** L451's agent-dataflow sketch
    maps `graph_query` to `GraphRepository.find_symbol_and_callers(symbol, snapshot_sha)` — a repo method that
    exists nowhere in the corpus (grep: L451 only). Plan 10 instead inlines a parameterised existence check
    (`_EXISTS_CYPHER`) + the literal-string citation builder (`build_callers_cypher`) — the *correct* split,
    because a GRAPH_QUERY locator MUST be a verbatim re-runnable string and a row-returning repo method cannot be
    one. **(b)** L1253's worked Claim example returns the caller node `c` (its excerpt is a caller FQN), whereas
    `build_callers_cypher` + `find_callers` both return `DISTINCT f.file_path` (caller file paths). Both are valid
    "who calls this symbol" answers; the excerpt-shape choice (caller-FQN vs caller-file) is orthogonal to the
    node-label / edge-vocabulary contract D-5.10/G15 governs. **Disposition: banked, not fixed** — informal-region
    divergences, not contradictions in the resolved contract; folding a spec-overview reconciliation into the B
    amendment would over-reach. Surfaced by the D-5.10/G15 grep sweep so it is recorded, not silently dropped
    (the same discipline that re-graded #13).

**This pass (G16 verdict correction — task #72):**

15. **Audit-quality correction — the G16 OPEN verdict was a reconciliation miss, not a real gap.** The prior §7
    overlay (e7a4e52, 2026-06-01) graded G16 **OPEN** — "stale-`APPLYING` snapshot reaper unowned across Plans
    11/12/13." The reaper was in fact already built and owned by **Plan 5**: `SnapshotRepository.mark_stale_applying_failed()`
    (L2779-2845), an atomic `UPDATE snapshots SET status='failed' … WHERE status='applying'` swept unconditionally
    check-on-use at the top of `_reindex_locked` once the advisory lock is held (L5694-97), owned via **DECIDE-D**
    (L29/L31) and unit-tested (L3035/L3051). The OPEN verdict followed the **superseded** "route to Plan 11+"
    pointer (frozen §6 EDIT L545) and verified only the *cited* Plan 5 L5558-70 — the **advisory-lock** janitor, a
    different object — without grepping Plan 5 for the actual snapshot reaper. **Corpus corroboration the
    reconciliation also missed:** the resolution is independently documented in two *sibling plans* — Plan 6
    (`snapshot-extractors-and-embeddings` L83/L152: "eliminated by amendment #46/G16 … no Plan 10–13 deferral") and
    Plan 7 (`continuous-consumer` L43: "amendment #46 moved [it] to Plan 5 `IndexingService` check-on-use … no
    standing stale-`APPLYING` janitor exists anymore"); the ledger was the lone doc in the corpus still asserting
    the gap. **Cause confirmed by git archaeology, not inferred:** `git log -S` dates the reaper's entry to Plan 5 at 2026-05-26 (0184fc8, the DECIDE-D amendment),
    **six days before** the 2026-06-01 reconciliation that called it unowned (e7a4e52), and the ledger has never
    once named `mark_stale_applying_failed` — so the reaper was *present-and-missed* (a reconciliation error), not
    *added-after* (overlay drift); had it post-dated the verdict the correct lesson would instead have been "keep
    the overlay synced with plan amendments." **Lesson — §7.4 #11's discipline turned on the reconciliation itself:
    a "Plan X never built Y" verdict must grep Plan X for Y's *implementation*, not only re-read the one line the
    prior verdict cited.** Same finding-class as the P0-11 reconciliation task #69 corrected (resolved-but-marked-open).
    After this flip **no P0-severity item and no availability-gap item remains OPEN** (the §7.3 availability gap
    closes to 0): the 35 residue legs at the G16 flip were 34 survivable-P1 declared-deviations + the lone
    OOP-convention finding (D-10.U2 — itself since RESOLVED via written justification, task #74 / §7.3, leaving 34
    survivable-P1 legs). The surviving P1 legs still carry correctness/perf *flavor* (e.g. D-5.2's `<3 min` budget, D-8.6's
    swallowed `GraphQueryError`) — they are declared, severity-P1, and not release-blocking, which is the precise
    claim, not "zero correctness concerns." *(Count note: this pass also corrects the §7
    preamble figure (L595) 83 → 86 — it had been left at the e7a4e52 value, missing task #71's D-5.10 + G15 R-flips
    that b0f945b propagated to the §7.2 subtotals and grand total but not to that one prose line (git log -S
    confirms the split: "83 of 123" entered at e7a4e52, the "75 RESOLVED · 10 DECIDED" totals at b0f945b, whose
    diff touched neither prose line — so the drift is verified, not inferred); the +1 here is G16.)*

16. **File-node path accessor read `.path` while the parser writes `.file_path` — silent-correctness query
    defect, RESOLVED this pass (new finding; task #75; surfaced by #12's integration test).** Three shipped graph
    readers returned a File node's path via `f.path` / `changed.path` / `dep.path`, but the Joern parser emits File
    nodes keyed `file_path` (Plan 5 `_node_statements` L4603, pinned by `test_parser_emits_file_node` L4774) — the
    per-file partition key `replace_file_subgraph` DELETEs by (L3228), shared by every node in a file's subgraph.
    `File` carries no bare `path` property, so in production each reader bound NULL and **silently returned zero
    rows**: `find_callers` (Plan 5 L3254) → no callers; `DeltaComputer` §8.4 (Plan 5 L3841-48) → **zero dependents
    on every incremental reindex** (under-indexing — a changed file's dependents never re-extracted);
    `build_callers_cypher` (Plan 10 L1637) → empty GRAPH_QUERY excerpts. Every test was **green by construction** —
    each seeded `:File {path:…}` to match its own query (Plan 5 find_callers L3389, the #12 seed L4184), and Plan
    10's test asserted only the MATCH *shape* (L1555), never executing against parser-shaped nodes. **Orthogonal to
    D-5.10/G15** (#11/#13): that pass fixed the edge *topology* (Symbol→Symbol `:CALLS`, File→Symbol `:DEFINES`)
    and never touched the File *property* accessor — same query family, different axis. **Fix (Design 1,
    advisor-endorsed):** the four readers + spec §8.4 (root, L1358-66) now read `file_path`, keeping the `AS path` /
    `AS result` *output aliases* so no Python consumer changed; the two `:File {path}` test seeds were re-keyed; the
    spec + each reader carry an inoculation note ("do not simplify the accessor to `.path`"). **One latent
    follow-up noted, not solved here:** whether *production* Joern indexing emits `file_path` values under the same
    path-normalization `git diff --name-only` produces (the #12 test holds by construction because it seeds and
    diffs the same root-relative names) — a §8.4 IN-match-fidelity question banked for the Plan 5/6
    extractor-normalization contract. **Related divergence (noted, not a defect — doc left unchanged):**
    `architecture.md` §20's target-vision schema names the File path property `path` (its cross-node convention —
    `Endpoint`/`Test`/`Doc` use `path` too) and lists `language`/`last_modified_at` the slice never emits; the
    slice's `file_path` is the implemented partition-key choice. Conforming the one token would break
    `architecture.md`'s internal `path` convention while leaving the rest divergent — reconciling the target schema
    to the implemented slice schema is a separate, pre-existing item, out of this query fix's scope.
    *(Intra-ledger reconciliation: §7.4 #13/#14's descriptive `DISTINCT f.path` mentions — written before this
    re-key — were updated to `f.file_path` so the ledger's own prose matches the resolved query; the `.path`
    references in this entry are intentionally retained as the historical defect description.)*
17. **Banked opt-in hardening discoveries #64/#65/#67/#68 — all actioned this pass (plan-doc `Create:`-spec
    edits).** Surfaced-and-banked during the audit (opt-in, not release-blocking); resolved now:
    - **#64 — RetryPolicy vestigial fields trimmed** (cross-plan): unused exponential-backoff fields removed so the
      type carries only what the reclaim/retry ladder actually reads.
    - **#65 — `CODEORACLE_VALKEY__*` required fields had no runtime env source.** All six `ValkeyConfig` fields are
      required (fail-fast at construction, no defaults), but nothing beyond `…__URL` was set in compose/env. Fixed
      with a shared `x-valkey-env` YAML anchor (Plan 13 compose) merged into the seven queue-touching services,
      plus a docstring inoculation against "unifying" the deliberate double-underscore `CODEORACLE_VALKEY__` prefix. Not the
      #70 mismatch — `ValkeyConfig` is self-consistent; the gap was missing env *sources*, so the fix adds vars, no
      rename.
    - **#67 — SCIP/OpenGrep extractor subprocesses had no timeout** (worker-liveness): per-call timeout fields added
      (Joern's ≤600s semantics, module-constant mechanism — advisor-endorsed) with kill-on-expiry. OpenGrep's scan
      timeout held at **100s** so a worst-case sequential batch (count_per_poll=5 × 100 = 500s) stays *under* the
      600s IndexingOrchestrator completion barrier with margin — the consumer drains its batch sequentially (Plan 5
      `for raw in …: await _process_one`), so the 5× math is real; 120s would land the batch *at* the barrier and
      could fire mid-batch, mis-marking not-yet-reached files FAILED. **Three explicitly deferred follow-ups:**
      (a) the reclaim-activation timing window (in-flight bound = count_per_poll × handle; reclaim rescues in-barrier
      only when `reclaim_min_idle_ms + one handle < 600s` — operators keeping per-file timeouts well under ~200s
      stay in the window); (b) the **embeddings** worker's separate "no per-handle ceiling" gap (Plan 6 L7221) is
      out of #67's extractor-subprocess scope, still banked; (c) optional promotion of the module-constant timeouts
      to env-config (`BaseSettings`) if operators need per-deploy tuning.
    - **#68 — all-extractors-failed barrier marked the reindex APPLIED** (availability): D-GATE Option B
      (last-good fallback) — an all-failed extractor barrier now resolves to FAILED (prior good index stays live),
      while a partial failure still APPLIES (per the AskUserQuestion decision "All-failed → FAILED, partial →
      APPLIED").
    - **Cross-file citation ripple — caught + repaired this pass.** Applying #64 (−57 lines, RetryPolicy field
      deletion), #65 (+4, `ValkeyConfig` docstring inoculation) and #67 (+154, extractor-timeout block) shifted
      line numbers that *other* docs cite by `LNNNN`. A post-application ripple audit (two isolated fresh-reader
      passes — cross-doc coherence + line-cite drift, substituting for the platform-repo `ripple-check` skill whose
      deterministic substrate is not provisioned in this orchestrator repo) re-pointed every drifted inbound
      citation: ledger §7.2/§7.3 Plan-6 refs (`L4413/4453/4500/4703` → `L4565/4605/4652/4855`) and the
      `ValkeyConfig` cite-cluster in mcp-server + api-service (`L437/440/2182/6629` → `L441/444/2132/6983` — the
      last a *pre-existing* wrong cite corrected while already rewriting that string). The same audit caught two
      *semantic* casualties: (1) of #68 — Plan 10 (agent-orch L56) still asserted the barrier "marks even an
      all-extractors-failed reindex APPLIED", orphaned by #68's inversion, now corrected to partial→APPLIED /
      all-failed→FAILED; (2) of #64 — the trimmed RetryPolicy's fixed-min-idle reclaim was still
      narrated as "backoff" at four sites: spec §3.2 (platform-scaffolding L241, "exponential backoff") and Plan 6
      (snapshot-extractors L419/L471/L7203, WorkerConsumer "retries … with backoff"), each contradicting the
      docstring's own inoculation ("not by a per-attempt backoff curve"; pacing is the fixed reclaim min-idle) —
      all four now corrected to reclaim-ladder phrasing. The spec site fell to the "exponential backoff"
      phrase-grep; the three Plan 6 sites used the bare word and surfaced only under a root-word ("backoff")
      concept-sweep. **Implication for code execution:** re-applying #64/#65/#67 against `codeoracle/` source must
      re-run *both* checks this audit ran — the inbound-cite check (a `LNNNN` citation is a dependency edge any
      insertion above it silently breaks) *and* the concept-drift check (a field removal can orphan prose that
      names no field — as #64's backoff casualties did — so sweep the root *concept* word, not just the adjectival
      phrase or the identifier; a line-cite or identifier grep will never catch it). The concept-drift check is
      **bounded, not open-ended**: only a *semantics-changing* edit can orphan a concept, and this pass had exactly
      two — #64's field removal and #68's `all-failed→APPLIED`→`FAILED` inversion (the additive #65/#67 changed no
      contract, so they orphan nothing). Both were then swept corpus-wide — the #64 root-word `backoff` sweep and a
      symmetric #68 `all-failed`-outcome sweep — confirming the #68 casualty (agent-orch L56) was the *only*
      surviving orphan of its inversion: every other `all-failed` sibling mention already asserts the post-inversion
      outcome (`→ FAILED`, partial → APPLIED) or names the old bug only to describe the regression test that guards
      it. The doc-side concept surface is therefore closed by **enumeration of the semantics-changing edits**, not by
      an unbounded "grep until clean" — the code-execution pass inherits the same two-edit completion criterion.

**Net-new findings surfaced during the close-out reconciliation (audit-quality rows — NOT §7.2 finding-legs, do NOT alter the 123 denominator):**

18. **NN-1 — two false "Renovate regex managers" claims — RESOLVED (cross-plan edit).** Plan 1 L34 ("Plan 2
    verifies the regex-manager block") + Plan 8 L32 ("Plan 8 extends the regex-manager set") asserted a mechanism
    that does NOT exist (Plan 1's `renovate.json5` is packageRules only). Explicitly NOT covered by any existing
    D-1.x (closest is D-1.3 *path* drift — a different defect). Fix: Plan 1 L34 rewritten to state there are NO
    custom/regex managers (`pinned.env` pins are not a Renovate-managed set; drift caught by Plan 2's
    `verification-pins` CI job) + drop the false Plan-2-cites-Task-13 parenthetical; Plan 8 L32 rewritten to state
    Plan 8 does NOT extend any regex-manager set (new deps land in `pyproject.toml`/compose, covered by Renovate
    standard managers + `verification-pins`). Registered standalone (not folded into D-2.7).
19. **NN-3 — spec §5.6 under-ban of "ollama" — RESOLVED (spec amendment).** The banned-api block bans only
    `openai`/`anthropic`/`litellm`; the plan corpus correctly also bans `ollama` (Plan 4 L7/L43/L161). Fix: add an
    `"ollama".msg` entry to spec §5.6 banned-api block (Plan 1 Task 4 owns it; §A11). Plan side already correct;
    informational, non-blocking (spec lagged plan).
20. **NN-4 — osv-scanner vs pip-audit — DECIDED → osv-scanner.** Spec §7.1/§7.4/§8.5 gate 7 name
    `osv-scanner --lockfile=uv.lock`; Plan 1A `security.yml` implemented `pip-audit --strict` (no osv-scanner).
    Reconciled to osv-scanner: Plan 1A `security.yml` changed to `osv-scanner --lockfile=uv.lock` (matches spec gate
    7; no spec amendment needed). Plan 1 L1012 already flags it as Plan-1A-owned, not a Plan-1 gate.
21. **NN-5A — CLI wiring inconsistency A ("when does the real CLI land") — RESOLVED (cross-plan edit, Typer
    migration).** repo-skeleton (L1471) + worker-framework say first subcommand = Plan 5 Task 14; operational-infra
    (L1252) said "Plan 11+". Fix: pin the first functional subcommand to Plan 5 Task 14 (`index`); correct
    operational-infra L1252's "Plan 11+" wording to match. Lands in the serialized `cli/__main__.py` Typer edit.
22. **NN-5B — CLI wiring inconsistency B (broken `[project.scripts]` `codeoracle` target) — RESOLVED (cross-plan edit,
    Typer migration).** mcp-server mapped `codeoracle = "codeoracle.cli.main:main"`, but every plan creates only
    `cli/__main__.py` exposing the Typer object named `app`; no `cli/main.py::main` exists. Fix: the console-script target is
    repointed to the real Typer app object (`codeoracle = "codeoracle.cli.__main__:app"`, verified at mcp-server L2904) in the
    serialized `cli/__main__.py` edit; the dangling `cli.main:main` reference is removed.

> **NN-2 is folded into D-4.11** (its spec §5.4 amendments §A4) — no standalone row. NN-1/NN-3/NN-4/NN-5A/NN-5B are
> standalone audit-quality rows here, each carrying a RESOLVED/DECIDED disposition — none is left OPEN, so none
> enters the §7.3 residue, and none becomes a §7.2 finding-leg (the 123 denominator is unchanged).

### 7.5 §6 action-list status vs HEAD

Maps each §6 prescription (which the frozen §6 introduces with "Nothing here has been actioned") to its HEAD
state, so a reader of §6 sees what is already done.

**§6 DECIDE table (L522-537):** P0-1/D-2.1 **DECIDED** (1536) · P0-8/G15/D-5.1 **split** (FQN-normalize
DECIDED + pinned; cross-file-link half **D-5.10 RESOLVED**, Option B this pass) · P0-3/G13 **DECIDED** (Slice +1) · P0-5/G1-G12 **RESOLVED**
(Plan 1A) · P0-6/D-2.2 **RESOLVED** · P0-7/D-10.1 **RESOLVED** · D-1.4 **DECIDED** (85) · D-3.1 **DECIDED**
(spec amended; 2 stale-order residuals fixed 261c6e8) · D-6.1/G14 **RESOLVED** · D-10.U2 **RESOLVED** (written justification, task #74) · D-5.10/Joern
**RESOLVED** (Option B — `:Symbol` kept, linking pass adopted) · G21/M-8.1 **RESOLVED** · D-9.3/M-9.1 **RESOLVED** (audit channel — §A10 / §7.2 Plans 8+9, this pass) · D-1.13 **DECIDED** (>=3.14).

**§6 EDIT table (L542-569):** D-3.0/D-4.7/P0-4 **RESOLVED** · G11/P0-2 **RESOLVED** (Plan 1A) · **G16 RESOLVED** (owner Plan 5, §7.4 #15) ·
D-10.3 **RESOLVED** (spec half) · D-1.1/1.2/1.3 **RESOLVED** · D-7.1 **RESOLVED** ·
D-3.4/D-4.4 **RESOLVED** (D-3.4 = full CODEORACLE_LLM__ corpus fix, d50e32e) · D-4.1 **RESOLVED** · D-9.1/M-9.2 **RESOLVED**
(StringConstraints in Plan 3) · D-9.2 **RESOLVED** · D-10.U1 **RESOLVED** · D-10.U4 **RESOLVED** · G18 **RESOLVED** ·
G19 **RESOLVED** · G20 **RESOLVED** · D-5.8 **RESOLVED** (Plan 1 scaffolds `cli/`) · D-2.6/P0-9 **RESOLVED** ·
D-4.6/P0-10 **RESOLVED** · D-4.10 **RESOLVED** · D-5.9/P0-11 **RESOLVED** (canary) · D-6.6/6.8/6.9/6.14
**RESOLVED** (P0-12/13/14) · D-7.5/P0-15 **RESOLVED** · D-7.6/P0-16 **RESOLVED** · D-10.U6/P0-17 **RESOLVED** ·
D-5.12/M-5.8 **RESOLVED** (Plan-5 side).

---

## Appendix — per-plan sub-agent reports

The 10 full adversarial reports (one per plan, each with the complete 72-item coverage map,
verbatim deviation transcriptions, file-size tables, and OOP/slop audits) are the source material
for this synthesis. They are not reproduced here; this document is the reconciled view. Re-run the
audit dispatch if a per-plan report needs to be regenerated.
