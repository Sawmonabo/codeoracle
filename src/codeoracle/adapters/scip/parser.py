"""ScipIndexParser — text-format `scip print` output parser.

The text format is whitespace-significant line-oriented:

  File: <path> Symbol: <scip-symbol-identifier>
  Definition: <start_line>:<start_col>-<end_line>:<end_col>
  Reference: <start_line>:<start_col>-<end_line>:<end_col> ...

Records are grouped per (File, Symbol) pair: each Symbol record under a
File record applies to that File until the next File or Symbol record.
Definitions and References can repeat for the same Symbol.

Line/column numbers in SCIP are 0-indexed; this parser converts to 1-indexed
to align with `code_references.line_number >= 1` and with how editors display
line numbers (humans count from 1).

Out-of-scope: parsing the binary protobuf `.scip` format. The text path is
sufficient for Plan 6's volume; if performance becomes a concern, a separate
adapter using `scip-protobuf` Python bindings can be added later.
"""

import re
from dataclasses import dataclass, field


@dataclass(frozen=True, slots=True)
class ScipDefinition:
    """One definition occurrence in a file."""

    file_path: str
    fqn: str  # The full SCIP symbol identifier — used as MERGE key in AGE.
    line_number: int  # 1-indexed start line.
    end_line: int  # 1-indexed end line.


@dataclass(frozen=True, slots=True)
class ScipReference:
    """One reference occurrence in a file."""

    file_path: str
    fqn: str
    line_number: int
    end_line: int


@dataclass(frozen=True, slots=True)
class ScipParseResult:
    definitions: list[ScipDefinition] = field(default_factory=list)
    references: list[ScipReference] = field(default_factory=list)
    files_covered: set[str] = field(default_factory=set)


class ScipParseError(ValueError):
    """Raised when `scip print` output is structurally malformed.

    The worker catches this and treats it as a project-level failure
    (per architectural invariant 3: SCIP failure granularity = project-level).
    """


_FILE_RE = re.compile(r"^File:\s+(.+)$")
_SYMBOL_RE = re.compile(r"^Symbol:\s+(.+)$")
_OCCURRENCE_RE = re.compile(
    r"^(Definition|Reference):\s+(\d+):(\d+)-(\d+):(\d+)$"
)


class ScipIndexParser:
    def parse(self, text: str) -> ScipParseResult:
        result = ScipParseResult()
        current_file: str | None = None
        current_symbol: str | None = None

        for idx, raw in enumerate(text.splitlines(), start=1):
            line = raw.strip()
            if not line:
                continue

            if m := _FILE_RE.match(line):
                current_file = m.group(1).strip()
                current_symbol = None
                result.files_covered.add(current_file)
                continue

            if m := _SYMBOL_RE.match(line):
                if current_file is None:
                    raise ScipParseError(
                        f"line {idx}: 'Symbol:' record before any "
                        f"'File:' record: {line!r}"
                    )
                current_symbol = m.group(1).strip()
                continue

            if m := _OCCURRENCE_RE.match(line):
                if current_file is None or current_symbol is None:
                    raise ScipParseError(
                        f"line {idx}: occurrence record without active "
                        f"File+Symbol: {line!r}"
                    )
                kind = m.group(1)
                start_line_0 = int(m.group(2))
                end_line_0 = int(m.group(4))
                # SCIP is 0-indexed; we store 1-indexed.
                start_line = start_line_0 + 1
                end_line = end_line_0 + 1
                if kind == "Definition":
                    result.definitions.append(
                        ScipDefinition(
                            file_path=current_file,
                            fqn=current_symbol,
                            line_number=start_line,
                            end_line=end_line,
                        )
                    )
                else:  # Reference
                    result.references.append(
                        ScipReference(
                            file_path=current_file,
                            fqn=current_symbol,
                            line_number=start_line,
                            end_line=end_line,
                        )
                    )
                continue

            raise ScipParseError(f"line {idx}: unrecognized record: {line!r}")

        return result
