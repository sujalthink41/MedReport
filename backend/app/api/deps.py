"""FastAPI dependencies — where ports get bound to concrete adapters.

This is the composition root for HTTP requests. It is the *only* place that knows
which implementation satisfies which port. Everything downstream asks for a port and
never learns what it got.

When CP10 adds a second storage adapter, this file changes and nothing else does.
"""

from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Annotated

from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.adapters.auth.google import GoogleIdentityProvider
from app.adapters.auth.jwt import JwtTokenService
from app.adapters.db.authz_repositories import (
    SqlAuditLog,
    SqlMembershipRepository,
    SqlStaffRoleRepository,
)
from app.adapters.db.session import session_scope
from app.adapters.db.uow import SessionUnitOfWork
from app.adapters.system import SystemClock, Uuid4Generator
from app.application.use_cases.sign_in import SignIn
from app.core.config import Settings, get_settings
from app.domain.errors import InvalidTokenError, NotFoundError
from app.domain.models.authz import Permission
from app.domain.models.identifiers import ProfileId
from app.domain.models.user import User
from app.domain.services.policy import AuthorizationContext, require

# auto_error=False so a missing header reaches our handler as InvalidTokenError and
# comes back in the standard error envelope. Left at the default, FastAPI raises its
# own 403 with a different body shape, and clients would need two error parsers.
_bearer = HTTPBearer(auto_error=False)


def get_session_factory(request: Request) -> async_sessionmaker[AsyncSession]:
    """The factory built once at startup and parked on ``app.state``.

    Not a module-level global: a global would be created at import time, shared by
    every test, and impossible to point at a different database. On ``app.state`` it
    belongs to one app instance, which is exactly what the app factory gives us.
    """
    factory: async_sessionmaker[AsyncSession] = request.app.state.session_factory
    return factory


async def get_session(
    factory: Annotated[async_sessionmaker[AsyncSession], Depends(get_session_factory)],
) -> AsyncIterator[AsyncSession]:
    """One session per request, committed at the end or rolled back on failure.

    The commit lives here, at the boundary — never inside a repository. One request
    is one transaction, so a failure halfway through leaves nothing half-written.
    """
    async for session in session_scope(factory):
        yield session


SessionDep = Annotated[AsyncSession, Depends(get_session)]
SettingsDep = Annotated[Settings, Depends(get_settings)]


def get_uow(session: SessionDep) -> SessionUnitOfWork:
    """A unit of work over the request's session.

    Not ``SqlUnitOfWork``: that would open a second session and a second
    transaction, so a later failure in the same request could not undo this work.
    """
    return SessionUnitOfWork(session)


UowDep = Annotated[SessionUnitOfWork, Depends(get_uow)]


def get_token_service(settings: SettingsDep) -> JwtTokenService:
    return JwtTokenService(
        secret=settings.jwt_secret,
        ttl_minutes=settings.jwt_ttl_minutes,
    )


TokenServiceDep = Annotated[JwtTokenService, Depends(get_token_service)]


def get_identity_provider(settings: SettingsDep) -> GoogleIdentityProvider:
    return GoogleIdentityProvider(client_id=settings.google_client_id)


def get_sign_in(
    uow: UowDep,
    tokens: TokenServiceDep,
    identity: Annotated[GoogleIdentityProvider, Depends(get_identity_provider)],
) -> SignIn:
    """Assemble the use case from its ports.

    Every argument here is a concrete class; the use case receives them typed as
    ports and never learns which it got. This function is the seam a test overrides
    to run the same use case against a fake Google.
    """
    return SignIn(
        identity_provider=identity,
        users=uow.users,
        tokens=tokens,
        clock=SystemClock(),
        ids=Uuid4Generator(),
    )


SignInDep = Annotated[SignIn, Depends(get_sign_in)]


async def get_current_user(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
    tokens: TokenServiceDep,
    uow: UowDep,
) -> User:
    """Resolve the caller from their bearer token.

    Note it returns a domain ``User``, not an id. Every downstream route and policy
    check (CP8) then works with a real object rather than passing a bare UUID around
    and re-fetching it.

    A valid token for a deleted account raises too: a token outliving its user must
    not keep working.
    """
    if credentials is None or not credentials.credentials:
        raise InvalidTokenError(reason="missing")

    user_id = tokens.verify(credentials.credentials)
    user = await uow.users.get(user_id)
    if user is None:
        raise NotFoundError(resource="user")
    return user


CurrentUser = Annotated[User, Depends(get_current_user)]


# ---------------------------------------------------------------------------
# Authorization
# ---------------------------------------------------------------------------


async def get_authz_context(user: CurrentUser, session: SessionDep) -> AuthorizationContext:
    """Gather the facts, once per request.

    Two queries - memberships and staff roles - and then every permission check in
    the request is a pure in-memory lookup. The alternative, querying inside each
    check, turns a page that touches five resources into five extra round trips and
    makes the policy layer untestable without a database.

    One clock reading governs the whole request, so a staff role cannot expire
    halfway through handling it.
    """
    now = SystemClock().now()
    memberships = await SqlMembershipRepository(session).roles_for_user(user.id)
    staff_roles = await SqlStaffRoleRepository(session).roles_for_user(user.id, now=now)

    return AuthorizationContext(
        user_id=user.id,
        memberships=memberships,
        staff_roles=staff_roles,
    )


AuthzDep = Annotated[AuthorizationContext, Depends(get_authz_context)]


def requires(permission: Permission) -> Callable[..., Awaitable[AuthorizationContext]]:
    """Build a dependency that enforces one global permission.

    Used as ``Depends(requires(Permission.DICTIONARY_WRITE))`` on staff routes.
    Profile-scoped checks cannot work this way - the profile id lives in the path,
    so those call ``require(...)`` inside the handler via ``require_on_profile``.
    """

    async def dependency(context: AuthzDep) -> AuthorizationContext:
        require(context, permission)
        return context

    return dependency


def require_on_profile(
    context: AuthorizationContext, permission: Permission, profile_id: ProfileId
) -> None:
    """Enforce a profile-scoped permission inside a handler or use case.

    Deliberately a plain function rather than a dependency: the profile id is only
    known after the path has been parsed, and the same check must be callable from
    the Celery worker, where no FastAPI dependency exists.
    """
    require(context, permission, profile_id=profile_id)


def get_audit_log(session: SessionDep) -> SqlAuditLog:
    return SqlAuditLog(session)


AuditDep = Annotated[SqlAuditLog, Depends(get_audit_log)]
