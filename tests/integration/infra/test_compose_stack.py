"""Integration tests: docker compose stack health.

These tests REQUIRE the compose stack to be running. CI runs:

    docker compose -f infra/compose/docker-compose.yml up -d \
        otel-collector tempo prometheus
    uv run pytest tests/integration/infra/test_compose_stack.py -v
    docker compose -f infra/compose/docker-compose.yml down -v

Locally, `make compose-up` does the same.
"""

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
