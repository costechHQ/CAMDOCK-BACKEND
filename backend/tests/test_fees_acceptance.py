"""SRS section 8, "Fees and receipts": one test per checklist item, over the HTTP API.

Students, parent links, bills and payments are created through the real endpoints. Only
what has no endpoint is inserted directly: classes, the school year and terms, and the
accounts of the shared `world` (the end-to-end story creates its accounts through
`POST /users`). Logins are real. Money is always compared as exact strings.

Two cross-cutting groups follow the checklist: a scope matrix over every fee endpoint, and
the error contract and secrets.
"""

import logging
import re
from datetime import date

import pytest
from sqlalchemy import func, select

from app.core.config import get_settings
from app.models import AuditLog, FeePayment, FeeRecord
from app.models.fees import FEE_TYPES
from tests.test_access_scope import make_class
from tests.test_auth_api import PASSWORD, add_user, bearer, login_token
from tests.test_terms_api import make_terms, make_year

API = "/api/v1"
FEES = f"{API}/fees"
STUDENTS = f"{API}/students"
YEAR = date.today().year
BILL_NUMBER = re.compile(rf"FEE-{YEAR}-\d{{6}}")
PAYMENT_NUMBER = re.compile(rf"RCP-{YEAR}-\d{{6}}")


def login(db, client, role, email):
    """Insert an account, log in through the API; returns (user id, auth headers)."""
    user_id = add_user(db, email=email, role=role)
    return user_id, bearer(login_token(client, email=email))


def count(db, model) -> int:
    return db.execute(select(func.count()).select_from(model)).scalar_one()


def post_bill(
    client, headers, student, term, fee_type="Tuition Fee", billed="1000", **x
):
    return client.post(
        FEES,
        headers=headers,
        json={
            "student_id": student,
            "term_id": term,
            "fee_type": fee_type,
            "amount_billed": billed,
            **x,
        },
    )


def bill(client, headers, student, term, *args, **kwargs):
    response = post_bill(client, headers, student, term, *args, **kwargs)
    assert response.status_code == 201, response.text
    return response.json()


def post_payment(client, headers, fee_id, amount):
    return client.post(
        f"{FEES}/{fee_id}/payments", headers=headers, json={"amount": amount}
    )


def pay(client, headers, fee_id, amount):
    response = post_payment(client, headers, fee_id, amount)
    assert response.status_code == 201, response.text
    return response.json()


def detail(client, headers, fee_id):
    return client.get(f"{FEES}/{fee_id}", headers=headers)


def listing(client, headers, **params):
    return client.get(FEES, headers=headers, params={"limit": 100, **params})


def receipt(client, headers, fee_id):
    return client.get(f"{FEES}/{fee_id}/receipt", headers=headers)


def instalment_receipt(client, headers, fee_id, payment_id):
    return client.get(f"{FEES}/{fee_id}/payments/{payment_id}/receipt", headers=headers)


def ids(page) -> list[int]:
    return [item["id"] for item in page["items"]]


