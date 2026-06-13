"""embeddings table

Revision ID: 0002 Revises: 0001 Create Date: 2026-05-21

The pgvector column dimension is hardcoded here to match the value pinned in
infra/versions/pinned.env at the time this migration was authored. To change
the dimension, write a NEW migration that runs ALTER TYPE on the column.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# EMBEDDING_DIM is the source of truth for this migration once committed.
# It is initially set to 1536 (text-embedding-3-small native dim from Plan 2),
# then auto-synced from infra/versions/pinned.env by Plan 3 Task 10 Step 3a
# BEFORE the first `alembic upgrade head` runs.
#
# After this migration is applied to any environment, the constant is frozen.
# To change the dim, write a NEW migration that ALTERs the column type — never
# edit this file retroactively.
EMBEDDING_DIM = 1536


def upgrade() -> None:
    op.create_table(
        "embeddings",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("snapshot_sha", sa.String(80), nullable=False),
        sa.Column("source_path", sa.String(1024), nullable=False),
        sa.Column("chunk_start_line", sa.Integer, nullable=False),
        sa.Column("chunk_end_line", sa.Integer, nullable=False),
        sa.Column("content", sa.Text, nullable=False),
        sa.Column("embedding", Vector(EMBEDDING_DIM), nullable=False),
    )
    op.create_index(
        "ix_embeddings_snapshot_sha",
        "embeddings",
        ["snapshot_sha"],
    )
    # HNSW index for ANN queries (Plan 9 Verifier will use this).
    # m=16, ef_construction=64 are pgvector defaults — appropriate for slice
    # scale.
    op.execute(
        "CREATE INDEX ix_embeddings_embedding_hnsw "
        "ON embeddings USING hnsw (embedding vector_cosine_ops) "
        "WITH (m = 16, ef_construction = 64);"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_embeddings_embedding_hnsw;")
    op.drop_index("ix_embeddings_snapshot_sha", "embeddings")
    op.drop_table("embeddings")
