"""Plan 10 Task 3 — AgentDependencies is a frozen, fully-populated bundle."""

import dataclasses

import pytest

from codeoracle.agent.dependencies import AgentDependencies


def _assign_for_validation(
    instance: object,
    field_name: str,
    value: object,
) -> None:
    setattr(instance, field_name, value)


def test_agent_dependencies_is_frozen(make_fake_deps) -> None:
    deps = make_fake_deps()
    with pytest.raises(dataclasses.FrozenInstanceError):
        _assign_for_validation(deps, "llm", None)


def test_agent_dependencies_exposes_every_collaborator(make_fake_deps) -> None:
    deps = make_fake_deps()
    for attr in (
        "graph_repo",
        "embeddings_repo",
        "snapshot_repo",
        "resolver",
        "verifier",
        "trust_matrix",
        "llm",
        "embeddings",
    ):
        assert getattr(deps, attr) is not None


def test_agent_dependencies_field_set_is_exactly_eight() -> None:
    fields = {f.name for f in dataclasses.fields(AgentDependencies)}
    assert fields == {
        "graph_repo",
        "embeddings_repo",
        "snapshot_repo",
        "resolver",
        "verifier",
        "trust_matrix",
        "llm",
        "embeddings",
    }
