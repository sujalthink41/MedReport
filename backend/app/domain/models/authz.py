"""Authorization vocabulary: what can be done, and by whom.

Two mechanisms live here, deliberately separate — see ADR 0004.

**Relationship-based (ReBAC)** answers 99% of requests: "can this user see this
report?" is about a *relationship* (user → profile → report), not a role. A person
is not "an owner" globally; they are the owner of one specific profile.

**Role-based (RBAC)** is for staff capabilities only: support, clinical curator,
admin. For an ordinary user, their set of global roles is empty.

Modelling the first as a role is the classic mistake: you end up with a `role`
column that secretly means "owner of something", and it collapses the moment two
people share a profile — which is this product's primary use case.
"""

from enum import StrEnum


class Permission(StrEnum):
    """Every distinct thing a caller may be allowed to do.

    An enum rather than free strings, so ``require(Permission.REPORT_DELETE)`` is
    checked by mypy while ``require("report:delte")`` would ship.

    Code checks **permissions, never role names**. `if user.role == "admin"` is fake
    RBAC: every new role becomes a code change and a deploy. Here a role is a bundle
    of these, so adding one is data.
    """

    # --- a profile and its contents -------------------------------------
    PROFILE_READ = "profile:read"
    PROFILE_UPDATE = "profile:update"
    PROFILE_DELETE = "profile:delete"
    PROFILE_SHARE = "profile:share"

    REPORT_READ = "report:read"
    REPORT_UPLOAD = "report:upload"
    REPORT_DELETE = "report:delete"

    # --- staff ------------------------------------------------------------
    REPORT_READ_METADATA_ANY = "report:read_metadata_any"
    """Status, page count, failure reason for ANY report - but not values.

    What support needs to answer "why did my upload fail?" without reading
    anybody's blood results.
    """

    TRACE_READ = "trace:read"
    """LLM prompt/response traces, for debugging a wrong extraction."""

    PHI_READ_ANY = "phi:read_any"
    """Actual health values for any profile. Break-glass only.

    Separate from every other staff permission on purpose. "Admin" must not
    silently mean "can read everyone's medical records" - that is a liability, not
    a feature. Every use writes an access_audit row and raises an alert.
    """

    DICTIONARY_WRITE = "dictionary:write"
    """Edit canonical tests, aliases, guideline thresholds, critical values.

    Dangerous in a quiet way: changing one critical threshold changes what every
    future report tells every user.
    """

    USER_ADMIN = "user:admin"
    """Grant and revoke roles."""


class MembershipRole(StrEnum):
    """What one person may do on one profile. The sharing feature.

    Two siblings caring for a parent are two rows in ``profile_members``.
    """

    OWNER = "owner"
    CAREGIVER = "caregiver"
    VIEWER = "viewer"


# What each membership role can do. Data, not branches - adding a role is one entry
# here plus a seed row, never an audit of every `if` in the codebase.
MEMBERSHIP_PERMISSIONS: dict[MembershipRole, frozenset[Permission]] = {
    MembershipRole.OWNER: frozenset(
        {
            Permission.PROFILE_READ,
            Permission.PROFILE_UPDATE,
            Permission.PROFILE_DELETE,
            Permission.PROFILE_SHARE,
            Permission.REPORT_READ,
            Permission.REPORT_UPLOAD,
            Permission.REPORT_DELETE,
        }
    ),
    # A caregiver does the day-to-day work - uploading reports, reading results -
    # but cannot delete the profile or change who else has access. Those are the
    # two irreversible acts, and they stay with the owner.
    MembershipRole.CAREGIVER: frozenset(
        {
            Permission.PROFILE_READ,
            Permission.REPORT_READ,
            Permission.REPORT_UPLOAD,
        }
    ),
    MembershipRole.VIEWER: frozenset(
        {
            Permission.PROFILE_READ,
            Permission.REPORT_READ,
        }
    ),
}


class StaffRole(StrEnum):
    """Global roles. Normally nobody has one."""

    SUPPORT = "support"
    CLINICAL_CURATOR = "clinical_curator"
    ADMIN = "admin"


STAFF_PERMISSIONS: dict[StaffRole, frozenset[Permission]] = {
    # Enough to debug "my haemoglobin showed wrong" - metadata and model traces -
    # and deliberately NOT the values themselves.
    StaffRole.SUPPORT: frozenset(
        {
            Permission.REPORT_READ_METADATA_ANY,
            Permission.TRACE_READ,
        }
    ),
    StaffRole.CLINICAL_CURATOR: frozenset(
        {
            Permission.DICTIONARY_WRITE,
            Permission.TRACE_READ,
        }
    ),
    # Note what is absent: PHI_READ_ANY. Admin administers users and roles; it does
    # not confer the ability to read anybody's medical records.
    StaffRole.ADMIN: frozenset(
        {
            Permission.USER_ADMIN,
            Permission.REPORT_READ_METADATA_ANY,
            Permission.TRACE_READ,
        }
    ),
}
