"""SQLAlchemy 2.0 engine, session factory and declarative base.

All vesana-community tables live in a dedicated ``community`` Postgres schema,
which keeps them cleanly namespaced when sharing the prod Postgres instance with
the rest of the Vesana stack.
"""

from __future__ import annotations

from collections.abc import Generator

from sqlalchemy import MetaData, create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.config import get_settings

SCHEMA = "community"

# An explicit naming convention keeps Alembic autogenerate deterministic.
_NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    """Declarative base; every table defaults to the ``community`` schema."""

    metadata = MetaData(schema=SCHEMA, naming_convention=_NAMING_CONVENTION)


def _make_engine():
    settings = get_settings()
    return create_engine(settings.DATABASE_URL, pool_pre_ping=True, future=True)


# Module-level engine/session factory. Cheap to create; no connection happens
# until first use, so importing this module never requires a live database.
engine = _make_engine()
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False, class_=Session)


def get_db() -> Generator[Session, None, None]:
    """FastAPI dependency yielding a scoped SQLAlchemy session."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
