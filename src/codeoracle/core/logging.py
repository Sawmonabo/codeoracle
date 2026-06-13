"""Structured logging setup.

Two output formats: dev:        Pretty console (structlog.dev.ConsoleRenderer)
    ci / prod:  JSON (structlog.processors.JSONRenderer)

A redaction processor runs before the renderer to strip secret-like
fields. Any key matching the regex /api_key|secret|token|password/
(case-insensitive) is replaced with the literal string '<redacted>'.
"""

import logging
import re
import sys
from collections.abc import Mapping, MutableMapping
from typing import TYPE_CHECKING, Any, TextIO

import structlog

if TYPE_CHECKING:
    # Type-only import — `Settings` is consumed as an annotation;
    # `configure_logging`
    # reads it at runtime by attribute (settings.log_level /
    # settings.environment),
    # so no runtime import of core.config is needed. Keeps the intra-core/
    # coupling
    # annotation-only (config.py does not import logging.py — no cycle either
    # way).
    from codeoracle.core.config import Settings

_REDACT_KEY_PATTERN = re.compile(
    r"api_key|secret|token|password", re.IGNORECASE
)
_REDACTED = "<redacted>"


def _redact_secrets(
    _logger: Any,
    _method_name: str,
    event_dict: MutableMapping[str, Any],
) -> Mapping[str, Any]:
    """Strip secret-like fields from the event dict before rendering."""
    for key in list(event_dict.keys()):
        if _REDACT_KEY_PATTERN.search(key):
            event_dict[key] = _REDACTED
    return event_dict


def configure_logging(
    settings: Settings, *, stream: TextIO = sys.stdout
) -> None:
    """Configure structlog + stdlib logging from Settings.

    Idempotent: calling twice is harmless (structlog.configure replaces
    state, and force=True rewires basicConfig). Worker / API / MCP
    entrypoints call this once at startup, passing the process Settings.

    The optional keyword-only `stream` overrides the destination for BOTH
    stdlib logging (logging.basicConfig(stream=...)) and structlog
    (PrintLoggerFactory(file=...)).
    Default stdout matches legacy behavior; the MCP stdio transport (Plan 12)
    passes
    sys.stderr to keep JSON-RPC framing on stdout clean (Plan 12 invariant 5).

    Level comes from `settings.log_level`; the renderer is chosen by
    `settings.environment` (ConsoleRenderer in dev, JSONRenderer otherwise).
    """
    log_level = getattr(logging, settings.log_level.upper(), logging.INFO)
    # force=True replaces any existing root-logger config so re-invocations
    # (notably from tests that pass a fresh stream buffer between cases)
    # actually rewire the stream. Without force=True, basicConfig is a no-op
    # after the first call and log output goes to the original (often dead)
    # stream.
    logging.basicConfig(
        format="%(message)s",
        stream=stream,
        level=log_level,
        force=True,
    )

    shared_processors: list[structlog.types.Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        _redact_secrets,
    ]

    if settings.environment == "dev":
        renderer: structlog.types.Processor = structlog.dev.ConsoleRenderer(
            colors=True
        )
    else:
        renderer = structlog.processors.JSONRenderer()

    structlog.configure(
        processors=[
            *shared_processors,
            renderer,
        ],
        wrapper_class=structlog.make_filtering_bound_logger(log_level),
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(file=stream),
        cache_logger_on_first_use=True,
    )


def get_logger(name: str | None = None) -> structlog.stdlib.BoundLogger:
    """
    Convenience accessor — modules call this instead of structlog.get_logger
    directly.
    """
    return structlog.get_logger(name)


def get_audit_logger(name: str | None = None) -> structlog.stdlib.BoundLogger:
    """
    Audit logger with a floor fixed at INFO regardless of `settings.log_level`.

    Used for records that must survive at any production verbosity (e.g. the
    per-citation resolution outcome, spec §8.5 crit. 11). Overrides only the
    wrapper class; processors + logger-factory defer to the global config, so
    it shares the redaction/render pipeline and remains observable under
    `structlog.testing.capture_logs()`.
    """
    return structlog.wrap_logger(
        None,
        wrapper_class=structlog.make_filtering_bound_logger(logging.INFO),
        logger_factory_args=(name,) if name is not None else (),
    )
