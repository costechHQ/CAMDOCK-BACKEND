from datetime import date
from typing import Annotated, Literal

from pydantic import (
    BaseModel,
    BeforeValidator,
    ConfigDict,
    StrictInt,
    StringConstraints,
    field_validator,
)

Name = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=150)
]


def _lowercase(value: object) -> object:
    return value.strip().lower() if isinstance(value, str) else value


Gender = Annotated[Literal["male", "female"], BeforeValidator(_lowercase)]


class StudentCreate(BaseModel):
    """Only these fields are accepted: the number, status and flags are server-owned."""

    model_config = ConfigDict(extra="forbid")

    full_name: Name
    class_id: StrictInt
    gender: Gender | None = None
    guardian_display_name: str | None = None
    date_of_birth: date | None = None

    @field_validator("guardian_display_name")
    @classmethod
    def _guardian(cls, value: str | None) -> str | None:
        value = (value or "").strip()
        if len(value) > 150:
            raise ValueError("String should have at most 150 characters")
        return value or None

    @field_validator("date_of_birth")
    @classmethod
    def _not_in_the_future(cls, value: date | None) -> date | None:
        if value is not None and value > date.today():
            raise ValueError("Date of birth cannot be in the future")
        return value


class StudentOut(BaseModel):
    id: int
    student_number: str
    full_name: str
    class_id: int
    class_name: str
    gender: str | None
    date_of_birth: date | None
    guardian_display_name: str | None
    status: str
