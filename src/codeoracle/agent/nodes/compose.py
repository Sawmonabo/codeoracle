"""The compose node — LLM evidence selection and DraftClaim assembly.

The LLM authors the explanation prose and *selects* which retrieved evidence
supports or contradicts it; it never authors citation excerpts (invariant 1).
``compose_node`` builds the candidate list, asks the LLM to call
``submit_explanation`` with a statement, a confidence, and evidence indices,
then assembles the ``DraftClaim`` from the platform-owned candidates.
"""

import json

from codeoracle.agent.dependencies import AgentDependencies
from codeoracle.agent.runtime import SPAN_COMPOSE, NodeUpdate, agent_node
from codeoracle.agent.state import AgentState
from codeoracle.core.exceptions import AgentError
from codeoracle.models import DraftCitation, DraftClaim
from codeoracle.models.llm import ChatMessage, ChatResponse, ToolDefinition

# Template ids — one per role. Plan 4's request fingerprint hashes template
# ids,
# not raw bytes, so a cosmetic prompt edit must not shatter the fingerprint
# (invariant 5). The user message's content varies per query; its template_id
# does not — that is the point.
COMPOSE_SYSTEM_TEMPLATE_ID = "agent.compose.system.v1"
COMPOSE_USER_TEMPLATE_ID = "agent.compose.user.v1"

_SYSTEM_PROMPT = """You are CodeOracle's explanation composer.

You are given a symbol and a numbered list of evidence items already \
retrieved and verified by the platform. Your job:

1. Write a precise, self-contained `statement` explaining what the symbol is \
and what the evidence shows about it. Describe only what the evidence \
supports — never speculate beyond it.
2. Give a calibrated `confidence` in [0, 1]: how fully the cited evidence \
supports your statement.
3. Select, by index, the evidence items that SUPPORT the statement \
(`citation_indices`, at least one) and any that CONTRADICT it \
(`contradiction_indices`).

You MUST respond by calling the `submit_explanation` tool. Do not quote or \
paraphrase evidence text into the statement — the platform owns the evidence \
excerpts; you only reference items by index."""

# A flat, hand-authored JSON-Schema — no nested `$defs`. The LLM never has to
# construct a citation object; it only writes prose and picks indices.
SUBMIT_EXPLANATION_TOOL = ToolDefinition(
    name="submit_explanation",
    description=(
        "Submit the final explanation of the symbol, citing the supporting "
        "and contradicting evidence by its list index."
    ),
    parameters_schema={
        "type": "object",
        "properties": {
            "statement": {
                "type": "string",
                "description": (
                    "A precise, self-contained explanation of the symbol."
                ),
            },
            "confidence": {
                "type": "number",
                "minimum": 0.0,
                "maximum": 1.0,
                "description": (
                    "Calibrated confidence that the evidence supports the "
                    "statement."
                ),
            },
            "citation_indices": {
                "type": "array",
                "items": {"type": "integer"},
                "description": (
                    "Indices of evidence items that support the statement "
                    "(at least one)."
                ),
            },
            "contradiction_indices": {
                "type": "array",
                "items": {"type": "integer"},
                "description": (
                    "Indices of evidence items that contradict the "
                    "statement, if any."
                ),
            },
        },
        "required": ["statement", "confidence", "citation_indices"],
    },
)


def build_compose_messages(
    symbol: str, candidates: list[DraftCitation]
) -> list[ChatMessage]:
    """Build the compose prompt: a system message and a user message listing
    every candidate citation with its index, handle type, and excerpt.

    Both messages carry a ``template_id`` (invariant 5).
    """
    lines = [f"Symbol to explain: {symbol}", "", "Evidence candidates:"]
    for index, citation in enumerate(candidates):
        header = (
            f"\n[{index}] ({citation.handle.type.value}) "
            f"{citation.handle.locator}"
        )
        lines.append(header)
        lines.append(citation.excerpt)
    return [
        ChatMessage(
            role="system",
            content=_SYSTEM_PROMPT,
            template_id=COMPOSE_SYSTEM_TEMPLATE_ID,
        ),
        ChatMessage(
            role="user",
            content="\n".join(lines),
            template_id=COMPOSE_USER_TEMPLATE_ID,
        ),
    ]


