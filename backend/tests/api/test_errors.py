"""Tests for the error-to-HTTP boundary.

The test worth reading twice is ``test_unexpected_errors_leak_nothing``. Everything
else here is about consistency; that one is about not handing an attacker a map of
your stack.
"""

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from pydantic import BaseModel

from app.api.error_handlers import status_for
from app.core.context import REQUEST_ID_HEADER
from app.domain.errors import (
    DuplicateReportError,
    FileTooLargeError,
    LLMUnavailableError,
    PermissionDeniedError,
    ProfileNotFoundError,
    StorageUnavailableError,
    UnsupportedFileTypeError,
)


class Body(BaseModel):
    pages: int


@pytest.fixture
def app_with_failing_routes(app: FastAPI) -> FastAPI:
    """Routes that raise, so we can observe the boundary. Note: no try/except."""

    @app.get("/boom/domain")
    async def _domain() -> None:
        raise ProfileNotFoundError(profile_id="p-1")

    @app.get("/boom/permission")
    async def _permission() -> None:
        raise PermissionDeniedError(action="report:read")

    @app.get("/boom/conflict")
    async def _conflict() -> None:
        raise DuplicateReportError(sha256="abc")

    @app.get("/boom/infra")
    async def _infra() -> None:
        raise LLMUnavailableError(provider="openai")

    @app.get("/boom/bug")
    async def _bug() -> None:
        raise ValueError("connection to postgres://user:pw@10.0.0.4/prod failed")

    @app.post("/boom/validate")
    async def _validate(body: Body) -> dict[str, int]:
        return {"pages": body.pages}

    return app


@pytest.fixture
async def client(app_with_failing_routes: FastAPI):  # type: ignore[no-untyped-def]
    # raise_app_exceptions=False makes the test behave like a real ASGI server.
    #
    # Starlette's catch-all Exception handler produces the 500 response and then
    # *re-raises*, so the server can log the crash. Uvicorn absorbs that; httpx's
    # test transport re-raises it into the test by default. Left at the default,
    # you cannot test the one path that matters most: what an unexpected crash
    # actually returns to a user.
    transport = ASGITransport(app=app_with_failing_routes, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


class TestStatusResolution:
    """Status comes from the MRO, so subclasses need no entry in STATUS_MAP."""

    @pytest.mark.parametrize(
        ("error", "expected"),
        [
            (ProfileNotFoundError(), 404),  # via NotFoundError
            (PermissionDeniedError(), 403),
            (DuplicateReportError(), 409),  # via ConflictError
            (UnsupportedFileTypeError(), 415),
            (FileTooLargeError(), 413),
            (StorageUnavailableError(), 503),
            (LLMUnavailableError(), 503),
        ],
    )
    def test_subclasses_inherit_their_category_status(
        self, error: Exception, expected: int
    ) -> None:
        assert status_for(error) == expected  # type: ignore[arg-type]


class TestErrorEnvelope:
    async def test_domain_error_returns_its_code_and_a_safe_message(self, client) -> None:  # type: ignore[no-untyped-def]
        response = await client.get("/boom/domain")

        assert response.status_code == 404
        error = response.json()["error"]
        assert error["code"] == "profile_not_found"
        assert error["message"] == "That profile could not be found."
        assert error["request_id"]

    async def test_context_is_never_echoed_to_the_client(self, client) -> None:  # type: ignore[no-untyped-def]
        # The error carried profile_id="p-1". Identifiers stay in our logs; in this
        # product an error's context can hold PHI, so none of it crosses the wire.
        response = await client.get("/boom/domain")

        assert "p-1" not in response.text

    async def test_infrastructure_error_is_5xx(self, client) -> None:  # type: ignore[no-untyped-def]
        response = await client.get("/boom/infra")

        assert response.status_code == 503
        assert response.json()["error"]["code"] == "llm_unavailable"

    async def test_unknown_route_uses_the_same_envelope(self, client) -> None:  # type: ignore[no-untyped-def]
        response = await client.get("/no-such-thing")

        assert response.status_code == 404
        # One error shape for everything means a client writes one parser.
        assert response.json()["error"]["code"] == "not_found"

    async def test_validation_errors_report_the_offending_field(self, client) -> None:  # type: ignore[no-untyped-def]
        response = await client.post("/boom/validate", json={"pages": "many"})

        assert response.status_code == 422
        body = response.json()["error"]
        assert body["code"] == "validation_error"
        # Safe to return: it describes the request they just sent us.
        assert body["details"][0]["field"] == "pages"


class TestNoLeakage:
    async def test_unexpected_errors_leak_nothing(self, client) -> None:  # type: ignore[no-untyped-def]
        response = await client.get("/boom/bug")

        assert response.status_code == 500
        body = response.text
        # The raised ValueError contained a database URL with credentials.
        assert "postgres" not in body
        assert "10.0.0.4" not in body
        assert "ValueError" not in body
        assert "Traceback" not in body
        # The user still gets one thing: an id to quote to support.
        assert response.json()["error"]["request_id"]


class TestRequestId:
    async def test_an_inbound_id_is_honoured(self, client) -> None:  # type: ignore[no-untyped-def]
        # This is what lets a trace survive across services: the gateway generates
        # the id once and everything downstream reports under the same one.
        response = await client.get("/boom/domain", headers={REQUEST_ID_HEADER: "trace-abc"})

        assert response.headers[REQUEST_ID_HEADER] == "trace-abc"
        assert response.json()["error"]["request_id"] == "trace-abc"

    async def test_an_id_is_generated_when_absent(self, client) -> None:  # type: ignore[no-untyped-def]
        response = await client.get("/api/v1/health")

        assert response.headers[REQUEST_ID_HEADER]

    async def test_ids_differ_between_requests(self, client) -> None:  # type: ignore[no-untyped-def]
        first = await client.get("/api/v1/health")
        second = await client.get("/api/v1/health")

        # Context must not leak between requests, or correlation becomes a lie.
        assert first.headers[REQUEST_ID_HEADER] != second.headers[REQUEST_ID_HEADER]

    async def test_successful_responses_carry_the_id_too(self, client) -> None:  # type: ignore[no-untyped-def]
        response = await client.get("/api/v1/health")

        assert response.status_code == 200
        assert response.headers[REQUEST_ID_HEADER]

    async def test_the_header_survives_an_unhandled_crash(self, client) -> None:  # type: ignore[no-untyped-def]
        # Regression: on an unhandled exception the response is built by Starlette's
        # ServerErrorMiddleware, which sits outside our middleware. The header was
        # missing from exactly the 500s where support needs it most.
        response = await client.get("/boom/bug", headers={REQUEST_ID_HEADER: "trace-500"})

        assert response.status_code == 500
        assert response.headers[REQUEST_ID_HEADER] == "trace-500"
        assert response.json()["error"]["request_id"] == "trace-500"
