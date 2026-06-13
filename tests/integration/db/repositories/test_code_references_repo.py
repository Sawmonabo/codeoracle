"""CodeReferencesRepository — unit tests against testcontainers PG."""

from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from codeoracle.db.repositories.code_references import (
    CodeReferenceRow,
    CodeReferencesRepository,
)

pytestmark = pytest.mark.integration


@pytest_asyncio.fixture
async def session(
    async_session_factory: async_sessionmaker[AsyncSession],
) -> AsyncIterator[AsyncSession]:
    async with async_session_factory() as s:
        yield s


@pytest.mark.asyncio
async def test_replace_inserts_rows(session: AsyncSession) -> None:
    repo = CodeReferencesRepository(session)
    rows = [
        CodeReferenceRow(
            snapshot_sha="s1",
            file_path="a.py",
            symbol_name="foo",
            line_number=10,
            kind="call",
        ),
        CodeReferenceRow(
            snapshot_sha="s1",
            file_path="a.py",
            symbol_name="foo",
            line_number=20,
            kind="call",
        ),
        CodeReferenceRow(
            snapshot_sha="s1",
            file_path="a.py",
            symbol_name="bar",
            line_number=5,
            kind="import",
        ),
    ]
    await repo.replace_file_references(
        snapshot_sha="s1", file_path="a.py", references=rows
    )
    await session.commit()

    count = await repo.count_by_symbol(symbol_name="foo", snapshot_sha="s1")
    assert count == 2

    count_bar = await repo.count_by_symbol(
        symbol_name="bar", snapshot_sha="s1"
    )
    assert count_bar == 1


@pytest.mark.asyncio
async def test_replace_deletes_existing_then_inserts(
    session: AsyncSession,
) -> None:
    """`replace_file_references` is atomic: all existing rows for this
    (snapshot, file) are deleted, then the new rows inserted. A subsequent
    call with fewer rows REDUCES the count, not adds to it."""
    repo = CodeReferencesRepository(session)

    first = [
        CodeReferenceRow(
            snapshot_sha="s2",
            file_path="x.py",
            symbol_name="foo",
            line_number=1,
            kind="call",
        ),
        CodeReferenceRow(
            snapshot_sha="s2",
            file_path="x.py",
            symbol_name="foo",
            line_number=2,
            kind="call",
        ),
        CodeReferenceRow(
            snapshot_sha="s2",
            file_path="x.py",
            symbol_name="foo",
            line_number=3,
            kind="call",
        ),
    ]
    await repo.replace_file_references(
        snapshot_sha="s2", file_path="x.py", references=first
    )
    await session.commit()
    assert (
        await repo.count_by_symbol(symbol_name="foo", snapshot_sha="s2") == 3
    )

    second = [
        CodeReferenceRow(
            snapshot_sha="s2",
            file_path="x.py",
            symbol_name="foo",
            line_number=99,
            kind="import",
        ),
    ]
    await repo.replace_file_references(
        snapshot_sha="s2", file_path="x.py", references=second
    )
    await session.commit()
    assert (
        await repo.count_by_symbol(symbol_name="foo", snapshot_sha="s2") == 1
    )


@pytest.mark.asyncio
async def test_replace_only_affects_target_file(session: AsyncSession) -> None:
    """`replace_file_references(s, file=A)` MUST NOT delete rows for file=B
    even if they share snapshot_sha and symbol_name."""
    repo = CodeReferencesRepository(session)

    await repo.replace_file_references(
        snapshot_sha="s3",
        file_path="a.py",
        references=[
            CodeReferenceRow(
                snapshot_sha="s3",
                file_path="a.py",
                symbol_name="foo",
                line_number=1,
                kind="call",
            ),
        ],
    )
    await repo.replace_file_references(
        snapshot_sha="s3",
        file_path="b.py",
        references=[
            CodeReferenceRow(
                snapshot_sha="s3",
                file_path="b.py",
                symbol_name="foo",
                line_number=1,
                kind="call",
            ),
        ],
    )
    await session.commit()

    # foo has one reference in each file: total 2.
    assert (
        await repo.count_by_symbol(symbol_name="foo", snapshot_sha="s3") == 2
    )

    # Now re-replace `a.py` with an empty list.
    await repo.replace_file_references(
        snapshot_sha="s3", file_path="a.py", references=[]
    )
    await session.commit()

    # b.py's row is preserved.
    assert (
        await repo.count_by_symbol(symbol_name="foo", snapshot_sha="s3") == 1
    )


