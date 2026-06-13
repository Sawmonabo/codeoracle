"""ReindexRequest model tests — pin the wire shape so the route handler
can rely on `model_validate` rejecting drift.
"""

import pytest
from pydantic import ValidationError

from codeoracle.models.reindex import ReindexRequest


def test_default_base_sha_is_none():
    """Empty body = full reindex (base_sha=None). Mirrors Plan 5 CLI
    `--full` (which sets `base_sha=None` before calling
    `IndexingService.reindex`).
    """
    req = ReindexRequest()
    assert req.base_sha is None


def test_base_sha_accepts_hex_string():
    """A SHA1 / SHA256 / arbitrary string is accepted — the model does
    NOT validate the format. Git SHA validation lives inside
    `IndexingService.reindex` (Plan 5's `DeltaComputer` resolves it via
    `git rev-parse`). Keeping the request shape permissive avoids
    duplicating SHA validation in two places.
    """
    req = ReindexRequest(base_sha="abc123def456")
    assert req.base_sha == "abc123def456"


def test_extra_field_rejected():
    """`extra="forbid"` — any unknown key triggers ValidationError so
    wire drift is loud. A user typo (`base_shaa`) or a contract change
    in a future plan that adds a field without updating Plan 11 surfaces
    here, not silently."""
    with pytest.raises(ValidationError) as excinfo:
        ReindexRequest.model_validate({"base_sha": "abc", "force": True})
    # Pydantic v2 errors include the extra-key location
    assert any("force" in str(err) for err in excinfo.value.errors())


def test_base_sha_empty_string_accepted_as_string():
    """Empty string is NOT coerced to None — it stays as "" and is
    forwarded to `IndexingService.reindex(base_sha="")`. Plan 5's
    `DeltaComputer.compute(base_sha="")` will fail at git resolution,
    which surfaces as HTTP 500 via CodeOracleError. The model does not
    second-guess the user's intent here.
    """
    req = ReindexRequest(base_sha="")
    assert req.base_sha == ""


def test_base_sha_explicit_null():
    """JSON `null` deserialises to None — same as omitting the field."""
    req = ReindexRequest.model_validate({"base_sha": None})
    assert req.base_sha is None


def test_model_dump_round_trip():
    """model_dump → model_validate must be lossless. Used by clients
    that re-emit the request (e.g., the MCP tool wrapper in Plan 12).
    """
    original = ReindexRequest(base_sha="deadbeef")
    payload = original.model_dump()
    restored = ReindexRequest.model_validate(payload)
    assert restored == original
