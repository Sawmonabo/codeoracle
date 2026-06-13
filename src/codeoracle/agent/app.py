"""FastAPI application factory for the agent process."""

from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from typing import Any, cast

from fastapi import FastAPI
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor

from codeoracle.adapters.llm.config import LLMConfig
from codeoracle.adapters.llm.embeddings import EmbeddingsProviderFactory
from codeoracle.adapters.sourcebot.client import SourcebotClient
from codeoracle.adapters.sourcebot.config import SourcebotConfig
from codeoracle.agent.server import register_routes
from codeoracle.core.config import Settings
from codeoracle.core.logging import get_logger
from codeoracle.db.session import make_async_engine, make_async_session_factory
from codeoracle.services.context_budget import ContextBudgetService
from codeoracle.services.llm import build_budgeted_provider

_LOG = get_logger(__name__)


def create_app(settings: Settings) -> FastAPI:
    """Create the production agent app with lifespan-managed resources."""

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        app.state.settings = settings
        app.state.engine = make_async_engine()
        app.state.session_factory = make_async_session_factory(
            app.state.engine
        )
        app.state.llm = build_budgeted_provider(
            LLMConfig(), ContextBudgetService(max_tokens=24_000)
        )
        app.state.embeddings = EmbeddingsProviderFactory.build(settings)
        app.state.sourcebot = SourcebotClient(
            config=cast(Any, SourcebotConfig)()
        )

        try:
            yield
        finally:
            await _drain_resource("sourcebot", app.state.sourcebot.aclose())
            await _drain_resource("llm", app.state.llm.aclose())
            await _drain_resource("engine", app.state.engine.dispose())

    app = FastAPI(lifespan=lifespan)
    FastAPIInstrumentor.instrument_app(app)
    register_routes(app)
    return app


def create_test_app(
    settings: Settings,
    *,
    engine: Any,
    session_factory: Callable[[], Any],
    sourcebot: Any,
    llm: Any,
    embeddings: Any,
) -> FastAPI:
    """Create an agent app using caller-supplied test resources."""
    app = FastAPI()
    app.state.settings = settings
    app.state.engine = engine
    app.state.session_factory = session_factory
    app.state.sourcebot = sourcebot
    app.state.llm = llm
    app.state.embeddings = embeddings
    register_routes(app)
    return app


async def _drain_resource(name: str, awaitable: Any) -> None:
    try:
        await awaitable
    except Exception as exc:
        _LOG.warning(
            "agent_shutdown_resource_drain_failed",
            resource=name,
            error=str(exc),
        )
