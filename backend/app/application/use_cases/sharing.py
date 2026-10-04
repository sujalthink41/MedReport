"""Sharing a profile with another person.

The caretaker feature from the product brief, made real: two siblings caring for a
parent, with different levels of access.
"""

from dataclasses import dataclass

from app.domain.errors import ConflictError, InvalidInputError, NotFoundError
from app.domain.models.authz import MembershipRole, Permission
from app.domain.models.identifiers import ProfileId, UserId
from app.domain.models.user import User
from app.domain.ports.authz import MembershipRepository
from app.domain.ports.repositories import UserRepository
from app.domain.ports.services import Clock
from app.domain.services.policy import AuthorizationContext, require


@dataclass(frozen=True)
class Member:
    user: User
    role: MembershipRole
    is_me: bool


class ListMembers:
    def __init__(self, *, memberships: MembershipRepository, users: UserRepository) -> None:
        self._memberships = memberships
        self._users = users

    async def execute(self, context: AuthorizationContext, profile_id: ProfileId) -> list[Member]:
        # PROFILE_READ, not PROFILE_SHARE. Everyone with access deserves to know who
        # else can see their relative's medical data — hiding that from viewers
        # would make the product less honest, not more secure.
        require(context, Permission.PROFILE_READ, profile_id=profile_id)

        roles = await self._memberships.members_of(profile_id)
        members: list[Member] = []
        for user_id, role in roles.items():
            user = await self._users.get(user_id)
            if user is None:
                continue
            members.append(Member(user=user, role=role, is_me=user_id == context.user_id))
        return sorted(members, key=lambda m: (m.role is not MembershipRole.OWNER, m.user.email))


class ShareProfile:
    def __init__(
        self,
        *,
        memberships: MembershipRepository,
        users: UserRepository,
        clock: Clock,
    ) -> None:
        self._memberships = memberships
        self._users = users
        self._clock = clock

    async def execute(
        self,
        context: AuthorizationContext,
        *,
        profile_id: ProfileId,
        email: str,
        role: MembershipRole,
    ) -> Member:
        """Give someone access, by email.

        Email is the invitation handle even though it is not our identity key.
        Nobody knows their own Google subject id, and you cannot share with someone
        by asking them for it.
        """
        require(context, Permission.PROFILE_SHARE, profile_id=profile_id)

        invitee = await self._users.find_by_email(email.strip().lower())
        if invitee is None:
            # A pending invitation for someone with no account yet is a real
            # feature, and a bigger one than it looks: it needs email delivery,
            # token expiry, and a claim flow. Out of scope for V1 - fail honestly
            # rather than silently doing nothing.
            raise NotFoundError(resource="user")

        if invitee.id == context.user_id:
            raise ConflictError(reason="already_a_member")

        now = self._clock.now()
        await self._memberships.grant(
            profile_id=profile_id,
            user_id=invitee.id,
            role=role,
            invited_by=context.user_id,
            at=now,
        )
        return Member(user=invitee, role=role, is_me=False)


class ChangeMemberRole:
    def __init__(self, *, memberships: MembershipRepository, clock: Clock) -> None:
        self._memberships = memberships
        self._clock = clock

    async def execute(
        self,
        context: AuthorizationContext,
        *,
        profile_id: ProfileId,
        user_id: UserId,
        role: MembershipRole,
    ) -> None:
        require(context, Permission.PROFILE_SHARE, profile_id=profile_id)

        current = await self._memberships.members_of(profile_id)
        if user_id not in current:
            raise NotFoundError(resource="membership")

        # Demoting the last owner would strand the profile: nobody could then share
        # it, delete it, or promote anyone. Check before writing, not after.
        if (
            current[user_id] is MembershipRole.OWNER
            and role is not MembershipRole.OWNER
            and await self._memberships.count_owners(profile_id) == 1
        ):
            raise InvalidInputError(field="role", reason="last_owner")

        await self._memberships.grant(
            profile_id=profile_id,
            user_id=user_id,
            role=role,
            invited_by=context.user_id,
            at=self._clock.now(),
        )


class RevokeMember:
    def __init__(self, *, memberships: MembershipRepository) -> None:
        self._memberships = memberships

    async def execute(
        self, context: AuthorizationContext, *, profile_id: ProfileId, user_id: UserId
    ) -> None:
        # Leaving a profile yourself needs no share permission. A viewer who no
        # longer wants access to a relative's records must not have to ask the
        # owner's permission to walk away.
        if user_id != context.user_id:
            require(context, Permission.PROFILE_SHARE, profile_id=profile_id)

        current = await self._memberships.members_of(profile_id)
        if user_id not in current:
            raise NotFoundError(resource="membership")

        if (
            current[user_id] is MembershipRole.OWNER
            and await self._memberships.count_owners(profile_id) == 1
        ):
            raise InvalidInputError(field="user_id", reason="last_owner")

        await self._memberships.revoke(profile_id, user_id)
