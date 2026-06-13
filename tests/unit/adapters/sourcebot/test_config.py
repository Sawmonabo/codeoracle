"""Plan 8 Task 3 — SourcebotConfig environment-loading tests.

Pins:
  1. Env prefix matches the per-adapter convention (CODEORACLE_SOURCEBOT__).
  2. api_key is SecretStr — its __repr__ does NOT leak the value.
  3. Validation ranges: timeout must be > 0; base_url must include scheme.
  4. default_repo is a free-form string but is rejected if empty.

Why SecretStr (not plain str) for api_key: it is redaction layer 1 —
SecretStr.__repr__ masks the value in any log/repr context, regardless
of key. Plan 3's _redact_secrets processor (Task 3) is layer 2, masking
event-dict values whose key matches api_key|secret|token|password.
"""

import pytest
from pydantic import SecretStr, ValidationError

from codeoracle.adapters.sourcebot.config import SourcebotConfig


def _env_block(monkeypatch: pytest.MonkeyPatch, **kw: str) -> None:
    """Helper: set all required env vars + any caller overrides."""
    defaults = {
        "CODEORACLE_SOURCEBOT__BASE_URL": "http://sourcebot.test:3000",
        "CODEORACLE_SOURCEBOT__API_KEY": "test-key-123",
        "CODEORACLE_SOURCEBOT__TIMEOUT_SECONDS": "5.0",
        "CODEORACLE_SOURCEBOT__DEFAULT_REPO": (
            "github.com/Sawmonabo/codeoracle"
        ),
    }
    defaults.update(kw)
    for k, v in defaults.items():
        monkeypatch.setenv(k, v)


def test_config_loads_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    _env_block(monkeypatch)
    cfg = SourcebotConfig.model_validate({})
    assert cfg.base_url == "http://sourcebot.test:3000"
    assert isinstance(cfg.api_key, SecretStr)
    assert cfg.api_key.get_secret_value() == "test-key-123"
    assert cfg.timeout_seconds == 5.0
    assert cfg.default_repo == "github.com/Sawmonabo/codeoracle"


def test_api_key_does_not_appear_in_repr(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _env_block(
        monkeypatch, CODEORACLE_SOURCEBOT__API_KEY="super-secret-do-not-log"
    )
    cfg = SourcebotConfig.model_validate({})
    rendered = repr(cfg) + str(cfg.api_key)
    assert "super-secret-do-not-log" not in rendered, (
        "api_key leaked via repr — SecretStr repr-masking is Plan 3 "
        "redaction layer 1"
    )


def test_base_url_requires_scheme(monkeypatch: pytest.MonkeyPatch) -> None:
    _env_block(
        monkeypatch, CODEORACLE_SOURCEBOT__BASE_URL="sourcebot.test:3000"
    )
    with pytest.raises(ValidationError) as exc:
        SourcebotConfig.model_validate({})
    assert (
        "must include scheme" in str(exc.value)
        or "url" in str(exc.value).lower()
    )


def test_timeout_seconds_must_be_positive(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _env_block(monkeypatch, CODEORACLE_SOURCEBOT__TIMEOUT_SECONDS="0")
    with pytest.raises(ValidationError):
        SourcebotConfig.model_validate({})


def test_default_repo_rejects_empty_string(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _env_block(monkeypatch, CODEORACLE_SOURCEBOT__DEFAULT_REPO="")
    with pytest.raises(ValidationError):
        SourcebotConfig.model_validate({})
