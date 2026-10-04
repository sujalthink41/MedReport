"""The authorization truth table.

Every row here runs with Docker stopped, in microseconds, because the policy layer
is a pure function over a snapshot of facts. If this suite ever needed a database,
the decision logic would have leaked into the adapter.

These assertions are a security boundary, so they are written exhaustively rather
than representatively.
"""

from uuid import uuid4

import pytest

from app.domain.errors import PermissionDeniedError
from app.domain.models.authz import MembershipRole, Permission, StaffRole
from app.domain.models.identifiers import ProfileId, UserId
from app.domain.services.policy import AuthorizationContext, can, permissions_on, require

AMMA = ProfileId(uuid4())
SOMEONE_ELSE = ProfileId(uuid4())


def context(
    role: MembershipRole | None = None,
    staff: set[StaffRole] | None = None,
) -> AuthorizationContext:
    return AuthorizationContext(
        user_id=UserId(uuid4()),
        memberships={AMMA: role} if role else {},
        staff_roles=frozenset(staff or set()),
    )


# The matrix, written out in full. Reading this file should tell a reviewer exactly
# who can do what, without tracing code.
MATRIX = [
    # (membership role, permission, allowed?)
    (MembershipRole.OWNER, Permission.PROFILE_READ, True),
    (MembershipRole.OWNER, Permission.PROFILE_UPDATE, True),
    (MembershipRole.OWNER, Permission.PROFILE_DELETE, True),
    (MembershipRole.OWNER, Permission.PROFILE_SHARE, True),
    (MembershipRole.OWNER, Permission.REPORT_READ, True),
    (MembershipRole.OWNER, Permission.REPORT_UPLOAD, True),
    (MembershipRole.OWNER, Permission.REPORT_DELETE, True),
    # A caregiver does the day-to-day work but cannot perform the two irreversible
    # acts - deleting the profile, or changing who else has access.
    (MembershipRole.CAREGIVER, Permission.PROFILE_READ, True),
    (MembershipRole.CAREGIVER, Permission.REPORT_READ, True),
    (MembershipRole.CAREGIVER, Permission.REPORT_UPLOAD, True),
    (MembershipRole.CAREGIVER, Permission.REPORT_DELETE, False),
    (MembershipRole.CAREGIVER, Permission.PROFILE_DELETE, False),
    (MembershipRole.CAREGIVER, Permission.PROFILE_SHARE, False),
    (MembershipRole.CAREGIVER, Permission.PROFILE_UPDATE, False),
    # Read-only: the sibling who wants to follow along but should not change things.
    (MembershipRole.VIEWER, Permission.PROFILE_READ, True),
    (MembershipRole.VIEWER, Permission.REPORT_READ, True),
    (MembershipRole.VIEWER, Permission.REPORT_UPLOAD, False),
    (MembershipRole.VIEWER, Permission.REPORT_DELETE, False),
    (MembershipRole.VIEWER, Permission.PROFILE_UPDATE, False),
    (MembershipRole.VIEWER, Permission.PROFILE_DELETE, False),
    (MembershipRole.VIEWER, Permission.PROFILE_SHARE, False),
]


class TestMembership:
    @pytest.mark.parametrize(("role", "permission", "allowed"), MATRIX)
    def test_matrix(self, role: MembershipRole, permission: Permission, allowed: bool) -> None:
        assert can(context(role), permission, profile_id=AMMA) is allowed

    @pytest.mark.parametrize("permission", list(Permission))
    def test_a_stranger_can_do_nothing(self, permission: Permission) -> None:
        # Deny by default, asserted over EVERY permission rather than a sample.
        # A permission added tomorrow is covered by this test the day it is written.
        assert can(context(), permission, profile_id=AMMA) is False

    def test_access_does_not_spill_to_other_profiles(self) -> None:
        # Owning your mother's profile grants nothing on a stranger's. Permissions
        # are scoped to a relationship, not to a person.
        owner = context(MembershipRole.OWNER)

        assert can(owner, Permission.REPORT_READ, profile_id=AMMA) is True
        assert can(owner, Permission.REPORT_READ, profile_id=SOMEONE_ELSE) is False

    def test_two_people_can_share_one_profile_with_different_power(self) -> None:
        # The product feature: two siblings caring for a parent. Modelling this as
        # a global role is exactly what collapses here.
        sibling_a = context(MembershipRole.OWNER)
        sibling_b = context(MembershipRole.VIEWER)

        assert can(sibling_a, Permission.REPORT_UPLOAD, profile_id=AMMA) is True
        assert can(sibling_b, Permission.REPORT_UPLOAD, profile_id=AMMA) is False
        assert can(sibling_b, Permission.REPORT_READ, profile_id=AMMA) is True