@pytest.fixture
def world(db, api_client):
    """A small school, all billed through the fee endpoints.

    Ada (Primary 1): Tuition Fee 1000 in the first term, 400 paid when billed and 100
    later, so 500 paid, 500 owing, partial. Bola (Primary 1): Tuition Fee 500, due.
    Chi (Primary 2): Tuition Fee 800, paid in full. Totals: billed 2300, paid 1300,
    balance 1000. Mum is Ada's parent, Pat is Bola's, and Nobody has no children.
    """
    ids_ = {
        "p1": make_class(db, "Primary 1"),
        "p2": make_class(db, "Primary 2"),
    }
    terms = make_terms(db, make_year(db, "2025/2026", date(2025, 9, 1)))
    ids_["term"], ids_["second"] = terms["First Term"], terms["Second Term"]

    ids_["admin_id"], admin = login(db, api_client, "admin", "admin@camdock.test")
    for name, class_key in [("ada", "p1"), ("bola", "p1"), ("chi", "p2")]:
        response = api_client.post(
            STUDENTS,
            headers=admin,
            json={"full_name": name.capitalize(), "class_id": ids_[class_key]},
        )
        assert response.status_code == 201, response.text
        ids_[name] = response.json()["id"]

    def parent(email, children):
        user_id, headers = login(db, api_client, "parent", email)
        for child in children:
            response = api_client.post(
                f"{STUDENTS}/{ids_[child]}/parents",
                headers=admin,
                json={"parent_user_id": user_id},
            )
            assert response.status_code == 201, response.text
        return user_id, headers

    ids_["mum_id"], mum = parent("mum@camdock.test", ["ada"])
    _, pat = parent("pat@camdock.test", ["bola"])
    _, nobody = parent("nobody@camdock.test", [])
    ids_["headers"] = {
        "anonymous": {},
        "admin": admin,
        "teacher": login(db, api_client, "teacher", "teacher@camdock.test")[1],
        "parent_ada": mum,
        "parent_bola": pat,
        "parent_unlinked": nobody,
    }

    ada = bill(api_client, admin, ids_["ada"], ids_["term"], amount_paid="400")
    ids_["ada_instalment"] = pay(api_client, admin, ada["id"], "100")["payment"]
    ids_["ada_fee"] = detail(api_client, admin, ada["id"]).json()
    ids_["bola_fee"] = bill(api_client, admin, ids_["bola"], ids_["term"], billed="500")
    ids_["chi_fee"] = bill(
        api_client,
        admin,
        ids_["chi"],
        ids_["term"],
        billed="800",
        amount_paid="800",
    )
    return ids_


# --- Only authorized administrators can record fee entries ----------------------------


@pytest.mark.parametrize(
    ("caller", "status"),
    [("anonymous", 401), ("teacher", 403), ("parent_ada", 403), ("admin", 201)],
)
def test_only_administrators_can_record_fee_entries(
    db, api_client, world, caller, status
):
    headers = world["headers"][caller]
    bills, payments = count(db, FeeRecord), count(db, FeePayment)

    new_bill = post_bill(api_client, headers, world["chi"], world["second"])
    new_payment = post_payment(api_client, headers, world["bola_fee"]["id"], "10")

    assert new_bill.status_code == new_payment.status_code == status
    granted = 1 if status == 201 else 0
    assert count(db, FeeRecord) == bills + granted
    assert count(db, FeePayment) == payments + granted


# --- Invalid billed or paid amounts are rejected ---------------------------------------

BAD_AMOUNTS = [0, "0", "-1", "-0.01", "0.001", "10.123", "abc", "", "NaN", "Infinity"]
BAD_AMOUNTS += [True, None, "10000000000.00", "1e3x", [], {}]


@pytest.mark.parametrize("amount", BAD_AMOUNTS)
def test_invalid_billed_amounts_are_rejected(db, api_client, world, amount):
    bills, payments = count(db, FeeRecord), count(db, FeePayment)

    response = post_bill(
        api_client,
        world["headers"]["admin"],
        world["chi"],
        world["second"],
        billed=amount,
    )

    assert response.status_code == 422
    assert (count(db, FeeRecord), count(db, FeePayment)) == (bills, payments)


@pytest.mark.parametrize(
    "paid", ["-1", "-0.01", "0.001", "abc", "NaN", True, "1000.01", "2000"]
)
def test_invalid_paid_amounts_are_rejected_on_a_new_bill(db, api_client, world, paid):
    bills, payments = count(db, FeeRecord), count(db, FeePayment)

    response = post_bill(
        api_client,
        world["headers"]["admin"],
        world["chi"],
        world["second"],
        billed="1000",
        amount_paid=paid,
    )

    assert response.status_code == 422
    assert (count(db, FeeRecord), count(db, FeePayment)) == (bills, payments)


@pytest.mark.parametrize("amount", [*BAD_AMOUNTS, "500.01", "99999"])
def test_invalid_and_excessive_payments_are_rejected(db, api_client, world, amount):
    payments = count(db, FeePayment)

    response = post_payment(
        api_client, world["headers"]["admin"], world["ada_fee"]["id"], amount
    )

    assert response.status_code == 422
    assert count(db, FeePayment) == payments


# --- Paid, Partial and Due are calculated correctly ------------------------------------


