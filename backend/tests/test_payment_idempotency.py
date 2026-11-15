"""Retrying a payment with the same Idempotency-Key must never record it twice."""

# Fixtures are imported from the payment test modules; pytest finds them by name, which
# ruff reads as a redefinition when a test then asks for them.
# ruff: noqa: F811

import threading
import time
from decimal import Decimal

import pytest
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.core.errors import AppError
from app.models import AuditLog
from app.schemas.fees import PaymentCreate
from app.services import fees as fee_service
from tests.test_fee_payments import (  # noqa: F401  (fixtures)
    FEES,
    admin,
    audits,
    bill,
    payment_count,
    school,
)
from tests.test_fee_payments_concurrency import (  # noqa: F401  (fixtures)
    committed_bill,
    stored_total,
    wipe,
)
from tests.test_teachers_api import login_headers

KEY = "pay-2026-0001"


def pay(api_client, headers, fee_id, amount, key=KEY):
    sent = dict(headers)
    if key is not None:
        sent["Idempotency-Key"] = key
    return api_client.post(
        f"{FEES}/{fee_id}/payments", headers=sent, json={"amount": amount}
    )


# --- the retry ---------------------------------------------------------------------


def test_the_first_call_records_the_payment_as_before(db, api_client, admin, school):
    fee = bill(api_client, admin, school)

    response = pay(api_client, admin, fee["id"], "300")

    assert response.status_code == 201
    assert response.json()["payment"]["amount"] == "300.00"
    assert payment_count(db) == 1


def test_a_retry_returns_the_original_payment_and_records_nothing(
    db, api_client, admin, school
):
    fee = bill(api_client, admin, school)
    first = pay(api_client, admin, fee["id"], "300")

    retry = pay(api_client, admin, fee["id"], "300")

    assert first.status_code == 201
    assert retry.status_code == 200
    assert retry.json() == first.json()
    assert payment_count(db) == 1
    assert retry.json()["fee"]["amount_paid"] == "300.00"


def test_a_retry_writes_no_second_audit_row_and_uses_no_receipt_number(
    db, api_client, admin, school
):
    fee = bill(api_client, admin, school)
    first = pay(api_client, admin, fee["id"], "300")
    next_before = db.execute(
        text("SELECT last_value FROM fee_payment_number_seq")
    ).scalar()

    pay(api_client, admin, fee["id"], "300")
    pay(api_client, admin, fee["id"], "300")

    assert len(audits(db)) == 1
    assert (
        db.execute(text("SELECT last_value FROM fee_payment_number_seq")).scalar()
        == next_before
    )
    assert first.json()["payment"]["receipt_number"].startswith("RCP-")


def test_a_retry_is_still_answered_after_the_bill_is_fully_paid(
    db, api_client, admin, school
):
    fee = bill(api_client, admin, school, amount_billed="500")
    first = pay(api_client, admin, fee["id"], "500")

    retry = pay(api_client, admin, fee["id"], "500")

    assert first.status_code == 201
    assert retry.status_code == 200
    assert retry.json()["fee"]["status"] == "paid"
    assert payment_count(db) == 1


def test_a_retry_of_an_earlier_payment_is_not_confused_by_later_ones(
    db, api_client, admin, school
):
    fee = bill(api_client, admin, school)
    first = pay(api_client, admin, fee["id"], "300", key="first-key-001")
    pay(api_client, admin, fee["id"], "200", key="second-key-02")

    retry = pay(api_client, admin, fee["id"], "300", key="first-key-001")

    assert retry.status_code == 200
    assert retry.json()["payment"] == first.json()["payment"]
    assert retry.json()["fee"]["amount_paid"] == "500.00"
    assert payment_count(db) == 2


def test_the_same_amount_written_differently_is_the_same_request(
    db, api_client, admin, school
):
    fee = bill(api_client, admin, school)
    pay(api_client, admin, fee["id"], "300")

    retry = pay(api_client, admin, fee["id"], "300.00")

    assert retry.status_code == 200
    assert payment_count(db) == 1


# --- a key reused for something different ------------------------------------------


def test_the_same_key_with_another_amount_is_refused(db, api_client, admin, school):
    fee = bill(api_client, admin, school)
    pay(api_client, admin, fee["id"], "300")

    response = pay(api_client, admin, fee["id"], "301")

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "idempotency_key_reused"
    assert payment_count(db) == 1
    assert len(audits(db)) == 1


def test_the_same_key_for_another_bill_is_refused(db, api_client, admin, school):
    first_bill = bill(api_client, admin, school)
    other_bill = bill(api_client, admin, school, fee_type="PTA Levy")
    pay(api_client, admin, first_bill["id"], "300")

    response = pay(api_client, admin, other_bill["id"], "300")

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "idempotency_key_reused"
    assert payment_count(db) == 1


def test_a_refusal_does_not_leak_the_other_payment(db, api_client, admin, school):
    fee = bill(api_client, admin, school)
    first = pay(api_client, admin, fee["id"], "300")

    text_body = pay(api_client, admin, fee["id"], "301").text

    assert first.json()["payment"]["receipt_number"] not in text_body


def test_a_reused_key_does_not_hide_an_unknown_bill(api_client, admin, school):
    fee = bill(api_client, admin, school)
    pay(api_client, admin, fee["id"], "300")

    assert pay(api_client, admin, 999999, "300").status_code == 404


# --- scope, validation, compatibility ----------------------------------------------


