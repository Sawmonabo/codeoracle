"""The verify node: Claim -> Verdict through the verifier service."""

from codeoracle.agent.dependencies import AgentDependencies
from codeoracle.agent.runtime import SPAN_VERIFY, NodeUpdate, agent_node
from codeoracle.agent.state import AgentState
from codeoracle.core.exceptions import AgentError


@agent_node(SPAN_VERIFY)
async def verify_node(
    state: AgentState, deps: AgentDependencies
) -> NodeUpdate:
    claim = state["claim"]
    if claim is None:
        raise AgentError("verify node reached with no claim")
    return {"verdict": await deps.verifier.verify(claim)}
