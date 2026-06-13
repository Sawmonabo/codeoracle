# Repo Skeleton + CI/CD from Commit #1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Bootstrap `src/codeoracle/` as a `uv`-managed Python 3.14 project with the core quality gates (ruff, ty, pytest+coverage, file-length 700-line ceiling, gitleaks via pre-commit, Renovate) wired into CI and pre-commit from the first commit. The dedicated security-CI gates (semgrep SAST, dependency-CVE audit, SBOM, Trivy image scans) ship in Plan 1A's discrete `security.yml` + `image-build.yml` per spec §7.1 — they are deliberately **not** folded into Plan 1's `ci.yml` (see Task 10).

**Architecture:** Single `pyproject.toml` houses all tool configs (`[tool.uv]`, `[tool.ruff]`, `[tool.ty]`, `[tool.pytest.ini_options]`, `[tool.coverage]`). The `.github/workflows/ci.yml` workflow Plan 1 ships has **four parallel jobs: lint, type, test, file-length-gate** — the spec (§7.1) splits the CI surface across four discrete workflow files, and the security + image-scan files (`security.yml`, `image-build.yml`) are owned by Plan 1A, which also refactors `ci.yml` to keep security out of it. Pre-commit runs the same lint/type/length checks locally as CI, plus a local gitleaks hook. Renovate config groups dep bumps by ecosystem so they land as reviewable batches.

