"""Our own session tokens.

After login we stop talking to Google entirely. This issues and verifies tokens we
control, so normal operation does not depend on Google's uptime and CP8 can put
permissions inside a token we own.
"""

from datetime import UTC, datetime, timedelta
from uuid import UUID

import jwt

from app.domain.errors import InvalidTokenError
from app.domain.models.identifiers import UserId

_ALGORITHM = "HS256"


class JwtTokenService:
    def __init__(self, secret: str, ttl_minutes: int, issuer: str = "medreport") -> None:
        if len(secret) < 32:
            # Fail at construction, not at the first forged token. A short HMAC
            # secret is brute-forceable offline, and the failure mode is silent:
            # everything works perfectly until someone mints their own tokens.
            raise RuntimeError("jwt secret must be at least 32 characters")
        self._secret = secret
        self._ttl = timedelta(minutes=ttl_minutes)
        self._issuer = issuer

    def issue(self, user_id: UserId) -> str:
        now = datetime.now(UTC)
        payload = {
            "sub": str(user_id),
            "iss": self._issuer,
            "iat": now,
            # Expiry is not optional. A token without one is a permanent credential;
            # if it leaks there is no way to revoke it short of rotating the secret
            # and logging out every user at once.
            "exp": now + self._ttl,
        }
        return jwt.encode(payload, self._secret, algorithm=_ALGORITHM)

    def verify(self, token: str) -> UserId:
        try:
            claims = jwt.decode(
                token,
                self._secret,
                # A LIST, pinned. Accepting whatever the token's header claims is
                # the classic JWT vulnerability: an attacker sets alg to "none", or
                # swaps HS256 for RS256 to make your public key the HMAC secret.
                algorithms=[_ALGORITHM],
                issuer=self._issuer,
                options={"require": ["exp", "iss", "sub"]},
            )
        except jwt.PyJWTError as exc:
            # One error for every failure mode. Distinguishing "expired" from "bad
            # signature" is free reconnaissance for anyone probing the endpoint.
            raise InvalidTokenError(reason="invalid") from exc

        try:
            return UserId(UUID(claims["sub"]))
        except (KeyError, ValueError) as exc:
            raise InvalidTokenError(reason="malformed_subject") from exc
