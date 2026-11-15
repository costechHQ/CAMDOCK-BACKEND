import pytest
from sqlalchemy import event, func, select, update

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


# --- list ------------------------------------------------------------------------------


def add_teacher(
    db, name, *, classes=(), subjects=(), phone=None, active=True, email=None
):
    email = email or f"{name.replace(' ', '').lower()}@camdock.test"
    user_id = add_user(db, email=email, role="teacher")
    teacher_id = add(
        db, Teacher, user_id=user_id, full_name=name, phone=phone, is_active=active
    )
    for class_id in classes:
        add_link(db, TeacherClass, teacher_id=teacher_id, class_id=class_id)
    for subject_id in subjects:
        add_link(db, TeacherSubject, teacher_id=teacher_id, subject_id=subject_id)
    return teacher_id


def add_link(db, model, **values):
    db.execute(model.__table__.insert().values(**values))


def names(response) -> list[str]:
    return [item["full_name"] for item in response.json()["items"]]


@pytest.fixture
def staff(db, setup):
    return {
        "ada": add_teacher(
            db,
            "Ada Obi",
            classes=[setup["p2"], setup["p1"]],
            subjects=[setup["maths"]],
            phone="0803",
        ),
        "chi": add_teacher(
            db, "Chidi Eze", classes=[setup["p1"]], subjects=[setup["english"]]
        ),
        "bola": add_teacher(db, "Bola Ade"),
    }


def test_admin_lists_teachers_with_assignments(db, api_client, admin, setup, staff):
    response = api_client.get(URL, headers=admin)

    assert response.status_code == 200
    body = response.json()
    assert names(response) == ["Ada Obi", "Bola Ade", "Chidi Eze"]
    assert (body["total"], body["limit"], body["offset"]) == (3, 20, 0)
    ada, bola, _ = body["items"]
    assert ada["phone"] == "0803" and ada["is_active"] is True
    assert ada["classes"] == [
        {"id": setup["p1"], "name": "Primary 1"},
        {"id": setup["p2"], "name": "Primary 2"},
    ]
    assert ada["subjects"] == [{"id": setup["maths"], "name": "Mathematics"}]
    assert bola["classes"] == [] and bola["subjects"] == []
    assert "email" not in ada


def test_empty_directory_is_a_valid_page(db, api_client, admin):
    response = api_client.get(URL, headers=admin)

    assert response.json() == {"items": [], "total": 0, "limit": 20, "offset": 0}


def test_pagination_pages_do_not_overlap(db, api_client, admin, setup, staff):
    # Same name: ordering falls back to id.
    add_teacher(db, "Ada Obi", email="ada2@camdock.test")

    seen = []
    for offset in (0, 2, 4):
        page = api_client.get(URL, headers=admin, params={"limit": 2, "offset": offset})
        assert page.json()["total"] == 4
        seen += [item["id"] for item in page.json()["items"]]

    assert len(seen) == len(set(seen)) == 4
    beyond = api_client.get(URL, headers=admin, params={"offset": 50})
    assert beyond.json()["items"] == [] and beyond.json()["total"] == 4


@pytest.mark.parametrize(
    "params", [{"limit": 0}, {"limit": 101}, {"offset": -1}, {"q": "x" * 101}]
)
def test_out_of_range_parameters_are_rejected(db, api_client, admin, params):
    assert api_client.get(URL, headers=admin, params=params).status_code == 422


def test_search_is_case_insensitive_and_literal(db, api_client, admin, setup, staff):
    add_teacher(db, "100%_Sure")

    for q, expected in [
        ("ada", ["Ada Obi"]),
        ("  OBI ", ["Ada Obi"]),
        ("nobody", []),
        ("%", ["100%_Sure"]),
        ("_", ["100%_Sure"]),
    ]:
        response = api_client.get(URL, headers=admin, params={"q": q})
        assert names(response) == expected, q
        assert response.json()["total"] == len(expected)


def test_class_and_subject_filters(db, api_client, admin, setup, staff):
    def listed(**params):
        response = api_client.get(URL, headers=admin, params=params)
        return names(response), response.json()["total"]

    assert listed(class_id=setup["p1"]) == (["Ada Obi", "Chidi Eze"], 2)
    assert listed(class_id=setup["p2"]) == (["Ada Obi"], 1)
    assert listed(subject_id=setup["maths"]) == (["Ada Obi"], 1)
    assert listed(class_id=setup["p1"], subject_id=setup["english"]) == (
        ["Chidi Eze"],
        1,
    )
    assert listed(class_id=setup["p2"], subject_id=setup["english"]) == ([], 0)
    assert listed(class_id=99999) == ([], 0)


def test_inactive_teachers_classes_and_subjects_are_hidden(
    db, api_client, admin, setup, staff
):
    add_teacher(db, "Gone", active=False)
    db.execute(
        update(SchoolClass).where(SchoolClass.id == setup["p2"]).values(is_active=False)
    )
    db.execute(
        update(Subject).where(Subject.id == setup["maths"]).values(is_active=False)
    )

    response = api_client.get(URL, headers=admin)

    assert "Gone" not in names(response)
    assert response.json()["total"] == 3
    ada = response.json()["items"][0]
    assert [c["id"] for c in ada["classes"]] == [setup["p1"]]
    assert ada["subjects"] == []


@pytest.mark.parametrize("role", ["teacher", "parent"])
def test_other_roles_cannot_list_teachers(db, api_client, role):
    _, headers = login_headers(db, api_client, role, f"{role}x@camdock.test")

    assert api_client.get(URL, headers=headers).status_code == 403


def test_anonymous_cannot_list_teachers(api_client):
    assert api_client.get(URL).status_code == 401


def test_listing_does_not_query_once_per_teacher(db, api_client, admin, setup):
    for n in range(10):
        add_teacher(
            db, f"Teacher {n:02d}", classes=[setup["p1"]], subjects=[setup["maths"]]
        )
    statements = []

    @event.listens_for(db, "before_cursor_execute")
    def record(conn, cursor, statement, *args):
        statements.append(statement)

    api_client.get(URL, headers=admin, params={"limit": 2})
    small = len(statements)
    statements.clear()
    api_client.get(URL, headers=admin, params={"limit": 10})

    event.remove(db, "before_cursor_execute", record)
    assert len(statements) == small