**Tech Stack:** Python 3.14 · `uv` · `ruff` · `ty` · `pytest` + `pytest-asyncio` + `pytest-cov` · `pre-commit` · `gitleaks` (pre-commit secret scan) · Renovate · GitHub Actions. (Semgrep / Trivy / dependency-CVE / SBOM tooling is Plan 1A's `security.yml` + `image-build.yml`.)

**Spec source:** `docs/superpowers/specs/2026-05-21-platform-scaffolding-design.md` §3.1, §3.3, §3.4, §7 (entire), §5.6 (banned-api).

**Commit cadence:** every task that produces work ends with a local commit (Task 10 is a deliberate no-op — security CI is deferred to Plan 1A — so it adds no commit). The whole plan ends with one push to `origin/main` after Task 14.

---

## Cross-plan dependencies

### Provides

| Provides to | Identifier | Used by |
|---|---|---|
| Plan 1A | `pyproject.toml` | Plan 1A's compose stack + Docker matrix inherit Plan 1's `pyproject.toml` (per Plan 1A's DAG-position prose: "consumes Plan 1's `pyproject.toml`"); Plan 1A adds OTel deps to it, does not re-create it. |
| Plan 1A | `ci.yml skeleton` (`.github/workflows/ci.yml`) | Plan 1A inherits the CI workflow shape (jobs, matrix, OS targets) from Plan 1's Task 9 and extends it with infra-specific steps (per Plan 1A's DAG-position prose enumerating `ci.yml` as inherited). |
| Plan 1A | `.dockerignore` | Plan 1A's Docker matrix uses Plan 1's `.dockerignore` to scope build contexts (per Plan 1A's DAG-position prose + Docker-matrix section). |
| Plan 1A | pytest marker: `integration` | Plan 1A's preflight + live-infra tests use the `integration` marker to scope CI gating (per Plan 1A's preflight + live-infra test sections). |
| Plan 1A | pytest marker: `characterization` | Plan 1A's characterization tests reuse this marker registered in Plan 1 (per Plan 1A's live-infra test section). |
| Plan 1A | pytest marker: `live_llm` | Plan 1A's live-LLM gated tests reuse this marker registered in Plan 1 (per Plan 1A's live-infra test section). |
| Plan 1A | `codeoracle.cli` (variant module, `src/codeoracle/cli/__init__.py` + `src/codeoracle/cli/__main__.py`) | Plan 1 ships the real Typer `app` scaffold (both files) before Plan 1A runs; Plan 1A's Dockerfile matrix bakes `codeoracle.cli` as the `VARIANT=cli` entrypoint and imports the package against Plan 1's files. Reconciled: Plan 1A creates no stub (the earlier ships-CLI-stub prose was retired once Plan 1 took ownership of the scaffold), so stub and real code never coexist. |
| Plan 1A | `infra/scripts/check_file_length.py` | Plan 1A defers file-length enforcement entirely to Plan 1's gate (Plan 1A's "Out of scope" section explicitly excludes it from Plan 1A's scope). |
| Plan 1A | D-1.2 mechanical EDIT (`check_file_length.py` `.sh`→`.py` shipping decision) | Plan 1A's "Plan Defers To" section defers the D-1.2 mechanical EDIT to Plan 1; Plan 1A inherits whichever form lands. |
| Plan 1A | D-1.3 mechanical EDIT (renovate path drift) | Plan 1A's "Plan Defers To" section defers the D-1.3 mechanical EDIT to Plan 1; Plan 1A's Renovate-config-path expectations track whatever Plan 1 ships. |
| Plan 2 | `uv` runtime toolchain (installed by Plan 1 Task 1) | Plan 2's prerequisite check explicitly cites Plan 1 ("`uv --version  # already installed in Plan 1`"); Plan 2 invokes `uv sync` and `uv run` against the venv Plan 1 scaffolded. |
| Plan 2 | Renovate config (Plan 1 Task 12) | Plan 1 Task 12 ships the Renovate config (`.github/renovate.json5` carries `packageRules` only — NO custom/regex managers). The `infra/versions/pinned.env` pins are NOT a Renovate-managed set; nothing in `renovate.json5` reads that file. Pin drift is caught by Plan 2 Task 13's `verification-pins` CI job (manifest-inspect re-run on every pin), not by a regex manager. |
| Plan 2 | `.env.example` | Plan 2 uses Plan 1's `.env.example` as the env-var boundary spec (env-key surface, namespacing convention `CODEORACLE_<COMPONENT>__`). |
| Plan 2 | `CODEORACLE_LLM_VLLM__MODEL` (empty env-var slot intentionally left blank for Plan 2) | Plan 1's `.env.example` declares the key with an empty value (no-placeholders rule); Plan 2 chooses the Qwen model identifier and fills it. |
| Plan 2 | `src/codeoracle/` empty layer dirs (scaffold) | Plan 1 Task 2 scaffolds the `core/models/db/adapters/services/api/mcp/agent/workers/cli` layer dirs; Plan 2 reads the scaffold to determine which paths are NOT yet populated (drives Plan 2's preflight-fixture placement decision). |
| Plan 3 | `pyproject.toml` | Plan 3 fail-fast-checks for the file at its prerequisite-check fail-fast; Plan 3 extends `[project] dependencies` with `pydantic>=2.7,<3.0` and `structlog>=24.1,<25.0`. |
| Plan 3 | `.github/workflows/ci.yml` | Plan 3 extends Plan 1's CI workflow by adding its DB-migration smoke-test job (alongside the existing lint/type/test/file-length jobs) and adds the `services.postgres` + `bootstrap_db.sh` block to the `test` job when it ships the first integration tests. Security CI lives in Plan 1A's `security.yml`, not `ci.yml`. |
| Plan 3 | `src/codeoracle/` scaffold | Plan 3's fail-fast at its prerequisite-check fail-fast checks for the scaffold; Plan 3 populates `core/`, `models/`, `db/` against the scaffold's empty layer dirs. |
| Plan 3 | `infra/scripts/check_file_length.py` | Plan 3 fail-fast-checks for the script at its prerequisite-check fail-fast and invokes it under Plan 3's in-task validation steps. |
| Plan 3 | `pyproject.toml [tool.ruff.lint.flake8-tidy-imports.banned-api]` block | Plan 1 Task 4 installs the banned-api block (initial openai/anthropic bans); Plan 3 verifies its presence at its banned-api verification step. |
| Plan 3 | `.env.example` (file stub) | Plan 1 Task 13 ships a minimal `.env.example` stub; Plan 3 Task 7 Step 2 overwrites with the full dev-default values (Database / Valkey / Tempo placeholder rows that Plan 4 and downstream plans append to). |
| Plan 4 | `ruff` lint/format toolchain | Plan 1 Task 3 installs ruff (`[dependency-groups].dev` + `[tool.ruff]` config); Plan 4 inherits the toolchain unchanged. |
| Plan 4 | `ruff>=0.5.0` version pin (in `[dependency-groups].dev`) | Plan 4 requires ruff `>=0.5.0` for the `per-file-ignores` syntax; Plan 1 ships `ruff>=0.6.0`, satisfying the bound. |
| Plan 4 | `pyproject.toml [tool.ruff.lint.flake8-tidy-imports.banned-api]` block (openai/anthropic) | Plan 1 Task 4 installs the bans for openai/anthropic outside `adapters/llm/providers/`; Plan 4 builds the LLM provider abstraction against that boundary (no provider SDK imports leak into services/api/agent paths). |
| Plan 5 | repo skeleton (`pyproject.toml` + `src/codeoracle/` scaffold + CI workflow + `check_file_length.py` + Renovate + pre-commit) | Plan 5 inherits the macro-deliverable of Plan 1; Plan 5's `imports.1` cites "repo skeleton" as a single unit (Worker framework + Joern vertical layers land into the scaffold's empty `adapters/`, `services/`, `workers/`). |
| Plan 5 | CI workflow (unit + integration tests on every PR — `.github/workflows/ci.yml`) | Plan 1 Task 9 ships the unit/integration jobs; Plan 5's tests (Joern vertical + Worker framework) run under those jobs. |
| Plan 5 | `pyproject.toml [tool.ruff] + [tool.ty]` config | Plan 1 Task 3 (`[tool.ruff]`) + Task 5 (`[tool.ty]`) install the lint/type configuration; Plan 5's modules pass through the same gates unchanged. |
| Plan 6 | `pyproject.toml [tool.ruff.per-file-ignores]` (`adapters/llm/providers/*`) | Plan 1 Task 4 establishes the per-file-ignores glob exempting `adapters/llm/providers/*` from the banned-api boundary; Plan 6 extends the glob to include `adapters/llm/embeddings.py` + `adapters/llm/providers/openai_embed.py`. |
| Plan 6 | `pyproject.toml [tool.ruff.lint.flake8-tidy-imports.banned-api]` block (openai/anthropic/litellm/vllm) | Plan 1 Task 4 installs the bans; Plan 6 respects the boundary by routing all LLM/embedding calls through ABCs in `adapters/llm/` rather than direct SDK imports. |
| Plan 6 | `.env.example` (env-var documentation surface) | Plan 1 Task 13 ships the file; Plan 6 appends `CODEORACLE_EMBEDDING__KIND`, `CODEORACLE_EMBEDDING__BASE_URL`, `CODEORACLE_EMBEDDING__API_KEY` rows to document the embedding adapter's env surface. |
| Plan 7 | `TEMPO_VERSION` pin policy (version-pin doctrine) | Plan 1's Renovate config + pyproject.toml pinning style set the doctrine ("every external tool is pinned by exact version, bumps via Renovate PRs"); Plan 7 inherits the convention for `TEMPO_VERSION`. |
| Plan 7 | `src/codeoracle/README.md` | Plan 1 Task 13 creates the README; Plan 7's closure step appends a `## Workers` section to enumerate the `traces_ingest` worker. |
| Plan 7 | roadmap convention (`Plan N: pending` → `Plan N: delivered (commit <sha>)`) | Plan 1 establishes the convention in its closure step; Plan 7's closure step adopts it verbatim. |
| Plan 9 | `pytest` | Plan 1 Task 1 Step 2 declares `pytest>=8.0` in `[dependency-groups].dev`; Plan 9 imports the test-runner without redeclaring it (per Plan 9's "no new pytest markers, no testcontainers, no new runtime dependencies" preamble). EQ-TRANSITIVE-DEP precondition (paired with C-09). |
| Plan 9 | `pytest-asyncio` | Plan 1 Task 1 Step 2 declares `pytest-asyncio>=0.23` in `[dependency-groups].dev`; Plan 9 uses the async-mode test runner without redeclaring it. EQ-TRANSITIVE-DEP precondition (paired with C-09). |
| Plan 13 | `ruff` / `ty` / `pytest` / `check_file_length.py` | `slice-acceptance.yml` (Plan 13 Task 12) consumes these as the architectural-invariant jobs — gates 1-5 of §8.5 |

---

## Plan-wide preconditions

Before starting Task 1, verify the working tree is clean (no unrelated uncommitted changes):

```bash
git status --short
# Expected: empty (or only the spec/plan files this work is tracking)
```

If the tree is dirty with unrelated work, stop and reconcile first. Do not let unrelated changes ride along.

---

## Task 1: Initialize uv project + Python pinning + ignores

**What this task delivers:** a minimal `pyproject.toml`, pinned Python version, sensible `.gitignore` / `.dockerignore`, and a generated `uv.lock`. After this task, `uv sync` produces a working virtualenv with no dependencies (yet).

**Files:**
- Create: `pyproject.toml`
- Create: `.python-version`
- Create: `.gitignore`
- Create: `.dockerignore`
- Generated: `uv.lock`, `.venv/` (in `.gitignore`)

- [ ] **Step 1: Create `.python-version`**

Single line, no trailing whitespace:

```
3.14
```

- [ ] **Step 2: Create initial `pyproject.toml`**

```toml
[project]
name = "codeoracle"
version = "0.1.0"
description = "CodeOracle — evidence-first agentic application intelligence platform"
readme = "README.md"
requires-python = ">=3.14"
license = { text = "Apache-2.0" }
authors = [{ name = "CodeOracle", email = "engineering@codeoracle.local" }]
dependencies = []

[dependency-groups]
dev = [
    "pytest>=8.0",
    "pytest-asyncio>=0.23",
    "pytest-cov>=5.0",
    "ruff>=0.6.0",
    "ty>=0.0.1",
    "pre-commit>=3.7",
]

[build-system]
requires = ["hatchling>=1.18"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["src/codeoracle"]

[tool.uv]
package = true
```

> **`[tool.uv] package = true` (spec §7.5 carries `managed = true`).** These keys are orthogonal in uv, not interchangeable: `managed` (default `true`) controls whether uv auto-locks/syncs the project environment; `package` controls whether the project *itself* is built and installed into that environment. For this installable `src/codeoracle/` layout — a real `[build-system]` + `[tool.hatch.build.targets.wheel]` above — `package = true` is the load-bearing key: it forces the editable build/install so `import codeoracle` resolves from the venv (the `uv run python -c "import codeoracle"` verification in Step 6 depends on it). `managed` defaults to `true` whether or not it is written, so the spec's `managed = true` is a no-op restatement of the default and is NOT what makes the package importable. This plan therefore ships `package = true`; spec §7.5 is amended to match (see the spec-amendment proposal — Plan 1 owns `pyproject.toml`, so the spec conforms to the plan here).

- [ ] **Step 3: Create `.gitignore`**

```
# Python
__pycache__/
*.pyc
*.pyo
*.egg-info/
dist/
build/

# Tooling caches
.cache/
.pytest_cache/
.ruff_cache/
.mypy_cache/

# Virtualenv
.venv/

# Secrets — never commit
.env
.env.*
!.env.example

# Editor / OS
.vscode/
.idea/
.DS_Store
*.swp
```

- [ ] **Step 4: Create `.dockerignore`**

```
# Git + GitHub
.git/
.github/

# Virtualenv + caches
.venv/
__pycache__/
.cache/
.pytest_cache/
.ruff_cache/

# Secrets
.env
.env.*

# Repo overhead the image doesn't need
docs/
tests/
*.md
.pre-commit-config.yaml
.gitignore
```

- [ ] **Step 5: Create empty package directory marker**

```bash
mkdir -p src/codeoracle
touch src/codeoracle/__init__.py
touch src/codeoracle/py.typed
```

- [ ] **Step 6: Generate `uv.lock` and verify install**

```bash
uv sync
```

Expected: `uv` creates `.venv/`, installs dev deps from `[dependency-groups]`, writes `uv.lock`. No errors.

```bash
uv run python -c "import codeoracle; print(codeoracle.__file__)"
```

Expected: prints a path ending in `src/codeoracle/__init__.py` (or the editable install equivalent).

- [ ] **Step 7: Commit**

```bash
git add pyproject.toml uv.lock .python-version .gitignore .dockerignore src/codeoracle/__init__.py src/codeoracle/py.typed
git commit -m "feat: initialize uv project with Python 3.14 + empty codeoracle package"
```

---

## Task 2: Scaffold the layered directory tree

**What this task delivers:** the empty package skeleton matching spec §3.2 — `models/`, `core/`, `db/`, `adapters/`, `services/`, `cli/`, `api/`, `mcp/`, `agent/`, `workers/`. Each is a Python package (`__init__.py`) so imports work cleanly later. Layer purity (per memory `strict-layer-purity`) is structural from the start. `cli/` ships a tiny `__main__.py` Typer `app` scaffold that Plan 5 Task 14 extends with the `codeoracle index` subcommand — defining the entry point here lets every later plan reference `python -m codeoracle` without depending on Plan 5 having shipped yet.

**Files:**
- Create: 10 empty directories under `src/codeoracle/` each with an `__init__.py`
- Create: `src/codeoracle/cli/__main__.py` (Typer `app` scaffold — the `codeoracle` CLI entry; Plan 5 Task 14 extends it with the first subcommand `codeoracle index`)
- Create: `tests/__init__.py`
- Create: `tests/{unit,integration,characterization}/__init__.py` (the three test roots named in spec §3.1; later plans drop test files into them — Plan 1A + Plan 8 add the first `tests/unit/...` and `tests/integration/...` files, Plan 13 the first `tests/characterization/...` — but the package dirs themselves are scaffolded here so the layout is coherent across plans from commit #1)

- [ ] **Step 1: Create the layer directories**

```bash
for layer in models core db adapters services cli api mcp agent workers; do
  mkdir -p "src/codeoracle/$layer"
  touch "src/codeoracle/$layer/__init__.py"
done

mkdir -p tests tests/unit tests/integration tests/characterization
touch tests/__init__.py \
  tests/unit/__init__.py \
  tests/integration/__init__.py \
  tests/characterization/__init__.py
```

The three subdirs are the test roots named in spec §3.1 (`tests/{unit,integration,characterization}/`): `unit/` (fast, no external deps), `integration/` (against the docker-compose stack), `characterization/` (incremental-vs-full equivalence tests — the home for the `characterization` pytest marker registered in Task 6). Scaffolding all three here keeps the layout coherent: Plan 1A writes the first `tests/unit/...` + `tests/integration/...` files, Plan 8 adds more under `tests/unit/`, and Plan 13 adds the first `tests/characterization/...` file — none of them re-create the package dir, they only add test modules into the already-scaffolded root.

- [ ] **Step 2: Add docstrings to layer `__init__.py` files documenting layer purpose**

Each `__init__.py` gets a single-line module docstring that tells a reader what belongs in this layer. This is the only place we add module-level comments — directory purpose is structural and unlikely to drift.

Write the following one-liners (use the Edit tool one at a time, or a small script):

```python
# src/codeoracle/models/__init__.py
"""Pure Pydantic data types and enums. No I/O, no behavior beyond @field_validator."""
```

```python
# src/codeoracle/core/__init__.py
"""Cross-cutting infrastructure: config, logging, telemetry, base exceptions."""
```

```python
# src/codeoracle/db/__init__.py
"""Platform's own database access (PG18 + AGE + pgvector). Repository pattern under db/repositories/."""
```

```python
# src/codeoracle/adapters/__init__.py
"""I/O wrappers for EXTERNAL systems only. If removing the external system would delete this class entirely, it belongs here."""
```

```python
# src/codeoracle/services/__init__.py
"""Business-logic classes that compose models + adapters + repositories. HandleResolver, Verifier, TrustMatrix, ContextBudget — all live here."""
```

```python
# src/codeoracle/cli/__init__.py
"""Single CLI entry point (`codeoracle`). Subcommands register against the root group; see cli/__main__.py."""
```

```python
# src/codeoracle/api/__init__.py
"""FastAPI HTTP service process."""
```

```python
# src/codeoracle/mcp/__init__.py
"""MCP server process (stdio + streamable HTTP transports)."""
```

```python
# src/codeoracle/agent/__init__.py
"""LangGraph agent process: compose → score → verify."""
```

```python
# src/codeoracle/workers/__init__.py
"""Background worker processes — long-running Valkey queue consumers."""
```

- [ ] **Step 3: Create the `codeoracle` CLI entry scaffold at `src/codeoracle/cli/__main__.py`**

```python
"""`python -m codeoracle` entry point.

A Typer root app declared as a command group (the no-op `@app.callback()`
keeps it a group even when only one command is registered — see Plan 5
Task 14 for the first one, `codeoracle index`). Keeping the entry point here in
Plan 1 means every later plan can reference `python -m codeoracle <subcommand>`
without coupling its task list to Plan 5's CLI work.
"""

import typer

app = typer.Typer()


@app.callback()
def main() -> None:
    """CodeOracle command-line interface."""


if __name__ == "__main__":
    app()
```

This scaffold adds one runtime dependency — `typer` — to the project. Add it to `pyproject.toml` `[project.dependencies]` so `uv sync` resolves it:

```bash
uv add typer
```

Expected: `uv.lock` updates, `typer` lands in the project venv.

- [ ] **Step 4: Verify imports + the CLI scaffold work**

```bash
uv run python -c "
from codeoracle import models, core, db, adapters, services, cli, api, mcp, agent, workers
print('all layers importable')
"
uv run python -m codeoracle --help
```

Expected: first command prints `all layers importable`; second prints Typer's auto-generated help text for the `app` (no subcommands listed yet — Plan 5 adds the first).

- [ ] **Step 5: Commit**

```bash
git add src/codeoracle/ tests/__init__.py tests/unit/__init__.py tests/integration/__init__.py tests/characterization/__init__.py pyproject.toml uv.lock
git commit -m "feat: scaffold layered directory tree (models, core, db, adapters, services, cli, api, mcp, agent, workers) + cli scaffold"
```

---

## Task 3: Configure ruff lint + format

**What this task delivers:** `[tool.ruff]` and `[tool.ruff.lint]` sections in `pyproject.toml`. `uv run ruff check` and `uv run ruff format --check` both pass green against the empty skeleton.

**Files:**
- Modify: `pyproject.toml` (append `[tool.ruff]` sections)

- [ ] **Step 1: Append ruff config to `pyproject.toml`**

Add this block at the end of the file:

```toml
[tool.ruff]
line-length = 100
target-version = "py314"
src = ["src", "tests"]
extend-exclude = ["dist", "build", ".venv", "docs/superpowers"]

[tool.ruff.lint]
select = [
    "E", "W",   # pycodestyle errors + warnings
    "F",        # Pyflakes
    "I",        # isort (import ordering)
    "B",        # flake8-bugbear (likely bugs)
    "N",        # pep8-naming
    "UP",       # pyupgrade (modernize syntax)
    "SIM",      # flake8-simplify
    "RUF",      # ruff-specific
    "TID",      # flake8-tidy-imports (used by banned-api in Task 4)
    "PT",       # flake8-pytest-style
    "S",        # flake8-bandit (security)
    "ASYNC",    # flake8-async
    "ANN",      # flake8-annotations (annotation rigor — spec §7.5 floor)
    "C90",      # mccabe complexity ceiling (spec §7.5 floor; configured below)
]
ignore = [
    "E501",  # line length is governed by the formatter, not the linter
]

[tool.ruff.lint.mccabe]
max-complexity = 10  # spec §7.5 — C90 fires above cyclomatic complexity 10

[tool.ruff.lint.per-file-ignores]
"tests/**/*.py" = ["S101", "S105", "S106"]  # asserts + hardcoded test creds are fine in tests

[tool.ruff.format]
quote-style = "double"
indent-style = "space"
docstring-code-format = true
line-ending = "lf"
```

- [ ] **Step 2: Run lint — expect green**

```bash
uv run ruff check
```

Expected: `All checks passed!` (or no output with exit code 0).

- [ ] **Step 3: Run format check — expect green**

```bash
uv run ruff format --check
```

Expected: `N files already formatted` (or similar) with exit code 0.

- [ ] **Step 4: Smoke-test that the lint rules actually trigger**

Create a temporary file that violates a rule:

```bash
cat > /tmp/lint_smoke.py <<'EOF'
import os
import os  # F811 redefinition
EOF
uv run ruff check /tmp/lint_smoke.py
```

Expected: ruff reports `F811 Redefinition of unused 'os'`. Exit code 1.

```bash
rm /tmp/lint_smoke.py
```

- [ ] **Step 5: Commit**

```bash
git add pyproject.toml
git commit -m "feat: configure ruff lint (E,W,F,I,B,N,UP,SIM,RUF,TID,PT,S,ASYNC) + formatter"
```

---

## Task 4: Add ruff `banned-api` rules blocking direct LLM-SDK imports

**What this task delivers:** the structural enforcement of the LLM-provider-agnostic invariant (per memory `project-llm-provider-agnostic`). Direct `import anthropic`, `import openai`, `import litellm`, `import ollama` outside `src/codeoracle/adapters/llm/providers/` triggers a ruff error.

**Files:**
- Modify: `pyproject.toml` (extend `[tool.ruff.lint.*]` sections)

- [ ] **Step 1: Write the failing test — a smoke file that imports a banned module from the wrong location**

Create a temporary smoke file in `src/codeoracle/services/` that should be flagged:

```bash
cat > src/codeoracle/services/_banned_smoke.py <<'EOF'
"""Smoke test — should be flagged by ruff banned-api. Delete after Task 4 verification."""
import anthropic  # noqa
EOF
```

Run ruff — expect NO violation yet (banned-api not configured):

```bash
uv run ruff check src/codeoracle/services/_banned_smoke.py
```

Expected: passes (TID251 not yet enabled).

- [ ] **Step 2: Add the `[tool.ruff.lint.flake8-tidy-imports.banned-api]` block to `pyproject.toml`**

Append:

```toml
[tool.ruff.lint.flake8-tidy-imports.banned-api]
"anthropic".msg = "Use codeoracle.adapters.llm; direct LLM SDK imports are banned outside src/codeoracle/adapters/llm/providers/."
"openai".msg = "Use codeoracle.adapters.llm; direct LLM SDK imports are banned outside src/codeoracle/adapters/llm/providers/."
"litellm".msg = "Use codeoracle.adapters.llm; direct LLM SDK imports are banned outside src/codeoracle/adapters/llm/providers/."
"ollama".msg = "Use codeoracle.adapters.llm; direct LLM SDK imports are banned outside src/codeoracle/adapters/llm/providers/."
"anthropic.AsyncAnthropic".msg = "Use codeoracle.adapters.llm; direct LLM SDK imports are banned outside src/codeoracle/adapters/llm/providers/."
"openai.AsyncOpenAI".msg = "Use codeoracle.adapters.llm; direct LLM SDK imports are banned outside src/codeoracle/adapters/llm/providers/."
"openai.OpenAI".msg = "Use codeoracle.adapters.llm; direct LLM SDK imports are banned outside src/codeoracle/adapters/llm/providers/."
"anthropic.Anthropic".msg = "Use codeoracle.adapters.llm; direct LLM SDK imports are banned outside src/codeoracle/adapters/llm/providers/."
```

- [ ] **Step 3: Add per-file ignores for the future provider implementation files**

Update the `[tool.ruff.lint.per-file-ignores]` block to add provider escape-hatches. Replace the existing block with:

```toml
[tool.ruff.lint.per-file-ignores]
"tests/**/*.py" = ["S101", "S105", "S106"]
"src/codeoracle/adapters/llm/providers/*.py" = ["TID251"]
```

(The `providers/` directory doesn't exist yet — provider implementations land in Plan 4. Ruff tolerates per-file-ignores for non-existent paths; the glob applies the moment any file is created under `src/codeoracle/adapters/llm/providers/`.)

This uses the **`…/providers/*.py` glob exactly as spec §5.6 (`"src/codeoracle/adapters/llm/providers/*.py" = ["TID251"]`) and §7.5 both show** — the providers directory *is* "the only allowed location" for direct SDK imports, so any file inside it is exempt by construction. The glob is preferred over a hand-enumerated per-provider list because (a) the list rots — every provider Plan 4/6 adds (`mock.py`, `litellm.py`, embedding providers) would need a new line or silently lose its exemption, and (b) `mock.py` is a real provider file (spec §5.3) that a hand-list omits today; under the glob it is covered automatically should it ever import a banned SDK. The catalogue of which providers exist is the `providers/` directory listing itself, not a duplicated config block.

- [ ] **Step 4: Run the smoke test — expect failure with TID251**

```bash
uv run ruff check src/codeoracle/services/_banned_smoke.py
```

Expected output (the exact code is `TID251`):

```
src/codeoracle/services/_banned_smoke.py:2:8: TID251 `anthropic` is banned: Use codeoracle.adapters.llm; direct LLM SDK imports are banned outside src/codeoracle/adapters/llm/providers/.
```

Exit code: 1.

- [ ] **Step 5: Smoke-test that the per-file-ignore would work — create the provider file and verify**

```bash
mkdir -p src/codeoracle/adapters/llm/providers
touch src/codeoracle/adapters/llm/__init__.py
touch src/codeoracle/adapters/llm/providers/__init__.py
mv src/codeoracle/services/_banned_smoke.py src/codeoracle/adapters/llm/providers/_banned_smoke.py
uv run ruff check src/codeoracle/adapters/llm/providers/_banned_smoke.py
```

Expected: passes (TID251 ignored in providers/).

- [ ] **Step 6: Clean up smoke files**

```bash
rm src/codeoracle/adapters/llm/providers/_banned_smoke.py
```

(Leave the empty `adapters/llm/` and `adapters/llm/providers/` directories — they're real package boundaries that Plan 4 will populate.)

- [ ] **Step 7: Commit**

```bash
git add pyproject.toml src/codeoracle/adapters/llm/__init__.py src/codeoracle/adapters/llm/providers/__init__.py
git commit -m "feat: enforce LLM-SDK import boundary via ruff banned-api (TID251)"
```

---

## Task 5: Configure `ty` (Astral type checker, strict mode)

**What this task delivers:** `[tool.ty]` section configured for strict checking. `uv run ty check src/codeoracle` passes against the empty skeleton.

**Files:**
- Modify: `pyproject.toml` (append `[tool.ty]` sections)

- [ ] **Step 1: Verify the installed `ty` version AND supported config schema (it's young; config keys may shift)**

```bash
uv run ty --version
uv run ty check --help
```

Note the version. Then confirm the `[tool.ty.environment].root` / `[tool.ty.src].respect-ignore-files` / `[tool.ty.rules].all` keys below are supported in the pinned build by reading the help output and the ty config reference (https://github.com/astral-sh/ty/blob/main/docs/reference/configuration.md). The config schema is young (`0.0.x`) and may shift; if a key has moved (e.g. `environment.root` renamed, or `all` no longer a valid `[tool.ty.rules]` target), adjust the Step 2 snippet accordingly *before* editing `pyproject.toml` — preserving the three intents (source root = `src`, respect ignore files, strictest rules on). If the executable is missing, run `uv sync` first.

- [ ] **Step 2: Append the `[tool.ty]` config block to `pyproject.toml`**

```toml
[tool.ty.environment]
root = ["src"]

[tool.ty.src]
respect-ignore-files = true

[tool.ty.rules]
all = "error"
```

This realizes the three intents spec §7.5 prescribes — source root = `src`, respect ignore files, strictest rules on — using `ty`'s **real** configuration schema (verified against the `ty` config reference, https://github.com/astral-sh/ty/blob/main/docs/reference/configuration.md):

- `[tool.ty.environment] root = ["src"]` is where `ty` resolves first-party modules (so `codeoracle.*` imports resolve). `ty` has **no** `[tool.ty] src.root` key — first-party roots live under `[tool.ty.environment].root`, as a list.
- `[tool.ty.src] respect-ignore-files = true` honors `.gitignore`/`.ignore` during file discovery (real key; default `true`, restated here for explicitness).
- `[tool.ty.rules] all = "error"` promotes *every* `ty` rule to error — the strictest blanket setting `ty` exports, and the truest expression of the spec's "strict" intent. **`ty` has no `rules.strict = true` toggle**: per `ty`'s own typing FAQ, "ty does not currently have a dedicated strict mode, though it is a tracked feature" (developers are steered to Ruff's `flake8-annotations` for annotation enforcement until then). `all = "error"` is therefore the correct way to express maximal strictness without inventing a key `ty` would reject at Step 3. See the deviation note below.

> **Spec §7.5 carries an invalid `[tool.ty]` block (`spec-amendment proposed`, leg D-1.7).** Spec §7.5 writes the block as `src.root = "src"` + `src.respect-ignore-files = true` + `rules.strict = true`. Two of those three keys do not exist in `ty`:
> - **`rules.strict = true` — invalid.** `ty` has no built-in strict mode (typing FAQ, verbatim above). The blanket equivalent is `[tool.ty.rules] all = "error"`, used above.
> - **`src.root = "src"` — invalid.** First-party source roots live under `[tool.ty.environment].root` (a list), not `[tool.ty.src].root`. The `[tool.ty.src]` table accepts only `include` / `exclude` / `respect-ignore-files`.
> - **`src.respect-ignore-files = true` — valid**, and preserved above.
> Net: this plan preserves the spec's type-rigor *intent* via real `ty` config; the spec text is the stale party (orchestrator applies the §7.5 amendment). Hedged because `ty` is pinned at a `0.0.x` release — Step 1 re-verifies the exact key/rule shape against the version `pyproject.toml` actually pins before relying on this note. If the pinned build still rejects a key, reconcile via `uv run ty check --help` + the config reference, preserving the three intents (source root = `src`, respect ignore files, strictest rules on); do not silently relax `all = "error"` to a hand-picked subset.

- [ ] **Step 3: Run ty against the empty skeleton — expect green**

```bash
uv run ty check src/codeoracle
```

Expected: zero diagnostics (the skeleton is just `__init__.py` files with docstrings — nothing to type-check yet).

- [ ] **Step 4: Smoke-test that rules actually trigger**

Create a temporary file with an unresolvable import (`unresolved-import` is a confirmed `ty` rule that reliably fires, and is promoted to `error` by `all = "error"`):

```bash
cat > src/codeoracle/core/_ty_smoke.py <<'EOF'
import this_module_does_not_exist  # noqa: F401
EOF
uv run ty check src/codeoracle/core/_ty_smoke.py
```

Expected: ty reports an `unresolved-import` error (the `all = "error"` blanket setting promotes it to error-severity). If you instead want to assert annotation strictness specifically, note that `ty` has no annotation-enforcement rule yet (it steers that to Ruff's `flake8-annotations`), so `unresolved-import` is the reliable strict-mode smoke signal here.

```bash
rm src/codeoracle/core/_ty_smoke.py
```

- [ ] **Step 5: Commit**

```bash
git add pyproject.toml
git commit -m "feat: configure ty at maximal strictness (rules.all = error, environment.root = src)"
```

---

## Task 6: Configure pytest + pytest-asyncio + markers

**What this task delivers:** `[tool.pytest.ini_options]` section with markers (`integration`, `characterization`, `live_llm`), asyncio auto-mode, strict markers (typos in `@pytest.mark.X` fail loudly), and the `tests/` test root.

**Files:**
- Modify: `pyproject.toml` (append `[tool.pytest.ini_options]`)

- [ ] **Step 1: Append the pytest config block to `pyproject.toml`**

```toml
[tool.pytest.ini_options]
minversion = "8.0"
testpaths = ["tests"]
addopts = "-ra --strict-markers --strict-config"
asyncio_mode = "auto"
markers = [
    "integration: requires external services (Postgres, Valkey, etc.); skipped by default in CI's unit-only matrix",
    "characterization: pins existing behavior; failure means behavior drifted, may be intentional",
    "live_llm: requires real LLM-provider credentials; skipped by default, gated by 'run-live-llm' PR label",
]

# Warnings policy: show by default; promote to errors per-package as we decide what's worth being strict about.
# Plans 3+ will likely add `error::DeprecationWarning:codeoracle.*` once internal code is the source of any warnings;
# third-party deprecations stay informational so a single noisy upstream release doesn't redden CI.
filterwarnings = [
    "default",
]
```

- [ ] **Step 2: Run pytest against the empty `tests/` directory**

```bash
uv run pytest
```

Expected: `no tests ran in 0.NNs` (exit code 5 from pytest, which means "no tests collected"). This is fine for now; Task 7 adds the first smoke test.

- [ ] **Step 3: Commit**

```bash
git add pyproject.toml
git commit -m "feat: configure pytest with strict markers (integration, characterization, live_llm) + asyncio auto-mode"
```

---

## Task 7: Configure coverage + add the first smoke test

**What this task delivers:** `[tool.coverage.run]` and `[tool.coverage.report]` sections; one passing smoke test that proves pytest collection works and coverage reports correctly. CI's `--cov-fail-under` threshold has something to measure.

**Files:**
- Modify: `pyproject.toml` (append `[tool.coverage]` sections)
- Create: `tests/unit/test_smoke.py`

- [ ] **Step 1: Append the coverage config to `pyproject.toml`**

```toml
[tool.coverage.run]
source = ["src/codeoracle"]
branch = true
data_file = ".cache/coverage/.coverage"
omit = [
    "*/__init__.py",  # init files are layer markers, not code
    "tests/*",
]

[tool.coverage.report]
exclude_lines = [
    "pragma: no cover",
    "raise NotImplementedError",
    "if TYPE_CHECKING:",
    "@(abc\\.)?abstractmethod",
]
fail_under = 85
show_missing = true
skip_covered = false
precision = 1
```

- [ ] **Step 2: Add a tiny stub function so coverage has something to measure**

Create `src/codeoracle/core/version.py`:

```python
"""Platform version surfacing — single source of truth read by API health endpoints."""

__version__ = "0.1.0"


def get_version() -> str:
    return __version__
```

- [ ] **Step 3: Write the smoke test**

Create `tests/unit/test_smoke.py`:

```python
"""Smoke tests — verify the package imports cleanly and basic infra works."""

from codeoracle.core.version import __version__, get_version


def test_package_imports() -> None:
    import codeoracle

    assert codeoracle is not None


def test_all_layers_importable() -> None:
    from codeoracle import (
        adapters,
        agent,
        api,
        cli,
        core,
        db,
        mcp,
        models,
        services,
        workers,
    )

    for layer in (models, core, db, adapters, services, cli, api, mcp, agent, workers):
        assert layer is not None


def test_version_module() -> None:
    assert get_version() == __version__
    assert isinstance(__version__, str)
    assert __version__.count(".") == 2
```

- [ ] **Step 4: Run pytest + coverage**

```bash
uv run pytest --cov=codeoracle --cov-report=term-missing
```

Expected:
- 3 tests pass
- Coverage report shows `src/codeoracle/core/version.py` at 100%
- Overall coverage well above 85% (small surface area, fully exercised)
- Exit code 0

- [ ] **Step 5: Smoke-test that `--cov-fail-under=85` would actually fail under-threshold code**

```bash
uv run pytest --cov=codeoracle --cov-fail-under=200
```

Expected: pytest exits non-zero with "Coverage failure: total of NN is less than fail-under=200".

- [ ] **Step 6: Commit**

```bash
git add pyproject.toml tests/unit/test_smoke.py src/codeoracle/core/version.py
git commit -m "feat: configure coverage (85% floor) + smoke tests verifying layer imports"
```

---

## Task 8: Add pre-commit hooks (ruff, ruff-format, gitleaks, uv-lock, ty)

**What this task delivers:** `.pre-commit-config.yaml` that catches the same things CI catches, but locally, before the commit lands. Developers don't need to remember to run each tool. Spec §7.7 requires the `astral-sh/uv-pre-commit` `uv-lock` hook so a `pyproject.toml` edit that forgets `uv lock` is caught before the commit lands instead of failing CI's `verification-pins` job.

**Files:**
- Create: `.pre-commit-config.yaml`
- Modify: README (developer setup — added in Task 13)

- [ ] **Step 1: Create `.pre-commit-config.yaml`**

```yaml
# See https://pre-commit.com for documentation.
# Install hooks locally with: uv run pre-commit install

repos:
  - repo: https://github.com/astral-sh/ruff-pre-commit
    rev: v0.6.9
    hooks:
      - id: ruff
        args: [--fix]
      - id: ruff-format

  - repo: https://github.com/astral-sh/uv-pre-commit
    rev: 0.5.13
    hooks:
      - id: uv-lock  # Spec §7.7 — fail the commit if pyproject.toml drifts from uv.lock

  - repo: https://github.com/gitleaks/gitleaks
    rev: v8.18.4
    hooks:
      - id: gitleaks

  - repo: https://github.com/pre-commit/pre-commit-hooks
    rev: v4.6.0
    hooks:
      - id: check-merge-conflict
      - id: check-yaml
      - id: check-toml
      - id: check-added-large-files
        args: [--maxkb=500]
      - id: end-of-file-fixer
      - id: trailing-whitespace

  - repo: local
    hooks:
      - id: ty
        name: ty (type check, whole src/codeoracle tree)
        entry: uv run ty check src/codeoracle
        language: system
        pass_filenames: false
        types: [python]
```

(Task 11 will extend the `local` hooks block with a `file-length-gate` entry once the underlying script is created. Don't add it here — the hook entry references a script that doesn't exist yet, which would crash `pre-commit run` at the end of Task 8.)

- [ ] **Step 2: Install the hooks**

```bash
uv run pre-commit install
```

Expected: `pre-commit installed at .git/hooks/pre-commit`.

- [ ] **Step 3: Run all hooks against the existing tree to make sure everything's clean**

```bash
uv run pre-commit run --all-files
```

Expected: all hooks pass green. If `end-of-file-fixer` or `trailing-whitespace` rewrites anything, accept the changes and re-run.

- [ ] **Step 4: Smoke-test that the hooks block a bad commit**

```bash
echo "import anthropic" > src/codeoracle/services/_smoke.py
git add src/codeoracle/services/_smoke.py
git commit -m "test: this should be blocked"
```

Expected: pre-commit refuses with `TID251` violation. The commit does NOT land.

```bash
git reset HEAD src/codeoracle/services/_smoke.py
rm src/codeoracle/services/_smoke.py
```

- [ ] **Step 5: Commit the config itself**

```bash
git add .pre-commit-config.yaml
git commit -m "feat: pre-commit hooks (ruff, ruff-format, uv-lock, gitleaks, ty, basic file hygiene)"
```

---

## Task 9: GitHub Actions CI — lint + type + test jobs

**What this task delivers:** `.github/workflows/ci.yml` with three parallel jobs (lint, type, test). Every PR and push-to-main runs these. The job names are stable so branch protection can require them.

**Files:**
- Create: `.github/workflows/ci.yml`

- [ ] **Step 1: Create the workflow directory**

```bash
mkdir -p .github/workflows
```

- [ ] **Step 2: Write the initial `ci.yml`**

Create `.github/workflows/ci.yml`:

```yaml
name: CI

on:
  push:
    branches: [main]
  pull_request:
    branches: [main]
  workflow_call:  # callable as a reusable workflow (no inputs) — Plan 13's slice-acceptance.yml invokes the whole suite

permissions:
  contents: read

concurrency:
  group: ci-${{ github.ref }}
  cancel-in-progress: true  # spec §7.2 — newer pushes cancel stale runs on PRs AND main, saving CI minutes

jobs:
  lint:
    name: Lint (ruff)
    runs-on: ubuntu-24.04
    steps:
      - uses: actions/checkout@v4
      - name: Install uv
        uses: astral-sh/setup-uv@v4
        with:
          enable-cache: true
          cache-dependency-glob: "uv.lock"
      - name: Sync dependencies
        run: uv sync --frozen
      - name: ruff check
        run: uv run ruff check --output-format=github
      - name: ruff format check
        run: uv run ruff format --check

  type:
    name: Type check (ty)
    runs-on: ubuntu-24.04
    steps:
      - uses: actions/checkout@v4
      - name: Install uv
        uses: astral-sh/setup-uv@v4
        with:
          enable-cache: true
          cache-dependency-glob: "uv.lock"
      - name: Sync dependencies
        run: uv sync --frozen
      - name: ty check
        run: uv run ty check src/codeoracle

  test:
    name: Unit tests + coverage
    runs-on: ubuntu-24.04
    env:
      CODEORACLE_LLM_PROVIDER: mock
    steps:
      - uses: actions/checkout@v4
      - name: Install uv
        uses: astral-sh/setup-uv@v4
        with:
          enable-cache: true
          cache-dependency-glob: "uv.lock"
      - name: Sync dependencies
        run: uv sync --frozen
      - name: pytest + coverage
        run: uv run pytest -m "not live_llm and not integration" --cov-fail-under=85
      - name: Upload coverage to Codecov
        uses: codecov/codecov-action@v4
        with:
          file: .cache/coverage/coverage.xml
```

> **Declared deviation from spec §7.2 — no Postgres service in Plan 1's `test` job (yet).** Spec §7.2's illustrative `test` job stands up a `postgres:18` service container and runs `bash infra/scripts/bootstrap_db.sh` before `pytest`. Plan 1 deliberately omits both, because at Plan-1 time **no integration test, no `db/` layer, and no `bootstrap_db.sh` exists** — Plan 3 creates the DB layer + bootstrap script, and the integration tests that need a live Postgres land in Plans 3+. Plan 1's `test` job therefore runs `pytest -m "not live_llm and not integration"` — unit tests only — so spinning up Postgres would be dead infrastructure gating nothing. **Owner of the deferral: Plan 3** adds the `services.postgres` block + the `bootstrap_db.sh` step to this same `test` job when it ships the first integration tests (see Plan 3's "extends Plan 1's CI workflow" provides-edge). Until then the absence is correct, not a gap. Everything else in this job conforms to §7.2: `ubuntu-24.04`, `setup-uv` with `cache-dependency-glob: "uv.lock"`, `--frozen`, `--cov-fail-under=85`, and `codecov/codecov-action@v4` for PR coverage comments.

- [ ] **Step 3: Validate the YAML parses**

```bash
uv run --with pyyaml python -c "import yaml; yaml.safe_load(open('.github/workflows/ci.yml'))"
```

Expected: no exception. (`pyyaml` is fetched transiently — we don't add it to dev deps because this is a one-shot sanity check, not a runtime dep.)

- [ ] **Step 4: Commit**

```bash
git add .github/workflows/ci.yml
git commit -m "ci: GitHub Actions workflow with lint + type + test jobs (uv-cached)"
```

---

## Task 10: Security CI — deferred to Plan 1A (no `security` job in Plan 1's `ci.yml`)

**Status: intentionally empty — no task work, no commit.** Task numbering is preserved (Tasks 11-14 and the cross-plan citations that anchor on them stay stable); this slot is a deliberate no-op, not a dropped task.

**Why Plan 1 ships no security CI:** Spec §7.1 enumerates **four discrete workflow files** — `ci.yml`, `image-build.yml`, `security.yml`, `lockfile-refresh.yml`. The security gates (gitleaks, semgrep `p/owasp-top-ten` + `p/python` + `p/secrets`, dependency CVE audit, SBOM) belong in the discrete **`security.yml`**, and the per-variant Trivy image scans belong in **`image-build.yml`** — both of which are **owned by Plan 1A (operational-infrastructure.md)**: Plan 1A Task 13 creates `security.yml` (gitleaks full-history + semgrep custom/OWASP + pip-audit + CycloneDX SBOM) and Plan 1A Task 14 creates `image-build.yml` (Trivy ×5 image variants). Plan 1A Task 13 *also* refactors `ci.yml` to remove any security steps and asserts (in `test_workflow_security.py`) that `ci.yml` references neither `gitleaks` nor `semgrep`.

Folding a `security` job into Plan 1's `ci.yml` would therefore (a) **duplicate** Plan 1A's `security.yml`, forcing Plan 1A to immediately rip it back out, and (b) **mislead** — presenting "one CI workflow with a security job" as the intended design when the spec's design is four discrete files. So Plan 1 owns the **lint / type / test / file-length-gate** jobs in `ci.yml` and stops there; security CI is Plan 1A's.

**What Plan 1 *does* ship on the security axis** (and where the rest lives):

| Security concern | Plan 1 (this plan) | Plan 1A (`security.yml` / `image-build.yml`) |
|---|---|---|
| Secret scanning (gitleaks) | pre-commit `gitleaks` hook (Task 8) — local, pre-commit-time | CI `gitleaks` full-history (`fetch-depth: 0`) |
| SAST (semgrep) | — | CI `semgrep` `p/owasp-top-ten` + `p/python` + `p/secrets` + custom rules |
| Dependency CVEs | — | CI dependency audit (Plan 1A uses `pip-audit`) |
| SBOM | — | CI CycloneDX SBOM artifact |
| Image CVEs (Trivy ×5) | — | `image-build.yml` Trivy per-variant |

> **Cross-plan note for the coordinator (osv-scanner vs pip-audit).** Spec §7.1 / §7.4 / §8.5 gate 7 name the dependency-CVE gate as `osv-scanner --lockfile=uv.lock`. Plan 1A's `security.yml` implements that gate as `pip-audit --strict` instead, and does **not** include osv-scanner. That is a Plan-1A-vs-spec divergence to reconcile **in Plan 1A or the spec** (either restore osv-scanner, or amend §7.4/§8.5 gate 7 to name pip-audit). It is flagged here only so removing Plan 1's folded osv-scanner step does not silently drop the spec-named gate from the corpus — Plan 1 is not the owner of that gate.

---

## Task 11: Extend `ci.yml` with the file-length gate + wire it into pre-commit

**What this task delivers:** an enforced 700-line hard ceiling per memory `project-code-quality-bar`. The gate ships as a small bash script (so it's runnable locally too), a CI job that calls it, AND a `pre-commit` hook that fires the same script locally before commit (added here, not in Task 8, because Task 8 runs before the script exists).

**Files:**
- Create: `infra/scripts/check_file_length.py`
- Modify: `.github/workflows/ci.yml` (append `file-length-gate` job)
- Modify: `.pre-commit-config.yaml` (append `file-length-gate` hook to the local block)

- [ ] **Step 1: Create the script**

```bash
mkdir -p infra/scripts
```

Create `infra/scripts/check_file_length.py`:

```python
#!/usr/bin/env python3
"""File-length gate for src/codeoracle/.

Hard ceiling: 700 lines per file — exceed and CI fails.
Soft target: 500 lines per file — exceed and CI warns.

Per docs/superpowers/specs/2026-05-21-platform-scaffolding-design.md §3.4
and memory project-code-quality-bar. Lives at infra/scripts/ so it stays
runnable before `uv sync` has any third-party deps installed — stdlib only.
"""

from __future__ import annotations

import sys
from pathlib import Path

HARD_CEILING = 700
SOFT_TARGET = 500


def _line_count(path: Path) -> int:
    """Count newline-terminated lines, matching shell `wc -l` semantics."""
    return path.read_bytes().count(b"\n")


def main(argv: list[str]) -> int:
    src_dir = Path(argv[1]) if len(argv) > 1 else Path("src/codeoracle")
    if not src_dir.is_dir():
        print(f"::error::Source directory not found: {src_dir}", file=sys.stderr)
        return 2

    over_ceiling: list[tuple[int, Path]] = []
    over_target: list[tuple[int, Path]] = []

    for py in sorted(src_dir.rglob("*.py")):
        if "__pycache__" in py.parts:
            continue
        n = _line_count(py)
        if n > HARD_CEILING:
            over_ceiling.append((n, py))
        elif n > SOFT_TARGET:
            over_target.append((n, py))

    if over_ceiling:
        print(f"::error::Files exceeding {HARD_CEILING}-line hard ceiling (split required):")
        for n, p in over_ceiling:
            print(f"  {n} {p}")
        print()
        print(
            f"Above {HARD_CEILING} lines -> split into modules following the layer pattern\n"
            f'(see CLAUDE.md "Platform code conventions" + memory strict-layer-purity).'
        )
        return 1

    if over_target:
        print(f"::warning::Files exceeding {SOFT_TARGET}-line soft target (still under hard ceiling):")
        for n, p in over_target:
            print(f"  {n} {p}")

    print(f"File-length gate passed: no files exceed {HARD_CEILING}-line ceiling.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
```

The script uses stdlib only, so it runs even before `uv sync` is invoked (which Plan 2's `bootstrap_db.sh` and CI's earliest steps rely on). Invocation throughout the plan is `uv run python infra/scripts/check_file_length.py` for consistency with the Astral toolchain — direct `python3 infra/scripts/check_file_length.py` works identically when no `.venv` is needed.

- [ ] **Step 2: Run the script locally — expect green**

```bash
uv run python infra/scripts/check_file_length.py
```

Expected: `File-length gate passed: no files exceed 700-line ceiling.`

- [ ] **Step 3: Smoke-test that the script catches a violation**

```bash
yes "x = 1" | head -n 701 > src/codeoracle/core/_length_smoke.py
uv run python infra/scripts/check_file_length.py || echo "gate correctly failed"
rm src/codeoracle/core/_length_smoke.py
```

Expected output includes `Files exceeding 700-line hard ceiling` and exit-on-failure prints `gate correctly failed`.

- [ ] **Step 4: Append the `file-length-gate` job to `.github/workflows/ci.yml`**

Add after the `test:` block (Plan 1's `ci.yml` has no `security:` job — security CI is Plan 1A's `security.yml`):

```yaml
  file-length-gate:
    name: File-length gate (700-line hard ceiling)
    runs-on: ubuntu-24.04
    steps:
      - uses: actions/checkout@v4
      - name: Run file-length gate
        run: uv run python infra/scripts/check_file_length.py
```

- [ ] **Step 5: Validate the workflow YAML parses**

```bash
uv run --with pyyaml python -c "import yaml; yaml.safe_load(open('.github/workflows/ci.yml'))"
```

- [ ] **Step 6: Append the `file-length-gate` hook to `.pre-commit-config.yaml`**

Edit `.pre-commit-config.yaml` so the `repos: - repo: local` block looks like:

```yaml
  - repo: local
    hooks:
      - id: ty
        name: ty (type check, whole src/codeoracle tree)
        entry: uv run ty check src/codeoracle
        language: system
        pass_filenames: false
        types: [python]
      - id: file-length-gate
        name: file-length gate (700-line hard ceiling)
        entry: uv run python infra/scripts/check_file_length.py
        language: system
        pass_filenames: false
        always_run: true
```

- [ ] **Step 7: Validate the pre-commit YAML parses**

```bash
uv run --with pyyaml python -c "import yaml; yaml.safe_load(open('.pre-commit-config.yaml'))"
```

- [ ] **Step 8: Run the new hook locally — expect green**

```bash
uv run pre-commit run file-length-gate --all-files
```

Expected: `File-length gate passed: no files exceed 700-line ceiling.` exit 0.

- [ ] **Step 9: Smoke-test the hook catches a violation**

```bash
yes "x = 1" | head -n 701 > src/codeoracle/core/_length_smoke.py
uv run pre-commit run file-length-gate --all-files || echo "hook correctly failed"
rm src/codeoracle/core/_length_smoke.py
```

Expected: hook fails, output includes `Files exceeding 700-line hard ceiling`, and `hook correctly failed` prints.

- [ ] **Step 10: Commit**

```bash
git add infra/scripts/check_file_length.py .github/workflows/ci.yml .pre-commit-config.yaml
git commit -m "ci: file-length gate enforces 700-line hard ceiling (500-line soft warning) — CI + pre-commit"
```

---

## Task 12: Renovate config (grouped dep bumps)

**What this task delivers:** `.github/renovate.json5` configured so Astral toolchain (ruff/ty/uv) bumps land as one grouped PR per week, pytest ecosystem bumps as another, GitHub Actions bumps as another, and major bumps require dashboard approval. Lives at `.github/renovate.json5` (not repo root) because (a) GitHub's auto-discovery convention prefers `.github/`, and (b) the `.json5` format lets us add comments to the rules over time without breaking the parser.

**Files:**
- Create: `.github/renovate.json5`

- [ ] **Step 1: Create `.github/renovate.json5`**

```json5
{
  "$schema": "https://docs.renovatebot.com/renovate-schema.json",
  "extends": [
    "config:recommended",
    ":semanticCommits",
    ":dependencyDashboard"
  ],
  "rangeStrategy": "pin",
  "schedule": ["before 4am on Monday"],
  "lockFileMaintenance": {
    "enabled": true,
    "schedule": ["before 4am on Monday"]
  },
  "vulnerabilityAlerts": {
    "labels": ["security"],
    "schedule": ["at any time"]
  },
  "packageRules": [
    {
      "description": "Astral toolchain (ruff, ty, uv) — bump as one PR",
      "matchPackageNames": ["ruff", "ty", "uv"],
      "groupName": "astral toolchain"
    },
    {
      "description": "pytest ecosystem",
      "matchPackageNames": ["pytest", "pytest-*"],
      "groupName": "pytest ecosystem"
    },
    {
      "description": "GitHub Actions",
      "matchManagers": ["github-actions"],
      "groupName": "github actions"
    },
    {
      "description": "Pre-commit hooks",
      "matchManagers": ["pre-commit"],
      "groupName": "pre-commit hooks"
    },
    {
      "description": "Majors require explicit approval to keep weekly noise down",
      "matchUpdateTypes": ["major"],
      "dependencyDashboardApproval": true
    }
  ]
}
```

The body is valid JSON (a strict subset of JSON5), so any later edit that adds JSON5 `//` line comments stays parseable; the `.json5` extension exists to allow that future without a second rename.

- [ ] **Step 2: Validate JSON5 parses**

```bash
uv run --with json5 python -c "import json5; json5.load(open('.github/renovate.json5'))"
```

Expected: no exception. The `--with json5` flag pulls the parser into a transient venv just for this check — `json5` does not need to land in `pyproject.toml`.

- [ ] **Step 3: Commit**

```bash
mkdir -p .github
git add .github/renovate.json5
git commit -m "ci: Renovate config (.github/renovate.json5) with grouped dep bumps (astral, pytest, gh-actions) + major approval gate"
```

---

## Task 13: README + `.env.example`

**What this task delivers:** a `README.md` that gives a new contributor everything they need to run the project locally; an `.env.example` documenting every env var the platform reads (per spec §7.9 secret handling).

**Files:**
- Create: `README.md`
- Create: `.env.example`

- [ ] **Step 1: Create `.env.example`**

```bash
# CodeOracle — environment variables.
# Copy to .env (gitignored). Never commit .env.

# --- LLM provider ---
# Selects the concrete implementation behind codeoracle.adapters.llm.
# Values: openai | anthropic | vllm | ollama | mock
CODEORACLE_LLM_PROVIDER=openai

# OpenAI (default dev provider per memory project-dev-llm-provider-openai)
CODEORACLE_LLM_OPENAI__API_KEY=
CODEORACLE_LLM_OPENAI__MODEL=gpt-4o-mini
CODEORACLE_LLM_OPENAI__BASE_URL=

# Anthropic
CODEORACLE_LLM_ANTHROPIC__API_KEY=
CODEORACLE_LLM_ANTHROPIC__MODEL=claude-opus-4-8

# vLLM (production default — fill in the concrete model id when wiring the prod stack)
CODEORACLE_LLM_VLLM__BASE_URL=http://vllm:8000
CODEORACLE_LLM_VLLM__MODEL=

# Ollama (fill in concrete model id)
CODEORACLE_LLM_OLLAMA__BASE_URL=http://ollama:11434
CODEORACLE_LLM_OLLAMA__MODEL=

# Mock provider (test-only — points at a fixtures directory)
CODEORACLE_LLM_MOCK__SCRIPTS_DIR=tests/fixtures/llm_scripts
CODEORACLE_LLM_MOCK__STRICT=true

# --- (Later plans) Database, Valkey, Tempo, etc. ---
# Documented here once Plan 3 lands. Keep this file as the canonical env-var
# catalog — no env var should be read by the platform without an entry here.
```

- [ ] **Step 2: Create `README.md`**

````markdown
# CodeOracle

> Evidence-first agentic application intelligence platform — the LLM should explain; the platform should prove.

This repo houses both the orchestrator state for the audit work (`docs/`, `.swarm/`) and the platform code itself (`src/codeoracle/`). For the platform's design see `docs/superpowers/specs/2026-05-21-platform-scaffolding-design.md`.

## Local development (platform code)

### Prerequisites

- Python 3.14 (`.python-version` pins it)
- [`uv`](https://docs.astral.sh/uv/) 0.5+ — package + env management
- Docker + Docker Compose (used in Plan 3 onwards for PG18 / AGE / Valkey / vLLM)
- A copy of `.env.example` saved as `.env` with values filled in

### First-time setup

```bash
uv sync                       # install runtime + dev deps from uv.lock
uv run pre-commit install     # wire pre-commit hooks (ruff, gitleaks, ty)
cp .env.example .env          # then fill in API keys
```

### Day-to-day commands

| Command | What it does |
|---|---|
| `uv run ruff check` | Lint (E, W, F, I, B, N, UP, SIM, RUF, TID, PT, S, ASYNC) |
| `uv run ruff format` | Auto-format |
| `uv run ty check src/codeoracle` | Strict type check |
| `uv run pytest` | Run unit tests (excludes `live_llm` + `integration` markers) |
| `uv run pytest -m integration` | Integration tests (require Docker stack — Plan 3+) |
| `uv run pytest -m live_llm` | Live-LLM tests (require real `OPENAI_API_KEY`) |
| `uv run pytest --cov=codeoracle --cov-report=term-missing` | Tests + coverage |
| `uv run python infra/scripts/check_file_length.py` | File-length gate (700-line ceiling) |
| `uv run pre-commit run --all-files` | Run every pre-commit hook against the whole tree |

### Architectural ground rules

- **Layered**: `models/` + `core/` ← `db/` + `adapters/` ← `services/` ← processes (`cli/`, `api/`, `mcp/`, `agent/`, `workers/`). A class's directory reveals its role; mixing layers in a file is "slop" (see memory `strict-layer-purity`).
- **LLM-agnostic**: every LLM call goes through `codeoracle.adapters.llm`. Direct provider imports (`import anthropic|openai|litellm|ollama`) outside `src/codeoracle/adapters/llm/providers/` are blocked by ruff `TID251`.
- **File length**: 500-line soft target, **700-line hard ceiling** (CI-enforced). Above 700, split.
- **OOP-first**: services, adapters, factories are classes with constructor-injected deps. Free functions only for pure transformations.

See `CLAUDE.md` (project root) for the full set of platform-code conventions.

## CI

`.github/workflows/ci.yml` runs four parallel jobs on every PR + push to `main`:

1. **lint** — `ruff check` + `ruff format --check`
2. **type** — `ty check src/codeoracle`
3. **test** — `pytest -m "not live_llm and not integration" --cov-fail-under=85`
4. **file-length-gate** — 700-line hard ceiling on `src/codeoracle/`

Security and image-scan CI live in dedicated workflows (per spec §7.1): `.github/workflows/security.yml` (gitleaks full-history, Semgrep SAST with `p/owasp-top-ten`, dependency-CVE audit, CycloneDX SBOM) and `.github/workflows/image-build.yml` (Trivy scan per image variant). Locally, the pre-commit `gitleaks` hook catches secrets before they are committed.

A separate `test-live-llm` job (added in Plan 4) is label-gated by `run-live-llm` and runs against real OpenAI.

## Secret handling

See `docs/superpowers/specs/2026-05-21-platform-scaffolding-design.md` §7.9 for the canonical secret-handling matrix (Dev / CI default / CI live-llm gate / Prod). Plan 1 implements the Dev tier (`.env.example` + `.gitignore` + `.dockerignore`) plus the local pre-commit `gitleaks` hook, and the CI-default posture of injecting **no** provider secrets (the default `test` job sets `CODEORACLE_LLM_PROVIDER=mock`). The CI **full-history** gitleaks scan is Plan 1A's `security.yml` (`fetch-depth: 0`). Live-llm and Prod tiers land in Plan 4 and Phase 7+ respectively.

## License

Apache-2.0.
````

- [ ] **Step 3: Commit**

```bash
git add README.md .env.example
git commit -m "docs: README with developer commands + .env.example as env-var catalog"
```

---

## Task 14: Final verification + push the plan

**What this task delivers:** confirmation every gate works against the just-built skeleton (local pre-commit, full ruff, ty, pytest, file-length), then a single `git push origin main` that lands the whole plan's commits and triggers CI.

**Files:** none modified — verification only.

- [ ] **Step 1: Run the full pre-commit suite against everything**

```bash
uv run pre-commit run --all-files
```

Expected: every hook passes. If any rewrites a file (formatters do this), stage and amend... no wait — we do NOT amend per CLAUDE.md. Instead: stage the rewrite, create a new commit:

```bash
git add -u
git commit -m "chore: pre-commit auto-fixes against the full tree"
```

If pre-commit passed cleanly the first time, no extra commit is needed.

- [ ] **Step 2: Run every CI gate locally to mirror what GitHub will do**

```bash
uv run ruff check
uv run ruff format --check
uv run ty check src/codeoracle
uv run pytest -m "not live_llm and not integration" --cov=codeoracle --cov-report=term-missing --cov-fail-under=85
uv run python infra/scripts/check_file_length.py
```

Expected: every command exits 0. Coverage report shows `src/codeoracle/core/version.py` exercised.

- [ ] **Step 3: Confirm git log shape**

```bash
git log --oneline -20
```

Expected: ~12-13 commits this plan added (Task 10 is a no-op deferral and adds none), each with a clear conventional-commit-style subject line.

- [ ] **Step 4: Push**

```bash
git push origin main
```

Expected: push succeeds. GitHub will start running CI within ~30 seconds.

- [ ] **Step 5: Watch CI green on origin**

```bash
gh run watch
```

Or, if `gh` isn't available:

```bash
gh run list --limit 1
```

Expected: all four `ci.yml` jobs (lint, type, test, file-length-gate) finish green within ~5 minutes. If any job fails, investigate before declaring Plan 1 done — a red CI on Plan 1 means a discipline gate has a config bug, and every later plan inherits the bug. (Plan 1A's `security.yml` + `image-build.yml` run independently once that plan lands; they are not part of Plan 1's `ci.yml`.)

- [ ] **Step 6: Final task commit (only if anything changed during verification — usually nothing)**

If steps 1-5 all passed without modification, there's nothing to commit. The plan is done.

---

## Definition of Done — Plan 1

This plan is complete when ALL of the following are true on `main` (origin):

- [ ] `uv sync` produces a working venv with no errors
- [ ] `src/codeoracle/{models,core,db,adapters,services,cli,api,mcp,agent,workers}` all exist as Python packages, and `src/codeoracle/cli/__main__.py` exposes the `python -m codeoracle` entry-point Typer app (Plan 5 Task 14 registers the first subcommand)
- [ ] `pyproject.toml` is the single config home for ruff, ty, pytest, coverage
- [ ] Ruff's `banned-api` correctly blocks `import anthropic|openai|litellm|ollama` outside `src/codeoracle/adapters/llm/providers/`
- [ ] `uv run ty check src/codeoracle` passes
- [ ] `uv run pytest` passes with ≥85% coverage on the (tiny) existing surface
- [ ] `.pre-commit-config.yaml` installed and green on `pre-commit run --all-files`
- [ ] `.github/workflows/ci.yml` exists with four jobs (lint, type, test, file-length-gate) — security + image-scan CI are Plan 1A's `security.yml` + `image-build.yml`, not `ci.yml`
- [ ] CI run on `main` is green
- [ ] `.github/renovate.json5` is committed (Renovate activation against the GitHub repo is a one-time admin action outside this plan)
- [ ] `README.md` documents the developer commands
- [ ] `.env.example` exists with every env var the platform will read

When all boxes are checked, the foundation is set: every subsequent commit (Plans 2-7) gets linted, type-checked, length-gated, and (via the local pre-commit gitleaks hook) secret-scanned automatically — and once Plan 1A lands its `security.yml` + `image-build.yml`, also SAST-scanned, CVE-scanned, and image-scanned in CI. That's the "ratchet that can't slip" the spec promises.
