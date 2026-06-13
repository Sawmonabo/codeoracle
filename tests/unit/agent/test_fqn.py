"""Plan 10 Task 2 — the injection-safe-FQN guard (plan invariant 3)."""

import pytest

from codeoracle.agent.fqn import is_safe_symbol_fqn


@pytest.mark.parametrize(
    "symbol",
    [
        "codeoracle.services.verifier.service.VerifierService.verify",
        "codeoracle.agent.graph.run_agent_query",
        "module.func()",  # SCIP-style callable suffix
        "package.module.ClassName#",  # SCIP-style class suffix
        "_private._helper_fn",
        "single",
    ],
)
def test_accepts_well_formed_fqns(symbol: str) -> None:
    assert is_safe_symbol_fqn(symbol) is True


@pytest.mark.parametrize(
    "symbol",
    [
        "x'}) DETACH DELETE (n) //",  # the canonical Cypher-injection payload
        "a.b; DROP GRAPH",
        "name with spaces",
        "",  # empty — Pydantic allows min_length=1 but charset still required
        "a..b",  # consecutive dots
        ".leading.dot",
        "trailing.dot.",
        "kebab-case",
        "path/with/slashes",
        'has"a"quote',
        "1starts_with_digit",
    ],
)
def test_rejects_injection_unsafe_symbols(symbol: str) -> None:
    assert is_safe_symbol_fqn(symbol) is False
