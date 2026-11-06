"""Database engine, session factory and the declarative base shared by every module."""

from collections.abc import Iterator
from datetime import UTC, datetime
from enum import StrEnum

from sqlalchemy import DateTime, Enum, MetaData, create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.core.config import get_settings

# Predictable constraint names let Alembic alter or drop them later.
NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


def enum_column(enum_cls: type[StrEnum]) -> Enum:
    """Store an enum as VARCHAR plus a CHECK constraint, so adding a value is a simple migration."""
    return Enum(
        enum_cls,
        native_enum=False,
        create_constraint=True,
        length=40,
        name=enum_cls.__name__.lower(),
    )


UTCDateTime = DateTime(timezone=True)


def utcnow() -> datetime:
    return datetime.now(UTC)


engine = create_engine(get_settings().database_url, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)


def get_db() -> Iterator[Session]:
    """FastAPI dependency: one session per request, always closed."""
    with SessionLocal() as session:
        yield session
