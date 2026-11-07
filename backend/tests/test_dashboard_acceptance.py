"""SRS section 7, "Dashboards and public statistics" (FR-DASH-001 to 005): one test per
requirement over the HTTP API, then scope, leakage and end-to-end checks across the
three roles."""

# ruff: noqa: F811
from datetime import date

import pytest
from sqlalchemy import func, insert, select

from app.models import AuditLog, ClassSubject, Subject, User
from app.models.fees import FEE_TYPES
from tests.test_access_scope import add, link, make_class, make_teacher
from tests.test_auth_api import PASSWORD, add_user, bearer, login_token
from tests.test_averages import admin, admin_login, averages_url, school  # noqa: F401
from tests.test_fee_receipts import bill, pay
from tests.test_teachers_api import login_headers
from tests.test_terms_api import make_terms, make_year

API = "/api/v1"
OVERVIEW = f"{API}/dashboard/overview"
PUBLIC = f"{API}/public/stats"


def overview(api_client, headers, term=None):
    params = {} if term is None else {"term_id": term}
    return api_client.get(OVERVIEW, headers=headers, params=params)


def teacher_for(db, api_client, classes, email):
    user_id, headers = login_headers(db, api_client, "teacher", email)
    make_teacher(db, User(id=user_id, role="teacher"), classes)
    return headers


def parent_for(db, api_client, children, email):
    user_id, headers = login_headers(db, api_client, "parent", email)
    for child in children:
        link(db, User(id=user_id, role="parent"), child)
    return headers


@pytest.fixture
def world(db, api_client, admin, school):
    """The averages school (Primary 1: Ada, Bola, Dave, inactive Eve; Primary 2: Chi;
    Primary 3 empty) with a teacher and a parent per side and some bills."""
    second = {**school, "term": school["term2"]}
    bill(api_client, admin, school, "ada", "1000", amount_paid="400")
    bill(api_client, admin, second, "ada", "700", amount_paid="100")
    bill(api_client, admin, school, "chi", "250.50", fee_type=FEE_TYPES[1])
    return {
        **school,
        "admin": admin,
        "teacher_a": teacher_for(db, api_client, [school["p1"]], "ta@camdock.test"),
        "teacher_b": teacher_for(db, api_client, [school["p2"]], "tb@camdock.test"),
        "parent_a": parent_for(db, api_client, [school["ada"]], "pa@camdock.test"),
        "parent_b": parent_for(db, api_client, [school["chi"]], "pb@camdock.test"),
    }


# --- FR-DASH-001: public statistics -------------------------------------------------


def test_fr_dash_001_public_stats_count_active_records_without_login(
    db, api_client, world
):
    response = api_client.get(PUBLIC)

    assert response.status_code == 200
    assert response.json() == {"students": 4, "teachers": 2, "classes": 3}
    for private in ("Student S-", "@camdock.test", "Primary"):
        assert private not in response.text


# --- FR-DASH-002: administrator and teacher overview --------------------------------


def test_fr_dash_002_the_admin_overview_has_every_listed_figure(db, api_client, world):
    body = overview(api_client, world["admin"], world["term"]).json()

    assert body["role"] == "admin"
    assert (body["students"], body["teachers"], body["active_classes"]) == (4, 2, 2)
    assert body["fees"]["term"] == {
        "billed": "1250.50",
        "collected": "400.00",
        "outstanding": "850.50",
    }
    averages = {
        c["school_class"]["name"]: c["overall_average"] for c in body["class_averages"]
    }
    assert averages == {"Primary 1": "60.9", "Primary 2": "70.0", "Primary 3": None}


def test_fr_dash_002_the_teacher_overview_is_my_overview_for_assigned_classes(
    db, api_client, world
):
    body = overview(api_client, world["teacher_a"], world["term"]).json()

    assert body["title"] == "My overview"
    assert [c["school_class"]["name"] for c in body["classes"]] == ["Primary 1"]
    assert body["students"] == 3
    assert "fees" not in body


# --- FR-DASH-003: parent overview ---------------------------------------------------


def test_fr_dash_003_the_parent_overview_has_every_listed_figure(db, api_client, world):
    (child,) = overview(api_client, world["parent_a"], world["term"]).json()["children"]

    assert child["student"]["full_name"] == "Student S-ADA"
    assert child["school_class"]["name"] == "Primary 1"
    assert child["student_average"] == "67.8"
    assert child["class_average"] == "60.9"
    assert child["subjects_recorded"] == 2
    assert child["fees"]["term"]["outstanding"] == "600.00"
    assert child["links"]["results"].startswith(
        f"{API}/students/{world['ada']}/results"
    )
    assert child["links"]["fees"] == f"{API}/fees?student_id={world['ada']}"


