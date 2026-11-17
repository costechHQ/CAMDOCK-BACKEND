from datetime import datetime
from decimal import Decimal
from typing import Annotated, Any, Literal

from pydantic import (
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    StrictInt,
    StringConstraints,
    model_validator,
)

from app.models.fees import FEE_TYPES
from app.schemas.results import SchoolInfo, TermRef

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


PaymentStatus = Literal["valid", "voided"]
# Why a payment is voided or refunded: required, trimmed, short enough to print.
Reason = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=5, max_length=200)
]


class PaymentOut(BaseModel):
    id: int
    receipt_number: str
    amount: Decimal  # two decimal places
    paid_at: datetime
    # A voided payment stays in the history but never counts. Only administrators are
    # told why: `void_reason` is null for everyone else.
    status: PaymentStatus = "valid"
    voided_at: datetime | None = None
    void_reason: str | None = None
    # The administrator who voided it; administrators only, like the reason.
    voided_by: str | None = None
    # Money given back on this payment, and what could still be refunded (0 once voided).
    refunded_amount: Decimal = Decimal("0.00")
    refundable_amount: Decimal = Decimal("0.00")


class RefundOut(BaseModel):
    id: int
    receipt_number: str  # REF-<year>-<6 digits>
    payment_id: int
    payment_receipt_number: str
    amount: Decimal  # two decimal places
    refunded_at: datetime
    # Only administrators are told why: null for everyone else.
    reason: str | None = None
    # The administrator who recorded it; administrators only.
    refunded_by: str | None = None


HistoryKind = Literal["payment", "void", "refund"]


class HistoryEntry(BaseModel):
    """One event on a bill, so a screen need not merge the payment and refund lists.
    A void is its own entry, dated when it happened. `reason` and `by` (the account
    email) are for administrators only; everyone else gets null."""

    kind: HistoryKind
    at: datetime
    receipt_number: str  # the payment's, or the refund's own REF- number
    payment_id: int
    refund_id: int | None = None
    amount: Decimal  # two decimal places
    reason: str | None = None
    by: str | None = None


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
    """A bill with its instalments and refunds, oldest first. `amount_paid` is live
    payments less refunds; `amount_refunded` is the refunds alone."""

    payments: list[PaymentOut]
    refunds: list[RefundOut] = []
    amount_refunded: Decimal = Decimal("0.00")
    # Every payment, void and refund in the order it happened.
    history: list[HistoryEntry] = []


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


class VoidRequest(BaseModel):
    """Why a payment is being voided. The reason is kept on record and printed nowhere
    except for administrators."""

    model_config = ConfigDict(extra="forbid")

    reason: Reason


class PaymentResult(BaseModel):
    payment: PaymentOut
    fee: FeeOut


class ReceiptStudent(BaseModel):
    id: int
    student_number: str
    full_name: str
    class_name: str  # the student's current class: bills do not store one


class ReceiptSignatures(BaseModel):
    """Labels only: the frontend draws blank signature and stamp boxes to print."""

    bursar_label: str = "Bursar"
    stamp_label: str = "School stamp"


class Receipt(BaseModel):
    """Printable receipt data (SRS FR-FEE-008). A bill receipt is numbered and dated
    like the bill; an instalment receipt (`payment` set) like that payment, with the
    figures as they stood once it was recorded."""

    school: SchoolInfo
    receipt_number: str
    date: datetime
    student: ReceiptStudent
    term: TermRef
    fee_type: str
    amount_billed: Decimal  # two decimal places
    amount_paid: Decimal
    balance: Decimal
    status: FeeStatus
    payment: PaymentOut | None = None
    payments: list[PaymentOut]
    refunds: list[RefundOut] = []
    signatures: ReceiptSignatures


class FeeTypes(BaseModel):
    items: list[str]


class RefundRequest(BaseModel):
    """Money to give back against a payment. The receipt number and date are server-owned."""

    model_config = ConfigDict(extra="forbid")

    amount: Amount
    reason: Reason


class RefundResult(BaseModel):
    refund: RefundOut
    payment: PaymentOut
    fee: FeeOut


class RefundReceipt(BaseModel):
    """Printable data for a refund. The bill's figures are as they stand now."""

    school: SchoolInfo
    receipt_number: str
    date: datetime
    student: ReceiptStudent
    term: TermRef
    fee_type: str
    refund: RefundOut
    payment_receipt_number: str
    amount_billed: Decimal  # two decimal places
    amount_paid: Decimal
    balance: Decimal
    status: FeeStatus
    signatures: ReceiptSignatures
