"""Tests for codeoracle.core.telemetry."""

from collections.abc import Iterator

import pytest
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
    InMemorySpanExporter,
)

from codeoracle.core.telemetry import (
    TelemetryConfig,
    _reset_for_testing,
    get_tracer,
    init_telemetry,
)


@pytest.fixture(autouse=True)
def _reset_telemetry() -> Iterator[None]:
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
        attributes = spans[0].attributes
        assert attributes is not None
        assert attributes["test.key"] == "value"


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
