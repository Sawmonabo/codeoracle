"""Unit tests for api/middleware.py — structured-log middleware.

The middleware binds OTel trace_id + http.method + http.path into
structlog contextvars per request. Tests verify (a) bind happens
before downstream handlers see the request, (b) unbind happens after
the response is returned, (c) trace_id is the 32-hex W3C format.
"""

import structlog
from fastapi import FastAPI
from fastapi.testclient import TestClient
from opentelemetry.context import attach, detach
from opentelemetry.trace import (
    NonRecordingSpan,
    SpanContext,
    TraceFlags,
    set_span_in_context,
)

from codeoracle.api.middleware.logging import StructuredLogMiddleware


def _build_app_with_middleware() -> FastAPI:
    """Build a minimal FastAPI app with the middleware mounted and a
    test endpoint that returns the current structlog contextvars.
    """
    app = FastAPI()
    app.add_middleware(StructuredLogMiddleware)

    @app.get("/_probe")
    async def probe():
        ctx = structlog.contextvars.get_contextvars()
        return {
            "trace_id": ctx.get("trace_id"),
            "http.method": ctx.get("http.method"),
            "http.path": ctx.get("http.path"),
        }

    return app


def test_middleware_binds_method_and_path():
    """Without any OTel span active, trace_id is None but method + path
    bind.
    """
    app = _build_app_with_middleware()
    client = TestClient(app)
    r = client.get("/_probe")
    assert r.status_code == 200
    body = r.json()
    assert body["http.method"] == "GET"
    assert body["http.path"] == "/_probe"
    # No active span → trace_id is None (we don't fabricate one)
    assert body["trace_id"] is None


def test_middleware_binds_trace_id_when_active_span():
    """With a parent span active in the request scope, the middleware
    extracts trace_id as 32-hex W3C and binds it.
    """
    span_ctx = SpanContext(
        trace_id=0xABCDEF0123456789ABCDEF0123456789,
        span_id=0x1111222233334444,
        is_remote=True,
        trace_flags=TraceFlags(0x01),
    )
    ctx = set_span_in_context(NonRecordingSpan(span_ctx))
    tok = attach(ctx)
    try:
        app = _build_app_with_middleware()
        client = TestClient(app)
        r = client.get("/_probe")
    finally:
        detach(tok)

    assert r.status_code == 200
    body = r.json()
    assert body["trace_id"] == "abcdef0123456789abcdef0123456789"


def test_middleware_unbinds_after_response():
    """After the request completes, the contextvars are clean for the
    NEXT request — no leakage between handlers.
    """
    app = _build_app_with_middleware()
    client = TestClient(app)

    # First request seeds the contextvars
    client.get("/_probe")

    # Inspect contextvars OUTSIDE a request — the middleware's unbind
    # should have cleared trace_id / method / path.
    ctx = structlog.contextvars.get_contextvars()
    assert ctx.get("trace_id") is None
    assert ctx.get("http.method") is None
    assert ctx.get("http.path") is None
