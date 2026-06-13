"""snapshot file outcomes

Revision ID: 0004 Revises: 0003 Create Date: 2026-06-12
"""

import sqlalchemy as sa
from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "snapshot_file_outcomes",
        sa.Column("snapshot_sha", sa.Text(), nullable=False),
        sa.Column("file_path", sa.Text(), nullable=False),
        sa.Column("extractor", sa.Text(), nullable=False),
        sa.Column("outcome", sa.Text(), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column(
            "applied_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint(
            "outcome IN ('success', 'failed', 'skipped')",
            name="ck_snapshot_file_outcomes_outcome",
        ),
        sa.PrimaryKeyConstraint(
            "snapshot_sha",
            "file_path",
            "extractor",
            name="pk_snapshot_file_outcomes",
        ),
    )
    op.create_index(
        "ix_snapshot_file_outcomes_barrier",
        "snapshot_file_outcomes",
        ["snapshot_sha", "extractor"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_snapshot_file_outcomes_barrier", "snapshot_file_outcomes"
    )
    op.drop_table("snapshot_file_outcomes")
