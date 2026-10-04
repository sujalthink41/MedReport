"""Auth endpoints, end to end through HTTP — with Google and the database faked."""

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.adapters.auth.jwt import JwtTokenService
from app.adapters.system import FrozenClock, Uuid4Generator
from app.api.deps import get_identity_provider, get_sign_in, get_token_service, get_uow
from app.application.use_cases.sign_in import SignIn
from app.domain.errors import InvalidTokenError
from app.domain.models.identifiers import UserId
from app.domain.ports.auth import GoogleIdentity
from tests.fakes import InMemoryUnitOfWork, InMemoryUserRepository

NOW = datetime(2026, 10, 4, 9, 0, tzinfo=UTC)
SECRET = "c" * 48
SUJAL = GoogleIdentity(subject="google-sub-1", email="sujal@example.com", name="Sujal")


class FakeGoogle:
    def __init__(self, identity: GoogleIdentity | None) -> None:
        self.identity = identity

    async def verify(self, id_token: str) -> GoogleIdentity:
        if self.identity is None:
            raise InvalidTokenError(provider="google")
        return self.identity


@pytest.fixture
def users() -> InMemoryUserRepository:
    return InMemoryUserRepository()


@pytest.fixture
def tokens() -> JwtTokenService:
    return JwtTokenService(secret=SECRET, ttl_minutes=60)


@pytest.fixture
def auth_app(app: FastAPI, users: InMemoryUserRepository, tokens: JwtTokenService) -> FastAPI:
    """Swap Google and the database for fakes, keeping the real HTTP stack.

    dependency_overrides works precisely because the routes asked for dependencies
    rather than constructing a Google client themselves. Design paying for testing.
    """
    google = FakeGoogle(SUJAL)
    uow = InMemoryUnitOfWork()
    uow.users = users  # one repository shared by sign-in and by /me

    app.dependency_overrides[get_identity_provider] = lambda: google
    app.dependency_overrides[get_token_service] = lambda: tokens
    app.dependency_overrides[get_uow] = lambda: uow
    app.dependency_overrides[get_sign_in] = lambda: SignIn(
        identity_provider=google,
        users=users,
        tokens=tokens,
        clock=FrozenClock(NOW),
        ids=Uuid4Generator(),
    )

    app.state.fake_google = google
    return app


@pytest.fixture
async def client(auth_app: FastAPI):  # type: ignore[no-untyped-def]
    transport = ASGITransport(app=auth_app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


class TestGoogleSignIn:
    async def test_a_new_user_is_signed_in(self, client) -> None:  # type: ignore[no-untyped-def]
        response = await client.post("/api/v1/auth/google", json={"id_token": "x"})

        assert response.status_code == 200
        body = response.json()
        assert body["token_type"] == "bearer"
        assert body["access_token"]
        assert body["is_new_user"] is True
        assert body["user"]["email"] == "sujal@example.com"

    async def test_a_second_sign_in_is_not_a_new_user(self, client) -> None:  # type: ignore[no-untyped-def]
        await client.post("/api/v1/auth/google", json={"id_token": "x"})

        second = await client.post("/api/v1/auth/google", json={"id_token": "x"})

        assert second.json()["is_new_user"] is False

    async def test_a_rejected_google_token_is_401(self, client, auth_app: FastAPI) -> None:  # type: ignore[no-untyped-def]
        auth_app.state.fake_google.identity = None

        response = await client.post("/api/v1/auth/google", json={"id_token": "forged"})

        # 401, not 500: the caller's token is the caller's problem. This works
        # because InvalidTokenError is a DomainError and STATUS_MAP routes it.
        assert response.status_code == 401
        assert response.json()["error"]["code"] == "invalid_token"

    async def test_a_missing_token_is_a_validation_error(self, client) -> None:  # type: ignore[no-untyped-def]
        response = await client.post("/api/v1/auth/google", json={})

        assert response.status_code == 422

    async def test_an_empty_token_is_rejected_before_reaching_google(self, client) -> None:  # type: ignore[no-untyped-def]
        response = await client.post("/api/v1/auth/google", json={"id_token": ""})

        # min_length=1 on the DTO. Cheap input validation at the edge means an
        # obviously-empty value never costs a network round trip.
        assert response.status_code == 422


class TestCurrentUser:
    async def test_a_valid_token_identifies_the_caller(self, client) -> None:  # type: ignore[no-untyped-def]
        signed_in = (await client.post("/api/v1/auth/google", json={"id_token": "x"})).json()

        response = await client.get(
            "/api/v1/auth/me",
            headers={"Authorization": f"Bearer {signed_in['access_token']}"},
        )

        assert response.status_code == 200
        assert response.json()["email"] == "sujal@example.com"

    async def test_no_header_is_401(self, client) -> None:  # type: ignore[no-untyped-def]
        response = await client.get("/api/v1/auth/me")

        assert response.status_code == 401
        assert response.json()["error"]["code"] == "invalid_token"

    async def test_a_garbage_token_is_401(self, client) -> None:  # type: ignore[no-untyped-def]
        response = await client.get("/api/v1/auth/me", headers={"Authorization": "Bearer nonsense"})

        assert response.status_code == 401

    async def test_a_valid_token_for_a_deleted_user_is_rejected(  # type: ignore[no-untyped-def]
        self, client, users: InMemoryUserRepository, tokens: JwtTokenService
    ) -> None:
        # A token that outlives its account must stop working. Otherwise deleting a
        # user leaves a credential that still authenticates for up to two weeks.
        orphan = tokens.issue(UserId(uuid4()))

        response = await client.get(
            "/api/v1/auth/me", headers={"Authorization": f"Bearer {orphan}"}
        )

        assert response.status_code == 404
