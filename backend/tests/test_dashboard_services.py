# ruff: noqa: F811  (pytest fixtures imported from other test modules)
from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import event, update
from sqlalchemy.orm import Session

from app.core.errors import AppError
from app.models import FeeRecord, SchoolClass, Student, Term, User
from app.models.fees import FEE_TYPES
from app.services import dashboard
from tests.test_access_scope import make_class, make_student, make_teacher
from tests.test_averages import admin, admin_login, averages_url, school  # noqa: F401
from tests.test_fee_receipts import bill, pay
from tests.test_teachers_api import login_headers
from tests.test_terms_api import make_terms, make_year

D = Decimal
TODAY = date(2026, 1, 15)


@pytest.fixture
def session(db):
    with Session(bind=db, join_transaction_mode="create_savepoint") as session:
        yield session


def set_dates(db, term_id, start, end):
    table = Term.__table__
    db.execute(
        update(table)
        .where(table.c.id == term_id)
        .values(start_date=start, end_date=end)
    )


def amounts(totals):
    return (str(totals.billed), str(totals.collected), str(totals.outstanding))


# --- resolve_term -------------------------------------------------------------------


@pytest.fixture
def year(db):
    year_id = make_year(db, "2025/2026", date(2025, 9, 1), is_current=True)
    return make_terms(db, year_id)


def test_a_given_term_is_returned_even_outside_the_current_year(db, session, year):
    other = make_terms(db, make_year(db, "2020/2021", date(2020, 9, 1)))

    term = dashboard.resolve_term(session, other["Second Term"], TODAY)

    assert (term.id, term.name, term.academic_year) == (
        other["Second Term"],
        "Second Term",
        "2020/2021",
    )


def test_an_unknown_term_is_a_validation_error(db, session, year):
    with pytest.raises(AppError) as error:
        dashboard.resolve_term(session, 999999, TODAY)
    assert error.value.status_code == 422


def test_the_default_is_the_term_containing_today(db, session, year):
    set_dates(db, year["First Term"], date(2025, 9, 1), date(2025, 12, 15))
    set_dates(db, year["Second Term"], date(2026, 1, 5), date(2026, 4, 1))
    set_dates(db, year["Third Term"], date(2026, 4, 20), date(2026, 7, 20))

    assert dashboard.resolve_term(session, None, TODAY).id == year["Second Term"]


def test_between_terms_the_default_is_the_latest_started(db, session, year):
    set_dates(db, year["First Term"], date(2025, 9, 1), date(2025, 12, 15))
    set_dates(db, year["Second Term"], date(2026, 2, 1), date(2026, 4, 1))

    assert dashboard.resolve_term(session, None, TODAY).id == year["First Term"]


def test_before_any_term_starts_the_default_is_the_first_by_name(db, session, year):
    for term_id in year.values():
        set_dates(db, term_id, date(2026, 9, 1), date(2026, 12, 1))

    assert dashboard.resolve_term(session, None, TODAY).id == year["First Term"]


def test_undated_terms_default_to_the_first_by_name(db, session, year):
    assert dashboard.resolve_term(session, None, TODAY).id == year["First Term"]


def test_a_current_year_without_terms_gives_none(db, session):
    make_year(db, "2025/2026", date(2025, 9, 1), is_current=True)

    assert dashboard.resolve_term(session, None, TODAY) is None


def test_no_current_year_gives_none(db, session):
    make_terms(db, make_year(db, "2025/2026", date(2025, 9, 1)))

    assert dashboard.resolve_term(session, None, TODAY) is None


# --- fee_totals ---------------------------------------------------------------------


@pytest.fixture
def fees(db, api_client, admin):
    p1, p2 = make_class(db, "Primary 1"), make_class(db, "Primary 2")
    terms = make_terms(db, make_year(db, "2025/2026", date(2025, 9, 1)))
    ctx = {
        "ada": make_student(db, p1, "S-ADA"),
        "ben": make_student(db, p2, "S-BEN"),
        "term": terms["First Term"],
    }
    second = {**ctx, "term": terms["Second Term"]}
    return {"ctx": ctx, "second": second, "admin": admin}


def test_no_bills_gives_zero_amounts(db, session):
    assert amounts(dashboard.fee_totals(session)) == ("0.00", "0.00", "0.00")


def test_totals_cover_paid_partial_and_due_bills(db, session, api_client, fees):
    ctx, headers = fees["ctx"], fees["admin"]
    bill(api_client, headers, ctx, "ada", "1000", amount_paid="1000")
    partial = bill(api_client, headers, ctx, "ben", "500.50")
    pay(api_client, headers, partial["id"], "200.25")
    bill(api_client, headers, ctx, "ben", "300", fee_type=FEE_TYPES[1])

    totals = dashboard.fee_totals(session)

    assert amounts(totals) == ("1800.50", "1200.25", "600.25")


def test_the_term_filter_separates_term_from_all_time(db, session, api_client, fees):
    ctx, second, headers = fees["ctx"], fees["second"], fees["admin"]
    bill(api_client, headers, ctx, "ada", "1000", amount_paid="400")
    bill(api_client, headers, second, "ada", "700", amount_paid="100")

    first = dashboard.fee_totals(session, FeeRecord.term_id == ctx["term"])
    both = dashboard.fee_totals(session)

    assert amounts(first) == ("1000.00", "400.00", "600.00")
    assert amounts(both) == ("1700.00", "500.00", "1200.00")


