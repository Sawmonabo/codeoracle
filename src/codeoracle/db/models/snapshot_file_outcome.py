"""SQLAlchemy model for per-file extractor outcomes."""

from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, Text
from sqlalchemy.orm import Mapped, mapped_column

from codeoracle.db.base import Base


class SnapshotFileOutcomeRecord(Base):
    """Per-file extractor outcome used by indexing barriers."""

    __tablename__ = "snapshot_file_outcomes"
    __table_args__ = (
        CheckConstraint(
            "outcome IN ('success', 'failed', 'skipped')",
            name="ck_snapshot_file_outcomes_outcome",
        ),
    )

    snapshot_sha: Mapped[str] = mapped_column(Text, primary_key=True)
    file_path: Mapped[str] = mapped_column(Text, primary_key=True)
    extractor: Mapped[str] = mapped_column(Text, primary_key=True)
    outcome: Mapped[str] = mapped_column(Text, nullable=False)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    applied_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
