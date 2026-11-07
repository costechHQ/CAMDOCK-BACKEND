# ruff: noqa: F811

import pytest
from sqlalchemy import event, func, insert, select, update

from app.models import AcademicYear, AuditLog, SchoolClass, TeacherClass, Term, User
from tests.test_access_scope import make_class, make_student, make_teacher
from tests.test_averages import admin, admin_login, averages_url, school  # noqa: F401
from tests.test_teachers_api import login_headers

URL = "/api/v1/dashboard/overview"
KEYS = {"role", "title", "term", "students", "classes"}


def overview(api_client, headers, term=None):
    params = {} if term is None else {"term_id": term}
    return api_client.get(URL, headers=headers, params=params)


def teacher_for(db, api_client, classes, email="teach@camdock.test"):
    user_id, headers = login_headers(db, api_client, "teacher", email)
    make_teacher(db, User(id=user_id, role="teacher"), classes)
    return headers


def named(body):
    return {
        c["school_class"]["name"]: (c["students"], c["overall_average"])
        for c in body["classes"]
    }


def test_a_teacher_sees_only_their_assigned_classes(db, api_client, school):
    mine = teacher_for(db, api_client, [school["p1"]])
    teacher_for(db, api_client, [school["p2"]], "other@camdock.test")

    response = overview(api_client, mine, school["term"])

    assert response.status_code == 200
    body = response.json()
    assert set(body) == KEYS
    assert body["role"] == "teacher"
    assert body["title"] == "My overview"
    assert body["term"]["name"] == "First Term"
    assert body["students"] == 3  # Ada, Bola, Dave; inactive Eve is not counted
    assert named(body) == {"Primary 1": (3, "60.9")}


def test_two_classes_sum_up_and_are_ordered_by_name(db, api_client, school):
    headers = teacher_for(db, api_client, [school["p2"], school["p1"], school["p3"]])

    body = overview(api_client, headers, school["term"]).json()

    assert [c["school_class"]["name"] for c in body["classes"]] == [
        "Primary 1",
        "Primary 2",
        "Primary 3",
    ]
    assert named(body) == {
        "Primary 1": (3, "60.9"),
        "Primary 2": (1, "70.0"),
        "Primary 3": (0, None),
    }
    assert body["students"] == 4


def test_averages_equal_the_class_endpoint_for_that_teacher(db, api_client, school):
    headers = teacher_for(db, api_client, [school["p1"], school["p2"]])

    for entry in overview(api_client, headers, school["term"]).json()["classes"]:
        endpoint = api_client.get(
            averages_url(entry["school_class"]["id"]),
            headers=headers,
            params={"term_id": school["term"]},
        ).json()["overall_average"]
        assert entry["overall_average"] == endpoint


def test_inactive_classes_are_not_listed(db, api_client, school):
    headers = teacher_for(db, api_client, [school["p1"], school["p2"]])
    db.execute(
        update(SchoolClass)
        .where(SchoolClass.id == school["p2"])
        .values(is_active=False)
    )

    body = overview(api_client, headers, school["term"]).json()

    assert list(named(body)) == ["Primary 1"]
    assert body["students"] == 3


def test_there_are_no_fee_or_school_wide_figures(db, api_client, school):
    headers = teacher_for(db, api_client, [school["p1"]])

    response = overview(api_client, headers, school["term"])

    assert set(response.json()) == KEYS
    text = response.text.lower()
    for forbidden in ("fee", "balance", "billed", "collected", "outstanding"):
        assert forbidden not in text
    assert "active_classes" not in text
    assert "teachers" not in text


def test_a_teacher_with_no_classes_gets_an_empty_valid_body(db, api_client, school):
    headers = teacher_for(db, api_client, [])

    body = overview(api_client, headers, school["term"]).json()

    assert (body["students"], body["classes"]) == (0, [])
    assert body["term"]["name"] == "First Term"


def test_a_deactivated_teacher_record_gets_an_empty_body(db, api_client, school):
    user_id, headers = login_headers(db, api_client, "teacher", "gone@camdock.test")
    make_teacher(
        db,
        User(id=user_id, role="teacher"),
        [school["p1"]],
        is_active=False,
    )

    body = overview(api_client, headers, school["term"]).json()

    assert (body["students"], body["classes"]) == (0, [])


