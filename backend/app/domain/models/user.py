from dataclasses import dataclass
from datetime import datetime

from app.domain.errors import InvalidInputError
from app.domain.models.identifiers import UserId


@dataclass(frozen=True, slots=True)
class User:
    """Someone with an account.

    Identity is ``google_sub``, not ``email``. People change their email address at
    Google; the sub never changes. Keying on email would create a second account
    and orphan that person's entire medical history.

    We never store a Google token here. We verify one at login and issue our own.
    """

    id: UserId
    google_sub: str
    email: str
    name: str | None
    created_at: datetime
    last_login_at: datetime | None

    def __post_init__(self) -> None:
        # An empty google_sub is not just untidy - it is the identity key. Two
        # users with "" would match each other on lookup, and in this product that
        # means one person seeing another person's medical history.
        if not self.google_sub.strip():
            raise InvalidInputError(field="google_sub", reason="empty")
        if not self.email.strip():
            raise InvalidInputError(field="email", reason="empty")
        if self.created_at.tzinfo is None:
            raise InvalidInputError(field="created_at", reason="must be timezone-aware")

    def logged_in_at(self, when: datetime) -> "User":
        """Same user, new login time.

        ``when`` is a parameter rather than ``datetime.now()`` inside, for the same
        reason ``Profile.age_years`` takes ``as_of``: a test can pass any moment and
        assert the result, instead of being at the mercy of the real clock.
        """
        if when.tzinfo is None:
            raise InvalidInputError(field="when", reason="must be timezone-aware")
        return User(
            id=self.id,
            google_sub=self.google_sub,
            email=self.email,
            name=self.name,
            created_at=self.created_at,
            last_login_at=when,  # the one thing that changes
        )
