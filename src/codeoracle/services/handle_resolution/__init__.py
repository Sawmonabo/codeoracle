"""Handle resolution service.

Public surface:
  - HandleResolverService — the dispatcher (Task 8). Callers (VerifierService
    in Plan 9) construct one of these from a strategy dict.
  - HandleResolverStrategy — the ABC (Task 7); subclass to add a new strategy.
  - The four concrete strategies: CodeSpanResolver (Task 9),
    DocParagraphResolver (Task 10), GraphQueryResolver (Task 11),
    StubResolver (Task 12).
  - parse_file_locator — exposed because Plan 9's tests construct synthetic
    handles and want the same parser the resolver uses.

The strategies subpackage is re-exported flat from this module so callers
can do `from codeoracle.services.handle_resolution import CodeSpanResolver`
without crossing into the subpackage. Composition root uses these
re-exports.
"""

from codeoracle.services.handle_resolution.service import HandleResolverService
from codeoracle.services.handle_resolution.strategies import (
    CodeSpanResolver,
    DocParagraphResolver,
    GraphQueryResolver,
    HandleResolverStrategy,
    StubResolver,
    parse_file_locator,
)

__all__ = [
    "CodeSpanResolver",
    "DocParagraphResolver",
    "GraphQueryResolver",
    "HandleResolverService",
    "HandleResolverStrategy",
    "StubResolver",
    "parse_file_locator",
]
