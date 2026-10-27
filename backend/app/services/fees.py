from datetime import date
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.errors import AppError
from app.models import (
    AcademicYear,
    AuditLog,
    FeePayment,
    FeeRecord,
    Student,
    Term,
    User,
)
from app.schemas.fees import FeeCreate, FeeOut, FeeStudent, PaymentOut
from app.schemas.results import TermRef
from app.services import money
from app.services.terms import require_term

BILL_UNIQUE = "uq_fee_records_student_id_term_id_fee_type"
ALREADY_BILLED = "That student has already been billed for this fee type in this term."


def next_bill_number(db: Session) -> str:
    """`FEE-<year>-<6 digits>` from a database sequence, so it is unique under
    concurrency. The sequence never resets; a rolled-back creation leaves a gap."""
    number = db.execute(select(func.nextval("fee_record_number_seq"))).scalar_one()
    return f"FEE-{date.today().year}-{number:06d}"


def next_payment_number(db: Session) -> str:
    """`RCP-<year>-<6 digits>` from its own sequence, with the same properties."""
    number = db.execute(select(func.nextval("fee_payment_number_seq"))).scalar_one()
    return f"RCP-{date.today().year}-{number:06d}"


def _is_billed(db: Session, student_id: int, term_id: int, fee_type: str) -> bool:
    return (
        db.execute(
            select(FeeRecord.id).where(
                FeeRecord.student_id == student_id,
                FeeRecord.term_id == term_id,
                FeeRecord.fee_type == fee_type,
            )
        ).first()
        is not None
    )


def load_fee(db: Session, fee_id: int) -> FeeOut:
    """One bill with its student, term and payments, as the API presents it."""
    fee, student, term, year = db.execute(
        select(FeeRecord, Student, Term, AcademicYear.name)
        .join(Student, Student.id == FeeRecord.student_id)
        .join(Term, Term.id == FeeRecord.term_id)
        .join(AcademicYear, AcademicYear.id == Term.academic_year_id)
        .where(FeeRecord.id == fee_id)
    ).one()
    payments = (
        db.execute(
            select(FeePayment)
            .where(FeePayment.fee_record_id == fee.id)
            .order_by(FeePayment.paid_at, FeePayment.id)
        )
        .scalars()
        .all()
    )
    billed = money.quantize(fee.amount_billed)
    paid = money.quantize(sum((p.amount for p in payments), Decimal(0)))
    return FeeOut(
        id=fee.id,
        receipt_number=fee.receipt_number,
        student=FeeStudent(
            id=student.id,
            student_number=student.student_number,
            full_name=student.full_name,
        ),
        term=TermRef(id=term.id, name=term.name, academic_year=year),
        fee_type=fee.fee_type,
        amount_billed=billed,
        amount_paid=paid,
        balance=money.quantize(money.balance(billed, paid)),
        status=money.status(billed, paid),
        recorded_at=fee.recorded_at,
        payments=[
            PaymentOut(
                id=p.id,
                receipt_number=p.receipt_number,
                amount=money.quantize(p.amount),
                paid_at=p.paid_at,
            )
            for p in payments
        ],
    )


def create_fee(db: Session, actor: User, data: FeeCreate) -> FeeOut:
    """Bill a student for a fee type in a term, with an optional first payment, and audit
    it. The bill, the payment and the audit rows share one transaction, which the caller
    owns."""
    student = db.execute(
        select(Student).where(
            Student.id == data.student_id, Student.is_active.is_(True)
        )
    ).scalar_one_or_none()
    if student is None:
        raise AppError("Student not found.", status_code=404, code="not_found")
    require_term(db, data.term_id)
    if _is_billed(db, student.id, data.term_id, data.fee_type):
        raise AppError(ALREADY_BILLED, status_code=409, code="conflict")

    billed = money.quantize(data.amount_billed)
    fee = FeeRecord(
        student_id=student.id,
        term_id=data.term_id,
        fee_type=data.fee_type,
        amount_billed=billed,
        receipt_number=next_bill_number(db),
        created_by=actor.id,
    )
    try:
        with db.begin_nested():
            db.add(fee)
            db.flush()
    except IntegrityError as exc:  # lost a race with another bill for the same fee
        if exc.orig.diag.constraint_name != BILL_UNIQUE:
            raise
        raise AppError(ALREADY_BILLED, status_code=409, code="conflict") from None
    db.add(
        AuditLog(
            actor_user_id=actor.id,
            action="fee.create",
            entity_type="fee_record",
            entity_id=fee.id,
            changes={
                "student_id": student.id,
                "term_id": data.term_id,
                "fee_type": data.fee_type,
                "amount_billed": str(billed),
                "receipt_number": fee.receipt_number,
            },
        )
    )

    if data.amount_paid is not None and data.amount_paid > 0:
        paid = money.quantize(data.amount_paid)
        payment = FeePayment(
            fee_record_id=fee.id,
            amount=paid,
            receipt_number=next_payment_number(db),
            created_by=actor.id,
        )
        db.add(payment)
        db.flush()
        db.add(
            AuditLog(
                actor_user_id=actor.id,
                action="fee.payment.create",
                entity_type="fee_payment",
                entity_id=payment.id,
                changes={
                    "fee_record_id": fee.id,
                    "amount": str(paid),
                    "receipt_number": payment.receipt_number,
                },
            )
        )
    db.flush()
    return load_fee(db, fee.id)
