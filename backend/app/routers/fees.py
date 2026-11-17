from typing import Annotated

from fastapi import APIRouter, Depends, Header, Query, Response
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
    RefundReceipt,
    RefundRequest,
    RefundResult,
    VoidRequest,
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


@router.post(
    "/{fee_id}/payments",
    status_code=201,
    responses={
        200: {
            "model": PaymentResult,
            "description": "A retry with the same Idempotency-Key: the payment that was "
            "already recorded, unchanged. Nothing new was created.",
        },
        409: {
            "description": "The Idempotency-Key was already used for a different bill "
            "or amount."
        },
    },
)
def add_payment(
    fee_id: int,
    body: PaymentCreate,
    admin: Annotated[User, Depends(require_roles("admin"))],
    db: Annotated[Session, Depends(get_db)],
    response: Response,
    idempotency_key: Annotated[
        str | None,
        Header(
            pattern=r"^[A-Za-z0-9._-]{8,64}$",
            description="Optional. A unique value (8 to 64 letters, digits, `.`, `_` "
            "or `-`) per payment. Send the same value when retrying so a payment is "
            "never recorded twice.",
        ),
    ] = None,
) -> PaymentResult:
    """Record an instalment. A payment that would exceed the amount billed is refused."""
    result, created = fee_service.record_payment(
        db, admin, fee_id, body, idempotency_key
    )
    db.commit()
    if not created:
        response.status_code = 200
    return result


@router.post(
    "/{fee_id}/payments/{payment_id}/void",
    responses={
        404: {"description": "No such payment on that bill."},
        422: {"description": "The reason is missing or not 5 to 200 characters."},
    },
)
def void_payment(
    fee_id: int,
    payment_id: int,
    body: VoidRequest,
    admin: Annotated[User, Depends(require_roles("admin"))],
    db: Annotated[Session, Depends(get_db)],
) -> PaymentResult:
    """Void a payment entered by mistake. It stays in the bill's history, marked voided
    with who, when and why, and no longer counts toward what has been paid. Repeating
    the call changes nothing and keeps the first reason."""
    result = fee_service.void_payment(db, admin, fee_id, payment_id, body.reason)
    db.commit()
    return result


@router.post(
    "/{fee_id}/payments/{payment_id}/refund",
    status_code=201,
    responses={
        200: {
            "model": RefundResult,
            "description": "A retry with the same Idempotency-Key: the refund that was "
            "already recorded, unchanged. Nothing new was created.",
        },
        404: {"description": "No such payment on that bill."},
        409: {
            "description": "The payment is voided, or the Idempotency-Key was already "
            "used for a different payment or amount."
        },
        422: {
            "description": "The amount is more than what is left of the payment, or "
            "the reason or the Idempotency-Key is missing or invalid."
        },
    },
)
def refund_payment(
    fee_id: int,
    payment_id: int,
    body: RefundRequest,
    admin: Annotated[User, Depends(require_roles("admin"))],
    db: Annotated[Session, Depends(get_db)],
    response: Response,
    idempotency_key: Annotated[
        str,
        Header(
            pattern=r"^[A-Za-z0-9._-]{8,64}$",
            description="Required. A unique value (8 to 64 letters, digits, `.`, `_` "
            "or `-`) per refund. Send the same value when retrying so money is never "
            "given back twice.",
        ),
    ],
) -> RefundResult:
    """Record money given back against a payment: any amount up to what is left of it,
    with a reason. It gets its own REF- receipt number and reduces what counts as paid.
    A voided payment cannot be refunded."""
    result, created = fee_service.refund_payment(
        db, admin, fee_id, payment_id, body, idempotency_key
    )
    db.commit()
    if not created:
        response.status_code = 200
    return result


@router.get("/{fee_id}/refunds/{refund_id}/receipt")
def get_refund_receipt(
    fee_id: int,
    refund_id: int,
    user: Annotated[User, Depends(require_roles("admin", "parent"))],
    db: Annotated[Session, Depends(get_db)],
) -> RefundReceipt:
    """Printable data for a refund; a parent only gets their own child's."""
    return fee_service.refund_receipt(db, user, fee_id, refund_id)
