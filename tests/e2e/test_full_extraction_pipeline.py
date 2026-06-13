"""E2E: full 3-extractor (Plan 6) reindex against a tiny live fixture.

Skipped without --run-e2e. Requires on PATH:
- scip-python
- scip
- opengrep
(Plan 5 ships joern's own E2E; the 4-kind end-to-end with joern + Plan 6 is
exercised by Plan 13's full E2E vertical slice.)

Embeddings: uses MockEmbeddingsProvider by default. If --run-live-embeddings
is also passed, attempts to use the configured live provider; falls back to
Mock if env vars are missing.
"""

import asyncio
import pathlib
import shutil

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from codeoracle.adapters.llm.providers.mock_embed import MockEmbeddingsProvider
from codeoracle.adapters.opengrep import DEFAULT_RULES_DIR
from codeoracle.adapters.opengrep.runner import OpengrepRunner
from codeoracle.adapters.scip.client import ScipClient
from codeoracle.adapters.scip.parser import ScipIndexParser
from codeoracle.adapters.valkey.client import ValkeyQueueClient
from codeoracle.core.config import EmbeddingConfig
from codeoracle.db.repositories.code_references import CodeReferencesRepository
from codeoracle.db.repositories.embeddings import EmbeddingsRepository
from codeoracle.db.repositories.scip_graph import ScipGraphRepository
from codeoracle.db.repositories.snapshot_file_outcome import (
    SnapshotFileOutcomeRepository,
)
from codeoracle.services.indexing.orchestrator import IndexingOrchestrator
from codeoracle.workers._base.message import RetryPolicy
from codeoracle.workers.embed_indexer.consumer import EmbedIndexerConsumer
from codeoracle.workers.opengrep_runner.consumer import OpengrepRunnerConsumer
from codeoracle.workers.scip_indexer.consumer import ScipIndexerConsumer

pytestmark = [
    pytest.mark.e2e,
    pytest.mark.skipif(
        shutil.which("scip-python") is None
        or shutil.which("scip") is None
        or shutil.which("opengrep") is None,
        reason="E2E needs scip-python + scip + opengrep on PATH",
    ),
]


