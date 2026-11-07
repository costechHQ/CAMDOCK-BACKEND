from decimal import Decimal
from typing import Literal

from pydantic import BaseModel

from app.schemas.results import ClassRef, TermRef


class PublicStats(BaseModel):
    """Headline counts of active records for the public page. Counts only: no names,
    ids or other personal data."""

    students: int
    teachers: int
    classes: int


class FeeAmounts(BaseModel):
    """Money over a set of bills, two decimal places. `collected` is the sum of the
    payments against those bills, whenever they were made."""

    billed: Decimal
    collected: Decimal
    outstanding: Decimal


class ClassAverageEntry(BaseModel):
    school_class: ClassRef
    overall_average: Decimal | None  # one decimal place; None when nothing is recorded


class FeesByPeriod(BaseModel):
    """`term` is for the selected term (None when there is no term); `all_time` spans
    every term. They are never mixed."""

    term: FeeAmounts | None
    all_time: FeeAmounts


class AdminOverview(BaseModel):
    role: Literal["admin"] = "admin"
    term: TermRef | None
    students: int
    teachers: int
    active_classes: int  # active classes holding at least one active student
    fees: FeesByPeriod
    class_averages: list[ClassAverageEntry]


class TeacherClassEntry(BaseModel):
    school_class: ClassRef
    students: int  # active students in the class
    overall_average: Decimal | None  # one decimal place; None when nothing is recorded


class TeacherOverview(BaseModel):
    """ "My overview": the teacher's own classes only. No fee or school-wide figures."""

    role: Literal["teacher"] = "teacher"
    title: str = "My overview"
    term: TermRef | None
    students: int  # active students across the assigned classes
    classes: list[TeacherClassEntry]


class ChildRef(BaseModel):
    id: int
    student_number: str
    full_name: str


class ChildLinks(BaseModel):
    """API paths for the frontend to follow (SRS FR-DASH-003)."""

    results: str
    fees: str


class ParentChild(BaseModel):
    student: ChildRef
    school_class: ClassRef
    student_average: Decimal | None  # one decimal place; None when nothing is recorded
    class_average: Decimal | None
    subjects_recorded: int
    fees: FeesByPeriod
    links: ChildLinks


class ParentOverview(BaseModel):
    role: Literal["parent"] = "parent"
    term: TermRef | None
    children: list[ParentChild]
