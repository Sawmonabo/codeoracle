"""Request-context dependencies for API routes."""

from typing import Annotated

from fastapi import Depends, Request
from opentelemetry import trace

_INVALID_TRACE_ID = 0


def get_trace_id() -> str | None:
    """Return the active W3C trace ID, if a valid OTel span is active."""

    span_ctx = trace.get_current_span().get_span_context()
    if span_ctx.trace_id == _INVALID_TRACE_ID:
        return None
    return f"{span_ctx.trace_id:032x}"


def get_correlation_id(request: Request) -> str | None:
    """Return the inbound correlation/request ID header, if present."""

    return request.headers.get("X-Correlation-ID") or request.headers.get(
        "X-Request-ID"
    )


TraceIdDep = Annotated[str | None, Depends(get_trace_id)]
CorrelationIdDep = Annotated[str | None, Depends(get_correlation_id)]
