"""Migration 0003 (traces table) — schema + insert round-trip."""

from datetime import UTC, datetime

import pytest
from sqlalchemy import text
from sqlalchemy.engine import Engine

pytestmark = pytest.mark.integration


def test_traces_table_exists(sync_engine: Engine):
    with sync_engine.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_name='traces' ORDER BY ordinal_position"
            )
        ).all()
    columns = {r[0] for r in rows}
    assert {
        "trace_id",
        "span_id",
        "parent_span_id",
        "service_name",
        "operation_name",
        "start_time",
        "end_time",
        "attributes",
        "snapshot_sha",
    }.issubset(columns)


def test_traces_composite_primary_key(sync_engine: Engine):
    """Same trace_id, different span_id → both rows must be insertable."""
    with sync_engine.begin() as conn:
        now = datetime.now(UTC).isoformat()
        conn.execute(
            text(
                "INSERT INTO traces "
                "(trace_id, span_id, service_name, operation_name, "
                "start_time, end_time, attributes) "
                "VALUES ('t1', 's1', 'api', 'POST /v1/query', "
                f"'{now}', '{now}', '{{}}')"
            )
        )
        conn.execute(
            text(
                "INSERT INTO traces "
                "(trace_id, span_id, service_name, operation_name, "
                "start_time, end_time, attributes) "
                "VALUES ('t1', 's2', 'agent', 'compose', "
                f"'{now}', '{now}', '{{}}')"
            )
        )
        count = conn.execute(
            text("SELECT COUNT(*) FROM traces WHERE trace_id='t1'")
        ).scalar()
        assert count == 2
        conn.execute(text("DELETE FROM traces WHERE trace_id='t1'"))


def test_traces_indexes_present(sync_engine: Engine):
    with sync_engine.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT indexname FROM pg_indexes "
                "WHERE tablename='traces' AND schemaname='public'"
            )
        ).all()
    index_names = {r[0] for r in rows}
    for ix in (
        "ix_traces_service_op",
        "ix_traces_snapshot_sha",
        "ix_traces_start_time",
    ):
        assert ix in index_names
