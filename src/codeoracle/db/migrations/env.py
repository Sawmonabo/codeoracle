"""Alembic environment — uses db/session.py's sync engine factory so the
AGE-per-connection event listener fires for migrations too."""

from logging.config import fileConfig

from alembic import context

# Import all model modules so Base.metadata sees their tables.
from codeoracle.db import models  # noqa: F401
from codeoracle.db.orm import Base
from codeoracle.db.session import make_sync_engine

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """We don't use offline mode — fail fast if invoked."""
    raise RuntimeError(
        "Offline migration mode is not supported. Run with --online."
    )


def run_migrations_online() -> None:
    engine = make_sync_engine()
    with engine.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
