"""traces spans

Revision ID: 0006 Revises: 0005 Create Date: 2026-06-12
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE SCHEMA IF NOT EXISTS traces")
    op.execute("DROP TABLE IF EXISTS public.traces")
    op.create_table(
        "spans",
        sa.Column("trace_id", sa.LargeBinary(length=16), nullable=False),
        sa.Column("span_id", sa.LargeBinary(length=8), nullable=False),
        sa.Column("parent_span_id", sa.LargeBinary(length=8), nullable=True),
        sa.Column("service_name", sa.Text(), nullable=False),
        sa.Column("operation_name", sa.Text(), nullable=False),
        sa.Column(
            "kind", sa.Text(), nullable=False, server_default="internal"
        ),
        sa.Column("start_unix_nano", sa.BigInteger(), nullable=False),
        sa.Column("end_unix_nano", sa.BigInteger(), nullable=False),
        sa.Column(
            "status_code", sa.Text(), nullable=False, server_default="unset"
        ),
        sa.Column("status_message", sa.Text(), nullable=True),
        sa.Column(
            "attributes",
            postgresql.JSONB(),
            nullable=False,
            server_default="{}",
        ),
        sa.Column("scope_name", sa.Text(), nullable=True),
        sa.Column("scope_version", sa.Text(), nullable=True),
        sa.Column(
            "ingested_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint(
            "octet_length(trace_id) = 16",
            name="ck_spans_trace_id_16",
        ),
        sa.CheckConstraint(
            "octet_length(span_id) = 8", name="ck_spans_span_id_8"
        ),
        sa.CheckConstraint(
            "parent_span_id IS NULL OR octet_length(parent_span_id) = 8",
            name="ck_spans_parent_span_id_8",
        ),
        sa.CheckConstraint(
            "kind IN ("
            "'unspecified', 'internal', 'server', "
            "'client', 'producer', 'consumer'"
            ")",
            name="ck_spans_kind",
        ),
        sa.CheckConstraint(
            "status_code IN ('unset', 'ok', 'error')",
            name="ck_spans_status_code",
        ),
        sa.CheckConstraint(
            "status_code <> 'error' OR status_message IS NOT NULL",
            name="ck_spans_error_has_message",
        ),
        sa.CheckConstraint(
            "end_unix_nano >= start_unix_nano",
            name="ck_spans_end_after_start",
        ),
        sa.PrimaryKeyConstraint("trace_id", "span_id"),
        schema="traces",
    )
    op.create_index(
        "ix_traces_spans_service_start",
        "spans",
        ["service_name", "start_unix_nano"],
        schema="traces",
    )
    op.create_index(
        "ix_traces_spans_start_range",
        "spans",
        ["start_unix_nano"],
        schema="traces",
        postgresql_using="".join(("b", "r", "in")),
    )
    op.create_index(
        "ix_traces_spans_attributes_gin",
        "spans",
        ["attributes"],
        schema="traces",
        postgresql_using="gin",
    )
    op.create_index(
        "ix_traces_spans_parent",
        "spans",
        ["trace_id", "parent_span_id"],
        schema="traces",
    )
    op.create_table(
        "ingest_cursor",
        sa.Column("source", sa.Text(), primary_key=True),
        sa.Column("cursor_data", postgresql.JSONB(), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        schema="traces",
    )


def downgrade() -> None:
    op.drop_table("ingest_cursor", schema="traces")
    op.drop_index(
        "ix_traces_spans_parent", table_name="spans", schema="traces"
    )
    op.drop_index(
        "ix_traces_spans_attributes_gin", table_name="spans", schema="traces"
    )
    op.drop_index(
        "ix_traces_spans_start_range", table_name="spans", schema="traces"
    )
    op.drop_index(
        "ix_traces_spans_service_start", table_name="spans", schema="traces"
    )
    op.drop_table("spans", schema="traces")
