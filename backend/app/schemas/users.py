from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, StrictBool


class UserCreate(BaseModel):
    """Administrators are created only through the CLI, never over HTTP."""

    model_config = ConfigDict(extra="forbid")

    email: str = Field(max_length=254)
    role: Literal["teacher", "parent"]
    password: str = Field(max_length=1024)


class UserOut(BaseModel):
    id: int
    email: str
    role: str
    is_active: bool


class UserSummary(UserOut):
    """An account as an administrator sees it in the directory. Never any credential."""

    model_config = ConfigDict(from_attributes=True)

    last_login_at: datetime | None
    created_at: datetime


class UserPage(BaseModel):
    items: list[UserSummary]
    total: int
    limit: int
    offset: int


class UserStatusUpdate(BaseModel):
    """Only the active flag can be changed here."""

    model_config = ConfigDict(extra="forbid")

    is_active: StrictBool


class ResetCodeIssued(BaseModel):
    """Shown once, to the administrator. The code cannot be retrieved afterwards."""

    code: str
    expires_at: datetime
    user: UserOut
