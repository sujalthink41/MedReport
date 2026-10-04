"""SQLAlchemy implementations of the authorization ports."""

from datetime import datetime
from uuid import uuid4

from sqlalchemy import delete, func, or_, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters.db.models import AccessAuditRow, ProfileMemberRow, UserRoleRow
from app.domain.models.authz import MembershipRole, StaffRole
from app.domain.models.identifiers import ProfileId, UserId


class SqlMembershipRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def roles_for_user(self, user_id: UserId) -> dict[ProfileId, MembershipRole]:
        result = await self._session.execute(
            select(ProfileMemberRow.profile_id, ProfileMemberRow.role).where(
                ProfileMemberRow.user_id == user_id
            )
        )
        return {ProfileId(pid): MembershipRole(role) for pid, role in result.all()}

    async def members_of(self, profile_id: ProfileId) -> dict[UserId, MembershipRole]:
        result = await self._session.execute(
            select(ProfileMemberRow.user_id, ProfileMemberRow.role).where(
                ProfileMemberRow.profile_id == profile_id
            )
        )
        return {UserId(uid): MembershipRole(role) for uid, role in result.all()}

    async def grant(
        self,
        *,
        profile_id: ProfileId,
        user_id: UserId,
        role: MembershipRole,
        invited_by: UserId | None,
        at: datetime,
    ) -> None:
        # Upsert rather than insert-or-update in Python. Re-inviting someone who is
        # already a member should change their role, not raise - and two concurrent
        # invites must not produce two rows with different roles, which would make
        # "what may they do?" depend on query order.
        await self._session.execute(
            pg_insert(ProfileMemberRow)
            .values(
                id=uuid4(),
                profile_id=profile_id,
                user_id=user_id,
                role=role.value,
                invited_by=invited_by,
                accepted_at=at,
            )
            .on_conflict_do_update(
                constraint="uq_profile_members_profile_user",
                set_={"role": role.value},
            )
        )
        await self._session.flush()

    async def revoke(self, profile_id: ProfileId, user_id: UserId) -> None:
        await self._session.execute(
            delete(ProfileMemberRow).where(
                ProfileMemberRow.profile_id == profile_id,
                ProfileMemberRow.user_id == user_id,
            )
        )
        await self._session.flush()

    async def count_owners(self, profile_id: ProfileId) -> int:
        count = await self._session.scalar(
            select(func.count())
            .select_from(ProfileMemberRow)
            .where(
                ProfileMemberRow.profile_id == profile_id,
                ProfileMemberRow.role == MembershipRole.OWNER.value,
            )
        )
        return int(count or 0)


class SqlStaffRoleRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def roles_for_user(self, user_id: UserId, *, now: datetime) -> frozenset[StaffRole]:
        result = await self._session.execute(
            select(UserRoleRow.role).where(
                UserRoleRow.user_id == user_id,
                # Expired grants are simply invisible. Filtering here rather than
                # relying on a cleanup job means a lapsed grant stops working the
                # moment it lapses, even if nothing ever deletes the row.
                or_(UserRoleRow.expires_at.is_(None), UserRoleRow.expires_at > now),
            )
        )
        roles = set()
        for (name,) in result.all():
            try:
                roles.add(StaffRole(name))
            except ValueError:
                # A role name the code no longer knows grants nothing. Fail closed:
                # an unrecognised row must never be treated as permissive.
                continue
        return frozenset(roles)


class SqlAuditLog:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def record(
        self,
        *,
        actor_user_id: UserId,
        action: str,
        resource_type: str,
        resource_id: str | None,
        subject_profile_id: ProfileId | None,
        via: str,
        reason: str | None,
        request_id: str | None,
    ) -> None:
        self._session.add(
            AccessAuditRow(
                id=uuid4(),
                actor_user_id=actor_user_id,
                action=action,
                resource_type=resource_type,
                resource_id=resource_id,
                subject_profile_id=subject_profile_id,
                via=via,
                reason=reason,
                request_id=request_id,
            )
        )
        await self._session.flush()
