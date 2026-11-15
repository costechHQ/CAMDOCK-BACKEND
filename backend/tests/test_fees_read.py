from datetime import date
from decimal import Decimal
from types import SimpleNamespace

import pytest
from sqlalchemy import event, update

from app.models import FeePayment, Student
from app.models.fees import FEE_TYPES
from app.services import money
from tests.test_access_scope import link, make_class, make_student
from tests.test_teachers_api import login_headers
from tests.test_terms_api import make_terms, make_year

FEES = "/api/v1/fees"


@pytest.fixture
def admin(db, api_client):
    return login_headers(db, api_client, "admin")[1]


@pytest.fixture
def school(db):
    """Ada (Primary 1) and Ben (Primary 2); mum is Ada's parent, dad is Ben's, and
    nobody has no children linked."""
    p1, p2 = make_class(db, "Primary 1"), make_class(db, "Primary 2")
    terms = make_terms(db, make_year(db, "2025/2026", date(2025, 9, 1)))
    return {
        "p1": p1,
        "p2": p2,
        "ada": make_student(db, p1, "S-ADA"),
        "ben": make_student(db, p2, "S-BEN"),
        "first": terms["First Term"],
        "second": terms["Second Term"],
    }


@pytest.fixture
def parents(db, api_client, school):
    mum_id, mum = login_headers(db, api_client, "parent", email="mum@camdock.test")
    dad_id, dad = login_headers(db, api_client, "parent", email="dad@camdock.test")
    _, nobody = login_headers(db, api_client, "parent", email="nobody@camdock.test")
    link(db, SimpleNamespace(id=mum_id), school["ada"])
    link(db, SimpleNamespace(id=dad_id), school["ben"])
    return {"mum": mum, "dad": dad, "nobody": nobody}


def bill(
    api_client, admin, student, term, fee_type="Tuition Fee", billed="1000", paid=None
):
    body = {
        "student_id": student,
        "term_id": term,
        "fee_type": fee_type,
        "amount_billed": billed,
    }
    if paid is not None:
        body["amount_paid"] = paid
    response = api_client.post(FEES, headers=admin, json=body)
    assert response.status_code == 201, response.text
    return response.json()


def listing(api_client, headers, **params):
    response = api_client.get(FEES, headers=headers, params=params)
    assert response.status_code == 200, response.text
    return response.json()


def ids(page) -> list[int]:
    return [item["id"] for item in page["items"]]


# --- detail -------------------------------------------------------------------------


def test_admin_reads_a_bill_with_its_payments_in_order(db, api_client, admin, school):
    fee = bill(api_client, admin, school["ada"], school["first"], paid="400")
    api_client.post(
        f"{FEES}/{fee['id']}/payments", headers=admin, json={"amount": "100"}
    )

    response = api_client.get(f"{FEES}/{fee['id']}", headers=admin)

    assert response.status_code == 200
    out = response.json()
    assert out["id"] == fee["id"]
    assert out["student"]["student_number"] == "S-ADA"
    assert out["term"]["name"] == "First Term"
    assert (out["amount_paid"], out["balance"], out["status"]) == (
        "500.00",
        "500.00",
        "partial",
    )
    assert [p["amount"] for p in out["payments"]] == ["400.00", "100.00"]


def test_a_parent_reads_their_childs_bill_but_not_another_familys(
    db, api_client, admin, school, parents
):
    ada = bill(api_client, admin, school["ada"], school["first"])
    ben = bill(api_client, admin, school["ben"], school["first"])

    own = api_client.get(f"{FEES}/{ada['id']}", headers=parents["mum"])
    other = api_client.get(f"{FEES}/{ben['id']}", headers=parents["mum"])
    missing = api_client.get(f"{FEES}/999999", headers=parents["mum"])

    assert own.status_code == 200 and own.json()["id"] == ada["id"]
    assert other.status_code == missing.status_code == 404
    assert other.json() == missing.json()
    assert other.json()["error"]["message"] == "Fee record not found."


