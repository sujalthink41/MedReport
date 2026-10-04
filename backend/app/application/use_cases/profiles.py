"""Profile use cases.

Grouped in one module rather than one file each. The convention in the standards
doc is one file per use case; these five are small, share their dependencies, and
are always read together, so splitting them would be five files of imports and one
line of logic. The rule exists to stop god-services, not to maximise file count.

Every one of them authorizes before acting, and the check happens **here**, not in
the router — so the Celery worker and a future CLI get the same enforcement rather
than each re-implementing it.
"""

from dataclasses import dataclass
from datetime import date

from app.domain.errors import ConflictError, ProfileNotFoundError
from app.domain.models.authz import MembershipRole, Permission
from app.domain.models.enums import Relationship, Sex
from app.domain.models.identifiers import ProfileId
from app.domain.models.profile import Profile
from app.domain.ports.authz import MembershipRepository
from app.domain.ports.repositories import ProfileRepository
from app.domain.ports.services import Clock, IdGenerator
from app.domain.services.policy import AuthorizationContext, can, require


@dataclass(frozen=True)
class ProfileView:
    """A profile plus what the asking user may do with it.

    The permissions travel with the data so the client renders exactly the buttons
    the server would honour. Without this the UI guesses, and a guess that is too
    generous shows a delete button that returns 403.
    """

    profile: Profile
    role: MembershipRole | None
    permissions: frozenset[Permission]


@dataclass(frozen=True)
class NewProfile:
    display_name: str
    date_of_birth: date
    sex: Sex
    relationship: Relationship


class CreateProfile:
    def __init__(
        self,
        *,
        profiles: ProfileRepository,
        memberships: MembershipRepository,
        clock: Clock,
        ids: IdGenerator,
    ) -> None:
        self._profiles = profiles
        self._memberships = memberships
        self._clock = clock
        self._ids = ids

    async def execute(self, context: AuthorizationContext, new: NewProfile) -> ProfileView:
        """Create a profile and make the creator its owner.

        No permission check: any signed-in user may create a profile for themselves
        or a relative. There is nothing to authorize against — the resource does not
        exist yet.

        The membership grant is not optional bookkeeping. A profile with no owner is
        unreachable: nobody can read it, share it, or delete it, and the data is
        stranded. Both writes share one transaction so that cannot half-happen.
        """
        now = self._clock.now()
        profile = Profile(
            id=ProfileId(self._ids.new_id()),
            owner_id=context.user_id,
            display_name=new.display_name,
            date_of_birth=new.date_of_birth,
            sex=new.sex,
            relationship=new.relationship,
            created_at=now,
        )
        await self._profiles.add(profile)
        await self._memberships.grant(
            profile_id=profile.id,
            user_id=context.user_id,
            role=MembershipRole.OWNER,
            invited_by=None,
            at=now,
        )
        return ProfileView(
            profile=profile,
            role=MembershipRole.OWNER,
            permissions=frozenset(),  # filled by the caller from a fresh context
        )


class ListProfiles:
    def __init__(self, *, profiles: ProfileRepository) -> None:
        self._profiles = profiles

    async def execute(self, context: AuthorizationContext) -> list[ProfileView]:
        """Every profile this user can reach — their own and ones shared with them.

        Driven by memberships, not by ``profiles.owner_id``. Listing by owner would
        silently hide a parent's profile that a sibling shared, which is the main
        thing this product is for.
        """
        views: list[ProfileView] = []
        for profile_id, role in context.memberships.items():
            profile = await self._profiles.get(profile_id)
            if profile is None:
                # A membership whose profile is gone. Skip rather than fail: one
                # stale row must not break the whole list screen.
                continue
            views.append(
                ProfileView(
                    profile=profile,
                    role=role,
                    permissions=_permissions(context, profile_id),
                )
            )
        return sorted(views, key=lambda v: v.profile.created_at)


class GetProfile:
    def __init__(self, *, profiles: ProfileRepository) -> None:
        self._profiles = profiles

    async def execute(self, context: AuthorizationContext, profile_id: ProfileId) -> ProfileView:
        # Authorize BEFORE fetching. Checking afterwards leaks existence through
        # timing and through the difference between 403 and 404.
        require(context, Permission.PROFILE_READ, profile_id=profile_id)

        profile = await self._profiles.get(profile_id)
        if profile is None:
            raise ProfileNotFoundError(profile_id=str(profile_id))
        return ProfileView(
            profile=profile,
            role=context.memberships.get(profile_id),
            permissions=_permissions(context, profile_id),
        )


class UpdateProfile:
    def __init__(self, *, profiles: ProfileRepository) -> None:
        self._profiles = profiles

    async def execute(
        self, context: AuthorizationContext, profile_id: ProfileId, display_name: str
    ) -> ProfileView:
        require(context, Permission.PROFILE_UPDATE, profile_id=profile_id)

        profile = await self._profiles.get(profile_id)
        if profile is None:
            raise ProfileNotFoundError(profile_id=str(profile_id))

        # Date of birth and sex are deliberately NOT editable here. Both select
        # reference ranges, so changing one silently re-bands every historical
        # result. That needs its own flow with a recomputation, not a text field.
        renamed = profile.renamed_to(display_name)
        await self._profiles.update(renamed)
        return ProfileView(
            profile=renamed,
            role=context.memberships.get(profile_id),
            permissions=_permissions(context, profile_id),
        )


class DeleteProfile:
    def __init__(self, *, profiles: ProfileRepository, memberships: MembershipRepository) -> None:
        self._profiles = profiles
        self._memberships = memberships

    async def execute(self, context: AuthorizationContext, profile_id: ProfileId) -> None:
        """Delete a profile and everything under it.

        Owner only, and a hard delete: reports, observations and memberships go via
        ``ON DELETE CASCADE``. "Delete my data" has to mean it — a soft-deleted
        medical record is retained health data for someone who asked to be
        forgotten.
        """
        require(context, Permission.PROFILE_DELETE, profile_id=profile_id)

        profile = await self._profiles.get(profile_id)
        if profile is None:
            raise ProfileNotFoundError(profile_id=str(profile_id))

        await self._profiles.delete(profile_id)


def _permissions(context: AuthorizationContext, profile_id: ProfileId) -> frozenset[Permission]:
    return frozenset(p for p in Permission if can(context, p, profile_id=profile_id))


__all__ = [
    "ConflictError",
    "CreateProfile",
    "DeleteProfile",
    "GetProfile",
    "ListProfiles",
    "NewProfile",
    "ProfileView",
    "UpdateProfile",
]
