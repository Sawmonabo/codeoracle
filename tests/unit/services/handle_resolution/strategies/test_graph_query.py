"""Plan 8 Task 11 — GraphQueryResolver unit tests.

Pins:
  1. Parses cypher:// locator → calls GraphRepository.execute_cypher with
     the exact Cypher body (no rewriting, no parameter extraction).
  2. Stringifies results deterministically: a JSON array of scalars, one
     per row, with agtype outer quotes stripped from string values.
  3. Empty result list → "[]" (a real "no matches" signal, NOT a failure).
  4. Wraps GraphQueryError as ResolverError.
  5. Rejects locator without cypher:// scheme.
  6. Rejects empty cypher body.
  7. Snapshot pin is enforced as a CONSTRAINT, not a token (D-8.1): rejects a
     body that omits $snapshot_sha entirely, one that mentions it without a
     node-property-map binding (e.g. behind `WHERE true OR ...`), and one whose
     only binding is inside a stripped comment; accepts a legitimate scoped
     query carrying extra map properties / whitespace.
  8. Binds the head snapshot_sha structurally via params (value from the
     handle,
     never from the query text), stripping any `+wt` dirty-tree suffix first.
"""

from unittest.mock import AsyncMock

import pytest

from codeoracle.core.exceptions import GraphQueryError, ResolverError
from codeoracle.models.evidence import EvidenceHandle, HandleType
from codeoracle.services.handle_resolution.strategies.graph_query import (
    GraphQueryResolver,
    stringify_cypher_rows,
)


def _handle(locator: str, snapshot_sha: str = "abc1234") -> EvidenceHandle:
    return EvidenceHandle(
        type=HandleType.GRAPH_QUERY, locator=locator, snapshot_sha=snapshot_sha
    )


@pytest.fixture
def mock_graph_repo() -> AsyncMock:
    m = AsyncMock()
    m.execute_cypher = AsyncMock(return_value=[])
    return m


# -----------------------------------------------------------------------------
# Dispatch — Cypher body passed through unchanged
# -----------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_resolve_runs_cypher_body_verbatim(
    mock_graph_repo: AsyncMock,
) -> None:
    # The body MUST reference $snapshot_sha — the resolver fails closed
    # otherwise
    # (D-8.1). The body itself is forwarded verbatim; the pin is BOUND, not
    # spliced.
    cypher = (
        "MATCH (c:Symbol {snapshot_sha:$snapshot_sha})-[:CALLS]->"
        "(t {fqn:'codeoracle.verify'}) RETURN c.fqn AS result"
    )
    mock_graph_repo.execute_cypher.return_value = [
        {"result": '"codeoracle.handle_explain_symbol"'}
    ]
    resolver = GraphQueryResolver(graph_repo=mock_graph_repo)

    await resolver.resolve(_handle(f"cypher://{cypher}"))

    mock_graph_repo.execute_cypher.assert_awaited_once()
    actual_cypher = mock_graph_repo.execute_cypher.call_args.args[0]
    assert actual_cypher == cypher  # body forwarded unchanged
    # Default column_spec is 'result agtype'
    assert (
        mock_graph_repo.execute_cypher.call_args.kwargs.get("column_spec")
        == "result agtype"
    )


@pytest.mark.asyncio
async def test_resolve_binds_head_snapshot_sha_as_param(
    mock_graph_repo: AsyncMock,
) -> None:
    """The snapshot pin is bound STRUCTURALLY (D-8.1): the resolver passes
    params={"snapshot_sha": <head sha>} as the second positional argument, so
    the value originates from the handle, never from the query text. A `+wt`
    dirty-tree suffix is stripped to its head first (G21 consistency)."""
    cypher = (
        "MATCH (n:Symbol {snapshot_sha:$snapshot_sha}) RETURN n.fqn AS result"
    )
    mock_graph_repo.execute_cypher.return_value = []
    resolver = GraphQueryResolver(graph_repo=mock_graph_repo)

    await resolver.resolve(
        _handle(f"cypher://{cypher}", snapshot_sha="abc123+wt0123456789ab")
    )

    params = mock_graph_repo.execute_cypher.call_args.args[1]
    assert params == {
        "snapshot_sha": "abc123"
    }  # +wt stripped, bound not spliced


@pytest.mark.asyncio
async def test_resolve_rejects_cypher_missing_snapshot_param(
    mock_graph_repo: AsyncMock,
) -> None:
    """A graph_query whose Cypher omits $snapshot_sha could read across
    snapshots. The resolver fails CLOSED before execution with ResolverError —
    it does not trust the agent prompt to have included the pin (D-8.1)."""
    resolver = GraphQueryResolver(graph_repo=mock_graph_repo)
    with pytest.raises(ResolverError, match="snapshot_sha"):
        await resolver.resolve(
            _handle("cypher://MATCH (n) RETURN n.fqn AS result")
        )
    mock_graph_repo.execute_cypher.assert_not_called()


