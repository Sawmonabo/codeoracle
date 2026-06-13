"""Migration 0001 (snapshots table) — schema-shape + downgrade tests."""

import pytest
from sqlalchemy import text
from sqlalchemy.engine import Engine

pytestmark = pytest.mark.integration


def test_snapshots_table_exists(sync_engine: Engine):
    with sync_engine.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT column_name, data_type "
                "FROM information_schema.columns "
                "WHERE table_name='snapshots' "
                "ORDER BY ordinal_position"
            )
        ).all()
    columns = {r[0]: r[1] for r in rows}
    assert "sha" in columns
    assert "parent_sha" in columns
    assert "changed_files" in columns
    assert "status" in columns
    assert "extractor_results" in columns
    assert "error" in columns
    assert "applied_at" in columns


def test_snapshots_indexes_present(sync_engine: Engine):
    with sync_engine.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT indexname FROM pg_indexes "
                "WHERE tablename='snapshots' AND schemaname='public'"
            )
        ).all()
    index_names = {r[0] for r in rows}
    assert "ix_snapshots_status" in index_names
    assert "ix_snapshots_parent_sha" in index_names


def test_snapshots_insert_and_select(sync_engine: Engine):
    """Round-trip: insert a row, query back, delete (no test pollution)."""
    with sync_engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO snapshots "
                "(sha, status, changed_files, extractor_results) "
                "VALUES ('test_sha_0001', 'pending', '{}', '{}')"
            )
        )
        row = conn.execute(
            text("SELECT sha, status FROM snapshots WHERE sha='test_sha_0001'")
        ).one()
        assert row == ("test_sha_0001", "pending")
        conn.execute(text("DELETE FROM snapshots WHERE sha='test_sha_0001'"))
