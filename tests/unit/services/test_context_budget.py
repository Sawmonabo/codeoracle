"""Plan 4 Task 3: ContextBudgetService unit tests."""

import pytest

from codeoracle.core.exceptions import CodeOracleError
from codeoracle.models.llm import ChatMessage
from codeoracle.services.context_budget import (
    ContextBudgetExceeded,
    ContextBudgetService,
)


def test_max_tokens_must_be_positive():
    with pytest.raises(ValueError, match="max_tokens"):
        ContextBudgetService(max_tokens=0)
    with pytest.raises(ValueError, match="max_tokens"):
        ContextBudgetService(max_tokens=-1)


def test_check_under_budget_returns_total():
    svc = ContextBudgetService(max_tokens=100)
    msgs = [
        ChatMessage(role="user", content="hello"),
        ChatMessage(role="assistant", content="hi"),
    ]
    # token_counter: len(content) — keep it deterministic and easy to assert
    total = svc.check(msgs, token_counter=len)
    assert total == len("hello") + len("hi")


def test_check_at_budget_returns_total_no_raise():
    svc = ContextBudgetService(max_tokens=5)
    msgs = [ChatMessage(role="user", content="hello")]  # len == 5
    assert svc.check(msgs, token_counter=len) == 5


def test_check_over_budget_raises_context_budget_exceeded():
    svc = ContextBudgetService(max_tokens=4)
    msgs = [ChatMessage(role="user", content="hello")]
    with pytest.raises(ContextBudgetExceeded) as exc_info:
        svc.check(msgs, token_counter=len)
    assert "5 tokens" in str(exc_info.value)
    assert "max 4" in str(exc_info.value)


def test_context_budget_exceeded_is_oracle_error():
    """
    All platform exceptions must inherit CodeOracleError so catch-all handlers
    work.
    """
    assert issubclass(ContextBudgetExceeded, CodeOracleError)


def test_max_tokens_property_exposes_configured_value():
    svc = ContextBudgetService(max_tokens=24_000)
    assert svc.max_tokens == 24_000


def test_empty_messages_returns_zero():
    svc = ContextBudgetService(max_tokens=100)
    assert svc.check([], token_counter=len) == 0
