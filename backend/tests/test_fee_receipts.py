import re
from datetime import date
from types import SimpleNamespace

import pytest
from sqlalchemy import func, select, update

from app.core.config import get_settings
from app.models import AuditLog, SchoolClass, Student
from tests.test_access_scope import link, make_class, make_student
from tests.test_teachers_api import login_headers
from tests.test_terms_api import make_terms, make_year

FEES = "/api/v1/fees"
BILL_NUMBER = re.compile(rf"FEE-{date.today().year}-\d{{6}}")
PAYMENT_NUMBER = re.compile(rf"RCP-{date.today().year}-\d{{6}}")


@pytest.fixture
def admin(db, api_client):
    return login_headers(db, api_client, "admin")[1]


@pytest.fixture
def school(db):
    p1, p2 = make_class(db, "Primary 1"), make_class(db, "Primary 2")
    terms = make_terms(db, make_year(db, "2025/2026", date(2025, 9, 1)))
    return {
        "p1": p1,
        "p2": p2,
        "ada": make_student(db, p1, "S-ADA"),
        "ben": make_student(db, p2, "S-BEN"),
        "term": terms["First Term"],
    }


@pytest.fixture
def parents(db, api_client, school):
    mum_id, mum = login_headers(db, api_client, "parent", email="mum@camdock.test")
    _, other = login_headers(db, api_client, "parent", email="other@camdock.test")
    link(db, SimpleNamespace(id=mum_id), school["ada"])
    return {"mum": mum, "other": other}


