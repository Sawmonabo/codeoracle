"""ReindexRequest — POST /v1/reindex body model (Plan 11).

A reindex is parameterised by a single optional `base_sha`:
  - `None` (default) → full reindex (Plan 5 CLI `--full` equivalent).
  - non-None hex string → incremental reindex from that SHA.

The model does NOT validate the SHA format — Plan 5's DeltaComputer
resolves it via `git rev-parse`, and any drift surfaces as an
IndexingService error (HTTP 500). Duplicating validation here would
create two places to keep in sync.
"""

from pydantic import BaseModel, ConfigDict


class ReindexRequest(BaseModel):
    """POST /v1/reindex request body.

    Wire contract: `extra="forbid"`. Unknown keys raise 422.
    """

    model_config = ConfigDict(extra="forbid")

    base_sha: str | None = None
