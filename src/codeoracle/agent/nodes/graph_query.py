"""The graph_query node — symbol existence check + GRAPH_QUERY citation."""

from codeoracle.agent.dependencies import AgentDependencies
from codeoracle.agent.runtime import (
    SPAN_GRAPH_QUERY,
    NodeUpdate,
    agent_node,
    resolve_to_citation,
)
from codeoracle.agent.state import AgentState
from codeoracle.models import (
    EvidenceHandle,
    HandleType,
    Verdict,
    VerdictStatus,
)

# Existence check — parameterised, run directly. A control query, never a
# citation, so bound parameters are correct and there is no injection concern.
# ``(:Symbol {fqn, snapshot_sha})`` is the one graph-schema fact stable across
# Plans 5 and 6. The btree property indexes that keep this an Index Scan rather
# than a per-query Seq Scan over the whole Symbol label (Symbol.fqn /
# Symbol.snapshot_sha) are created in Plan 3 ``bootstrap_db.sql`` — NFR D3-1.
_EXISTS_CYPHER = (
    "MATCH (s:Symbol {fqn: $fqn, snapshot_sha: $sha}) "
    "RETURN s.fqn AS fqn LIMIT 1"
)

# Callers-query fan-out bound (NFR D3-3). A high-fan-in symbol — a logger, a
# base class, a shared util — can be called from thousands of files; an
# unbounded callers result becomes one multi-KB GRAPH_QUERY excerpt that blows
# the compose-prompt budget and is fragile to byte-for-byte re-match at
# verification. 50 is a deliberate, tunable cap (cf. KNN_K); changing it is a
# contract change pinned by test_callers_cypher_is_deterministically_ordered.
_CALLERS_LIMIT = 50


def build_callers_cypher(fqn: str, sha: str) -> str:
    """Build the literal callers query embedded in the GRAPH_QUERY locator.

    ``fqn`` and ``sha`` are interpolated *literally* — Plan 8's ``cypher://``
    resolver re-runs the string verbatim with no parameters. The caller MUST
    have validated ``fqn`` with ``is_safe_symbol_fqn`` first (invariant 3);
    ``intent_node`` does.

    The query carries a deterministic ``ORDER BY result`` so the stringified
    rows are row-order-stable between retrieval and verification (invariant 2)
    — without it AGE/openCypher may return rows in a different order on the
    re-run and the citation would be spuriously REFUTED.

    It is then bounded by ``LIMIT _CALLERS_LIMIT`` (NFR D3-3). The bound is
    verification-safe because it lives *inside* the locator string: the
    verifier re-runs the identical bounded query, and the deterministic order
    makes the first ``_CALLERS_LIMIT`` rows the same set on both runs, so the
    excerpt byte-matches. The excerpt is a bounded view, honest by
    construction — every path shown is a real caller; exhaustiveness is never
    asserted.
    """
    # File path accessor is `file_path` (the parser's per-file partition key,
    # shared with replace_file_subgraph's DELETE) — File has no bare `path`
    # property; `AS result` is only the output-column alias.
    return (
        f"MATCH (caller:Symbol {{snapshot_sha: '{sha}'}})"
        f"-[:CALLS]->(s:Symbol {{fqn: '{fqn}', snapshot_sha: '{sha}'}}) "
        f"MATCH (f:File {{snapshot_sha: '{sha}'}})-[:DEFINES]->(caller) "
        f"RETURN DISTINCT f.file_path AS result "
        f"ORDER BY result "
        f"LIMIT {_CALLERS_LIMIT}"
    )


@agent_node(SPAN_GRAPH_QUERY)
async def graph_query_node(
    state: AgentState, deps: AgentDependencies
) -> NodeUpdate:
    """
    Check the symbol exists in the graph; if so, emit a GRAPH_QUERY citation.

    Symbol absent → set the terminal UNKNOWN verdict (the §8.7 reason string)
    and short-circuit: every downstream node is skipped by ``@agent_node``.
    Symbol present → build a ``cypher://`` handle for the callers query and
    resolve it; the resolved, stringified result is the citation's excerpt
    (invariant 1).
    """
    fqn = state["query"].symbol
    sha = state["snapshot_sha"]

    rows = await deps.graph_repo.execute_cypher(
        _EXISTS_CYPHER, {"fqn": fqn, "sha": sha}
    )
    if not rows:
        return {
            "symbol_found": False,
            "verdict": Verdict(
                status=VerdictStatus.UNKNOWN,
                reason=f"symbol not found in graph at snapshot {sha}",
                resolved_citations=[],
            ),
        }

    handle = EvidenceHandle(
        type=HandleType.GRAPH_QUERY,
        locator=f"cypher://{build_callers_cypher(fqn, sha)}",
        snapshot_sha=sha,
    )
    citation = await resolve_to_citation(handle, deps.resolver)
    return {"symbol_found": True, "graph_citations": [citation]}
