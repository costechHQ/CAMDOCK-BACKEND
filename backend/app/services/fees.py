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
    FeeRefund,
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
    HistoryEntry,
    PaymentCreate,
    PaymentOut,
    PaymentResult,
    Receipt,
    ReceiptSignatures,
    ReceiptStudent,
    RefundOut,
    RefundReceipt,
    RefundRequest,
    RefundResult,
)
from app.schemas.results import SchoolInfo, TermRef
from app.services import access, money
from app.services.paid import (
    paid_per_fee_subquery,
    paid_total,
    refunded_on_payment,
    refunded_per_payment,
)
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


def next_refund_number(db: Session) -> str:
    """`REF-<year>-<6 digits>` from its own sequence, with the same properties."""
    number = db.execute(select(func.nextval("fee_refund_number_seq"))).scalar_one()
    return f"REF-{date.today().year}-{number:06d}"


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
    fee = load_fee(db, visible)
    if user.role != "admin":
        # Why a payment was voided is for administrators; families see that it was.
        fee = fee.model_copy(
            update={
                "payments": [
                    p.model_copy(update={"void_reason": None, "voided_by": None})
                    for p in fee.payments
                ],
                "refunds": [
                    r.model_copy(update={"reason": None, "refunded_by": None})
                    for r in fee.refunds
                ],
                "history": [
                    h.model_copy(update={"reason": None, "by": None})
                    for h in fee.history
                ],
            }
        )
    return fee


def _without_actor(payment: PaymentOut) -> PaymentOut:
    """A printed receipt never carries the email of the administrator who acted."""
    return payment.model_copy(update={"voided_by": None})


def _refund_without_actor(refund: RefundOut) -> RefundOut:
    return refund.model_copy(update={"refunded_by": None})


def _receipt(
    db: Session,
    fee: FeeOut,
    payments: list[PaymentOut],
    paid: Decimal,
    *,
    receipt_number: str,
    issued: datetime,
    payment: PaymentOut | None,
    refunds: list[RefundOut],
) -> Receipt:
    """A receipt over `payments`, the instalments counted so far, with `paid` the
    figure `paid.paid_total` gives for them; status and balance come from `money` like
    every other fee response."""
    class_name = _class_name(db, fee.student.id)
    settings = get_settings()
    paid = money.quantize(paid)
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
        payment=_without_actor(payment) if payment else None,
        payments=[_without_actor(p) for p in payments],
        refunds=[_refund_without_actor(r) for r in refunds],
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
        fee.amount_paid,
        receipt_number=fee.receipt_number,
        issued=fee.recorded_at,
        payment=None,
        refunds=fee.refunds,
    )


def payment_receipt(db: Session, user: User, fee_id: int, payment_id: int) -> Receipt:
    """The receipt for one instalment, with the figures as they stood once it was
    recorded. A payment of another bill is a 404 like any unknown one."""
    fee = get_fee(db, user, fee_id)
    for index, payment in enumerate(fee.payments):
        if payment.id == payment_id:
            so_far = fee.payments[: index + 1]
            shown = {p.id for p in so_far}
            return _receipt(
                db,
                fee,
                so_far,
                paid_total(db, fee_id, through=payment_id),
                receipt_number=payment.receipt_number,
                issued=payment.paid_at,
                payment=payment,
                refunds=[r for r in fee.refunds if r.payment_id in shown],
            )
    raise AppError("Payment not found.", status_code=404, code="not_found")


def _class_name(db: Session, student_id: int) -> str:
    return db.execute(
        select(SchoolClass.name)
        .join(Student, Student.class_id == SchoolClass.id)
        .where(Student.id == student_id)
    ).scalar_one()


