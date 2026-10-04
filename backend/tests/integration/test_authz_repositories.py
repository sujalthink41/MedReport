"""Authorization persistence, against a real Postgres."""

from datetime import UTC, date, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters.db.authz_repositories import (
    SqlAuditLog,
    SqlMembershipRepository,
    SqlStaffRoleRepository,
)
from app.adapters.db.models import AccessAuditRow, ProfileRow, UserRoleRow, UserRow
from app.domain.models.authz import MembershipRole, StaffRole
from app.domain.models.identifiers import ProfileId, UserId

pytestmark = pytest.mark.integration

NOW = datetime(2026, 10, 4, 12, 0, tzinfo=UTC)


async def make_user(session: AsyncSession) -> UserId:
    user = UserRow(id=uuid4(), google_sub=f"sub-{uuid4()}", email="a@b.com")
    session.add(user)
    await session.flush()
    return UserId(user.id)


async def make_profile(session: AsyncSession, owner: UserId) -> ProfileId:
    profile = ProfileRow(
        id=uuid4(),
        owner_id=owner,
        display_name="Amma",
        date_of_birth=date(1962, 4, 11),
        sex="female",
        relationship="parent",
    )
    session.add(profile)
    await session.flush()
    return ProfileId(profile.id)


class TestMemberships:
    async def test_a_grant_is_readable_back(self, session: AsyncSession) -> None:
        owner = await make_user(session)
        profile = await make_profile(session, owner)
        repo = SqlMembershipRepository(session)

        await repo.grant(
            profile_id=profile,
            user_id=owner,
            role=MembershipRole.OWNER,
            invited_by=None,
            at=NOW,
        )

        assert await repo.roles_for_user(owner) == {profile: MembershipRole.OWNER}

    async def test_re_inviting_changes_the_role_rather_than_duplicating(
        self, session: AsyncSession
    ) -> None:
        owner = await make_user(session)
        sibling = await make_user(session)
        profile = await make_profile(session, owner)
        repo = SqlMembershipRepository(session)

        await repo.grant(
            profile_id=profile,
            user_id=sibling,
            role=MembershipRole.VIEWER,
            invited_by=owner,
            at=NOW,
        )
        await repo.grant(
            profile_id=profile,
            user_id=sibling,
            role=MembershipRole.CAREGIVER,
            invited_by=owner,
            at=NOW,
        )

        # Two rows with different roles would make "what may they do?" depend on
        # query order - a non-deterministic authorization answer.
        assert await repo.roles_for_user(sibling) == {profile: MembershipRole.CAREGIVER}

    async def test_two_people_share_one_profile(self, session: AsyncSession) -> None:
        owner = await make_user(session)
        sibling = await make_user(session)
        profile = await make_profile(session, owner)
        repo = SqlMembershipRepository(session)

        await repo.grant(
            profile_id=profile, user_id=owner, role=MembershipRole.OWNER, invited_by=None, at=NOW
        )
        await repo.grant(
            profile_id=profile,
            user_id=sibling,
            role=MembershipRole.VIEWER,
            invited_by=owner,
            at=NOW,
        )

        members = await repo.members_of(profile)
        assert members == {owner: MembershipRole.OWNER, sibling: MembershipRole.VIEWER}

    async def test_revoking_removes_access(self, session: AsyncSession) -> None:
        owner = await make_user(session)
        sibling = await make_user(session)
        profile = await make_profile(session, owner)
        repo = SqlMembershipRepository(session)
        await repo.grant(
            profile_id=profile,
            user_id=sibling,
            role=MembershipRole.VIEWER,
            invited_by=owner,
            at=NOW,
        )

        await repo.revoke(profile, sibling)

        assert await repo.roles_for_user(sibling) == {}

    async def test_owner_count_guards_the_last_owner(self, session: AsyncSession) -> None:
        owner = await make_user(session)
        viewer = await make_user(session)
        profile = await make_profile(session, owner)
        repo = SqlMembershipRepository(session)
        await repo.grant(
            profile_id=profile, user_id=owner, role=MembershipRole.OWNER, invited_by=None, at=NOW
        )
        await repo.grant(
            profile_id=profile, user_id=viewer, role=MembershipRole.VIEWER, invited_by=owner, at=NOW
        )

        # A profile with no owner is stranded: nobody can share it, nobody can
        # delete it, and the data becomes unreachable.
        assert await repo.count_owners(profile) == 1


class TestStaffRoles:
    async def test_a_granted_role_is_active(self, session: AsyncSession) -> None:
        user = await make_user(session)
        session.add(UserRoleRow(id=uuid4(), user_id=user, role=StaffRole.SUPPORT.value))
        await session.flush()

        roles = await SqlStaffRoleRepository(session).roles_for_user(user, now=NOW)

        assert roles == frozenset({StaffRole.SUPPORT})

    async def test_an_expired_role_grants_nothing(self, session: AsyncSession) -> None:
        user = await make_user(session)
        session.add(
            UserRoleRow(
                id=uuid4(),
                user_id=user,
                role=StaffRole.ADMIN.value,
                expires_at=NOW - timedelta(days=1),
            )
        )
        await session.flush()

        roles = await SqlStaffRoleRepository(session).roles_for_user(user, now=NOW)

        # Filtered in the query, not by a cleanup job. A lapsed grant stops working
        # the moment it lapses, even if nothing ever deletes the row.
        assert roles == frozenset()

    async def test_an_unknown_role_name_grants_nothing(self, session: AsyncSession) -> None:
        user = await make_user(session)
        session.add(UserRoleRow(id=uuid4(), user_id=user, role="role_we_removed"))
        await session.flush()

        roles = await SqlStaffRoleRepository(session).roles_for_user(user, now=NOW)

        # Fail closed. An unrecognised row must never be treated as permissive.
        assert roles == frozenset()

    async def test_an_ordinary_user_has_none(self, session: AsyncSession) -> None:
        user = await make_user(session)

        assert await SqlStaffRoleRepository(session).roles_for_user(user, now=NOW) == frozenset()


class TestAuditLog:
    async def test_access_is_recorded(self, session: AsyncSession) -> None:
        actor = await make_user(session)
        owner = await make_user(session)
        profile = await make_profile(session, owner)

        await SqlAuditLog(session).record(
            actor_user_id=actor,
            action="report:read",
            resource_type="report",
            resource_id="r-1",
            subject_profile_id=profile,
            via="break_glass",
            reason="investigating ticket 4821",
            request_id="req-9",
        )

        row = (await session.execute(select(AccessAuditRow))).scalars().one()
        assert row.actor_user_id == actor
        assert row.via == "break_glass"
        assert row.reason == "investigating ticket 4821"
        assert row.request_id == "req-9"  # ties the audit row to the request logs