def statuses_everywhere(client, headers, fee_id, payment_id):
    """The status of one bill as every endpoint reports it."""
    found = {
        "detail": detail(client, headers, fee_id).json()["status"],
        "receipt": receipt(client, headers, fee_id).json()["status"],
        "instalment": instalment_receipt(client, headers, fee_id, payment_id).json()[
            "status"
        ]
        if payment_id
        else None,
    }
    for status in ("paid", "partial", "due"):
        if fee_id in ids(listing(client, headers, status=status).json()):
            found.setdefault("filters", []).append(status)
    item = next(
        i for i in listing(client, headers).json()["items"] if i["id"] == fee_id
    )
    found["list"] = item["status"]
    return found


def test_paid_partial_and_due_are_calculated_correctly(db, api_client, world):
    admin, fee_id = world["headers"]["admin"], world["bola_fee"]["id"]

    due = statuses_everywhere(api_client, admin, fee_id, None)
    first = pay(api_client, admin, fee_id, "200")["payment"]["id"]
    partial = statuses_everywhere(api_client, admin, fee_id, first)
    last = pay(api_client, admin, fee_id, "300")["payment"]["id"]
    paid = statuses_everywhere(api_client, admin, fee_id, last)

    assert due == {
        "detail": "due",
        "receipt": "due",
        "instalment": None,
        "list": "due",
        "filters": ["due"],
    }
    for found, expected in ((partial, "partial"), (paid, "paid")):
        assert found == {
            "detail": expected,
            "receipt": expected,
            "instalment": expected,
            "list": expected,
            "filters": [expected],
        }


# --- Outstanding balances are calculated correctly -------------------------------------


def test_outstanding_balances_are_calculated_correctly(db, api_client, world):
    admin = world["headers"]["admin"]

    ada, bola, chi = (
        detail(api_client, admin, world[k]["id"]).json()
        for k in ("ada_fee", "bola_fee", "chi_fee")
    )

    assert [
        (f["amount_billed"], f["amount_paid"], f["balance"]) for f in (ada, bola, chi)
    ] == [
        ("1000.00", "500.00", "500.00"),
        ("500.00", "0.00", "500.00"),
        ("800.00", "800.00", "0.00"),
    ]


def test_balances_use_exact_cents(db, api_client, world):
    admin = world["headers"]["admin"]
    fee = bill(api_client, admin, world["chi"], world["second"], billed="0.30")

    first = pay(api_client, admin, fee["id"], "0.10")["fee"]
    second = pay(api_client, admin, fee["id"], "0.20")["fee"]

    assert (first["amount_paid"], first["balance"], first["status"]) == (
        "0.10",
        "0.20",
        "partial",
    )
    assert (second["amount_paid"], second["balance"], second["status"]) == (
        "0.30",
        "0.00",
        "paid",
    )


def test_list_totals_equal_the_sum_of_the_bills(db, api_client, world):
    page = listing(api_client, world["headers"]["admin"]).json()

    assert page["total"] == 3
    assert page["totals"] == {
        "billed": "2300.00",
        "paid": "1300.00",
        "balance": "1000.00",
    }
    for key, field in (
        ("billed", "amount_billed"),
        ("paid", "amount_paid"),
        ("balance", "balance"),
    ):
        assert sum(float(i[field]) for i in page["items"]) == float(page["totals"][key])


# --- Parents can retrieve fee records only for linked students -------------------------


def test_parents_can_retrieve_fee_records_only_for_linked_students(
    db, api_client, world
):
    mum, pat, nobody = (
        world["headers"][k] for k in ("parent_ada", "parent_bola", "parent_unlinked")
    )
    ada, bola = world["ada_fee"], world["bola_fee"]

    assert ids(listing(api_client, mum).json()) == [ada["id"]]
    assert ids(listing(api_client, pat).json()) == [bola["id"]]
    assert listing(api_client, nobody).json()["items"] == []
    assert listing(api_client, mum).json()["totals"]["billed"] == "1000.00"
    missing = detail(api_client, mum, 999999)
    for response in (
        detail(api_client, mum, bola["id"]),
        receipt(api_client, mum, bola["id"]),
        instalment_receipt(api_client, mum, bola["id"], 1),
        detail(api_client, nobody, ada["id"]),
    ):
        assert response.status_code == 404
    assert detail(api_client, mum, bola["id"]).json() == missing.json()
    assert detail(api_client, mum, ada["id"]).status_code == 200
    assert receipt(api_client, mum, ada["id"]).status_code == 200
    assert (
        instalment_receipt(
            api_client, mum, ada["id"], world["ada_instalment"]["id"]
        ).status_code
        == 200
    )
    # Asking the list for another family's child reveals nothing either.
    assert listing(api_client, mum, student_id=world["bola"]).json()["items"] == []


