"""SRS section 8, "Students and teachers": one test per checklist item, over the HTTP API.

Accounts are inserted directly (there is no account-creation endpoint yet); everything
else goes through the API with real login tokens. The parent and teacher scope checks
that Phase 3 could only run against probe routes are repeated here on the real routes.

Still open until later phases: results and fee scope checks (Phases 5 and 6).
"""

from datetime import date, timedelta

import pytest
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError

from app.models import SchoolClass, Student, Subject, User
from tests.test_access_scope import add, link, make_class, make_student
from tests.test_auth_api import add_user, bearer, login_token

API = "/api/v1"
PHASE_4_PREFIXES = (
    f"{API}/students",
    f"{API}/teachers",
    f"{API}/classes",
    f"{API}/subjects",
)


def login_as(db, client, role, email=None):
    """Insert an account, log in through the API and return (user, auth headers)."""
    email = email or f"{role}@camdock.test"
    user_id = add_user(db, email=email, role=role)
    user = User(id=user_id, email=email, role=role, is_active=True)
    return user, bearer(login_token(client, email=email))


@pytest.fixture
def admin(db, api_client):
    return login_as(db, api_client, "admin")[1]


@pytest.fixture
def school(db):
    return {
        "p1": make_class(db, "Primary 1"),
        "p2": make_class(db, "Primary 2"),
        "maths": add(db, Subject, name="Mathematics"),
        "english": add(db, Subject, name="English Language"),
    }


def create_student(client, headers, **fields):
    response = client.post(f"{API}/students", headers=headers, json=fields)
    assert response.status_code == 201, response.text
    return response.json()


def create_teacher(db, client, headers, email, **fields):
    user_id = add_user(db, email=email, role="teacher")
    response = client.post(
        f"{API}/teachers", headers=headers, json={"user_id": user_id, **fields}
    )
    assert response.status_code == 201, response.text
    return response.json()


# --- An administrator can create a valid student ---------------------------------------


def test_an_administrator_can_create_a_valid_student(db, api_client, admin, school):
    created = create_student(
        api_client,
        admin,
        full_name="Ada Obi",
        class_id=school["p1"],
        gender="female",
        guardian_display_name="Mrs Obi",
    )

    fetched = api_client.get(f"{API}/students/{created['id']}", headers=admin)
    listed = api_client.get(f"{API}/students", headers=admin)

    assert fetched.status_code == 200 and fetched.json() == created
    assert listed.json()["items"] == [created]
    assert created["status"] == "active"
    assert created["class_name"] == "Primary 1"


# --- Invalid or missing required fields return documented validation errors ------------


@pytest.mark.parametrize(
    ("overrides", "field"),
    [
        ({"full_name": None}, "full_name"),
        ({"full_name": "   "}, "full_name"),
        ({"class_id": None}, "class_id"),
        ({"gender": "SECRET-VALUE"}, "gender"),
        (
            {"date_of_birth": (date.today() + timedelta(days=1)).isoformat()},
            "date_of_birth",
        ),
        ({"student_number": "SECRET-VALUE"}, "student_number"),
    ],
)
def test_invalid_student_fields_return_documented_validation_errors(
    db, api_client, admin, school, overrides, field
):
    payload = {"full_name": "Ada Obi", "class_id": school["p1"], **overrides}
    payload = {k: v for k, v in payload.items() if v is not None}

    response = api_client.post(f"{API}/students", headers=admin, json=payload)

    assert response.status_code == 422
    error = response.json()["error"]
    assert error["code"] == "validation_error"
    assert error["message"] == "Request validation failed."
    assert any(field in d["field"].split(".") for d in error["details"])
    assert all(set(d) == {"field", "message"} for d in error["details"])
    assert "SECRET-VALUE" not in response.text  # submitted values are never echoed
    assert db.execute(select(Student.id)).first() is None


