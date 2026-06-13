"""Per-message handler for the joern_indexer worker.

Pulled out into a free function so the consumer subclass stays thin
and the handler is independently unit-testable with a fixture of
JoernClient / GraphRepository / SnapshotFileOutcomeRepository mocks.
"""

import logging
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from codeoracle.adapters.joern.client import JoernClient
from codeoracle.adapters.joern.parser import JoernCpgParser
from codeoracle.core.exceptions import (
    JoernExtractionError,
    TerminalWorkerError,
)
from codeoracle.db.repositories.graph import GraphRepository
from codeoracle.db.repositories.snapshot_file_outcome import (
    SnapshotFileOutcomeRepository,
)
from codeoracle.workers.joern_indexer.message import JoernIndexerMessage

logger = logging.getLogger(__name__)


async def extract_file_to_graph(
    *,
    message: JoernIndexerMessage,
    joern_client: JoernClient,
    parser: JoernCpgParser,
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Extract one file's CPG, MERGE into AGE, record the outcome.

    Raises JoernExtractionError (terminal) when the joern-cli rejects
    the file. Transient errors (DB connection blips) bubble up
    untranslated; the base WorkerConsumer catches them and treats
    them as transient by default.
    """
    file_path = Path(message.repo_root) / message.file_path
    logger.info(
        "joern_indexer_dispatch",
        extra={
            "snapshot_sha": message.snapshot_sha,
            "file": message.file_path,
        },
    )

    export = None
    try:
        export = joern_client.extract(file_path)
        cypher_stmts = parser.to_cypher(
            export_dir=export.export_dir,
            snapshot_sha=message.snapshot_sha,
            file_path=message.file_path,
        )

        async with db_session_factory() as session:
            graph = GraphRepository(session)
            await graph.replace_file_subgraph(
                snapshot_sha=message.snapshot_sha,
                file_path=message.file_path,
                cypher_inserts=cypher_stmts,
            )
            file_outcomes = SnapshotFileOutcomeRepository(session)
            await file_outcomes.upsert(
                snapshot_sha=message.snapshot_sha,
                file_path=message.file_path,
                extractor="joern",
                outcome="success",
            )
            await session.commit()
    except JoernExtractionError as exc:
        # Record the failed outcome BEFORE re-raising so IndexingService's
        # barrier count is accurate even when extraction fails.
        async with db_session_factory() as session:
            file_outcomes = SnapshotFileOutcomeRepository(session)
            await file_outcomes.upsert(
                snapshot_sha=message.snapshot_sha,
                file_path=message.file_path,
                extractor="joern",
                outcome="failed",
                error=str(exc),
            )
            await session.commit()
        raise TerminalWorkerError(str(exc)) from exc
    finally:
        if export is not None:
            joern_client.cleanup(export)
