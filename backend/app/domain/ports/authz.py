"""Ports for loading authorization facts, and for recording access."""

from datetime import datetime
from typing import Protocol

from app.domain.models.authz import MembershipRole, StaffRole
from app.domain.models.identifiers import ProfileId, UserId


class MembershipRepository(Protocol):
    async def roles_for_user(self, user_id: UserId) -> dict[ProfileId, MembershipRole]:
        """Every profile this user has access to, and at what level.

        All memberships in one query, not one lookup per profile. A user typically
        has two or three; fetching them together turns authorization into a single
        round trip rather than an N+1 inside a permission check.
        """
        ...

    async def members_of(self, profile_id: ProfileId) -> dict[UserId, MembershipRole]:
        """Who can access this profile. Powers the sharing screen."""
        ...

    async def grant(
        self,
        *,
        profile_id: ProfileId,
        user_id: UserId,
        role: MembershipRole,
        invited_by: UserId | None,
        at: datetime,
    ) -> None:
        """Add or change a membership. Idempotent on (profile, user)."""
        ...

    async def revoke(self, profile_id: ProfileId, user_id: UserId) -> None: ...

    async def count_owners(self, profile_id: ProfileId) -> int:
        """Used to refuse removing the last owner.

        A profile with no owner is unreachable: nobody can share it, nobody can
        delete it, and the data is stranded.
        """
        ...


class StaffRoleRepository(Protocol):
    async def roles_for_user(self, user_id: UserId, *, now: datetime) -> frozenset[StaffRole]:
        """Active staff roles only.

        ``now`` is passed in rather than read from the clock here, so expiry is
        testable and so the same instant governs the whole authorization decision -
        a role must not expire halfway through one request.
        """
        ...


class AuditLog(Protocol):
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
        """Append one access record. Never updates, never deletes."""
        ...
