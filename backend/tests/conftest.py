"""Shared test fixtures.

Note what is *absent*: no database, no Docker, no network. Tests in ``tests/domain``
and ``tests/application`` must stay that way — if they ever need a container, the
dependency rule has been broken somewhere.
"""

from collections.abc import AsyncIterator

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.core.config import Environment, Settings
from app.main import create_app


@pytest.fixture
def settings() -> Settings:
    return Settings(environment=Environment.LOCAL, debug=True)


@pytest.fixture
def app(settings: Settings) -> FastAPI:
    return create_app(settings)


@pytest.fixture
async def client(app: FastAPI) -> AsyncIterator[AsyncClient]:
    """In-process HTTP client — no socket, no server, no port."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
