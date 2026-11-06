from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import and_, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.errors import AppError
from app.models import (
    AcademicYear,
    AuditLog,
    FeePayment,
    FeeRecord,
    SchoolClass,
    Student,
    Term,
    User,
)
from app.schemas.fees import (
    FeeCreate,
    FeeOut,
    FeePage,
    FeeStudent,
    FeeSummary,
    FeeTotals,
    PaymentCreate,
    PaymentOut,
    PaymentResult,
    Receipt,
    ReceiptSignatures,
    ReceiptStudent,
)
from app.schemas.results import SchoolInfo, TermRef
from app.services import access, money
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


def _summary_fields(
    fee: FeeRecord, student: Student, term: Term, year: str, paid: Decimal
) -> dict:
    """The fields every fee response shares; status and balance only from `money`."""
    billed = money.quantize(fee.amount_billed)
    paid = money.quantize(paid)
    return {
        "id": fee.id,
        "receipt_number": fee.receipt_number,
        "student": FeeStudent(
            id=student.id,
            student_number=student.student_number,
            full_name=student.full_name,
        ),
        "term": TermRef(id=term.id, name=term.name, academic_year=year),
        "fee_type": fee.fee_type,
        "amount_billed": billed,
        "amount_paid": paid,
        "balance": money.quantize(money.balance(billed, paid)),
        "status": money.status(billed, paid),
        "recorded_at": fee.recorded_at,
    }


def get_fee(db: Session, user: User, fee_id: int) -> FeeOut:
    """One bill the user may see; a missing bill and another family's bill give the
    same 404."""
    visible = db.execute(
        select(FeeRecord.id)
        .join(Student, Student.id == FeeRecord.student_id)
        .where(FeeRecord.id == fee_id, access.student_scope(user))
    ).scalar_one_or_none()
    if visible is None:
        raise AppError("Fee record not found.", status_code=404, code="not_found")
    return load_fee(db, visible)


def _receipt(
    db: Session,
    fee: FeeOut,
    payments: list[PaymentOut],
    *,
    receipt_number: str,
    issued: datetime,
    payment: PaymentOut | None,
) -> Receipt:
    """A receipt over `payments`, the instalments counted so far; status and balance
    come from `money` like every other fee response."""
    class_name = db.execute(
        select(SchoolClass.name)
        .join(Student, Student.class_id == SchoolClass.id)
        .where(Student.id == fee.student.id)
    ).scalar_one()
    settings = get_settings()
    paid = money.quantize(sum((p.amount for p in payments), Decimal(0)))
    return Receipt(
        school=SchoolInfo(name=settings.school_name, location=settings.school_location),
        receipt_number=receipt_number,
        date=issued,
        student=ReceiptStudent(
            id=fee.student.id,
            student_number=fee.student.student_number,
            full_name=fee.student.full_name,
            class_name=class_name,
        ),
        term=fee.term,
        fee_type=fee.fee_type,
        amount_billed=fee.amount_billed,
        amount_paid=paid,
        balance=money.quantize(money.balance(fee.amount_billed, paid)),
        status=money.status(fee.amount_billed, paid),
        payment=payment,
        payments=payments,
        signatures=ReceiptSignatures(),
    )


def bill_receipt(db: Session, user: User, fee_id: int) -> Receipt:
    """The receipt for a bill and every instalment so far. Bills the user may not see
    give the same 404 as a missing one."""
    fee = get_fee(db, user, fee_id)
    return _receipt(
        db,
        fee,
        fee.payments,
        receipt_number=fee.receipt_number,
        issued=fee.recorded_at,
        payment=None,
    )


def payment_receipt(db: Session, user: User, fee_id: int, payment_id: int) -> Receipt:
    """The receipt for one instalment, with the figures as they stood once it was
    recorded. A payment of another bill is a 404 like any unknown one."""
    fee = get_fee(db, user, fee_id)
    for index, payment in enumerate(fee.payments):
        if payment.id == payment_id:
            return _receipt(
                db,
                fee,
                fee.payments[: index + 1],
                receipt_number=payment.receipt_number,
                issued=payment.paid_at,
                payment=payment,
            )
    raise AppError("Payment not found.", status_code=404, code="not_found")


