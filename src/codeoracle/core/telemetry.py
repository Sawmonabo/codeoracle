"""OpenTelemetry SDK wiring for CodeOracle.

Provides `get_tracer(__name__)` for emitting spans across the platform.
Designed to be safe to import from any layer (no service-layer or
db-layer dependencies). The actual OTLP exporter is wired to the
otel-collector defined in `infra/observability/otel-collector.yaml`.

Service-layer code emitting a span:

    from codeoracle.core.telemetry import get_tracer
    tracer = get_tracer(__name__)
    with tracer.start_as_current_span("verifier.verify") as span:
        span.set_attribute("claim.id", claim.id) ...

Process-entry code (api/main.py, worker startup, CLI entry) calls
`init_telemetry()` once at startup. Subsequent calls are no-ops.
"""

from dataclasses import dataclass

from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import (
    OTLPSpanExporter,
)
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class TelemetryConfig(BaseSettings):
    """Telemetry configuration sourced from environment."""

    model_config = SettingsConfigDict(
        env_prefix="CODEORACLE_OTEL_",
        env_file=".env",
        extra="ignore",
    )

    endpoint: str = Field(
        default="http://localhost:4317",
        description="OTLP gRPC endpoint for the otel-collector.",
    )
    service_name: str = Field(
        default="codeoracle",
        min_length=1,
        description="OpenTelemetry service.name resource attribute.",
    )
    service_version: str = Field(
        default="0.1.0",
        min_length=1,
        description="OpenTelemetry service.version resource attribute.",
    )
    enabled: bool = Field(
        default=True,
        description=(
            "When False, the SDK uses a no-op TracerProvider — used in "
            "tests and in environments without an otel-collector."
        ),
    )


class TelemetryNotInitializedError(RuntimeError):
    """Raised when telemetry is used before init_telemetry() in strict mode."""


@dataclass
class _TelemetryState:
    initialized: bool = False


_STATE = _TelemetryState()


def init_telemetry(config: TelemetryConfig | None = None) -> None:
    """Wire up the global TracerProvider. Idempotent.

    Call once at process entry. Multiple calls are no-ops so tests and
    repeated module imports do not duplicate the provider.
    """
    if _STATE.initialized:
        return

    cfg = config or TelemetryConfig()
    resource = Resource.create(
        {
            "service.name": cfg.service_name,
            "service.version": cfg.service_version,
        }
    )
    provider = TracerProvider(resource=resource)

    if cfg.enabled:
        exporter = OTLPSpanExporter(endpoint=cfg.endpoint, insecure=True)
        provider.add_span_processor(BatchSpanProcessor(exporter))

    trace.set_tracer_provider(provider)
    _STATE.initialized = True


def get_tracer(name: str) -> trace.Tracer:
    """Return a Tracer instance scoped to the given module name.

    Convention: callers use `get_tracer(__name__)` to scope spans by
    Python module path.
    """
    return trace.get_tracer(name)


def _reset_for_testing() -> None:
    """Test-only hook to allow re-init in pytest fixtures."""
    _STATE.initialized = False
