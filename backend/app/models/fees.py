from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Numeric,
    String,
    UniqueConstraint,
    func,
)
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
        # One bill per student, term and fee type, so a race cannot bill twice.
        UniqueConstraint(
            "student_id",
            "term_id",
            "fee_type",
            name="uq_fee_records_student_id_term_id_fee_type",
        ),
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
    __table_args__ = (
        CheckConstraint("amount > 0", name="amount_positive"),
        # Paid totals are summed per bill.
        Index("ix_fee_payments_fee_record_id", "fee_record_id"),
        # A retry key is unique per admin; payments without a key (NULL) never clash.
        UniqueConstraint(
            "created_by",
            "idempotency_key",
            name="uq_fee_payments_created_by_idempotency_key",
        ),
        # Lets a refund name its bill and its payment together, so they cannot disagree.
        UniqueConstraint(
            "id", "fee_record_id", name="uq_fee_payments_id_fee_record_id"
        ),
        # A void records who, when and why together, or not at all.
        CheckConstraint(
            "(voided_at IS NULL) = (voided_by IS NULL) "
            "AND (voided_at IS NULL) = (void_reason IS NULL)",
            name="void_fields_together",
        ),
        CheckConstraint(
            "void_reason IS NULL OR length(btrim(void_reason)) >= 5",
            name="void_reason_length",
        ),
    )

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
    idempotency_key: Mapped[str | None] = mapped_column(String(64))
    # Set once by a void. A voided payment stays on record but never counts.
    voided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    voided_by: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT")
    )
    void_reason: Mapped[str | None] = mapped_column(String(200))


class FeeRefund(Base):
    """Money given back against one payment. Append-only: a refund is never edited or
    deleted (a database trigger enforces it), and it is never more than what is left of
    its payment. What counts as paid is worked out in `app/services/paid.py`."""

    __tablename__ = "fee_refunds"
    __table_args__ = (
        CheckConstraint("amount > 0", name="amount_positive"),
        CheckConstraint("length(btrim(reason)) >= 5", name="reason_length"),
        ForeignKeyConstraint(
            ["fee_payment_id", "fee_record_id"],
            ["fee_payments.id", "fee_payments.fee_record_id"],
            name="fk_fee_refunds_fee_payment_id_fee_payments",
            ondelete="RESTRICT",
        ),
        UniqueConstraint(
            "created_by",
            "idempotency_key",
            name="uq_fee_refunds_created_by_idempotency_key",
        ),
        Index("ix_fee_refunds_fee_record_id", "fee_record_id"),
        Index("ix_fee_refunds_fee_payment_id", "fee_payment_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    fee_payment_id: Mapped[int] = mapped_column()
    fee_record_id: Mapped[int] = mapped_column()
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    reason: Mapped[str] = mapped_column(String(200))
    receipt_number: Mapped[str] = mapped_column(String(40), unique=True)
    refunded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    created_by: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    idempotency_key: Mapped[str | None] = mapped_column(String(64))
