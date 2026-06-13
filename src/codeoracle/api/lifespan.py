"""Application lifespan management for the API process."""

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
)

from codeoracle.adapters.agent.client import AgentClient
from codeoracle.adapters.valkey.client import ValkeyQueueClient
from codeoracle.adapters.valkey.config import load_valkey_config
from codeoracle.api.metrics import (
    ConnectionPoolCollector,
    register_api_metrics,
    start_queue_metrics_refresher,
    stop_queue_metrics_refresher,
    unregister_api_metrics,
)
from codeoracle.api.resources import ApiResources
from codeoracle.core.config import Settings
from codeoracle.db.session import make_async_engine, make_async_session_factory
from codeoracle.services.indexing.service import IndexingService

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Initialize and drain API-owned process resources."""

    settings: Settings = app.state.settings
    logger.info("api_lifespan_startup_begin")

    engine: AsyncEngine | None = None
    session_factory: async_sessionmaker[AsyncSession] | None = None
    valkey_client: ValkeyQueueClient | None = None
    agent_client: AgentClient | None = None
    pool_collector: ConnectionPoolCollector | None = None
    queue_metrics_task: asyncio.Task[None] | None = None

    try:
        engine = make_async_engine()
        session_factory = make_async_session_factory(engine)
        pool_collector = register_api_metrics(engine, process="api")

        valkey_client = ValkeyQueueClient(load_valkey_config())
        await valkey_client.start()
        queue_metrics_task = start_queue_metrics_refresher(valkey_client)

        indexing_service = IndexingService(
            engine=engine,
            db_session_factory=session_factory,
            queue_client=valkey_client,
            repo_root=settings.repo_root,
            extractor_kinds=tuple(settings.indexing.extractor_kinds),
            advisory_lock_timeout_seconds=(
                settings.api.lock_acquisition_timeout_seconds
            ),
        )
        agent_client = AgentClient(
            base_url=settings.api.agent_base_url,
            request_timeout_seconds=settings.api.agent_request_timeout_seconds,
            shared_token=settings.agent.shared_token,
        )
        app.state.resources = ApiResources(
            settings=settings,
            engine=engine,
            session_factory=session_factory,
            valkey_client=valkey_client,
            indexing_service=indexing_service,
            agent_client=agent_client,
        )

        logger.info("api_lifespan_startup_done")
        yield
    finally:
        logger.info("api_lifespan_shutdown_begin")
        await stop_queue_metrics_refresher(queue_metrics_task)

        if pool_collector is not None:
            unregister_api_metrics(pool_collector)

        if agent_client is not None:
            try:
                await agent_client.aclose()
            except Exception as exc:
                logger.warning(
                    "api_shutdown_agent_aclose_failed",
                    extra={"error": str(exc)},
                )
        if valkey_client is not None:
            try:
                await valkey_client.aclose()
            except Exception as exc:
                logger.warning(
                    "api_shutdown_valkey_aclose_failed",
                    extra={"error": str(exc)},
                )
        if engine is not None:
            try:
                await engine.dispose()
            except Exception as exc:
                logger.warning(
                    "api_shutdown_engine_dispose_failed",
                    extra={"error": str(exc)},
                )
        logger.info("api_lifespan_shutdown_done")
