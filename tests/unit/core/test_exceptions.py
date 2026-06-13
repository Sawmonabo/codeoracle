"""Test the exception hierarchy preserves expected inheritance."""

import pytest

from codeoracle.core.exceptions import (
    CodeOracleError,
    ConfigError,
    DatabaseError,
    MigrationError,
)


def test_config_error_is_oracle_error():
    assert issubclass(ConfigError, CodeOracleError)


def test_database_error_is_oracle_error():
    assert issubclass(DatabaseError, CodeOracleError)


def test_migration_error_is_database_error():
    assert issubclass(MigrationError, DatabaseError)
    assert issubclass(MigrationError, CodeOracleError)


def test_can_raise_and_catch_as_oracle_error():
    with pytest.raises(CodeOracleError):
        raise MigrationError("test")
