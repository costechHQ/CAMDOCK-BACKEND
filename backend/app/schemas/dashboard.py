from decimal import Decimal

from pydantic import BaseModel


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