@pytest.mark.asyncio
async def test_count_by_symbol_isolated_by_snapshot(
    session: AsyncSession,
) -> None:
    repo = CodeReferencesRepository(session)
    await repo.replace_file_references(
        snapshot_sha="s4-a",
        file_path="a.py",
        references=[
            CodeReferenceRow(
                snapshot_sha="s4-a",
                file_path="a.py",
                symbol_name="foo",
                line_number=1,
                kind="call",
            ),
        ],
    )
    await repo.replace_file_references(
        snapshot_sha="s4-b",
        file_path="a.py",
        references=[
            CodeReferenceRow(
                snapshot_sha="s4-b",
                file_path="a.py",
                symbol_name="foo",
                line_number=1,
                kind="call",
            ),
            CodeReferenceRow(
                snapshot_sha="s4-b",
                file_path="a.py",
                symbol_name="foo",
                line_number=2,
                kind="call",
            ),
        ],
    )
    await session.commit()

    assert (
        await repo.count_by_symbol(symbol_name="foo", snapshot_sha="s4-a") == 1
    )
    assert (
        await repo.count_by_symbol(symbol_name="foo", snapshot_sha="s4-b") == 2
    )


@pytest.mark.asyncio
async def test_count_by_symbol_and_kind(session: AsyncSession) -> None:
    repo = CodeReferencesRepository(session)
    await repo.replace_file_references(
        snapshot_sha="s5",
        file_path="a.py",
        references=[
            CodeReferenceRow(
                snapshot_sha="s5",
                file_path="a.py",
                symbol_name="foo",
                line_number=1,
                kind="import",
            ),
            CodeReferenceRow(
                snapshot_sha="s5",
                file_path="a.py",
                symbol_name="foo",
                line_number=10,
                kind="call",
            ),
            CodeReferenceRow(
                snapshot_sha="s5",
                file_path="a.py",
                symbol_name="foo",
                line_number=20,
                kind="call",
            ),
            CodeReferenceRow(
                snapshot_sha="s5",
                file_path="a.py",
                symbol_name="foo",
                line_number=30,
                kind="string-literal",
            ),
        ],
    )
    await session.commit()

    by_kind = await repo.count_by_symbol_and_kind(
        symbol_name="foo", snapshot_sha="s5"
    )
    assert by_kind == {"import": 1, "call": 2, "string-literal": 1}


@pytest.mark.asyncio
async def test_replace_with_empty_list_is_legal(session: AsyncSession) -> None:
    """Pure delete (no new rows). Used when OpenGrep found nothing in a file
    on re-run after previously finding matches."""
    repo = CodeReferencesRepository(session)
    await repo.replace_file_references(
        snapshot_sha="s6",
        file_path="empty.py",
        references=[
            CodeReferenceRow(
                snapshot_sha="s6",
                file_path="empty.py",
                symbol_name="foo",
                line_number=1,
                kind="call",
            ),
        ],
    )
    await session.commit()
    assert (
        await repo.count_by_symbol(symbol_name="foo", snapshot_sha="s6") == 1
    )

    await repo.replace_file_references(
        snapshot_sha="s6", file_path="empty.py", references=[]
    )
    await session.commit()
    assert (
        await repo.count_by_symbol(symbol_name="foo", snapshot_sha="s6") == 0
    )
