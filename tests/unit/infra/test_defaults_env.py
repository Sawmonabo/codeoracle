"""Tests for infra/config/defaults.env."""

from pathlib import Path

import pytest

DEFAULTS_ENV = (
    Path(__file__).resolve().parents[3] / "infra" / "config" / "defaults.env"
)


REQUIRED_KEYS = {
    # OTel telemetry (consumed by core/telemetry.py)
    "CODEORACLE_OTEL_ENDPOINT",
    "CODEORACLE_OTEL_ENABLED",
    # LLM provider abstraction (consumed by adapters/llm/factory.py — Plan 4)
    "CODEORACLE_LLM_PROVIDER",
    "CODEORACLE_LLM_OPENAI__MODEL",
    "CODEORACLE_EMBEDDING__MODEL",
    "CODEORACLE_EMBEDDING__DIM",
    # Database (consumed by db/session.py — Plan 3)
    "CODEORACLE_DB__HOST",
    "CODEORACLE_DB__PORT",
    "CODEORACLE_DB__DATABASE",
    "CODEORACLE_DB__USER",
    # Trace store (consumed by adapters/tempo — Plan 7)
    "CODEORACLE_TRACES__TEMPO_URL",
    # Sourcebot handle resolution (consumed by adapters/sourcebot — Plan 8)
    "CODEORACLE_SOURCEBOT__BASE_URL",
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
    assert parsed["CODEORACLE_EMBEDDING__DIM"] == "1536"


def test_defaults_env_llm_provider_is_openai() -> None:
    parsed = _parse_env(DEFAULTS_ENV)
    assert parsed["CODEORACLE_LLM_PROVIDER"] == "mock"


def test_defaults_env_otel_endpoint_matches_collector() -> None:
    parsed = _parse_env(DEFAULTS_ENV)
    # otel-collector exposes OTLP gRPC on 4317 (Task 4 of this plan)
    assert parsed["CODEORACLE_OTEL_ENDPOINT"] == "http://otel-collector:4317"
