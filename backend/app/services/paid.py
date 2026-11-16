"""The one definition of how much has been paid on a bill.

Every figure shown anywhere (the bill, the list and its totals, the receipts, both
dashboards and the overpayment check) comes from here. If the rule about which payments
count ever changes, it changes in this file and nowhere else.
`tests/test_paid_consistency.py` checks that every screen agrees, and fails if other code
adds up payment amounts by itself.
"""

from decimal import Decimal

from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import Session

from app.models import FeePayment


def paid_per_fee_subquery():
    """The sum of payments per bill, and the column to use for it (0 when unpaid).
    For queries over many bills: the fee list, its totals, and the dashboards."""
    paid_per_fee = (
        select(
            FeePayment.fee_record_id.label("fee_id"),
            func.sum(FeePayment.amount).label("paid"),
        )
        .group_by(FeePayment.fee_record_id)
        .subquery()
    )
    return paid_per_fee, func.coalesce(paid_per_fee.c.paid, 0)


def paid_total(db: Session, fee_id: int, *, through: int | None = None) -> Decimal:
    """What has been paid on one bill.

    With `through` (a payment id) it is the running total up to and including that
    payment, in the order instalments were recorded (by time, then id): the figure an
    instalment's receipt shows.
    """
    conditions = [FeePayment.fee_record_id == fee_id]
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
    return db.execute(
        select(func.coalesce(func.sum(FeePayment.amount), 0)).where(*conditions)
    ).scalar_one()
