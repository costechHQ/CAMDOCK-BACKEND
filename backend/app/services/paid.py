"""The one definition of how much has been paid on a bill.

Every figure shown anywhere (the bill, the list and its totals, the receipts, both
dashboards and the overpayment check) comes from here. What counts as paid is the live
payments (not voided) minus the refunds given back. If that rule ever changes, it
changes in this file and nowhere else.
`tests/test_paid_consistency.py` checks that every screen agrees, and fails if other code
adds up payment or refund amounts by itself.
"""

from decimal import Decimal

from sqlalchemy import and_, func, or_, select, union_all
from sqlalchemy.orm import Session

from app.models import FeePayment, FeeRefund


def paid_per_fee_subquery():
    """What has been paid per bill, and the column to use for it (0 when unpaid): live
    payments less refunds. For queries over many bills: the fee list, its totals, and
    the dashboards."""
    movements = union_all(
        select(
            FeePayment.fee_record_id.label("fee_id"),
            FeePayment.amount.label("amount"),
        ).where(FeePayment.voided_at.is_(None)),
        select(
            FeeRefund.fee_record_id.label("fee_id"),
            (-FeeRefund.amount).label("amount"),
        ),
    ).subquery()
    paid_per_fee = (
        select(movements.c.fee_id, func.sum(movements.c.amount).label("paid"))
        .group_by(movements.c.fee_id)
        .subquery()
    )
    return paid_per_fee, func.coalesce(paid_per_fee.c.paid, 0)


def paid_total(db: Session, fee_id: int, *, through: int | None = None) -> Decimal:
    """What has been paid on one bill: live payments less their refunds.

    Voided payments never count. With `through` (a payment id) it is the running total
    up to and including that payment, in the order instalments were recorded (by time,
    then id), with the refunds given on those payments: the figure an instalment's
    receipt shows. For a voided payment that total leaves the payment itself out.
    """
    conditions = [FeePayment.fee_record_id == fee_id, FeePayment.voided_at.is_(None)]
    if through is not None:
        anchor = db.execute(
            select(FeePayment.paid_at, FeePayment.id).where(
                FeePayment.id == through, FeePayment.fee_record_id == fee_id
            )
        ).one_or_none()
        if anchor is None:
            raise ValueError("That payment does not belong to this bill.")
        conditions.append(
            or_(
                FeePayment.paid_at < anchor.paid_at,
                and_(FeePayment.paid_at == anchor.paid_at, FeePayment.id <= anchor.id),
            )
        )
    counted = select(FeePayment.id).where(*conditions)
    payments = (
        select(func.coalesce(func.sum(FeePayment.amount), 0))
        .where(*conditions)
        .scalar_subquery()
    )
    refunds = (
        select(func.coalesce(func.sum(FeeRefund.amount), 0))
        .where(FeeRefund.fee_payment_id.in_(counted))
        .scalar_subquery()
    )
    return db.execute(select(payments - refunds)).scalar_one()


def refunded_on_payment(db: Session, payment_id: int) -> Decimal:
    """Everything refunded so far on one payment."""
    return db.execute(
        select(func.coalesce(func.sum(FeeRefund.amount), 0)).where(
            FeeRefund.fee_payment_id == payment_id
        )
    ).scalar_one()


def refunded_per_payment(db: Session, fee_id: int) -> dict[int, Decimal]:
    """Everything refunded so far, per payment, for the payments of one bill."""
    rows = db.execute(
        select(FeeRefund.fee_payment_id, func.sum(FeeRefund.amount))
        .where(FeeRefund.fee_record_id == fee_id)
        .group_by(FeeRefund.fee_payment_id)
    ).all()
    return {payment_id: total for payment_id, total in rows}
