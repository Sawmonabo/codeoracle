"""Health endpoint response model — published as an HTTP contract (Plan 10
Provides table). Mirrors Plan 3's other models: model_config = extra="forbid"
for wire-contract drift detection."""

from typing import Literal

from pydantic import BaseModel, ConfigDict


class HealthResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    status: Literal["ok", "degraded"]
    db: Literal["ok", "fail"]
