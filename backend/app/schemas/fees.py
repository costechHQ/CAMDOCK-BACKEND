from datetime import datetime
from decimal import Decimal
from typing import Annotated, Any, Literal

from pydantic import (
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    StrictInt,
    model_validator,
)

from app.models.fees import FEE_TYPES
from app.schemas.results import TermRef

MAX_AMOUNT = Decimal("9999999999.99")  # the NUMERIC(12,2) column limit
RecordId = Annotated[StrictInt, Field(ge=1)]


def _not_a_boolean(value: Any) -> Any:
    if isinstance(value, bool):
        # ValueError, not TypeError: only ValueError becomes a 422.
        raise ValueError("Input should be a valid number")  # noqa: TRY004
    return value


# A naira amount from the client: a JSON number or string, greater than zero, at most
# two decimals. NaN, infinity and booleans are refused, and nothing is rounded or
# clamped. Responses carry amounts as strings with two decimals, e.g. "25000.00"; the
# services quantize them (`money.quantize`) before building a response.
Amount = Annotated[
    Decimal,
    BeforeValidator(_not_a_boolean),
    Field(gt=0, le=MAX_AMOUNT, max_digits=12, decimal_places=2, allow_inf_nan=False),
]
# The same rule for an amount that may be zero ("nothing paid yet"), never negative.
PaidAmount = Annotated[
    Decimal,
    BeforeValidator(_not_a_boolean),
    Field(ge=0, le=MAX_AMOUNT, max_digits=12, decimal_places=2, allow_inf_nan=False),
]
FeeType = Literal[*FEE_TYPES]


class FeeCreate(BaseModel):
    """Only these fields are accepted: the receipt number, dates and status are
    server-owned."""

    model_config = ConfigDict(extra="forbid")

    student_id: RecordId
    term_id: RecordId
    fee_type: FeeType
    amount_billed: Amount
    # Omitted, null or 0 means nothing paid yet; a positive amount is a first payment.
    amount_paid: PaidAmount | None = None

    @model_validator(mode="after")
    def _paid_within_billed(self) -> "FeeCreate":
        if self.amount_paid is not None and self.amount_paid > self.amount_billed:
            raise ValueError("Amount paid must not exceed the amount billed")
        return self


class FeeStudent(BaseModel):
    id: int
    student_number: str
    full_name: str


class PaymentOut(BaseModel):
    id: int
    receipt_number: str
    amount: Decimal  # two decimal places
    paid_at: datetime


FeeStatus = Literal["paid", "partial", "due"]


class FeeSummary(BaseModel):
    """A bill and what has been paid against it. `amount_paid` is the sum of the
    payments, never stored; `balance` and `status` come from `services.money`."""

    id: int
    receipt_number: str
    student: FeeStudent
    term: TermRef
    fee_type: str
    amount_billed: Decimal  # two decimal places
    amount_paid: Decimal
    balance: Decimal
    status: FeeStatus
    recorded_at: datetime


class FeeOut(FeeSummary):
    """A bill with its instalments, oldest first."""

    payments: list[PaymentOut]


class FeeTotals(BaseModel):
    """Sums over every bill matching the filters, not just the current page."""

    billed: Decimal
    paid: Decimal
    balance: Decimal


class FeePage(BaseModel):
    items: list[FeeSummary]
    total: int
    limit: int
    offset: int
    totals: FeeTotals


class PaymentCreate(BaseModel):
    """An instalment on an existing bill. The receipt number and date are server-owned."""

    model_config = ConfigDict(extra="forbid")

    amount: Amount


class PaymentResult(BaseModel):
    payment: PaymentOut
    fee: FeeOut


class FeeTypes(BaseModel):
    items: list[str]