def test_unknown_and_malformed_ids(db, api_client, admin, school):
    assert api_client.get(f"{FEES}/999999", headers=admin).status_code == 404
    assert api_client.get(f"{FEES}/abc", headers=admin).status_code == 422


def test_a_teacher_and_anonymous_cannot_read_fees(db, api_client, admin, school):
    fee = bill(api_client, admin, school["ada"], school["first"])
    teacher = login_headers(db, api_client, "teacher")[1]

    for path in (FEES, f"{FEES}/{fee['id']}"):
        assert api_client.get(path, headers=teacher).status_code == 403
        assert api_client.get(path).status_code == 401


# --- list: scope --------------------------------------------------------------------


def test_admin_sees_every_bill_and_a_parent_only_their_childrens(
    db, api_client, admin, school, parents
):
    ada = bill(api_client, admin, school["ada"], school["first"], billed="300")
    ben = bill(api_client, admin, school["ben"], school["first"], billed="700")

    everyone = listing(api_client, admin)
    mum = listing(api_client, parents["mum"])

    assert sorted(ids(everyone)) == sorted([ada["id"], ben["id"]])
    assert everyone["total"] == 2 and everyone["totals"]["billed"] == "1000.00"
    assert ids(mum) == [ada["id"]]
    assert mum["total"] == 1
    assert mum["totals"] == {"billed": "300.00", "paid": "0.00", "balance": "300.00"}


def test_a_parent_without_children_gets_an_empty_page(
    db, api_client, admin, school, parents
):
    bill(api_client, admin, school["ada"], school["first"])

    page = listing(api_client, parents["nobody"])

    assert page["items"] == [] and page["total"] == 0
    assert page["totals"] == {"billed": "0.00", "paid": "0.00", "balance": "0.00"}


def test_a_parent_filtering_by_another_familys_child_gets_nothing(
    db, api_client, admin, school, parents
):
    bill(api_client, admin, school["ben"], school["first"])

    by_student = listing(api_client, parents["mum"], student_id=school["ben"])
    by_class = listing(api_client, parents["mum"], class_id=school["p2"])

    assert by_student["items"] == [] and by_student["total"] == 0
    assert by_class["items"] == [] and by_class["total"] == 0


# --- list: filters ------------------------------------------------------------------


@pytest.fixture
def many(db, api_client, admin, school):
    """Five bills across both students, both terms and several types and statuses."""
    return {
        "ada_tuition_due": bill(
            api_client, admin, school["ada"], school["first"], billed="1000"
        ),
        "ada_levy_partial": bill(
            api_client,
            admin,
            school["ada"],
            school["first"],
            "Development Levy",
            billed="500",
            paid="200",
        ),
        "ada_tuition_t2_paid": bill(
            api_client,
            admin,
            school["ada"],
            school["second"],
            billed="1000",
            paid="1000",
        ),
        "ben_tuition_partial": bill(
            api_client, admin, school["ben"], school["first"], billed="800", paid="0.01"
        ),
        "ben_exam_due": bill(
            api_client,
            admin,
            school["ben"],
            school["second"],
            "Examination Fee",
            billed="50",
        ),
    }


def only(page, many, *names) -> bool:
    return sorted(ids(page)) == sorted(many[n]["id"] for n in names)


def test_each_filter_on_its_own(db, api_client, admin, school, many):
    assert only(
        listing(api_client, admin, student_id=school["ada"]),
        many,
        "ada_tuition_due",
        "ada_levy_partial",
        "ada_tuition_t2_paid",
    )
    assert only(
        listing(api_client, admin, term_id=school["second"]),
        many,
        "ada_tuition_t2_paid",
        "ben_exam_due",
    )
    assert only(
        listing(api_client, admin, class_id=school["p2"]),
        many,
        "ben_tuition_partial",
        "ben_exam_due",
    )
    assert only(
        listing(api_client, admin, fee_type="Development Levy"),
        many,
        "ada_levy_partial",
    )
    assert only(listing(api_client, admin, status="paid"), many, "ada_tuition_t2_paid")
    assert only(
        listing(api_client, admin, status="partial"),
        many,
        "ada_levy_partial",
        "ben_tuition_partial",
    )
    assert only(
        listing(api_client, admin, status="due"),
        many,
        "ada_tuition_due",
        "ben_exam_due",
    )


