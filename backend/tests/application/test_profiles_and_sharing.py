"""Profile and sharing use cases, against fakes only.

The scenarios here are the product brief made executable: a person creates a
profile for their mother, shares it with a sibling, and the two have different
power over it.
"""

from datetime import UTC, date, datetime
from uuid import uuid4

import pytest

from app.adapters.system import FrozenClock, SequentialIdGenerator
from app.application.use_cases.profiles import (
    CreateProfile,
    DeleteProfile,
    GetProfile,
    ListProfiles,
    NewProfile,
    UpdateProfile,
)
from app.application.use_cases.sharing import (
    ChangeMemberRole,
    ListMembers,
    RevokeMember,
    ShareProfile,
)
from app.domain.errors import (
    InvalidInputError,
    NotFoundError,
    PermissionDeniedError,
    ProfileNotFoundError,
)
from app.domain.models.authz import MembershipRole, Permission
from app.domain.models.enums import Relationship, Sex
from app.domain.models.identifiers import ProfileId, UserId
from app.domain.models.user import User
from app.domain.services.policy import AuthorizationContext
from tests.fakes import InMemoryUnitOfWork

NOW = datetime(2026, 10, 4, 9, 0, tzinfo=UTC)
AMMA = NewProfile(
    display_name="Amma",
    date_of_birth=date(1962, 4, 11),
    sex=Sex.FEMALE,
    relationship=Relationship.PARENT,
)


@pytest.fixture
def uow() -> InMemoryUnitOfWork:
    return InMemoryUnitOfWork()


async def make_user(uow: InMemoryUnitOfWork, email: str) -> UserId:
    user = User(
        id=UserId(uuid4()),
        google_sub=f"sub-{uuid4()}",
        email=email,
        name=email.split("@")[0],
        created_at=NOW,
        last_login_at=NOW,
    )
    await uow.users.add(user)
    return user.id


def context_for(uow: InMemoryUnitOfWork, user_id: UserId) -> AuthorizationContext:
    """Rebuild the context the way the API dependency does, from current facts."""
    memberships = {
        pid: role for (pid, uid), role in uow.memberships.grants.items() if uid == user_id
    }
    return AuthorizationContext(user_id=user_id, memberships=memberships)


async def create_amma(uow: InMemoryUnitOfWork, owner: UserId) -> ProfileId:
    view = await CreateProfile(
        profiles=uow.profiles,
        memberships=uow.memberships,
        clock=FrozenClock(NOW),
        ids=SequentialIdGenerator(),
    ).execute(context_for(uow, owner), AMMA)
    return view.profile.id


class TestCreate:
    async def test_the_creator_becomes_the_owner(self, uow: InMemoryUnitOfWork) -> None:
        me = await make_user(uow, "me@example.com")

        profile_id = await create_amma(uow, me)

        # Without this grant the profile is stranded: nobody can read, share or
        # delete it, and the data is unreachable forever.
        assert context_for(uow, me).memberships == {profile_id: MembershipRole.OWNER}

    async def test_the_profile_is_stored(self, uow: InMemoryUnitOfWork) -> None:
        me = await make_user(uow, "me@example.com")

        profile_id = await create_amma(uow, me)

        stored = await uow.profiles.get(profile_id)
        assert stored is not None
        assert stored.display_name == "Amma"
        assert stored.date_of_birth == date(1962, 4, 11)


class TestRead:
    async def test_an_owner_can_read(self, uow: InMemoryUnitOfWork) -> None:
        me = await make_user(uow, "me@example.com")
        profile_id = await create_amma(uow, me)

        view = await GetProfile(profiles=uow.profiles).execute(context_for(uow, me), profile_id)

        assert view.profile.display_name == "Amma"
        assert Permission.REPORT_UPLOAD in view.permissions

    async def test_a_stranger_cannot_read(self, uow: InMemoryUnitOfWork) -> None:
        me = await make_user(uow, "me@example.com")
        stranger = await make_user(uow, "stranger@example.com")
        profile_id = await create_amma(uow, me)

        with pytest.raises(PermissionDeniedError):
            await GetProfile(profiles=uow.profiles).execute(context_for(uow, stranger), profile_id)

    async def test_listing_includes_profiles_shared_with_me(self, uow: InMemoryUnitOfWork) -> None:
        me = await make_user(uow, "me@example.com")
        sibling = await make_user(uow, "sibling@example.com")
        profile_id = await create_amma(uow, me)
        await ShareProfile(
            memberships=uow.memberships, users=uow.users, clock=FrozenClock(NOW)
        ).execute(
            context_for(uow, me),
            profile_id=profile_id,
            email="sibling@example.com",
            role=MembershipRole.VIEWER,
        )

        listed = await ListProfiles(profiles=uow.profiles).execute(context_for(uow, sibling))

        # The whole caretaker story: a sibling sees Amma's profile even though
        # they did not create it. Listing by owner_id would hide it.
        assert [v.profile.id for v in listed] == [profile_id]
        assert listed[0].role is MembershipRole.VIEWER

    async def test_listing_excludes_other_peoples_profiles(self, uow: InMemoryUnitOfWork) -> None:
        me = await make_user(uow, "me@example.com")
        stranger = await make_user(uow, "stranger@example.com")
        await create_amma(uow, me)

        assert await ListProfiles(profiles=uow.profiles).execute(context_for(uow, stranger)) == []


