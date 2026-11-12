from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

Email = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True, to_lower=True, min_length=1, max_length=254
    ),
]


class LoginRequest(BaseModel):
    """Only credentials are accepted: the role always comes from the database."""

    email: Email
    # The upper bound limits the work a single request can force the hasher to do.
    password: str = Field(min_length=1, max_length=1024)


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    email: str
    role: str


class LoginResponse(BaseModel):
    access_token: str
    token_type: Literal["bearer"] = "bearer"  # noqa: S105  (the scheme name)
    expires_at: datetime
    user: UserOut


class PasswordChange(BaseModel):
    """A signed-in user choosing a new password. Passwords are used exactly as sent."""

    model_config = ConfigDict(extra="forbid")

    current_password: str = Field(min_length=1, max_length=1024)
    new_password: str = Field(min_length=1, max_length=1024)


class PasswordResetRequest(BaseModel):
    """A reset code, the account's email, and the new password to set."""

    model_config = ConfigDict(extra="forbid")

    email: Email
    code: str = Field(min_length=1, max_length=64)
    new_password: str = Field(min_length=1, max_length=1024)