def test_a_parent_loses_access_when_unlinked(db, api_client, world):
    admin, mum = world["headers"]["admin"], world["headers"]["parent_ada"]
    fee_id = world["ada_fee"]["id"]
    assert detail(api_client, mum, fee_id).status_code == 200

    response = api_client.delete(
        f"{STUDENTS}/{world['ada']}/parents/{world['mum_id']}", headers=admin
    )

    assert response.status_code == 204
    assert detail(api_client, mum, fee_id).status_code == 404
    assert listing(api_client, mum).json()["items"] == []


# --- Receipt numbers are unique ----------------------------------------------------------


def test_receipt_numbers_are_unique(db, api_client, world):
    admin = world["headers"]["admin"]
    created = []
    for term in (world["term"], world["second"]):
        for fee_type in FEE_TYPES:
            if term == world["term"] and fee_type == "Tuition Fee":
                continue  # Ada already has this one
            created.append(
                bill(api_client, admin, world["ada"], term, fee_type, billed="100")
            )
    for fee in created:
        pay(api_client, admin, fee["id"], "10")
        pay(api_client, admin, fee["id"], "10")

    bills, payments = [], []
    for fee in listing(api_client, admin).json()["items"]:
        full = detail(api_client, admin, fee["id"]).json()
        bills.append(full["receipt_number"])
        payments += [p["receipt_number"] for p in full["payments"]]

    assert len(bills) == 3 + len(created) and len(payments) == 3 + 2 * len(created)
    assert all(BILL_NUMBER.fullmatch(n) for n in bills)
    assert all(PAYMENT_NUMBER.fullmatch(n) for n in payments)
    assert len(set(bills)) == len(bills)
    assert len(set(payments)) == len(payments)
    assert not set(bills) & set(payments)


def test_a_refused_bill_does_not_reuse_or_duplicate_a_number(db, api_client, world):
    admin = world["headers"]["admin"]
    before = {f["receipt_number"] for f in listing(api_client, admin).json()["items"]}

    duplicate = post_bill(api_client, admin, world["ada"], world["term"])
    fresh = bill(api_client, admin, world["ada"], world["second"])

    assert duplicate.status_code == 409
    assert fresh["receipt_number"] not in before


# --- Receipt data includes the fields specified in FR-FEE-008 ---------------------------


def test_receipt_data_includes_the_fr_fee_008_fields(db, api_client, world):
    admin, ada = world["headers"]["admin"], world["ada_fee"]
    settings = get_settings()

    bill_receipt = receipt(api_client, admin, ada["id"]).json()
    instalment = instalment_receipt(
        api_client, admin, ada["id"], world["ada_instalment"]["id"]
    ).json()

    for body, number in (
        (bill_receipt, ada["receipt_number"]),
        (instalment, world["ada_instalment"]["receipt_number"]),
    ):
        assert body["receipt_number"] == number  # unique receipt/record number
        assert body["date"]  # date
        assert body["student"]["full_name"] == "Ada"  # student name
        assert body["student"]["class_name"] == "Primary 1"  # and class
        assert body["fee_type"] == "Tuition Fee"  # fee type
        assert body["term"]["name"] == "First Term"  # and term
        assert body["term"]["academic_year"] == "2025/2026"
        assert body["amount_billed"] == "1000.00"  # amount billed
        assert body["amount_paid"] == "500.00"  # amount paid
        assert body["balance"] == "500.00"  # outstanding balance
        assert body["status"] == "partial"  # payment status
        assert body["school"] == {  # school identity
            "name": settings.school_name,
            "location": settings.school_location,
        }
        assert body["signatures"] == {  # bursar signature and stamp placeholders
            "bursar_label": "Bursar",
            "stamp_label": "School stamp",
        }
    assert bill_receipt["payment"] is None
    assert instalment["payment"]["amount"] == "100.00"


