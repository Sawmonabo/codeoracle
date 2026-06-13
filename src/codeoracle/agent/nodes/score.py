"""The score node — DraftClaim -> Claim via the trust matrix."""

from codeoracle.agent.dependencies import AgentDependencies
from codeoracle.agent.runtime import NodeUpdate, agent_node
from codeoracle.agent.state import AgentState
from codeoracle.core.exceptions import AgentError


@agent_node(None)
async def score_node(state: AgentState, deps: AgentDependencies) -> NodeUpdate:
    """Score the draft claim into a trust-weighted Claim.

    ``TrustMatrixService.score_each`` is the only DraftClaim -> Claim producer
    in non-test code — it stamps each citation with a ``source_trust`` the LLM
    cannot forge. The call is synchronous (pure computation, no I/O), so it is
    not awaited. This node has no §8.5 gate-9 span — ``@agent_node`` is given
    ``None`` — but still gets the short-circuit and exception-trapping wrapper.
    """
    draft = state["draft_claim"]
    if draft is None:
        raise AgentError("score node reached with no draft claim")
    return {"claim": deps.trust_matrix.score_each(draft)}
