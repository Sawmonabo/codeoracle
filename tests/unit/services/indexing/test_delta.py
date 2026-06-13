"""Plan 5 Task 10: DeltaComputer unit tests.

Pure tests for the git+hash logic. Subprocess to git is real (uses a
temp git repo); dependent discovery is mocked (no real DB).
"""

import subprocess
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from codeoracle.services.indexing.delta import DeltaComputer


@pytest.fixture
def tmp_git_repo(tmp_path: Path) -> Path:
    """Initialize a minimal git repo in tmp_path."""
    subprocess.run(
        ["git", "init"], cwd=tmp_path, check=True, capture_output=True
    )
    subprocess.run(
        ["git", "config", "user.email", "test@example.com"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Test"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
    )
    return tmp_path


def _commit(repo: Path, files: dict[str, str], msg: str) -> str:
    """Write files, add, commit. Return the new HEAD SHA."""
    for rel, content in files.items():
        p = repo / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content)
    subprocess.run(
        ["git", "add", "."], cwd=repo, check=True, capture_output=True
    )
    subprocess.run(
        ["git", "commit", "-m", msg],
        cwd=repo,
        check=True,
        capture_output=True,
    )
    return subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


async def test_clean_workdir_returns_head_sha(tmp_git_repo: Path):
    sha = _commit(tmp_git_repo, {"a.py": "x = 1"}, "initial")

    graph = MagicMock()
    graph.execute_cypher = AsyncMock(return_value=[])
    dc = DeltaComputer(graph_repo=graph, repo_root=tmp_git_repo)

    cs = await dc.compute(base_sha=None)
    assert cs.new_snapshot_sha == sha
    assert cs.is_dirty is False
    assert "a.py" in cs.changed_files


async def test_dirty_workdir_appends_wt_digest(tmp_git_repo: Path):
    sha = _commit(tmp_git_repo, {"a.py": "x = 1"}, "initial")
    # Modify without committing
    (tmp_git_repo / "a.py").write_text("x = 2")

    graph = MagicMock()
    graph.execute_cypher = AsyncMock(return_value=[])
    dc = DeltaComputer(graph_repo=graph, repo_root=tmp_git_repo)

    cs = await dc.compute(base_sha=sha)
    assert cs.is_dirty is True
    assert cs.new_snapshot_sha.startswith(f"{sha}+wt")
    # Suffix is exactly 12 hex chars per spec
    suffix = cs.new_snapshot_sha.split("+wt", 1)[1]
    assert len(suffix) == 12
    assert all(c in "0123456789abcdef" for c in suffix)


async def test_dirty_digest_is_content_addressed(tmp_git_repo: Path):
    """Same modification → same digest. Different modification → different."""
    sha = _commit(tmp_git_repo, {"a.py": "x = 1"}, "initial")

    graph = MagicMock()
    graph.execute_cypher = AsyncMock(return_value=[])
    dc = DeltaComputer(graph_repo=graph, repo_root=tmp_git_repo)

    (tmp_git_repo / "a.py").write_text("x = 2")
    cs1 = await dc.compute(base_sha=sha)

    (tmp_git_repo / "a.py").write_text("x = 2")  # same content
    cs2 = await dc.compute(base_sha=sha)
    assert cs1.new_snapshot_sha == cs2.new_snapshot_sha

    (tmp_git_repo / "a.py").write_text("x = 3")  # different
    cs3 = await dc.compute(base_sha=sha)
    assert cs3.new_snapshot_sha != cs1.new_snapshot_sha


async def test_committed_diff_against_base(tmp_git_repo: Path):
    base = _commit(tmp_git_repo, {"a.py": "x = 1", "b.py": "y = 2"}, "initial")
    head = _commit(tmp_git_repo, {"b.py": "y = 3", "c.py": "z = 4"}, "two")

    graph = MagicMock()
    graph.execute_cypher = AsyncMock(return_value=[])
    dc = DeltaComputer(graph_repo=graph, repo_root=tmp_git_repo)

    cs = await dc.compute(base_sha=base)
    assert cs.head_sha == head
    assert cs.new_snapshot_sha == head
    assert set(cs.changed_files) == {"b.py", "c.py"}
    assert cs.dependent_files == []


async def test_dependents_excluded_when_in_changed(tmp_git_repo: Path):
    base = _commit(tmp_git_repo, {"a.py": "x = 1", "b.py": "y = 1"}, "initial")
    _commit(tmp_git_repo, {"a.py": "x = 2"}, "modify")

    # Dependent discovery is spec §8.4's two-pattern Cypher. Its
    # `WHERE NOT dep.file_path IN $changed_files` clause excludes changed
    # files IN-QUERY (the exclusion moved from Python to Cypher when the
    # query was conformed to §8.4 — integration-tested against real AGE),
    # so the DB never returns a changed file. The mock simulates that
    # filtered output: b.py (unchanged) depends on the changed a.py and
    # is returned; a.py is not. compute() surfaces the query's dependents
    # verbatim and adds no changed file back.
    graph = MagicMock()
    graph.execute_cypher = AsyncMock(return_value=[{"path": '"b.py"'}])
    dc = DeltaComputer(graph_repo=graph, repo_root=tmp_git_repo)

    cs = await dc.compute(base_sha=base)
    assert "a.py" in cs.changed_files
    assert "b.py" in cs.dependent_files  # external dependent surfaces
    assert (
        "a.py" not in cs.dependent_files
    )  # changed file never double-counted


async def test_base_none_returns_all_tracked_files(tmp_git_repo: Path):
    _commit(tmp_git_repo, {"a.py": "1", "b.py": "2", "c/d.py": "3"}, "initial")

    graph = MagicMock()
    graph.execute_cypher = AsyncMock(return_value=[])
    dc = DeltaComputer(graph_repo=graph, repo_root=tmp_git_repo)

    cs = await dc.compute(base_sha=None)
    assert set(cs.changed_files) == {"a.py", "b.py", "c/d.py"}
    # No dependent discovery for full reindex
    assert cs.dependent_files == []
    graph.execute_cypher.assert_not_awaited()
