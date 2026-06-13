"""Plan 4 Task 5: request_fingerprint unit tests.

Test convention: every call passes `model="test"` explicitly. `model` is a
required keyword arg per the spec §5.7 contract — making it required at
the type level forces every call-site (including future ones) to declare
which model the fixture corresponds to. Tests of the key-parameter axes
(model, temperature, max_tokens, structured_output) vary one axis at a
time and assert the hash changes.
"""

from codeoracle.adapters.llm.fingerprint import request_fingerprint
from codeoracle.models.llm import ChatMessage, ToolDefinition


def test_templated_messages_stable_under_content_edits():
    """Cosmetic edits to template content must not change the hash IF
    template_id is set. This is the load-bearing property of the
    fingerprint algorithm."""
    msg_v1 = ChatMessage(
        role="system",
        content="You are a helpful assistant.",
        template_id="sys_v1",
    )
    msg_v2 = ChatMessage(
        role="system",
        content="You're a helpful assistant.",
        template_id="sys_v1",
    )
    assert request_fingerprint([msg_v1], model="test") == request_fingerprint(
        [msg_v2], model="test"
    )


def test_template_id_change_changes_hash():
    msg_a = ChatMessage(role="system", content="x", template_id="sys_v1")
    msg_b = ChatMessage(role="system", content="x", template_id="sys_v2")
    assert request_fingerprint([msg_a], model="test") != request_fingerprint(
        [msg_b], model="test"
    )


def test_untemplated_messages_hash_by_content():
    """If template_id is None, the hash falls back to content bytes."""
    msg_a = ChatMessage(role="user", content="hello")
    msg_b = ChatMessage(role="user", content="HELLO")
    assert request_fingerprint([msg_a], model="test") != request_fingerprint(
        [msg_b], model="test"
    )


def test_untemplated_identical_content_same_hash():
    msg_a = ChatMessage(role="user", content="hello")
    msg_b = ChatMessage(role="user", content="hello")
    assert request_fingerprint([msg_a], model="test") == request_fingerprint(
        [msg_b], model="test"
    )


def test_templated_and_untemplated_with_same_text_differ():
    """Forces test authors to opt into stability rather than accidentally
    relying on raw-content hashing for templated messages."""
    a = ChatMessage(role="user", content="hi")
    b = ChatMessage(role="user", content="hi", template_id="user_v1")
    assert request_fingerprint([a], model="test") != request_fingerprint(
        [b], model="test"
    )


def test_message_order_matters():
    a = ChatMessage(role="system", content="x", template_id="sys")
    b = ChatMessage(role="user", content="y", template_id="user")
    assert request_fingerprint([a, b], model="test") != request_fingerprint(
        [b, a], model="test"
    )


def test_tools_in_fingerprint():
    msgs = [ChatMessage(role="user", content="x", template_id="user")]
    tool = ToolDefinition(
        name="search", description="...", parameters_schema={"type": "object"}
    )
    no_tools = request_fingerprint(msgs, model="test")
    with_tools = request_fingerprint(msgs, tools=[tool], model="test")
    assert no_tools != with_tools


def test_empty_tools_list_same_as_none():
    msgs = [ChatMessage(role="user", content="x", template_id="user")]
    assert request_fingerprint(
        msgs, tools=[], model="test"
    ) == request_fingerprint(msgs, tools=None, model="test")


def test_hash_is_hex_string_length_64():
    msgs = [ChatMessage(role="user", content="x", template_id="user")]
    h = request_fingerprint(msgs, model="test")
    assert len(h) == 64
    int(h, 16)  # validates hex


def test_model_change_changes_hash():
    """Spec §5.7 "key parameters" axis 1: model. The same messages routed to
    two different models must NOT collide on the same fixture — the fixture
    is implicitly model-specific."""
    msgs = [ChatMessage(role="user", content="x", template_id="user_v1")]
    assert request_fingerprint(
        msgs, model="gpt-4o-mini"
    ) != request_fingerprint(msgs, model="claude-opus-4-8")


def test_temperature_change_changes_hash():
    """Spec §5.7 "key parameters" axis 2: temperature. A deterministic call
    (temperature=0) and a sampled call (temperature=0.7) get different
    fixtures even with identical messages."""
    msgs = [ChatMessage(role="user", content="x", template_id="user_v1")]
    assert request_fingerprint(
        msgs, model="test", temperature=0.0
    ) != request_fingerprint(msgs, model="test", temperature=0.7)


def test_max_tokens_change_changes_hash():
    """Spec §5.7 "key parameters" axis 3: max_tokens. A 100-token cap and a
    4000-token cap can plausibly elicit different responses — fixtures must
    be ceiling-specific so a future per-call max_tokens caller doesn't
    silently inherit a fixture recorded under a different ceiling."""
    msgs = [ChatMessage(role="user", content="x", template_id="user_v1")]
    assert request_fingerprint(
        msgs, model="test", max_tokens=100
    ) != request_fingerprint(msgs, model="test", max_tokens=4000)


def test_structured_output_change_changes_hash():
    """Spec §5.7 "key parameters" axis 4: structured_output. A free-form
    call (structured_output=None), a call expecting a `ResponseA` schema,
    and a call expecting `ResponseB` all resolve to DIFFERENT fixtures —
    even with identical messages — because the response shape they expect
    is part of the contract being mocked."""

    class ResponseA:
        pass

    class ResponseB:
        pass

    msgs = [ChatMessage(role="user", content="x", template_id="user_v1")]
    free_form = request_fingerprint(msgs, model="test")
    schema_a = request_fingerprint(
        msgs, model="test", structured_output=ResponseA
    )
    schema_b = request_fingerprint(
        msgs, model="test", structured_output=ResponseB
    )
    assert free_form != schema_a
    assert free_form != schema_b
    assert schema_a != schema_b


def test_structured_output_qualname_distinguishes_same_named_classes():
    """Two `class Response` defined in different scopes must not collide —
    the canonical form is `<module>.<qualname>`, not just the class name."""

    def make_a():
        class Response:
            pass

        return Response

    def make_b():
        class Response:
            pass

        return Response

    msgs = [ChatMessage(role="user", content="x", template_id="user_v1")]
    fp_a = request_fingerprint(msgs, model="test", structured_output=make_a())
    fp_b = request_fingerprint(msgs, model="test", structured_output=make_b())
    assert fp_a != fp_b
