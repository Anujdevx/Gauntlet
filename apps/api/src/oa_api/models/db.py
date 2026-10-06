"""Async engine and session factory.

Two decisions worth stating:

**One engine per process, created lazily.** ``asyncpg`` opens a connection pool,
and a pool created at import time leaks when the module is imported in a forked
Celery child before the fork. Lazily constructing it means the first caller in
each process builds its own, which is what ``CeleryApp.__init__`` needs.

**``pool_pre_ping`` is on.** A long-lived worker holds connections for longer than
a proxy or a database restart tolerates, and the first symptom of a stale
connection is a ``OperationalError`` in the middle of a candidate's submission.
Pre-ping converts that into one transparent reconnect.

**No global session.** Sessions are request-scoped via
:func:`async_sessionmaker`. A module-level session is the classic source of
cross-request state leaking between candidates.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from oa_api.models.tables import Base
from oa_engine.platform.config import get_settings
from oa_engine.platform.logging import get_logger

log = get_logger(__name__, service="oa-api")

__all__ = [
    "async_sessionmaker",
    "create_engine_and_session",
    "dispose_engine",
    "get_engine",
    "get_sessionmaker",
    "session_scope",
]


@dataclass(slots=True)
class _EngineSlot:
    """Mutable holder for the process-wide engine and sessionmaker.

    A holder object rather than module-level ``global`` bindings: rebinding a
    global under a lock is invisible at the declaration site, so a reader has to
    prove no other thread observed a stale value. Mutating one attribute of a
    single well-named object makes the shared state greppable.
    """

    engine: AsyncEngine | None = None
    sessionmaker: async_sessionmaker[AsyncSession] | None = None


_slot = _EngineSlot()


def create_engine_and_session() -> tuple[AsyncEngine, async_sessionmaker[AsyncSession]]:
    """Construct a fresh engine and sessionmaker.

    Returns:
        The pair. Callers own disposal; :func:`dispose_engine` handles the shared
        instance.

    Raises:
        sqlalchemy.exc.SQLAlchemyError: If the DSN is malformed. Surfacing this at
            construction rather than on first query means a typo'd ``POSTGRES_DSN``
            fails at startup with a readable message.
    """
    settings = get_settings()
    engine = create_async_engine(
        settings.postgres_dsn,
        pool_pre_ping=True,
        pool_size=10,
        max_overflow=10,
        # A request must not hold a connection open indefinitely behind a slow
        # candidate; the sandbox wall clock is the real bound on any single unit
        # of work.
        pool_timeout=10,
        pool_recycle=1800,
        echo=False,
        # asyncpg does not accept certain libpq-style params, and passing an
        # unknown one fails at connect time with an opaque message.
        connect_args={"server_settings": {"application_name": "oa-api"}},
    )
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    return engine, factory


def get_engine() -> AsyncEngine:
    """Return the process-wide engine, creating it on first use."""
    get_sessionmaker()
    engine = _slot.engine
    assert engine is not None  # noqa: S101 - invariant of get_sessionmaker
    return engine


def get_sessionmaker() -> async_sessionmaker[AsyncSession]:
    """Return the process-wide sessionmaker, creating it and the engine on first use.

    Both are derived from one engine, so creating either must create both; doing
    it in one place stops the two accessors from drifting apart.
    """
    existing = _slot.sessionmaker
    if existing is not None:
        return existing
    engine, factory = create_engine_and_session()
    _slot.engine = engine
    _slot.sessionmaker = factory
    return factory


async def dispose_engine() -> None:
    """Close the pool and forget the engine. Safe to call when none exists."""
    engine, _slot.engine, _slot.sessionmaker = _slot.engine, None, None
    if engine is not None:
        await engine.dispose()
        log.info("db_engine_disposed")


@asynccontextmanager
async def session_scope() -> AsyncIterator[AsyncSession]:
    """Provide a transactional session.

    Commits on clean exit, rolls back on any exception. Used by background work --
    migrations, seeding, the submission completion handler -- where there is no
    request to hang a dependency off.
    """
    factory = get_sessionmaker()
    async with factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


async def create_all() -> None:
    """Create every table directly from the models.

    **For tests and local development only.** Production uses Alembic, because
    ``create_all`` cannot alter a table, cannot add a constraint to a populated
    one, and leaves no record of what changed. Kept because Alembic's full
    round trip is slower to iterate on than a disposable database.
    """
    engine = get_engine()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    log.info("db_schema_created", tables=len(Base.metadata.tables))