class TestStaff:
    def test_support_can_debug_without_reading_health_data(self) -> None:
        support = context(staff={StaffRole.SUPPORT})

        assert can(support, Permission.TRACE_READ) is True
        assert can(support, Permission.REPORT_READ_METADATA_ANY) is True
        # The line that matters. Support can find out WHY an extraction went wrong
        # without ever seeing somebody's blood results.
        assert can(support, Permission.PHI_READ_ANY) is False
        assert can(support, Permission.REPORT_READ, profile_id=AMMA) is False

    def test_admin_does_not_mean_read_everyones_records(self) -> None:
        admin = context(staff={StaffRole.ADMIN})

        assert can(admin, Permission.USER_ADMIN) is True
        # "Admin can see everything" is a liability in a health product, not a
        # feature. PHI access is break-glass, granted explicitly and audited.
        assert can(admin, Permission.PHI_READ_ANY) is False
        assert can(admin, Permission.REPORT_READ, profile_id=AMMA) is False

    def test_a_curator_cannot_touch_user_data(self) -> None:
        curator = context(staff={StaffRole.CLINICAL_CURATOR})

        assert can(curator, Permission.DICTIONARY_WRITE) is True
        assert can(curator, Permission.USER_ADMIN) is False
        assert can(curator, Permission.REPORT_READ, profile_id=AMMA) is False

    def test_an_ordinary_user_has_no_staff_power(self) -> None:
        owner = context(MembershipRole.OWNER)

        assert can(owner, Permission.DICTIONARY_WRITE) is False
        assert can(owner, Permission.TRACE_READ) is False
        assert can(owner, Permission.REPORT_READ_METADATA_ANY) is False

    def test_the_two_systems_compose(self) -> None:
        # A support engineer who also cares for their own parent gets both sets.
        # Neither mechanism overrides the other.
        both = AuthorizationContext(
            user_id=UserId(uuid4()),
            memberships={AMMA: MembershipRole.OWNER},
            staff_roles=frozenset({StaffRole.SUPPORT}),
        )

        assert can(both, Permission.REPORT_UPLOAD, profile_id=AMMA) is True
        assert can(both, Permission.TRACE_READ) is True
        assert can(both, Permission.REPORT_READ, profile_id=SOMEONE_ELSE) is False


class TestGlobalVsScoped:
    def test_membership_grants_nothing_global(self) -> None:
        # Owning a profile must not imply any system-wide capability.
        owner = context(MembershipRole.OWNER)

        assert can(owner, Permission.PROFILE_READ) is False  # no profile_id given

    def test_staff_permissions_apply_to_scoped_checks_too(self) -> None:
        support = context(staff={StaffRole.SUPPORT})

        assert can(support, Permission.TRACE_READ, profile_id=AMMA) is True


class TestRequire:
    def test_require_passes_silently_when_allowed(self) -> None:
        require(context(MembershipRole.OWNER), Permission.REPORT_READ, profile_id=AMMA)

    def test_require_raises_when_denied(self) -> None:
        with pytest.raises(PermissionDeniedError):
            require(context(), Permission.REPORT_READ, profile_id=AMMA)

    def test_the_error_does_not_confirm_the_resource_exists(self) -> None:
        with pytest.raises(PermissionDeniedError) as caught:
            require(context(), Permission.REPORT_READ, profile_id=AMMA)

        # Carries the permission attempted, never the profile id. "This profile
        # exists but you may not read it" is free reconnaissance.
        assert "profile_id" not in caught.value.context
        assert caught.value.context == {"permission": "report:read"}


class TestPermissionsOn:
    def test_lists_everything_a_user_may_do(self) -> None:
        # Used by the API to tell a client which buttons to render, so the UI and
        # the enforcement can never disagree - both read the same source.
        granted = permissions_on(context(MembershipRole.CAREGIVER), AMMA)

        assert Permission.REPORT_UPLOAD in granted
        assert Permission.PROFILE_DELETE not in granted
