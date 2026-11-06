from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.dependencies import require_roles
from app.models import User
from app.schemas.fees import FeeCreate, FeeOut, PaymentCreate, PaymentResult
from app.services import fees as fee_service

router = APIRouter(prefix="/fees", tags=["fees"])


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