def test_another_admin_can_use_the_same_key_text(db, api_client, admin, school):
    fee = bill(api_client, admin, school)
    other = login_headers(db, api_client, "admin", email="admin2@camdock.test")[1]
    pay(api_client, admin, fee["id"], "300")

    response = pay(api_client, other, fee["id"], "200")

    assert response.status_code == 201
    assert payment_count(db) == 2


def test_another_admins_retry_cannot_read_my_payment(db, api_client, admin, school):
    fee = bill(api_client, admin, school)
    other = login_headers(db, api_client, "admin", email="admin2@camdock.test")[1]
    mine = pay(api_client, admin, fee["id"], "300")

    theirs = pay(api_client, other, fee["id"], "300")

    assert theirs.status_code == 201
    assert theirs.json()["payment"]["id"] != mine.json()["payment"]["id"]


@pytest.mark.parametrize(
    "key", ["short", "x" * 65, "has space in it", "semi;colon-key", "tab\tkey-12345"]
)
def test_a_malformed_key_is_refused(db, api_client, admin, school, key):
    fee = bill(api_client, admin, school)

    response = pay(api_client, admin, fee["id"], "300", key=key)

    assert response.status_code in (422, 400)
    assert payment_count(db) == 0


@pytest.mark.parametrize("key", ["abcdefgh", "A.b_c-1234", "x" * 64])
def test_keys_at_the_edges_are_accepted(db, api_client, admin, school, key):
    fee = bill(api_client, admin, school)

    assert pay(api_client, admin, fee["id"], "10", key=key).status_code == 201


def test_without_a_key_every_call_records_a_payment(db, api_client, admin, school):
    fee = bill(api_client, admin, school)

    first = pay(api_client, admin, fee["id"], "100", key=None)
    second = pay(api_client, admin, fee["id"], "100", key=None)

    assert first.status_code == second.status_code == 201
    assert payment_count(db) == 2


def test_payments_without_keys_do_not_clash_with_each_other(
    db, api_client, admin, school
):
    fee = bill(api_client, admin, school)
    for _ in range(3):
        assert pay(api_client, admin, fee["id"], "10", key=None).status_code == 201


def test_a_key_does_not_bypass_the_overpayment_check(db, api_client, admin, school):
    fee = bill(api_client, admin, school, amount_billed="100")

    response = pay(api_client, admin, fee["id"], "150")

    assert response.status_code == 422
    assert payment_count(db) == 0


def test_a_refused_payment_does_not_use_up_its_key(db, api_client, admin, school):
    fee = bill(api_client, admin, school, amount_billed="100")
    pay(api_client, admin, fee["id"], "150")

    retry = pay(api_client, admin, fee["id"], "100")

    assert retry.status_code == 201
    assert payment_count(db) == 1


def test_only_an_admin_can_pay(db, api_client, admin, school):
    fee = bill(api_client, admin, school)
    parent = login_headers(db, api_client, "parent")[1]
    teacher = login_headers(db, api_client, "teacher")[1]
    pay(api_client, admin, fee["id"], "300")

    for headers in (parent, teacher):
        assert pay(api_client, headers, fee["id"], "300").status_code == 403
    assert pay(api_client, {}, fee["id"], "300").status_code == 401


def test_the_stored_key_never_appears_in_responses(api_client, admin, school):
    fee = bill(api_client, admin, school)

    response = pay(api_client, admin, fee["id"], "300")

    assert KEY not in response.text
    assert KEY not in api_client.get(f"{FEES}/{fee['id']}", headers=admin).text


def test_the_key_is_kept_off_the_audit_log(db, api_client, admin, school):
    fee = bill(api_client, admin, school)
    pay(api_client, admin, fee["id"], "300")

    logged = db.execute(select(AuditLog.changes)).scalars().all()

    assert KEY not in str(logged)


# --- two requests at the same moment (real, committed rows) ------------------------


def run_same_key(engine, committed, key, amounts, other_fee=None):
    """One request per amount at once, each in its own session, all using `key`."""
    real_paid_total = fee_service._paid_total

    def slow_paid_total(db, fee_id):
        total = real_paid_total(db, fee_id)
        time.sleep(0.3)
        return total

    fee_service._paid_total = slow_paid_total
    start = threading.Barrier(len(amounts))
    outcomes: list[tuple[str, bool | int]] = []
    guard = threading.Lock()

    def worker(amount):
        with Session(engine) as session:
            start.wait()
            try:
                _, created = fee_service.record_payment(
                    session,
                    committed["admin"],
                    committed["fee_id"],
                    PaymentCreate(amount=amount),
                    key,
                )
                session.commit()
                result = ("ok", created)
            except AppError as error:
                session.rollback()
                result = ("refused", error.status_code)
            with guard:
                outcomes.append(result)

    threads = [threading.Thread(target=worker, args=(a,)) for a in amounts]
    try:
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=30)
            assert not thread.is_alive(), "a payment hung"
    finally:
        fee_service._paid_total = real_paid_total
    return outcomes


def test_simultaneous_requests_with_one_key_record_one_payment(
    migrated_engine, committed_bill
):
    outcomes = run_same_key(migrated_engine, committed_bill, KEY, ["40", "40", "40"])

    assert sorted(outcomes) == [("ok", False), ("ok", False), ("ok", True)]
    assert stored_total(migrated_engine) == Decimal("40.00")


def test_simultaneous_requests_with_one_key_but_different_amounts(
    migrated_engine, committed_bill
):
    outcomes = run_same_key(migrated_engine, committed_bill, KEY, ["40", "50"])

    assert sorted(outcomes) == [("ok", True), ("refused", 409)]
    assert stored_total(migrated_engine) in (Decimal("40.00"), Decimal("50.00"))
