"""LangGraph state schema for the explain-symbol agent.

``AgentState`` is the substrate every node reads and writes. It is a plain
``TypedDict`` (no reducers): the graph is a strictly sequential chain, so each
key is written exactly once by exactly one node. ``run_agent_query`` seeds
every key before invocation, so nodes may read any key unconditionally.
"""

from typing import TypedDict

from codeoracle.models import (
    Claim,
    DraftCitation,
    DraftClaim,
    HandleType,
    Query,
    Verdict,
)


class AgentState(TypedDict):
    """The agent graph's shared state. Keys are grouped by the node that writes
    them; ``run_agent_query`` seeds defaults for every key before invocation.
    """

    # --- input: seeded by run_agent_query ---
    query: Query
    trace_id: str
    # --- written by intent_node ---
    snapshot_sha: str
    plan_handle_types: list[HandleType]
    # --- written by graph_query_node ---
    symbol_found: bool
    graph_citations: list[DraftCitation]
    # --- written by embed_query_node ---
    embed_citations: list[DraftCitation]
    # --- written by compose_node ---
    draft_claim: DraftClaim | None
    # --- written by score_node ---
    claim: Claim | None
    # --- written by verify_node (also by graph_query_node on symbol-not-found)
    # ---
    verdict: Verdict | None
    # --- terminal short-circuit, written by @agent_node on any failure ---
    error: str | None