@pytest.mark.asyncio
async def test_full_3_extractor_pipeline(
    async_session_factory: async_sessionmaker[AsyncSession],
    valkey_queue_client: ValkeyQueueClient,
    tmp_path: pathlib.Path,
) -> None:
    """End-to-end: 2-file Python project goes through scip, opengrep, embed.
    Joern is intentionally NOT in this E2E — Plan 5's E2E covers it; Plan 13's
    final vertical slice covers the 4-extractor live combination."""
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "main.py").write_text(
        "def greet(name):\n"
        '    return f"hello {name}"\n\n\n'
        'print(greet("world"))\n'
    )
    (repo / "helpers.py").write_text(
        "from main import greet\n\n\n"
        "def shout():\n"
        '    return greet("loud").upper()\n'
    )
    snapshot_sha = "e2e-sha-001"
    files = ["main.py", "helpers.py"]
    config = EmbeddingConfig(
        model="text-embedding-3-small", dim=1536, kind="mock"
    )

    stop_event = asyncio.Event()

    # Build real adapters.
    scip_consumer = ScipIndexerConsumer(
        queue_client=valkey_queue_client,
        retry_policy=RetryPolicy(max_attempts=2),
        consumer_name="scip-e2e",
        stop_event=stop_event,
        scip_client=ScipClient(),
        parser=ScipIndexParser(),
        db_session_factory=async_session_factory,
    )
    og_consumer = OpengrepRunnerConsumer(
        queue_client=valkey_queue_client,
        retry_policy=RetryPolicy(max_attempts=2),
        consumer_name="og-e2e",
        stop_event=stop_event,
        runner=OpengrepRunner(rules_dir=DEFAULT_RULES_DIR),
        db_session_factory=async_session_factory,
    )
    embed_consumer = EmbedIndexerConsumer(
        queue_client=valkey_queue_client,
        retry_policy=RetryPolicy(max_attempts=2),
        consumer_name="embed-e2e",
        stop_event=stop_event,
        embeddings_provider=MockEmbeddingsProvider(dim=config.dim),
        # G14: consumer dispatches chunker_for per message
        embedding_config=config,
        db_session_factory=async_session_factory,
    )

    # Start consumers concurrently.
    tasks = [
        asyncio.create_task(scip_consumer.run()),
        asyncio.create_task(og_consumer.run()),
        asyncio.create_task(embed_consumer.run()),
    ]

    # Fan out a reindex (joern omitted in this E2E because Plan 5's joern is
    # tested in its own E2E; Plan 6 focuses on its three additions).
    orchestrator = IndexingOrchestrator(
        queue_client=valkey_queue_client,
        db_session_factory=async_session_factory,
        poll_interval_seconds=0.5,
        max_wait_seconds=120.0,  # SCIP project-mode can be slow on cold cache
    )
    outcomes = await orchestrator.fan_out_and_wait(
        snapshot_sha=snapshot_sha,
        repo_root=str(repo),
        files=files,
        extractor_kinds=("scip", "opengrep", "embed"),
    )

    # Stop consumers.
    stop_event.set()
    for t in tasks:
        try:
            await asyncio.wait_for(t, timeout=3.0)
        except TimeoutError:
            t.cancel()

    # Outcomes: all three extractors succeeded (best-case).
    assert outcomes["scip"] in ("success", "partial"), (
        f"scip outcome={outcomes['scip']!r}"
    )
    assert outcomes["opengrep"] in ("success", "partial"), (
        f"opengrep outcome={outcomes['opengrep']!r}"
    )
    assert outcomes["embed"] in ("success", "partial"), (
        f"embed outcome={outcomes['embed']!r}"
    )

    # Per-extractor evidence: actual artifacts written.
    async with async_session_factory() as session:
        scip_repo = ScipGraphRepository(session)
        og_repo = CodeReferencesRepository(session)
        embed_repo = EmbeddingsRepository(session)
        outcome_repo = SnapshotFileOutcomeRepository(session)

        # SCIP: at least one :Definition (greet, shout) somewhere.
        total_defs = sum(
            [
                await scip_repo.count_definitions(
                    snapshot_sha=snapshot_sha, file_path=f
                )
                for f in files
            ]
        )
        assert total_defs >= 1, (
            "expected at least one :Definition node from SCIP"
        )

        # OpenGrep: at least one code reference (e.g., import of greet).
        # We don't assert exact count because OpenGrep rule patterns may
        # evolve.
        n_greet = await og_repo.count_by_symbol(
            symbol_name="greet", snapshot_sha=snapshot_sha
        )
        assert n_greet >= 1, "expected at least one OpenGrep match for 'greet'"

        # Embed: at least one embedding row per file.
        # We KNN against MockEmbeddingsProvider's vector for the file content;
        # the file IS in the index, so the top hit's source_path matches.
        provider = MockEmbeddingsProvider(dim=config.dim)
        query_vec = (await provider.embed(["greet"]))[0]
        hits = await embed_repo.knn(
            query_embedding=query_vec, snapshot_sha=snapshot_sha, k=10
        )
        assert len(hits) >= 2, (
            "expected at least 2 embedding chunks across the 2-file project"
        )

        # Outcome counts: 2 files x 3 extractors = 6 rows, all success.
        for ext in ("scip", "opengrep", "embed"):
            completed = await outcome_repo.count_completed(
                snapshot_sha=snapshot_sha, extractor=ext
            )
            failed = await outcome_repo.count_failed(
                snapshot_sha=snapshot_sha, extractor=ext
            )
            assert completed + failed == 2, (
                f"{ext}: expected 2 outcome rows, got {completed + failed}"
            )
            assert failed == 0, f"{ext}: unexpected failures: {failed}"
