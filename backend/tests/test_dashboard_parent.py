# ruff: noqa: F811
import pytest
from sqlalchemy import delete, event, func, insert, select, update

from app.models import AuditLog, ParentStudent, SchoolClass, Student, User
from app.models.fees import FEE_TYPES
from tests.test_access_scope import link, make_class, make_student
from tests.test_averages import admin, admin_login, averages_url, school  # noqa: F401
from tests.test_fee_receipts import bill
from tests.test_teachers_api import login_headers

URL = "/api/v1/dashboard/overview"
KEYS = {"role", "term", "children"}
CHILD_KEYS = {
    "student",
    "school_class",
    "student_average",
    "class_average",
    "subjects_recorded",
    "fees",
    "links",
}


def overview(api_client, headers, term=None):
    params = {} if term is None else {"term_id": term}
    return api_client.get(URL, headers=headers, params=params)


def parent_for(db, api_client, children, email="mum@camdock.test"):
    user_id, headers = login_headers(db, api_client, "parent", email)
    for child in children:
        link(db, User(id=user_id, role="parent"), child)
    return user_id, headers


def by_name(body):
    return {c["student"]["student_number"]: c for c in body["children"]}


def fee_data(api_client, admin, school):
    """Ada: first term 1000 billed / 400 paid; second term 700 billed / 100 paid.
    Bola: 500 billed, nothing paid. Chi: 250.50 billed, first term."""
    second = {**school, "term": school["term2"]}
    bill(api_client, admin, school, "ada", "1000", amount_paid="400")
    bill(api_client, admin, second, "ada", "700", amount_paid="100")
    bill(api_client, admin, school, "bola", "500")
    bill(api_client, admin, school, "chi", "250.50", fee_type=FEE_TYPES[1])


def test_one_child_gets_every_figure(db, api_client, admin, school):
    fee_data(api_client, admin, school)
    _, headers = parent_for(db, api_client, [school["ada"]])

    response = overview(api_client, headers, school["term"])

    assert response.status_code == 200
    body = response.json()
    assert set(body) == KEYS
    assert body["role"] == "parent"
    assert body["term"]["name"] == "First Term"
    (child,) = body["children"]
    assert set(child) == CHILD_KEYS
    assert child["student"] == {
        "id": school["ada"],
        "student_number": "S-ADA",
        "full_name": "Student S-ADA",
    }
    assert child["school_class"] == {"id": school["p1"], "name": "Primary 1"}
    assert child["student_average"] == "67.8"  # (80 + 55.5) / 2 = 67.75
    assert child["class_average"] == "60.9"
    assert child["subjects_recorded"] == 2
    assert child["fees"] == {
        "term": {"billed": "1000.00", "collected": "400.00", "outstanding": "600.00"},
        "all_time": {
            "billed": "1700.00",
            "collected": "500.00",
            "outstanding": "1200.00",
        },
    }
    assert child["links"] == {
        "results": f"/api/v1/students/{school['ada']}/results?term_id={school['term']}",
        "fees": f"/api/v1/fees?student_id={school['ada']}",
    }


def test_figures_equal_the_endpoints_the_parent_and_admin_use(
    db, api_client, admin, school
):
    fee_data(api_client, admin, school)
    _, headers = parent_for(db, api_client, [school["ada"]])
    params = {"term_id": school["term"]}

    (child,) = overview(api_client, headers, school["term"]).json()["children"]

    results = api_client.get(
        f"/api/v1/students/{school['ada']}/results", headers=headers, params=params
    ).json()
    assert child["student_average"] == results["average"]
    assert child["subjects_recorded"] == results["subjects_recorded"]
    klass = api_client.get(
        averages_url(school["p1"]), headers=admin, params=params
    ).json()
    assert child["class_average"] == klass["overall_average"]
    fees = api_client.get(
        "/api/v1/fees", headers=headers, params={"student_id": school["ada"], **params}
    ).json()["totals"]
    assert child["fees"]["term"] == {
        "billed": fees["billed"],
        "collected": fees["paid"],
        "outstanding": fees["balance"],
    }
    all_time = api_client.get(
        "/api/v1/fees", headers=headers, params={"student_id": school["ada"]}
    ).json()["totals"]
    assert child["fees"]["all_time"]["outstanding"] == all_time["balance"]


def test_two_children_are_listed_in_order_each_with_their_own_figures(
    db, api_client, admin, school
):
    fee_data(api_client, admin, school)
    _, headers = parent_for(db, api_client, [school["chi"], school["bola"]])

    body = overview(api_client, headers, school["term"]).json()

    assert [c["student"]["student_number"] for c in body["children"]] == [
        "S-BOLA",
        "S-CHI",
    ]
    bola, chi = body["children"]
    assert (bola["student_average"], bola["class_average"]) == ("65.0", "60.9")
    assert (chi["student_average"], chi["class_average"]) == ("70.0", "70.0")
    assert chi["school_class"]["name"] == "Primary 2"
    assert bola["fees"]["term"]["outstanding"] == "500.00"
    assert chi["fees"]["term"]["outstanding"] == "250.50"


def test_other_families_and_removed_links_never_appear(db, api_client, school):
    mum_id, mum = parent_for(db, api_client, [school["ada"]])
    parent_for(db, api_client, [school["bola"]], "other@camdock.test")

    assert list(by_name(overview(api_client, mum, school["term"]).json())) == ["S-ADA"]

    db.execute(delete(ParentStudent).where(ParentStudent.parent_user_id == mum_id))
    assert overview(api_client, mum, school["term"]).json()["children"] == []


