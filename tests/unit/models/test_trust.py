"""Smoke tests for the trust enums."""

from codeoracle.models.trust import SourceKind, TrustLevel


def test_source_kind_values():
    assert SourceKind.CODE.value == "code"
    assert SourceKind.SCHEMA.value == "schema"


def test_trust_level_band_ordering():
    assert TrustLevel.UNTRUSTED.value < TrustLevel.LOW.value
    assert TrustLevel.LOW.value < TrustLevel.MEDIUM.value
    assert TrustLevel.MEDIUM.value < TrustLevel.HIGH.value
    assert TrustLevel.HIGH.value < TrustLevel.AUTHORITATIVE.value


def test_trust_level_is_int_subclass():
    assert int(TrustLevel.HIGH) == 9
