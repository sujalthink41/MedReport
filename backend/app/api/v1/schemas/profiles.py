"""Request and response shapes for profiles and sharing."""

from datetime import date

from pydantic import BaseModel, EmailStr, Field, field_validator

from app.domain.models.authz import MembershipRole
from app.domain.models.enums import Relationship, Sex


class CreateProfileRequest(BaseModel):
    display_name: str = Field(min_length=1, max_length=120)

    date_of_birth: date = Field(
        description="Required: reference ranges for haemoglobin, creatinine and "
        "ferritin all depend on age."
    )
    sex: Sex = Field(
        description="Required for the same reason. 'unspecified' is supported and "
        "falls back to the widest interval rather than guessing."
    )
    relationship: Relationship

    @field_validator("date_of_birth")
    @classmethod
    def not_in_the_future(cls, value: date) -> date:
        from datetime import UTC, datetime

        if value > datetime.now(UTC).date():
            raise ValueError("date of birth cannot be in the future")
        return value


class UpdateProfileRequest(BaseModel):
    display_name: str = Field(min_length=1, max_length=120)


class ProfileResponse(BaseModel):
    id: str
    display_name: str
    date_of_birth: date
    sex: Sex
    relationship: Relationship
    role: MembershipRole | None
    permissions: list[str] = Field(
        description="What the asking user may do. The client renders exactly these "
        "actions, so the UI can never offer a button the server would refuse."
    )


class ShareProfileRequest(BaseModel):
    email: EmailStr
    role: MembershipRole = Field(
        description="owner | caregiver | viewer. A caregiver may upload and read "
        "but cannot delete the profile or change who else has access."
    )


class ChangeRoleRequest(BaseModel):
    role: MembershipRole


class MemberResponse(BaseModel):
    user_id: str
    email: str
    name: str | None
    role: MembershipRole
    is_me: bool