# --- Financial writes are attributable to an authenticated user -------------------------


def audit_rows(db, action):
    return db.execute(
        select(AuditLog.__table__)
        .where(AuditLog.action == action)
        .order_by(AuditLog.__table__.c.id)
    ).all()


def test_financial_writes_are_attributable_to_the_admin_who_made_them(
    db, api_client, world
):
    second_id, second_admin = login(db, api_client, "admin", "second@camdock.test")
    fee = bill(api_client, second_admin, world["chi"], world["second"], billed="300")
    payment = pay(api_client, second_admin, fee["id"], "50")["payment"]

    (created,) = [r for r in audit_rows(db, "fee.create") if r.entity_id == fee["id"]]
    (paid,) = [
        r for r in audit_rows(db, "fee.payment.create") if r.entity_id == payment["id"]
    ]

    assert created.actor_user_id == second_id != world["admin_id"]
    assert paid.actor_user_id == second_id
    assert paid.changes["receipt_number"] == payment["receipt_number"]
    # The world's own writes were made by the first admin.
    assert {r.actor_user_id for r in audit_rows(db, "fee.create")} == {
        world["admin_id"],
        second_id,
    }
    assert count(db, FeeRecord) == len(audit_rows(db, "fee.create"))
    assert count(db, FeePayment) == len(audit_rows(db, "fee.payment.create"))


def test_refused_writes_leave_no_audit_row(db, api_client, world):
    audits = (
        len(audit_rows(db, "fee.create")),
        len(audit_rows(db, "fee.payment.create")),
    )
    admin, teacher = world["headers"]["admin"], world["headers"]["teacher"]

    post_bill(api_client, teacher, world["chi"], world["second"])
    post_bill(api_client, admin, world["ada"], world["term"])  # duplicate: 409
    post_bill(api_client, admin, world["chi"], world["second"], billed="-1")
    post_payment(api_client, teacher, world["ada_fee"]["id"], "10")
    post_payment(api_client, admin, world["ada_fee"]["id"], "99999")  # overpayment

    assert (
        len(audit_rows(db, "fee.create")),
        len(audit_rows(db, "fee.payment.create")),
    ) == audits


# --- Scope matrix: every fee endpoint x every caller ------------------------------------

ENDPOINTS = {
    "create_bill": lambda c, h, w: post_bill(c, h, w["chi"], w["second"]),
    "add_payment": lambda c, h, w: post_payment(c, h, w["bola_fee"]["id"], "10"),
    "list": lambda c, h, w: listing(c, h),
    "detail": lambda c, h, w: detail(c, h, w["ada_fee"]["id"]),
    "receipt": lambda c, h, w: receipt(c, h, w["ada_fee"]["id"]),
    "instalment_receipt": lambda c, h, w: instalment_receipt(
        c, h, w["ada_fee"]["id"], w["ada_instalment"]["id"]
    ),
    "fee_types": lambda c, h, w: c.get(f"{API}/fee-types", headers=h),
}
#                    create pay list detail receipt instalment types
EXPECTED = {
    "anonymous": (401, 401, 401, 401, 401, 401, 401),
    "teacher": (403, 403, 403, 403, 403, 403, 403),
    "parent_unlinked": (403, 403, 200, 404, 404, 404, 200),
    "parent_bola": (403, 403, 200, 404, 404, 404, 200),
    "parent_ada": (403, 403, 200, 200, 200, 200, 200),
    "admin": (201, 201, 200, 200, 200, 200, 200),
}
MATRIX = [
    (caller, endpoint, status)
    for caller, statuses in EXPECTED.items()
    for endpoint, status in zip(ENDPOINTS, statuses, strict=True)
]


@pytest.mark.parametrize(("caller", "endpoint", "status"), MATRIX)
def test_scope_matrix(api_client, world, caller, endpoint, status):
    response = ENDPOINTS[endpoint](api_client, world["headers"][caller], world)

    assert response.status_code == status, response.text
    if status >= 400:
        error = response.json()["error"]
        assert isinstance(error["code"], str) and isinstance(error["message"], str)


