"""Unit coverage for AGE parameter-map SQL generation."""

import json
from typing import Any, cast

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from codeoracle.core.exceptions import GraphQueryError
from codeoracle.db.repositories.graph import GraphRepository


class _FakeResult:
    def mappings(self) -> _FakeResult:
        return self

    def all(self) -> list[dict[str, object]]:
        return [{"result": '"ok"'}]


class _FakeSession:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, object]]] = []

    async def execute(
        self, statement: object, params: dict[str, object]
    ) -> _FakeResult:
        self.calls.append((str(statement), params))
        return _FakeResult()


def _repo_with_session() -> tuple[GraphRepository, _FakeSession]:
    session = _FakeSession()
    return GraphRepository(cast(AsyncSession, session)), session


async def test_execute_cypher_uses_age_parameter_map_for_scalars() -> None:
    repo, session = _repo_with_session()

    await repo.execute_cypher(
        "MATCH (n {snapshot_sha:$snapshot_sha}) RETURN n AS result",
        {"snapshot_sha": "abc123"},
    )

    sql, params = session.calls[0]
    assert "cypher('codeoracle'" in sql
    assert "CAST(:age_params AS agtype)" in sql
    assert "$snapshot_sha" in sql
    assert json.loads(str(params["age_params"])) == {"snapshot_sha": "abc123"}


async def test_execute_cypher_uses_age_parameter_map_for_lists() -> None:
    repo, session = _repo_with_session()

    await repo.execute_cypher(
        "MATCH (n {snapshot_sha:$base}) "
        "WHERE n.file_path IN $changed_files "
        "RETURN n.file_path AS result",
        {"base": "abc123", "changed_files": ["a.py", "b.py"]},
    )

    _, params = session.calls[0]
    assert json.loads(str(params["age_params"])) == {
        "base": "abc123",
        "changed_files": ["a.py", "b.py"],
    }


async def test_execute_cypher_keeps_two_argument_form_without_params() -> None:
    repo, session = _repo_with_session()

    await repo.execute_cypher("MATCH (n) RETURN n AS result")

    sql, params = session.calls[0]
    assert "CAST(:age_params AS agtype)" not in sql
    assert params == {}


async def test_execute_cypher_rejects_non_json_serializable_params() -> None:
    repo, session = _repo_with_session()
    non_json_param: dict[str, Any] = {"bad": object()}

    with pytest.raises(GraphQueryError, match="JSON-serializable"):
        await repo.execute_cypher(
            "MATCH (n {value:$bad}) RETURN n AS result", non_json_param
        )

    assert session.calls == []
