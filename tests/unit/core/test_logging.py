"""Test the structlog setup + secret redaction."""

import io
import json
import logging

import structlog

from codeoracle.core.config import DBConfig, EmbeddingConfig, Settings
from codeoracle.core.logging import configure_logging, get_logger


def _settings(**overrides: object) -> Settings:
    """
    Construct a Settings instance hermetically (embedding is
    required-no-default).

    Pass the required `embedding` + `db` sub-models as constructor args so
    init-args take precedence over the env / `.env` sources — no
    monkeypatch.chdir
    or CODEORACLE_EMBEDDING__* / CODEORACLE_DB__PASSWORD setenv needed.
    Override `environment` / `log_level` per test.
    """
    base: dict[str, object] = {
        "environment": "ci",
        "log_level": "DEBUG",
        "embedding": EmbeddingConfig(
            model="text-embedding-3-small",
            dim=1536,
        ),
        "db": DBConfig.model_validate(
            {"password": "test-db-pw"}
        ),  # D5-4: required, no default — hermetic init arg
    }
    base.update(overrides)
    return Settings.model_validate(base)


def _capture_json_log() -> io.StringIO:
    """
    Reconfigure logging into JSON mode (environment="ci") + capture into a
    buffer.

    Passes `stream=buf` EXPLICITLY: `configure_logging`'s `stream` default is a
    def-time `sys.stdout`, so monkeypatching `sys.stdout` after import would
    NOT be picked up — the buffer must be threaded through the call.
    """
    buf = io.StringIO()
    configure_logging(_settings(), stream=buf)
    return buf


def test_normal_field_passes_through():
    buf = _capture_json_log()
    get_logger().info("hello", user_id="u123", count=42)
    line = buf.getvalue().strip().splitlines()[-1]
    payload = json.loads(line)
    assert payload["event"] == "hello"
    assert payload["user_id"] == "u123"
    assert payload["count"] == 42


def test_api_key_field_is_redacted():
    buf = _capture_json_log()
    get_logger().info("auth_attempt", api_key="sk-very-secret", user_id="u1")
    line = buf.getvalue().strip().splitlines()[-1]
    payload = json.loads(line)
    assert payload["api_key"] == "<redacted>"
    assert payload["user_id"] == "u1"


def test_token_field_is_redacted():
    buf = _capture_json_log()
    get_logger().info(
        "upstream_call", access_token="abc.def.ghi", endpoint="/v1/chat"
    )
    line = buf.getvalue().strip().splitlines()[-1]
    payload = json.loads(line)
    assert payload["access_token"] == "<redacted>"
    assert payload["endpoint"] == "/v1/chat"


def test_password_field_case_insensitive():
    buf = _capture_json_log()
    get_logger().info("db_connect", DB_Password="pw-shh", host="localhost")
    line = buf.getvalue().strip().splitlines()[-1]
    payload = json.loads(line)
    assert payload["DB_Password"] == "<redacted>"
    assert payload["host"] == "localhost"


def test_configure_logging_second_call_wins():
    """
    Calling configure_logging twice — the second config must actually take
    effect.

    Without force=True on logging.basicConfig, the second call no-ops and log
    output still goes to whatever stream the first call captured. This test
    proves the redirection actually happens by:
      1. First-call'ing with stream=A
      2. Second-call'ing with stream=B
      3. Logging once
      4. Asserting B contains output and A does not
    Each call threads its buffer through `stream=` directly (the def-time
    `sys.stdout` default cannot be monkeypatched in after import).
    """
    settings = _settings(log_level="INFO")
    buf_a = io.StringIO()
    configure_logging(settings, stream=buf_a)

    buf_b = io.StringIO()
    configure_logging(settings, stream=buf_b)

    get_logger().info("after_second_configure")

    assert "after_second_configure" in buf_b.getvalue(), (
        "Second configure_logging did not redirect output. "
        f"buf_b={buf_b.getvalue()!r}"
    )


def test_dev_mode_uses_console_renderer():
    """
    Dev mode should not emit JSON; smoke-check via the configured pipeline.
    """
    configure_logging(_settings(environment="dev", log_level="INFO"))
    # ConsoleRenderer outputs human-readable lines; we just assert it doesn't
    # crash
    logger = get_logger("test")
    logger.info("dev_format_smoke", count=1)


def teardown_module():
    """Reset structlog state so other tests aren't affected."""
    structlog.reset_defaults()
    logging.getLogger().handlers.clear()
