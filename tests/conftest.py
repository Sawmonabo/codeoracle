"""Shared test fixtures for DB-backed tests outside tests/integration."""

import os
import subprocess
from collections.abc import AsyncIterator, Iterator
from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
)

from codeoracle.core.config import get_settings
from codeoracle.db.session import make_async_engine, make_async_session_factory

REPO_ROOT = Path(__file__).resolve().parents[1]
BOOTSTRAP_SCRIPT = REPO_ROOT / "infra" / "scripts" / "bootstrap_db.sh"

_LIVE_MARKER_OPTIONS = {
    "e2e": "--run-e2e",
    "live_llm": "--run-live-llm",
    "live_embeddings": "--run-live-embeddings",
    "live_tempo": "--run-live-tempo",
    "live_sourcebot": "--run-live-sourcebot",
}


def pytest_addoption(parser: pytest.Parser) -> None:
    for option in _LIVE_MARKER_OPTIONS.values():
        parser.addoption(
            option,
            action="store_true",
            default=False,
            help=f"run tests marked {option.removeprefix('--run-')}",
        )


def pytest_collection_modifyitems(
    config: pytest.Config,
    items: list[pytest.Item],
) -> None:
    for item in items:
        for marker, option in _LIVE_MARKER_OPTIONS.items():
            if item.get_closest_marker(marker) and not config.getoption(
                option
            ):
                item.add_marker(
                    pytest.mark.skip(
                        reason=(
                            f"requires {option} to run live integration test"
                        )
                    )
                )


@pytest.fixture(scope="session")
def bootstrapped_db() -> Iterator[None]:
    """Bootstrap the local CodeOracle test database once when DB tests run."""
    if os.environ.get("CODEORACLE_SKIP_BOOTSTRAP") == "1":
        yield
        return

    get_settings.cache_clear()
    result = subprocess.run(
        [str(BOOTSTRAP_SCRIPT)],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        pytest.fail(
            f"bootstrap_db.sh failed:\n{result.stdout}\n{result.stderr}"
        )
    yield


@pytest.fixture(scope="session")
def async_engine(bootstrapped_db: None) -> AsyncEngine:
    """Async SQLAlchemy engine bound to the bootstrapped database."""
    return make_async_engine()


@pytest.fixture(scope="session")
def async_session_factory(
    async_engine: AsyncEngine,
) -> async_sessionmaker[AsyncSession]:
    """Session factory shared by DB-backed tests in all test subtrees."""
    return make_async_session_factory(async_engine)


@pytest.fixture
async def async_session(
    async_session_factory: async_sessionmaker[AsyncSession],
) -> AsyncIterator[AsyncSession]:
    """Per-test async session with rollback cleanup."""
    async with async_session_factory() as session:
        try:
            yield session
        finally:
            await session.rollback()
