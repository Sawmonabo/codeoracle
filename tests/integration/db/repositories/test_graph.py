"""Plan 5 Task 8: GraphRepository integration tests against AGE-loaded PG."""

import pytest

from codeoracle.core.exceptions import GraphQueryError
from codeoracle.db.repositories.graph import GraphRepository

pytestmark = pytest.mark.integration


async def test_execute_cypher_returns_rows(async_session):
    repo = GraphRepository(async_session)
    rows = await repo.execute_cypher(
        "CREATE (n:Test {value:1}) RETURN n.value AS v",
        column_spec="v agtype",
    )
    assert rows
    assert str(rows[0]["v"]).strip('"') == "1"
    # Cleanup
    await repo.execute_cypher("MATCH (n:Test) DETACH DELETE n")


async def test_replace_file_subgraph_deletes_old_then_inserts_new(
    async_session,
):
    repo = GraphRepository(async_session)

    # Seed: insert one node with snapshot_sha='v1'
    await repo.execute_cypher(
        "CREATE (n:File {snapshot_sha:'v1', file_path:'src/x.py', "
        "marker:'old'}) RETURN n",
        column_spec="n agtype",
    )

    # Replace with a new node for the same (snapshot_sha, file_path)
    new_inserts = [
        "CREATE (n:File {snapshot_sha:'v1', file_path:'src/x.py', "
        "marker:'new'}) RETURN n"
    ]
    await repo.replace_file_subgraph(
        snapshot_sha="v1",
        file_path="src/x.py",
        cypher_inserts=new_inserts,
    )

    # Old node gone; new node present
    rows = await repo.execute_cypher(
        "MATCH (n:File {snapshot_sha:'v1', file_path:'src/x.py'}) "
        "RETURN n.marker AS m",
        column_spec="m agtype",
    )
    assert len(rows) == 1
    assert str(rows[0]["m"]).strip('"') == "new"

    # Cleanup
    await repo.execute_cypher(
        "MATCH (n:File {snapshot_sha:'v1'}) DETACH DELETE n"
    )


async def test_replace_file_subgraph_leaves_other_files_alone(async_session):
    repo = GraphRepository(async_session)
    await repo.execute_cypher(
        "CREATE (a:File {snapshot_sha:'v2', file_path:'src/a.py'}), "
        "       (b:File {snapshot_sha:'v2', file_path:'src/b.py'})"
    )

    # Replace only src/a.py
    await repo.replace_file_subgraph(
        snapshot_sha="v2",
        file_path="src/a.py",
        cypher_inserts=[
            "CREATE (a:File {snapshot_sha:'v2', file_path:'src/a.py', "
            "new:'yes'}) RETURN a"
        ],
    )

    # src/b.py untouched
    rows = await repo.execute_cypher(
        "MATCH (n:File {snapshot_sha:'v2', file_path:'src/b.py'}) "
        "RETURN n.file_path AS p",
        column_spec="p agtype",
    )
    assert len(rows) == 1

    # Cleanup
    await repo.execute_cypher(
        "MATCH (n:File {snapshot_sha:'v2'}) DETACH DELETE n"
    )


async def test_find_callers_returns_distinct_paths(async_session):
    repo = GraphRepository(async_session)
    # Valid §3.7 topology: a target :Symbol; caller :Symbols DEFINED by
    # src/a.py and src/b.py that each CALL the target; src/c.py defines no
    # caller. A "caller file" is the file that DEFINES a symbol calling the
    # target — so the result is {src/a.py, src/b.py}.
    await repo.execute_cypher(
        "CREATE (t:Symbol {fqn:'pkg.target', file_path:'src/t.py', "
        "snapshot_sha:'v3'}), "
        "       (ca:Symbol {fqn:'pkg.ca', file_path:'src/a.py', "
        "snapshot_sha:'v3'}), "
        "       (cb:Symbol {fqn:'pkg.cb', file_path:'src/b.py', "
        "snapshot_sha:'v3'}), "
        "       (fa:File {file_path:'src/a.py', snapshot_sha:'v3'}), "
        "       (fb:File {file_path:'src/b.py', snapshot_sha:'v3'}), "
        "       (fc:File {file_path:'src/c.py', snapshot_sha:'v3'}), "
        "       (fa)-[:DEFINES]->(ca), "
        "       (fb)-[:DEFINES]->(cb), "
        "       (ca)-[:CALLS]->(t), "
        "       (cb)-[:CALLS]->(t)"
    )
    paths = await repo.find_callers(symbol_fqn="pkg.target", snapshot_sha="v3")
    assert set(paths) == {"src/a.py", "src/b.py"}

    # Cleanup
    await repo.execute_cypher(
        "MATCH (n) WHERE n.snapshot_sha='v3' DETACH DELETE n"
    )


