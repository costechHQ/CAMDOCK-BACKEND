from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import delete, func, insert, select

from app.db.base import Base
from app.models import (
    AcademicYear,
    FeePayment,
    FeeRecord,
    SchoolClass,
    Student,
    Term,
    User,
)
from tests.support import assert_rejected


def add(db, model, **values) -> int:
    return db.execute(insert(model).values(**values).returning(model.id)).scalar_one()


@pytest.fixture
def ctx(db) -> dict[str, int]:
    """The rows a fee record points at."""
    year_id = add(
        db,
        AcademicYear,
        name="2026/2027",
        start_date=date(2026, 9, 1),
        end_date=date(2027, 7, 31),
    )
    class_id = add(db, SchoolClass, name="JSS 1", level="junior_secondary")
    return {
        "student_id": add(
            db, Student, student_number="CAM/001", full_name="Ngozi", class_id=class_id
        ),
        "term_id": add(db, Term, academic_year_id=year_id, name="First Term"),
        "created_by": add(
            db, User, email="a@camdock.test", password_hash="hash", role="admin"
        ),
    }


def add_record(db, ctx, **overrides) -> int:
    values = {
        **ctx,
        "fee_type": "Tuition Fee",
        "amount_billed": Decimal(50000),
        "receipt_number": "REC-0001",
        **overrides,
    }
    return add(db, FeeRecord, **values)


def add_payment(db, record_id, created_by, **overrides) -> int:
    values = {
        "fee_record_id": record_id,
        "amount": Decimal(10000),
        "receipt_number": "PAY-0001",
        "created_by": created_by,
        **overrides,
    }
    return add(db, FeePayment, **values)


def test_models_registered_on_metadata():
    assert {"fee_records", "fee_payments"} <= set(Base.metadata.tables)


def test_fee_record_has_no_paid_amount_column():
    assert "amount_paid" not in FeeRecord.__table__.columns


def test_valid_record_gets_defaults(db, ctx):
    record_id = add_record(db, ctx)
    row = db.execute(select(FeeRecord).where(FeeRecord.id == record_id)).one()
    assert row.recorded_at is not None
    assert row.updated_by is None
    assert row.created_at is not None and row.updated_at is not None


@pytest.mark.parametrize("amount", ["0.01", "50000", "9999999999.99"])
def test_positive_billed_amounts_accepted(db, ctx, amount):
    add_record(db, ctx, amount_billed=Decimal(amount))


@pytest.mark.parametrize("amount", ["0", "-1", "-0.01"])
def test_non_positive_billed_amounts_rejected(db, ctx, amount):
    assert_rejected(
        db,
        insert(FeeRecord).values(
            **ctx,
            fee_type="Tuition Fee",
            amount_billed=Decimal(amount),
            receipt_number="REC-0002",
        ),
        "ck_fee_records_amount_billed_positive",
    )


def test_amounts_are_exact_decimals(db, ctx):
    record_id = add_record(db, ctx, amount_billed=Decimal("0.10"))
    add_payment(db, record_id, ctx["created_by"], amount=Decimal("0.20"))
    total = db.execute(select(func.sum(FeePayment.amount))).scalar_one()
    assert total == Decimal("0.20")
    third = add_payment(
        db,
        record_id,
        ctx["created_by"],
        amount=Decimal("0.10"),
        receipt_number="PAY-0002",
    )
    assert third
    assert db.execute(select(func.sum(FeePayment.amount))).scalar_one() == Decimal(
        "0.30"
    )


@pytest.mark.parametrize(
    "fee_type",
    [
        "Tuition Fee",
        "Development Levy",
        "Examination Fee",
        "PTA Levy",
        "Uniform & Materials",
    ],
)
def test_allowed_fee_types_accepted(db, ctx, fee_type):
    add_record(db, ctx, fee_type=fee_type)


def test_unknown_fee_type_rejected(db, ctx):
    assert_rejected(
        db,
        insert(FeeRecord).values(
            **ctx,
            fee_type="Bus Fee",
            amount_billed=Decimal(100),
            receipt_number="REC-0002",
        ),
        "ck_fee_records_valid_fee_type",
    )


