"""The intent node — deterministic query validation and snapshot resolution."""

from codeoracle.agent.dependencies import AgentDependencies
from codeoracle.agent.fqn import is_safe_symbol_fqn
from codeoracle.agent.runtime import SPAN_INTENT, NodeUpdate, agent_node
from codeoracle.agent.state import AgentState
from codeoracle.core.exceptions import AgentError
from codeoracle.models import HandleType, SnapshotStatus

# The handle types the explain_symbol plan emits (invariant 8). TRACE_SPAN and
# DB_RECORD are structurally absent — the §8.5 intent-classifier test pins
# this.
EXPLAIN_SYMBOL_HANDLE_TYPES = [
    HandleType.CODE_SPAN,
    HandleType.DOC_PARAGRAPH,
    HandleType.GRAPH_QUERY,
]


@agent_node(SPAN_INTENT)
async def intent_node(
    state: AgentState, deps: AgentDependencies
) -> NodeUpdate:
    """Validate the query and resolve the corpus snapshot.

    Deterministic — no LLM call (Deviation 2). Two responsibilities:

    1. Reject an injection-unsafe symbol *before* any Cypher is built
       (invariant 3) — the symbol is interpolated into literal ``cypher://``
       queries downstream, so this is the injection gate.
    2. Resolve ``snapshot_sha``: the query's explicit pin if present, else the
       latest APPLIED snapshot. Either way the resolved snapshot MUST be
       APPLIED — a pinned SHA that is missing, PENDING, APPLYING, or FAILED is
       rejected, so the retrieval nodes never query a half-applied corpus. No
       APPLIED snapshot at all → the query cannot run.
    """
    query = state["query"]
    if not is_safe_symbol_fqn(query.symbol):
        raise AgentError(
            f"symbol is not an injection-safe FQN: {query.symbol!r}"
        )

    if query.snapshot_sha is not None:
        snapshot = await deps.snapshot_repo.get_by_sha(query.snapshot_sha)
        if snapshot is None or snapshot.status is not SnapshotStatus.APPLIED:
            raise AgentError(
                f"pinned snapshot {query.snapshot_sha!r} is not APPLIED; "
                "cannot resolve a corpus"
            )
        snapshot_sha = snapshot.sha
    else:
        snapshot = await deps.snapshot_repo.get_latest_applied()
        if snapshot is None:
            raise AgentError(
                "no APPLIED snapshot exists; cannot resolve a corpus"
            )
        snapshot_sha = snapshot.sha

    return {
        "snapshot_sha": snapshot_sha,
        "plan_handle_types": list(EXPLAIN_SYMBOL_HANDLE_TYPES),
    }
