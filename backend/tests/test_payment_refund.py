# ruff: noqa: F811
"""POST /fees/{id}/payments/{payment_id}/refund: money given back against a payment."""

import re
import secrets
import threading
import time
from decimal import Decimal

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.orm import Session

from app.core.errors import AppError
from app.models import AuditLog, FeeRefund
from app.schemas.fees import PaymentCreate, RefundRequest
from app.services import fees as fee_service
from tests.test_averages import admin, admin_login, school  # noqa: F401
from tests.test_dashboard_parent import parent_for
from tests.test_fee_payments_concurrency import (  # noqa: F401
    committed_bill,
    stored_total,
    wipe,
)
from tests.test_fee_receipts import bill, pay
from tests.test_payment_void import void
from tests.test_teachers_api import login_headers

FEES = "/api/v1/fees"
REASON = "parent withdrew the child"
REFUND_NUMBER = re.compile(r"REF-\d{4}-\d{6}")


def refund(api_client, headers, fee_id, payment_id, amount, reason=REASON, key=None):
    sent = dict(headers)
    sent["Idempotency-Key"] = key or f"refund-{secrets.token_hex(6)}"
    return api_client.post(
        f"{FEES}/{fee_id}/payments/{payment_id}/refund",
        headers=sent,
        json={"amount": amount, "reason": reason},
    )


def audits(db):
    return db.execute(
        select(AuditLog.__table__).where(AuditLog.action == "fee.payment.refund")
    ).all()


def refund_rows(db):
    table = FeeRefund.__table__
    return db.execute(select(table).order_by(table.c.id)).all()


@pytest.fixture
def paid_in_full(api_client, admin, school):
    """A bill of 1000, paid in full by one payment. Returns (fee_id, payment)."""
    fee = bill(api_client, admin, school, "ada", "1000", amount_paid="1000")
    return fee["id"], fee["payments"][0]


# --- what a refund does -------------------------------------------------------------------


def test_a_refund_records_and_recomputes_the_bill(db, api_client, admin, paid_in_full):
    fee_id, payment = paid_in_full

    response = refund(api_client, admin, fee_id, payment["id"], "300")

    assert response.status_code == 201
    body = response.json()
    assert REFUND_NUMBER.fullmatch(body["refund"]["receipt_number"])
    assert body["refund"]["amount"] == "300.00"
    assert body["refund"]["reason"] == REASON
    assert body["refund"]["payment_id"] == payment["id"]
    assert body["refund"]["payment_receipt_number"] == payment["receipt_number"]
    assert (
        body["payment"]["refunded_amount"],
        body["payment"]["refundable_amount"],
    ) == (
        "300.00",
        "700.00",
    )
    fee = body["fee"]
    assert (fee["amount_paid"], fee["balance"], fee["status"]) == (
        "700.00",
        "300.00",
        "partial",
    )
    assert fee["amount_refunded"] == "300.00"
    assert [r["id"] for r in fee["refunds"]] == [body["refund"]["id"]]


def test_refunding_everything_makes_the_bill_due_again(
    db, api_client, admin, paid_in_full
):
    fee_id, payment = paid_in_full

    response = refund(api_client, admin, fee_id, payment["id"], "1000")

    assert response.json()["fee"]["status"] == "due"
    assert response.json()["fee"]["amount_paid"] == "0.00"
    assert response.json()["payment"]["refundable_amount"] == "0.00"


def test_partial_refunds_add_up_and_stop_at_the_payment(
    db, api_client, admin, paid_in_full
):
    fee_id, payment = paid_in_full
    refund(api_client, admin, fee_id, payment["id"], "400.25")
    refund(api_client, admin, fee_id, payment["id"], "300")

    over = refund(api_client, admin, fee_id, payment["id"], "299.76")
    exact = refund(api_client, admin, fee_id, payment["id"], "299.75")
    more = refund(api_client, admin, fee_id, payment["id"], "0.01")

    assert over.status_code == 422
    assert "exceeds the refundable amount of 299.75" in over.json()["error"]["message"]
    assert exact.status_code == 201
    assert exact.json()["fee"]["amount_paid"] == "0.00"
    assert more.status_code == 422
    assert "refunded in full" in more.json()["error"]["message"]
    assert [r["amount"] for r in exact.json()["fee"]["refunds"]] == [
        "400.25",
        "300.00",
        "299.75",
    ]


