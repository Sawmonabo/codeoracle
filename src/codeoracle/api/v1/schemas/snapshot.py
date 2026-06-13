"""Snapshot response schemas for API v1."""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from codeoracle.models.snapshot import ExtractorOutcome, SnapshotStatus


class SnapshotResponse(BaseModel):
    """Public API representation of a snapshot lifecycle row."""

    model_config = ConfigDict(from_attributes=True, extra="forbid")

    sha: str
    parent_sha: str | None = None
    changed_files: list[str] = Field(default_factory=list)
    status: SnapshotStatus
    extractor_results: dict[str, ExtractorOutcome] = Field(
        default_factory=dict
    )
    error: str | None = None
    applied_at: datetime | None = None
