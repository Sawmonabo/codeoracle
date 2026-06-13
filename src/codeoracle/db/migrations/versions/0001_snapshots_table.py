"""snapshots table

Revision ID: 0001 Revises: None Create Date: 2026-05-21
"""

import sqlalchemy as sa
from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "snapshots",
        sa.Column("sha", sa.String(length=80), nullable=False),
        sa.Column("parent_sha", sa.String(length=80), nullable=True),
        sa.Column("changed_files", sa.ARRAY(sa.String()), nullable=False),
        sa.Column(
            "status",
            sa.String(length=16),
            nullable=False,
            server_default="pending",
        ),
        sa.Column(
            "extractor_results",
            sa.JSON(),
            nullable=False,
            server_default="{}",
        ),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("applied_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("sha", name="pk_snapshots"),
    )
    op.create_index("ix_snapshots_status", "snapshots", ["status"])
    op.create_index("ix_snapshots_parent_sha", "snapshots", ["parent_sha"])


def downgrade() -> None:
    op.drop_index("ix_snapshots_parent_sha", "snapshots")
    op.drop_index("ix_snapshots_status", "snapshots")
    op.drop_table("snapshots")
