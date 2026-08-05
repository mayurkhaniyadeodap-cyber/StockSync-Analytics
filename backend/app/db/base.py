"""Declarative base for every ORM model.

No models yet — the schema lands in M1–M4. This exists so Alembic's autogenerate
has a stable ``target_metadata`` from M0 onward.
"""

from __future__ import annotations

from sqlalchemy import MetaData
from sqlalchemy.orm import DeclarativeBase

# Explicit naming so Alembic emits stable, readable constraint names instead of
# database-generated ones. Without this, dropping a constraint in a later
# migration means looking up whatever name Postgres happened to choose.
NAMING_CONVENTION = {
    "ix": "ix_%(table_name)s_%(column_0_N_name)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


__all__ = ["NAMING_CONVENTION", "Base"]
