"""
Plan 10 Task 7 — the compose node: LLM evidence selection + DraftClaim
assembly.
"""

import json

import pytest

from codeoracle.agent.nodes.compose import (
    COMPOSE_SYSTEM_TEMPLATE_ID,
    COMPOSE_USER_TEMPLATE_ID,
    SUBMIT_EXPLANATION_TOOL,
    build_compose_messages,
    compose_node,
    parse_submit_explanation,
)
from codeoracle.core.exceptions import AgentError
from codeoracle.models import DraftCitation, EvidenceHandle, HandleType
from codeoracle.models.llm import ChatResponse, TokenUsage, ToolCall


def _response(
    arguments_json: str,
    *,
    tool_name: str = "submit_explanation",
    tool_calls: list[ToolCall] | None = None,
) -> ChatResponse:
    calls = (
        tool_calls
        if tool_calls is not None
        else [ToolCall(id="c1", name=tool_name, arguments_json=arguments_json)]
    )
    return ChatResponse(
        content="",
        usage=TokenUsage(input_tokens=1, output_tokens=1),
        finish_reason="tool_calls",
        tool_calls=calls,
    )


def _args(
    citation_indices: list[int] | None = None,
    contradiction_indices: list[int] | None = None,
    *,
    statement: str = "It does X.",
    confidence: float = 0.9,
) -> str:
    return json.dumps(
        {
            "statement": statement,
            "confidence": confidence,
            "citation_indices": citation_indices
            if citation_indices is not None
            else [0, 1],
            "contradiction_indices": contradiction_indices or [],
        }
    )


def _citation(handle_type: HandleType, locator: str) -> DraftCitation:
    return DraftCitation(
        handle=EvidenceHandle(
            type=handle_type, locator=locator, snapshot_sha="abc1234"
        ),
        excerpt="platform-owned excerpt",
    )


# --- the submit_explanation tool schema ---


def test_submit_explanation_tool_schema_is_flat() -> None:
    schema = SUBMIT_EXPLANATION_TOOL.parameters_schema
    assert (
        "$defs" not in schema
    )  # flat, hand-authored — no nested Pydantic refs
    properties = schema["properties"]
    assert isinstance(properties, dict)
    assert set(properties) == {
        "statement",
        "confidence",
        "citation_indices",
        "contradiction_indices",
    }
    assert SUBMIT_EXPLANATION_TOOL.name == "submit_explanation"


# --- build_compose_messages ---


def test_compose_messages_carry_template_ids() -> None:
    """
    invariant 5 — every ChatMessage the agent builds has a non-None
    template_id.
    """
    msgs = build_compose_messages(
        "codeoracle.x.y", [_citation(HandleType.CODE_SPAN, "file://a.py:1-2")]
    )
    assert [m.template_id for m in msgs] == [
        COMPOSE_SYSTEM_TEMPLATE_ID,
        COMPOSE_USER_TEMPLATE_ID,
    ]
    assert all(m.template_id is not None for m in msgs)


def test_compose_messages_index_every_candidate() -> None:
    candidates = [
        _citation(HandleType.GRAPH_QUERY, "cypher://MATCH (n) RETURN n"),
        _citation(HandleType.CODE_SPAN, "file://a.py:1-2"),
    ]
    user = build_compose_messages("codeoracle.x.y", candidates)[1].content
    assert "[0]" in user
    assert "[1]" in user
    assert "codeoracle.x.y" in user


# --- parse_submit_explanation ---


def test_parse_accepts_a_well_formed_tool_call() -> None:
    statement, confidence, cites, contras = parse_submit_explanation(
        _response(_args(citation_indices=[0, 2], contradiction_indices=[1])),
        candidate_count=3,
    )
    assert statement == "It does X."
    assert confidence == 0.9
    assert cites == [0, 2]
    assert contras == [1]


def test_parse_dedupes_indices_preserving_order() -> None:
    _, _, cites, _ = parse_submit_explanation(
        _response(_args(citation_indices=[2, 0, 2, 0])), candidate_count=3
    )
    assert cites == [2, 0]


