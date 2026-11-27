"""Database connection setup (SQLAlchemy + Neon Postgres)."""

from functools import cache

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from supportpilot.config import settings


class Base(DeclarativeBase):
    """Parent class for all table models. Collects them so we can create the tables."""


def to_driver_url(url: str) -> str:
    """Neon gives `postgresql://...`. SQLAlchemy needs to be told to use the psycopg driver."""
    for prefix in ("postgresql://", "postgres://"):
        if url.startswith(prefix):
            return "postgresql+psycopg://" + url[len(prefix) :]
    return url


@cache
def get_engine() -> Engine:
    """Create the connection pool once and reuse it.

    pool_pre_ping tests a connection before using it, because Neon pauses idle databases
    and a stale connection would otherwise raise an error.
    """
    return create_engine(to_driver_url(settings.database_url), pool_pre_ping=True)


def get_session() -> Session:
    """Open a new session (a unit of work: queries plus one commit or rollback)."""
    return sessionmaker(bind=get_engine(), expire_on_commit=False)()
