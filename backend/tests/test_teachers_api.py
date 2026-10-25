import pytest
from sqlalchemy import func, select, update

from app.models import (
    AuditLog,
    SchoolClass,
    Subject,
    Teacher,
    TeacherClass,
    TeacherSubject,
    User,
)
from tests.test_access_scope import add, make_class, make_student
from tests.test_auth_api import add_user, bearer, login_token

URL = "/api/v1/teachers"


def count(db, model) -> int:
    return db.execute(select(func.count()).select_from(model)).scalar_one()


def login_headers(db, client, role, email=None):
    email = email or f"{role}@camdock.test"
    user_id = add_user(db, email=email, role=role)
    return user_id, bearer(login_token(client, email=email))


@pytest.fixture
def admin(db, api_client):
    return login_headers(db, api_client, "admin")[1]


@pytest.fixture
def setup(db):
    """A teacher-role account (not yet linked), two classes and two subjects."""
    return {
        "user": add_user(db, email="t@camdock.test", role="teacher"),
        "p1": make_class(db, "Primary 1"),
        "p2": make_class(db, "Primary 2"),
        "maths": add(db, Subject, name="Mathematics"),
        "english": add(db, Subject, name="English Language"),
    }


def body(setup, **extra):
    return {"user_id": setup["user"], "full_name": "Ngozi Eze", **extra}


def no_teacher_rows(db):
    return all(count(db, m) == 0 for m in (Teacher, TeacherClass, TeacherSubject))


def test_admin_creates_a_teacher_with_assignments(db, api_client, admin, setup):
    response = api_client.post(
        URL,
        headers=admin,
        json=body(
            setup,
            full_name="  Ngozi Eze ",
            phone=" +234 803 123 4567 ",
            class_ids=[setup["p2"], setup["p1"]],
            subject_ids=[setup["maths"], setup["english"]],
        ),
    )

    assert response.status_code == 201
    out = response.json()
    assert out == {
        "id": out["id"],
        "user_id": setup["user"],
        "full_name": "Ngozi Eze",
        "phone": "+234 803 123 4567",
        "is_active": True,
        "classes": [
            {"id": setup["p1"], "name": "Primary 1"},
            {"id": setup["p2"], "name": "Primary 2"},
        ],
        "subjects": [
            {"id": setup["english"], "name": "English Language"},
            {"id": setup["maths"], "name": "Mathematics"},
        ],
    }
    assert count(db, TeacherClass) == 2 and count(db, TeacherSubject) == 2


def test_a_teacher_without_assignments_is_created(db, api_client, admin, setup):
    response = api_client.post(URL, headers=admin, json=body(setup))

    assert response.status_code == 201
    assert response.json()["classes"] == [] and response.json()["subjects"] == []
    assert response.json()["phone"] is None


def test_new_teacher_gets_exactly_the_assigned_scope(db, api_client, admin, setup):
    mine = make_student(db, setup["p1"], "S-MINE")
    make_student(db, setup["p2"], "S-OTHER")
    api_client.post(URL, headers=admin, json=body(setup, class_ids=[setup["p1"]]))
    teacher = bearer(login_token(api_client, email="t@camdock.test"))

    classes = api_client.get("/api/v1/classes", headers=teacher).json()
    students = api_client.get("/api/v1/students", headers=teacher).json()

    assert [c["id"] for c in classes] == [setup["p1"]]
    assert [s["id"] for s in students["items"]] == [mine]


@pytest.mark.parametrize("role", ["admin", "parent"])
def test_account_must_have_the_teacher_role(db, api_client, admin, setup, role):
    other = add_user(db, email=f"{role}2@camdock.test", role=role)

    response = api_client.post(
        URL, headers=admin, json={"user_id": other, "full_name": "X"}
    )

    assert response.status_code == 422
    assert response.json()["error"]["message"] == "Teacher account not found."
    assert no_teacher_rows(db)


def test_missing_and_disabled_accounts_look_the_same(db, api_client, admin, setup):
    db.execute(update(User).where(User.id == setup["user"]).values(is_active=False))

    disabled = api_client.post(URL, headers=admin, json=body(setup))
    missing = api_client.post(
        URL, headers=admin, json={"user_id": setup["user"] + 1000, "full_name": "X"}
    )

    assert disabled.status_code == missing.status_code == 422
    assert disabled.json() == missing.json()
    assert no_teacher_rows(db)


