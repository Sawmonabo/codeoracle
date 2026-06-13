"""Plan 5 Task 10: DeltaComputer integration tests against AGE-loaded PG.

The unit suite (test_delta.py) mocks execute_cypher; this proves spec §8.4's
two-pattern dependent-discovery Cypher against a real graph — the
`:CALLS`-via-`:Symbol` UNION `:IMPORTS` File→File traversal and its
`WHERE NOT dep.file_path IN $changed_files` exclusion, including the
list-valued
$changed_files bind that find_callers (scalar params only) never reaches.
"""

import subprocess
from pathlib import Path

import pytest

from codeoracle.db.repositories.graph import GraphRepository
from codeoracle.services.indexing.delta import DeltaComputer

pytestmark = pytest.mark.integration


@pytest.fixture
def tmp_git_repo(tmp_path: Path) -> Path:
    """
    Minimal git repo in tmp_path (mirrors the test_delta.py unit fixture).
    """
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


async def test_compute_resolves_dependents_via_both_patterns_excluding_changed(
    async_session, tmp_git_repo: Path
):
    """End-to-end §8.4 dependent discovery against real AGE.

    Graph at the base snapshot (sha = the base commit):
      changed.py     -[:DEFINES]-> pkg.target
      caller.py      -[:DEFINES]-> pkg.caller -[:CALLS]-> pkg.target
      (pattern 1)
      both.py        -[:DEFINES]-> pkg.both   -[:CALLS]-> pkg.target +
                     both.py      -[:IMPORTS]-> changed.py              (both
                     patterns)
      importer.py    -[:IMPORTS]-> changed.py                          (pattern
      2) alsochanged.py -[:DEFINES]-> pkg.alsoc  -[:CALLS]-> pkg.target
      (pattern 1, but alsochanged.py is itself in the changeset → excluded)
      unrelated.py   -[:DEFINES]-> pkg.unrel  (no edge to target)       (not a
      dependent)

    Two committed edits make changed_files = {changed.py, alsochanged.py}, so
    the resolved dependents are {caller.py, importer.py, both.py}:
      - caller.py via the CALLS pattern,
      - importer.py via the IMPORTS pattern,
      - both.py via BOTH but returned once (UNION is DISTINCT),
      - alsochanged.py suppressed by `NOT dep.file_path IN $changed_files`
        though it calls the target,
      - unrelated.py never traversed.
    """
    base = _commit(
        tmp_git_repo,
        {
            "changed.py": "v = 1",
            "alsochanged.py": "v = 1",
            "caller.py": "import changed",
            "importer.py": "import changed",
            "both.py": "import changed",
            "unrelated.py": "v = 1",
        },
        "base",
    )
    _commit(
        tmp_git_repo,
        {"changed.py": "v = 2", "alsochanged.py": "v = 2"},
        "edit the two changed files",
    )

    # Seed the base-snapshot subgraph. The literal snapshot_sha (the git base
    # SHA
    # spliced in for __SHA__) mirrors test_graph.py's literal-seed idiom, so
    # the
    # only parameter-bound value under test is compute()'s query side — exactly
    # the list-valued $changed_files bind the mocked unit tests cannot
    # exercise.
    # File nodes carry `file_path` (the parser's per-file partition key, the
    # property the §8.4 query matches on), seeded with the same root-relative
    # names `git diff` emits, so the `$changed_files` IN-match holds by
    # construction.
    seed = (
        "CREATE "
        "(fchanged:File {file_path:'changed.py', snapshot_sha:'__SHA__'}), "
        "(fcaller:File {file_path:'caller.py', snapshot_sha:'__SHA__'}), "
        "(fimporter:File {file_path:'importer.py', snapshot_sha:'__SHA__'}), "
        "(fboth:File {file_path:'both.py', snapshot_sha:'__SHA__'}), "
        "(fa:File {file_path:'alsochanged.py', snapshot_sha:'__SHA__'}), "
        "(fu:File {file_path:'unrelated.py', snapshot_sha:'__SHA__'}), "
        "(target:Symbol {fqn:'pkg.target', file_path:'changed.py', "
        "snapshot_sha:'__SHA__'}), "
        "(scaller:Symbol {fqn:'pkg.caller', file_path:'caller.py', "
        "snapshot_sha:'__SHA__'}), "
        "(sboth:Symbol {fqn:'pkg.both', file_path:'both.py', "
        "snapshot_sha:'__SHA__'}), "
        "(salso:Symbol {fqn:'pkg.alsoc', file_path:'alsochanged.py', "
        "snapshot_sha:'__SHA__'}), "
        "(sunrel:Symbol {fqn:'pkg.unrel', file_path:'unrelated.py', "
        "snapshot_sha:'__SHA__'}), "
        "(fchanged)-[:DEFINES]->(target), "
        "(fcaller)-[:DEFINES]->(scaller), "
        "(fboth)-[:DEFINES]->(sboth), "
        "(fa)-[:DEFINES]->(salso), "
        "(fu)-[:DEFINES]->(sunrel), "
        "(scaller)-[:CALLS]->(target), "
        "(sboth)-[:CALLS]->(target), "
        "(salso)-[:CALLS]->(target), "
        "(fimporter)-[:IMPORTS]->(fchanged), "
        "(fboth)-[:IMPORTS]->(fchanged)"
    )
    repo = GraphRepository(async_session)
    await repo.execute_cypher(seed.replace("__SHA__", base))

    cs = await DeltaComputer(graph_repo=repo, repo_root=tmp_git_repo).compute(
        base_sha=base
    )

    assert set(cs.changed_files) == {"changed.py", "alsochanged.py"}
    assert set(cs.dependent_files) == {"caller.py", "importer.py", "both.py"}

    # Cleanup — the AGE graph is shared across the session-scoped container.
    await repo.execute_cypher(
        "MATCH (n) WHERE n.snapshot_sha=$sha DETACH DELETE n", {"sha": base}
    )
