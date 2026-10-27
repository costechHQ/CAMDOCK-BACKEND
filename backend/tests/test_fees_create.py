import re
from datetime import UTC, date, datetime, timedelta

import pytest
from sqlalchemy import func, select, update

from app.models import AuditLog, FeePayment, FeeRecord, Student
from app.models.fees import FEE_TYPES
from app.services import fees as fee_service
from tests.test_access_scope import make_class, make_student
from tests.test_teachers_api import login_headers
from tests.test_terms_api import make_terms, make_year

URL = "/api/v1/fees"
YEAR = date.today().year
BILL_NUMBER = re.compile(rf"FEE-{YEAR}-\d{{6}}")
PAYMENT_NUMBER = re.compile(rf"RCP-{YEAR}-\d{{6}}")


@pytest.fixture
def admin(db, api_client):
    return login_headers(db, api_client, "admin")[1]


@pytest.fixture
def school(db):
    p1 = make_class(db, "Primary 1")
    terms = make_terms(db, make_year(db, "2025/2026", date(2025, 9, 1)))
    return {
        "ada": make_student(db, p1, "S-ADA"),
        "bola": make_student(db, p1, "S-BOLA"),
        "term": terms["First Term"],
        "term2": terms["Second Term"],
    }


def body(school, **extra):
    return {
        "student_id": school["ada"],
        "term_id": school["term"],
        "fee_type": "Tuition Fee",
        "amount_billed": "50000",
        **extra,
    }


def counts(db) -> tuple[int, int]:
    return (
        db.execute(select(func.count()).select_from(FeeRecord)).scalar_one(),
        db.execute(select(func.count()).select_from(FeePayment)).scalar_one(),
    )


def audits(db, action):
    return db.execute(select(AuditLog.__table__).where(AuditLog.action == action)).all()


def post(api_client, headers, school, **extra):
    return api_client.post(URL, headers=headers, json=body(school, **extra))


def test_admin_bills_a_student_with_nothing_paid(db, api_client, admin, school):
    response = post(api_client, admin, school)

    assert response.status_code == 201
    out = response.json()
    assert BILL_NUMBER.fullmatch(out["receipt_number"])
    assert out == {
        "id": out["id"],
        "receipt_number": out["receipt_number"],
        "student": {
            "id": school["ada"],
            "student_number": "S-ADA",
            "full_name": "Student S-ADA",
        },
        "term": {
            "id": school["term"],
            "name": "First Term",
            "academic_year": "2025/2026",
        },
        "fee_type": "Tuition Fee",
        "amount_billed": "50000.00",
        "amount_paid": "0.00",
        "balance": "50000.00",
        "status": "due",
        "recorded_at": out["recorded_at"],
        "payments": [],
    }
    assert counts(db) == (1, 0)


def test_a_first_payment_makes_the_bill_partial(db, api_client, admin, school):
    out = post(api_client, admin, school, amount_paid="20000.50").json()

    assert (out["amount_paid"], out["balance"], out["status"]) == (
        "20000.50",
        "29999.50",
        "partial",
    )
    (payment,) = out["payments"]
    assert PAYMENT_NUMBER.fullmatch(payment["receipt_number"])
    assert payment["receipt_number"] != out["receipt_number"]
    assert payment["amount"] == "20000.50"
    assert counts(db) == (1, 1)


def test_paying_in_full_settles_the_bill(db, api_client, admin, school):
    out = post(api_client, admin, school, amount_paid=50000).json()

    assert (out["amount_paid"], out["balance"], out["status"]) == (
        "50000.00",
        "0.00",
        "paid",
    )


def test_amounts_are_exact_decimals(api_client, admin, school):
    out = post(
        api_client, admin, school, amount_billed="0.30", amount_paid="0.10"
    ).json()

    assert (out["amount_billed"], out["amount_paid"], out["balance"]) == (
        "0.30",
        "0.10",
        "0.20",
    )


