import re
from datetime import date

import pytest
from sqlalchemy import func, select

from app.models import AuditLog, FeePayment
from tests.test_access_scope import make_class, make_student
from tests.test_teachers_api import login_headers
from tests.test_terms_api import make_terms, make_year

FEES = "/api/v1/fees"
PAYMENT_NUMBER = re.compile(rf"RCP-{date.today().year}-\d{{6}}")


@pytest.fixture
def admin(db, api_client):
    return login_headers(db, api_client, "admin")[1]


@pytest.fixture
def school(db):
    p1 = make_class(db, "Primary 1")
    terms = make_terms(db, make_year(db, "2025/2026", date(2025, 9, 1)))
    return {"ada": make_student(db, p1, "S-ADA"), "term": terms["First Term"]}


def bill(api_client, admin, school, amount_billed="1000", **extra):
    response = api_client.post(
        FEES,
        headers=admin,
        json={
            "student_id": school["ada"],
            "term_id": school["term"],
            "fee_type": "Tuition Fee",
            "amount_billed": amount_billed,
            **extra,
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def pay(api_client, admin, fee_id, amount):
    return api_client.post(
        f"{FEES}/{fee_id}/payments", headers=admin, json={"amount": amount}
    )


def payment_count(db) -> int:
    return db.execute(select(func.count()).select_from(FeePayment)).scalar_one()


def audits(db):
    return db.execute(
        select(AuditLog.__table__).where(AuditLog.action == "fee.payment.create")
    ).all()


def test_a_payment_makes_a_due_bill_partial(db, api_client, admin, school):
    fee = bill(api_client, admin, school)

    response = pay(api_client, admin, fee["id"], "250.50")

    assert response.status_code == 201
    out = response.json()
    assert PAYMENT_NUMBER.fullmatch(out["payment"]["receipt_number"])
    assert out["payment"]["amount"] == "250.50"
    assert out["fee"]["id"] == fee["id"]
    assert (out["fee"]["amount_paid"], out["fee"]["balance"], out["fee"]["status"]) == (
        "250.50",
        "749.50",
        "partial",
    )
    assert out["fee"]["payments"] == [out["payment"]]


def test_instalments_accumulate_until_the_bill_is_settled(
    db, api_client, admin, school
):
    fee = bill(api_client, admin, school, amount_paid="400")

    second = pay(api_client, admin, fee["id"], "350").json()
    last = pay(api_client, admin, fee["id"], "250").json()

    assert second["fee"]["status"] == "partial"
    assert (
        last["fee"]["amount_paid"],
        last["fee"]["balance"],
        last["fee"]["status"],
    ) == (
        "1000.00",
        "0.00",
        "paid",
    )
    assert [p["amount"] for p in last["fee"]["payments"]] == [
        "400.00",
        "350.00",
        "250.00",
    ]
    assert payment_count(db) == 3
    receipts = {p["receipt_number"] for p in last["fee"]["payments"]}
    assert len(receipts) == 3 and last["fee"]["receipt_number"] not in receipts


def test_a_payment_of_exactly_the_balance_settles_it(db, api_client, admin, school):
    fee = bill(api_client, admin, school)

    out = pay(api_client, admin, fee["id"], "1000").json()

    assert (out["fee"]["status"], out["fee"]["balance"]) == ("paid", "0.00")


def test_cents_add_up_exactly(db, api_client, admin, school):
    fee = bill(api_client, admin, school, amount_billed="0.30")

    pay(api_client, admin, fee["id"], "0.10")
    out = pay(api_client, admin, fee["id"], "0.20").json()

    assert (out["fee"]["amount_paid"], out["fee"]["balance"], out["fee"]["status"]) == (
        "0.30",
        "0.00",
        "paid",
    )


def test_one_cent_over_the_balance_is_refused(db, api_client, admin, school):
    fee = bill(api_client, admin, school, amount_paid="500")

    response = pay(api_client, admin, fee["id"], "500.01")

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"
    assert response.json()["error"]["message"] == (
        "Payment of 500.01 exceeds the outstanding balance of 500.00."
    )
    assert payment_count(db) == 1


def test_a_settled_bill_takes_no_more_payments(db, api_client, admin, school):
    fee = bill(api_client, admin, school, amount_paid="1000")

    response = pay(api_client, admin, fee["id"], "0.01")

    assert response.status_code == 422
    assert (
        response.json()["error"]["message"] == "This fee has already been paid in full."
    )
    assert payment_count(db) == 1


@pytest.mark.parametrize(
    "amount", [0, "0", "-1", "-0.01", "0.001", "10.123", "abc", "", "NaN", True, None]
)
def test_invalid_amounts_are_rejected_and_nothing_is_stored(
    db, api_client, admin, school, amount
):
    fee = bill(api_client, admin, school)

    response = pay(api_client, admin, fee["id"], amount)

    assert response.status_code == 422
    assert payment_count(db) == 0
    assert audits(db) == []


def test_unknown_fee_is_not_found(db, api_client, admin, school):
    response = pay(api_client, admin, 999999, "10")

    assert response.status_code == 404
    assert response.json()["error"]["message"] == "Fee record not found."
    assert (
        api_client.post(
            f"{FEES}/abc/payments", headers=admin, json={"amount": "10"}
        ).status_code
        == 422
    )


@pytest.mark.parametrize(
    "payload",
    [{}, {"amount": "10", "receipt_number": "RCP-1"}, {"amount": "10", "paid_at": "x"}],
)
def test_missing_and_server_owned_fields(db, api_client, admin, school, payload):
    fee = bill(api_client, admin, school)

    response = api_client.post(
        f"{FEES}/{fee['id']}/payments", headers=admin, json=payload
    )

    assert response.status_code == 422
    assert payment_count(db) == 0


def test_receipt_numbers_increase(db, api_client, admin, school):
    fee = bill(api_client, admin, school)

    first = pay(api_client, admin, fee["id"], "1").json()["payment"]["receipt_number"]
    second = pay(api_client, admin, fee["id"], "1").json()["payment"]["receipt_number"]

    assert int(second.rsplit("-", 1)[1]) > int(first.rsplit("-", 1)[1])


def test_payments_are_audited_and_refusals_are_not(db, api_client, admin, school):
    fee = bill(api_client, admin, school)
    admin_id = db.execute(select(AuditLog.actor_user_id)).scalars().first()

    ok = pay(api_client, admin, fee["id"], "300").json()["payment"]
    pay(api_client, admin, fee["id"], "9999")

    (row,) = audits(db)
    assert row.actor_user_id == admin_id
    assert (row.entity_type, row.entity_id) == ("fee_payment", ok["id"])
    assert row.changes == {
        "fee_record_id": fee["id"],
        "amount": "300.00",
        "receipt_number": ok["receipt_number"],
    }


@pytest.mark.parametrize("role", ["teacher", "parent"])
def test_non_admins_are_forbidden(db, api_client, admin, school, role):
    fee = bill(api_client, admin, school)
    headers = login_headers(db, api_client, role)[1]

    response = pay(api_client, headers, fee["id"], "10")

    assert response.status_code == 403
    assert payment_count(db) == 0


def test_anonymous_request_is_unauthorized(db, api_client, admin, school):
    fee = bill(api_client, admin, school)

    response = api_client.post(f"{FEES}/{fee['id']}/payments", json={"amount": "10"})

    assert response.status_code == 401
    assert payment_count(db) == 0


def test_responses_never_contain_secrets(db, api_client, admin, school):
    fee = bill(api_client, admin, school)

    text = pay(api_client, admin, fee["id"], "10").text.lower()

    for forbidden in ("password", "hash", "token"):
        assert forbidden not in text