def test_each_refund_has_its_own_number(db, api_client, admin, paid_in_full):
    fee_id, payment = paid_in_full

    numbers = {
        refund(api_client, admin, fee_id, payment["id"], "10").json()["refund"][
            "receipt_number"
        ]
        for _ in range(4)
    }

    assert len(numbers) == 4
    assert all(REFUND_NUMBER.fullmatch(n) for n in numbers)
    assert payment["receipt_number"] not in numbers


def test_a_bill_can_be_paid_again_after_a_refund(db, api_client, admin, paid_in_full):
    fee_id, payment = paid_in_full
    refund(api_client, admin, fee_id, payment["id"], "250")

    too_much = api_client.post(
        f"{FEES}/{fee_id}/payments", headers=admin, json={"amount": "250.01"}
    )
    exact = api_client.post(
        f"{FEES}/{fee_id}/payments", headers=admin, json={"amount": "250"}
    )

    assert too_much.status_code == 422
    assert exact.status_code == 201
    assert exact.json()["fee"]["status"] == "paid"


def test_the_refund_is_audited(db, api_client, admin_login, paid_in_full):
    fee_id, payment = paid_in_full

    made = refund(api_client, admin_login[1], fee_id, payment["id"], "120").json()

    (row,) = db.execute(
        select(
            AuditLog.actor_user_id,
            AuditLog.entity_type,
            AuditLog.entity_id,
            AuditLog.changes,
        ).where(AuditLog.action == "fee.payment.refund")
    ).all()
    assert row.actor_user_id == admin_login[0]
    assert (row.entity_type, row.entity_id) == ("fee_refund", made["refund"]["id"])
    assert row.changes == {
        "fee_record_id": fee_id,
        "fee_payment_id": payment["id"],
        "payment_receipt_number": payment["receipt_number"],
        "amount": "120.00",
        "receipt_number": made["refund"]["receipt_number"],
        "reason": REASON,
    }


# --- what a refund needs ------------------------------------------------------------------


@pytest.mark.parametrize("amount", ["0", "-5", "0.001", "abc", "", None, True, "1e400"])
def test_the_amount_must_be_a_positive_naira_figure(
    db, api_client, admin, paid_in_full, amount
):
    fee_id, payment = paid_in_full

    response = refund(api_client, admin, fee_id, payment["id"], amount)

    assert response.status_code == 422
    assert refund_rows(db) == []


@pytest.mark.parametrize(
    "reason", ["", "    ", "oops", "x" * 201, None, 12345], ids=lambda r: str(r)[:8]
)
def test_the_reason_must_be_five_to_two_hundred_characters(
    db, api_client, admin, paid_in_full, reason
):
    fee_id, payment = paid_in_full

    response = refund(api_client, admin, fee_id, payment["id"], "10", reason=reason)

    assert response.status_code == 422
    assert refund_rows(db) == []


def test_the_reason_is_trimmed_and_the_limits_are_inclusive(
    db, api_client, admin, paid_in_full
):
    fee_id, payment = paid_in_full

    short = refund(api_client, admin, fee_id, payment["id"], "1", reason="  fives  ")
    longest = refund(api_client, admin, fee_id, payment["id"], "1", reason="z" * 200)

    assert short.json()["refund"]["reason"] == "fives"
    assert longest.json()["refund"]["reason"] == "z" * 200


