"""ScipIndexParser — text-format `scip print` output parser."""

import pathlib

import pytest

from codeoracle.adapters.scip.parser import (
    ScipIndexParser,
    ScipParseError,
)

FIXTURES = pathlib.Path(__file__).parent / "fixtures"


def test_parses_sample_fixture() -> None:
    sample_text = (FIXTURES / "sample.scip.txt").read_text()
    parser = ScipIndexParser()
    result = parser.parse(sample_text)

    # We expect:
    # - 2 file entries
    # - 1 definition of foo() in example.py at line 5 (0-idx 4 -> 1-idx 5)
    # - 1 definition of Bar in example.py at line 21
    # - 2 references to foo() in example.py (lines 12, 18)
    # - 1 reference to foo() in example2.py (line 3)
    assert len(result.definitions) == 2
    assert len(result.references) == 3

    foo_def = next(d for d in result.definitions if d.fqn.endswith("/foo()"))
    assert foo_def.file_path == "src/codeoracle/example.py"
    assert foo_def.line_number == 5  # 1-indexed

    bar_def = next(d for d in result.definitions if d.fqn.endswith("/Bar#"))
    assert bar_def.file_path == "src/codeoracle/example.py"
    assert bar_def.line_number == 21

    foo_refs = [r for r in result.references if r.fqn.endswith("/foo()")]
    foo_ref_lines = sorted([(r.file_path, r.line_number) for r in foo_refs])
    assert foo_ref_lines == [
        ("src/codeoracle/example.py", 12),
        ("src/codeoracle/example.py", 18),
        ("src/codeoracle/example2.py", 3),
    ]


def test_empty_input_returns_empty_result() -> None:
    parser = ScipIndexParser()
    result = parser.parse("")
    assert result.definitions == []
    assert result.references == []


def test_files_covered_excludes_files_with_no_occurrences() -> None:
    """The parser exposes `result.files_covered` — the set of file paths that
    appeared at least once. Workers use this to detect files NOT in the SCIP
    output (which still need outcome rows written, per architectural invariant
    2).
    """
    sample_text = (FIXTURES / "sample.scip.txt").read_text()
    parser = ScipIndexParser()
    result = parser.parse(sample_text)
    assert result.files_covered == {
        "src/codeoracle/example.py",
        "src/codeoracle/example2.py",
    }


def test_malformed_line_raises_with_context() -> None:
    """A garbled line should raise ScipParseError with the line content and
    line number for diagnostics; the worker catches this and treats it as
    project-level failure (writes outcome=failed for all changeset files)."""
    parser = ScipIndexParser()
    bad = """File: src/x.py
NOT A VALID LINE
"""
    with pytest.raises(ScipParseError) as exc_info:
        parser.parse(bad)
    assert "NOT A VALID LINE" in str(exc_info.value)
    assert "line 2" in str(exc_info.value)


def test_ignores_blank_lines() -> None:
    sample_text = """
File: a.py

Symbol: scip-python python . . `a.py`/foo()
Definition: 0:0-0:3

"""
    parser = ScipIndexParser()
    result = parser.parse(sample_text)
    assert len(result.definitions) == 1
    assert result.definitions[0].fqn.endswith("/foo()")