# --- Error contract and secrets -----------------------------------------------------------


def test_no_response_or_log_contains_secrets_or_other_families_data(
    api_client, world, caplog
):
    caplog.set_level(logging.DEBUG)
    h = world["headers"]
    responses = [
        ENDPOINTS[name](api_client, h[caller], world)
        for caller in EXPECTED
        for name in ENDPOINTS
    ]
    tokens = [v["Authorization"].split()[1] for v in h.values() if v]

    for response in responses:
        for forbidden in ("argon2", "password_hash", "token_hash", PASSWORD):
            assert forbidden not in response.text, response.text
    assert PASSWORD not in caplog.text
    assert not any(token in caplog.text for token in tokens)
    # Another family's child never appears in a parent's responses.
    parent_texts = " ".join(
        r.text
        for r in (
            listing(api_client, h["parent_ada"]),
            detail(api_client, h["parent_ada"], world["bola_fee"]["id"]),
            receipt(api_client, h["parent_ada"], world["chi_fee"]["id"]),
            detail(api_client, h["parent_ada"], world["ada_fee"]["id"]),
        )
    )
    assert "Bola" not in parent_texts and "Chi" not in parent_texts


# --- End to end, creating accounts through the API ----------------------------------------


def test_end_to_end_bill_pay_and_read_receipts(db, api_client):
    class_id = make_class(db, "Primary 3")
    term = make_terms(db, make_year(db, "2025/2026", date(2025, 9, 1)))["First Term"]
    _, admin = login(db, api_client, "admin", "bursar@camdock.test")

    def create_account(email, role):
        response = api_client.post(
            f"{API}/users",
            headers=admin,
            json={"email": email, "role": role, "password": PASSWORD},
        )
        assert response.status_code == 201, response.text
        return response.json()["id"], bearer(
            login_token(api_client, email=email, password=PASSWORD)
        )

    obi_id, obi = create_account("o.obi@camdock.test", "parent")
    _, eze = create_account("n.eze@camdock.test", "parent")
    student = api_client.post(
        STUDENTS, headers=admin, json={"full_name": "Ada Obi", "class_id": class_id}
    ).json()["id"]
    assert (
        api_client.post(
            f"{STUDENTS}/{student}/parents",
            headers=admin,
            json={"parent_user_id": obi_id, "relationship": "Mother"},
        ).status_code
        == 201
    )

    fee = bill(api_client, admin, student, term, billed="25000.50", amount_paid="10000")
    middle = pay(api_client, admin, fee["id"], "5000.25")["payment"]
    overpay = post_payment(api_client, admin, fee["id"], "10000.26")
    last = pay(api_client, admin, fee["id"], "10000.25")["payment"]

    mine = listing(api_client, obi).json()
    full = detail(api_client, obi, fee["id"]).json()
    bill_receipt = receipt(api_client, obi, fee["id"]).json()
    middle_receipt = instalment_receipt(api_client, obi, fee["id"], middle["id"]).json()
    last_receipt = instalment_receipt(api_client, obi, fee["id"], last["id"]).json()

    assert overpay.status_code == 422
    assert mine["total"] == 1 and mine["totals"] == {
        "billed": "25000.50",
        "paid": "25000.50",
        "balance": "0.00",
    }
    assert (full["status"], full["balance"]) == ("paid", "0.00")
    assert [p["amount"] for p in full["payments"]] == [
        "10000.00",
        "5000.25",
        "10000.25",
    ]
    assert bill_receipt["student"]["class_name"] == "Primary 3"
    assert (middle_receipt["amount_paid"], middle_receipt["balance"]) == (
        "15000.25",
        "10000.25",
    )
    assert (last_receipt["status"], last_receipt["balance"]) == ("paid", "0.00")
    assert (
        len({fee["receipt_number"], *(p["receipt_number"] for p in full["payments"])})
        == 4
    )
    # A different family's parent can see none of it.
    assert listing(api_client, eze).json()["items"] == []
    assert detail(api_client, eze, fee["id"]).status_code == 404
    assert receipt(api_client, eze, fee["id"]).status_code == 404
    assert api_client.get(f"{API}/fee-types", headers=obi).json()["items"] == list(
        FEE_TYPES
    )
