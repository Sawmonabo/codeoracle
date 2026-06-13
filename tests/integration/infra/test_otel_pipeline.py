"""End-to-end test: Python span → otel-collector → Tempo → query.

This is the canonical 'observability works' gate referenced by spec
§8.5 gate 9. It runs against a live compose stack:

    make compose-up
    uv run pytest tests/integration/infra/test_otel_pipeline.py -v -m
    integration make compose-down
"""

import time
import uuid
from collections.abc import Iterator

import httpx
import pytest

from codeoracle.core.telemetry import (
    TelemetryConfig,
    _reset_for_testing,
    get_tracer,
    init_telemetry,
)


@pytest.fixture
def _telemetry_to_collector() -> Iterator[None]:
    _reset_for_testing()
    init_telemetry(
        TelemetryConfig(endpoint="http://localhost:4317", enabled=True)
    )
    yield
    _reset_for_testing()


@pytest.mark.integration
@pytest.mark.usefixtures("_telemetry_to_collector")
def test_span_flows_end_to_end() -> None:
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
                assert any(
                    t.get("rootServiceName") == "codeoracle" for t in traces
                ), (
                    "Tempo returned traces but none with "
                    f"service.name=codeoracle: {traces}"
                )
                return
        except Exception as exc:
            last_error = exc
        time.sleep(2.0)

    pytest.fail(
        "Tempo did not return span tagged "
        f"test.run_id={span_name} within 60s. "
        f"Last error: {last_error}"
    )