def test_extra_fields_are_refused(db, api_client, admin, paid_in_full):
    fee_id, payment = paid_in_full

    response = api_client.post(
        f"{FEES}/{fee_id}/payments/{payment['id']}/refund",
        headers={**admin, "Idempotency-Key": "refund-extra-0001"},
        json={"amount": "5", "reason": REASON, "receipt_number": "REF-2026-000001"},
    )

    assert response.status_code == 422
    assert refund_rows(db) == []


@pytest.mark.parametrize(
    "key", [None, "short", "x" * 65, "has space in it", "semi;colon-key"]
)
def test_an_idempotency_key_is_required_and_must_be_well_formed(
    db, api_client, admin, paid_in_full, key
):
    fee_id, payment = paid_in_full
    headers = dict(admin)
    if key is not None:
        headers["Idempotency-Key"] = key

    response = api_client.post(
        f"{FEES}/{fee_id}/payments/{payment['id']}/refund",
        headers=headers,
        json={"amount": "5", "reason": REASON},
    )

    assert response.status_code == 422
    assert refund_rows(db) == []


# --- which payments may be refunded -------------------------------------------------------


def test_unknown_bills_and_payments_are_one_404(
    db, api_client, admin, school, paid_in_full
):
    fee_id, payment = paid_in_full
    other = bill(api_client, admin, school, "bola", "300", amount_paid="100")
    other_payment = other["payments"][0]["id"]

    unknown_bill = refund(api_client, admin, 999999, payment["id"], "5")
    unknown_payment = refund(api_client, admin, fee_id, 999999, "5")
    wrong_bill = refund(api_client, admin, fee_id, other_payment, "5")

    for response in (unknown_bill, unknown_payment, wrong_bill):
        assert response.status_code == 404
        assert response.json() == unknown_bill.json()
    assert refund_rows(db) == []


def test_non_numeric_ids_are_refused(api_client, admin):
    response = api_client.post(
        f"{FEES}/abc/payments/1/refund",
        headers={**admin, "Idempotency-Key": "refund-nonnum-01"},
        json={"amount": "5", "reason": REASON},
    )

    assert response.status_code == 422


def test_a_voided_payment_cannot_be_refunded(db, api_client, admin, paid_in_full):
    fee_id, payment = paid_in_full
    void(api_client, admin, fee_id, payment["id"])

    response = refund(api_client, admin, fee_id, payment["id"], "5")

    assert response.status_code == 409
    assert "voided" in response.json()["error"]["message"]
    assert refund_rows(db) == []


def test_a_refunded_payment_cannot_be_voided(db, api_client, admin, paid_in_full):
    fee_id, payment = paid_in_full
    refund(api_client, admin, fee_id, payment["id"], "5")

    response = void(api_client, admin, fee_id, payment["id"])

    assert response.status_code == 409
    assert "refunded" in response.json()["error"]["message"]
    detail = api_client.get(f"{FEES}/{fee_id}", headers=admin).json()
    assert detail["payments"][0]["status"] == "valid"
    assert detail["amount_paid"] == "995.00"


def test_voiding_an_already_voided_payment_still_works_when_nothing_was_refunded(
    db, api_client, admin, paid_in_full
):
    fee_id, payment = paid_in_full
    void(api_client, admin, fee_id, payment["id"])

    assert void(api_client, admin, fee_id, payment["id"]).status_code == 200


def test_only_an_administrator_may_refund(db, api_client, admin, school, paid_in_full):
    fee_id, payment = paid_in_full
    teacher = login_headers(db, api_client, "teacher")[1]
    _, parent = parent_for(db, api_client, [school["ada"]])

    assert refund(api_client, {}, fee_id, payment["id"], "5").status_code == 401
    assert refund(api_client, teacher, fee_id, payment["id"], "5").status_code == 403
    assert refund(api_client, parent, fee_id, payment["id"], "5").status_code == 403
    assert refund_rows(db) == []
    assert audits(db) == []


# --- retrying a refund --------------------------------------------------------------------