def test_a_teacher_account_without_a_teacher_record_gets_an_empty_body(
    db, api_client, school
):
    headers = login_headers(db, api_client, "teacher", "norecord@camdock.test")[1]

    response = overview(api_client, headers, school["term"])

    assert response.status_code == 200
    assert response.json()["classes"] == []


def test_the_term_changes_averages_but_not_the_class_list(db, api_client, school):
    headers = teacher_for(db, api_client, [school["p1"], school["p2"]])

    first = overview(api_client, headers, school["term"]).json()
    second = overview(api_client, headers, school["term2"]).json()

    assert second["term"]["name"] == "Second Term"
    assert list(named(first)) == list(named(second))
    assert named(second) == {"Primary 1": (3, "10.0"), "Primary 2": (1, None)}


def test_the_default_term_is_used_when_none_is_given(db, api_client, school):
    headers = teacher_for(db, api_client, [school["p1"]])
    year_id = db.execute(
        select(Term.academic_year_id).where(Term.id == school["term2"])
    ).scalar_one()
    db.execute(
        update(AcademicYear).where(AcademicYear.id == year_id).values(is_current=True)
    )

    assert overview(api_client, headers).json()["term"]["name"] == "First Term"


def test_without_any_term_the_averages_are_null_and_classes_remain(
    db, api_client, school
):
    headers = teacher_for(db, api_client, [school["p1"]])

    body = overview(api_client, headers).json()  # no academic year is current

    assert body["term"] is None
    assert named(body) == {"Primary 1": (3, None)}


@pytest.mark.parametrize("value", ["999999", "abc", "0", "-1"])
def test_a_bad_term_is_rejected(db, api_client, school, value):
    headers = teacher_for(db, api_client, [school["p1"]])

    response = api_client.get(URL, headers=headers, params={"term_id": value})

    assert response.status_code == 422


def test_anonymous_users_are_still_refused(db, api_client, school):

    assert api_client.get(URL).status_code == 401


def test_the_query_count_does_not_grow_with_classes(db, api_client, school):
    user_id, headers = login_headers(db, api_client, "teacher", "many@camdock.test")
    teacher_id = make_teacher(db, User(id=user_id, role="teacher"), [school["p1"]])
    statements: list[str] = []

    def record(conn, cursor, statement, *args):
        statements.append(statement)

    def queries() -> int:
        statements.clear()
        overview(api_client, headers, school["term"])
        return len([s for s in statements if s.startswith("SELECT")])

    event.listen(db, "before_cursor_execute", record)
    try:
        few = queries()
        for number in range(4, 9):
            class_id = make_class(db, f"Primary {number}")
            make_student(db, class_id, f"S-X{number}")
            db.execute(
                insert(TeacherClass).values(teacher_id=teacher_id, class_id=class_id)
            )
        many = queries()
    finally:
        event.remove(db, "before_cursor_execute", record)

    assert few == many


def test_an_admin_still_gets_the_admin_shape(db, api_client, admin, school):
    body = overview(api_client, admin, school["term"]).json()

    assert body["role"] == "admin"
    assert {"fees", "teachers", "active_classes"} <= set(body)
    assert "title" not in body


def test_openapi_documents_every_shape(db, api_client):
    schema = api_client.app.openapi()
    response = schema["paths"]["/api/v1/dashboard/overview"]["get"]["responses"]["200"]
    refs = {
        option["$ref"].rsplit("/", 1)[-1]
        for option in response["content"]["application/json"]["schema"]["oneOf"]
    }

    assert refs == {"AdminOverview", "TeacherOverview", "ParentOverview"}


def test_no_secrets_and_no_audit_rows(db, api_client, school):
    headers = teacher_for(db, api_client, [school["p1"]])
    before = db.execute(select(func.count()).select_from(AuditLog)).scalar_one()

    text = overview(api_client, headers, school["term"]).text.lower()

    assert db.execute(select(func.count()).select_from(AuditLog)).scalar_one() == before
    for forbidden in ("password", "hash", "token", "@camdock.test"):
        assert forbidden not in text
