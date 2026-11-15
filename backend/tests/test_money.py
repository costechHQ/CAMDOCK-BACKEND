from decimal import Decimal

import pytest
from pydantic import BaseModel, TypeAdapter, ValidationError

from app.schemas.fees import MAX_AMOUNT, Amount
from app.services import money

D = Decimal
amount = TypeAdapter(Amount)


class Money(BaseModel):
    value: Amount


@pytest.mark.parametrize(
    ("billed", "paid", "status", "balance"),
    [
        ("50000", "0", "due", "50000"),
        ("50000", "0.00", "due", "50000.00"),
        ("50000", "0.01", "partial", "49999.99"),
        ("50000", "25000", "partial", "25000"),
        ("50000", "49999.99", "partial", "0.01"),
        ("50000", "50000", "paid", "0"),
        ("0.01", "0.01", "paid", "0.00"),
        ("0.30", "0.10", "partial", "0.20"),
    ],
)
def test_status_and_balance(billed, paid, status, balance):
    assert money.status(D(billed), D(paid)) == status
    assert money.balance(D(billed), D(paid)) == D(balance)


def test_balance_is_exact_not_floating_point():
    assert money.balance(D("0.30"), D("0.10")) == D("0.20")
    assert money.balance(D("0.30"), D("0.20")) == D("0.10")


@pytest.mark.parametrize(
    ("billed", "paid", "message"),
    [
        ("50000", "-0.01", "negative"),
        ("50000", "50000.01", "exceed"),
        ("0", "0", "greater than zero"),
        ("-1", "0", "greater than zero"),
    ],
)
def test_invalid_states_never_get_a_status_or_balance(billed, paid, message):
    with pytest.raises(ValueError, match=message):
        money.status(D(billed), D(paid))
    with pytest.raises(ValueError, match=message):
        money.balance(D(billed), D(paid))


def test_quantize_gives_exactly_two_places():
    assert str(money.quantize(D("25000"))) == "25000.00"
    assert str(money.quantize(D("0.1"))) == "0.10"
    assert Money(value=money.quantize(D("25000"))).model_dump_json() == (
        '{"value":"25000.00"}'
    )


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("25000.00", "25000.00"),
        ("25000", "25000"),
        (25000, "25000"),
        (72.5, "72.5"),
        ("0.01", "0.01"),
        (" 10.5 ", "10.5"),
        (str(MAX_AMOUNT), "9999999999.99"),
    ],
)
def test_valid_amounts_are_accepted_exactly(value, expected):
    assert amount.validate_python(value) == D(expected)


@pytest.mark.parametrize(
    "value",
    [
        0,
        "0",
        "0.00",
        "-1",
        "-0.01",
        "0.001",
        "10.123",
        0.001,
        "10000000000.00",
        "abc",
        "",
        "NaN",
        "Infinity",
        "-Infinity",
        None,
        True,
        False,
        [100],
        {"amount": 1},
    ],
)
def test_invalid_amounts_are_rejected_not_rounded_or_clamped(value):
    with pytest.raises(ValidationError):
        amount.validate_python(value)


def test_float_noise_is_rejected_rather_than_rounded():
    with pytest.raises(ValidationError):
        amount.validate_python(0.1 + 0.2)
