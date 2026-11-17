# ruff: noqa: F811
"""POST /fees/{id}/payments/{payment_id}/void: marking a payment as entered by mistake."""

import threading
import time
from decimal import Decimal

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.orm import Session

from app.core.errors import AppError
from app.models import AuditLog, FeePayment
from app.schemas.fees import PaymentCreate
from app.services import fees as fee_service
from tests.test_averages import admin, admin_login, school  # noqa: F401
from tests.test_dashboard_parent import parent_for
from tests.test_fee_payments_concurrency import (  # noqa: F401
    committed_bill,
    stored_total,
    wipe,
)
from tests.test_fee_receipts import bill, pay
from tests.test_teachers_api import login_headers

FEES = "/api/v1/fees"
REASON = "entered the wrong amount"


def void(api_client, headers, fee_id, payment_id, reason=REASON):
    return api_client.post(
        f"{FEES}/{fee_id}/payments/{payment_id}/void",
        headers=headers,
        json={"reason": reason},
    )


def audits(db):
    return db.execute(
        select(AuditLog.__table__).where(AuditLog.action == "fee.payment.void")
    ).all()


def payment_row(db, payment_id):
    table = FeePayment.__table__
    return db.execute(select(table).where(table.c.id == payment_id)).one()


@pytest.fixture
def two_payments(api_client, admin, school):
    """A bill of 1000 with 400 paid at creation and 600 paid after: fully paid."""
    fee = bill(api_client, admin, school, "ada", "1000", amount_paid="400")
    second = pay(api_client, admin, fee["id"], "600")
    return fee["id"], fee["payments"][0], second


# --- what a void does --------------------------------------------------------------------


def test_a_void_marks_the_payment_and_recomputes_the_bill(
    db, api_client, admin, two_payments
):
    fee_id, _first, second = two_payments

    response = void(api_client, admin, fee_id, second["id"])

    assert response.status_code == 200
    body = response.json()
    assert body["payment"]["status"] == "voided"
    assert body["payment"]["void_reason"] == REASON
    assert body["payment"]["voided_at"]
    assert body["payment"]["amount"] == "600.00"
    assert body["payment"]["receipt_number"] == second["receipt_number"]
    assert (body["fee"]["amount_paid"], body["fee"]["balance"]) == ("400.00", "600.00")
    assert body["fee"]["status"] == "partial"


def test_voiding_the_only_payment_makes_the_bill_due(db, api_client, admin, school):
    fee = bill(api_client, admin, school, "ada", "500", amount_paid="500")

    response = void(api_client, admin, fee["id"], fee["payments"][0]["id"])

    assert response.json()["fee"]["status"] == "due"
    assert response.json()["fee"]["amount_paid"] == "0.00"


def test_a_voided_payment_stays_in_the_history_in_order(
    db, api_client, admin, two_payments
):
    fee_id, first, second = two_payments
    void(api_client, admin, fee_id, first["id"])

    history = api_client.get(f"{FEES}/{fee_id}", headers=admin).json()["payments"]

    assert [(p["id"], p["status"]) for p in history] == [
        (first["id"], "voided"),
        (second["id"], "valid"),
    ]
    assert history[1]["void_reason"] is None and history[1]["voided_at"] is None


def test_it_records_who_and_when(db, api_client, admin_login, school):
    admin = admin_login[1]
    fee = bill(api_client, admin, school, "ada", "500", amount_paid="100")
    payment_id = fee["payments"][0]["id"]

    void(api_client, admin, fee["id"], payment_id)

    row = payment_row(db, payment_id)
    assert row.voided_by == admin_login[0]
    assert row.voided_at is not None
    assert row.void_reason == REASON


def test_a_new_payment_can_follow_a_void(db, api_client, admin, two_payments):
    fee_id, _first, second = two_payments
    void(api_client, admin, fee_id, second["id"])

    too_much = api_client.post(
        f"{FEES}/{fee_id}/payments", headers=admin, json={"amount": "600.01"}
    )
    exact = api_client.post(
        f"{FEES}/{fee_id}/payments", headers=admin, json={"amount": "600"}
    )

    assert too_much.status_code == 422
    assert exact.status_code == 201
    assert exact.json()["fee"]["status"] == "paid"


def test_the_void_is_audited(db, api_client, admin_login, two_payments):
    fee_id, _first, second = two_payments

    void(api_client, admin_login[1], fee_id, second["id"])

    (row,) = db.execute(
        select(
            AuditLog.actor_user_id,
            AuditLog.entity_type,
            AuditLog.entity_id,
            AuditLog.changes,
        ).where(AuditLog.action == "fee.payment.void")
    ).all()
    assert row.actor_user_id == admin_login[0]
    assert (row.entity_type, row.entity_id) == ("fee_payment", second["id"])
    assert row.changes == {
        "fee_record_id": fee_id,
        "amount": "600.00",
        "receipt_number": second["receipt_number"],
        "reason": REASON,
    }


