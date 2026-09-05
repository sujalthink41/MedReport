"""Response DTOs for the health endpoints.

DTOs live here, not in the router, because a response shape is a **published
contract**. Keeping them in one place means you can read `schemas/` and know the
entire API surface without opening a single route handler.

It also stops the quiet drift where a route starts returning a dict "just for now"
and the API becomes undocumented one endpoint at a time.
"""

from typing import Literal

from pydantic import BaseModel, Field


class HealthResponse(BaseModel):
    status: Literal["ok"]
    app: str
    environment: str


class ReadyResponse(BaseModel):
    status: Literal["ready", "degraded"]
    checks: dict[str, bool] = Field(
        default_factory=dict,
        description="Dependency name -> reachable. Empty until CP5/CP12 add real checks.",
    )