def test_filters_combine_with_and(db, api_client, admin, school, many):
    page = listing(
        api_client,
        admin,
        student_id=school["ada"],
        term_id=school["first"],
        status="partial",
    )

    assert only(page, many, "ada_levy_partial")
    assert page["total"] == 1


def test_a_filter_with_no_match_is_empty_not_an_error(
    db, api_client, admin, school, many
):
    page = listing(api_client, admin, fee_type="PTA Levy")

    assert page["items"] == [] and page["total"] == 0
    assert page["totals"] == {"billed": "0.00", "paid": "0.00", "balance": "0.00"}


@pytest.mark.parametrize(
    "params",
    [
        {"fee_type": "Tuition"},
        {"status": "overdue"},
        {"status": "PAID"},
        {"student_id": "abc"},
        {"term_id": "x"},
        {"class_id": 0},
        {"student_id": -1},
        {"limit": 0},
        {"limit": 101},
        {"offset": -1},
        {"limit": "ten"},
    ],
)
def test_invalid_filters_are_rejected(db, api_client, admin, school, params):
    response = api_client.get(FEES, headers=admin, params=params)

    assert response.status_code == 422


def test_every_fee_type_is_an_accepted_filter(db, api_client, admin, school):
    for fee_type in FEE_TYPES:
        assert listing(api_client, admin, fee_type=fee_type)["total"] == 0


def test_bills_of_a_deactivated_student_stay_visible(
    db, api_client, admin, school, parents
):
    fee = bill(api_client, admin, school["ada"], school["first"])
    db.execute(
        update(Student).where(Student.id == school["ada"]).values(is_active=False)
    )

    assert ids(listing(api_client, admin)) == [fee["id"]]
    assert ids(listing(api_client, parents["mum"])) == [fee["id"]]
    assert api_client.get(f"{FEES}/{fee['id']}", headers=admin).status_code == 200


# --- list: status filter agrees with money.status -------------------------------------


@pytest.mark.parametrize(
    "paid",
    ["0", "0.01", "499.99", "500", "999.99", "1000"],
)
def test_the_sql_status_filter_agrees_with_money_status(
    db, api_client, admin, school, paid
):
    """The status rule exists twice (money.status and the SQL filter); at each boundary
    the bill must appear under exactly the status `money.status` gives it."""
    billed = Decimal("1000.00")
    fee = bill(
        api_client, admin, school["ada"], school["first"], billed="1000", paid=paid
    )
    expected = money.status(billed, Decimal(paid))

    for status in (money.PAID, money.PARTIAL, money.DUE):
        found = fee["id"] in ids(listing(api_client, admin, status=status))
        assert found == (status == expected), (paid, status)
    assert listing(api_client, admin)["items"][0]["status"] == expected


# --- list: totals, order, pagination --------------------------------------------------


def test_totals_are_exact_decimals(db, api_client, admin, school):
    bill(api_client, admin, school["ada"], school["first"], billed="0.10", paid="0.10")
    bill(
        api_client,
        admin,
        school["ada"],
        school["first"],
        "PTA Levy",
        billed="0.20",
        paid="0.05",
    )

    totals = listing(api_client, admin)["totals"]

    assert totals == {"billed": "0.30", "paid": "0.15", "balance": "0.15"}


