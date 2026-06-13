```text
   ______          __     ____                 __
  / ____/___  ____/ /__  / __ \________ ______/ /__
 / /   / __ \/ __  / _ \/ / / / ___/ __ `/ ___/ / _ \
/ /___/ /_/ / /_/ /  __/ /_/ / /  / /_/ / /__/ /  __/
\____/\____/\__,_/\___/\____/_/   \__,_/\___/_/\___/
```

# CodeOracle

![Python](https://img.shields.io/badge/python-3.14%2B-3776AB?logo=python&logoColor=white)
![Version](https://img.shields.io/badge/version-0.1.0-blue)
![Package Manager](https://img.shields.io/badge/package%20manager-uv-261230)
![Conventional Commits](https://img.shields.io/badge/Conventional%20Commits-1.0.0-yellow)
![License](https://img.shields.io/badge/license-MIT-green)
![Author](https://img.shields.io/badge/author-Sawmon%20Abo-blue)

CodeOracle is an evidence-first codebase intelligence platform for source-grounded answers about software
systems. It indexes repository snapshots into graph and vector stores, routes extraction through worker streams,
and uses an agent to compose answers only after retrievable evidence has been collected and verified. The current
implementation is a Python 3.14 self-corpus vertical slice centered on `explain_symbol`, with broader orchestration
work documented under `docs/superpowers/`.

## Table Of Contents

- [Overview](#overview)
- [Features](#features)
- [Architecture](#architecture)
- [Repository Structure](#repository-structure)
- [Installation](#installation)
- [Usage](#usage)
- [Development](#development)
- [Testing](#testing)
- [CI/CD And Releases](#cicd-and-releases)
- [Conventional Commits](#conventional-commits)
- [Maintainers](#maintainers)
- [License](#license)

## Overview

CodeOracle exists to make codebase answers auditable. Instead of treating an LLM response as the source of truth,
the platform builds a snapshot-backed evidence layer from source code, static analysis output, embeddings, traces,
and citation resolvers. The agent can then answer a supported query, attach platform-computed trust, and classify
the result as `PROVEN`, `UNKNOWN`, or `REFUTED`.

The project vision is a disciplined codebase investigator: retrieve real implementation evidence, preserve
provenance, expose uncertainty, and make another engineer or agent able to verify the answer later. The implemented
runtime is focused on the `self` corpus and the `explain_symbol` query type; design notes for future primitive
orchestration are present but are not the current public API.

## Features

- FastAPI service with health, metrics, query, and asynchronous reindex endpoints.
- MCP server exposing `explain_symbol` and `reindex` tools over stdio or streamable HTTP.
- LangGraph agent flow for intent validation, graph lookup, embedding retrieval, LLM composition, trust scoring,
  and verification.
- Snapshot indexing through Valkey streams and workers for Joern, SCIP, OpenGrep, embeddings, and trace ingestion.
- PostgreSQL persistence with Apache AGE graph storage, pgvector embeddings, SQLAlchemy repositories, and Alembic
  migrations.
- Source evidence resolution through Sourcebot, graph query replay, and citation verification.
- Docker Compose infrastructure for Postgres AGE, Valkey, Sourcebot, Tempo, OpenTelemetry Collector, Prometheus,
  Grafana, API, MCP, agent, and workers.
- CI definitions for linting, formatting, type checking, tests, security scanning, image builds, slice acceptance,
  dependency refreshes, and live LLM checks.

## Architecture

CodeOracle is organized as a set of service entrypoints around a shared evidence and persistence core.

```text
Repository snapshot
  -> reindex request
  -> Valkey stream fan-out
  -> Joern / SCIP / OpenGrep / embedding / trace workers
  -> Postgres AGE + pgvector + relational tables
  -> API, MCP, or agent query
  -> LangGraph retrieval and composition
  -> citation resolution + verifier verdict
```

The API layer accepts HTTP requests and delegates supported questions to the agent service. The MCP layer exposes
the same core capabilities as tools. The agent layer runs the `explain_symbol` graph: it validates a dotted fully
qualified symbol, finds an applied snapshot, queries the graph, retrieves nearby embedding chunks, asks an LLM
provider for structured explanation output, scores evidence with the trust matrix, and verifies citations before
returning the final result.

Indexing is asynchronous. `POST /v1/reindex` creates a snapshot record and enqueues work. The reindex orchestrator
computes deltas, fans out extractor jobs by file or project, waits for worker outcomes, links cross-file symbols,
and marks the snapshot `APPLIED` or `FAILED`.

Configuration is provided through Pydantic settings with `CODEORACLE_` environment variables. Runtime state lives
in PostgreSQL, Apache AGE, pgvector, Valkey streams, and optional tracing/metrics services.

## Repository Structure

| Path | Purpose |
| --- | --- |
| `src/codeoracle/api/` | FastAPI app, routes, lifespan wiring, metrics, and HTTP schemas. |
| `src/codeoracle/agent/` | LangGraph query workflow, agent API, dependency wiring, and prompts. |
| `src/codeoracle/mcp/` | MCP server and stdio/HTTP transports. |
| `src/codeoracle/workers/` | Valkey stream consumers for indexing, embeddings, OpenGrep, SCIP, Joern, and traces. |
| `src/codeoracle/adapters/` | Integrations for LLMs, embeddings, Sourcebot, Tempo, Joern, SCIP, OpenGrep, and Valkey. |
| `src/codeoracle/db/` | SQLAlchemy models, repositories, migrations, graph and vector persistence. |
| `src/codeoracle/services/` | Indexing, verification, handle resolution, context budgets, trust, and telemetry. |
| `src/codeoracle/models/` | Query, evidence, snapshot, and trace domain models. |
| `infra/` | Dockerfiles, Compose stacks, image pins, bootstrap scripts, security rules, and operational docs. |
| `.github/workflows/` | CI, security, image build, release, dependency refresh, slice, and live LLM workflows. |
| `tests/` | Unit, integration, slice, E2E, live-provider, infrastructure, and workflow tests. |
| `docs/superpowers/` | Platform design, implementation plans, orchestration notes, and audit history. |

## Installation

Prerequisites:

- Python `3.14+`
- `uv`
- Docker and Docker Compose for the dependency stack
- `psql` for database bootstrap scripts
- Optional extractor/provider tools for non-container local runs, including Joern, SCIP, OpenGrep, and live LLM or
  embedding credentials

Bootstrap the local environment:

```bash
make bootstrap
cp .env.example .env
```

Edit `.env` with the required `CODEORACLE_` settings, especially database credentials and any live provider keys.
The repository includes defaults for the Compose-based stack in `infra/config/defaults.env` and digest-pinned
infrastructure versions in `infra/versions/pinned.env`.

Start the dependency stack and initialize the database:

```bash
make compose-up
bash infra/scripts/bootstrap_db.sh
uv run alembic upgrade head
```

For the full application stack used by slice acceptance:

```bash
docker compose --env-file infra/versions/pinned.env \
  -f infra/compose/docker-compose.yml \
  -f infra/compose/docker-compose.e2e.yml \
  up -d --build