@pytest.mark.asyncio
async def test_resolve_rejects_snapshot_param_without_constraint(
    mock_graph_repo: AsyncMock,
) -> None:
    """The pin check is a CONSTRAINT check, not a token-presence check (D-8.1).
    A body that MENTIONS $snapshot_sha but does not bind it inside a
    node-property
    map — here behind a `WHERE true OR ...` short-circuit — reads every
    snapshot.
    A naive `"$snapshot_sha" in body` test would wrongly pass it; the resolver
    MUST reject it before execution."""
    resolver = GraphQueryResolver(graph_repo=mock_graph_repo)
    leaky = (
        "MATCH (c:Symbol) "
        "WHERE true OR c.snapshot_sha = $snapshot_sha "
        "RETURN c.fqn AS result"
    )
    with pytest.raises(ResolverError, match="snapshot_sha"):
        await resolver.resolve(_handle(f"cypher://{leaky}"))
    mock_graph_repo.execute_cypher.assert_not_called()


@pytest.mark.asyncio
async def test_resolve_rejects_cypher_with_commented_out_snapshot_constraint(
    mock_graph_repo: AsyncMock,
) -> None:
    """A `//`-commented constraint is not a real constraint. The check strips
    Cypher comments before matching, so a body whose only `{snapshot_sha:
    $snapshot_sha}` occurrence is inside a comment is rejected (D-8.1)."""
    resolver = GraphQueryResolver(graph_repo=mock_graph_repo)
    commented = (
        "MATCH (c:Symbol) // {snapshot_sha:$snapshot_sha}\n"
        "RETURN c.fqn AS result"
    )
    with pytest.raises(ResolverError, match="snapshot_sha"):
        await resolver.resolve(_handle(f"cypher://{commented}"))
    mock_graph_repo.execute_cypher.assert_not_called()


@pytest.mark.asyncio
async def test_resolve_accepts_constraint_with_extra_properties_in_map(
    mock_graph_repo: AsyncMock,
) -> None:
    """The constraint regex tolerates a node-property map that carries OTHER
    properties alongside the pin (e.g. `{fqn:'x', snapshot_sha:$snapshot_sha}`)
    and arbitrary intra-brace whitespace — a legitimate, scoped query is NOT
    falsely rejected (D-8.1 must narrow the surface without breaking valid
    citations)."""
    mock_graph_repo.execute_cypher.return_value = []
    resolver = GraphQueryResolver(graph_repo=mock_graph_repo)
    scoped = (
        "MATCH (c:Symbol { fqn:'codeoracle.verify', "
        "snapshot_sha : $snapshot_sha }) "
        "RETURN c.fqn AS result"
    )
    out = await resolver.resolve(_handle(f"cypher://{scoped}"))
    assert out == "[]"
    mock_graph_repo.execute_cypher.assert_awaited_once()


@pytest.mark.asyncio
async def test_resolve_returns_deterministic_json_string(
    mock_graph_repo: AsyncMock,
) -> None:
    """Two rows; agtype outer-quote stripped from string values. Row order
    is preserved — the JSON array is never sorted (only map keys would be,
    and graph_query produces no maps). The Verifier's excerpt-comparison is
    byte-exact (after NFC + LF + rstrip), so the form MUST be stable."""
    mock_graph_repo.execute_cypher.return_value = [
        {"result": '"codeoracle.b"'},
        {"result": '"codeoracle.a"'},
    ]
    resolver = GraphQueryResolver(graph_repo=mock_graph_repo)
    out = await resolver.resolve(
        _handle(
            "cypher://MATCH (n:Symbol {snapshot_sha:$snapshot_sha}) "
            "RETURN n.fqn AS result"
        )
    )
    # Quotes stripped + serialized as JSON list
    assert out == '["codeoracle.b", "codeoracle.a"]'


@pytest.mark.asyncio
async def test_resolve_returns_empty_list_string_when_no_rows(
    mock_graph_repo: AsyncMock,
) -> None:
    """No matches is a legitimate result, not a failure. Verifier compares
    '[]' to whatever the LLM put in excerpt; if the LLM claimed 'no callers'
    by emitting excerpt='[]', it's PROVEN. If the LLM claimed callers but
    the graph has none, REFUTED."""
    mock_graph_repo.execute_cypher.return_value = []
    resolver = GraphQueryResolver(graph_repo=mock_graph_repo)
    out = await resolver.resolve(
        _handle(
            "cypher://MATCH "
            "(n:Symbol {fqn:'nope', snapshot_sha:$snapshot_sha}) "
            "RETURN n.fqn AS result"
        )
    )
    assert out == "[]"