def test_a_retry_with_the_same_key_returns_the_original(
    db, api_client, admin, paid_in_full
):
    fee_id, payment = paid_in_full
    first = refund(
        api_client, admin, fee_id, payment["id"], "100", key="refund-retry-0001"
    )

    again = refund(
        api_client, admin, fee_id, payment["id"], "100", key="refund-retry-0001"
    )

    assert first.status_code == 201 and again.status_code == 200
    assert again.json()["refund"] == first.json()["refund"]
    assert len(refund_rows(db)) == 1
    assert len(audits(db)) == 1
    assert again.json()["fee"]["amount_paid"] == "900.00"


def test_a_retry_still_answers_after_the_payment_is_fully_refunded(
    db, api_client, admin, paid_in_full
):
    fee_id, payment = paid_in_full
    refund(api_client, admin, fee_id, payment["id"], "1000", key="refund-retry-0002")

    again = refund(
        api_client, admin, fee_id, payment["id"], "1000", key="refund-retry-0002"
    )

    assert again.status_code == 200
    assert len(refund_rows(db)) == 1


@pytest.mark.parametrize("amount", ["101", "99.99"])
def test_the_same_key_for_another_amount_is_refused(
    db, api_client, admin, paid_in_full, amount
):
    fee_id, payment = paid_in_full
    refund(api_client, admin, fee_id, payment["id"], "100", key="refund-retry-0003")

    response = refund(
        api_client, admin, fee_id, payment["id"], amount, key="refund-retry-0003"
    )

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "idempotency_key_reused"
    assert "refund" in response.json()["error"]["message"]
    assert len(refund_rows(db)) == 1


def test_the_same_key_for_another_payment_is_refused(
    db, api_client, admin, school, paid_in_full
):
    fee_id, payment = paid_in_full
    other = bill(api_client, admin, school, "bola", "500", amount_paid="500")
    refund(api_client, admin, fee_id, payment["id"], "100", key="refund-retry-0004")

    response = refund(
        api_client,
        admin,
        other["id"],
        other["payments"][0]["id"],
        "100",
        key="refund-retry-0004",
    )

    assert response.status_code == 409
    assert len(refund_rows(db)) == 1


def test_another_administrator_can_use_the_same_key_text(
    db, api_client, admin, paid_in_full
):
    fee_id, payment = paid_in_full
    second = login_headers(db, api_client, "admin", email="admin2@camdock.test")[1]
    refund(api_client, admin, fee_id, payment["id"], "100", key="refund-shared-001")

    response = refund(
        api_client, second, fee_id, payment["id"], "50", key="refund-shared-001"
    )

    assert response.status_code == 201
    assert len(refund_rows(db)) == 2


# --- receipts and the family's view -------------------------------------------------------


def test_the_refund_receipt_has_everything_needed_to_print(
    db, api_client, admin, school, paid_in_full
):
    fee_id, payment = paid_in_full
    made = refund(api_client, admin, fee_id, payment["id"], "250").json()["refund"]

    response = api_client.get(
        f"{FEES}/{fee_id}/refunds/{made['id']}/receipt", headers=admin
    )

    assert response.status_code == 200
    body = response.json()
    assert body["receipt_number"] == made["receipt_number"]
    assert REFUND_NUMBER.fullmatch(body["receipt_number"])
    assert body["date"]
    assert body["school"]["name"]
    assert body["student"]["full_name"] and body["student"]["class_name"]
    assert body["term"]["name"] and body["fee_type"] == "Tuition Fee"
    assert body["refund"]["amount"] == "250.00"
    assert body["refund"]["reason"] == REASON
    assert body["payment_receipt_number"] == payment["receipt_number"]
    assert (body["amount_billed"], body["amount_paid"], body["balance"]) == (
        "1000.00",
        "750.00",
        "250.00",
    )
    assert body["status"] == "partial"
    assert body["signatures"]["bursar_label"] and body["signatures"]["stamp_label"]


