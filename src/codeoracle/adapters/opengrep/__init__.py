"""OpenGrep adapter package."""

from pathlib import Path

from codeoracle.adapters.opengrep.runner import OpengrepFinding, OpengrepRunner

DEFAULT_RULES_DIR = Path(__file__).parent / "rules"

__all__ = ["DEFAULT_RULES_DIR", "OpengrepFinding", "OpengrepRunner"]
