"""Smoke tests — verify the package imports cleanly and basic infra works."""

import codeoracle
from codeoracle import (
    adapters,
    agent,
    api,
    cli,
    core,
    db,
    mcp,
    models,
    services,
    workers,
)
from codeoracle.core.version import __version__, get_version


def test_package_imports() -> None:
    assert codeoracle is not None


def test_all_layers_importable() -> None:
    for layer in (
        models,
        core,
        db,
        adapters,
        services,
        cli,
        api,
        mcp,
        agent,
        workers,
    ):
        assert layer is not None


def test_version_module() -> None:
    assert get_version() == __version__
    assert isinstance(__version__, str)
    assert __version__.count(".") == 2
