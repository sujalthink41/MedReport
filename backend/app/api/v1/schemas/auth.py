"""Request and response shapes for authentication."""

from pydantic import BaseModel, Field


class GoogleSignInRequest(BaseModel):
    id_token: str = Field(
        min_length=1,
        description="The ID token returned by Google Sign-In on the client.",
    )


class SignedInUser(BaseModel):
    id: str
    email: str
    name: str | None


class SignInResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"  # noqa: S105 - an OAuth2 literal, not a credential
    user: SignedInUser
    is_new_user: bool = Field(
        description="True on first sign-in, so the client can route to onboarding "
        "instead of an empty dashboard."
    )


class CurrentUserResponse(BaseModel):
    id: str
    email: str
    name: str | None
