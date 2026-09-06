"""The error envelope.

Every failure this API produces has the same shape — ours, FastAPI's validation
errors, and a 404 for an unknown route alike. One shape means a client writes error
handling once instead of guessing which of three formats came back.

    {
      "error": {
        "code": "profile_not_found",
        "message": "That profile could not be found.",
        "request_id": "7f3a...",
        "details": null
      }
    }

``code`` is the machine-readable part. Clients branch on it, never on ``message`` —
message text is allowed to change, codes are a contract.

``request_id`` is here so a user can quote one string to support and we can find every
log line for that request.
"""

from pydantic import BaseModel, Field


class ErrorDetail(BaseModel):
    code: str = Field(description="Stable machine-readable code. Branch on this.")
    message: str = Field(description="Human-readable. May change; do not parse.")
    request_id: str | None = Field(
        default=None, description="Quote this to support to locate the failure."
    )
    details: list[dict[str, str]] | None = Field(
        default=None,
        description="Field-level problems. Populated for validation errors only.",
    )


class ErrorResponse(BaseModel):
    error: ErrorDetail
