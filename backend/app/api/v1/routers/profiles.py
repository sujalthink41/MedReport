"""Profile and sharing endpoints.

Thin by design: translate HTTP into a use-case call, and the result back into HTTP.
Every authorization check lives in the use case, not here, so the same rules apply
when the worker or a CLI calls the same code.
"""

from uuid import UUID

from fastapi import APIRouter, status

from app.adapters.system import SystemClock, Uuid4Generator
from app.api.deps import AuthzDep, UowDep
from app.api.v1.schemas.profiles import (
    ChangeRoleRequest,
    CreateProfileRequest,
    MemberResponse,
    ProfileResponse,
    ShareProfileRequest,
    UpdateProfileRequest,
)
from app.application.use_cases.profiles import (
    CreateProfile,
    DeleteProfile,
    GetProfile,
    ListProfiles,
    NewProfile,
    ProfileView,
    UpdateProfile,
)
from app.application.use_cases.sharing import (
    ChangeMemberRole,
    ListMembers,
    Member,
    RevokeMember,
    ShareProfile,
)
from app.domain.models.authz import Permission
from app.domain.models.identifiers import ProfileId, UserId
from app.domain.services.policy import AuthorizationContext, can

router = APIRouter(prefix="/profiles", tags=["profiles"])


def _to_response(view: ProfileView, context: AuthorizationContext) -> ProfileResponse:
    permissions = sorted(p.value for p in Permission if can(context, p, profile_id=view.profile.id))
    return ProfileResponse(
        id=str(view.profile.id),
        display_name=view.profile.display_name,
        date_of_birth=view.profile.date_of_birth,
        sex=view.profile.sex,
        relationship=view.profile.relationship,
        role=view.role,
        permissions=permissions,
    )


def _member_response(member: Member) -> MemberResponse:
    return MemberResponse(
        user_id=str(member.user.id),
        email=member.user.email,
        name=member.user.name,
        role=member.role,
        is_me=member.is_me,
    )


@router.post("", response_model=ProfileResponse, status_code=status.HTTP_201_CREATED)
async def create_profile(
    body: CreateProfileRequest, context: AuthzDep, uow: UowDep
) -> ProfileResponse:
    view = await CreateProfile(
        profiles=uow.profiles,
        memberships=uow.memberships,
        clock=SystemClock(),
        ids=Uuid4Generator(),
    ).execute(context, NewProfile(**body.model_dump()))

    # The context was built before this profile existed, so it holds no membership
    # for it. Reporting permissions from a stale context would show the creator as
    # having none on the thing they just created.
    from app.domain.models.authz import MembershipRole

    fresh = AuthorizationContext(
        user_id=context.user_id,
        memberships={**context.memberships, view.profile.id: MembershipRole.OWNER},
        staff_roles=context.staff_roles,
    )
    return _to_response(view, fresh)


@router.get("", response_model=list[ProfileResponse])
async def list_profiles(context: AuthzDep, uow: UowDep) -> list[ProfileResponse]:
    views = await ListProfiles(profiles=uow.profiles).execute(context)
    return [_to_response(v, context) for v in views]


@router.get("/{profile_id}", response_model=ProfileResponse)
async def get_profile(profile_id: UUID, context: AuthzDep, uow: UowDep) -> ProfileResponse:
    view = await GetProfile(profiles=uow.profiles).execute(context, ProfileId(profile_id))
    return _to_response(view, context)


@router.patch("/{profile_id}", response_model=ProfileResponse)
async def update_profile(
    profile_id: UUID, body: UpdateProfileRequest, context: AuthzDep, uow: UowDep
) -> ProfileResponse:
    view = await UpdateProfile(profiles=uow.profiles).execute(
        context, ProfileId(profile_id), body.display_name
    )
    return _to_response(view, context)


@router.delete("/{profile_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_profile(profile_id: UUID, context: AuthzDep, uow: UowDep) -> None:
    await DeleteProfile(profiles=uow.profiles, memberships=uow.memberships).execute(
        context, ProfileId(profile_id)
    )


# --- sharing ---------------------------------------------------------------


@router.get("/{profile_id}/members", response_model=list[MemberResponse])
async def list_members(profile_id: UUID, context: AuthzDep, uow: UowDep) -> list[MemberResponse]:
    members = await ListMembers(memberships=uow.memberships, users=uow.users).execute(
        context, ProfileId(profile_id)
    )
    return [_member_response(m) for m in members]


@router.post(
    "/{profile_id}/members", response_model=MemberResponse, status_code=status.HTTP_201_CREATED
)
async def share_profile(
    profile_id: UUID, body: ShareProfileRequest, context: AuthzDep, uow: UowDep
) -> MemberResponse:
    member = await ShareProfile(
        memberships=uow.memberships, users=uow.users, clock=SystemClock()
    ).execute(context, profile_id=ProfileId(profile_id), email=str(body.email), role=body.role)
    return _member_response(member)


@router.patch("/{profile_id}/members/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
async def change_member_role(
    profile_id: UUID, user_id: UUID, body: ChangeRoleRequest, context: AuthzDep, uow: UowDep
) -> None:
    await ChangeMemberRole(memberships=uow.memberships, clock=SystemClock()).execute(
        context, profile_id=ProfileId(profile_id), user_id=UserId(user_id), role=body.role
    )


@router.delete("/{profile_id}/members/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
async def revoke_member(profile_id: UUID, user_id: UUID, context: AuthzDep, uow: UowDep) -> None:
    """Remove someone's access, or leave a profile yourself.

    One endpoint for both: removing yourself needs no share permission, which the
    use case handles. A viewer must not need the owner's permission to walk away
    from a relative's medical records.
    """
    await RevokeMember(memberships=uow.memberships).execute(
        context, profile_id=ProfileId(profile_id), user_id=UserId(user_id)
    )