class TestUpdateAndDelete:
    async def test_an_owner_can_rename(self, uow: InMemoryUnitOfWork) -> None:
        me = await make_user(uow, "me@example.com")
        profile_id = await create_amma(uow, me)

        view = await UpdateProfile(profiles=uow.profiles).execute(
            context_for(uow, me), profile_id, "Mother"
        )

        assert view.profile.display_name == "Mother"

    async def test_a_caregiver_cannot_rename(self, uow: InMemoryUnitOfWork) -> None:
        me = await make_user(uow, "me@example.com")
        helper = await make_user(uow, "helper@example.com")
        profile_id = await create_amma(uow, me)
        await ShareProfile(
            memberships=uow.memberships, users=uow.users, clock=FrozenClock(NOW)
        ).execute(
            context_for(uow, me),
            profile_id=profile_id,
            email="helper@example.com",
            role=MembershipRole.CAREGIVER,
        )

        with pytest.raises(PermissionDeniedError):
            await UpdateProfile(profiles=uow.profiles).execute(
                context_for(uow, helper), profile_id, "Hacked"
            )

    async def test_only_an_owner_can_delete(self, uow: InMemoryUnitOfWork) -> None:
        me = await make_user(uow, "me@example.com")
        helper = await make_user(uow, "helper@example.com")
        profile_id = await create_amma(uow, me)
        await ShareProfile(
            memberships=uow.memberships, users=uow.users, clock=FrozenClock(NOW)
        ).execute(
            context_for(uow, me),
            profile_id=profile_id,
            email="helper@example.com",
            role=MembershipRole.CAREGIVER,
        )

        with pytest.raises(PermissionDeniedError):
            await DeleteProfile(profiles=uow.profiles, memberships=uow.memberships).execute(
                context_for(uow, helper), profile_id
            )

        await DeleteProfile(profiles=uow.profiles, memberships=uow.memberships).execute(
            context_for(uow, me), profile_id
        )
        assert await uow.profiles.get(profile_id) is None

    async def test_deleting_something_that_is_gone_is_not_found(
        self, uow: InMemoryUnitOfWork
    ) -> None:
        me = await make_user(uow, "me@example.com")
        profile_id = await create_amma(uow, me)
        await uow.profiles.delete(profile_id)

        with pytest.raises(ProfileNotFoundError):
            await DeleteProfile(profiles=uow.profiles, memberships=uow.memberships).execute(
                context_for(uow, me), profile_id
            )


class TestSharing:
    @pytest.fixture
    async def shared(self, uow: InMemoryUnitOfWork):  # type: ignore[no-untyped-def]
        me = await make_user(uow, "me@example.com")
        sibling = await make_user(uow, "sibling@example.com")
        profile_id = await create_amma(uow, me)
        return me, sibling, profile_id

    def _share(self, uow: InMemoryUnitOfWork) -> ShareProfile:
        return ShareProfile(memberships=uow.memberships, users=uow.users, clock=FrozenClock(NOW))

    async def test_an_owner_can_share(self, uow: InMemoryUnitOfWork, shared) -> None:  # type: ignore[no-untyped-def]
        me, sibling, profile_id = shared

        member = await self._share(uow).execute(
            context_for(uow, me),
            profile_id=profile_id,
            email="sibling@example.com",
            role=MembershipRole.CAREGIVER,
        )

        assert member.role is MembershipRole.CAREGIVER
        assert context_for(uow, sibling).memberships == {profile_id: MembershipRole.CAREGIVER}

    async def test_a_viewer_cannot_share(self, uow: InMemoryUnitOfWork, shared) -> None:  # type: ignore[no-untyped-def]
        me, sibling, profile_id = shared
        await self._share(uow).execute(
            context_for(uow, me),
            profile_id=profile_id,
            email="sibling@example.com",
            role=MembershipRole.VIEWER,
        )

        with pytest.raises(PermissionDeniedError):
            await self._share(uow).execute(
                context_for(uow, sibling),
                profile_id=profile_id,
                email="someone@example.com",
                role=MembershipRole.VIEWER,
            )

    async def test_sharing_with_an_unknown_email_fails_honestly(
        self,
        uow: InMemoryUnitOfWork,
        shared,  # type: ignore[no-untyped-def]
    ) -> None:
        me, _, profile_id = shared

        # A pending invitation for someone with no account is a real feature with
        # email delivery, token expiry and a claim flow. Out of scope for V1 - so
        # fail rather than silently doing nothing.
        with pytest.raises(NotFoundError):
            await self._share(uow).execute(
                context_for(uow, me),
                profile_id=profile_id,
                email="nobody@example.com",
                role=MembershipRole.VIEWER,
            )

    async def test_email_matching_ignores_case(self, uow: InMemoryUnitOfWork, shared) -> None:  # type: ignore[no-untyped-def]
        me, sibling, profile_id = shared

        await self._share(uow).execute(
            context_for(uow, me),
            profile_id=profile_id,
            email="  SIBLING@Example.com ",
            role=MembershipRole.VIEWER,
        )

        assert profile_id in context_for(uow, sibling).memberships

    async def test_everyone_with_access_can_see_who_else_has_access(
        self,
        uow: InMemoryUnitOfWork,
        shared,  # type: ignore[no-untyped-def]
    ) -> None:
        me, sibling, profile_id = shared
        await self._share(uow).execute(
            context_for(uow, me),
            profile_id=profile_id,
            email="sibling@example.com",
            role=MembershipRole.VIEWER,
        )

        # A viewer may list members. Hiding who can read your relative's medical
        # data would make the product less honest, not more secure.
        members = await ListMembers(memberships=uow.memberships, users=uow.users).execute(
            context_for(uow, sibling), profile_id
        )

        assert {m.user.email for m in members} == {"me@example.com", "sibling@example.com"}
        assert [m.is_me for m in members if m.user.id == sibling] == [True]


