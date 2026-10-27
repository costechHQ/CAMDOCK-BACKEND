from decimal import Decimal
from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Field, StrictInt, field_validator

RecordId = Annotated[StrictInt, Field(ge=1)]


class ResultWrite(BaseModel):
    """One score for a student, subject and term. Out-of-range or over-precise scores
    are rejected, never clamped or rounded."""

    model_config = ConfigDict(extra="forbid")

    student_id: RecordId
    subject_id: RecordId
    term_id: RecordId
    score: Annotated[
        Decimal,
        Field(ge=0, le=100, max_digits=5, decimal_places=2, allow_inf_nan=False),
    ]

    @field_validator("score", mode="before")
    @classmethod
    def _not_a_boolean(cls, value: Any) -> Any:
        if isinstance(value, bool):
            # ValueError, not TypeError: only ValueError becomes a 422.
            raise ValueError("Input should be a valid number")  # noqa: TRY004
        return value


class ResultRow(BaseModel):
    """One score in a class sheet listing."""

    id: int
    student_id: int
    student_number: str
    student_name: str
    subject_id: int
    subject_name: str
    term_id: int
    class_id: int
    score: Decimal
    grade: str


class ResultPage(BaseModel):
    items: list[ResultRow]
    total: int
    limit: int
    offset: int


class StudentRef(BaseModel):
    id: int
    student_number: str
    full_name: str
    class_id: int
    class_name: str


class TermRef(BaseModel):
    id: int
    name: str
    academic_year: str


class SubjectScore(BaseModel):
    result_id: int
    subject_id: int
    subject_name: str
    score: Decimal
    grade: str


class StudentResults(BaseModel):
    student: StudentRef
    term: TermRef
    # Over every recorded subject, whatever part of it the caller may see in `items`.
    average: Decimal | None  # one decimal place, e.g. "72.3"
    subjects_recorded: int
    items: list[SubjectScore]


class ClassRef(BaseModel):
    id: int
    name: str


class SubjectAverage(BaseModel):
    subject_id: int
    subject_name: str
    average: Decimal  # one decimal place
    students_scored: int


class ClassAverages(BaseModel):
    # Serialized as "class", which is a Python keyword.
    model_config = ConfigDict(populate_by_name=True)

    school_class: ClassRef = Field(serialization_alias="class")
    term: TermRef
    students_with_results: int
    subjects: list[SubjectAverage]
    overall_average: Decimal | None


class ResultOut(BaseModel):
    id: int
    student_id: int
    subject_id: int
    term_id: int
    class_id: int
    score: Decimal  # serialized as a string with two decimals, e.g. "75.50"
    grade: str
