"""Tests for the sign-in use case.

No Google, no database, no HTTP. Every outside thing is a fake, which is what a
use case made of ports buys you.
"""

from datetime import UTC, datetime

import pytest

from app.adapters.system import FrozenClock, SequentialIdGenerator
from app.application.use_cases.sign_in import SignIn
from app.domain.errors import InvalidTokenError
from app.domain.models.identifiers import UserId
from app.domain.ports.auth import GoogleIdentity
from tests.fakes import InMemoryUserRepository

NOW = datetime(2026, 10, 4, 9, 0, tzinfo=UTC)


class FakeIdentityProvider:
    """Stands in for Google. Hands back whatever identity the test wants."""

    def __init__(self, identity: GoogleIdentity | None = None) -> None:
        self.identity = identity
        self.seen: list[str] = []

    async def verify(self, id_token: str) -> GoogleIdentity:
        self.seen.append(id_token)
        if self.identity is None:
            raise InvalidTokenError(provider="google")
        return self.identity


class FakeTokenService:
    def issue(self, user_id: UserId) -> str:
        return f"token-for-{user_id}"

    def verify(self, token: str) -> UserId:
        raise NotImplementedError


def build(  # type: ignore[no-untyped-def]
    identity: GoogleIdentity | None,
    users: InMemoryUserRepository | None = None,
    ids: SequentialIdGenerator | None = None,
):
    """Assemble the use case from fakes.

    `ids` is threaded through so a test that signs in twice keeps ONE id generator.
    A fresh SequentialIdGenerator restarts at 1, which collides with the first
    user - an artefact of the test double, since Uuid4Generator never repeats.
    """
    repo = users or InMemoryUserRepository()
    use_case = SignIn(
        identity_provider=FakeIdentityProvider(identity),
        users=repo,
        tokens=FakeTokenService(),
        clock=FrozenClock(NOW),
        ids=ids or SequentialIdGenerator(),
    )
    return use_case, repo


SUJAL = GoogleIdentity(subject="google-sub-1", email="sujal@example.com", name="Sujal")


class TestFirstSignIn:
    async def test_an_unknown_person_gets_an_account(self) -> None:
        sign_in, users = build(SUJAL)

        result = await sign_in.execute("any-token")

        assert result.is_new_user is True
        assert result.user.google_sub == "google-sub-1"
        assert result.user.created_at == NOW
        assert result.user.last_login_at == NOW
        assert await users.find_by_google_sub("google-sub-1") is not None

    async def test_a_token_is_issued(self) -> None:
        sign_in, _ = build(SUJAL)

        result = await sign_in.execute("any-token")

        assert result.access_token == f"token-for-{result.user.id}"


class TestReturningSignIn:
    async def test_the_same_person_does_not_get_a_second_account(self) -> None:
        sign_in, users = build(SUJAL)
        first = await sign_in.execute("t1")

        second = await sign_in.execute("t2")

        assert second.is_new_user is False
        assert second.user.id == first.user.id
        assert len(users.items) == 1

    async def test_a_changed_email_updates_the_same_account(self) -> None:
        # The entire reason identity is google_sub and not email. Someone changes
        # their address at Google; keying on email would create a second account
        # and orphan every report they have ever uploaded.
        sign_in, users = build(SUJAL)
        first = await sign_in.execute("t1")

        moved = GoogleIdentity(subject="google-sub-1", email="new@example.com", name="Sujal K")
        sign_in, _ = build(moved, users)
        second = await sign_in.execute("t2")

        assert second.user.id == first.user.id
        assert second.user.email == "new@example.com"
        assert second.user.name == "Sujal K"
        assert len(users.items) == 1

    async def test_the_original_signup_date_is_preserved(self) -> None:
        sign_in, users = build(SUJAL)
        await sign_in.execute("t1")

        later = FrozenClock(datetime(2026, 12, 25, tzinfo=UTC))
        sign_in = SignIn(
            identity_provider=FakeIdentityProvider(SUJAL),
            users=users,
            tokens=FakeTokenService(),
            clock=later,
            ids=SequentialIdGenerator(),
        )
        result = await sign_in.execute("t2")

        assert result.user.created_at == NOW  # unchanged
        assert result.user.last_login_at == datetime(2026, 12, 25, tzinfo=UTC)

    async def test_two_different_people_get_two_accounts(self) -> None:
        ids = SequentialIdGenerator()
        sign_in, users = build(SUJAL, ids=ids)
        await sign_in.execute("t1")

        other = GoogleIdentity(subject="google-sub-2", email="amma@example.com", name="Amma")
        sign_in, _ = build(other, users, ids=ids)
        await sign_in.execute("t2")

        assert len(users.items) == 2


class TestRejection:
    async def test_a_bad_token_creates_nothing(self) -> None:
        sign_in, users = build(None)

        with pytest.raises(InvalidTokenError):
            await sign_in.execute("forged")

        # A failed verification must leave no trace. An account created before
        # verification completed would be an unauthenticated signup.
        assert len(users.items) == 0