@pytest.mark.parametrize("paid", [None, 0, "0", "0.00"])
def test_no_payment_row_when_nothing_is_paid(db, api_client, admin, school, paid):
    extra = {} if paid is None else {"amount_paid": paid}

    response = post(api_client, admin, school, **extra)

    assert response.status_code == 201
    assert response.json()["payments"] == []
    assert counts(db) == (1, 0)


def test_null_amount_paid_is_accepted(db, api_client, admin, school):
    assert post(api_client, admin, school, amount_paid=None).status_code == 201


@pytest.mark.parametrize(
    "extra",
    [
        {"amount_paid": "50000.01"},
        {"amount_paid": "-0.01"},
        {"amount_paid": "10.123"},
        {"amount_paid": True},
        {"amount_paid": "abc"},
        {"amount_billed": 0},
        {"amount_billed": "-1"},
        {"amount_billed": "0.001"},
        {"amount_billed": "10000000000.00"},
        {"amount_billed": True},
        {"amount_billed": "NaN"},
        {"amount_billed": None},
    ],
)
def test_invalid_amounts_are_rejected_and_nothing_is_stored(
    db, api_client, admin, school, extra
):
    response = post(api_client, admin, school, **extra)

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"
    assert counts(db) == (0, 0)
    assert audits(db, "fee.create") == []


@pytest.mark.parametrize("fee_type", FEE_TYPES)
def test_every_fee_type_is_accepted(db, api_client, admin, school, fee_type):
    response = post(api_client, admin, school, fee_type=fee_type)

    assert response.status_code == 201
    assert response.json()["fee_type"] == fee_type


@pytest.mark.parametrize(
    "fee_type", ["Bus Fee", "tuition fee", "TUITION FEE", " Tuition Fee", "", None]
)
def test_other_fee_types_are_rejected(db, api_client, admin, school, fee_type):
    assert post(api_client, admin, school, fee_type=fee_type).status_code == 422
    assert counts(db) == (0, 0)


def test_unknown_and_inactive_students_are_not_found(db, api_client, admin, school):
    db.execute(
        update(Student).where(Student.id == school["bola"]).values(is_active=False)
    )

    for student_id in (999999, school["bola"]):
        response = post(api_client, admin, school, student_id=student_id)
        assert response.status_code == 404
        assert response.json()["error"]["message"] == "Student not found."
    assert counts(db) == (0, 0)


def test_unknown_term_is_unprocessable(db, api_client, admin, school):
    response = post(api_client, admin, school, term_id=999999)

    assert response.status_code == 422
    assert response.json()["error"]["message"] == "Term not found."
    assert counts(db) == (0, 0)


def test_billing_the_same_fee_twice_is_a_conflict(db, api_client, admin, school):
    post(api_client, admin, school, amount_paid="100")

    again = post(api_client, admin, school, amount_billed="70000", amount_paid="5")

    assert again.status_code == 409
    assert again.json()["error"]["code"] == "conflict"
    assert counts(db) == (1, 1)
    assert len(audits(db, "fee.create")) == 1


def test_other_types_terms_and_students_may_still_be_billed(
    db, api_client, admin, school
):
    post(api_client, admin, school)

    assert post(api_client, admin, school, fee_type="PTA Levy").status_code == 201
    assert post(api_client, admin, school, term_id=school["term2"]).status_code == 201
    assert post(api_client, admin, school, student_id=school["bola"]).status_code == 201


def test_losing_a_race_to_bill_is_a_conflict_not_a_server_error(
    db, api_client, admin, school, monkeypatch
):
    post(api_client, admin, school)
    monkeypatch.setattr(fee_service, "_is_billed", lambda *args: False)

    response = post(api_client, admin, school)

    assert response.status_code == 409
    assert counts(db) == (1, 0)


def test_receipt_numbers_increase(api_client, admin, school):
    first = post(api_client, admin, school).json()["receipt_number"]
    second = post(api_client, admin, school, fee_type="PTA Levy").json()[
        "receipt_number"
    ]

    assert int(second.rsplit("-", 1)[1]) > int(first.rsplit("-", 1)[1])