async def test_link_cross_file_symbols_resolves_and_retains(async_session):
    repo = GraphRepository(async_session)
    # Seed: a caller (file_path set) -[:CALLS]-> an UNRESOLVED callee
    # (file_path NULL) whose fqn DOES have a resolved match; a resolved
    # :Symbol with that same fqn (file_path set); and a second unresolved
    # callee whose fqn has NO resolved match (external — stdlib/3rd-party).
    await repo.execute_cypher(
        "CREATE (caller:Symbol {fqn:'pkg.caller', file_path:'src/caller.py', "
        "snapshot_sha:'v4'}), "
        "       (unresolved:Symbol {fqn:'pkg.callee', snapshot_sha:'v4'}), "
        "       (resolved:Symbol {fqn:'pkg.callee', "
        "file_path:'src/callee.py', "
        "snapshot_sha:'v4'}), "
        "       (ext:Symbol {fqn:'os.path.join', snapshot_sha:'v4'}), "
        "       (caller)-[:CALLS]->(unresolved), "
        "       (caller)-[:CALLS]->(ext)"
    )

    await repo.link_cross_file_symbols("v4")

    # (a) the CALLS edge now points to the RESOLVED node (file_path set)
    resolved_rows = await repo.execute_cypher(
        "MATCH (c:Symbol {fqn:'pkg.caller', snapshot_sha:'v4'})-[:CALLS]->"
        "(t:Symbol {fqn:'pkg.callee', snapshot_sha:'v4'}) "
        "WHERE t.file_path IS NOT NULL "
        "RETURN t.file_path AS p",
        column_spec="p agtype",
    )
    assert len(resolved_rows) == 1
    assert str(resolved_rows[0]["p"]).strip('"') == "src/callee.py"

    # (b) the consumed unresolved placeholder for pkg.callee is deleted —
    # exactly one :Symbol with that fqn survives (the resolved one).
    callee_rows = await repo.execute_cypher(
        "MATCH (s:Symbol {fqn:'pkg.callee', snapshot_sha:'v4'}) "
        "RETURN s.file_path AS p",
        column_spec="p agtype",
    )
    assert len(callee_rows) == 1
    assert str(callee_rows[0]["p"]).strip('"') == "src/callee.py"

    # (c) the external unresolved :Symbol is RETAINED with file_path NULL,
    # and the caller's CALLS edge into it is untouched.
    ext_rows = await repo.execute_cypher(
        "MATCH (c:Symbol {fqn:'pkg.caller', snapshot_sha:'v4'})-[:CALLS]->"
        "(e:Symbol {fqn:'os.path.join', snapshot_sha:'v4'}) "
        "WHERE e.file_path IS NULL "
        "RETURN count(e) AS n",
        column_spec="n agtype",
    )
    assert str(ext_rows[0]["n"]).strip('"') == "1"

    # (d) idempotent — a second run is a no-op (no new edges, no deletions).
    await repo.link_cross_file_symbols("v4")
    after_rows = await repo.execute_cypher(
        "MATCH (c:Symbol {fqn:'pkg.caller', snapshot_sha:'v4'})"
        "-[:CALLS]->(t) RETURN t.fqn AS f",
        column_spec="f agtype",
    )
    assert {str(r["f"]).strip('"') for r in after_rows} == {
        "pkg.callee",
        "os.path.join",
    }

    # Cleanup
    await repo.execute_cypher(
        "MATCH (n) WHERE n.snapshot_sha='v4' DETACH DELETE n"
    )


async def test_execute_cypher_wraps_errors_as_graph_query_error(async_session):
    repo = GraphRepository(async_session)
    with pytest.raises(GraphQueryError, match="execution failed"):
        await repo.execute_cypher("THIS IS NOT VALID CYPHER")