def test_a_deactivated_child_is_not_listed(db, api_client, school):
    _, headers = parent_for(db, api_client, [school["ada"], school["bola"]])
    db.execute(
        update(Student).where(Student.id == school["ada"]).values(is_active=False)
    )

    body = overview(api_client, headers, school["term"]).json()

    assert list(by_name(body)) == ["S-BOLA"]


def test_a_parent_without_children_gets_an_empty_valid_body(db, api_client, school):
    _, headers = parent_for(db, api_client, [])

    response = overview(api_client, headers, school["term"])

    assert response.status_code == 200
    assert response.json()["children"] == []
    assert response.json()["term"]["name"] == "First Term"


def test_a_child_without_results_or_bills_gets_nulls_and_zeroes(db, api_client, school):
    newcomer = make_student(db, school["p3"], "S-NEW")
    _, headers = parent_for(db, api_client, [newcomer])

    (child,) = overview(api_client, headers, school["term"]).json()["children"]

    assert child["student_average"] is None
    assert child["class_average"] is None
    assert child["subjects_recorded"] == 0
    zero = {"billed": "0.00", "collected": "0.00", "outstanding": "0.00"}
    assert child["fees"] == {"term": zero, "all_time": zero}


def test_the_term_changes_term_figures_but_not_all_time(db, api_client, admin, school):
    fee_data(api_client, admin, school)
    _, headers = parent_for(db, api_client, [school["ada"]])

    first = overview(api_client, headers, school["term"]).json()["children"][0]
    second = overview(api_client, headers, school["term2"]).json()["children"][0]

    assert second["student_average"] == "10.0"
    assert second["subjects_recorded"] == 1
    assert second["fees"]["term"]["outstanding"] == "600.00"
    assert second["fees"]["all_time"] == first["fees"]["all_time"]
    assert second["links"]["results"].endswith(f"term_id={school['term2']}")


def test_without_any_term_the_children_are_listed_with_null_figures(
    db, api_client, admin, school
):
    fee_data(api_client, admin, school)
    _, headers = parent_for(db, api_client, [school["ada"]])

    body = overview(api_client, headers).json()  # no academic year is current

    assert body["term"] is None
    (child,) = body["children"]
    assert child["student_average"] is None
    assert child["class_average"] is None
    assert child["fees"]["term"] is None
    assert child["fees"]["all_time"]["billed"] == "1700.00"
    assert child["links"]["results"] == f"/api/v1/students/{school['ada']}/results"


def test_the_links_lead_to_endpoints_the_parent_may_open(db, api_client, admin, school):
    fee_data(api_client, admin, school)
    _, headers = parent_for(db, api_client, [school["ada"]])
    (child,) = overview(api_client, headers, school["term"]).json()["children"]

    for path in child["links"].values():
        assert api_client.get(path, headers=headers).status_code == 200


def test_a_class_average_is_hidden_for_an_inactive_class(db, api_client, school):
    _, headers = parent_for(db, api_client, [school["ada"]])
    db.execute(
        update(SchoolClass)
        .where(SchoolClass.id == school["p1"])
        .values(is_active=False)
    )

    (child,) = overview(api_client, headers, school["term"]).json()["children"]

    assert child["class_average"] is None
    assert child["student_average"] == "67.8"


@pytest.mark.parametrize("value", ["999999", "abc", "0", "-1"])
def test_a_bad_term_is_rejected(db, api_client, school, value):
    _, headers = parent_for(db, api_client, [school["ada"]])

    response = api_client.get(URL, headers=headers, params={"term_id": value})

    assert response.status_code == 422


def test_anonymous_users_are_refused(db, api_client, school):
    assert api_client.get(URL).status_code == 401


def test_the_query_count_does_not_grow_with_children(db, api_client, admin, school):
    fee_data(api_client, admin, school)
    user_id, headers = parent_for(db, api_client, [school["ada"]])
    statements: list[str] = []

    def record(conn, cursor, statement, *args):
        statements.append(statement)

    def queries() -> int:
        statements.clear()
        overview(api_client, headers, school["term"])
        return len([s for s in statements if s.startswith("SELECT")])

    event.listen(db, "before_cursor_execute", record)
    try:
        one = queries()
        for number in range(4):
            class_id = make_class(db, f"Junior {number}")
            child = make_student(db, class_id, f"S-KID{number}")
            db.execute(
                insert(ParentStudent).values(parent_user_id=user_id, student_id=child)
            )
        many = queries()
    finally:
        event.remove(db, "before_cursor_execute", record)

    assert one == many


def test_the_other_roles_keep_their_shapes(db, api_client, admin, school):
    teacher = login_headers(db, api_client, "teacher", "t@camdock.test")[1]

    assert overview(api_client, admin, school["term"]).json()["role"] == "admin"
    assert overview(api_client, teacher, school["term"]).json()["role"] == "teacher"


def test_no_secrets_and_no_audit_rows(db, api_client, admin, school):
    fee_data(api_client, admin, school)
    _, headers = parent_for(db, api_client, [school["ada"]])
    before = db.execute(select(func.count()).select_from(AuditLog)).scalar_one()

    text = overview(api_client, headers, school["term"]).text.lower()

    assert db.execute(select(func.count()).select_from(AuditLog)).scalar_one() == before
    for forbidden in ("password", "hash", "token", "@camdock.test", "s-bola", "s-chi"):
        assert forbidden not in text