def refund_receipt(
    db: Session, user: User, fee_id: int, refund_id: int
) -> RefundReceipt:
    """The receipt for one refund, with the bill's figures as they stand now. A refund
    of another bill is a 404 like any unknown one."""
    fee = get_fee(db, user, fee_id)
    refund = next((r for r in fee.refunds if r.id == refund_id), None)
    if refund is None:
        raise AppError("Refund not found.", status_code=404, code="not_found")
    settings = get_settings()
    return RefundReceipt(
        school=SchoolInfo(name=settings.school_name, location=settings.school_location),
        receipt_number=refund.receipt_number,
        date=refund.refunded_at,
        student=ReceiptStudent(
            id=fee.student.id,
            student_number=fee.student.student_number,
            full_name=fee.student.full_name,
            class_name=_class_name(db, fee.student.id),
        ),
        term=fee.term,
        fee_type=fee.fee_type,
        refund=_refund_without_actor(refund),
        payment_receipt_number=refund.payment_receipt_number,
        amount_billed=fee.amount_billed,
        amount_paid=fee.amount_paid,
        balance=fee.balance,
        status=fee.status,
        signatures=ReceiptSignatures(),
    )


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
    paid_per_fee, paid = paid_per_fee_subquery()

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
    paid = paid_total(db, fee.id)
    refunded = refunded_per_payment(db, fee.id)
    refund_rows = db.execute(
        select(FeeRefund, FeePayment.receipt_number)
        .join(FeePayment, FeePayment.id == FeeRefund.fee_payment_id)
        .where(FeeRefund.fee_record_id == fee.id)
        .order_by(FeeRefund.refunded_at, FeeRefund.id)
    ).all()
    user_ids = {p.created_by for p in payments} | {
        p.voided_by for p in payments if p.voided_by is not None
    }
    user_ids |= {r.created_by for r, _ in refund_rows}
    emails = dict(
        db.execute(select(User.id, User.email).where(User.id.in_(user_ids))).all()
    )

    def payment_out(p: FeePayment) -> PaymentOut:
        given_back = money.quantize(refunded.get(p.id, Decimal(0)))
        voided = p.voided_at is not None
        return PaymentOut(
            id=p.id,
            receipt_number=p.receipt_number,
            amount=money.quantize(p.amount),
            paid_at=p.paid_at,
            status="voided" if voided else "valid",
            voided_at=p.voided_at,
            void_reason=p.void_reason,
            voided_by=emails.get(p.voided_by),
            refunded_amount=given_back,
            refundable_amount=Decimal("0.00")
            if voided
            else money.quantize(p.amount) - given_back,
        )

    # Same-instant events keep the order a person would expect: payment, void, refund.
    events: list[tuple[datetime, int, int, HistoryEntry]] = []
    for p in payments:
        amount = money.quantize(p.amount)
        events.append(
            (
                p.paid_at,
                0,
                p.id,
                HistoryEntry(
                    kind="payment",
                    at=p.paid_at,
                    receipt_number=p.receipt_number,
                    payment_id=p.id,
                    amount=amount,
                    by=emails.get(p.created_by),
                ),
            )
        )
        if p.voided_at is not None:
            events.append(
                (
                    p.voided_at,
                    1,
                    p.id,
                    HistoryEntry(
                        kind="void",
                        at=p.voided_at,
                        receipt_number=p.receipt_number,
                        payment_id=p.id,
                        amount=amount,
                        reason=p.void_reason,
                        by=emails.get(p.voided_by),
                    ),
                )
            )
    for r, _ in refund_rows:
        events.append(
            (
                r.refunded_at,
                2,
                r.id,
                HistoryEntry(
                    kind="refund",
                    at=r.refunded_at,
                    receipt_number=r.receipt_number,
                    payment_id=r.fee_payment_id,
                    refund_id=r.id,
                    amount=money.quantize(r.amount),
                    reason=r.reason,
                    by=emails.get(r.created_by),
                ),
            )
        )
    events.sort(key=lambda e: e[:3])

    return FeeOut(
        **_summary_fields(fee, student, term, year, paid),
        history=[e[3] for e in events],
        payments=[payment_out(p) for p in payments],
        refunds=[
            RefundOut(
                id=r.id,
                receipt_number=r.receipt_number,
                payment_id=r.fee_payment_id,
                payment_receipt_number=payment_receipt_number,
                amount=money.quantize(r.amount),
                refunded_at=r.refunded_at,
                reason=r.reason,
                refunded_by=emails.get(r.created_by),
            )
            for r, payment_receipt_number in refund_rows
        ],
        amount_refunded=money.quantize(sum(refunded.values(), Decimal(0))),
    )


