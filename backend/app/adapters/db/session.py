"""Engine and session lifecycle.

Two objects with very different lifetimes, and confusing them is the classic
SQLAlchemy mistake:

* **The engine** owns the connection pool. **One per process**, created at startup.
  Creating an engine per request means opening a TCP connection per request, which
  destroys both latency and your Postgres connection limit.

* **The session** is a unit of work. **One per request** (or per task), created and
  discarded constantly. A session shared between concurrent requests would leak one
  user's uncommitted data into another's transaction.
"""

from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.core.config import Settings


def create_engine(settings: Settings) -> AsyncEngine:
    return create_async_engine(
        settings.database_url,
        echo=settings.database_echo,
        # Sizing is a capacity calculation, not a default to leave alone.
        # Postgres has a hard max_connections; every API container and every Celery
        # worker draws from it. pool_size + max_overflow, times the number of
        # processes, must stay under that limit or new connections start failing
        # under exactly the load where you need them.
        pool_size=settings.database_pool_size,
        max_overflow=settings.database_max_overflow,
        # Recycle before a proxy or Postgres silently drops an idle connection,
        # which otherwise surfaces as a random failure on a quiet morning.
        pool_recycle=1800,
        pool_pre_ping=True,
    )


def create_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(
        bind=engine,
        expire_on_commit=False,
        # Default is True: after commit, every loaded attribute is expired and the
        # next access triggers a fresh SELECT. In async code that lazy load happens
        # outside the await boundary and raises MissingGreenlet - a confusing error
        # for what is really just "you read an attribute after committing".
        #
        # We map rows to domain objects at the repository boundary anyway, so
        # nothing downstream depends on live ORM identity.
        autoflush=False,
    )


async def session_scope(
    factory: async_sessionmaker[AsyncSession],
) -> AsyncIterator[AsyncSession]:
    """One session, committed on success and rolled back on any failure.

    Note where the commit is: **here, at the boundary**, not inside repositories.
    One request is one transaction. A repository that commits on its own turns a
    single logical operation into several, so a failure halfway through leaves the
    database in a state that no part of the code intended.
    """
    async with factory() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise
        else:
            await session.commit()
