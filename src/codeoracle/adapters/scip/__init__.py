"""SCIP adapter: scip-python + scip subprocess wrapping + text-format parser.

Plan 6. Used by `workers/scip_indexer` (project-mode worker).
"""

from codeoracle.adapters.scip.client import ScipClient
from codeoracle.adapters.scip.parser import (
    ScipDefinition,
    ScipIndexParser,
    ScipParseError,
    ScipParseResult,
    ScipReference,
)

__all__ = [
    "ScipClient",
    "ScipDefinition",
    "ScipIndexParser",
    "ScipParseError",
    "ScipParseResult",
    "ScipReference",
]
