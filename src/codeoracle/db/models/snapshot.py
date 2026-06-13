"""SQLAlchemy model for repository snapshots."""

from datetime import datetime

from sqlalchemy import ARRAY, JSON, DateTime, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from codeoracle.db.base import Base


class SnapshotRecord(Base):
    """One end-to-end extraction pass."""

    __tablename__ = "snapshots"

    sha: Mapped[str] = mapped_column(String(80), primary_key=True)
    parent_sha: Mapped[str | None] = mapped_column(String(80), nullable=True)
    changed_files: Mapped[list[str]] = mapped_column(
        ARRAY(String), nullable=False, default=list
    )
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="pending"
    )
    extractor_results: Mapped[dict[str, str]] = mapped_column(
        JSON, nullable=False, default=dict
    )
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    applied_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
