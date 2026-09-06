"""Shared test fixtures.

Note what is *absent*: no database, no Docker, no network. Tests in ``tests/domain``
and ``tests/application`` must stay that way — if they ever need a container, the
dependency rule has been broken somewhere.

``tests/api`` is allowed an app, but still no database: the session dependency is
overridden below. Those tests are about HTTP behaviour — status codes, envelopes,
headers — and pulling a real Postgres into them would make them slow and flaky for
no extra coverage. Real database behaviour is tested in ``tests/integration``.
"""

from collections.abc import AsyncIterator
from typing import Any

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.api.deps import get_session
from app.core.config import Environment, Settings
from app.main import create_app


class FakeSession:
    """Enough of an AsyncSession for routes that only ping the database.

    A fake, not a mock. It has real behaviour you can reason about, and it does not
    break the moment someone refactors the call sequence inside a route — which is
    precisely what assertion-heavy mocks do.
    """

    def __init__(self) -> None:
        self.executed: list[Any] = []

    async def execute(self, statement: Any, *args: Any, **kwargs: Any) -> None:
        self.executed.append(statement)


@pytest.fixture
def settings() -> Settings:
    return Settings(environment=Environment.LOCAL, debug=True)


@pytest.fixture
def app(settings: Settings) -> FastAPI:
    application = create_app(settings)
    # dependency_overrides is FastAPI's built-in seam for swapping a real
    # implementation for a test one. It works because the route asked for a
    # *dependency*, not for a concrete session it built itself.
    application.dependency_overrides[get_session] = FakeSession
    return application


@pytest.fixture
async def client(app: FastAPI) -> AsyncIterator[AsyncClient]:
    """In-process HTTP client — no socket, no server, no port."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
