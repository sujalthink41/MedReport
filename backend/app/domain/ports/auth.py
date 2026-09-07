"""Ports for authentication.

Two ports rather than one, because they change for different reasons: swapping
Google for Apple Sign-In touches only ``IdentityProvider``; swapping JWTs for
server-side sessions touches only ``TokenService``.
"""

from dataclasses import dataclass
from typing import Protocol

from app.domain.errors import InvalidInputError
from app.domain.models.identifiers import UserId


@dataclass(frozen=True, slots=True)
class GoogleIdentity:
    """What Google told us about a person, and nothing more.

    Deliberately not a ``User``. At the moment a token is verified we do not yet
    know whether this person has an account - deciding that is a business decision
    ("existing login" vs "new signup") and it belongs to a use case, not to an
    adapter talking to Google.

    An empty ``subject`` is refused because it is the identity key: two identities
    with ``""`` would match each other on lookup, which in this product means one
    person seeing another person's medical history.
    """

    subject: str
    email: str
    name: str | None

    def __post_init__(self) -> None:
        if not self.subject.strip():
            raise InvalidInputError(field="subject", reason="empty")
        if not self.email.strip():
            raise InvalidInputError(field="email", reason="empty")


class IdentityProvider(Protocol):
    """Turns a third-party sign-in token into a verified identity.

    Implemented in CP7 against Google, and faked in tests so that logging in never
    requires a network call.

    Async because it makes a network call. ``TokenService`` below is not, because
    local crypto never waits on anything - ``async`` describes "this waits on the
    outside world", not "this is important".

    Raises:
        InvalidTokenError: the token is expired, malformed, has a bad signature, or
            was not issued for our client id. Every implementation must raise this
            and not its provider's own exception type - a ``google.auth`` error
            reaching the domain would break the dependency rule, and would not map
            to a 401 at the API boundary.
        LLMUnavailableError-style infrastructure errors are NOT expected here; if the
            provider is unreachable, raise ``InfrastructureError`` so it is retried
            rather than reported as the caller's fault.
    """

    async def verify(self, id_token: str) -> GoogleIdentity: ...


class TokenService(Protocol):
    """Issues and verifies our own session tokens.

    After login we stop talking to Google entirely and carry a token of our own.
    That keeps normal operation independent of Google's uptime, lets us control
    expiry, and means CP8 can put permissions inside a token we control.

    Not async: signing and checking a signature is local CPU work measured in
    microseconds.
    """

    def issue(self, user_id: UserId) -> str:
        """Return a signed token identifying this user."""
        ...

    def verify(self, token: str) -> UserId:
        """Return the user id carried by a valid token.

        Raises:
            InvalidTokenError: the token is expired, tampered with, signed by the
                wrong key, or malformed. One error type for all of those on purpose -
                telling a caller *which* one failed is free reconnaissance for
                someone probing the endpoint.
        """
        ...
