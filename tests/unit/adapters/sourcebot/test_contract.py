"""Plan 8 Task 3a — SourcebotSourceResponse wire-contract tests.

Pins:
  1. The model accepts the documented response shape ({"source": "<str>"}).
  2. The model accepts an empty source field (empty file is legitimate).
  3. The model rejects a missing source field.
  4. The model rejects extra unexpected fields (extra=forbid catches schema
     drift).
  5. The model rejects a wrong type for the source field.

This is the single source of truth for the /api/source response shape;
every call site (adapter live HTTP, respx mocked tests, live integration
test) validates against this model. Closes D-8.4.
"""

import pytest
from pydantic import ValidationError

from codeoracle.adapters.sourcebot.contract import SourcebotSourceResponse


def test_contract_accepts_valid_payload() -> None:
    model = SourcebotSourceResponse.model_validate({"source": "abc"})
    assert model.source == "abc"


def test_contract_accepts_empty_source_field() -> None:
    """An empty source string is a legitimate response (empty file)."""
    model = SourcebotSourceResponse.model_validate({"source": ""})
    assert model.source == ""


def test_contract_rejects_missing_source_field() -> None:
    with pytest.raises(ValidationError):
        SourcebotSourceResponse.model_validate({})


def test_contract_rejects_extra_unexpected_field() -> None:
    """extra=forbid catches additive-but-unverified schema changes upstream."""
    with pytest.raises(ValidationError):
        SourcebotSourceResponse.model_validate(
            {"source": "abc", "unexpected": 1}
        )


def test_contract_rejects_wrong_type_for_source() -> None:
    with pytest.raises(ValidationError):
        SourcebotSourceResponse.model_validate({"source": 123})