def test_an_unknown_class_is_a_documented_validation_error(api_client, admin):
    response = api_client.post(
        f"{API}/students", headers=admin, json={"full_name": "A", "class_id": 999}
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"
    assert response.json()["error"]["message"] == "Class not found."


# --- Student identifiers are unique ----------------------------------------------------


def test_student_identifiers_are_unique(db, api_client, admin, school):
    numbers = [
        create_student(api_client, admin, full_name="Same Name", class_id=school["p1"])[
            "student_number"
        ]
        for _ in range(5)
    ]

    assert len(set(numbers)) == 5
    # The database enforces it too, whatever the application does.
    with pytest.raises(IntegrityError), db.begin_nested():
        make_student(db, school["p1"], numbers[0])


# --- Teachers see only students in their assigned classes ------------------------------


def test_teachers_see_only_students_in_their_assigned_classes(
    db, api_client, admin, school
):
    own = [
        create_student(api_client, admin, full_name=n, class_id=school["p1"])
        for n in ("Ada Obi", "Chidi Eze")
    ]
    other = create_student(
        api_client, admin, full_name="Bola Ade", class_id=school["p2"]
    )
    create_teacher(
        db,
        api_client,
        admin,
        "teacher@camdock.test",
        full_name="Ngozi Eze",
        class_ids=[school["p1"]],
    )
    teacher = bearer(login_token(api_client, email="teacher@camdock.test"))

    def get(path, **params):
        return api_client.get(f"{API}{path}", headers=teacher, params=params)

    assert [s["id"] for s in get("/students").json()["items"]] == [s["id"] for s in own]
    assert get("/students", class_id=school["p2"]).json()["items"] == []
    assert get("/students", q="bola").json()["items"] == []
    assert get(f"/students/{own[0]['id']}").status_code == 200
    hidden, missing = get(f"/students/{other['id']}"), get("/students/99999")
    assert hidden.status_code == missing.status_code == 404
    assert hidden.json() == missing.json()  # cannot probe which ids exist
    assert [c["id"] for c in get("/classes").json()] == [school["p1"]]


# --- An administrator can create a teacher and assign classes --------------------------


def test_an_administrator_can_create_a_teacher_and_assign_classes(
    db, api_client, admin, school
):
    created = create_teacher(
        db,
        api_client,
        admin,
        "teacher@camdock.test",
        full_name="Ngozi Eze",
        phone="08031234567",
        class_ids=[school["p2"], school["p1"]],
        subject_ids=[school["maths"]],
    )

    directory = api_client.get(f"{API}/teachers", headers=admin).json()

    assert directory["items"] == [created]
    assert [c["name"] for c in created["classes"]] == ["Primary 1", "Primary 2"]
    assert [s["name"] for s in created["subjects"]] == ["Mathematics"]
    assert created["phone"] == "08031234567"


@pytest.mark.parametrize("role", ["teacher", "parent"])
def test_only_administrators_manage_teachers_and_students(db, api_client, school, role):
    _, headers = login_as(db, api_client, role)
    teacher_user = add_user(db, email="other-teacher@camdock.test", role="teacher")

    assert (
        api_client.post(
            f"{API}/teachers",
            headers=headers,
            json={"user_id": teacher_user, "full_name": "X"},
        ).status_code
        == 403
    )
    assert api_client.get(f"{API}/teachers", headers=headers).status_code == 403
    assert (
        api_client.post(
            f"{API}/students",
            headers=headers,
            json={"full_name": "X", "class_id": school["p1"]},
        ).status_code
        == 403
    )


# --- Class names are stored consistently -----------------------------------------------


def test_class_names_are_stored_consistently(db, api_client, admin, school):
    student = create_student(
        api_client, admin, full_name="Ada Obi", class_id=school["p1"]
    )

    db.execute(
        update(SchoolClass).where(SchoolClass.id == school["p1"]).values(name="Year 1")
    )

    detail = api_client.get(f"{API}/students/{student['id']}", headers=admin).json()
    listed = api_client.get(f"{API}/students", headers=admin).json()["items"][0]
    assert detail["class_id"] == listed["class_id"] == school["p1"]
    assert detail["class_name"] == listed["class_name"] == "Year 1"
    # A student stores a class reference, never free text.
    assert "class_name" not in Student.__table__.columns


# --- Scope checks from Phase 3, now on the real endpoints ------------------------------


def test_a_parent_cannot_reach_other_students_by_changing_ids(
    db, api_client, admin, school
):
    own = create_student(api_client, admin, full_name="Ada Obi", class_id=school["p1"])
    create_student(api_client, admin, full_name="Chidi Eze", class_id=school["p1"])
    create_student(api_client, admin, full_name="Bola Ade", class_id=school["p2"])
    parent, headers = login_as(db, api_client, "parent")
    link(db, parent, own["id"])

    reachable = [
        student_id
        for student_id in range(1, own["id"] + 10)
        if api_client.get(f"{API}/students/{student_id}", headers=headers).status_code
        == 200
    ]

    assert reachable == [own["id"]]
    unlinked = api_client.get(f"{API}/students/{own['id'] + 1}", headers=headers)
    missing = api_client.get(f"{API}/students/99999", headers=headers)
    assert unlinked.status_code == missing.status_code == 404
    assert unlinked.json() == missing.json()


@pytest.mark.parametrize("path", ["/students", "/teachers", "/classes", "/subjects"])
def test_a_parent_has_no_access_to_directories(db, api_client, path):
    _, headers = login_as(db, api_client, "parent")

    assert api_client.get(f"{API}{path}", headers=headers).status_code == 403


def test_anonymous_requests_are_rejected_on_every_phase_4_route(api_client):
    paths = api_client.app.openapi()["paths"]
    routes = [
        (method, path)
        for path, item in paths.items()
        if path.startswith(PHASE_4_PREFIXES)
        for method in item
    ]
    assert {path for _, path in routes} >= {
        f"{API}/students",
        f"{API}/students/{{student_id}}",
        f"{API}/teachers",
        f"{API}/classes",
        f"{API}/subjects",
    }

    for method, path in routes:
        response = api_client.request(method, path.replace("{student_id}", "1"))
        assert response.status_code == 401, (method, path)
