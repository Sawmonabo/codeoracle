"""Health-check response schemas for API v1."""

from typing import Literal

from pydantic import BaseModel, ConfigDict


class HealthLiveResponse(BaseModel):
    """Liveness response for the API process."""

    model_config = ConfigDict(extra="forbid")

    status: Literal["ok"] = "ok"


class HealthReadyResponse(BaseModel):
    """Readiness response for API-owned infrastructure."""

    model_config = ConfigDict(extra="forbid")

    status: Literal["ok", "degraded"]
    db: Literal["ok", "fail"]
    valkey: Literal["ok", "fail"]
