"""Database engine/session helpers. Postgres in docker, SQLite everywhere else.

Engines use ``hide_parameters=True`` so SQL errors never echo PHI in logs.
"""

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker


class Base(DeclarativeBase):
    """Declarative base for all Core tables (see models_db.py)."""


def make_engine(url: str) -> Engine:
    """Create an engine. SQLite gets FK enforcement, WAL and a busy timeout."""
    if not url.startswith("sqlite"):
        # [2.8] Server-side backstops for Postgres: no statement may run longer
        # than 10s (a wedged query must not pin a pool slot), and no session may
        # idle inside a transaction for more than 30s (they hold locks).
        return create_engine(
            url,
            pool_pre_ping=True,
            hide_parameters=True,
            connect_args={
                "options": "-c statement_timeout=10000 -c idle_in_transaction_session_timeout=30000"
            },
        )

    database = url.split("///", 1)[-1]
    if database and database != ":memory:":
        Path(database).parent.mkdir(parents=True, exist_ok=True)
    engine = create_engine(
        url, connect_args={"check_same_thread": False, "timeout": 30}, hide_parameters=True
    )

    @event.listens_for(engine, "connect")
    def _sqlite_pragmas(dbapi_connection, _record) -> None:  # type: ignore[no-untyped-def]
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        if database and database != ":memory:":
            cursor.execute("PRAGMA journal_mode=WAL")
        cursor.close()

    return engine


def make_session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine, expire_on_commit=False, autoflush=True)


@contextmanager
def session_scope(factory: sessionmaker[Session]) -> Iterator[Session]:
    """One transaction: commit on success, roll back on error, always close."""
    session = factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def utcnow() -> datetime:
    return datetime.now(UTC)


def as_utc(value: datetime | None) -> datetime | None:
    """SQLite returns naive datetimes; treat them as UTC."""
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=UTC)