def test_dates_come_from_the_server_clock(api_client, admin, school):
    out = post(api_client, admin, school, amount_paid="1").json()

    now = datetime.now(UTC)
    for stamp in (out["recorded_at"], out["payments"][0]["paid_at"]):
        assert abs(now - datetime.fromisoformat(stamp)) < timedelta(minutes=5)


@pytest.mark.parametrize("role", ["teacher", "parent"])
def test_non_admins_are_forbidden(db, api_client, school, role):
    headers = login_headers(db, api_client, role)[1]

    response = post(api_client, headers, school)

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "forbidden"
    assert counts(db) == (0, 0)


def test_anonymous_request_is_unauthorized(db, api_client, school):
    assert api_client.post(URL, json=body(school)).status_code == 401
    assert counts(db) == (0, 0)


@pytest.mark.parametrize(
    "extra",
    [
        {"recorded_at": "2020-01-01T00:00:00Z"},
        {"receipt_number": "FEE-2020-000001"},
        {"status": "paid"},
        {"id": 1},
        {"student_id": 0},
        {"student_id": "1"},
        {"term_id": 1.5},
    ],
)
def test_server_owned_and_malformed_fields_are_rejected(
    db, api_client, admin, school, extra
):
    assert post(api_client, admin, school, **extra).status_code == 422
    assert counts(db) == (0, 0)


@pytest.mark.parametrize(
    "missing", ["student_id", "term_id", "fee_type", "amount_billed"]
)
def test_required_fields_are_required(db, api_client, admin, school, missing):
    payload = body(school)
    del payload[missing]

    assert api_client.post(URL, headers=admin, json=payload).status_code == 422


def test_creation_is_audited_with_the_acting_admin(db, api_client, admin, school):
    out = post(api_client, admin, school, amount_paid="12500").json()
    admin_id = db.execute(select(AuditLog.actor_user_id)).scalars().first()

    (bill,) = audits(db, "fee.create")
    (payment,) = audits(db, "fee.payment.create")
    assert bill.actor_user_id == payment.actor_user_id == admin_id
    assert (bill.entity_type, bill.entity_id) == ("fee_record", out["id"])
    assert bill.changes == {
        "student_id": school["ada"],
        "term_id": school["term"],
        "fee_type": "Tuition Fee",
        "amount_billed": "50000.00",
        "receipt_number": out["receipt_number"],
    }
    assert (payment.entity_type, payment.entity_id) == (
        "fee_payment",
        out["payments"][0]["id"],
    )
    assert payment.changes == {
        "fee_record_id": out["id"],
        "amount": "12500.00",
        "receipt_number": out["payments"][0]["receipt_number"],
    }


def test_an_unpaid_bill_writes_no_payment_audit(db, api_client, admin, school):
    post(api_client, admin, school)

    assert len(audits(db, "fee.create")) == 1
    assert audits(db, "fee.payment.create") == []


def test_responses_never_contain_secrets(api_client, admin, school):
    text = post(api_client, admin, school, amount_paid="1").text

    for forbidden in ("password", "hash", "token"):
        assert forbidden not in text.lower()


# --- GET /fee-types --------------------------------------------------------------------


@pytest.mark.parametrize("role", ["admin", "parent"])
def test_fee_types_are_listed_in_order(db, api_client, role):
    headers = login_headers(db, api_client, role)[1]

    response = api_client.get("/api/v1/fee-types", headers=headers)

    assert response.status_code == 200
    assert response.json() == {"items": list(FEE_TYPES)}


def test_teachers_cannot_list_fee_types(db, api_client):
    headers = login_headers(db, api_client, "teacher")[1]

    assert api_client.get("/api/v1/fee-types", headers=headers).status_code == 403


def test_fee_types_require_authentication(api_client):
    assert api_client.get("/api/v1/fee-types").status_code == 401