# --- repeating a void --------------------------------------------------------------------


def test_voiding_again_changes_nothing_and_keeps_the_first_reason(
    db, api_client, admin, two_payments
):
    fee_id, _first, second = two_payments
    once = void(api_client, admin, fee_id, second["id"], "the first reason given")

    again = void(api_client, admin, fee_id, second["id"], "a different second reason")

    assert again.status_code == 200
    assert again.json()["payment"]["void_reason"] == "the first reason given"
    assert again.json()["payment"]["voided_at"] == once.json()["payment"]["voided_at"]
    assert again.json()["fee"] == once.json()["fee"]
    assert len(audits(db)) == 1


# --- the reason --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"reason": ""},
        {"reason": "    "},
        {"reason": "oops"},
        {"reason": "  ok  "},
        {"reason": "x" * 201},
        {"reason": None},
        {"reason": 12345},
        {"reason": REASON, "amount": "1"},
    ],
)
def test_the_reason_must_be_five_to_two_hundred_characters(
    db, api_client, admin, two_payments, body
):
    fee_id, _first, second = two_payments

    response = api_client.post(
        f"{FEES}/{fee_id}/payments/{second['id']}/void", headers=admin, json=body
    )

    assert response.status_code == 422
    assert payment_row(db, second["id"]).voided_at is None
    assert audits(db) == []


def test_the_reason_is_trimmed_and_the_limits_are_inclusive(
    db, api_client, admin, two_payments
):
    fee_id, first, second = two_payments

    short = void(api_client, admin, fee_id, first["id"], "  fives  ")
    longest = void(api_client, admin, fee_id, second["id"], "y" * 200)

    assert short.json()["payment"]["void_reason"] == "fives"
    assert longest.json()["payment"]["void_reason"] == "y" * 200


# --- who and what ------------------------------------------------------------------------


def test_unknown_bills_and_payments_are_one_404(
    db, api_client, admin, school, two_payments
):
    fee_id, _first, second = two_payments
    other = bill(api_client, admin, school, "bola", "300", amount_paid="100")
    other_payment = other["payments"][0]["id"]

    unknown_bill = void(api_client, admin, 999999, second["id"])
    unknown_payment = void(api_client, admin, fee_id, 999999)
    wrong_bill = void(api_client, admin, fee_id, other_payment)

    for response in (unknown_bill, unknown_payment, wrong_bill):
        assert response.status_code == 404
        assert response.json() == unknown_bill.json()
    assert payment_row(db, other_payment).voided_at is None


def test_non_numeric_ids_are_refused(api_client, admin):
    response = api_client.post(
        f"{FEES}/abc/payments/1/void", headers=admin, json={"reason": REASON}
    )

    assert response.status_code == 422


def test_only_an_administrator_may_void(db, api_client, admin, school, two_payments):
    fee_id, _first, second = two_payments
    teacher = login_headers(db, api_client, "teacher")[1]
    _, parent = parent_for(db, api_client, [school["ada"]])

    assert void(api_client, {}, fee_id, second["id"]).status_code == 401
    assert void(api_client, teacher, fee_id, second["id"]).status_code == 403
    assert void(api_client, parent, fee_id, second["id"]).status_code == 403
    assert payment_row(db, second["id"]).voided_at is None
    assert audits(db) == []


# --- receipts and the family's view ------------------------------------------------------


def test_a_voided_payments_receipt_opens_flagged_and_excludes_itself(
    db, api_client, admin, two_payments
):
    fee_id, _first, second = two_payments
    void(api_client, admin, fee_id, second["id"])

    receipt = api_client.get(
        f"{FEES}/{fee_id}/payments/{second['id']}/receipt", headers=admin
    )

    assert receipt.status_code == 200
    body = receipt.json()
    assert body["receipt_number"] == second["receipt_number"]
    assert body["payment"]["status"] == "voided"
    assert body["payment"]["void_reason"] == REASON
    assert (body["amount_paid"], body["balance"]) == ("400.00", "600.00")
    assert [p["status"] for p in body["payments"]] == ["valid", "voided"]


def test_later_receipts_leave_out_an_earlier_voided_payment(
    db, api_client, admin, school
):
    fee = bill(api_client, admin, school, "ada", "1000", amount_paid="300")
    first = fee["payments"][0]
    second = pay(api_client, admin, fee["id"], "200")
    void(api_client, admin, fee["id"], first["id"])

    receipt = api_client.get(
        f"{FEES}/{fee['id']}/payments/{second['id']}/receipt", headers=admin
    ).json()

    assert receipt["amount_paid"] == "200.00"
    assert [p["status"] for p in receipt["payments"]] == ["voided", "valid"]


