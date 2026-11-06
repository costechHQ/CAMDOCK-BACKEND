# ruff: noqa: F811
from datetime import date, timedelta

import pytest
from sqlalchemy import event, func, select, update

from app.models import (
    AcademicYear,
    AuditLog,
    SchoolClass,
    Student,
    Teacher,
    Term,
)
from app.models.fees import FEE_TYPES
from tests.test_access_scope import add, make_class, make_student
from tests.test_averages import admin, admin_login, averages_url, school  # noqa: F401
from tests.test_fee_receipts import bill
from tests.test_teachers_api import login_headers
from tests.test_terms_api import make_year

URL = "/api/v1/dashboard/overview"


def overview(api_client, headers, term=None, **params):
    if term is not None:
        params["term_id"] = term
    return api_client.get(URL, headers=headers, params=params)


def fee_data(api_client, admin, school):
    """Ada: 1000 billed, 400 paid in the first term, plus 700 billed (100 paid) in the
    second. Chi: 250 billed, nothing paid, first term."""
    bill(api_client, admin, school, "ada", "1000", amount_paid="400")
    bill(
        api_client,
        admin,
        {**school, "term": school["term2"]},
        "ada",
        "700",
        amount_paid="100",
    )
    bill(api_client, admin, school, "chi", "250.50", fee_type=FEE_TYPES[1])


# --- empty and populated ------------------------------------------------------------


def test_an_empty_school_gives_a_valid_empty_overview(db, api_client, admin_login):
    response = overview(api_client, admin_login[1])

    assert response.status_code == 200
    assert response.json() == {
        "role": "admin",
        "term": None,
        "students": 0,
        "teachers": 0,
        "active_classes": 0,
        "fees": {
            "term": None,
            "all_time": {"billed": "0.00", "collected": "0.00", "outstanding": "0.00"},
        },
        "class_averages": [],
    }


def test_a_populated_school_reports_every_figure(db, api_client, admin, school):
    add(db, Teacher, full_name="Teach One")
    add(db, Teacher, full_name="Teach Two", is_active=False)
    fee_data(api_client, admin, school)

    body = overview(api_client, admin, school["term"]).json()

    assert body["role"] == "admin"
    assert body["term"]["name"] == "First Term"
    assert (body["students"], body["teachers"], body["active_classes"]) == (4, 1, 2)
    assert body["fees"] == {
        "term": {"billed": "1250.50", "collected": "400.00", "outstanding": "850.50"},
        "all_time": {
            "billed": "1950.50",
            "collected": "500.00",
            "outstanding": "1450.50",
        },
    }
    assert [
        (c["school_class"]["name"], c["overall_average"])
        for c in body["class_averages"]
    ] == [("Primary 1", "60.9"), ("Primary 2", "70.0"), ("Primary 3", None)]


def test_class_averages_equal_the_class_endpoint(db, api_client, admin, school):
    entries = overview(api_client, admin, school["term"]).json()["class_averages"]

    assert len(entries) == 3
    for entry in entries:
        endpoint = api_client.get(
            averages_url(entry["school_class"]["id"]),
            headers=admin,
            params={"term_id": school["term"]},
        ).json()["overall_average"]
        assert entry["overall_average"] == endpoint


def test_active_classes_means_classes_holding_an_active_student(
    db, api_client, admin, school
):
    only_inactive = make_class(db, "Primary 4")
    ghost = make_student(db, only_inactive, "S-GHOST")
    db.execute(update(Student).where(Student.id == ghost).values(is_active=False))
    retired = make_class(db, "Primary 5")
    make_student(db, retired, "S-RETIRED")
    db.execute(
        update(SchoolClass).where(SchoolClass.id == retired).values(is_active=False)
    )

    body = overview(api_client, admin, school["term"]).json()

    assert body["active_classes"] == 2  # Primary 1 and Primary 2 only
    assert len(body["class_averages"]) == 4  # Primary 1..4 are active classes


# --- term handling ------------------------------------------------------------------