def list_fees(
    db: Session,
    user: User,
    *,
    student_id: int | None,
    term_id: int | None,
    class_id: int | None,
    fee_type: str | None,
    status: str | None,
    limit: int,
    offset: int,
) -> FeePage:
    """Bills the user may see, scoped in SQL, newest first. Bills of deactivated
    students stay visible. `totals` cover the whole filtered set."""
    paid_per_fee = (
        select(
            FeePayment.fee_record_id.label("fee_id"),
            func.sum(FeePayment.amount).label("paid"),
        )
        .group_by(FeePayment.fee_record_id)
        .subquery()
    )
    paid = func.coalesce(paid_per_fee.c.paid, 0)

    conditions = [access.student_scope(user)]
    if student_id is not None:
        conditions.append(FeeRecord.student_id == student_id)
    if term_id is not None:
        conditions.append(FeeRecord.term_id == term_id)
    if class_id is not None:
        conditions.append(Student.class_id == class_id)
    if fee_type is not None:
        conditions.append(FeeRecord.fee_type == fee_type)
    if status is not None:
        # The same three rules as `money.status`, as SQL so the filter can page.
        conditions.append(
            {
                money.PAID: paid >= FeeRecord.amount_billed,
                money.DUE: paid == 0,
                money.PARTIAL: and_(paid > 0, paid < FeeRecord.amount_billed),
            }[status]
        )

    def joined(statement):
        return (
            statement.select_from(FeeRecord)
            .join(Student, Student.id == FeeRecord.student_id)
            .join(Term, Term.id == FeeRecord.term_id)
            .join(AcademicYear, AcademicYear.id == Term.academic_year_id)
            .outerjoin(paid_per_fee, paid_per_fee.c.fee_id == FeeRecord.id)
            .where(*conditions)
        )

    total, billed_sum, paid_sum = db.execute(
        joined(
            select(
                func.count(),
                func.coalesce(func.sum(FeeRecord.amount_billed), 0),
                func.coalesce(func.sum(paid), 0),
            )
        )
    ).one()
    rows = db.execute(
        joined(select(FeeRecord, Student, Term, AcademicYear.name, paid))
        .order_by(FeeRecord.recorded_at.desc(), FeeRecord.id.desc())
        .limit(limit)
        .offset(offset)
    ).all()
    billed_sum, paid_sum = money.quantize(billed_sum), money.quantize(paid_sum)
    return FeePage(
        items=[
            FeeSummary(**_summary_fields(fee, student, term, year, fee_paid))
            for fee, student, term, year, fee_paid in rows
        ],
        total=total,
        limit=limit,
        offset=offset,
        totals=FeeTotals(
            billed=billed_sum, paid=paid_sum, balance=billed_sum - paid_sum
        ),
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
    paid = sum((p.amount for p in payments), Decimal(0))
    return FeeOut(
        **_summary_fields(fee, student, term, year, paid),
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


def _paid_total(db: Session, fee_id: int) -> Decimal:
    return db.execute(
        select(func.coalesce(func.sum(FeePayment.amount), 0)).where(
            FeePayment.fee_record_id == fee_id
        )
    ).scalar_one()


def add_payment(
    db: Session, actor: User, fee_id: int, data: PaymentCreate
) -> PaymentResult:
    """Record an instalment against a bill and audit it; overpayment is refused.

    The bill row is locked before the paid total is read, so two simultaneous payments
    queue: the second sees the first's total and cannot push the sum past the amount
    billed. The caller owns the transaction (the lock lasts until it ends).
    """
    fee = db.execute(
        select(FeeRecord).where(FeeRecord.id == fee_id).with_for_update()
    ).scalar_one_or_none()
    if fee is None:
        raise AppError("Fee record not found.", status_code=404, code="not_found")

    amount = money.quantize(data.amount)
    outstanding = money.quantize(fee.amount_billed) - money.quantize(
        _paid_total(db, fee.id)
    )
    if outstanding <= 0:
        raise _payment_error("This fee has already been paid in full.")
    if amount > outstanding:
        raise _payment_error(
            f"Payment of {amount} exceeds the outstanding balance of {outstanding}."
        )

    payment = FeePayment(
        fee_record_id=fee.id,
        amount=amount,
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
                "amount": str(amount),
                "receipt_number": payment.receipt_number,
            },
        )
    )
    db.flush()
    loaded = load_fee(db, fee.id)
    return PaymentResult(
        payment=next(p for p in loaded.payments if p.id == payment.id), fee=loaded
    )


def _payment_error(message: str) -> AppError:
    return AppError(message, status_code=422, code="validation_error")


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
