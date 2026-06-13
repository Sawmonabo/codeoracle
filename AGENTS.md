# Repository Guidelines

## Project Structure & Module Organization

Source lives under `src/codeoracle/`. Key areas are `api/` for FastAPI routes,
`agent/` for the LangGraph query flow, `mcp/` for MCP transports, `workers/` for
Valkey consumers, `adapters/` for external systems, `db/` for SQLAlchemy and
Alembic, `services/` for orchestration, and `models/` for domain contracts.
Tests live in `tests/` across unit, integration, slice, E2E, worker, trace, and
live-provider suites. Infrastructure lives in `infra/`; design material lives in
`docs/superpowers/`.

## Build, Test, and Development Commands

- `make bootstrap`: install `uv` dependencies and pre-commit hooks for a fresh
  checkout.
- `uv sync --frozen`: sync the locked Python 3.14 environment from `uv.lock`.
- `make compose-up` / `make compose-down`: start or stop the dependency stack.
- `bash infra/scripts/bootstrap_db.sh && uv run alembic upgrade head`: initialize
  database extensions and migrations.
- `make lint`: run Ruff linting and format checks.
- `make type-check`: run `ty check src tests`.
- `make test`, `make test-unit`, `make test-integration`: run pytest suites.
- `make docker-build VARIANT=api` or `make docker-build ALL=1`: build image
  variants.

## Coding Style & Naming Conventions

Use Python 3.14, four-space indentation, LF line endings, Ruff formatting,
79-character Python lines, and double quotes. Keep imports first-party under
`codeoracle`. Runtime code should use adapters such as
`codeoracle.adapters.llm` and `codeoracle.adapters.valkey`; direct provider SDK
imports are banned outside adapters. Configuration should flow through
`CODEORACLE_` environment variables.

## Testing Guidelines

Pytest discovers `test_*.py` files under `tests/`. Use markers declared in
`pyproject.toml`, such as `unit`, `integration`, `slice`, `e2e`, `worker`,
`traces`, and `live_llm`. Live-provider tests are opt-in and require flags such
as `--run-live-llm`. CI enforces coverage for its main test job; new behavior
should include focused tests near the changed layer and prefer mock providers.

## Type And Lint Failure Policy

Never hide type failures by adding ignore-list entries, narrowing hook scope, or
adding inline suppressions such as `# type: ignore`. Fix the code, tests, or
types directly so `uv run ty check src tests` remains meaningful. If the correct
fix is unclear, consult the current official documentation or authoritative
project docs for the tool or library, then apply the modern recommended pattern.

## Commit & Pull Request Guidelines

Repository tooling configures Conventional Commits through Commitizen and a
`commit-msg` hook. Use messages like `feat(api): add query validation` or
`fix(workers): retry failed stream jobs`. Before opening a PR, run relevant
lint, type, and test commands, describe scope and risk, link issues, and call
out migrations, env vars, workflow changes, or live provider requirements.

## Security & Configuration Tips

Start from `.env.example` and avoid committing secrets. Pre-commit includes
Gitleaks, `uv-lock`, and vulnerability checks; GitHub Actions runs Semgrep,
OSV scanning, and SBOM generation. For local services, prefer the Compose
stack and pinned image versions in `infra/versions/pinned.env`.
