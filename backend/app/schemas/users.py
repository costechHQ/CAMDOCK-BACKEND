from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


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