def test_an_account_cannot_be_linked_twice(db, api_client, admin, setup):
    first = api_client.post(URL, headers=admin, json=body(setup))
    second = api_client.post(
        URL, headers=admin, json=body(setup, class_ids=[setup["p1"]])
    )

    assert first.status_code == 201
    assert second.status_code == 409
    assert second.json()["error"]["code"] == "conflict"
    assert count(db, Teacher) == 1 and count(db, TeacherClass) == 0


def make_inactive_class(db):
    row = add(db, SchoolClass, name="Closed", level="primary")
    db.execute(update(SchoolClass).where(SchoolClass.id == row).values(is_active=False))
    return row


def make_inactive_subject(db):
    row = add(db, Subject, name="Retired")
    db.execute(update(Subject).where(Subject.id == row).values(is_active=False))
    return row


@pytest.mark.parametrize(
    ("key", "label", "make_inactive"),
    [
        ("class_ids", "classes", make_inactive_class),
        ("subject_ids", "subjects", make_inactive_subject),
    ],
)
def test_missing_and_inactive_assignments_are_rejected(
    db, api_client, admin, setup, key, label, make_inactive
):
    real = setup["p1"] if key == "class_ids" else setup["maths"]
    closed = make_inactive(db)

    for bad in (closed, real + 1000):
        response = api_client.post(
            URL, headers=admin, json=body(setup, **{key: [real, bad]})
        )
        assert response.status_code == 422
        assert response.json()["error"]["message"] == (
            f"One or more {label} were not found."
        )
    assert no_teacher_rows(db)


@pytest.mark.parametrize(
    ("extra", "field"),
    [
        ({"full_name": "   "}, "full_name"),
        ({"full_name": "x" * 151}, "full_name"),
        ({"phone": "call me"}, "phone"),
        ({"phone": "+-()"}, "phone"),
        ({"phone": "1" * 31}, "phone"),
        ({"class_ids": [1, 1]}, "class_ids"),
        ({"subject_ids": [2, 2]}, "subject_ids"),
        ({"class_ids": list(range(1, 52))}, "class_ids"),
        ({"class_ids": ["1"]}, "class_ids"),
        ({"class_ids": [0]}, "class_ids"),
        ({"user_id": "1"}, "user_id"),
        ({"is_active": False}, "is_active"),
        ({"id": 5}, "id"),
    ],
)
def test_invalid_bodies_are_rejected(db, api_client, admin, setup, extra, field):
    response = api_client.post(URL, headers=admin, json=body(setup, **extra))

    assert response.status_code == 422
    error = response.json()["error"]
    assert error["code"] == "validation_error"
    assert any(field in d["field"].split(".") for d in error["details"])
    assert no_teacher_rows(db)


def test_missing_required_fields_are_rejected(db, api_client, admin, setup):
    for payload in ({"full_name": "X"}, {"user_id": setup["user"]}):
        response = api_client.post(URL, headers=admin, json=payload)
        assert response.status_code == 422
    assert no_teacher_rows(db)


@pytest.mark.parametrize("role", ["teacher", "parent"])
def test_other_roles_are_forbidden(db, api_client, setup, role):
    _, headers = login_headers(db, api_client, role, f"{role}x@camdock.test")

    response = api_client.post(URL, headers=headers, json=body(setup))

    assert response.status_code == 403
    assert no_teacher_rows(db)


def test_anonymous_is_unauthorized(api_client, setup):
    assert api_client.post(URL, json=body(setup)).status_code == 401


def test_creation_is_audited_without_the_phone(db, api_client, admin, setup):
    out = api_client.post(
        URL,
        headers=admin,
        json=body(
            setup,
            phone="08031234567",
            class_ids=[setup["p2"], setup["p1"]],
            subject_ids=[setup["maths"]],
        ),
    ).json()

    row = db.execute(select(AuditLog.__table__)).one()
    assert row.action == "teacher.create"
    assert (row.entity_type, row.entity_id) == ("teacher", out["id"])
    assert row.actor_user_id is not None
    assert row.changes == {
        "user_id": setup["user"],
        "full_name": "Ngozi Eze",
        "class_ids": sorted([setup["p1"], setup["p2"]]),
        "subject_ids": [setup["maths"]],
    }