def test_totals_cover_the_whole_filtered_set_not_the_page(
    db, api_client, admin, school
):
    for index, fee_type in enumerate(FEE_TYPES):
        bill(
            api_client,
            admin,
            school["ada"],
            school["first"],
            fee_type,
            billed=str(100 * (index + 1)),
            paid="10",
        )

    page = listing(api_client, admin, limit=2)

    assert len(page["items"]) == 2 and page["total"] == 5
    assert page["totals"] == {
        "billed": "1500.00",
        "paid": "50.00",
        "balance": "1450.00",
    }


def test_payments_added_later_change_the_list_and_totals(db, api_client, admin, school):
    fee = bill(api_client, admin, school["ada"], school["first"], billed="100")

    api_client.post(
        f"{FEES}/{fee['id']}/payments", headers=admin, json={"amount": "40"}
    )
    page = listing(api_client, admin)

    item = page["items"][0]
    assert (item["amount_paid"], item["balance"], item["status"]) == (
        "40.00",
        "60.00",
        "partial",
    )
    assert page["totals"]["paid"] == "40.00"
    assert "payments" not in item


def test_newest_bill_first_and_pages_do_not_overlap(db, api_client, admin, school):
    created = [
        bill(api_client, admin, school["ada"], school["first"], fee_type)["id"]
        for fee_type in FEE_TYPES
    ]

    first = listing(api_client, admin, limit=2, offset=0)
    second = listing(api_client, admin, limit=2, offset=2)
    third = listing(api_client, admin, limit=2, offset=4)

    assert ids(first) + ids(second) + ids(third) == sorted(created, reverse=True)
    assert [len(p["items"]) for p in (first, second, third)] == [2, 2, 1]
    assert {p["total"] for p in (first, second, third)} == {5}
    assert (first["limit"], first["offset"]) == (2, 0)


def test_an_offset_past_the_end_is_empty_with_the_right_total(
    db, api_client, admin, school, many
):
    page = listing(api_client, admin, offset=50)

    assert page["items"] == [] and page["total"] == 5
    assert page["totals"]["billed"] == "3350.00"


def test_the_default_page_size_is_twenty(db, api_client, admin, school):
    page = listing(api_client, admin)

    assert (page["limit"], page["offset"]) == (20, 0)


def test_the_list_runs_a_fixed_number_of_queries(db, api_client, admin, school):
    statements: list[str] = []

    def record(conn, cursor, statement, *args):
        statements.append(statement)

    def fee_queries() -> int:
        statements.clear()
        listing(api_client, admin)
        return sum("fee_records" in s for s in statements)

    bill(api_client, admin, school["ada"], school["first"], FEE_TYPES[0])
    event.listen(db, "before_cursor_execute", record)
    try:
        one = fee_queries()
        for fee_type in FEE_TYPES[1:]:
            bill(api_client, admin, school["ada"], school["first"], fee_type)
        five = fee_queries()
    finally:
        event.remove(db, "before_cursor_execute", record)

    assert one == five == 2


# --- safety ---------------------------------------------------------------------------


def test_responses_never_contain_secrets(db, api_client, admin, school, many):
    fee_id = many["ada_tuition_due"]["id"]
    texts = [
        api_client.get(FEES, headers=admin).text,
        api_client.get(f"{FEES}/{fee_id}", headers=admin).text,
    ]

    for text in texts:
        for forbidden in ("password", "hash", "token"):
            assert forbidden not in text.lower()


def test_reading_writes_nothing(db, api_client, admin, school, many):
    from sqlalchemy import func, select

    from app.models import AuditLog

    before = db.execute(select(func.count()).select_from(AuditLog)).scalar_one()
    payments = db.execute(select(func.count()).select_from(FeePayment)).scalar_one()

    listing(api_client, admin)
    api_client.get(f"{FEES}/{many['ada_tuition_due']['id']}", headers=admin)

    assert db.execute(select(func.count()).select_from(AuditLog)).scalar_one() == before
    assert (
        db.execute(select(func.count()).select_from(FeePayment)).scalar_one()
        == payments
    )
