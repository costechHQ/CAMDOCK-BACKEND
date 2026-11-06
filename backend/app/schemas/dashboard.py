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
