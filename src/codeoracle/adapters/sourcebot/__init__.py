"""Sourcebot adapter — HTTP wrapper for sourcebot.dev's /api/source endpoint.

Public surface:
  - SourcebotConfig — env-loaded settings
  - SourcebotClient — async client; `fetch_span(path, line_range, ref) -> str`
  - SourcebotError / SourcebotTransientError / SourcebotTerminalError
    (re-exported from core.exceptions for adapter-import convenience).
"""

from codeoracle.adapters.sourcebot.client import SourcebotClient
from codeoracle.adapters.sourcebot.config import SourcebotConfig
from codeoracle.core.exceptions import (
    SourcebotError,
    SourcebotTerminalError,
    SourcebotTransientError,
)

__all__ = [
    "SourcebotClient",
    "SourcebotConfig",
    "SourcebotError",
    "SourcebotTerminalError",
    "SourcebotTransientError",
]
