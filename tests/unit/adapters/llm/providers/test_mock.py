"""Plan 4 Task 7: MockLLMProvider unit tests."""

import pytest
import yaml

from codeoracle.adapters.llm.config import MockLLMProviderConfig
from codeoracle.adapters.llm.fingerprint import request_fingerprint
from codeoracle.adapters.llm.providers.mock import MockLLMProvider
from codeoracle.core.exceptions import LLMProviderError
from codeoracle.models.llm import ChatMessage


def _write_fixture(
    scripts_dir, name: str, fingerprint: str, content: str
) -> None:
    """Write one fixture entry to a YAML file."""
    (scripts_dir / f"{name}.yaml").write_text(
        yaml.safe_dump(
            [
                {
                    "request_fingerprint": fingerprint,
                    "response": {
                        "content": content,
                        "usage": {"input_tokens": 10, "output_tokens": 20},
                        "finish_reason": "stop",
                        "tool_calls": [],
                    },
                },
            ]
        )
    )


async def test_mock_returns_scripted_response_on_known_fingerprint(tmp_path):
    msgs = [ChatMessage(role="user", content="ping", template_id="ping_v1")]
    # Match MockLLMProvider.chat()'s fingerprinting: model=model_name()="mock",
    # structured_output=None — keeps the seeded fingerprint identical to the
    # one the provider will compute at lookup time.
    fp = request_fingerprint(msgs, model="mock")
    _write_fixture(tmp_path, "test_one", fp, "pong")

    provider = MockLLMProvider(
        MockLLMProviderConfig(scripts_dir=str(tmp_path), strict=True),
    )
    response = await provider.chat(msgs)
    assert response.content == "pong"
    assert response.usage.input_tokens == 10
    assert response.usage.output_tokens == 20
    assert response.finish_reason == "stop"


async def test_mock_strict_unknown_fingerprint_raises(tmp_path):
    """Strict mode = fixture rot must surface immediately."""
    provider = MockLLMProvider(
        MockLLMProviderConfig(scripts_dir=str(tmp_path), strict=True),
    )
    msgs = [ChatMessage(role="user", content="hi", template_id="hi_v1")]
    with pytest.raises(LLMProviderError) as exc_info:
        await provider.chat(msgs)
    assert "no fixture" in str(exc_info.value)


async def test_mock_non_strict_unknown_fingerprint_returns_default(tmp_path):
    provider = MockLLMProvider(
        MockLLMProviderConfig(scripts_dir=str(tmp_path), strict=False),
    )
    msgs = [ChatMessage(role="user", content="hi", template_id="hi_v1")]
    response = await provider.chat(msgs)
    assert response.content == ""
    assert response.usage.input_tokens == 0
    assert response.finish_reason == "stop"


async def test_mock_duplicate_fingerprint_in_fixture_raises(tmp_path):
    """Loading two fixtures with the same fingerprint must fail at __init__."""
    msgs = [ChatMessage(role="user", content="x", template_id="t1")]
    fp = request_fingerprint(msgs, model="mock")
    _write_fixture(tmp_path, "a", fp, "first")
    _write_fixture(tmp_path, "b", fp, "second")
    with pytest.raises(LLMProviderError) as exc_info:
        MockLLMProvider(
            MockLLMProviderConfig(scripts_dir=str(tmp_path), strict=True),
        )
    assert "duplicate request_fingerprint" in str(exc_info.value)


async def test_mock_count_tokens_deterministic():
    provider = MockLLMProvider(
        MockLLMProviderConfig(scripts_dir="nonexistent_dir", strict=False),
    )
    assert provider.count_tokens("12345678") == 2  # 8 // 4
    assert provider.count_tokens("") == 0


async def test_mock_model_name():
    provider = MockLLMProvider(
        MockLLMProviderConfig(scripts_dir="nonexistent_dir", strict=False),
    )
    assert provider.model_name() == "mock"


async def test_mock_missing_scripts_dir_loads_empty_no_error(tmp_path):
    """Bootstrap case — directory doesn't exist yet. Should not raise; strict
    mode still surfaces on first chat()."""
    provider = MockLLMProvider(
        MockLLMProviderConfig(
            scripts_dir=str(tmp_path / "does_not_exist"), strict=False
        ),
    )
    msgs = [ChatMessage(role="user", content="x", template_id="t1")]
    response = await provider.chat(msgs)
    assert response.content == ""
