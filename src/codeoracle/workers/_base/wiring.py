"""Worker composition root — `build_worker_context()` returns a
WorkerContext with every dependency a worker subclass might need.

Why a composition root? Workers are independent processes started via
`python -m codeoracle.workers.<name>`. Each process needs to wire up its
own pool of dependencies (DB session factory, queue client, LLM
factory). Centralizing the wiring here means:
  1. A new worker (Plan 6 adds 5) only writes its consumer subclass
     and __main__.py — no boilerplate per worker.
  2. Tests can swap one or more dependencies via the optional
     parameters of build_worker_context() — same fixture shape across
     all workers.
"""

import asyncio
import logging
import os
import signal
from contextlib import suppress
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
)  # exposed on WorkerContext

from codeoracle.adapters.llm.config import LLMConfig
from codeoracle.adapters.llm.interfaces import LLMProvider
from codeoracle.adapters.valkey.client import ValkeyQueueClient
from codeoracle.adapters.valkey.config import ValkeyConfig, load_valkey_config
from codeoracle.core.config import get_settings
from codeoracle.core.logging import configure_logging
from codeoracle.db.session import make_async_engine, make_async_session_factory
from codeoracle.services.context_budget import ContextBudgetService
from codeoracle.services.llm import build_budgeted_provider
from codeoracle.workers._base.message import RetryPolicy

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class WorkerContext:
    """Everything a WorkerConsumer subclass needs at construction time.

    The base WorkerConsumer takes a queue_client + retry_policy +
    consumer_name + stop_event; subclasses additionally take whatever
    of these fields they need (db session factory for the indexers,
    llm_provider for the embed indexer in Plan 6).
    """

    queue_client: ValkeyQueueClient
    db_session_factory: async_sessionmaker[AsyncSession]
    # 2026-05-26 amendment — Plan 11 async-job driver: engine exposed so
    # IndexingService (constructed in the reindex_orchestrator __main__)
    # can acquire pg_advisory_lock(0x46AE0001) on the WORKER's connection
    # rather than constructing a second engine (which would double the
    # connection pool and break the single-flight lock invariant if the
    # two engines disagreed on dialect quoting / search_path / etc.).
    # The JoernIndexer (Task 12) doesn't need this — it operates via
    # the session factory only — but exposing it on the shared context
    # avoids a per-worker wiring fork.
    engine: AsyncEngine
    llm_provider: LLMProvider  # eagerly constructed
    retry_policy: RetryPolicy
    stop_event: asyncio.Event
    consumer_name: str


async def build_worker_context(
    *,
    consumer_name_prefix: str,
    retry_policy: RetryPolicy | None = None,
    valkey_config: ValkeyConfig | None = None,
    llm_config: LLMConfig | None = None,
) -> WorkerContext:
    """Construct the WorkerContext.

    All optional parameters default to env-loaded config — pass them
    only in tests. `consumer_name_prefix` is the consumer-group name
    (e.g., "joern_indexer"); a "-<pid>" suffix is appended so multiple
    replicas get distinct names.

    Wires SIGTERM/SIGINT → stop_event.set() so K8s rolling restarts
    drain in-flight work cleanly.
    """
    valkey_config = valkey_config or load_valkey_config()
    llm_config = llm_config or LLMConfig.model_validate({})
    retry_policy = retry_policy or RetryPolicy()

    # 1) Queue client — start so it can XADD to DLQs even on early failures
    queue_client = ValkeyQueueClient(valkey_config)
    await queue_client.start()

    # 2) DB session factory (async) — Plan 3's session.py.
    # `make_async_engine()` reads Settings internally;
    # `make_async_session_factory(engine)`
    # binds the factory. Plan 5 is engine-stateless beyond the factory — the
    # engine
    # is owned by the factory's lifecycle.
    engine = make_async_engine()
    db_session_factory = make_async_session_factory(engine)

    # 3) LLM provider — eager construction (cheap; SDK clients lazy-init
    #    sockets on first request, so no network hit here)
    llm_provider = build_budgeted_provider(
        llm_config, ContextBudgetService(max_tokens=24_000)
    )

    # 4) Stop event wired to SIGTERM/SIGINT
    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        with suppress(NotImplementedError):
            loop.add_signal_handler(sig, stop_event.set)

    # 5) Configure structlog — Plan 3's helper installs the §7.9
    # secret-redaction
    # processor (api_key / secret / token / password keys → '<redacted>') in
    # addition to JSON output. Workers MUST NOT roll their own structlog config
    # or that redaction silently disappears.
    # `get_settings().environment` is Plan 3's canonical source; the
    # CODEORACLE_ENVIRONMENT env var feeds it via pydantic-settings. Don't read
    # CODEORACLE_ENV directly — that's a third name and drifts the contract.
    configure_logging(get_settings())

    consumer_name = f"{consumer_name_prefix}-{os.getpid()}"
    logger.info(
        "worker_context_ready",
        extra={"consumer_name": consumer_name, "stream": consumer_name_prefix},
    )
    return WorkerContext(
        queue_client=queue_client,
        db_session_factory=db_session_factory,
        engine=engine,  # 2026-05-26 amendment — Plan 11 async-job driver
        llm_provider=llm_provider,
        retry_policy=retry_policy,
        stop_event=stop_event,
        consumer_name=consumer_name,
    )


async def teardown_worker_context(ctx: WorkerContext) -> None:
    """Drain worker-owned process resources in reverse construction order."""
    try:
        await ctx.llm_provider.aclose()
    finally:
        try:
            await ctx.queue_client.aclose()
        finally:
            await ctx.engine.dispose()
