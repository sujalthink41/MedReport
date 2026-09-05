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

from fastapi import APIRouter

from app.api.v1.schemas.health import HealthResponse, ReadyResponse
from app.core.config import get_settings

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
async def ready() -> ReadyResponse:
    checks: dict[str, bool] = {}  # populated in CP5 (db) and CP12 (redis)
    return ReadyResponse(
        status="ready" if all(checks.values()) else "degraded",
        checks=checks,
    )