def add_payment(
    db: Session, actor: User, fee_id: int, data: PaymentCreate
) -> PaymentResult:
    """Record an instalment against a bill (see `record_payment`)."""
    return record_payment(db, actor, fee_id, data)[0]


def record_payment(
    db: Session,
    actor: User,
    fee_id: int,
    data: PaymentCreate,
    idempotency_key: str | None = None,
) -> tuple[PaymentResult, bool]:
    """Record an instalment against a bill and audit it; overpayment is refused.

    The bill row is locked before the paid total is read, so two simultaneous payments
    queue: the second sees the first's total and cannot push the sum past the amount
    billed. The caller owns the transaction (the lock lasts until it ends).

    With an `idempotency_key`, a retry of the same request (same admin, bill and amount)
    returns the payment already recorded and `False`, creating and auditing nothing. The
    same key with a different bill or amount is a 409. Returns `(result, created)`.
    """
    fee = db.execute(
        select(FeeRecord).where(FeeRecord.id == fee_id).with_for_update()
    ).scalar_one_or_none()
    if fee is None:
        raise AppError("Fee record not found.", status_code=404, code="not_found")

    amount = money.quantize(data.amount)
    if idempotency_key is not None:
        earlier = db.execute(
            select(FeePayment).where(
                FeePayment.created_by == actor.id,
                FeePayment.idempotency_key == idempotency_key,
            )
        ).scalar_one_or_none()
        if earlier is not None:
            return _replay(db, earlier, fee.id, amount), False
    outstanding = money.quantize(fee.amount_billed) - money.quantize(
        paid_total(db, fee.id)
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
        idempotency_key=idempotency_key,
    )
    try:
        # A savepoint, so a clash on the key (the same key racing on another bill) can
        # be answered without losing the caller's transaction.
        with db.begin_nested():
            db.add(payment)
            db.flush()
    except IntegrityError as error:
        if idempotency_key is None:
            raise
        raise _key_reused() from error
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
    return (
        PaymentResult(
            payment=next(p for p in loaded.payments if p.id == payment.id), fee=loaded
        ),
        True,
    )


def _replay(
    db: Session, earlier: FeePayment, fee_id: int, amount: Decimal
) -> PaymentResult:
    """The payment a retried request already created, or a 409 if the retry differs."""
    if earlier.fee_record_id != fee_id or money.quantize(earlier.amount) != amount:
        raise _key_reused()
    loaded = load_fee(db, fee_id)
    return PaymentResult(
        payment=next(p for p in loaded.payments if p.id == earlier.id), fee=loaded
    )


def _key_reused(what: str = "payment") -> AppError:
    return AppError(
        f"This Idempotency-Key was already used for a different {what}. "
        f"Use a new key for a new {what}.",
        status_code=409,
        code="idempotency_key_reused",
    )