def test_the_bill_receipt_lists_the_voided_payment_but_does_not_count_it(
    db, api_client, admin, two_payments
):
    fee_id, _first, second = two_payments
    void(api_client, admin, fee_id, second["id"])

    receipt = api_client.get(f"{FEES}/{fee_id}/receipt", headers=admin).json()

    assert receipt["amount_paid"] == "400.00"
    assert receipt["status"] == "partial"
    assert [p["status"] for p in receipt["payments"]] == ["valid", "voided"]


def test_a_parent_sees_that_it_was_voided_but_not_why(
    db, api_client, admin, school, two_payments
):
    fee_id, _first, second = two_payments
    _, parent = parent_for(db, api_client, [school["ada"]])
    void(api_client, admin, fee_id, second["id"])

    detail = api_client.get(f"{FEES}/{fee_id}", headers=parent).json()
    receipt = api_client.get(
        f"{FEES}/{fee_id}/payments/{second['id']}/receipt", headers=parent
    ).json()

    voided = detail["payments"][1]
    assert voided["status"] == "voided" and voided["voided_at"]
    assert voided["void_reason"] is None
    assert receipt["payment"]["void_reason"] is None
    assert receipt["payments"][1]["void_reason"] is None
    assert REASON not in str(detail) + str(receipt)
    admin_view = api_client.get(f"{FEES}/{fee_id}", headers=admin).json()
    assert admin_view["payments"][1]["void_reason"] == REASON


def test_a_retried_payment_after_a_void_shows_it_voided(db, api_client, admin, school):
    fee = bill(api_client, admin, school, "ada", "800")
    headers = {**admin, "Idempotency-Key": "void-test-key-1"}
    made = api_client.post(
        f"{FEES}/{fee['id']}/payments", headers=headers, json={"amount": "300"}
    )
    void(api_client, admin, fee["id"], made.json()["payment"]["id"])

    retry = api_client.post(
        f"{FEES}/{fee['id']}/payments", headers=headers, json={"amount": "300"}
    )

    assert retry.status_code == 200
    assert retry.json()["payment"]["status"] == "voided"
    assert retry.json()["fee"]["amount_paid"] == "0.00"
    count = db.execute(select(func.count()).select_from(FeePayment)).scalar_one()
    assert count == 1


# --- the database refuses to edit or delete money ----------------------------------------


@pytest.fixture
def payment_id(api_client, admin, school):
    fee = bill(api_client, admin, school, "ada", "900", amount_paid="300")
    return fee["payments"][0]["id"]


def refused(db, sql, **params):
    """Run a statement that must be refused; the savepoint keeps the test going."""
    with pytest.raises((IntegrityError, DBAPIError)) as error, db.begin_nested():
        db.execute(text(sql), params)
    return str(error.value)


@pytest.mark.parametrize(
    "column",
    ["amount = 1", "receipt_number = 'RCP-X'", "paid_at = now() - interval '1 day'"],
)
def test_a_payments_figures_cannot_be_edited(db, payment_id, column):
    message = refused(
        db, f"UPDATE fee_payments SET {column} WHERE id = :id", id=payment_id
    )

    assert "only the void fields" in message


def test_a_payment_cannot_be_moved_to_another_bill_or_creator(
    db, admin_login, payment_id
):
    message = refused(
        db,
        "UPDATE fee_payments SET created_by = created_by + 1 WHERE id = :id",
        id=payment_id,
    )

    assert "only the void fields" in message


def test_a_payment_cannot_be_deleted(db, payment_id):
    message = refused(db, "DELETE FROM fee_payments WHERE id = :id", id=payment_id)

    assert "cannot be deleted" in message
    assert payment_row(db, payment_id)


def test_the_void_fields_can_be_set_once_and_then_nothing_changes(
    db, admin_login, payment_id
):
    db.execute(
        text(
            "UPDATE fee_payments SET voided_at = now(), voided_by = :by, "
            "void_reason = 'mistaken entry' WHERE id = :id"
        ),
        {"by": admin_login[0], "id": payment_id},
    )

    again = refused(
        db,
        "UPDATE fee_payments SET void_reason = 'another reason' WHERE id = :id",
        id=payment_id,
    )
    cleared = refused(
        db,
        "UPDATE fee_payments SET voided_at = NULL, voided_by = NULL, void_reason = NULL "
        "WHERE id = :id",
        id=payment_id,
    )
    deleted = refused(db, "DELETE FROM fee_payments WHERE id = :id", id=payment_id)

    assert "cannot be changed" in again and "cannot be changed" in cleared
    assert "cannot be deleted" in deleted
    assert payment_row(db, payment_id).void_reason == "mistaken entry"


