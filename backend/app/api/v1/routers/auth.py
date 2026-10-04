"""Authentication endpoints.

Note what is absent: no try/except, no token parsing, no database access. The route
converts HTTP into a use-case call and a use-case result back into HTTP. That is all
a driving adapter should ever do.
"""

from fastapi import APIRouter, status

from app.api.deps import CurrentUser, SignInDep
from app.api.v1.schemas.auth import (
    CurrentUserResponse,
    GoogleSignInRequest,
    SignedInUser,
    SignInResponse,
)

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/google", response_model=SignInResponse, status_code=status.HTTP_200_OK)
async def sign_in_with_google(body: GoogleSignInRequest, sign_in: SignInDep) -> SignInResponse:
    """Exchange a Google ID token for one of ours.

    Login and signup are the same endpoint deliberately. A separate /register is a
    screen the user has to choose correctly, and choosing wrong is a confusing error
    for no benefit — Google already tells us whether we have seen this person.
    """
    result = await sign_in.execute(body.id_token)

    return SignInResponse(
        access_token=result.access_token,
        user=SignedInUser(
            id=str(result.user.id),
            email=result.user.email,
            name=result.user.name,
        ),
        is_new_user=result.is_new_user,
    )


@router.get("/me", response_model=CurrentUserResponse)
async def current_user(user: CurrentUser) -> CurrentUserResponse:
    """Who am I?

    Used by clients on startup to tell "my stored token still works" from "I need to
    sign in again", without having to decode the token themselves.
    """
    return CurrentUserResponse(id=str(user.id), email=user.email, name=user.name)
