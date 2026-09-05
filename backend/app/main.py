"""Application composition root for the HTTP entrypoint.

``create_app()`` is a *factory*, not a module-level ``app = FastAPI()``. That matters
more than it looks:

* tests build an app with overridden settings/dependencies, per test if needed
* nothing runs at import time, so importing ``app.main`` has no side effects
* the wiring of concrete implementations happens in exactly one visible place

CP2 adds middleware, structured logging and the error handlers.
CP7 mounts auth, CP8 authorization. CP9 onward mount feature routers here.
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import APIRouter, FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.v1.routers import health
from app.core.config import Settings, get_settings


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    """Startup and shutdown.

    Connection pools, the Redis client and the LLM client get created here from
    CP5 onward — once per process, not once per request.
    """
    yield


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()

    app = FastAPI(
        title=settings.app_name,
        version="0.1.0",
        lifespan=lifespan,
        # Never expose interactive docs in production: they advertise every
        # endpoint and schema to anyone who finds the URL.
        docs_url=None if settings.is_production else "/docs",
        redoc_url=None,
        openapi_url=None if settings.is_production else "/openapi.json",
    )

    if settings.cors_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=settings.cors_origins,
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
        )

    v1 = APIRouter(prefix=settings.api_v1_prefix)
    v1.include_router(health.router)
    app.include_router(v1)

    return app


app = create_app()
