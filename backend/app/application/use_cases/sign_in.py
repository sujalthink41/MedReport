"""Sign in with Google.

The sequence, in one place: verify the token, find or create the user, record the
login, hand back our own token.

Notice this file names three ports and zero technologies. It does not know it is
behind HTTP, that identity comes from Google, or that persistence is Postgres.
Exactly the same code serves a CLI or a mobile backend.
"""

from dataclasses import dataclass

from app.domain.models.identifiers import UserId
from app.domain.models.user import User
from app.domain.ports.auth import IdentityProvider, TokenService
from app.domain.ports.repositories import UserRepository
from app.domain.ports.services import Clock, IdGenerator


@dataclass(frozen=True)
class SignInResult:
    """What the caller gets back.

    ``is_new_user`` lets the API tell a first-time user apart from a returning one,
    so the client can route them to onboarding (create your first profile) instead
    of straight to an empty dashboard.
    """

    user: User
    access_token: str
    is_new_user: bool


class SignIn:
    def __init__(
        self,
        *,
        identity_provider: IdentityProvider,
        users: UserRepository,
        tokens: TokenService,
        clock: Clock,
        ids: IdGenerator,
    ) -> None:
        self._identity = identity_provider
        self._users = users
        self._tokens = tokens
        self._clock = clock
        self._ids = ids

    async def execute(self, google_token: str) -> SignInResult:
        # Raises InvalidTokenError, which the API maps to 401. No try/except here:
        # there is nothing useful this layer could do about a bad token.
        identity = await self._identity.verify(google_token)

        now = self._clock.now()
        existing = await self._users.find_by_google_sub(identity.subject)

        if existing is None:
            # Signup and login are the same endpoint on purpose. A separate
            # /register would be a screen the user has to choose correctly, and
            # choosing wrong is a confusing error for no benefit - we already know
            # from Google whether we have seen this person.
            user = User(
                id=UserId(self._ids.new_id()),
                google_sub=identity.subject,
                email=identity.email,
                name=identity.name,
                created_at=now,
                last_login_at=now,
            )
            await self._users.add(user)
            return SignInResult(
                user=user,
                access_token=self._tokens.issue(user.id),
                is_new_user=True,
            )

        # Their email or display name may have changed at Google since last time.
        # Identity is google_sub, so we follow the change rather than creating a
        # second account - which is the whole reason we key on sub and not email.
        refreshed = existing.with_google_profile(
            email=identity.email, name=identity.name
        ).logged_in_at(now)
        await self._users.update(refreshed)

        return SignInResult(
            user=refreshed,
            access_token=self._tokens.issue(refreshed.id),
            is_new_user=False,
        )
