"""Injection-safe symbol-FQN validation (plan invariant 3).

``Query.symbol`` flows into *literal* openCypher embedded in ``cypher://``
evidence locators — Plan 8's ``GraphQueryResolver`` re-runs the literal query
verbatim. An unsanitised symbol is therefore a Cypher-injection surface.
``is_safe_symbol_fqn`` is the gate: ``intent_node`` calls it before any query
construction, and an unsafe symbol terminates the request before retrieval.
"""

import re

# A dotted identifier path: each segment starts with a letter or underscore and
# continues with word characters. An optional SCIP-style suffix marks a
# callable
# ``()`` or a class ``#``. Deliberately strict — letters, digits, underscores,
# dots, and the two suffixes only. No quotes, spaces, braces, or semicolons can
# survive this pattern, so nothing injectable reaches a Cypher string.
SAFE_FQN_PATTERN = re.compile(
    r"^[A-Za-z_][A-Za-z0-9_]*(\.[A-Za-z_][A-Za-z0-9_]*)*(\(\)|#)?$"
)


def is_safe_symbol_fqn(symbol: str) -> bool:
    """Return ``True`` iff ``symbol`` is a well-formed, injection-safe FQN."""
    return SAFE_FQN_PATTERN.fullmatch(symbol) is not None