def test_record_receipt_number_unique(db, ctx):
    add_record(db, ctx)
    assert_rejected(
        db,
        insert(FeeRecord).values(
            **ctx,
            fee_type="PTA Levy",
            amount_billed=Decimal(100),
            receipt_number="REC-0001",
        ),
        "uq_fee_records_receipt_number",
    )


def test_same_student_term_and_type_may_be_billed_twice(db, ctx):
    add_record(db, ctx)
    add_record(db, ctx, receipt_number="REC-0002")


@pytest.mark.parametrize(
    ("column", "constraint"),
    [
        ("student_id", "fk_fee_records_student_id_students"),
        ("term_id", "fk_fee_records_term_id_terms"),
        ("created_by", "fk_fee_records_created_by_users"),
    ],
)
def test_record_references_must_exist(db, ctx, column, constraint):
    assert_rejected(
        db,
        insert(FeeRecord).values(
            **{**ctx, column: 999999},
            fee_type="Tuition Fee",
            amount_billed=Decimal(100),
            receipt_number="REC-0002",
        ),
        constraint,
    )


def test_updated_by_must_exist_when_set(db, ctx):
    assert_rejected(
        db,
        insert(FeeRecord).values(
            **ctx,
            fee_type="Tuition Fee",
            amount_billed=Decimal(100),
            receipt_number="REC-0002",
            updated_by=999999,
        ),
        "fk_fee_records_updated_by_users",
    )


def test_many_payments_per_record(db, ctx):
    record_id = add_record(db, ctx)
    add_payment(db, record_id, ctx["created_by"])
    add_payment(db, record_id, ctx["created_by"], receipt_number="PAY-0002")
    count = db.execute(select(func.count()).select_from(FeePayment)).scalar_one()
    assert count == 2


@pytest.mark.parametrize("amount", ["0", "-5000"])
def test_non_positive_payments_rejected(db, ctx, amount):
    record_id = add_record(db, ctx)
    assert_rejected(
        db,
        insert(FeePayment).values(
            fee_record_id=record_id,
            amount=Decimal(amount),
            receipt_number="PAY-0009",
            created_by=ctx["created_by"],
        ),
        "ck_fee_payments_amount_positive",
    )


def test_payment_receipt_number_unique(db, ctx):
    record_id = add_record(db, ctx)
    add_payment(db, record_id, ctx["created_by"])
    assert_rejected(
        db,
        insert(FeePayment).values(
            fee_record_id=record_id,
            amount=Decimal(1),
            receipt_number="PAY-0001",
            created_by=ctx["created_by"],
        ),
        "uq_fee_payments_receipt_number",
    )


def test_payment_references_must_exist(db, ctx):
    record_id = add_record(db, ctx)
    assert_rejected(
        db,
        insert(FeePayment).values(
            fee_record_id=999999,
            amount=Decimal(1),
            receipt_number="PAY-0003",
            created_by=ctx["created_by"],
        ),
        "fk_fee_payments_fee_record_id_fee_records",
    )
    assert_rejected(
        db,
        insert(FeePayment).values(
            fee_record_id=record_id,
            amount=Decimal(1),
            receipt_number="PAY-0004",
            created_by=999999,
        ),
        "fk_fee_payments_created_by_users",
    )


def test_referenced_rows_cannot_be_deleted(db, ctx):
    record_id = add_record(db, ctx)
    add_payment(db, record_id, ctx["created_by"])
    assert_rejected(
        db,
        delete(FeeRecord).where(FeeRecord.id == record_id),
        "fk_fee_payments_fee_record_id_fee_records",
    )
    assert_rejected(
        db,
        delete(Student).where(Student.id == ctx["student_id"]),
        "fk_fee_records_student_id_students",
    )
    assert_rejected(
        db,
        delete(Term).where(Term.id == ctx["term_id"]),
        "fk_fee_records_term_id_terms",
    )


def test_overpayment_is_not_prevented_by_the_database(db, ctx):
    # Known gap, by design: a CHECK cannot see the sum of other rows, so payments may
    # exceed the bill here. The Phase 6 service layer must reject overpayment.
    record_id = add_record(db, ctx, amount_billed=Decimal(100))
    add_payment(db, record_id, ctx["created_by"], amount=Decimal(80))
    add_payment(
        db,
        record_id,
        ctx["created_by"],
        amount=Decimal(80),
        receipt_number="PAY-0002",
    )
