# src/codeoracle/mcp/__main__.py
"""Composition root + binary entry for the MCP server process.

Registered as `codeoracle-mcp` in pyproject.toml [project.scripts]. Two
transport modes selected by argv:

- (default, no args) → stdio JSON-RPC (dev / IDE integration)
- `--http`           → streamable HTTP (service mode; §8.5 parity gate)

The composition root constructs the dependency graph eagerly
(process-lifetime scope), wraps it in MCPServer, dispatches to the
chosen transport runner, and tears down via try/finally on exit.

Stdout-pollution invariant (Plan 12 invariant 5): in stdio mode,
configure_logging is called with stream=sys.stderr BEFORE any
dependency construction so the first log line (e.g., engine init)
doesn't corrupt the JSON-RPC framing on stdout.
"""

import asyncio
import logging
import sys
from dataclasses import dataclass
from typing import TYPE_CHECKING, Annotated

import typer

from codeoracle.adapters.agent.client import AgentClient
from codeoracle.adapters.valkey.client import ValkeyQueueClient
from codeoracle.adapters.valkey.config import load_valkey_config
from codeoracle.core.config import Settings, get_settings
from codeoracle.core.logging import configure_logging
from codeoracle.db.session import make_async_engine, make_async_session_factory
from codeoracle.mcp.server import MCPServer
from codeoracle.mcp.transports import run_stdio, run_streamable_http
from codeoracle.services.indexing.service import IndexingService

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncEngine


logger = logging.getLogger(__name__)


app = typer.Typer(
    help=(
        "CodeOracle MCP server — stdio (default) or streamable HTTP (--http)."
    ),
)


@dataclass
class _Dependencies:
    """Holder for the eagerly-constructed dependency graph.

    Lives for the entire process. Cleanup is via aclose() in __main__'s
    finally block — NOT via the SDK Server's lifespan (Plan 12 invariant 7).
    """

    engine: AsyncEngine
    valkey_client: ValkeyQueueClient
    indexing_service: IndexingService
    agent_client: AgentClient

    async def aclose(self) -> None:
        """Tear down all deps in reverse construction order."""
        await self.agent_client.aclose()
        await self.valkey_client.aclose()
        await self.engine.dispose()


def _build_dependency_graph(settings: Settings) -> _Dependencies:
    """Construct the dep graph (process-lifetime scope).

    Construction order:
    1. AsyncEngine via Plan 3's make_async_engine() — must be first
       (registers the AGE connect listener so agtype queries work — D2-7);
       IndexingService consumes it
    2. ValkeyQueueClient — needed by IndexingService for queue enqueue
    3. IndexingService — composes engine + session_factory + queue + repo_root
    4. AgentClient — independent (HTTP client to the agent process)

    Each construct uses settings fields from Plan 3 + Plan 11's amendments.
    Valkey settings are loaded through `load_valkey_config()`, which preserves
    the runtime fail-fast env contract while keeping this composition root
    statically typed.
    """
    # reads get_settings() (== settings param); registers AGE listener (D2-7)
    engine = make_async_engine()
    valkey_client = ValkeyQueueClient(load_valkey_config())
    session_factory = make_async_session_factory(engine)
    indexing_service = IndexingService(
        engine=engine,
        db_session_factory=session_factory,
        queue_client=valkey_client,
        repo_root=settings.repo_root,
        # advisory_lock_timeout_seconds + extractor_kinds left at
        # IndexingService
        # defaults: the MCP reindex tool calls enqueue_reindex (persist a
        # PENDING
        # Snapshot + XADD to STREAM_REINDEX_REQUESTS), which never acquires
        # pg_advisory_lock(0x46AE0001) nor runs extractors — those are the
        # ReindexOrchestratorConsumer worker's job (invariant 3 / DECIDE-J
        # L177).
        # Mirroring the API's lock-timeout here would imply MCP shares lock
        # contention the 2026-05-26 async pivot deliberately moved off this
        # boundary.
    )
    agent_client = AgentClient(
        base_url=settings.api.agent_base_url,
        request_timeout_seconds=settings.api.agent_request_timeout_seconds,
        shared_token=settings.agent.shared_token,
    )
    return _Dependencies(
        engine=engine,
        valkey_client=valkey_client,
        indexing_service=indexing_service,
        agent_client=agent_client,
    )


@app.command()
def main(
    http: Annotated[
        bool,
        typer.Option(
            "--http",
            help="Use streamable HTTP transport (default: stdio JSON-RPC).",
        ),
    ] = False,
) -> None:
    """Binary entry — stdio (default) or streamable HTTP (--http)."""
    transport = "http" if http else "stdio"
    settings = get_settings()

    # CRITICAL ORDERING — stdout-pollution invariant (Plan 12 invariant 5):
    # In stdio mode, configure_logging MUST be called with stream=sys.stderr
    # BEFORE any other code path that might log (including engine init).
    if transport == "stdio":
        configure_logging(settings, stream=sys.stderr)
    else:
        configure_logging(settings)

    logger.info("codeoracle_mcp_starting transport=%s", transport)
    deps = _build_dependency_graph(settings)
    server = MCPServer(
        agent_client=deps.agent_client,
        indexing_service=deps.indexing_service,
        settings=settings,
    )
    try:
        if transport == "stdio":
            asyncio.run(run_stdio(server))
        else:
            run_streamable_http(
                server,
                host=settings.mcp.host,
                port=settings.mcp.port,
            )
    finally:
        # asyncio.run is OK here because we're outside any running loop
        # (run_stdio's loop has exited by the time we reach finally; the
        # uvicorn HTTP path is sync and doesn't leave a loop running).
        asyncio.run(deps.aclose())


if __name__ == "__main__":
    app()
