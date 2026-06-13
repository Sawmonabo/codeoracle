"""
End-to-end bootstrap reproducibility — Plan 3 deliverables prove out together.

This test does NOT call bootstrap_db.sh or alembic upgrade itself (the conftest
session fixture already does). Instead it asserts that AFTER bootstrap +
migrate, the DB has the current 0006 shape and a cypher + vector + snapshot
round-trip all work."""

import pytest
from sqlalchemy import text
from sqlalchemy.engine import Engine
from sqlalchemy.ext.asyncio import AsyncSession

pytestmark = pytest.mark.integration


def test_core_tables_present(sync_engine: Engine):
    with sync_engine.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT table_schema, table_name "
                "FROM information_schema.tables "
                "WHERE table_schema IN ('public', 'traces') "
                "AND table_type='BASE TABLE'"
            )
        ).all()
    table_names = {(r[0], r[1]) for r in rows}
    assert ("public", "snapshots") in table_names
    assert ("public", "embeddings") in table_names
    assert ("public", "snapshot_file_outcomes") in table_names
    assert ("public", "code_references") in table_names
    assert ("traces", "spans") in table_names
    assert ("traces", "ingest_cursor") in table_names
    assert ("public", "traces") not in table_names


def test_alembic_version_is_head(sync_engine: Engine):
    """alembic_version should hold the latest revision id."""
    with sync_engine.connect() as conn:
        version = conn.execute(
            text("SELECT version_num FROM alembic_version")
        ).scalar()
    assert version == "0006"


def test_both_extensions_installed(sync_engine: Engine):
    with sync_engine.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT extname FROM pg_extension "
                "WHERE extname IN ('age', 'vector')"
            )
        ).all()
    names = {r[0] for r in rows}
    assert names == {"age", "vector"}


def test_age_codeoracle_graph_exists(sync_engine: Engine):
    with sync_engine.connect() as conn:
        names = conn.execute(
            text(
                "SELECT name FROM ag_catalog.ag_graph WHERE name='codeoracle'"
            )
        ).all()
    assert len(names) == 1


async def test_cypher_create_match_round_trip(async_session: AsyncSession):
    """Async path: create a node + edge, match, drop.

    No @pytest.mark.asyncio needed — pyproject.toml sets asyncio_mode = "auto".
    """
    await async_session.execute(
        text(
            "SELECT * FROM cypher('codeoracle', $$ "
            "CREATE (a:TestNode {name: 'x'}) "
            "CREATE (b:TestNode {name: 'y'}) "
            "CREATE (a)-[:LINKS_TO]->(b) "
            "$$) AS (v agtype)"
        )
    )
    rows = (
        await async_session.execute(
            text(
                "SELECT * FROM cypher('codeoracle', $$ "
                "MATCH (a:TestNode {name:'x'})-[:LINKS_TO]->(b:TestNode) "
                "RETURN b.name "
                "$$) AS (n agtype)"
            )
        )
    ).all()
    assert len(rows) == 1
    assert "y" in str(rows[0][0])
    # cleanup
    await async_session.execute(
        text(
            "SELECT * FROM cypher('codeoracle', $$ "
            "MATCH (n:TestNode) DETACH DELETE n "
            "$$) AS (v agtype)"
        )
    )


