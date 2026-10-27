"""Grades and averages, defined once so no endpoint can compute them differently."""

from collections.abc import Iterable
from decimal import ROUND_HALF_UP, Decimal

SCORE_MIN = Decimal(0)
SCORE_MAX = Decimal(100)

# (lowest score, grade), highest first. Anything below the last threshold is F9.
GRADE_THRESHOLDS = (
    (Decimal(75), "A1"),
    (Decimal(70), "B2"),
    (Decimal(65), "B3"),
    (Decimal(60), "C4"),
    (Decimal(55), "C5"),
    (Decimal(50), "C6"),
    (Decimal(45), "D7"),
    (Decimal(40), "E8"),
)
LOWEST_GRADE = "F9"

AVERAGE_PLACES = Decimal("0.1")


def grade_for(score: Decimal) -> str:
    """The grade for a stored score. Never pass a rounded average here."""
    if not SCORE_MIN <= score <= SCORE_MAX:
        raise ValueError("score must be between 0 and 100")
    for lowest, grade in GRADE_THRESHOLDS:
        if score >= lowest:
            return grade
    return LOWEST_GRADE


def round_average(value: Decimal) -> Decimal:
    """Round half up to one decimal place, as the prototype does."""
    return value.quantize(AVERAGE_PLACES, rounding=ROUND_HALF_UP)


def mean(values: Iterable[Decimal]) -> Decimal | None:
    """The unrounded arithmetic mean, or None when there is nothing to average.

    Missing scores are left out by the caller; they are never counted as zero.
    """
    values = list(values)
    if not values:
        return None
    return sum(values, Decimal(0)) / len(values)
