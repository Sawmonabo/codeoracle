"""FastAPI application factory for the API process."""

from typing import Any, cast

from fastapi import FastAPI
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from prometheus_client import make_asgi_app

from codeoracle.api.errors import get_exception_handlers
from codeoracle.api.lifespan import lifespan
from codeoracle.api.middleware import StructuredLogMiddleware
from codeoracle.api.v1.router import router as v1_router
from codeoracle.core.config import Settings, get_settings
from codeoracle.core.version import get_version


def create_app(settings: Settings | None = None) -> FastAPI:
    """Build the FastAPI application."""

    s = settings or get_settings()
    app = FastAPI(
        title="CodeOracle API",
        version=get_version(),
        lifespan=lifespan,
        exception_handlers=cast(Any, get_exception_handlers()),
    )
    app.state.settings = s

    FastAPIInstrumentor.instrument_app(app)
    app.add_middleware(StructuredLogMiddleware)
    app.mount("/metrics", make_asgi_app())
    app.include_router(v1_router)

    return app
