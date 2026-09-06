"""A person whose reports we hold.

Note the shape of this file: it is a person, and the rules about a person. There is
no ``save()``, no ``id`` generated inside, no timestamp taken from the clock. An
entity does not know how it is stored or when "now" is - both of those arrive from
outside, which is what makes this testable with no database and no frozen time.
"""

from dataclasses import dataclass
from datetime import date, datetime

from app.domain.errors import InvalidInputError
from app.domain.models.enums import Relationship, Sex
from app.domain.models.identifiers import ProfileId, UserId

MAX_HUMAN_AGE_YEARS = 130


@dataclass(frozen=True, slots=True)
class Profile:
    """An entity: it has identity.

    Two profiles both called "Amma" are different people. That is the whole
    distinction from a value object - equality here is about *who*, not about what
    the fields happen to contain.

    ``frozen=True`` on an entity looks odd at first, since entities change over time.
    The trick is that a change produces a *new* instance with the same id (see
    ``renamed_to``). You get immutability's safety without losing identity.
    """

    id: ProfileId
    owner_id: UserId
    display_name: str
    date_of_birth: date
    sex: Sex
    relationship: Relationship
    created_at: datetime

    def __post_init__(self) -> None:
        if not self.display_name.strip():
            raise InvalidInputError(field="display_name", reason="empty")
        if self.created_at.tzinfo is None:
            # A naive datetime compared against an aware one raises at runtime, and
            # trend maths silently shifts by hours. Reject it at the door.
            raise InvalidInputError(field="created_at", reason="must be timezone-aware")

    def age_years(self, as_of: date) -> int:
        """Age in whole years.

        ``as_of`` is a parameter rather than ``date.today()`` inside. That single
        choice is what lets us test "this reference range applies at 12 but not at
        13" without waiting a year or monkey-patching the standard library.
        """
        if as_of < self.date_of_birth:
            raise InvalidInputError(field="as_of", reason="before date of birth")

        years = as_of.year - self.date_of_birth.year
        had_birthday = (as_of.month, as_of.day) >= (
            self.date_of_birth.month,
            self.date_of_birth.day,
        )
        if not had_birthday:
            years -= 1

        if years > MAX_HUMAN_AGE_YEARS:
            raise InvalidInputError(field="date_of_birth", reason="implausible")
        return years

    def renamed_to(self, display_name: str) -> "Profile":
        """Return a new Profile with a different name, same identity.

        Mutation would be shorter. This is safer: nothing that holds a reference to
        the old value can be surprised by it changing underneath, and validation in
        ``__post_init__`` runs again on the new one - so an invalid Profile cannot
        exist even for a moment.
        """
        return Profile(
            id=self.id,
            owner_id=self.owner_id,
            display_name=display_name,
            date_of_birth=self.date_of_birth,
            sex=self.sex,
            relationship=self.relationship,
            created_at=self.created_at,
        )