def test_the_bill_receipt_lists_the_refunds_and_counts_them(
    db, api_client, admin, paid_in_full
):
    fee_id, payment = paid_in_full
    refund(api_client, admin, fee_id, payment["id"], "100")

    receipt = api_client.get(f"{FEES}/{fee_id}/receipt", headers=admin).json()

    assert receipt["amount_paid"] == "900.00"
    assert [r["amount"] for r in receipt["refunds"]] == ["100.00"]


def test_an_instalment_receipt_shows_only_refunds_on_payments_so_far(
    db, api_client, admin, school
):
    fee = bill(api_client, admin, school, "ada", "1000", amount_paid="600")
    first = fee["payments"][0]
    second = pay(api_client, admin, fee["id"], "400")
    refund(api_client, admin, fee["id"], second["id"], "100")

    early = api_client.get(
        f"{FEES}/{fee['id']}/payments/{first['id']}/receipt", headers=admin
    ).json()
    late = api_client.get(
        f"{FEES}/{fee['id']}/payments/{second['id']}/receipt", headers=admin
    ).json()

    assert (early["amount_paid"], early["refunds"]) == ("600.00", [])
    assert late["amount_paid"] == "900.00"
    assert [r["amount"] for r in late["refunds"]] == ["100.00"]


def test_a_parent_sees_refunds_but_not_why(db, api_client, admin, school, paid_in_full):
    fee_id, payment = paid_in_full
    _, parent = parent_for(db, api_client, [school["ada"]])
    made = refund(api_client, admin, fee_id, payment["id"], "100").json()["refund"]

    detail = api_client.get(f"{FEES}/{fee_id}", headers=parent).json()
    receipt = api_client.get(f"{FEES}/{fee_id}/receipt", headers=parent).json()
    mine = api_client.get(
        f"{FEES}/{fee_id}/refunds/{made['id']}/receipt", headers=parent
    )

    assert mine.status_code == 200
    assert detail["refunds"][0]["amount"] == "100.00"
    assert detail["refunds"][0]["reason"] is None
    assert receipt["refunds"][0]["reason"] is None
    assert mine.json()["refund"]["reason"] is None
    assert REASON not in str(detail) + str(receipt) + mine.text
    assert (
        api_client.get(f"{FEES}/{fee_id}", headers=admin).json()["refunds"][0]["reason"]
        == REASON
    )


def test_another_familys_refund_receipt_is_a_404(
    db, api_client, admin, school, paid_in_full
):
    fee_id, payment = paid_in_full
    made = refund(api_client, admin, fee_id, payment["id"], "100").json()["refund"]
    _, stranger = parent_for(
        db, api_client, [school["bola"]], email="other@camdock.test"
    )

    response = api_client.get(
        f"{FEES}/{fee_id}/refunds/{made['id']}/receipt", headers=stranger
    )
    missing = api_client.get(
        f"{FEES}/999999/refunds/{made['id']}/receipt", headers=stranger
    )

    assert response.status_code == 404
    assert response.json() == missing.json()


def test_refund_receipts_need_an_admin_or_a_parent(db, api_client, admin, paid_in_full):
    fee_id, payment = paid_in_full
    made = refund(api_client, admin, fee_id, payment["id"], "100").json()["refund"]
    teacher = login_headers(db, api_client, "teacher")[1]
    url = f"{FEES}/{fee_id}/refunds/{made['id']}/receipt"

    assert api_client.get(url).status_code == 401
    assert api_client.get(url, headers=teacher).status_code == 403


def test_payments_show_how_much_was_refunded_and_is_refundable(
    db, api_client, admin, paid_in_full
):
    fee_id, payment = paid_in_full
    refund(api_client, admin, fee_id, payment["id"], "100")
    refund(api_client, admin, fee_id, payment["id"], "50.50")

    shown = api_client.get(f"{FEES}/{fee_id}", headers=admin).json()["payments"][0]

    assert (shown["refunded_amount"], shown["refundable_amount"]) == (
        "150.50",
        "849.50",
    )


# --- the database refuses to edit money that was given back --------------------------------


