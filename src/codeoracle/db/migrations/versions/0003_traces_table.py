"""traces table

Revision ID: 0003 Revises: 0002 Create Date: 2026-05-21

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "traces",
        sa.Column("trace_id", sa.String(64), primary_key=True),
        sa.Column("span_id", sa.String(64), primary_key=True),
        sa.Column("parent_span_id", sa.String(64), nullable=True),
        sa.Column("service_name", sa.String(256), nullable=False),
        sa.Column("operation_name", sa.String(256), nullable=False),
        sa.Column("start_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("end_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("attributes", sa.JSON, nullable=False, server_default="{}"),
        sa.Column("snapshot_sha", sa.String(80), nullable=True),
    )
    op.create_index(
        "ix_traces_service_op", "traces", ["service_name", "operation_name"]
    )
    op.create_index("ix_traces_snapshot_sha", "traces", ["snapshot_sha"])
    op.create_index("ix_traces_start_time", "traces", ["start_time"])


def downgrade() -> None:
    op.drop_index("ix_traces_start_time", "traces")
    op.drop_index("ix_traces_snapshot_sha", "traces")
    op.drop_index("ix_traces_service_op", "traces")
    op.drop_table("traces")
