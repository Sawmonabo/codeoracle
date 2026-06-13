"""
Plan 10 Task 1 — AgentError is an CodeOracleError carrying a plain message.
"""

from codeoracle.core.exceptions import AgentError, CodeOracleError


def test_agent_error_is_an_oracle_error() -> None:
    assert issubclass(AgentError, CodeOracleError)


def test_agent_error_carries_its_message() -> None:
    err = AgentError("no APPLIED snapshot exists")
    assert str(err) == "no APPLIED snapshot exists"
    assert isinstance(err, CodeOracleError)