def refused(db, sql, **params):
    with pytest.raises((IntegrityError, DBAPIError)) as error, db.begin_nested():
        db.execute(text(sql), params)
    return str(error.value)


def insert_refund(
    db,
    payment_id,
    fee_id,
    by,
    amount="10",
    reason="a long enough reason",
    number="REF-T-1",
):
    return db.execute(
        text(
            "INSERT INTO fee_refunds (fee_payment_id, fee_record_id, amount, reason, "
            "receipt_number, created_by) VALUES (:p, :f, :a, :r, :n, :by)"
        ),
        {"p": payment_id, "f": fee_id, "a": amount, "r": reason, "n": number, "by": by},
    )


def test_a_refund_cannot_be_edited_or_deleted(
    db, api_client, admin, admin_login, paid_in_full
):
    fee_id, payment = paid_in_full
    made = refund(api_client, admin, fee_id, payment["id"], "100").json()["refund"]

    edited = refused(
        db, "UPDATE fee_refunds SET amount = 1 WHERE id = :id", id=made["id"]
    )
    deleted = refused(db, "DELETE FROM fee_refunds WHERE id = :id", id=made["id"])

    assert "append-only" in edited and "append-only" in deleted
    assert refund_rows(db)[0].amount == Decimal("100.00")


def test_the_database_will_not_take_a_refund_for_a_voided_payment(
    db, api_client, admin, admin_login, paid_in_full
):
    fee_id, payment = paid_in_full
    void(api_client, admin, fee_id, payment["id"])

    with pytest.raises((IntegrityError, DBAPIError)) as error, db.begin_nested():
        insert_refund(db, payment["id"], fee_id, admin_login[0])

    assert "voided payment cannot be refunded" in str(error.value)


def test_the_database_will_not_take_refunds_beyond_the_payment(
    db, api_client, admin, admin_login, paid_in_full
):
    fee_id, payment = paid_in_full
    insert_refund(
        db, payment["id"], fee_id, admin_login[0], amount="900", number="REF-T-1"
    )

    with pytest.raises((IntegrityError, DBAPIError)) as error, db.begin_nested():
        insert_refund(
            db, payment["id"], fee_id, admin_login[0], amount="100.01", number="REF-T-2"
        )

    assert "cannot exceed the payment" in str(error.value)
    insert_refund(
        db, payment["id"], fee_id, admin_login[0], amount="100", number="REF-T-3"
    )


def test_a_refund_cannot_name_another_bills_payment(
    db, api_client, admin, admin_login, school, paid_in_full
):
    _fee_id, payment = paid_in_full
    other = bill(api_client, admin, school, "bola", "300", amount_paid="100")

    with pytest.raises((IntegrityError, DBAPIError)) as error, db.begin_nested():
        insert_refund(db, payment["id"], other["id"], admin_login[0])

    assert "fk_fee_refunds_fee_payment_id_fee_payments" in str(error.value)


def test_a_refund_must_have_a_positive_amount_and_a_real_reason(
    db, api_client, admin, admin_login, paid_in_full
):
    fee_id, payment = paid_in_full

    for amount, reason, wanted in (
        ("0", "a long enough reason", "amount_positive"),
        ("-5", "a long enough reason", "amount_positive"),
        ("5", "no", "reason_length"),
    ):
        with pytest.raises((IntegrityError, DBAPIError)) as error, db.begin_nested():
            insert_refund(
                db, payment["id"], fee_id, admin_login[0], amount=amount, reason=reason
            )
        assert wanted in str(error.value)


def test_a_refunded_payment_cannot_be_voided_even_directly(
    db, api_client, admin, admin_login, paid_in_full
):
    fee_id, payment = paid_in_full
    refund(api_client, admin, fee_id, payment["id"], "100")

    message = refused(
        db,
        "UPDATE fee_payments SET voided_at = now(), voided_by = :by, "
        "void_reason = 'entered in error' WHERE id = :id",
        by=admin_login[0],
        id=payment["id"],
    )

    assert "refunded payment cannot be voided" in message