def test_parse_rejects_missing_tool_call() -> None:
    with pytest.raises(AgentError, match="did not call submit_explanation"):
        parse_submit_explanation(
            _response("", tool_calls=[]), candidate_count=3
        )


def test_parse_rejects_wrong_tool_name() -> None:
    with pytest.raises(AgentError, match="unexpected tool"):
        parse_submit_explanation(
            _response(_args(), tool_name="something_else"), candidate_count=3
        )


def test_parse_rejects_invalid_json() -> None:
    with pytest.raises(AgentError, match="not valid JSON"):
        parse_submit_explanation(_response("{not json"), candidate_count=3)


def test_parse_rejects_empty_statement() -> None:
    with pytest.raises(AgentError, match="non-empty 'statement'"):
        parse_submit_explanation(
            _response(_args(statement="   ")), candidate_count=3
        )


def test_parse_rejects_confidence_out_of_range() -> None:
    with pytest.raises(AgentError, match="outside"):
        parse_submit_explanation(
            _response(_args(confidence=1.5)), candidate_count=3
        )


def test_parse_rejects_out_of_range_index() -> None:
    with pytest.raises(AgentError, match="out-of-range"):
        parse_submit_explanation(
            _response(_args(citation_indices=[0, 9])), candidate_count=3
        )


def test_parse_rejects_empty_citation_indices() -> None:
    with pytest.raises(AgentError, match="at least one"):
        parse_submit_explanation(
            _response(_args(citation_indices=[])), candidate_count=3
        )


# --- compose_node ---


async def test_compose_assembles_draft_claim_from_candidates(
    make_fake_deps, make_agent_state
) -> None:
    gc = _citation(HandleType.GRAPH_QUERY, "cypher://MATCH (n) RETURN n")
    ec = _citation(HandleType.CODE_SPAN, "file://a.py:1-9")
    deps = make_fake_deps(llm_tool_args=_args(citation_indices=[0, 1]))
    state = make_agent_state(graph_citations=[gc], embed_citations=[ec])
    update = await compose_node(state, deps)
    draft = update["draft_claim"]
    assert draft is not None
    assert draft.statement == "It does X."
    assert len(draft.citations) == 2
    # invariant 1 — every excerpt is platform-owned, never LLM-authored
    citations = draft.citations
    assert all(c.excerpt == "platform-owned excerpt" for c in citations)


async def test_compose_candidate_order_is_graph_then_embed(
    make_fake_deps, make_agent_state
) -> None:
    gc = _citation(HandleType.GRAPH_QUERY, "cypher://MATCH (n) RETURN n")
    ec = _citation(HandleType.CODE_SPAN, "file://e.py:1-9")
    deps = make_fake_deps(llm_tool_args=_args(citation_indices=[0]))
    state = make_agent_state(graph_citations=[gc], embed_citations=[ec])
    update = await compose_node(state, deps)
    # index 0 is always the graph citation
    draft = update["draft_claim"]
    assert draft is not None
    assert draft.citations[0].handle.type == HandleType.GRAPH_QUERY


async def test_compose_fails_when_llm_makes_no_tool_call(
    make_fake_deps, make_agent_state
) -> None:
    no_call = ChatResponse(
        content="cannot help",
        usage=TokenUsage(input_tokens=1, output_tokens=1),
        finish_reason="stop",
        tool_calls=[],
    )
    deps = make_fake_deps(llm_response=no_call)
    state = make_agent_state(
        graph_citations=[
            _citation(HandleType.GRAPH_QUERY, "cypher://MATCH (n) RETURN n")
        ]
    )
    update = await compose_node(state, deps)
    assert "did not call submit_explanation" in str(update["error"])


async def test_compose_fails_when_no_candidates(
    make_fake_deps, make_agent_state
) -> None:
    deps = make_fake_deps()
    state = make_agent_state(graph_citations=[], embed_citations=[])
    update = await compose_node(state, deps)
    assert "no evidence candidates" in str(update["error"])
