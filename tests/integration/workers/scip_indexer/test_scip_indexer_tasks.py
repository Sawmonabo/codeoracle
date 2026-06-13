"""Pure-function tests for `extract_project_to_graph` — the worker's logic
factored out of the consumer so it can be unit-tested without Valkey.

The function takes injected adapters (ScipClient, ScipIndexParser,
ScipGraphRepository, SnapshotFileOutcomeRepository) and a message; runs the
project-mode pipeline; returns the per-file outcome status map.
"""

import pathlib
from collections.abc import AsyncIterator
from unittest.mock import AsyncMock, MagicMock

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from codeoracle.adapters.scip.parser import (
    ScipDefinition,
    ScipIndexParser,
    ScipParseError,
    ScipParseResult,
)
from codeoracle.core.exceptions import ScipExtractionError
from codeoracle.db.repositories.scip_graph import ScipGraphRepository
from codeoracle.db.repositories.snapshot_file_outcome import (
    SnapshotFileOutcomeRepository,
)
from codeoracle.workers.scip_indexer.message import ScipIndexerMessage
from codeoracle.workers.scip_indexer.tasks import extract_project_to_graph

pytestmark = [pytest.mark.integration, pytest.mark.worker]


@pytest_asyncio.fixture
async def session(
    async_session_factory: async_sessionmaker[AsyncSession],
) -> AsyncIterator[AsyncSession]:
    async with async_session_factory() as s:
        yield s


@pytest.mark.asyncio
async def test_writes_outcomes_for_all_files_in_message(
    session: AsyncSession,
    tmp_path: pathlib.Path,
) -> None:
    """Every file in `message.files` MUST receive an outcome row, regardless
    of SCIP coverage (architectural invariant 2)."""
    msg = ScipIndexerMessage(
        batch_id="sha-cov",
        snapshot_sha="sha-cov",
        repo_root=str(tmp_path),
        files=["a.py", "b.py", "c.py"],  # SCIP will only find symbols in a.py
    )

    # Fake adapters.
    scip_client = MagicMock()
    scip_client.index_project = AsyncMock(return_value=None)
    scip_client.print_index = AsyncMock(return_value="<text>")

    parser = MagicMock(spec=ScipIndexParser)
    parser.parse = MagicMock(
        return_value=ScipParseResult(
            definitions=[
                ScipDefinition(
                    file_path="a.py",
                    fqn="`a.py`/foo()",
                    line_number=5,
                    end_line=5,
                ),
            ],
            references=[],
            files_covered={"a.py"},  # b.py, c.py not in covered set
        )
    )

    graph_repo = ScipGraphRepository(session)
    outcome_repo = SnapshotFileOutcomeRepository(session)

    statuses = await extract_project_to_graph(
        message=msg,
        scip_client=scip_client,
        parser=parser,
        scip_graph_repo=graph_repo,
        outcome_repo=outcome_repo,
        session=session,
    )

    assert statuses == {
        "a.py": "success",
        "b.py": "success",
        "c.py": "success",
    }

    # The outcome rows for ALL three files exist.
    completed_count = await outcome_repo.count_completed(
        snapshot_sha="sha-cov", extractor="scip"
    )
    assert completed_count == 3


@pytest.mark.asyncio
async def test_scip_failure_marks_all_files_failed(
    session: AsyncSession,
    tmp_path: pathlib.Path,
) -> None:
    """Architectural invariant 3: project-level failure → all-file failure."""
    msg = ScipIndexerMessage(
        batch_id="sha-fail",
        snapshot_sha="sha-fail",
        repo_root=str(tmp_path),
        files=["a.py", "b.py"],
    )

    scip_client = MagicMock()
    scip_client.index_project = AsyncMock(
        side_effect=ScipExtractionError("scip blew up")
    )
    scip_client.print_index = AsyncMock(return_value="")

    parser = MagicMock(spec=ScipIndexParser)
    graph_repo = ScipGraphRepository(session)
    outcome_repo = SnapshotFileOutcomeRepository(session)

    statuses = await extract_project_to_graph(
        message=msg,
        scip_client=scip_client,
        parser=parser,
        scip_graph_repo=graph_repo,
        outcome_repo=outcome_repo,
        session=session,
    )
    assert statuses == {"a.py": "failed", "b.py": "failed"}

    failed_count = await outcome_repo.count_failed(
        snapshot_sha="sha-fail", extractor="scip"
    )
    assert failed_count == 2


@pytest.mark.asyncio
async def test_parse_failure_marks_all_files_failed(
    session: AsyncSession,
    tmp_path: pathlib.Path,
) -> None:
    msg = ScipIndexerMessage(
        batch_id="sha-parsefail",
        snapshot_sha="sha-parsefail",
        repo_root=str(tmp_path),
        files=["a.py"],
    )

    scip_client = MagicMock()
    scip_client.index_project = AsyncMock(return_value=None)
    scip_client.print_index = AsyncMock(return_value="garbage")

    parser = MagicMock(spec=ScipIndexParser)
    parser.parse = MagicMock(side_effect=ScipParseError("bad output"))

    graph_repo = ScipGraphRepository(session)
    outcome_repo = SnapshotFileOutcomeRepository(session)

    statuses = await extract_project_to_graph(
        message=msg,
        scip_client=scip_client,
        parser=parser,
        scip_graph_repo=graph_repo,
        outcome_repo=outcome_repo,
        session=session,
    )
    assert statuses == {"a.py": "failed"}