class TestLastOwnerProtection:
    async def test_the_last_owner_cannot_be_demoted(self, uow: InMemoryUnitOfWork) -> None:
        me = await make_user(uow, "me@example.com")
        profile_id = await create_amma(uow, me)

        with pytest.raises(InvalidInputError, match="last_owner"):
            await ChangeMemberRole(memberships=uow.memberships, clock=FrozenClock(NOW)).execute(
                context_for(uow, me),
                profile_id=profile_id,
                user_id=me,
                role=MembershipRole.VIEWER,
            )

    async def test_the_last_owner_cannot_be_removed(self, uow: InMemoryUnitOfWork) -> None:
        me = await make_user(uow, "me@example.com")
        profile_id = await create_amma(uow, me)

        with pytest.raises(InvalidInputError, match="last_owner"):
            await RevokeMember(memberships=uow.memberships).execute(
                context_for(uow, me), profile_id=profile_id, user_id=me
            )

    async def test_an_owner_can_step_down_once_another_exists(
        self, uow: InMemoryUnitOfWork
    ) -> None:
        me = await make_user(uow, "me@example.com")
        # Created so the email lookup finds them; the id itself is not needed here.
        await make_user(uow, "sibling@example.com")
        profile_id = await create_amma(uow, me)
        await ShareProfile(
            memberships=uow.memberships, users=uow.users, clock=FrozenClock(NOW)
        ).execute(
            context_for(uow, me),
            profile_id=profile_id,
            email="sibling@example.com",
            role=MembershipRole.OWNER,
        )

        await ChangeMemberRole(memberships=uow.memberships, clock=FrozenClock(NOW)).execute(
            context_for(uow, me), profile_id=profile_id, user_id=me, role=MembershipRole.VIEWER
        )

        assert context_for(uow, me).memberships == {profile_id: MembershipRole.VIEWER}


class TestLeaving:
    async def test_a_viewer_can_leave_without_permission(self, uow: InMemoryUnitOfWork) -> None:
        me = await make_user(uow, "me@example.com")
        sibling = await make_user(uow, "sibling@example.com")
        profile_id = await create_amma(uow, me)
        await ShareProfile(
            memberships=uow.memberships, users=uow.users, clock=FrozenClock(NOW)
        ).execute(
            context_for(uow, me),
            profile_id=profile_id,
            email="sibling@example.com",
            role=MembershipRole.VIEWER,
        )

        # No PROFILE_SHARE needed. Walking away from a relative's medical records
        # must not require the owner's approval.
        await RevokeMember(memberships=uow.memberships).execute(
            context_for(uow, sibling), profile_id=profile_id, user_id=sibling
        )

        assert context_for(uow, sibling).memberships == {}

    async def test_a_viewer_cannot_remove_somebody_else(self, uow: InMemoryUnitOfWork) -> None:
        me = await make_user(uow, "me@example.com")
        sibling = await make_user(uow, "sibling@example.com")
        profile_id = await create_amma(uow, me)
        await ShareProfile(
            memberships=uow.memberships, users=uow.users, clock=FrozenClock(NOW)
        ).execute(
            context_for(uow, me),
            profile_id=profile_id,
            email="sibling@example.com",
            role=MembershipRole.VIEWER,
        )

        with pytest.raises(PermissionDeniedError):
            await RevokeMember(memberships=uow.memberships).execute(
                context_for(uow, sibling), profile_id=profile_id, user_id=me
            )
