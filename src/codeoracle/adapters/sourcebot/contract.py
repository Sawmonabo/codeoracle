"""Pydantic wire-contract model for Sourcebot /api/source responses.

This model is the single source of truth for the response shape. Every call
site (adapter live HTTP, respx mocked tests, live integration test) validates
against
it. Schema drift on the Sourcebot side surfaces here as ValidationError, which
the adapter converts into SourcebotTerminalError. The forbid extra-field policy
catches additive-but-unverified schema changes from upstream.

Closes audit finding D-8.4 (no preflight against unverified wire contract).
"""

from pydantic import BaseModel, ConfigDict


class SourcebotSourceResponse(BaseModel):
    """Validated response body of GET /api/source.

    Sourcebot returns JSON of shape ``{"source": "<file body>"}``. Empty string
    is a legitimate value (empty file). Any other field, missing field, or
    non-str source value is rejected as schema drift.
    """

    model_config = ConfigDict(extra="forbid")

    source: str
