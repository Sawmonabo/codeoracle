"""ScipIndexerConsumer — handle() integration.

These tests verify the consumer's wiring (it constructs a session from the
factory, calls extract_project_to_graph, returns without raising) rather
than re-testing extract_project_to_graph itself (already covered).
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from codeoracle.adapters.scip.client import ScipClient
from codeoracle.adapters.scip.parser import ScipIndexParser, ScipParseResult
from codeoracle.workers._base.message import RetryPolicy
from codeoracle.workers.scip_indexer.consumer import ScipIndexerConsumer
from codeoracle.workers.scip_indexer.message import ScipIndexerMessage

pytestmark = [pytest.mark.integration, pytest.mark.worker]


@pytest.mark.asyncio
async def test_handle_invokes_pipeline_and_commits(
    async_session_factory: async_sessionmaker[AsyncSession],
    valkey_queue_client,  # Plan 5 fixture
) -> None:
    scip_client = MagicMock(spec=ScipClient)
    scip_client.index_project = AsyncMock(return_value=None)
    scip_client.print_index = AsyncMock(return_value="")
    parser = MagicMock(spec=ScipIndexParser)
    parser.parse = MagicMock(return_value=ScipParseResult())

    consumer = ScipIndexerConsumer(
        queue_client=valkey_queue_client,
        retry_policy=RetryPolicy(
            max_attempts=2,
        ),
        consumer_name="scip-test",
        stop_event=asyncio.Event(),
        scip_client=scip_client,
        parser=parser,
        db_session_factory=async_session_factory,
    )

    msg = ScipIndexerMessage(
        batch_id="sha-handle",
        snapshot_sha="sha-handle",
        repo_root="/tmp/repo",
        files=["a.py"],
    )
    # Direct handle() invocation — not through poll loop.
    await consumer.handle(msg)

    # Verify scip_client was called (the pipeline ran).
    scip_client.index_project.assert_awaited_once()
    scip_client.print_index.assert_awaited_once()
    parser.parse.assert_called_once()
