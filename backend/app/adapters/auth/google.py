"""Google Sign-In verification.

Satisfies ``IdentityProvider`` by shape — no inheritance, so ``adapters`` never
imports a base class from ``domain`` and the dependency arrow stays pointing inward.
"""

import asyncio

from google.auth.exceptions import GoogleAuthError
from google.auth.transport import requests as google_requests
from google.oauth2 import id_token as google_id_token

from app.core.logging import get_logger
from app.domain.errors import InvalidTokenError
from app.domain.ports.auth import GoogleIdentity

log = get_logger(__name__)

_GOOGLE_ISSUERS = frozenset({"accounts.google.com", "https://accounts.google.com"})


class GoogleIdentityProvider:
    def __init__(self, client_id: str) -> None:
        # Injected, not read from get_settings() inside verify(). A test constructs
        # this with a fake id, and the composition root stays the only place that
        # reads configuration.
        self._client_id = client_id
        self._request = google_requests.Request()

    async def verify(self, id_token: str) -> GoogleIdentity:
        if not self._client_id:
            # Misconfiguration, not a bad token. Fail loudly rather than skipping
            # the audience check below, which would accept tokens from any app.
            raise RuntimeError("google_client_id is not configured")

        try:
            # asyncio.to_thread because verify_oauth2_token is SYNCHRONOUS and makes
            # an HTTP call to fetch Google's signing keys. Calling it directly inside
            # `async def` would block the event loop - every other request on this
            # worker freezes until Google answers. One slow login stalls the server.
            claims = await asyncio.to_thread(
                google_id_token.verify_oauth2_token,
                id_token,
                self._request,
                # THE security-critical argument. Without `audience`, the signature
                # and expiry still check out, and any valid Google token from any
                # app in the world would authenticate here. Someone's token for an
                # unrelated todo app would become a login for a medical record.
                self._client_id,
            )
        except (ValueError, GoogleAuthError) as exc:
            # Translation at the boundary. A google.auth exception reaching the
            # domain would break the dependency rule, and would map to a 500 rather
            # than a 401 - telling the user their own expired token is our bug.
            log.warning("google_token_rejected", error_type=type(exc).__name__)
            raise InvalidTokenError(provider="google") from exc

        return self._to_identity(claims)

    def _to_identity(self, claims: dict[str, object]) -> GoogleIdentity:
        if claims.get("iss") not in _GOOGLE_ISSUERS:
            raise InvalidTokenError(provider="google", reason="issuer")

        # Google only guarantees `email` is real when `email_verified` is true. An
        # unverified address could belong to someone else entirely, and we key
        # notifications and support on it - so an unverified token is refused.
        if not claims.get("email_verified"):
            raise InvalidTokenError(provider="google", reason="email_unverified")

        subject = claims.get("sub")
        email = claims.get("email")
        if not isinstance(subject, str) or not isinstance(email, str):
            raise InvalidTokenError(provider="google", reason="incomplete_claims")

        name = claims.get("name")
        return GoogleIdentity(
            subject=subject,
            email=email,
            # Google does not always return a name; a missing one must not block
            # sign-in, so it is optional rather than an error.
            name=name if isinstance(name, str) else None,
        )
