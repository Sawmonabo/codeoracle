"""HandleResolverStrategy ABC + shared locator parser.

Every concrete strategy (CodeSpan / DocParagraph / GraphQuery / Stub)
inherits from `HandleResolverStrategy` and implements `resolve(handle)`.
The shared `parse_file_locator` helper exists because the CODE_SPAN and
DOC_PARAGRAPH handle types use the same `file://<path>:<start>-<end>`
locator format per the spec at §6.1 / §6.3.

The cypher:// scheme for GRAPH_QUERY is parsed locally in
strategies/graph_query.py (it's structurally different — no path, no
line range — and folding it into a single parser would force a useless
union return type).
"""

from abc import ABC, abstractmethod

from codeoracle.models.evidence import EvidenceHandle


class HandleResolverStrategy(ABC):
    """Resolve one EvidenceHandle to its current actual content.

    Implementations MUST raise either ResolverError (or subclass) on
    failure, or return the resolved content as a string. They MUST NOT
    return None or empty-on-failure — the Verifier interprets an empty
    string as "file existed but the requested lines are past EOF" (a
    legitimate REFUTED case if the LLM's excerpt was non-empty), not as
    "could not resolve".
    """

    @abstractmethod
    async def resolve(self, handle: EvidenceHandle) -> str:
        """Fetch and return the actual content the handle points to.

        Raises: ResolverError (or subclass) on resolution failure.
        """


# -----------------------------------------------------------------------------
# Shared locator parser — file://path:start-end
# -----------------------------------------------------------------------------

_FILE_SCHEME = "file://"


def parse_file_locator(locator: str) -> tuple[str, tuple[int, int]]:
    """Parse `file://<path>:<line_start>-<line_end>` into (path, (start, end)).

    Both line numbers are 1-indexed and inclusive of the endpoint.

    Raises: ValueError: if the locator is missing the file:// scheme, has no
            line range, has a malformed range, or violates 1 <= start <= end.
    """
    if not locator.startswith(_FILE_SCHEME):
        raise ValueError(f"locator missing 'file://' scheme: {locator!r}")

    rest = locator[len(_FILE_SCHEME) :]

    # Path and line range are separated by the LAST ':' — paths theoretically
    # can contain colons (rare, but Windows drive letters used to do this).
    # Using rsplit isolates the parsing right-to-left and avoids spurious
    # splits when the path contains a ':'.
    if ":" not in rest:
        raise ValueError(
            "locator missing line range — expected "
            f"'file://<path>:<start>-<end>': {locator!r}"
        )
    path, range_part = rest.rsplit(":", 1)
    if not path:
        raise ValueError(f"locator path is empty: {locator!r}")

    if "-" not in range_part:
        raise ValueError(
            "line range must be 'start-end', got "
            f"{range_part!r} in {locator!r}"
        )
    start_str, end_str = range_part.split("-", 1)
    try:
        line_start = int(start_str)
        line_end = int(end_str)
    except ValueError as e:
        raise ValueError(
            "line range must be integers, got "
            f"{range_part!r} in {locator!r}: {e}"
        ) from e

    if line_start < 1:
        raise ValueError(
            f"line_start must be >= 1, got {line_start} in {locator!r}"
        )
    if line_end < line_start:
        raise ValueError(
            f"line_end ({line_end}) must be >= line_start "
            f"({line_start}) in {locator!r}"
        )

    return path, (line_start, line_end)
