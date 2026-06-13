"""Migration 0002 (embeddings table) — schema-shape + vector round-trip."""

import os
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.engine import Engine

pytestmark = pytest.mark.integration

REPO_ROOT = Path(__file__).resolve().parents[2]


def _expected_dim() -> int:
    """
    Read the dim from infra/versions/pinned.env — the project's source of
    truth.

    Migration filenames are digit-prefixed (`0002_embeddings_table.py`), which
    are not valid Python identifiers, so we cannot import them as modules.
    Parsing pinned.env directly avoids that constraint entirely.
    """
    env_override = os.environ.get("OPENAI_EMBEDDING_DIM")
    if env_override:
        return int(env_override)
    pinned_env = REPO_ROOT / "infra" / "versions" / "pinned.env"
    if not pinned_env.exists():
        pytest.fail(f"pinned.env not found at {pinned_env} — Plan 2 not run?")
    for line in pinned_env.read_text().splitlines():
        if line.startswith("OPENAI_EMBEDDING_DIM="):
            return int(line.split("=", 1)[1].strip())
    pytest.fail("OPENAI_EMBEDDING_DIM not present in pinned.env")


def test_embeddings_table_exists(sync_engine: Engine):
    with sync_engine.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_name='embeddings' ORDER BY ordinal_position"
            )
        ).all()
    columns = {r[0] for r in rows}
    assert {
        "id",
        "snapshot_sha",
        "source_path",
        "chunk_start_line",
        "chunk_end_line",
        "content",
        "embedding",
    }.issubset(columns)


def test_embedding_column_dim_matches_pinned_env(sync_engine: Engine):
    """
    The most load-bearing assertion of Task 10: vector(N) must match
    pinned.env.
    """
    with sync_engine.connect() as conn:
        dim = conn.execute(
            text(
                "SELECT atttypmod FROM pg_attribute a "
                "JOIN pg_class c ON c.oid=a.attrelid "
                "WHERE c.relname='embeddings' AND a.attname='embedding'"
            )
        ).scalar()
    assert dim == _expected_dim(), (
        f"embeddings.embedding column dim ({dim}) does not match pinned.env "
        f"OPENAI_EMBEDDING_DIM ({_expected_dim()}). Migration 0002's "
        "EMBEDDING_DIM constant is the source of truth — a follow-up "
        "migration is required to change it."
    )


def test_hnsw_index_exists(sync_engine: Engine):
    with sync_engine.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT indexname FROM pg_indexes "
                "WHERE tablename='embeddings' "
                "AND indexname='ix_embeddings_embedding_hnsw'"
            )
        ).all()
    assert len(rows) == 1


def test_embedding_round_trip(sync_engine: Engine):
    """
    Insert a vector, query it back, delete. Proves the column accepts the right
    shape.
    """
    dim = _expected_dim()
    vec = [0.0] * dim
    vec[0] = 1.0  # unit-length on axis 0
    vec_literal = "[" + ",".join(str(x) for x in vec) + "]"

    with sync_engine.begin() as conn:
        # Use a unique snapshot_sha that won't collide
        conn.execute(
            text(
                "INSERT INTO embeddings "
                "(snapshot_sha, source_path, chunk_start_line, "
                "chunk_end_line, content, embedding) "
                "VALUES ('test_emb_0002', '/test.py', 1, 10, "
                f"'hello', '{vec_literal}')"
            )
        )
        result = conn.execute(
            text(
                "SELECT source_path FROM embeddings "
                "WHERE snapshot_sha='test_emb_0002'"
            )
        ).one()
        assert result[0] == "/test.py"
        conn.execute(
            text("DELETE FROM embeddings WHERE snapshot_sha='test_emb_0002'")
        )


def test_ann_query_works(sync_engine: Engine):
    """Insert two vectors, ANN-query for the closer one."""
    dim = _expected_dim()
    vec_a = [0.0] * dim
    vec_a[0] = 1.0
    vec_b = [0.0] * dim
    vec_b[1] = 1.0  # orthogonal to A
    vec_query = [0.0] * dim
    vec_query[0] = 0.9  # closer to A

    def _lit(v: list[float]) -> str:
        return "[" + ",".join(str(x) for x in v) + "]"

    with sync_engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO embeddings "
                "(snapshot_sha, source_path, chunk_start_line, "
                "chunk_end_line, content, embedding) "
                "VALUES "
                f"('test_ann', '/a.py', 1, 10, 'a', '{_lit(vec_a)}'), "
                f"('test_ann', '/b.py', 1, 10, 'b', '{_lit(vec_b)}')"
            )
        )
        # Cosine distance ordering: A should rank first
        result = conn.execute(
            text(
                "SELECT source_path FROM embeddings "
                "WHERE snapshot_sha='test_ann' "
                f"ORDER BY embedding <=> '{_lit(vec_query)}' "
                "LIMIT 2"
            )
        ).all()
        paths = [r[0] for r in result]
        assert paths == ["/a.py", "/b.py"]
        conn.execute(
            text("DELETE FROM embeddings WHERE snapshot_sha='test_ann'")
        )
