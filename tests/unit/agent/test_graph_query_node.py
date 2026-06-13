"""
Plan 10 Task 5 — the graph_query node: existence check + GRAPH_QUERY citation.
"""

from typing import Any, Protocol, cast

from codeoracle.agent.nodes.graph_query import (
    build_callers_cypher,
    graph_query_node,
)
from codeoracle.models import HandleType, Query, VerdictStatus


class _RecordedGraphRepo(Protocol):
    calls: list[tuple[str, dict[str, Any]]]


async def test_graph_query_emits_citation_when_symbol_found(
    make_fake_deps, make_agent_state
) -> None:
    deps = make_fake_deps(symbol_exists=True, resolve_content="CALLERS")
    update = await graph_query_node(
        make_agent_state(snapshot_sha="abc1234"), deps
    )
    assert update["symbol_found"] is True
    citations = update["graph_citations"]
    assert isinstance(citations, list)
    assert len(citations) == 1
    citation = citations[0]
    assert citation.handle.type == HandleType.GRAPH_QUERY
    assert citation.handle.locator.startswith("cypher://")
    # invariant 1 — the excerpt is the resolver's output verbatim
    assert citation.excerpt.startswith("CALLERS::cypher://")


async def test_graph_query_sets_unknown_verdict_when_symbol_missing(
    make_fake_deps, make_agent_state
) -> None:
    deps = make_fake_deps(symbol_exists=False)
    update = await graph_query_node(
        make_agent_state(snapshot_sha="abc1234"), deps
    )
    assert update["symbol_found"] is False
    verdict = update["verdict"]
    assert verdict is not None
    assert verdict.status == VerdictStatus.UNKNOWN
    # §8.7 ceremony point 4 — the exact reason-string format
    reason = verdict.reason
    assert reason == "symbol not found in graph at snapshot abc1234"
    assert (
        "graph_citations" not in update
    )  # nothing retrieved on the UNKNOWN path


def test_callers_cypher_is_deterministically_ordered() -> None:
    """
    invariant 2 — every cypher:// query the agent builds is deterministically
    ordered, and (NFR D3-3) bounded so a high-fan-in symbol cannot produce an
    unbounded excerpt."""
    cypher = build_callers_cypher("codeoracle.x.Y.z", "abc1234")
    assert "ORDER BY result" in cypher  # invariant 2 — stable re-run order
    assert cypher.rstrip().endswith("LIMIT 50")  # NFR D3-3 — bounded fan-out


def test_callers_cypher_interpolates_fqn_and_sha() -> None:
    cypher = build_callers_cypher("codeoracle.x.Y.z", "abc1234")
    assert "'codeoracle.x.Y.z'" in cypher
    assert "'abc1234'" in cypher
    # §3.7 topology — Symbol→Symbol :CALLS, with the caller resolved to its
    # File via :DEFINES; the impossible File→Symbol :CALLS and the out-of-scope
    # :USES / :IMPORTS edge tokens are gone (D-5.10/G15, Option B).
    assert ":CALLS]->(s:Symbol" in cypher
    assert "(f:File" in cypher
    assert ":DEFINES]->(caller)" in cypher
    assert ":USES" not in cypher
    assert ":IMPORTS" not in cypher


async def test_graph_query_existence_check_is_parameterised(
    make_fake_deps, make_agent_state
) -> None:
    """
    The control-flow existence query uses bound params — never interpolation.
    """
    deps = make_fake_deps(symbol_exists=True)
    state = make_agent_state(
        query=Query(symbol="codeoracle.x.y"), snapshot_sha="abc1234"
    )
    await graph_query_node(state, deps)
    graph_repo = cast(_RecordedGraphRepo, deps.graph_repo)
    stmt, params = graph_repo.calls[0]
    assert "$fqn" in stmt
    assert "$sha" in stmt
    assert params == {"fqn": "codeoracle.x.y", "sha": "abc1234"}
