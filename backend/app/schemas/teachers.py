import re
from typing import Annotated

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictInt,
    StringConstraints,
    field_validator,
)

Name = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=150)
]
RecordId = Annotated[StrictInt, Field(ge=1)]
PHONE_PATTERN = re.compile(r"[0-9+()\-\s]+")


def _no_duplicates(ids: list[int]) -> list[int]:
    if len(set(ids)) != len(ids):
        raise ValueError("Duplicate ids are not allowed")
    return ids


class TeacherCreate(BaseModel):
    """The teacher record is always linked to an existing teacher-role account."""

    model_config = ConfigDict(extra="forbid")

    user_id: RecordId
    full_name: Name
    phone: str | None = None
    class_ids: Annotated[list[RecordId], Field(max_length=50)] = []
    subject_ids: Annotated[list[RecordId], Field(max_length=50)] = []

    @field_validator("phone")
    @classmethod
    def _phone(cls, value: str | None) -> str | None:
        value = (value or "").strip()
        if not value:
            return None
        if len(value) > 30:
            raise ValueError("String should have at most 30 characters")
        if not PHONE_PATTERN.fullmatch(value) or not any(c.isdigit() for c in value):
            raise ValueError("Enter a valid phone number")
        return value

    _unique_classes = field_validator("class_ids")(_no_duplicates)
    _unique_subjects = field_validator("subject_ids")(_no_duplicates)


class NamedRef(BaseModel):
    id: int
    name: str


class TeacherOut(BaseModel):
    id: int
    user_id: int | None
    full_name: str
    phone: str | None
    is_active: bool
    classes: list[NamedRef]
    subjects: list[NamedRef]


class TeacherPage(BaseModel):
    items: list[TeacherOut]
    total: int
    limit: int
    offset: int
