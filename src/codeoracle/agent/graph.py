"""Assembly of the explain-symbol agent graph.

``build_agent_graph`` wires the six nodes into a sequential LangGraph
``StateGraph``; ``run_agent_query`` seeds the initial state, invokes the
compiled graph, and maps the final state to a ``QueryResult``.
"""

from collections.abc import Awaitable, Callable
from typing import Any, cast
from uuid import uuid4

from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from codeoracle.agent.dependencies import AgentDependencies
from codeoracle.agent.nodes.compose import compose_node
from codeoracle.agent.nodes.embed_query import embed_query_node
from codeoracle.agent.nodes.graph_query import graph_query_node
from codeoracle.agent.nodes.intent import intent_node
from codeoracle.agent.nodes.score import score_node
from codeoracle.agent.nodes.verify import verify_node
from codeoracle.agent.runtime import NodeFn, NodeUpdate
from codeoracle.agent.state import AgentState
from codeoracle.models import Query, QueryResult, QueryStatus


def _with_deps(
    node: NodeFn, deps: AgentDependencies
) -> Callable[[AgentState], Awaitable[NodeUpdate]]:
    """
    Bind ``deps`` to a node, yielding the single-argument coroutine LangGraph
    invokes.

    An explicit ``async def`` (not ``functools.partial``) so LangGraph
    unambiguously recognises the node as asynchronous — a ``partial`` over a
    coroutine function relies on the runner unwrapping it correctly.
    """

    async def bound(state: AgentState) -> NodeUpdate:
        return await node(state, deps)

    return bound


def build_agent_graph(deps: AgentDependencies) -> CompiledStateGraph:
    """Compile the explain-symbol agent graph for one query.

    The graph is a plain linear chain — ``intent -> graph_query -> embed_query
    -> compose -> score -> verify`` — with no conditional edges. Short-circuit
    on a terminal ``error`` / ``verdict`` is the ``@agent_node`` decorator's
    job
    (invariant 10). ``deps`` is bound into every node, so the compiled graph
    belongs to exactly one query (invariant 9); compilation is graph validation
    only — microseconds — so compile-per-query costs nothing.
    """
    builder = StateGraph(cast(Any, AgentState))
    builder.add_node("intent", cast(Any, _with_deps(intent_node, deps)))
    builder.add_node(
        "graph_query", cast(Any, _with_deps(graph_query_node, deps))
    )
    builder.add_node(
        "embed_query", cast(Any, _with_deps(embed_query_node, deps))
    )
    builder.add_node("compose", cast(Any, _with_deps(compose_node, deps)))
    builder.add_node("score", cast(Any, _with_deps(score_node, deps)))
    builder.add_node("verify", cast(Any, _with_deps(verify_node, deps)))

    builder.add_edge(START, "intent")
    builder.add_edge("intent", "graph_query")
    builder.add_edge("graph_query", "embed_query")
    builder.add_edge("embed_query", "compose")
    builder.add_edge("compose", "score")
    builder.add_edge("score", "verify")
    builder.add_edge("verify", END)
    return cast(CompiledStateGraph, builder.compile())


async def run_agent_query(
    graph: CompiledStateGraph, query: Query
) -> QueryResult:
    """Run one explain-symbol query through a compiled agent graph.

    The function builds the initial ``AgentState``, invokes the graph, and
    maps the final state into a ``QueryResult``. It is the outer defensive
    boundary for query execution: graph failures are converted into failed
    results instead of being raised to the caller.

    Terminal outcomes:

    * ``error`` is set: return ``QueryResult.failed``.
    * ``verdict`` is set and ``claim`` is None:
      return a COMPLETE result with no claim. This is the
      symbol-not-found / UNKNOWN path.
    * ``claim`` and ``verdict`` are both set: return ``QueryResult.proven``.

    Node-level failures are normally trapped by ``@agent_node``; the graph
    invoke is still wrapped here to preserve the non-raising query contract.
    """
    trace_id = uuid4().hex
    initial: AgentState = {
        "query": query,
        "trace_id": trace_id,
        "snapshot_sha": "",
        "plan_handle_types": [],
        "symbol_found": False,
        "graph_citations": [],
        "embed_citations": [],
        "draft_claim": None,
        "claim": None,
        "verdict": None,
        "error": None,
    }
    try:
        final = cast(AgentState, await graph.ainvoke(initial))
    except Exception as exc:
        return QueryResult.failed(trace_id, f"agent graph crashed: {exc}")

    error = final["error"]
    if error is not None:
        return QueryResult.failed(trace_id, error)
    verdict = final["verdict"]
    if verdict is None:
        return QueryResult.failed(
            trace_id, "agent finished without producing a verdict"
        )
    claim = final["claim"]
    if claim is None:
        # Symbol-not-found: a COMPLETE query with an UNKNOWN verdict and
        # no claim.
        return QueryResult(
            trace_id=trace_id, status=QueryStatus.COMPLETE, verdict=verdict
        )
    return QueryResult.proven(trace_id, claim, verdict)
