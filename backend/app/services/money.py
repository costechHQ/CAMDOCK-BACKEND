"""Fee arithmetic (SRS FR-FEE-004 to 006), in `Decimal` only and defined once so no
endpoint can derive a status or balance differently."""

from decimal import Decimal

PAID = "paid"
PARTIAL = "partial"
DUE = "due"

TWO_PLACES = Decimal("0.01")


def quantize(amount: Decimal) -> Decimal:
    """An amount at exactly two decimal places, so it serializes as e.g. "25000.00"."""
    return amount.quantize(TWO_PLACES)


def _check(billed: Decimal, paid: Decimal) -> None:
    if billed <= 0:
        raise ValueError("amount billed must be greater than zero")
    if paid < 0:
        raise ValueError("amount paid must not be negative")
    if paid > billed:
        raise ValueError("amount paid must not exceed the amount billed")


def balance(billed: Decimal, paid: Decimal) -> Decimal:
    """What is still owed: billed minus paid."""
    _check(billed, paid)
    return billed - paid


def status(billed: Decimal, paid: Decimal) -> str:
    """`paid` when settled, `partial` when something but not all is paid, else `due`."""
    _check(billed, paid)
    if paid == billed:
        return PAID
    if paid > 0:
        return PARTIAL
    return DUE
