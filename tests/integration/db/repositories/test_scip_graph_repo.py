"""
ScipGraphRepository — AGE writes/reads for `:Definition` + `:Reference` nodes.

The repository COMPOSES Plan 5's `GraphRepository` (at
`codeoracle.db.repositories.graph`)
— there is no separate `codeoracle.db.age_driver` module; that name was a stale
sketch from an earlier draft (P0-13 / D-6.8). Plan 5's
`GraphRepository.execute_cypher`
already owns the `SELECT * FROM cypher('codeoracle', $$...$$)` SQL wrapping,
AGE
error translation (`GraphQueryError`), and the agtype-decode convention. This
class only owns the SCIP-specific schema (label namespace `:Definition` and
`:Reference`, property shape).

Tests use the testcontainers PG+AGE instance via the `pg_age_database` fixture.
"""

from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from codeoracle.adapters.scip.parser import ScipDefinition, ScipReference
from codeoracle.db.repositories.scip_graph import ScipGraphRepository

pytestmark = pytest.mark.integration


@pytest_asyncio.fixture
async def session(
    async_session_factory: async_sessionmaker[AsyncSession],
) -> AsyncIterator[AsyncSession]:
    async with async_session_factory() as s:
        yield s


@pytest.mark.asyncio
async def test_replace_file_subgraph_inserts_nodes(
    session: AsyncSession,
) -> None:
    repo = ScipGraphRepository(session)
    defs = [
        ScipDefinition(
            file_path="src/codeoracle/example.py",
            fqn="`example.py`/foo()",
            line_number=5,
            end_line=5,
        ),
    ]
    refs = [
        ScipReference(
            file_path="src/codeoracle/example.py",
            fqn="`example.py`/foo()",
            line_number=12,
            end_line=12,
        ),
        ScipReference(
            file_path="src/codeoracle/example.py",
            fqn="`example.py`/foo()",
            line_number=18,
            end_line=18,
        ),
    ]
    await repo.replace_file_subgraph(
        snapshot_sha="s1",
        file_path="src/codeoracle/example.py",
        definitions=defs,
        references=refs,
    )
    await session.commit()

    assert (
        await repo.count_definitions(
            snapshot_sha="s1", file_path="src/codeoracle/example.py"
        )
        == 1
    )
    assert (
        await repo.count_references(
            snapshot_sha="s1", file_path="src/codeoracle/example.py"
        )
        == 2
    )


@pytest.mark.asyncio
async def test_replace_is_atomic_per_file(session: AsyncSession) -> None:
    """
    Second replace_file_subgraph for same (snapshot, file) MUST delete prior
    nodes.
    """
    repo = ScipGraphRepository(session)
    await repo.replace_file_subgraph(
        snapshot_sha="s2",
        file_path="a.py",
        definitions=[
            ScipDefinition(
                file_path="a.py", fqn="X", line_number=1, end_line=1
            )
        ],
        references=[
            ScipReference(file_path="a.py", fqn="X", line_number=2, end_line=2)
        ],
    )
    await session.commit()
    assert (
        await repo.count_definitions(snapshot_sha="s2", file_path="a.py") == 1
    )
    assert (
        await repo.count_references(snapshot_sha="s2", file_path="a.py") == 1
    )

    # Second call with different content.
    await repo.replace_file_subgraph(
        snapshot_sha="s2",
        file_path="a.py",
        definitions=[
            ScipDefinition(
                file_path="a.py", fqn="Y", line_number=10, end_line=10
            ),
            ScipDefinition(
                file_path="a.py", fqn="Z", line_number=20, end_line=20
            ),
        ],
        references=[],
    )
    await session.commit()
    assert (
        await repo.count_definitions(snapshot_sha="s2", file_path="a.py") == 2
    )
    assert (
        await repo.count_references(snapshot_sha="s2", file_path="a.py") == 0
    )


@pytest.mark.asyncio
async def test_replace_only_affects_target_file(session: AsyncSession) -> None:
    repo = ScipGraphRepository(session)
    await repo.replace_file_subgraph(
        snapshot_sha="s3",
        file_path="a.py",
        definitions=[
            ScipDefinition(
                file_path="a.py", fqn="X", line_number=1, end_line=1
            )
        ],
        references=[],
    )
    await repo.replace_file_subgraph(
        snapshot_sha="s3",
        file_path="b.py",
        definitions=[
            ScipDefinition(
                file_path="b.py", fqn="X", line_number=1, end_line=1
            )
        ],
        references=[],
    )
    await session.commit()

    # Re-replace a.py to empty; b.py must be preserved.
    await repo.replace_file_subgraph(
        snapshot_sha="s3",
        file_path="a.py",
        definitions=[],
        references=[],
    )
    await session.commit()

    assert (
        await repo.count_definitions(snapshot_sha="s3", file_path="a.py") == 0
    )
    assert (
        await repo.count_definitions(snapshot_sha="s3", file_path="b.py") == 1
    )


@pytest.mark.asyncio
async def test_replace_isolated_by_snapshot(session: AsyncSession) -> None:
    repo = ScipGraphRepository(session)
    await repo.replace_file_subgraph(
        snapshot_sha="s4-A",
        file_path="a.py",
        definitions=[
            ScipDefinition(
                file_path="a.py", fqn="X", line_number=1, end_line=1
            )
        ],
        references=[],
    )
    await repo.replace_file_subgraph(
        snapshot_sha="s4-B",
        file_path="a.py",
        definitions=[
            ScipDefinition(
                file_path="a.py", fqn="X", line_number=1, end_line=1
            ),
            ScipDefinition(
                file_path="a.py", fqn="Y", line_number=2, end_line=2
            ),
        ],
        references=[],
    )
    await session.commit()
    assert (
        await repo.count_definitions(snapshot_sha="s4-A", file_path="a.py")
        == 1
    )
    assert (
        await repo.count_definitions(snapshot_sha="s4-B", file_path="a.py")
        == 2
    )
