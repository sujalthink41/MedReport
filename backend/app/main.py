"""Application composition root for the HTTP entrypoint.

``create_app()`` is a *factory*, not a module-level ``app = FastAPI()``. That matters
more than it looks:

* tests build an app with overridden settings/dependencies, per test if needed
* nothing runs at import time, so importing ``app.main`` has no side effects
* the wiring of concrete implementations happens in exactly one visible place

CP7 mounts auth, CP8 authorization. CP9 onward mount feature routers here.
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import APIRouter, FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.adapters.db.session import create_engine, create_session_factory
from app.api.error_handlers import register_error_handlers
from app.api.middleware import AccessLogMiddleware, RequestContextMiddleware
from app.api.v1.routers import auth, health, profiles
from app.core.config import Settings, get_settings
from app.core.logging import configure_logging, get_logger
from app.domain.ports.services import FileStorage

log = get_logger(__name__)


def build_storage(settings: Settings) -> FileStorage:
    """Choose the storage adapter. The entire plug/unplug mechanism, in one place.

    Count the files that change when you swap R2 for local disk, or for S3, or for
    anything else: this one. Nothing in ``application/`` or ``domain/`` names a
    provider, so nothing downstream can notice.
    """
    if settings.storage_backend == "r2":
        from app.adapters.storage.r2 import R2Storage

        return R2Storage(
            account_id=settings.r2_account_id,
            access_key_id=settings.r2_access_key_id,
            secret_access_key=settings.r2_secret_access_key,
            bucket=settings.r2_bucket,
        )

    from pathlib import Path

    from app.adapters.storage.local import LocalDiskStorage

    return LocalDiskStorage(
        root=Path(settings.storage_local_root),
        signing_secret=settings.jwt_secret,
    )


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Startup and shutdown.

    The engine owns the connection pool, so it is created ONCE per process here -
    not per request. An engine per request means a TCP connection per request, which
    ruins latency and exhausts Postgres' connection limit under load.

    Disposing on shutdown matters too: without it, a rolling deploy leaves the old
    container's connections open until Postgres times them out, and for a few
    minutes you are over your connection budget.
    """
    settings = get_settings()
    log.info("app_starting", environment=settings.environment.value)

    engine = create_engine(settings)
    app.state.engine = engine
    app.state.session_factory = create_session_factory(engine)
    app.state.storage = build_storage(settings)
    log.info("storage_ready", backend=settings.storage_backend)

    try:
        yield
    finally:
        await engine.dispose()
        log.info("app_stopped")


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(settings)

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

    # ---- Middleware -------------------------------------------------------
    # Ordering is the part people get wrong. Starlette applies the LAST-ADDED
    # middleware OUTERMOST, so this list runs bottom-up on the way in:
    #
    #     RequestContextMiddleware   <- outermost: every request gets an id first
    #       AccessLogMiddleware      <- so its log lines already carry that id
    #         CORSMiddleware
    #           routes
    #
    # Get this backwards and your access log has no request_id on it, which is
    # exactly when you need one.
    if settings.cors_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=settings.cors_origins,
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
        )
    app.add_middleware(AccessLogMiddleware)
    app.add_middleware(RequestContextMiddleware)

    register_error_handlers(app)

    v1 = APIRouter(prefix=settings.api_v1_prefix)
    v1.include_router(health.router)
    v1.include_router(auth.router)
    v1.include_router(profiles.router)
    app.include_router(v1)

    return app


app = create_app()
