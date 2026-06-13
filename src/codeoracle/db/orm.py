"""SQLAlchemy declarative base + Mapped-type helpers.

Migrations and (in Plans 5-7) repositories share `Base`. Models in
src/codeoracle/models/ are Pydantic-only — never SQLAlchemy. The translation
is per-repository (Plans 5-7 own the four repositories: snapshots/graph
in Plan 5, embeddings in Plan 6, traces in Plan 7).
"""

from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    """Single declarative base for all platform tables."""