def bill(api_client, admin, school, student="ada", billed="1000", **extra):
    response = api_client.post(
        FEES,
        headers=admin,
        json={
            "student_id": school[student],
            "term_id": school["term"],
            "fee_type": "Tuition Fee",
            "amount_billed": billed,
            **extra,
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def pay(api_client, admin, fee_id, amount):
    response = api_client.post(
        f"{FEES}/{fee_id}/payments", headers=admin, json={"amount": amount}
    )
    assert response.status_code == 201, response.text
    return response.json()["payment"]


def receipt(api_client, headers, fee_id):
    return api_client.get(f"{FEES}/{fee_id}/receipt", headers=headers)


def instalment_receipt(api_client, headers, fee_id, payment_id):
    return api_client.get(
        f"{FEES}/{fee_id}/payments/{payment_id}/receipt", headers=headers
    )


# --- bill receipt -------------------------------------------------------------------


def test_a_bill_receipt_carries_every_required_field(db, api_client, admin, school):
    fee = bill(api_client, admin, school, amount_paid="400")
    second = pay(api_client, admin, fee["id"], "100.50")

    response = receipt(api_client, admin, fee["id"])

    assert response.status_code == 200
    body = response.json()
    assert BILL_NUMBER.fullmatch(body["receipt_number"])
    assert body["receipt_number"] == fee["receipt_number"]
    assert body["date"] == fee["recorded_at"]
    assert body["school"] == {
        "name": "Camdock International School",
        "location": get_settings().school_location,
    }
    assert body["student"] == {
        "id": school["ada"],
        "student_number": "S-ADA",
        "full_name": "Student S-ADA",
        "class_name": "Primary 1",
    }
    assert body["term"]["name"] == "First Term"
    assert body["term"]["academic_year"] == "2025/2026"
    assert body["fee_type"] == "Tuition Fee"
    assert (body["amount_billed"], body["amount_paid"], body["balance"]) == (
        "1000.00",
        "500.50",
        "499.50",
    )
    assert body["status"] == "partial"
    assert body["payment"] is None
    assert [p["amount"] for p in body["payments"]] == ["400.00", "100.50"]
    assert body["payments"][-1]["id"] == second["id"]
    assert all(PAYMENT_NUMBER.fullmatch(p["receipt_number"]) for p in body["payments"])
    assert body["signatures"] == {
        "bursar_label": "Bursar",
        "stamp_label": "School stamp",
    }


def test_a_bill_with_no_payments_still_has_a_receipt(db, api_client, admin, school):
    fee = bill(api_client, admin, school)

    body = receipt(api_client, admin, fee["id"]).json()

    assert (body["status"], body["amount_paid"], body["balance"]) == (
        "due",
        "0.00",
        "1000.00",
    )
    assert body["payments"] == []


def test_a_settled_bill_receipt_is_paid_with_no_balance(db, api_client, admin, school):
    fee = bill(api_client, admin, school, amount_paid="1000")

    body = receipt(api_client, admin, fee["id"]).json()

    assert (body["status"], body["balance"]) == ("paid", "0.00")


def test_fetching_a_receipt_twice_gives_the_same_document(
    db, api_client, admin, school
):
    fee = bill(api_client, admin, school, amount_paid="100")

    first = receipt(api_client, admin, fee["id"]).json()
    second = receipt(api_client, admin, fee["id"]).json()

    assert first == second


def test_school_identity_comes_from_settings(
    monkeypatch, db, api_client, admin, school
):
    fee = bill(api_client, admin, school)
    monkeypatch.setenv("SCHOOL_NAME", "Camdock Test Academy")
    monkeypatch.setenv("SCHOOL_LOCATION", "Enugu, Nigeria")
    get_settings.cache_clear()
    try:
        body = receipt(api_client, admin, fee["id"]).json()
    finally:
        monkeypatch.undo()
        get_settings.cache_clear()

    assert body["school"] == {
        "name": "Camdock Test Academy",
        "location": "Enugu, Nigeria",
    }


def test_the_receipt_shows_the_students_current_class(db, api_client, admin, school):
    fee = bill(api_client, admin, school)
    db.execute(
        update(Student).where(Student.id == school["ada"]).values(class_id=school["p2"])
    )

    body = receipt(api_client, admin, fee["id"]).json()

    assert body["student"]["class_name"] == "Primary 2"
    assert db.execute(select(func.count()).select_from(SchoolClass)).scalar_one() == 2


def test_a_deactivated_students_receipt_still_works(db, api_client, admin, school):
    fee = bill(api_client, admin, school)
    db.execute(
        update(Student).where(Student.id == school["ada"]).values(is_active=False)
    )

    assert receipt(api_client, admin, fee["id"]).status_code == 200


# --- instalment receipt -------------------------------------------------------------


def test_an_instalment_receipt_shows_the_figures_once_it_was_paid(
    db, api_client, admin, school
):
    fee = bill(api_client, admin, school, amount_paid="300")
    second = pay(api_client, admin, fee["id"], "200")
    third = pay(api_client, admin, fee["id"], "500")

    first_body = instalment_receipt(
        api_client, admin, fee["id"], fee["payments"][0]["id"]
    ).json()
    second_body = instalment_receipt(api_client, admin, fee["id"], second["id"]).json()
    third_body = instalment_receipt(api_client, admin, fee["id"], third["id"]).json()

    assert (
        first_body["amount_paid"],
        first_body["balance"],
        first_body["status"],
    ) == ("300.00", "700.00", "partial")
    assert (second_body["amount_paid"], second_body["balance"]) == ("500.00", "500.00")
    assert (
        third_body["amount_paid"],
        third_body["balance"],
        third_body["status"],
    ) == ("1000.00", "0.00", "paid")
    assert [p["amount"] for p in second_body["payments"]] == ["300.00", "200.00"]


def test_an_instalment_receipt_is_numbered_and_dated_by_the_payment(
    db, api_client, admin, school
):
    fee = bill(api_client, admin, school)
    payment = pay(api_client, admin, fee["id"], "250")

    body = instalment_receipt(api_client, admin, fee["id"], payment["id"]).json()

    assert body["receipt_number"] == payment["receipt_number"]
    assert PAYMENT_NUMBER.fullmatch(body["receipt_number"])
    assert body["date"] == payment["paid_at"]
    assert body["payment"] == payment
    assert body["student"]["class_name"] == "Primary 1"
    assert body["fee_type"] == "Tuition Fee"
    assert set(body["signatures"]) == {"bursar_label", "stamp_label"}


def test_a_payment_of_another_bill_or_an_unknown_one_is_not_found(
    db, api_client, admin, school
):
    ada = bill(api_client, admin, school)
    pay(
        api_client, admin, ada["id"], "5"
    )  # Ada has a payment, just not the one asked for
    ben = bill(api_client, admin, school, student="ben")
    ben_payment = pay(api_client, admin, ben["id"], "10")

    wrong_bill = instalment_receipt(api_client, admin, ada["id"], ben_payment["id"])
    unknown = instalment_receipt(api_client, admin, ada["id"], 999999)

    assert wrong_bill.status_code == unknown.status_code == 404
    assert wrong_bill.json() == unknown.json()
    assert unknown.json()["error"]["message"] == "Payment not found."


# --- access -------------------------------------------------------------------------


def test_a_parent_gets_their_childs_receipts_only(
    db, api_client, admin, school, parents
):
    ada = bill(api_client, admin, school)
    payment = pay(api_client, admin, ada["id"], "50")
    ben = bill(api_client, admin, school, student="ben")
    ben_payment = pay(api_client, admin, ben["id"], "50")

    assert receipt(api_client, parents["mum"], ada["id"]).status_code == 200
    assert (
        instalment_receipt(
            api_client, parents["mum"], ada["id"], payment["id"]
        ).status_code
        == 200
    )
    missing = receipt(api_client, parents["mum"], 999999)
    other_family = receipt(api_client, parents["mum"], ben["id"])
    other_payment = instalment_receipt(
        api_client, parents["mum"], ben["id"], ben_payment["id"]
    )
    assert other_family.status_code == other_payment.status_code == 404
    assert other_family.json() == missing.json()
    assert other_payment.json() == missing.json()


def test_an_unlinked_parent_gets_nothing(db, api_client, admin, school, parents):
    fee = bill(api_client, admin, school)
    payment = pay(api_client, admin, fee["id"], "50")

    assert receipt(api_client, parents["other"], fee["id"]).status_code == 404
    assert (
        instalment_receipt(
            api_client, parents["other"], fee["id"], payment["id"]
        ).status_code
        == 404
    )


def test_teachers_and_anonymous_users_are_refused(db, api_client, admin, school):
    fee = bill(api_client, admin, school)
    payment = pay(api_client, admin, fee["id"], "50")
    teacher = login_headers(db, api_client, "teacher")[1]

    for path in (
        f"{FEES}/{fee['id']}/receipt",
        f"{FEES}/{fee['id']}/payments/{payment['id']}/receipt",
    ):
        assert api_client.get(path, headers=teacher).status_code == 403
        assert api_client.get(path).status_code == 401


@pytest.mark.parametrize(
    "path", ["abc/receipt", "1/payments/abc/receipt", "abc/payments/1/receipt"]
)
def test_malformed_ids_are_rejected(db, api_client, admin, school, path):
    assert api_client.get(f"{FEES}/{path}", headers=admin).status_code == 422


def test_an_unknown_bill_is_not_found(db, api_client, admin, school):
    assert receipt(api_client, admin, 999999).status_code == 404
    assert instalment_receipt(api_client, admin, 999999, 1).status_code == 404


# --- safety -------------------------------------------------------------------------


def test_reading_a_receipt_writes_nothing(db, api_client, admin, school):
    fee = bill(api_client, admin, school, amount_paid="10")
    before = db.execute(select(func.count()).select_from(AuditLog)).scalar_one()

    receipt(api_client, admin, fee["id"])
    instalment_receipt(api_client, admin, fee["id"], fee["payments"][0]["id"])

    assert db.execute(select(func.count()).select_from(AuditLog)).scalar_one() == before


def test_responses_never_contain_secrets(db, api_client, admin, school):
    fee = bill(api_client, admin, school, amount_paid="10")
    texts = [
        receipt(api_client, admin, fee["id"]).text,
        instalment_receipt(api_client, admin, fee["id"], fee["payments"][0]["id"]).text,
    ]

    for text in texts:
        for forbidden in ("password", "hash", "token"):
            assert forbidden not in text.lower()
