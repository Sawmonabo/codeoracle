"""CodeReferenceRecord: per-snapshot textual reference row.

Plan 6 — backed by migration 0005. Composite primary key spans all five
columns: (snapshot_sha, file_path, symbol_name, line_number, kind).

Idempotency: writers (`workers/opengrep_runner`) use `CodeReferencesRepository`
which performs DELETE-WHERE + INSERT inside one transaction per file. This
guarantees no duplicates across re-runs of the same file in the same snapshot.
"""

from datetime import datetime
from typing import Literal

from sqlalchemy import CheckConstraint, DateTime, Integer, String
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from codeoracle.db.base import Base

CodeReferenceKind = Literal[
    "import", "call", "string-literal", "comment-mention"
]


class CodeReferenceRecord(Base):
    __tablename__ = "code_references"
    __table_args__ = (
        CheckConstraint(
            "kind IN ('import', 'call', 'string-literal', 'comment-mention')",
            name="ck_code_references_kind",
        ),
        CheckConstraint(
            "line_number >= 1", name="ck_code_references_line_positive"
        ),
    )

    snapshot_sha: Mapped[str] = mapped_column(String(64), primary_key=True)
    file_path: Mapped[str] = mapped_column(String(1024), primary_key=True)
    symbol_name: Mapped[str] = mapped_column(String(512), primary_key=True)
    line_number: Mapped[int] = mapped_column(Integer, primary_key=True)
    kind: Mapped[str] = mapped_column(String(32), primary_key=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.current_timestamp(),
    )
