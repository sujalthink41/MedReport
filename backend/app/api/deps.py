"""FastAPI dependencies — where ports get bound to concrete adapters.

This is the composition root for HTTP requests. It is the *only* place that knows
which implementation satisfies which port. Everything downstream asks for a port and
never learns what it got.

When CP10 adds a second storage adapter, this file changes and nothing else does.
"""

from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.adapters.db.session import session_scope


def get_session_factory(request: Request) -> async_sessionmaker[AsyncSession]:
    """The factory built once at startup and parked on ``app.state``.

    Not a module-level global: a global would be created at import time, shared by
    every test, and impossible to point at a different database. On ``app.state`` it
    belongs to one app instance, which is exactly what the app factory gives us.
    """
    factory: async_sessionmaker[AsyncSession] = request.app.state.session_factory
    return factory


async def get_session(
    factory: Annotated[async_sessionmaker[AsyncSession], Depends(get_session_factory)],
) -> AsyncIterator[AsyncSession]:
    """One session per request, committed at the end or rolled back on failure.

    The commit lives here, at the boundary — never inside a repository. One request
    is one transaction, so a failure halfway through leaves nothing half-written.
    """
    async for session in session_scope(factory):
        yield session


SessionDep = Annotated[AsyncSession, Depends(get_session)]
