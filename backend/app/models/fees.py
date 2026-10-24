from datetime import datetime
from decimal import Decimal

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Numeric, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.academic import TimestampMixin, _in_list

FEE_TYPES = (
    "Tuition Fee",
    "Development Levy",
    "Examination Fee",
    "PTA Levy",
    "Uniform & Materials",
)


class FeeRecord(TimestampMixin, Base):
    """A bill. What has been paid is the sum of its FeePayment rows, never stored here."""

    __tablename__ = "fee_records"
    __table_args__ = (
        CheckConstraint(_in_list("fee_type", FEE_TYPES), name="valid_fee_type"),
        CheckConstraint("amount_billed > 0", name="amount_billed_positive"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    student_id: Mapped[int] = mapped_column(
        ForeignKey("students.id", ondelete="RESTRICT")
    )
    term_id: Mapped[int] = mapped_column(ForeignKey("terms.id", ondelete="RESTRICT"))
    fee_type: Mapped[str] = mapped_column(String(30))
    amount_billed: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    recorded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    receipt_number: Mapped[str] = mapped_column(String(40), unique=True)
    created_by: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    updated_by: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT")
    )


class FeePayment(TimestampMixin, Base):
    __tablename__ = "fee_payments"
    __table_args__ = (CheckConstraint("amount > 0", name="amount_positive"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    fee_record_id: Mapped[int] = mapped_column(
        ForeignKey("fee_records.id", ondelete="RESTRICT")
    )
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    paid_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    receipt_number: Mapped[str] = mapped_column(String(40), unique=True)
    created_by: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