def test_the_term_changes_term_figures_but_not_all_time(db, api_client, admin, school):
    fee_data(api_client, admin, school)

    first = overview(api_client, admin, school["term"]).json()
    second = overview(api_client, admin, school["term2"]).json()

    assert second["term"]["name"] == "Second Term"
    assert second["fees"]["term"] == {
        "billed": "700.00",
        "collected": "100.00",
        "outstanding": "600.00",
    }
    assert second["fees"]["all_time"] == first["fees"]["all_time"]
    averages = {
        c["school_class"]["name"]: c["overall_average"]
        for c in second["class_averages"]
    }
    assert averages == {"Primary 1": "10.0", "Primary 2": None, "Primary 3": None}


def test_the_default_term_is_the_one_containing_today(db, api_client, admin, school):
    today = date.today()
    year_id = db.execute(
        select(Term.academic_year_id).where(Term.id == school["term2"])
    ).scalar_one()
    db.execute(
        update(AcademicYear).where(AcademicYear.id == year_id).values(is_current=True)
    )
    db.execute(
        update(Term)
        .where(Term.id == school["term2"])
        .values(
            start_date=today - timedelta(days=10), end_date=today + timedelta(days=10)
        )
    )

    body = overview(api_client, admin).json()

    assert body["term"]["id"] == school["term2"]


@pytest.mark.parametrize("value", ["999999", "abc", "0", "-1"])
def test_a_bad_term_is_rejected(db, api_client, admin, school, value):
    assert overview(api_client, admin, term=value).status_code == 422


def test_a_current_year_without_terms_still_gives_all_time_fees(
    db, api_client, admin, school
):
    fee_data(api_client, admin, school)
    make_year(db, "2026/2027", date(2026, 9, 1), is_current=True)

    body = overview(api_client, admin).json()

    assert body["term"] is None
    assert body["fees"]["term"] is None
    assert body["fees"]["all_time"]["billed"] == "1950.50"


def test_no_default_term_gives_null_term_figures(db, api_client, admin, school):
    fee_data(api_client, admin, school)  # no academic year is current in this fixture

    body = overview(api_client, admin).json()

    assert body["term"] is None
    assert body["fees"]["term"] is None
    assert body["fees"]["all_time"]["billed"] == "1950.50"
    assert all(c["overall_average"] is None for c in body["class_averages"])
    assert len(body["class_averages"]) == 3


def test_bills_of_a_deactivated_student_still_count(db, api_client, admin, school):
    fee_data(api_client, admin, school)
    db.execute(
        update(Student).where(Student.id == school["ada"]).values(is_active=False)
    )

    body = overview(api_client, admin, school["term"]).json()

    assert body["fees"]["term"]["billed"] == "1250.50"


# --- access -------------------------------------------------------------------------


def test_teachers_parents_and_anonymous_users_are_refused(db, api_client, admin_login):
    teacher = login_headers(db, api_client, "teacher")[1]
    parent = login_headers(db, api_client, "parent")[1]

    assert overview(api_client, teacher).status_code == 403
    assert overview(api_client, parent).status_code == 403
    assert api_client.get(URL).status_code == 401


# --- safety and cost ----------------------------------------------------------------


def test_the_query_count_does_not_grow_with_classes(db, api_client, admin, school):
    statements: list[str] = []

    def record(conn, cursor, statement, *args):
        statements.append(statement)

    def queries() -> int:
        statements.clear()
        overview(api_client, admin, school["term"])
        return len([s for s in statements if s.startswith("SELECT")])

    event.listen(db, "before_cursor_execute", record)
    try:
        few = queries()
        for number in range(4, 10):
            make_student(db, make_class(db, f"Primary {number}"), f"S-X{number}")
        many = queries()
    finally:
        event.remove(db, "before_cursor_execute", record)

    assert few == many


def test_the_response_never_contains_secrets(db, api_client, admin, school):
    fee_data(api_client, admin, school)
    text = overview(api_client, admin, school["term"]).text.lower()

    for forbidden in ("password", "hash", "token", "@camdock.test"):
        assert forbidden not in text


def test_reading_writes_nothing(db, api_client, admin, school):
    before = db.execute(select(func.count()).select_from(AuditLog)).scalar_one()

    overview(api_client, admin, school["term"])

    assert db.execute(select(func.count()).select_from(AuditLog)).scalar_one() == before
