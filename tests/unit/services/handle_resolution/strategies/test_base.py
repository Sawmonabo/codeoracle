"""Plan 8 Task 7 — HandleResolverStrategy ABC + parse_file_locator helper.

Pins:
  1. HandleResolverStrategy is abstract — instantiating raises TypeError.
  2. Subclasses missing `resolve` cannot instantiate.
  3. parse_file_locator accepts well-formed `file://path:start-end`.
  4. parse_file_locator rejects: missing scheme, wrong scheme, no path,
     no line range, malformed line range, start > end, start < 1.
  5. parse_file_locator preserves the path verbatim including leading
     slashes and dots — Sourcebot expects paths relative to the repo
     root, no normalization.
"""

from typing import Any, cast

import pytest

from codeoracle.services.handle_resolution.strategies.base import (
    HandleResolverStrategy,
    parse_file_locator,
)

# -----------------------------------------------------------------------------
# ABC enforcement
# -----------------------------------------------------------------------------


def test_handle_resolver_strategy_cannot_be_instantiated_directly() -> None:
    strategy_type = cast(Any, HandleResolverStrategy)
    with pytest.raises(TypeError, match="abstract"):
        strategy_type()


def test_subclass_without_resolve_cannot_instantiate() -> None:
    class IncompleteResolver(HandleResolverStrategy):
        pass

    resolver_type = cast(Any, IncompleteResolver)
    with pytest.raises(TypeError, match="abstract"):
        resolver_type()


def test_subclass_with_resolve_instantiates_cleanly() -> None:
    class MinimalResolver(HandleResolverStrategy):
        async def resolve(self, handle: object) -> str:
            return "ok"

    instance = MinimalResolver()
    assert isinstance(instance, HandleResolverStrategy)


# -----------------------------------------------------------------------------
# parse_file_locator — happy path
# -----------------------------------------------------------------------------


def test_parse_file_locator_extracts_path_and_lines() -> None:
    path, (start, end) = parse_file_locator(
        "file://src/codeoracle/services/verifier/service.py:42-58"
    )
    assert path == "src/codeoracle/services/verifier/service.py"
    assert (start, end) == (42, 58)


def test_parse_file_locator_accepts_single_line_range() -> None:
    """`file://path:7-7` is a valid one-line range."""
    path, (start, end) = parse_file_locator("file://x.py:7-7")
    assert path == "x.py"
    assert (start, end) == (7, 7)


def test_parse_file_locator_preserves_path_verbatim_no_normalization() -> None:
    """Slashes, dots, and parent-dir tokens stay as-is — Sourcebot's repo
    is the source of truth on what paths are valid."""
    path, _ = parse_file_locator("file://./a/b/../c.py:1-2")
    assert path == "./a/b/../c.py"


# -----------------------------------------------------------------------------
# parse_file_locator — validation failures
# -----------------------------------------------------------------------------


def test_parse_file_locator_rejects_missing_scheme() -> None:
    with pytest.raises(ValueError, match="scheme"):
        parse_file_locator("src/codeoracle/foo.py:1-5")


def test_parse_file_locator_rejects_wrong_scheme() -> None:
    with pytest.raises(ValueError, match="scheme"):
        parse_file_locator("cypher://MATCH (n) RETURN n")


def test_parse_file_locator_rejects_no_line_range() -> None:
    with pytest.raises(ValueError, match="line range"):
        parse_file_locator("file://src/foo.py")


def test_parse_file_locator_rejects_malformed_line_range() -> None:
    with pytest.raises(ValueError, match="line range"):
        parse_file_locator("file://src/foo.py:abc")


def test_parse_file_locator_rejects_line_start_below_one() -> None:
    with pytest.raises(ValueError, match="line_start"):
        parse_file_locator("file://src/foo.py:0-5")


def test_parse_file_locator_rejects_end_before_start() -> None:
    with pytest.raises(ValueError, match="line_end"):
        parse_file_locator("file://src/foo.py:10-5")


def test_parse_file_locator_rejects_empty_path() -> None:
    with pytest.raises(ValueError, match="path"):
        parse_file_locator("file://:1-5")