# --- two requests at the same moment (real, committed rows) ---------------------------------


def request(amount="60", reason="parent withdrew the child"):
    return RefundRequest(amount=Decimal(amount), reason=reason)


def test_two_refunds_at_once_cannot_exceed_the_payment(
    migrated_engine, committed_bill, monkeypatch
):
    actor, fee_id = committed_bill["admin"], committed_bill["fee_id"]
    with Session(migrated_engine) as session, session.begin():
        made, _ = fee_service.record_payment(
            session, actor, fee_id, PaymentCreate(amount="100")
        )
        payment_id = made.payment.id
    real = fee_service.refunded_on_payment

    def slow(db, pid):
        total = real(db, pid)
        time.sleep(0.4)  # both would read the same total before either wrote
        return total

    monkeypatch.setattr(fee_service, "refunded_on_payment", slow)
    start = threading.Barrier(2)
    outcomes: list[str] = []
    guard = threading.Lock()

    def worker(key):
        with Session(migrated_engine) as session:
            start.wait()
            try:
                fee_service.refund_payment(
                    session, actor, fee_id, payment_id, request(), key
                )
                session.commit()
                result = "refunded"
            except AppError as error:
                session.rollback()
                result = f"refused:{error.status_code}"
            with guard:
                outcomes.append(result)

    threads = [
        threading.Thread(target=worker, args=("race-refund-key-1",)),
        threading.Thread(target=worker, args=("race-refund-key-2",)),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)
        assert not thread.is_alive(), "a refund hung"

    with Session(migrated_engine) as session:
        refunded = session.execute(
            select(func.coalesce(func.sum(FeeRefund.amount), 0))
        ).scalar_one()
    assert sorted(outcomes) == ["refunded", "refused:422"]
    assert refunded == Decimal("60.00")


def test_a_refund_and_a_void_at_once_have_one_winner(
    migrated_engine, committed_bill, monkeypatch
):
    actor, fee_id = committed_bill["admin"], committed_bill["fee_id"]
    with Session(migrated_engine) as session, session.begin():
        made, _ = fee_service.record_payment(
            session, actor, fee_id, PaymentCreate(amount="100")
        )
        payment_id = made.payment.id
    real = fee_service.refunded_on_payment

    def slow(db, pid):
        total = real(db, pid)
        time.sleep(0.4)
        return total

    monkeypatch.setattr(fee_service, "refunded_on_payment", slow)
    start = threading.Barrier(2)
    outcomes: dict[str, str] = {}
    guard = threading.Lock()

    def refunder():
        with Session(migrated_engine) as session:
            start.wait()
            try:
                fee_service.refund_payment(
                    session,
                    actor,
                    fee_id,
                    payment_id,
                    request("50"),
                    "race-refund-key-3",
                )
                session.commit()
                result = "ok"
            except AppError as error:
                session.rollback()
                result = f"refused:{error.status_code}"
            with guard:
                outcomes["refund"] = result

    def voider():
        with Session(migrated_engine) as session:
            start.wait()
            try:
                fee_service.void_payment(
                    session, actor, fee_id, payment_id, "entered in error"
                )
                session.commit()
                result = "ok"
            except AppError as error:
                session.rollback()
                result = f"refused:{error.status_code}"
            with guard:
                outcomes["void"] = result

    threads = [threading.Thread(target=refunder), threading.Thread(target=voider)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)
        assert not thread.is_alive(), "a request hung"

    try:
        assert sorted(outcomes.values()) == ["ok", "refused:409"], outcomes
        with Session(migrated_engine) as session:
            refunds = session.execute(
                select(func.count()).select_from(FeeRefund)
            ).scalar_one()
            voided = session.execute(
                text("SELECT count(*) FROM fee_payments WHERE voided_at IS NOT NULL")
            ).scalar_one()
        assert (refunds, voided) in ((1, 0), (0, 1))
    finally:
        wipe(migrated_engine)
