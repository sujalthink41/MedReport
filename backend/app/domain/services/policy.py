"""The policy layer: one function that decides whether an action is allowed.

The shape to notice: **gather facts, then decide.**

Fetching memberships and roles is IO and belongs to an adapter. Deciding what they
mean is a rule and belongs here. So the caller assembles an ``AuthorizationContext``
— a plain snapshot of facts — and this module is a pure function over it.

That split is what lets the entire authorization truth table be tested with no
database, no app and no network, in milliseconds. It also means the same decision
runs identically from HTTP, from the Celery worker, and from a future CLI.
"""

from dataclasses import dataclass, field

from app.domain.errors import PermissionDeniedError
from app.domain.models.authz import (
    MEMBERSHIP_PERMISSIONS,
    STAFF_PERMISSIONS,
    MembershipRole,
    Permission,
    StaffRole,
)
from app.domain.models.identifiers import ProfileId, UserId


@dataclass(frozen=True)
class AuthorizationContext:
    """Everything needed to decide, captured at one moment.

    A snapshot, not a live object. Nothing here can lazily hit the database
    mid-decision, so a policy check can never be slow, flaky, or dependent on
    whichever session happens to be open.
    """

    user_id: UserId
    memberships: dict[ProfileId, MembershipRole] = field(default_factory=dict)
    staff_roles: frozenset[StaffRole] = frozenset()

    @property
    def staff_permissions(self) -> frozenset[Permission]:
        granted: set[Permission] = set()
        for role in self.staff_roles:
            granted |= STAFF_PERMISSIONS.get(role, frozenset())
        return frozenset(granted)


def permissions_on(context: AuthorizationContext, profile_id: ProfileId) -> frozenset[Permission]:
    """What this user may do on this profile.

    Membership permissions and staff permissions are unioned. A support engineer who
    is also caring for their own parent gets both sets — the two systems compose
    rather than one overriding the other.
    """
    role = context.memberships.get(profile_id)
    from_membership = MEMBERSHIP_PERMISSIONS.get(role, frozenset()) if role else frozenset()
    return frozenset(from_membership | context.staff_permissions)


def can(
    context: AuthorizationContext,
    permission: Permission,
    *,
    profile_id: ProfileId | None = None,
) -> bool:
    """May this user do this?

    **Deny by default.** Absence of a rule is a denial, never a fallthrough. A
    permission nobody has been granted is simply refused, which means adding a new
    permission is safe: it starts life denied to everyone rather than accidentally
    allowed.
    """
    if profile_id is None:
        # A global capability - staff only. Membership says nothing about actions
        # that are not scoped to a particular person's data.
        return permission in context.staff_permissions
    return permission in permissions_on(context, profile_id)


def require(
    context: AuthorizationContext,
    permission: Permission,
    *,
    profile_id: ProfileId | None = None,
) -> None:
    """Raise unless allowed.

    Separate from ``can`` on purpose. ``can`` is for branching in the UI layer
    ("should I show the delete button?"); ``require`` is for enforcement. Mixing
    them produces code that checks a boolean and forgets to act on it — a bug that
    reads as correct.

    ``PermissionDeniedError`` carries no explanation. Telling a caller "this profile
    exists but you may not read it" confirms the resource exists, which is free
    reconnaissance.
    """
    if not can(context, permission, profile_id=profile_id):
        raise PermissionDeniedError(permission=permission.value)
