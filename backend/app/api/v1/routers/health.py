"""Liveness and readiness probes.

These are two different questions, and conflating them causes real outages:

* ``/health`` — "is this process alive?" If it fails, the orchestrator restarts the
  container. It must therefore check *nothing external*: a brief database blip would
  otherwise trigger a restart loop that makes the outage considerably worse.

* ``/ready``  — "should this instance receive traffic?" It checks dependencies. If it
  fails, the instance is pulled from the load balancer but left running, so it can
  recover on its own.

CP5 and CP12 give ``/ready`` real dependency checks.
"""

from fastapi import APIRouter, Response
from sqlalchemy import text

from app.api.deps import SessionDep, SettingsDep
from app.api.v1.schemas.health import HealthResponse, ReadyResponse
from app.core.config import get_settings
from app.core.logging import get_logger

log = get_logger(__name__)

router = APIRouter(tags=["health"])


@router.get("/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    settings = get_settings()
    return HealthResponse(
        status="ok",
        app=settings.app_name,
        environment=settings.environment.value,
    )


@router.get("/ready", response_model=ReadyResponse)
async def ready(session: SessionDep, settings: SettingsDep, response: Response) -> ReadyResponse:
    """Can this instance actually serve traffic?

    A real query, not just "is the pool object present". A pool can hold connections
    to a database that has since gone away, and a readiness probe that cannot fail
    is decoration rather than a check.

    Returns 503 when degraded so the load balancer stops routing here. The process
    keeps running and keeps being probed, so it rejoins automatically once the
    database is back - no human, no restart.
    """
    checks: dict[str, bool] = {}
    try:
        await session.execute(text("SELECT 1"))
        checks["database"] = True
    except Exception:
        log.exception("readiness_check_failed", dependency="database")
        checks["database"] = False

    # Redis is the broker. Without it an upload still succeeds and still stores
    # the file, but nothing ever processes it - so the instance is genuinely not
    # ready to serve, and should be pulled from the load balancer.
    checks["queue"] = await _queue_reachable(settings)

    healthy = all(checks.values())
    if not healthy:
        response.status_code = 503
    return ReadyResponse(status="ready" if healthy else "degraded", checks=checks)


async def _queue_reachable(settings: object) -> bool:
    import redis.asyncio as redis

    url = getattr(settings, "redis_url", "")
    if getattr(settings, "queue_backend", "celery") == "null":
        return True
    client = redis.from_url(url)
    try:
        await client.ping()
    except Exception:
        log.exception("readiness_check_failed", dependency="queue")
        return False
    else:
        return True
    finally:
        await client.aclose()
