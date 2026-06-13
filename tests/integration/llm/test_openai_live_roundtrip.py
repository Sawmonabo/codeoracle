import os

import pytest
from pydantic import SecretStr

from codeoracle.adapters.llm.config import OpenAIProviderConfig
from codeoracle.adapters.llm.providers.openai import OpenAIProvider
from codeoracle.models.llm import ChatMessage

pytestmark = pytest.mark.live_llm


@pytest.mark.asyncio
async def test_openai_provider_live_roundtrip() -> None:
    api_key = os.environ.get("CODEORACLE_LLM_OPENAI__API_KEY")
    if not api_key:
        pytest.skip(reason="CODEORACLE_LLM_OPENAI__API_KEY is required")

    provider = OpenAIProvider(
        OpenAIProviderConfig(
            api_key=SecretStr(api_key),
            model=os.environ.get(
                "CODEORACLE_LLM_OPENAI__MODEL", "gpt-4o-mini"
            ),
            base_url=os.environ.get("CODEORACLE_LLM_OPENAI__BASE_URL") or None,
            request_timeout_seconds=30.0,
        )
    )
    try:
        response = await provider.chat(
            [ChatMessage(role="user", content="Reply exactly: codeoracle-ok")]
        )
    finally:
        await provider.aclose()

    assert "codeoracle-ok" in response.content.lower()