def _validate_index_list(
    name: str, value: object, candidate_count: int
) -> list[int]:
    """Validate a JSON value is a list of in-range integers; return it deduped.

    ``bool`` is rejected explicitly — ``bool`` is a subclass of ``int`` in
    Python and JSON ``true``/``false`` parse to ``bool``, but a boolean is not
    a valid evidence index.
    """
    if not isinstance(value, list):
        raise AgentError(f"tool call field {name!r} must be a list")
    indices: list[int] = []
    for item in value:
        if isinstance(item, bool) or not isinstance(item, int):
            raise AgentError(
                f"tool call field {name!r} must contain only integers"
            )
        if item < 0 or item >= candidate_count:
            raise AgentError(
                f"tool call field {name!r} has out-of-range index {item} "
                f"(candidate count is {candidate_count})"
            )
        indices.append(item)
    return list(dict.fromkeys(indices))  # dedupe, preserve first-seen order


def parse_submit_explanation(
    response: ChatResponse, candidate_count: int
) -> tuple[str, float, list[int], list[int]]:
    """Parse and validate the LLM's ``submit_explanation`` tool call.

    Returns ``(statement, confidence, citation_indices,
    contradiction_indices)``.
    Raises ``AgentError`` on any malformed response — compose is single-shot
    (Deviation 8), so a malformed call ends the query as FAILED. A truncated
    response is caught here too: a half-written tool call fails JSON parsing.
    """
    if not response.tool_calls:
        raise AgentError("LLM did not call submit_explanation")
    call = response.tool_calls[0]
    if call.name != "submit_explanation":
        raise AgentError(f"LLM called unexpected tool {call.name!r}")
    try:
        args = json.loads(call.arguments_json)
    except json.JSONDecodeError as exc:
        raise AgentError(
            f"tool call arguments are not valid JSON: {exc}"
        ) from exc
    if not isinstance(args, dict):
        raise AgentError("tool call arguments are not a JSON object")

    statement = args.get("statement")
    if not isinstance(statement, str) or not statement.strip():
        raise AgentError("tool call is missing a non-empty 'statement'")

    confidence = args.get("confidence")
    if isinstance(confidence, bool) or not isinstance(
        confidence, (int, float)
    ):
        raise AgentError("tool call 'confidence' must be a number")
    if not 0.0 <= float(confidence) <= 1.0:
        raise AgentError(
            f"tool call 'confidence' {confidence} is outside [0, 1]"
        )

    citation_indices = _validate_index_list(
        "citation_indices", args.get("citation_indices"), candidate_count
    )
    if not citation_indices:
        raise AgentError(
            "tool call 'citation_indices' must select at least one item"
        )
    contradiction_indices = _validate_index_list(
        "contradiction_indices",
        args.get("contradiction_indices", []),
        candidate_count,
    )
    return (
        statement,
        float(confidence),
        citation_indices,
        contradiction_indices,
    )


@agent_node(SPAN_COMPOSE)
async def compose_node(
    state: AgentState, deps: AgentDependencies
) -> NodeUpdate:
    """Ask the LLM to select evidence; assemble the DraftClaim from candidates.

    Candidate order is fixed — ``graph_citations`` then ``embed_citations`` —
    because the LLM's tool call references candidates by index. The LLM never
    authors excerpts; ``compose_node`` builds the ``DraftClaim`` entirely from
    the platform-owned candidate citations (invariant 1) and asserts every
    cited handle type is in the query plan (invariant 8).
    """
    candidates = list(state["graph_citations"]) + list(
        state["embed_citations"]
    )
    if not candidates:
        raise AgentError("no evidence candidates were retrieved")

    messages = build_compose_messages(state["query"].symbol, candidates)
    response = await deps.llm.chat(messages, tools=[SUBMIT_EXPLANATION_TOOL])
    statement, confidence, cite_idx, contra_idx = parse_submit_explanation(
        response, len(candidates)
    )

    draft = DraftClaim(
        statement=statement,
        citations=[candidates[i] for i in cite_idx],
        confidence=confidence,
        contradictions=[candidates[i] for i in contra_idx],
    )

    plan = set(state["plan_handle_types"])
    for citation in draft.citations:
        if citation.handle.type not in plan:
            raise AgentError(
                "composed citation type "
                f"{citation.handle.type.value!r} is not in the query plan "
                f"{sorted(t.value for t in plan)}"
            )
    return {"draft_claim": draft}
