"""Per-HandleType resolver strategies.

Public surface populated incrementally across Plan 8 tasks 7-12.
"""

from codeoracle.services.handle_resolution.strategies.base import (
    HandleResolverStrategy,
    parse_file_locator,
)
from codeoracle.services.handle_resolution.strategies.code_span import (
    CodeSpanResolver,
)
from codeoracle.services.handle_resolution.strategies.doc_paragraph import (
    DocParagraphResolver,
)
from codeoracle.services.handle_resolution.strategies.graph_query import (
    GraphQueryResolver,
)
from codeoracle.services.handle_resolution.strategies.stub import StubResolver

__all__ = [
    "CodeSpanResolver",
    "DocParagraphResolver",
    "GraphQueryResolver",
    "HandleResolverStrategy",
    "StubResolver",
    "parse_file_locator",
]
