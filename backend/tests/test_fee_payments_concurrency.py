"""Two simultaneous payments must not overpay a bill.

The shared `db` fixture wraps a test in one connection and one transaction, so it cannot
show a race. This test commits real rows and uses two sessions on separate connections,
then truncates every table afterwards. It only ever runs against the guarded `*_test`
database (the `migrated_engine` fixture refuses any other).
"""

import threading
import time
from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import func, insert, select, text
from sqlalchemy.orm import Session

from app.core.errors import AppError
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
from app.schemas.fees import PaymentCreate
from app.services import fees as fee_service

TABLES = ", ".join(t.name for t in Base.metadata.sorted_tables)


def wipe(engine) -> None:
    with engine.begin() as conn:
        conn.execute(text(f"TRUNCATE TABLE {TABLES} RESTART IDENTITY CASCADE"))


@pytest.fixture
def committed_bill(migrated_engine):
    """A bill of 100.00 committed to the test database; everything is wiped afterwards."""
    wipe(migrated_engine)
    with migrated_engine.begin() as conn:

        def add(model, **values) -> int:
            return conn.execute(
                insert(model).values(**values).returning(model.id)
            ).scalar_one()

        admin_id = add(User, email="a@camdock.test", password_hash="x", role="admin")
        year = add(
            AcademicYear,
            name="2026/2027",
            start_date=date(2026, 9, 1),
            end_date=date(2027, 7, 31),
        )
        class_id = add(SchoolClass, name="Primary 1", level="primary")
        student = add(Student, student_number="S-1", full_name="Ada", class_id=class_id)
        term = add(Term, academic_year_id=year, name="First Term")
        fee_id = add(
            FeeRecord,
            student_id=student,
            term_id=term,
            fee_type="Tuition Fee",
            amount_billed=Decimal("100.00"),
            receipt_number="FEE-TEST-000001",
            created_by=admin_id,
        )
    yield {"fee_id": fee_id, "admin": User(id=admin_id, role="admin")}
    wipe(migrated_engine)


def race(engine, committed, amounts, monkeypatch):
    """Run one payment per amount at the same moment, each in its own session.

    The paid-total read is slowed down, so without a row lock every payment would read
    the same total before any of them inserted, and all would pass the overpayment check.
    """
    real_paid_total = fee_service._paid_total

    def slow_paid_total(db, fee_id):
        total = real_paid_total(db, fee_id)
        time.sleep(0.4)
        return total

    monkeypatch.setattr(fee_service, "_paid_total", slow_paid_total)
    start = threading.Barrier(len(amounts))
    outcomes: list[str] = []
    guard = threading.Lock()

    def worker(amount):
        with Session(engine) as session:
            start.wait()
            try:
                fee_service.add_payment(
                    session,
                    committed["admin"],
                    committed["fee_id"],
                    PaymentCreate(amount=amount),
                )
                session.commit()
                result = "paid"
            except AppError as error:
                session.rollback()
                result = f"refused:{error.status_code}"
            with guard:
                outcomes.append(result)

    threads = [threading.Thread(target=worker, args=(a,)) for a in amounts]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)
        assert not thread.is_alive(), "a payment hung"
    return outcomes


def stored_total(engine) -> Decimal:
    with engine.connect() as conn:
        return conn.execute(
            select(func.coalesce(func.sum(FeePayment.amount), 0))
        ).scalar_one()


def test_two_simultaneous_payments_cannot_overpay(
    migrated_engine, committed_bill, monkeypatch
):
    outcomes = race(migrated_engine, committed_bill, ["60", "60"], monkeypatch)

    assert sorted(outcomes) == ["paid", "refused:422"]
    assert stored_total(migrated_engine) == Decimal("60.00")


def test_simultaneous_payments_that_fit_are_all_recorded(
    migrated_engine, committed_bill, monkeypatch
):
    outcomes = race(migrated_engine, committed_bill, ["30", "30", "40"], monkeypatch)

    assert outcomes == ["paid"] * 3
    assert stored_total(migrated_engine) == Decimal("100.00")


def test_a_third_payment_that_no_longer_fits_is_refused(
    migrated_engine, committed_bill, monkeypatch
):
    outcomes = race(migrated_engine, committed_bill, ["50", "50", "50"], monkeypatch)

    assert sorted(outcomes) == ["paid", "paid", "refused:422"]
    assert stored_total(migrated_engine) == Decimal("100.00")