def void_payment(
    db: Session, actor: User, fee_id: int, payment_id: int, reason: str
) -> PaymentResult:
    """Mark a payment as entered by mistake: it stays on record but never counts, and
    who, when and why are kept. Voiding a voided payment changes nothing and keeps the
    first reason. The bill row is locked as for any payment, so a void and a new payment
    at the same moment cannot give a wrong total. The caller owns the transaction.
    """
    fee = db.execute(
        select(FeeRecord)
        .where(FeeRecord.id == fee_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    ).scalar_one_or_none()
    payment = (
        None
        if fee is None
        else db.execute(
            select(FeePayment)
            .where(FeePayment.id == payment_id, FeePayment.fee_record_id == fee.id)
            .with_for_update()
            .execution_options(populate_existing=True)
        ).scalar_one_or_none()
    )
    if fee is None or payment is None:
        raise AppError("Payment not found.", status_code=404, code="not_found")

    if payment.voided_at is None:
        if refunded_on_payment(db, payment.id) > 0:
            raise AppError(
                "A payment that has been refunded cannot be voided.",
                status_code=409,
                code="conflict",
            )
        payment.voided_at = func.now()  # the database clock, like payments and refunds
        payment.voided_by = actor.id
        payment.void_reason = reason
        db.add(
            AuditLog(
                actor_user_id=actor.id,
                action="fee.payment.void",
                entity_type="fee_payment",
                entity_id=payment.id,
                changes={
                    "fee_record_id": fee.id,
                    "amount": str(money.quantize(payment.amount)),
                    "receipt_number": payment.receipt_number,
                    "reason": reason,
                },
            )
        )
        db.flush()
    loaded = load_fee(db, fee.id)
    return PaymentResult(
        payment=next(p for p in loaded.payments if p.id == payment.id), fee=loaded
    )


def refund_payment(
    db: Session,
    actor: User,
    fee_id: int,
    payment_id: int,
    data: RefundRequest,
    idempotency_key: str,
) -> tuple[RefundResult, bool]:
    """Record money given back against a payment; returns `(result, created)`.

    A refund is any amount up to what is left of its payment, with a reason, its own
    number and an audit row. A voided payment cannot be refunded. The bill row is locked
    first, as for payments and voids, so two refunds at once cannot together exceed the
    payment. Repeating a request with the same `idempotency_key` (same administrator,
    payment and amount) returns the refund already recorded and `False`; the same key
    for anything else is a 409. The caller owns the transaction.
    """
    fee = db.execute(
        select(FeeRecord)
        .where(FeeRecord.id == fee_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    ).scalar_one_or_none()
    payment = (
        None
        if fee is None
        else db.execute(
            select(FeePayment)
            .where(FeePayment.id == payment_id, FeePayment.fee_record_id == fee.id)
            .with_for_update()
            .execution_options(populate_existing=True)
        ).scalar_one_or_none()
    )
    if fee is None or payment is None:
        raise AppError("Payment not found.", status_code=404, code="not_found")

    amount = money.quantize(data.amount)
    earlier = db.execute(
        select(FeeRefund).where(
            FeeRefund.created_by == actor.id,
            FeeRefund.idempotency_key == idempotency_key,
        )
    ).scalar_one_or_none()
    if earlier is not None:
        if (
            earlier.fee_payment_id != payment.id
            or money.quantize(earlier.amount) != amount
        ):
            raise _key_reused("refund")
        return _refund_result(db, fee.id, earlier.id), False

    if payment.voided_at is not None:
        raise AppError(
            "A voided payment cannot be refunded.", status_code=409, code="conflict"
        )
    already = money.quantize(refunded_on_payment(db, payment.id))
    refundable = money.quantize(payment.amount) - already
    if refundable <= 0:
        raise _payment_error("This payment has already been refunded in full.")
    if amount > refundable:
        raise _payment_error(
            f"Refund of {amount} exceeds the refundable amount of {refundable}."
        )

    refund = FeeRefund(
        fee_payment_id=payment.id,
        fee_record_id=fee.id,
        amount=amount,
        reason=data.reason,
        receipt_number=next_refund_number(db),
        created_by=actor.id,
        idempotency_key=idempotency_key,
    )
    try:
        # A savepoint, so a clash on the key can be answered without losing the
        # caller's transaction.
        with db.begin_nested():
            db.add(refund)
            db.flush()
    except IntegrityError as error:
        # Only a clash on the key means "already used". Anything else is the database
        # refusing something the checks above should have caught: let it surface.
        if "uq_fee_refunds_created_by_idempotency_key" not in str(error.orig):
            raise
        raise _key_reused("refund") from error
    db.add(
        AuditLog(
            actor_user_id=actor.id,
            action="fee.payment.refund",
            entity_type="fee_refund",
            entity_id=refund.id,
            changes={
                "fee_record_id": fee.id,
                "fee_payment_id": payment.id,
                "payment_receipt_number": payment.receipt_number,
                "amount": str(amount),
                "receipt_number": refund.receipt_number,
                "reason": data.reason,
            },
        )
    )
    db.flush()
    return _refund_result(db, fee.id, refund.id), True


def _refund_result(db: Session, fee_id: int, refund_id: int) -> RefundResult:
    loaded = load_fee(db, fee_id)
    refund = next(r for r in loaded.refunds if r.id == refund_id)
    payment = next(p for p in loaded.payments if p.id == refund.payment_id)
    return RefundResult(refund=refund, payment=payment, fee=loaded)


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
