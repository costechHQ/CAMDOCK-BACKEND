from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.dependencies import require_roles
from app.models import User
from app.schemas.fees import (
    FeeCreate,
    FeeOut,
    FeePage,
    FeeStatus,
    FeeType,
    PaymentCreate,
    PaymentResult,
    Receipt,
)
from app.services import fees as fee_service

router = APIRouter(prefix="/fees", tags=["fees"])


@router.get("")
def list_fees(
    user: Annotated[User, Depends(require_roles("admin", "parent"))],
    db: Annotated[Session, Depends(get_db)],
    student_id: Annotated[int | None, Query(ge=1)] = None,
    term_id: Annotated[int | None, Query(ge=1)] = None,
    class_id: Annotated[int | None, Query(ge=1)] = None,
    fee_type: FeeType | None = None,
    status: FeeStatus | None = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> FeePage:
    """Admins see every bill; parents only their linked children's. `totals` cover all
    bills matching the filters, not just this page."""
    return fee_service.list_fees(
        db,
        user,
        student_id=student_id,
        term_id=term_id,
        class_id=class_id,
        fee_type=fee_type,
        status=status,
        limit=limit,
        offset=offset,
    )


@router.get("/{fee_id}")
def get_fee(
    fee_id: int,
    user: Annotated[User, Depends(require_roles("admin", "parent"))],
    db: Annotated[Session, Depends(get_db)],
) -> FeeOut:
    """One bill with its payments; a parent only gets their own child's."""
    return fee_service.get_fee(db, user, fee_id)


@router.get("/{fee_id}/receipt")
def get_receipt(
    fee_id: int,
    user: Annotated[User, Depends(require_roles("admin", "parent"))],
    db: Annotated[Session, Depends(get_db)],
) -> Receipt:
    """Printable receipt data for a bill; a parent only gets their own child's."""
    return fee_service.bill_receipt(db, user, fee_id)


@router.get("/{fee_id}/payments/{payment_id}/receipt")
def get_payment_receipt(
    fee_id: int,
    payment_id: int,
    user: Annotated[User, Depends(require_roles("admin", "parent"))],
    db: Annotated[Session, Depends(get_db)],
) -> Receipt:
    """Receipt for one instalment, with the figures as they stood once it was paid."""
    return fee_service.payment_receipt(db, user, fee_id, payment_id)


@router.post("", status_code=201)
def create_fee(
    body: FeeCreate,
    admin: Annotated[User, Depends(require_roles("admin"))],
    db: Annotated[Session, Depends(get_db)],
) -> FeeOut:
    """Bill a student, optionally recording a first payment. Amounts are naira with at
    most two decimals; the receipt number and dates are assigned by the server."""
    fee = fee_service.create_fee(db, admin, body)
    db.commit()
    return fee


@router.post("/{fee_id}/payments", status_code=201)
def add_payment(
    fee_id: int,
    body: PaymentCreate,
    admin: Annotated[User, Depends(require_roles("admin"))],
    db: Annotated[Session, Depends(get_db)],
) -> PaymentResult:
    """Record an instalment. A payment that would exceed the amount billed is refused."""
    result = fee_service.add_payment(db, admin, fee_id, body)
    db.commit()
    return result