async def test_symbol_existence_check_plans_index_scan(
    async_session: AsyncSession,
):
    """The agent's hot symbol-existence check must plan an Index Scan, not a
    Seq Scan, on the Symbol label (NFR D3-1). bootstrap_db.sql pre-creates the
    Symbol/File vlabels + btree property indexes on the agtype property-access
    expression AGE emits for a `{fqn, snapshot_sha}` map-match; without them
    every `_EXISTS_CYPHER` (Plan 10) full-scans the whole label table once per
    user query.

    `enable_seqscan = off` makes the assertion deterministic at any table size:
    on a tiny test table PG's cost model would prefer a Seq Scan even with the
    index present, so forcing it off proves the index *can serve the predicate*
    (the property under test) rather than that it merely wins on cost today.
    """
    sha = "d3_1_idx_test"
    await async_session.execute(
        text(
            "SELECT * FROM cypher('codeoracle', $$ "
            f"CREATE (:Symbol {{fqn: 'pkg.mod.fn', snapshot_sha: '{sha}'}}) "
            "$$) AS (v agtype)"
        )
    )
    await async_session.execute(text("SET LOCAL enable_seqscan = off"))
    plan_rows = (
        await async_session.execute(
            text(
                "EXPLAIN SELECT * FROM cypher('codeoracle', $$ "
                "MATCH (s:Symbol "
                f"{{fqn: 'pkg.mod.fn', snapshot_sha: '{sha}'}}) "
                "RETURN s.fqn $$) AS (fqn agtype)"
            )
        )
    ).all()
    plan = "\n".join(str(r[0]) for r in plan_rows)
    assert "symbol_fqn_idx" in plan, (
        f"existence check did not use the fqn index:\n{plan}"
    )
    # cleanup — scope the delete to this test's sha so it can't touch other
    # tests' Symbol vertices in the session-scoped graph.
    await async_session.execute(
        text(
            "SELECT * FROM cypher('codeoracle', $$ "
            f"MATCH (n:Symbol {{snapshot_sha: '{sha}'}}) DETACH DELETE n "
            "$$) AS (v agtype)"
        )
    )


def test_full_snapshot_embedding_trace_insert(sync_engine: Engine):
    """
    Insert one row into each of the three Plan 3 tables; verify all readable;
    cleanup.
    """
    # Use a unique sha so this test doesn't collide with other tests' fixtures
    sha = "test_repro_sha"
    trace_id_hex = "01010101010101010101010101010101"
    span_id_hex = "0202020202020202"

    # snapshots
    with sync_engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO snapshots "
                "(sha, status, changed_files, extractor_results) "
                f"VALUES ('{sha}', 'applied', ARRAY['/x.py'], '{{}}')"
            )
        )

    # embeddings (1-dim test value works because we don't enforce dim at
    # insert; we just
    # need the vector column to accept *some* shape — full-dim is tested in
    # 0002 tests)
    # Read the actual dim from the column type:
    with sync_engine.connect() as conn:
        dim = conn.execute(
            text(
                "SELECT atttypmod FROM pg_attribute a "
                "JOIN pg_class c ON c.oid = a.attrelid "
                "WHERE c.relname='embeddings' AND a.attname='embedding'"
            )
        ).scalar()
        assert dim is not None
        assert dim > 0

    vec_literal = "[" + ",".join("0.0" for _ in range(dim)) + "]"
    with sync_engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO embeddings "
                "(snapshot_sha, source_path, chunk_start_line, "
                "chunk_end_line, content, embedding) "
                f"VALUES ('{sha}', '/x.py', 1, 10, 'x', '{vec_literal}')"
            )
        )

    # traces
    with sync_engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO traces.spans "
                "(trace_id, span_id, service_name, operation_name, kind, "
                "start_unix_nano, end_unix_nano, status_code, attributes) "
                f"VALUES (decode('{trace_id_hex}', 'hex'), "
                f"decode('{span_id_hex}', 'hex'), "
                "'api', '/v1/query', 'server', 1, 2, 'ok', '{}')"
            )
        )

    # cross-table check
    with sync_engine.connect() as conn:
        s_count = conn.execute(
            text(f"SELECT COUNT(*) FROM snapshots WHERE sha='{sha}'")
        ).scalar()
        e_count = conn.execute(
            text(f"SELECT COUNT(*) FROM embeddings WHERE snapshot_sha='{sha}'")
        ).scalar()
        t_count = conn.execute(
            text(
                "SELECT COUNT(*) FROM traces.spans "
                f"WHERE trace_id = decode('{trace_id_hex}', 'hex')"
            )
        ).scalar()
        assert s_count == 1
        assert e_count == 1
        assert t_count == 1

    # cleanup
    with sync_engine.begin() as conn:
        conn.execute(
            text(
                "DELETE FROM traces.spans "
                f"WHERE trace_id = decode('{trace_id_hex}', 'hex')"
            )
        )
        conn.execute(
            text(f"DELETE FROM embeddings WHERE snapshot_sha='{sha}'")
        )
        conn.execute(text(f"DELETE FROM snapshots WHERE sha='{sha}'"))
