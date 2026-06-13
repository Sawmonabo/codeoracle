"""IndexingService end-to-end with the three NEW Plan 6 extractor kinds active.

This is a barrier-close test for the three workers Plan 6 introduces. The
fourth kind (joern) is covered by Plan 5's own integration test; including
it here would duplicate that coverage or hand-roll a fragile copy of Plan 5's
consumer construction. The change to `DEFAULT_EXTRACTOR_KINDS` is asserted
separately in `test_service_default_kinds.py`; this test validates the
barrier-close behavior the three new kinds exercise together.

Fixtures used:
- testcontainers PG+AGE+Valkey (real)
- Mocked ScipClient (in-test fake that emits a small ScipParseResult)
- Mocked OpengrepRunner (returns empty matches — barrier still closes)
- MockEmbeddingsProvider (deterministic vectors)

Live SCIP + OpenGrep binary exercise is the E2E test (Task 14, next).
"""

import asyncio
import pathlib
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from codeoracle.adapters.llm.providers.mock_embed import MockEmbeddingsProvider
from codeoracle.adapters.opengrep.runner import OpengrepRunner
from codeoracle.adapters.scip.client import ScipClient
from codeoracle.adapters.scip.parser import (
    ScipDefinition,
    ScipIndexParser,
    ScipParseResult,
)
from codeoracle.adapters.valkey.client import ValkeyQueueClient
from codeoracle.core.config import EmbeddingConfig
from codeoracle.services.indexing.orchestrator import IndexingOrchestrator
from codeoracle.workers._base.message import RetryPolicy
from codeoracle.workers.embed_indexer.consumer import EmbedIndexerConsumer
from codeoracle.workers.opengrep_runner.consumer import OpengrepRunnerConsumer
from codeoracle.workers.scip_indexer.consumer import ScipIndexerConsumer

pytestmark = pytest.mark.integration


@pytest.mark.asyncio
async def test_three_new_kinds_fan_out_and_barrier_closes(
    async_session_factory: async_sessionmaker[AsyncSession],
    valkey_queue_client: ValkeyQueueClient,
    tmp_path: pathlib.Path,
) -> None:
    # Fixtures: tiny repo with 2 Python files.
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "a.py").write_text("def foo(): pass\n")
    (repo / "b.py").write_text("def bar(): pass\n")
    snapshot_sha = "sha-3kinds"
    files = ["a.py", "b.py"]

    # Build the SCIP fake — returns a valid ScipParseResult covering only a.py.
    scip_client = MagicMock(spec=ScipClient)
    scip_client.index_project = AsyncMock(return_value=None)
    scip_client.print_index = AsyncMock(return_value="scip-text")
    scip_parser = MagicMock(spec=ScipIndexParser)
    scip_parser.parse = MagicMock(
        return_value=ScipParseResult(
            definitions=[
                ScipDefinition(
                    file_path="a.py",
                    fqn="`a.py`/foo()",
                    line_number=1,
                    end_line=1,
                )
            ],
            references=[],
            files_covered={"a.py"},
        )
    )

    # OpenGrep fake — empty matches.
    og_runner = MagicMock(spec=OpengrepRunner)
    og_runner.scan_file = AsyncMock(return_value=[])

    config = EmbeddingConfig(
        model="text-embedding-3-small", dim=1536, kind="mock"
    )
    embed_provider = MockEmbeddingsProvider(dim=config.dim)

    stop_event = asyncio.Event()

    # Construct each of the three new Plan 6 consumers.
    scip_consumer = ScipIndexerConsumer(
        queue_client=valkey_queue_client,
        retry_policy=RetryPolicy(max_attempts=2),
        consumer_name="scip-integ",
        stop_event=stop_event,
        scip_client=scip_client,
        parser=scip_parser,
        db_session_factory=async_session_factory,
    )
    og_consumer = OpengrepRunnerConsumer(
        queue_client=valkey_queue_client,
        retry_policy=RetryPolicy(max_attempts=2),
        consumer_name="og-integ",
        stop_event=stop_event,
        runner=og_runner,
        db_session_factory=async_session_factory,
    )
    embed_consumer = EmbedIndexerConsumer(
        queue_client=valkey_queue_client,
        retry_policy=RetryPolicy(max_attempts=2),
        consumer_name="embed-integ",
        stop_event=stop_event,
        embeddings_provider=embed_provider,
        # G14: consumer dispatches chunker_for per message
        embedding_config=config,
        db_session_factory=async_session_factory,
    )

    # Start the three Plan 6 consumers concurrently.
    consumer_tasks = [
        asyncio.create_task(scip_consumer.run()),
        asyncio.create_task(og_consumer.run()),
        asyncio.create_task(embed_consumer.run()),
    ]

    # Build the orchestrator, kick off a reindex with the three new kinds only.
    orchestrator = IndexingOrchestrator(
        queue_client=valkey_queue_client,
        db_session_factory=async_session_factory,
        poll_interval_seconds=0.2,
        max_wait_seconds=30.0,
    )
    outcomes = await orchestrator.fan_out_and_wait(
        snapshot_sha=snapshot_sha,
        repo_root=str(repo),
        files=files,
        extractor_kinds=("scip", "opengrep", "embed"),
    )

    # Stop workers.
    stop_event.set()
    for t in consumer_tasks:
        try:
            await asyncio.wait_for(t, timeout=2.0)
        except TimeoutError:
            t.cancel()

    # Assertions: each extractor produced an outcome status.
    assert set(outcomes.keys()) == {"scip", "opengrep", "embed"}
    for kind, status in outcomes.items():
        assert status in ("success", "partial", "failed"), f"{kind}={status}"
    # SCIP, OpenGrep, embed all returned success (mocks happy).
    assert outcomes["scip"] == "success"
    assert outcomes["opengrep"] == "success"
    assert outcomes["embed"] == "success"