# -----------------------------------------------------------------------------
# Error wrapping
# -----------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_resolve_wraps_graph_query_error_as_resolver_error(
    mock_graph_repo: AsyncMock,
) -> None:
    # The body MUST carry the snapshot constraint so it passes
    # _assert_snapshot_pinned
    # and reaches execute_cypher — only then does the seeded GraphQueryError
    # drive the
    # service-boundary wrap (D-8.6). A body lacking the constraint would be
    # rejected
    # earlier by the D-8.1 check, with no GraphQueryError cause.
    mock_graph_repo.execute_cypher.side_effect = GraphQueryError(
        "Cypher execution failed: ..."
    )
    resolver = GraphQueryResolver(graph_repo=mock_graph_repo)
    with pytest.raises(ResolverError) as exc:
        await resolver.resolve(
            _handle(
                "cypher://MATCH (n:Symbol {snapshot_sha:$snapshot_sha}) "
                "RETURN n.fqn AS result"
            )
        )
    assert isinstance(exc.value.__cause__, GraphQueryError)


@pytest.mark.asyncio
async def test_resolve_does_not_leak_worker_family_exception(
    mock_graph_repo: AsyncMock,
) -> None:
    # Complement to the wrap test: the wrap test pins that the worker-family
    # cause is
    # preserved as __cause__; this one pins the OTHER half of the D-8.6
    # contract — the
    # exception that actually PROPAGATES across the service boundary is a
    # ResolverError
    # and is NOT a GraphQueryError. Catching `ResolverError` must NOT also
    # catch the
    # raised type, which proves the worker-family vocabulary never escapes
    # services/.
    mock_graph_repo.execute_cypher.side_effect = GraphQueryError(
        "Cypher execution failed: ..."
    )
    resolver = GraphQueryResolver(graph_repo=mock_graph_repo)
    with pytest.raises(ResolverError) as exc:
        await resolver.resolve(
            _handle(
                "cypher://MATCH (n:Symbol {snapshot_sha:$snapshot_sha}) "
                "RETURN n.fqn AS result"
            )
        )
    assert not isinstance(exc.value, GraphQueryError)


# -----------------------------------------------------------------------------
# Locator validation
# -----------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_resolve_rejects_wrong_scheme(
    mock_graph_repo: AsyncMock,
) -> None:
    resolver = GraphQueryResolver(graph_repo=mock_graph_repo)
    handle = EvidenceHandle(
        type=HandleType.GRAPH_QUERY,
        # Pydantic accepts (has '://'); resolver rejects scheme
        locator="file://foo.py:1-2",
        snapshot_sha="abc1234",
    )
    with pytest.raises(ValueError, match="cypher://"):
        await resolver.resolve(handle)
    mock_graph_repo.execute_cypher.assert_not_called()


@pytest.mark.asyncio
async def test_resolve_rejects_empty_cypher_body(
    mock_graph_repo: AsyncMock,
) -> None:
    resolver = GraphQueryResolver(graph_repo=mock_graph_repo)
    handle = EvidenceHandle(
        type=HandleType.GRAPH_QUERY,
        locator="cypher://",
        snapshot_sha="abc1234",
    )
    with pytest.raises(ValueError, match="empty"):
        await resolver.resolve(handle)
    mock_graph_repo.execute_cypher.assert_not_called()


# -----------------------------------------------------------------------------
# stringify_cypher_rows helper — exposed for direct unit testing
# -----------------------------------------------------------------------------


def test_stringify_strips_agtype_quotes_from_string_values() -> None:
    rows = [{"result": '"alpha"'}, {"result": '"beta"'}]
    assert stringify_cypher_rows(rows) == '["alpha", "beta"]'


def test_stringify_preserves_int_values_unquoted() -> None:
    """AGE encodes ints without quotes; the resolver MUST keep them numeric."""
    rows = [{"result": 3}]
    assert stringify_cypher_rows(rows) == "[3]"


def test_stringify_handles_float_bool_and_null_scalars() -> None:
    """graph_query is constrained to single-column, scalar-per-row returns.
    String and int scalars are pinned above; this pins the remaining scalar
    agtype shapes — float, bool, null. Maps and lists are out of slice scope
    (see the Task 11 scalar-per-row constraint)."""
    rows = [
        {"result": 3.5},
        {"result": True},
        {"result": False},
        {"result": None},
    ]
    assert stringify_cypher_rows(rows) == "[3.5, true, false, null]"
