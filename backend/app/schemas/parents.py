from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StrictInt, field_validator

RELATIONSHIP_MAX_LENGTH = 30  # the column width


class ParentLinkCreate(BaseModel):
    """Links an existing parent-role account to a student. The relationship is free
    text because the SRS defines no list of values."""

    model_config = ConfigDict(extra="forbid")

    parent_user_id: Annotated[StrictInt, Field(ge=1)]
    relationship: str | None = None

    @field_validator("relationship")
    @classmethod
    def _relationship(cls, value: str | None) -> str | None:
        value = (value or "").strip()
        if len(value) > RELATIONSHIP_MAX_LENGTH:
            raise ValueError(
                f"String should have at most {RELATIONSHIP_MAX_LENGTH} characters"
            )
        return value or None


class ParentLinkOut(BaseModel):
    student_id: int
    parent_user_id: int
    parent_email: str
    relationship: str | None
