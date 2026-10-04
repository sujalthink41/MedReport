"""Tests for the JWT adapter.

Security-relevant, so these are written as attacks rather than as happy paths.
"""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import jwt
import pytest

from app.adapters.auth.jwt import JwtTokenService
from app.domain.errors import InvalidTokenError
from app.domain.models.identifiers import UserId

SECRET = "a" * 48
OTHER_SECRET = "b" * 48


@pytest.fixture
def tokens() -> JwtTokenService:
    return JwtTokenService(secret=SECRET, ttl_minutes=60)


class TestRoundTrip:
    def test_a_token_identifies_the_user_who_was_issued_it(self, tokens: JwtTokenService) -> None:
        user_id = UserId(uuid4())

        assert tokens.verify(tokens.issue(user_id)) == user_id

    def test_each_user_gets_a_distinct_token(self, tokens: JwtTokenService) -> None:
        assert tokens.issue(UserId(uuid4())) != tokens.issue(UserId(uuid4()))


class TestRejection:
    def test_a_token_signed_with_another_key_is_refused(self, tokens: JwtTokenService) -> None:
        forged = JwtTokenService(secret=OTHER_SECRET, ttl_minutes=60).issue(UserId(uuid4()))

        with pytest.raises(InvalidTokenError):
            tokens.verify(forged)

    def test_an_expired_token_is_refused(self) -> None:
        expired = JwtTokenService(secret=SECRET, ttl_minutes=-1).issue(UserId(uuid4()))

        with pytest.raises(InvalidTokenError):
            JwtTokenService(secret=SECRET, ttl_minutes=60).verify(expired)

    def test_a_tampered_token_is_refused(self, tokens: JwtTokenService) -> None:
        token = tokens.issue(UserId(uuid4()))
        tampered = token[:-4] + ("abcd" if not token.endswith("abcd") else "efgh")

        with pytest.raises(InvalidTokenError):
            tokens.verify(tampered)

    def test_the_alg_none_attack_is_refused(self, tokens: JwtTokenService) -> None:
        # The classic JWT vulnerability: an attacker sets the header algorithm to
        # "none" and ships an unsigned token. Libraries that trust the header
        # accept it. We pass algorithms=["HS256"] as a pinned list, so the header
        # gets no say.
        unsigned = jwt.encode(
            {
                "sub": str(uuid4()),
                "iss": "medreport",
                "exp": datetime.now(UTC) + timedelta(hours=1),
            },
            key="",
            algorithm="none",
        )

        with pytest.raises(InvalidTokenError):
            tokens.verify(unsigned)

    def test_a_token_from_another_issuer_is_refused(self, tokens: JwtTokenService) -> None:
        foreign = JwtTokenService(secret=SECRET, ttl_minutes=60, issuer="someone-else").issue(
            UserId(uuid4())
        )

        with pytest.raises(InvalidTokenError):
            tokens.verify(foreign)

    def test_a_token_without_an_expiry_is_refused(self, tokens: JwtTokenService) -> None:
        # A token with no exp is a permanent credential. If it leaks there is no way
        # to revoke it short of rotating the secret and logging out every user.
        forever = jwt.encode({"sub": str(uuid4()), "iss": "medreport"}, SECRET, algorithm="HS256")

        with pytest.raises(InvalidTokenError):
            tokens.verify(forever)

    @pytest.mark.parametrize("junk", ["", "not.a.token", "a.b.c"])
    def test_malformed_input_is_refused(self, tokens: JwtTokenService, junk: str) -> None:
        with pytest.raises(InvalidTokenError):
            tokens.verify(junk)


class TestConfiguration:
    def test_a_short_secret_is_refused_at_construction(self) -> None:
        # Fail at startup, not at the first forged token. A short HMAC secret is
        # brute-forceable offline, and the failure mode is silent - everything works
        # perfectly until someone mints their own tokens.
        with pytest.raises(RuntimeError, match="32 characters"):
            JwtTokenService(secret="short", ttl_minutes=60)