# --- FR-DASH-004: term filter and fee totals ----------------------------------------


@pytest.mark.parametrize("role", ["admin", "teacher_a", "parent_a"])
def test_fr_dash_004_the_term_filter_applies_to_every_role(db, api_client, world, role):
    first = overview(api_client, world[role], world["term"]).json()
    second = overview(api_client, world[role], world["term2"]).json()

    assert first["term"]["name"] == "First Term"
    assert second["term"]["name"] == "Second Term"
    assert first != second


def test_fr_dash_004_term_and_all_time_fee_totals_are_separate_and_agree_with_fees(
    db, api_client, world
):
    fees = overview(api_client, world["admin"], world["term"]).json()["fees"]

    listed = {
        "term": api_client.get(
            f"{API}/fees", headers=world["admin"], params={"term_id": world["term"]}
        ).json()["totals"],
        "all_time": api_client.get(f"{API}/fees", headers=world["admin"]).json()[
            "totals"
        ],
    }
    assert fees["term"] != fees["all_time"]
    for period in ("term", "all_time"):
        assert fees[period] == {
            "billed": listed[period]["billed"],
            "collected": listed[period]["paid"],
            "outstanding": listed[period]["balance"],
        }


def test_fr_dash_004_a_parents_class_average_matches_the_class_endpoint(
    db, api_client, world
):
    (child,) = overview(api_client, world["parent_b"], world["term"]).json()["children"]

    endpoint = api_client.get(
        averages_url(world["p2"]),
        headers=world["admin"],
        params={"term_id": world["term"]},
    ).json()["overall_average"]
    assert child["class_average"] == endpoint == "70.0"


# --- FR-DASH-005: empty states ------------------------------------------------------


def test_fr_dash_005_an_empty_school_gives_valid_bodies_for_every_role(db, api_client):
    admin_headers = login_headers(db, api_client, "admin")[1]
    teacher = login_headers(db, api_client, "teacher")[1]
    parent = login_headers(db, api_client, "parent")[1]
    zero = {"billed": "0.00", "collected": "0.00", "outstanding": "0.00"}

    assert api_client.get(PUBLIC).json() == {"students": 0, "teachers": 0, "classes": 0}
    assert overview(api_client, admin_headers).json() == {
        "role": "admin",
        "term": None,
        "students": 0,
        "teachers": 0,
        "active_classes": 0,
        "fees": {"term": None, "all_time": zero},
        "class_averages": [],
    }
    assert overview(api_client, teacher).json() == {
        "role": "teacher",
        "title": "My overview",
        "term": None,
        "students": 0,
        "classes": [],
    }
    assert overview(api_client, parent).json() == {
        "role": "parent",
        "term": None,
        "children": [],
    }


# --- scope matrix -------------------------------------------------------------------

SCOPE_CASES = [
    ("teacher_a", "Primary 2"),
    ("teacher_a", "Primary 3"),
    ("teacher_b", "Primary 1"),
    ("parent_a", "S-CHI"),
    ("parent_a", "S-BOLA"),
    ("parent_b", "S-ADA"),
    ("parent_b", "S-BOLA"),
]


@pytest.mark.parametrize(("caller", "forbidden"), SCOPE_CASES)
def test_nobody_sees_data_outside_their_scope(db, api_client, world, caller, forbidden):
    text = overview(api_client, world[caller], world["term"]).text

    assert forbidden not in text


def test_anonymous_callers_are_refused(db, api_client, world):
    assert api_client.get(OVERVIEW).status_code == 401


# --- leakage across roles -----------------------------------------------------------

FORBIDDEN_KEYS = {
    "teacher": {"fees", "teachers", "active_classes", "class_averages", "children"},
    "parent": {"classes", "students", "teachers", "active_classes", "class_averages"},
    "admin": {"children", "title", "classes"},
}


@pytest.mark.parametrize(
    ("role", "key"),
    [("admin", "admin"), ("teacher_a", "teacher"), ("parent_a", "parent")],
)
def test_each_role_only_gets_its_own_fields(db, api_client, world, role, key):
    body = overview(api_client, world[role], world["term"]).json()

    assert body["role"] == key
    assert not (set(body) & FORBIDDEN_KEYS[key])