@pytest.mark.asyncio
async def test_scip_emits_divergent_paths_still_matches_message_files(
    session: AsyncSession,
    tmp_path: pathlib.Path,
) -> None:
    """SCIP-python may emit paths that don't byte-identically match the
    `message.files` strings — e.g., SCIP outputs `src/codeoracle/example.py`
    while the orchestrator dispatched `files=["example.py"]` (or vice versa,
    or with a leading "./" prefix). The worker MUST normalize and still
    write the SCIP graph to the corresponding message_file slot.

    Without this normalization, the symptom is silent: outcome=success rows
    land for every file but `count_definitions` returns 0 because the path
    lookup missed.
    """
    msg = ScipIndexerMessage(
        batch_id="sha-pathnorm",
        snapshot_sha="sha-pathnorm",
        repo_root=str(tmp_path),
        files=["example.py"],  # orchestrator dispatched short path
    )

    scip_client = MagicMock()
    scip_client.index_project = AsyncMock(return_value=None)
    scip_client.print_index = AsyncMock(return_value="<text>")

    # SCIP-python emits the LONGER path with subdirectory prefix.
    parser = MagicMock(spec=ScipIndexParser)
    parser.parse = MagicMock(
        return_value=ScipParseResult(
            definitions=[
                ScipDefinition(
                    # NOT byte-identical to message.files
                    file_path="src/codeoracle/example.py",
                    fqn="`example.py`/foo()",
                    line_number=5,
                    end_line=5,
                ),
            ],
            references=[],
            files_covered={"src/codeoracle/example.py"},
        )
    )

    graph_repo = ScipGraphRepository(session)
    outcome_repo = SnapshotFileOutcomeRepository(session)

    statuses = await extract_project_to_graph(
        message=msg,
        scip_client=scip_client,
        parser=parser,
        scip_graph_repo=graph_repo,
        outcome_repo=outcome_repo,
        session=session,
    )
    assert statuses == {"example.py": "success"}
    # CRITICAL ASSERTION: the symbol actually landed in the graph, keyed
    # under "example.py" (the message.files path), not
    # "src/codeoracle/example.py".
    assert (
        await graph_repo.count_definitions(
            snapshot_sha="sha-pathnorm",
            file_path="example.py",
        )
        == 1
    )


@pytest.mark.asyncio
async def test_scip_emits_absolute_path_still_matches(
    session: AsyncSession,
    tmp_path: pathlib.Path,
) -> None:
    """SCIP may emit absolute paths (`/repo/example.py`); the normalizer
    strips `repo_root` and matches."""
    msg = ScipIndexerMessage(
        batch_id="sha-abspath",
        snapshot_sha="sha-abspath",
        repo_root="/repo",
        files=["example.py"],
    )

    scip_client = MagicMock()
    scip_client.index_project = AsyncMock(return_value=None)
    scip_client.print_index = AsyncMock(return_value="<text>")

    parser = MagicMock(spec=ScipIndexParser)
    parser.parse = MagicMock(
        return_value=ScipParseResult(
            definitions=[
                ScipDefinition(
                    file_path="/repo/example.py",  # absolute
                    fqn="X",
                    line_number=1,
                    end_line=1,
                ),
            ],
            references=[],
            files_covered={"/repo/example.py"},
        )
    )

    graph_repo = ScipGraphRepository(session)
    outcome_repo = SnapshotFileOutcomeRepository(session)

    statuses = await extract_project_to_graph(
        message=msg,
        scip_client=scip_client,
        parser=parser,
        scip_graph_repo=graph_repo,
        outcome_repo=outcome_repo,
        session=session,
    )
    assert statuses == {"example.py": "success"}
    assert (
        await graph_repo.count_definitions(
            snapshot_sha="sha-abspath",
            file_path="example.py",
        )
        == 1
    )


@pytest.mark.asyncio
async def test_empty_files_list_writes_no_outcomes(
    session: AsyncSession,
    tmp_path: pathlib.Path,
) -> None:
    """Edge case: no Python files in the snapshot. Worker must NOT raise;
    must NOT block scip-python. Returns empty status dict."""
    msg = ScipIndexerMessage(
        batch_id="sha-empty",
        snapshot_sha="sha-empty",
        repo_root=str(tmp_path),
        files=[],
    )

    scip_client = MagicMock()
    scip_client.index_project = AsyncMock(return_value=None)
    scip_client.print_index = AsyncMock(return_value="")

    parser = MagicMock(spec=ScipIndexParser)
    parser.parse = MagicMock(return_value=ScipParseResult())

    graph_repo = ScipGraphRepository(session)
    outcome_repo = SnapshotFileOutcomeRepository(session)

    statuses = await extract_project_to_graph(
        message=msg,
        scip_client=scip_client,
        parser=parser,
        scip_graph_repo=graph_repo,
        outcome_repo=outcome_repo,
        session=session,
    )
    assert statuses == {}
