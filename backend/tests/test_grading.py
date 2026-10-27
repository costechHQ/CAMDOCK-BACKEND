from decimal import Decimal

import pytest

from app.services.grading import grade_for, mean, mean_from_sum, round_average

D = Decimal


@pytest.mark.parametrize(
    ("score", "grade"),
    [
        ("100", "A1"),
        ("75", "A1"),
        ("74.99", "B2"),
        ("70", "B2"),
        ("69.99", "B3"),
        ("65", "B3"),
        ("64.99", "C4"),
        ("60", "C4"),
        ("59.99", "C5"),
        ("55", "C5"),
        ("54.99", "C6"),
        ("50", "C6"),
        ("49.99", "D7"),
        ("45", "D7"),
        ("44.99", "E8"),
        ("40", "E8"),
        ("39.99", "F9"),
        ("0", "F9"),
    ],
)
def test_grade_boundaries(score, grade):
    assert grade_for(D(score)) == grade


@pytest.mark.parametrize("score", ["-0.01", "100.01", "1000"])
def test_out_of_range_scores_are_rejected(score):
    with pytest.raises(ValueError, match="between 0 and 100"):
        grade_for(D(score))


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("72.25", "72.3"),
        ("72.249", "72.2"),
        ("72.05", "72.1"),
        ("72.04", "72.0"),
        ("0", "0.0"),
        ("100", "100.0"),
    ],
)
def test_averages_round_half_up_to_one_place(value, expected):
    assert round_average(D(value)) == D(expected)
    assert str(round_average(D(value))) == expected


def test_mean_of_nothing_is_none_not_zero():
    assert mean([]) is None


def test_mean_is_exact_before_rounding():
    assert mean([D("70"), D("75.5"), D("80")]) == D("225.5") / 3
    assert round_average(mean([D("70"), D("75.5"), D("80")])) == D("75.2")


def test_mean_from_sum_matches_mean():
    values = [D("70"), D("75.5"), D("80")]

    assert mean_from_sum(sum(values, D(0)), len(values)) == mean(values)


def test_mean_from_sum_of_nothing_is_none_not_zero():
    assert mean_from_sum(D(0), 0) is None