```

## Usage

The reliable current runtime surfaces are the API, MCP server, agent service, workers, and Compose stack. Package
metadata also declares console scripts, and a CLI index command implementation exists under
`src/codeoracle/cli/commands/index.py`, but that command is not registered on the root Typer app in this checkout.

Run services directly during development:

```bash
uv run python -m codeoracle.api
uv run python -m codeoracle.agent
uv run python -m codeoracle.mcp --http
uv run python -m codeoracle.workers.reindex_orchestrator
uv run python -m codeoracle.workers.joern_indexer
uv run python -m codeoracle.workers.scip_indexer
uv run python -m codeoracle.workers.opengrep_runner
uv run python -m codeoracle.workers.embed_indexer
uv run python -m codeoracle.workers.traces_ingest
```

Basic API flow:

```bash
curl http://127.0.0.1:8080/v1/health

curl -X POST http://127.0.0.1:8080/v1/reindex \
  -H 'content-type: application/json' \
  -d '{"base_sha": null}'

curl -X POST http://127.0.0.1:8080/v1/query \
  -H 'content-type: application/json' \
  -d '{
    "type": "explain_symbol",
    "symbol": "codeoracle.services.verifier.service.VerifierService.verify",
    "corpus": "self"
  }'
```

Queries require an `APPLIED` snapshot and currently accept safe dotted fully qualified symbols for the `self` corpus.

## Development

Install dependencies and hooks:

```bash
uv sync --frozen
uv run pre-commit install --install-hooks
```

Common development commands:

```bash
make format
make lint
make type-check
make test
make test-unit
make test-integration
make compose-logs
make compose-down
```

Configuration is environment-driven. See `.env.example`, `src/codeoracle/core/config.py`, and
`infra/config/defaults.env` for the supported `CODEORACLE_` settings.

Contributions should keep changes source-grounded, preserve the evidence and verifier model, and run the relevant
lint, type, and test targets before review.

## Testing

Tests use `pytest` with unit, integration, slice, E2E, worker, trace, benchmark, and live-provider markers. Local
coverage output is written under `.cache/coverage`; CI enforces an 85% coverage threshold for its main pytest job.

```bash
make test
uv run pytest tests/unit -v
uv run pytest tests/integration -v -m integration
uv run pytest tests/integration/slice/ -v -m slice
```

Live tests are opt-in and require provider credentials plus explicit flags, for example:

```bash
CODEORACLE_LLM_OPENAI__API_KEY=... \
uv run pytest -m live_llm tests/integration/llm/ -v --run-live-llm
```

## CI/CD And Releases

GitHub Actions workflows are defined for:

- `ci.yml`: Ruff lint/format checks, `ty` type checking, pytest with coverage, file-length guardrails, and
  pinned-environment verification.
- `security.yml`: Gitleaks, Semgrep, OSV scanning, and CycloneDX SBOM generation.
- `image-build.yml`: Docker image builds and Trivy scans for API, worker, CLI, MCP, agent, and model-server variants.
- `slice-acceptance.yml`: full-stack Compose acceptance for the current `explain_symbol` slice.
- `test-live-llm.yml`: advisory live LLM tests gated by labels, schedules, manual dispatch, and secrets.
- `lockfile-refresh.yml`: scheduled `uv lock --upgrade` pull requests.
- `release.yml`: Release Please automation for changelog, tag, and version updates.

Versioning is coordinated across `pyproject.toml`, `src/codeoracle/core/version.py`, Commitizen, and Release Please.
The current package version is `0.1.0`. No package registry publish step is configured in the repository workflows
reviewed here.

## Conventional Commits

Conventional Commits are configured through Commitizen, installed as a `commit-msg` pre-commit hook, used by
Renovate semantic commits, and consumed by Release Please. They are locally enforced when repository hooks are
installed; this checkout does not show a separate server-side enforcement policy.

## Maintainers

Package metadata declares the author as **Sawmon Abo**. `CODEOWNERS` currently contains placeholder teams for
maintainers and security owners, so no additional active maintainers are declared in repository metadata.

## License

Package metadata declares the project license as **MIT**. No standalone `LICENSE` file is currently present in the
repository.
