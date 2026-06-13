"""code_references: per-snapshot textual references from OpenGrep.

Revision ID: 0005 Revises: 0004 Create Date: 2026-05-21

The table stores results from `workers/opengrep_runner` (Plan 6). Each row is
one textual reference of one kind at one line. `kind` is one of:

- "import"           — Python `from X import Y` / JavaScript `import Y from
  "X"`.
- "call"             — Function/method invocation at this line.
- "string-literal"   — String literal containing the symbol name.
- "comment-mention"  — Comment or docstring mentioning the symbol name.

Plan 9's verifier queries `count_by_symbol(symbol_name, snapshot_sha)` to
cross-validate Joern/SCIP claims about how often a symbol is referenced.
"""

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
# Bare-numeric convention — matches Plan 3 ("0001"/"0002"/"0003") + Plan 5
# ("0004") + Plan 7 ("0006"). The slug-suffix form ("0005_code_references")
# breaks the down_revision chain because Plan 7's down_revision = "0005"
# and Plan 5's revision = "0004"; alembic will fail to walk the chain.
# See Plan 5's "Cross-plan contracts" §2 for the convention statement.
revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


_ALLOWED_KINDS = ("import", "call", "string-literal", "comment-mention")


def upgrade() -> None:
    op.create_table(
        "code_references",
        sa.Column("snapshot_sha", sa.String(length=64), nullable=False),
        sa.Column("file_path", sa.String(length=1024), nullable=False),
        sa.Column("symbol_name", sa.String(length=512), nullable=False),
        sa.Column("line_number", sa.Integer, nullable=False),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint(
            "snapshot_sha",
            "file_path",
            "symbol_name",
            "line_number",
            "kind",
            name="pk_code_references",
        ),
        sa.CheckConstraint(
            "kind IN ('import', 'call', 'string-literal', 'comment-mention')",
            name="ck_code_references_kind",
        ),
        sa.CheckConstraint(
            "line_number >= 1",
            name="ck_code_references_line_positive",
        ),
    )
    op.create_index(
        "ix_code_references_symbol_lookup",
        "code_references",
        ["symbol_name", "snapshot_sha"],
    )
    op.create_index(
        "ix_code_references_snapshot",
        "code_references",
        ["snapshot_sha"],
    )


def downgrade() -> None:
    op.drop_index("ix_code_references_snapshot", table_name="code_references")
    op.drop_index(
        "ix_code_references_symbol_lookup", table_name="code_references"
    )
    op.drop_table("code_references")
