"""Error response schemas for API v1."""

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class ErrorDetail(BaseModel):
    """Machine-readable API error detail."""

    model_config = ConfigDict(extra="forbid")

    code: str = Field(..., examples=["VALIDATION_ERROR"])
    message: str = Field(..., examples=["Invalid request"])
    details: dict[str, Any] | None = None


class ErrorResponse(BaseModel):
    """Standard API error response."""

    model_config = ConfigDict(extra="forbid")

    error: ErrorDetail