@pytest.mark.parametrize("role", ["admin", "teacher_a", "parent_a"])
def test_bodies_never_contain_secrets(db, api_client, world, role):
    text = overview(api_client, world[role], world["term"]).text.lower()

    for forbidden in ("password", "hash", "token", "@camdock.test"):
        assert forbidden not in text


@pytest.mark.parametrize("role", ["admin", "teacher_a", "parent_a"])
def test_reads_are_stable_and_write_nothing(db, api_client, world, role):
    before = db.execute(select(func.count()).select_from(AuditLog)).scalar_one()

    first = overview(api_client, world[role], world["term"]).json()
    second = overview(api_client, world[role], world["term"]).json()

    assert first == second
    assert db.execute(select(func.count()).select_from(AuditLog)).scalar_one() == before


# --- the two class counts -----------------------------------------------------------


def test_the_public_class_count_and_the_admin_classes_in_use_differ_by_design(
    db, api_client, world
):
    public = api_client.get(PUBLIC).json()["classes"]
    in_use = overview(api_client, world["admin"], world["term"]).json()[
        "active_classes"
    ]

    assert (public, in_use) == (3, 2)  # Primary 3 exists but has no active students


# --- end to end, creating everything through the API --------------------------------


def test_end_to_end_all_three_dashboards_agree_with_the_api(db, api_client):
    class_id = make_class(db, "Primary 3")
    maths = add(db, Subject, name="Mathematics")
    english = add(db, Subject, name="English Language")
    for subject in (maths, english):
        db.execute(insert(ClassSubject).values(class_id=class_id, subject_id=subject))
    term = make_terms(db, make_year(db, "2025/2026", date(2025, 9, 1)))["First Term"]
    add_user(db, email="head@camdock.test", role="admin")
    admin_headers = bearer(login_token(api_client, email="head@camdock.test"))

    def account(email, role):
        response = api_client.post(
            f"{API}/users",
            headers=admin_headers,
            json={"email": email, "role": role, "password": PASSWORD},
        )
        assert response.status_code == 201, response.text
        return response.json()["id"], bearer(
            login_token(api_client, email=email, password=PASSWORD)
        )

    teacher_id, teacher = account("t.eze@camdock.test", "teacher")
    parent_id, parent = account("o.obi@camdock.test", "parent")
    assert (
        api_client.post(
            f"{API}/teachers",
            headers=admin_headers,
            json={
                "user_id": teacher_id,
                "full_name": "Ngozi Eze",
                "class_ids": [class_id],
                "subject_ids": [maths, english],
            },
        ).status_code
        == 201
    )
    student = api_client.post(
        f"{API}/students",
        headers=admin_headers,
        json={"full_name": "Ada Obi", "class_id": class_id},
    ).json()["id"]
    api_client.post(
        f"{API}/students/{student}/parents",
        headers=admin_headers,
        json={"parent_user_id": parent_id, "relationship": "Mother"},
    )
    for subject, score in ((maths, "80"), (english, "55.5")):
        response = api_client.put(
            f"{API}/results",
            headers=teacher,
            json={
                "student_id": student,
                "subject_id": subject,
                "term_id": term,
                "score": score,
            },
        )
        assert response.status_code == 201, response.text
    fee = api_client.post(
        f"{API}/fees",
        headers=admin_headers,
        json={
            "student_id": student,
            "term_id": term,
            "fee_type": FEE_TYPES[0],
            "amount_billed": "25000.50",
            "amount_paid": "10000",
        },
    ).json()
    pay(api_client, admin_headers, fee["id"], "5000.25")

    admin_view = overview(api_client, admin_headers, term).json()
    teacher_view = overview(api_client, teacher, term).json()
    (child,) = overview(api_client, parent, term).json()["children"]
    public = api_client.get(PUBLIC).json()
    sheet = api_client.get(
        f"{API}/students/{student}/results", headers=parent, params={"term_id": term}
    ).json()
    totals = api_client.get(f"{API}/fees", headers=parent).json()["totals"]

    assert public == {"students": 1, "teachers": 1, "classes": 1}
    assert (admin_view["students"], admin_view["active_classes"]) == (1, 1)
    assert admin_view["fees"]["term"] == admin_view["fees"]["all_time"]
    assert admin_view["fees"]["term"]["outstanding"] == "10000.25"
    assert admin_view["class_averages"][0]["overall_average"] == "67.8"
    assert teacher_view["classes"][0]["overall_average"] == "67.8"
    assert teacher_view["classes"][0]["students"] == 1
    assert child["student_average"] == sheet["average"] == "67.8"
    assert child["class_average"] == "67.8"
    assert child["fees"]["all_time"]["outstanding"] == totals["balance"]
