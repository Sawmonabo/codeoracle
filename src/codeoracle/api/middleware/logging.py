"""Logging middleware for per-request structlog context binding."""

from collections.abc import Awaitable, Callable

import structlog
from opentelemetry import trace
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

_INVALID_TRACE_ID = 0


class StructuredLogMiddleware(BaseHTTPMiddleware):
    """Bind trace and HTTP request fields into structlog contextvars."""

    async def dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        span = trace.get_current_span()
        span_ctx = span.get_span_context()
        trace_id: str | None = (
            f"{span_ctx.trace_id:032x}"
            if span_ctx.trace_id != _INVALID_TRACE_ID
            else None
        )

        structlog.contextvars.bind_contextvars(
            trace_id=trace_id,
            **{
                "http.method": request.method,
                "http.path": request.url.path,
            },
        )
        try:
            return await call_next(request)
        finally:
            structlog.contextvars.unbind_contextvars(
                "trace_id",
                "http.method",
                "http.path",
            )
