# CodeOracle — top-level Makefile.
#
# Thin wrapper around uv, docker compose, docker. Does NOT re-implement
# logic that already lives in infra/scripts/. If you want to know what
# `make bootstrap` does, read infra/scripts/bootstrap_dev.sh.

.PHONY: help bootstrap test test-unit test-integration lint format type-check \
        compose-up compose-down compose-logs docker-build trivy-scan seed-corpus clean

COMPOSE := docker compose -f infra/compose/docker-compose.yml
PYTHON_VERSION ?= 3.14-slim-bookworm

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
		docker build --build-arg PYTHON_VERSION=$(PYTHON_VERSION) --build-arg VARIANT=$$v -t codeoracle:$$v -f infra/docker/Dockerfile .; \
	done
else
	@docker build --build-arg PYTHON_VERSION=$(PYTHON_VERSION) --build-arg VARIANT=$(VARIANT) -t codeoracle:$(VARIANT) -f infra/docker/Dockerfile .
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
	@rm -rf .cache
	@rm -rf dist build *.egg-info