def test_a_student_filter_excludes_other_students(db, session, api_client, fees):
    ctx, headers = fees["ctx"], fees["admin"]
    bill(api_client, headers, ctx, "ada", "1000")
    bill(api_client, headers, ctx, "ben", "50")

    totals = dashboard.fee_totals(session, FeeRecord.student_id == ctx["ada"])

    assert amounts(totals) == ("1000.00", "0.00", "1000.00")


def test_bills_of_deactivated_students_still_count(db, session, api_client, fees):
    ctx, headers = fees["ctx"], fees["admin"]
    bill(api_client, headers, ctx, "ada", "1000")
    db.execute(update(Student).where(Student.id == ctx["ada"]).values(is_active=False))

    assert amounts(dashboard.fee_totals(session)) == ("1000.00", "0.00", "1000.00")


def test_fractional_amounts_sum_exactly(db, session, api_client, fees):
    ctx, headers = fees["ctx"], fees["admin"]
    for fee_type in FEE_TYPES[:3]:
        bill(api_client, headers, ctx, "ada", "0.10", fee_type=fee_type)

    assert dashboard.fee_totals(session).billed == D("0.30")


def test_totals_equal_what_the_fee_list_reports(db, session, api_client, fees):
    ctx, headers = fees["ctx"], fees["admin"]
    bill(api_client, headers, ctx, "ada", "1000", amount_paid="333.33")
    bill(api_client, headers, ctx, "ben", "250.10", amount_paid="0.07")

    listed = api_client.get(
        "/api/v1/fees", headers=headers, params={"term_id": ctx["term"]}
    ).json()["totals"]
    totals = dashboard.fee_totals(session, FeeRecord.term_id == ctx["term"])

    assert amounts(totals) == (listed["billed"], listed["paid"], listed["balance"])


def test_totals_use_a_fixed_number_of_queries(db, session, api_client, fees):
    ctx, headers = fees["ctx"], fees["admin"]
    statements: list[str] = []

    def record(conn, cursor, statement, *args):
        statements.append(statement)

    def queries() -> int:
        statements.clear()
        dashboard.fee_totals(session)
        return sum("fee_records" in s for s in statements)

    bill(api_client, headers, ctx, "ada", "10")
    event.listen(db, "before_cursor_execute", record)
    try:
        one = queries()
        for fee_type in FEE_TYPES[1:]:
            bill(api_client, headers, ctx, "ada", "10", fee_type=fee_type)
        five = queries()
    finally:
        event.remove(db, "before_cursor_execute", record)

    assert one == five == 1


# --- class_overall_averages ---------------------------------------------------------


def as_user(db, api_client, role):
    user_id, headers = login_headers(db, api_client, role, f"{role}@camdock.test")
    return User(id=user_id, role=role, is_active=True), headers


def test_every_class_matches_the_class_averages_endpoint(
    db,
    session,
    api_client,
    admin,
    admin_login,
    school,
):
    admin_user = User(id=admin_login[0], role="admin", is_active=True)

    averages = dashboard.class_overall_averages(session, admin_user, school["term"])

    for key in ("p1", "p2", "p3"):
        endpoint = api_client.get(
            averages_url(school[key]), headers=admin, params={"term_id": school["term"]}
        ).json()["overall_average"]
        expected = None if endpoint is None else D(endpoint)
        assert averages[school[key]] == expected, key
    assert averages[school["p1"]] == D("60.9")
    assert averages[school["p2"]] == D("70.0")


def test_a_class_without_results_is_none_not_zero(db, session, admin_login, school):
    admin_user = User(id=admin_login[0], role="admin", is_active=True)

    averages = dashboard.class_overall_averages(session, admin_user, school["term"])

    assert averages[school["p3"]] is None


def test_other_terms_and_inactive_classes_are_excluded(
    db, session, admin_login, school
):
    admin_user = User(id=admin_login[0], role="admin", is_active=True)
    table = SchoolClass.__table__
    db.execute(update(table).where(table.c.id == school["p2"]).values(is_active=False))

    first = dashboard.class_overall_averages(session, admin_user, school["term"])
    second = dashboard.class_overall_averages(session, admin_user, school["term2"])

    assert school["p2"] not in first
    assert first[school["p1"]] == D("60.9")
    assert second[school["p1"]] == D("10.0")


def test_a_teacher_only_gets_assigned_classes(db, session, api_client, school):
    user, _ = as_user(db, api_client, "teacher")
    make_teacher(db, user, classes=[school["p2"]])

    averages = dashboard.class_overall_averages(session, user, school["term"])

    assert set(averages) == {school["p2"]}


def test_class_averages_use_a_fixed_number_of_queries(db, session, admin_login, school):
    admin_user = User(id=admin_login[0], role="admin", is_active=True)
    statements: list[str] = []

    def record(conn, cursor, statement, *args):
        statements.append(statement)

    def queries() -> int:
        statements.clear()
        dashboard.class_overall_averages(session, admin_user, school["term"])
        return len([s for s in statements if s.startswith("SELECT")])

    event.listen(db, "before_cursor_execute", record)
    try:
        few = queries()
        for number in range(4, 10):
            make_class(db, f"Primary {number}")
        many = queries()
    finally:
        event.remove(db, "before_cursor_execute", record)

    assert few == many == 2
