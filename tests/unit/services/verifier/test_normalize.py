"""
Unit tests for the Verifier's excerpt-normalization helper + confidence floor.
"""

import pytest

from codeoracle.services.verifier.service import (
    PROVEN_CONFIDENCE_FLOOR,
    _normalize_for_comparison,
)


def test_proven_confidence_floor_value() -> None:
    """The floor is 0.6 — pinned so a silent retune is caught in review."""
    assert PROVEN_CONFIDENCE_FLOOR == 0.6


def test_normalize_strips_trailing_whitespace_per_line() -> None:
    assert _normalize_for_comparison("a   \nb\t\nc") == "a\nb\nc"


def test_normalize_crlf_becomes_lf() -> None:
    assert _normalize_for_comparison("a\r\nb\r\nc") == "a\nb\nc"


def test_normalize_lone_cr_becomes_lf() -> None:
    """An old-Mac lone CR is also folded to LF."""
    assert _normalize_for_comparison("a\rb\rc") == "a\nb\nc"


def test_normalize_strips_outer_whitespace() -> None:
    assert _normalize_for_comparison("\n  payload  \n\n") == "payload"


def test_normalize_nfc_nfd_equivalence() -> None:
    """A combining-accent (NFD) string and its precomposed (NFC) twin are
    different code points but normalize equal. Sourcebot and the LLM may
    disagree on Unicode form without that being real content drift."""
    # NFD: 'e' + U+0301 combining acute accent (5 code points)
    nfd = "cafe\u0301"
    nfc = (
        "ca" + "f" + "\u00e9"
    )  # NFC: precomposed U+00E9 e-acute (4 code points)
    assert nfd != nfc
    assert _normalize_for_comparison(nfd) == _normalize_for_comparison(nfc)


def test_normalize_is_idempotent() -> None:
    """Normalizing an already-normalized string changes nothing."""
    once = _normalize_for_comparison("  a \r\n b\t \n")
    assert _normalize_for_comparison(once) == once


def test_normalize_preserves_meaningful_difference() -> None:
    """Normalization must NOT collapse genuinely different content — otherwise
    REFUTED could never fire."""
    assert _normalize_for_comparison("return 1") != _normalize_for_comparison(
        "return 2"
    )


def test_normalize_keeps_internal_blank_lines() -> None:
    """A blank line *between* content is structural. Only per-line trailing
    whitespace and the outer edges are stripped — interior blank lines stay."""
    assert _normalize_for_comparison("a\n\nb") == "a\n\nb"


def test_normalize_whitespace_only_line_becomes_blank() -> None:
    """An interior line that is *only* whitespace is rstripped to empty — it
    stays as a structural blank line, it is not deleted. This is also what lets
    the verify() evidence-free-citation guard catch a whitespace-only excerpt:
    such an excerpt normalizes all the way to `""` (plan Invariant 12)."""
    assert _normalize_for_comparison("a\n   \nb") == "a\n\nb"


@pytest.mark.parametrize(
    "graph_query_excerpt",
    ['["alpha", "beta"]', "[3]", "[3.5, true, false, null]", "[]"],
)
def test_normalize_leaves_graph_query_json_untouched(
    graph_query_excerpt: str,
) -> None:
    """A GRAPH_QUERY citation excerpt is the exact string GraphQueryResolver
    emits (Plan 8: json.dumps(values, sort_keys=True, separators=(", ", ":
    "))). Normalization must pass it through byte-identical — otherwise every
    GRAPH_QUERY citation would REFUTE against its own resolver output."""
    assert (
        _normalize_for_comparison(graph_query_excerpt) == graph_query_excerpt
    )
