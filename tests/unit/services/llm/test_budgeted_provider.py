"""Plan 4 Task 12b: BudgetedLLMProvider decorator unit tests.

One decorator test pair replaces the five per-provider
test_*_respects_context_budget tests removed in NFR D7a-1 — budget enforcement
now lives in ONE place, so it is tested in ONE place.
"""

import pytest

from codeoracle.adapters.llm.config import MockLLMProviderConfig
from codeoracle.adapters.llm.providers.mock import MockLLMProvider
from codeoracle.models.llm import ChatMessage
from codeoracle.services.context_budget import (
    ContextBudgetExceeded,
    ContextBudgetService,
)
from codeoracle.services.llm import BudgetedLLMProvider


def _mock(tmp_path, *, strict: bool) -> MockLLMProvider:
    return MockLLMProvider(
        MockLLMProviderConfig(scripts_dir=str(tmp_path), strict=strict)
    )


async def test_budgeted_provider_enforces_budget_before_delegating(tmp_path):
    """check() fires BEFORE inner.chat(): an over-budget request never reaches
    the wrapped provider. The mock counts len(text)//4 tokens, so a message far
    longer than four characters exceeds a 1-token cap."""
    wrapped = BudgetedLLMProvider(
        _mock(tmp_path, strict=True), ContextBudgetService(max_tokens=1)
    )
    with pytest.raises(ContextBudgetExceeded):
        await wrapped.chat(
            [ChatMessage(role="user", content="far longer than one token")]
        )


async def test_budgeted_provider_delegates_within_budget(tmp_path):
    """Within budget, chat() reaches the inner provider (a non-strict mock
    returns an empty ChatResponse for an unknown fingerprint), and the other
    LLMProvider methods delegate unchanged."""
    inner = _mock(tmp_path, strict=False)
    wrapped = BudgetedLLMProvider(
        inner, ContextBudgetService(max_tokens=24_000)
    )
    response = await wrapped.chat([ChatMessage(role="user", content="ping")])
    assert response.content == ""
    assert wrapped.model_name() == inner.model_name() == "mock"
    assert wrapped.count_tokens("12345678") == 2  # mock stub: len(text) // 4
    await wrapped.aclose()  # delegates to the inner no-op; must not raise