def test_the_void_fields_must_come_together(db, admin_login, payment_id):
    only_time = refused(
        db, "UPDATE fee_payments SET voided_at = now() WHERE id = :id", id=payment_id
    )
    no_who = refused(
        db,
        "UPDATE fee_payments SET voided_at = now(), void_reason = 'long enough' WHERE id = :id",
        id=payment_id,
    )

    no_reason = refused(
        db,
        "UPDATE fee_payments SET voided_at = now(), voided_by = :by WHERE id = :id",
        by=admin_login[0],
        id=payment_id,
    )

    assert "void_fields_together" in only_time
    assert "void_fields_together" in no_who
    assert "void_fields_together" in no_reason


def test_a_short_reason_is_refused_by_the_database_too(db, admin_login, payment_id):
    message = refused(
        db,
        "UPDATE fee_payments SET voided_at = now(), voided_by = :by, void_reason = 'no' "
        "WHERE id = :id",
        by=admin_login[0],
        id=payment_id,
    )

    assert "void_reason_length" in message


# --- two requests at the same moment (real, committed rows) ------------------------------


def test_two_simultaneous_voids_make_one_void(
    migrated_engine, committed_bill, monkeypatch
):
    with Session(migrated_engine) as session, session.begin():
        made, _ = fee_service.record_payment(
            session,
            committed_bill["admin"],
            committed_bill["fee_id"],
            PaymentCreate(amount="60"),
        )
        payment_id = made.payment.id
    start = threading.Barrier(2)
    outcomes: list[str] = []
    guard = threading.Lock()

    def worker(reason):
        with Session(migrated_engine) as session:
            start.wait()
            try:
                result = fee_service.void_payment(
                    session,
                    committed_bill["admin"],
                    committed_bill["fee_id"],
                    payment_id,
                    reason,
                )
                session.commit()
                outcome = result.payment.status
            except AppError as error:
                session.rollback()
                outcome = f"refused:{error.status_code}"
            with guard:
                outcomes.append(outcome)

    threads = [
        threading.Thread(target=worker, args=("first reason given",)),
        threading.Thread(target=worker, args=("second reason given",)),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)
        assert not thread.is_alive(), "a void hung"

    with Session(migrated_engine) as session:
        rows = session.execute(
            select(func.count())
            .select_from(AuditLog)
            .where(AuditLog.action == "fee.payment.void")
        ).scalar_one()
        stored = session.execute(
            select(FeePayment.void_reason).where(FeePayment.id == payment_id)
        ).scalar_one()
    assert outcomes == ["voided", "voided"]
    assert rows == 1
    assert stored in ("first reason given", "second reason given")


def test_a_void_and_a_new_payment_at_once_never_overpay(
    migrated_engine, committed_bill, monkeypatch
):
    actor, fee_id = committed_bill["admin"], committed_bill["fee_id"]
    with Session(migrated_engine) as session, session.begin():
        made, _ = fee_service.record_payment(
            session, actor, fee_id, PaymentCreate(amount="60")
        )
        first_id = made.payment.id
    real = fee_service.paid_total

    def slow(db, fee, **kwargs):
        total = real(db, fee, **kwargs)
        time.sleep(0.4)
        return total

    monkeypatch.setattr(fee_service, "paid_total", slow)
    start = threading.Barrier(2)
    outcomes: list[str] = []
    guard = threading.Lock()

    def voider():
        with Session(migrated_engine) as session:
            start.wait()
            fee_service.void_payment(
                session, actor, fee_id, first_id, "entered in error"
            )
            session.commit()
            with guard:
                outcomes.append("voided")

    def payer():
        with Session(migrated_engine) as session:
            start.wait()
            try:
                fee_service.add_payment(
                    session, actor, fee_id, PaymentCreate(amount="60")
                )
                session.commit()
                result = "paid"
            except AppError as error:
                session.rollback()
                result = f"refused:{error.status_code}"
            with guard:
                outcomes.append(result)

    threads = [threading.Thread(target=voider), threading.Thread(target=payer)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)
        assert not thread.is_alive(), "a request hung"

    try:
        with Session(migrated_engine) as session:
            live = session.execute(
                select(func.coalesce(func.sum(FeePayment.amount), 0)).where(
                    FeePayment.voided_at.is_(None)
                )
            ).scalar_one()
        # Either the void came first (so the new 60 fits) or the payment was refused.
        assert live in (Decimal("60.00"), Decimal("0.00"))
        assert live <= Decimal("100.00")
        assert sorted(outcomes) in (["paid", "voided"], ["refused:422", "voided"])
        assert (live == Decimal("60.00")) == ("paid" in outcomes)
    finally:
        wipe(migrated_engine)
