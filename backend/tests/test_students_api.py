from datetime import date, timedelta

import pytest
from sqlalchemy import event, func, select, update

from app.models import (
    AuditLog,
    FeePayment,
    FeeRecord,
    Result,
    SchoolClass,
    Student,
    User,
)
from tests.test_access_scope import add, link, make_class, make_teacher
from tests.test_auth_api import add_user, bearer, login_token

URL = "/api/v1/students"
NUMBER_PREFIX = f"CAM-{date.today().year}-"


def headers_for(db, client, role="admin"):
    add_user(db, email=f"{role}@camdock.test", role=role)
    return bearer(login_token(client, email=f"{role}@camdock.test"))


def count(db, model) -> int:
    return db.execute(select(func.count()).select_from(model)).scalar_one()


@pytest.fixture
def admin(db, api_client):
    return headers_for(db, api_client)


def test_admin_creates_a_student(db, api_client, admin):
    class_id = make_class(db, "Primary 1")

    response = api_client.post(
        URL,
        headers=admin,
        json={
            "full_name": "  Ada Obi ",
            "class_id": class_id,
            "gender": "Female",
            "guardian_display_name": " Mrs Obi ",
            "date_of_birth": "2015-04-02",
        },
    )

    assert response.status_code == 201
    body = response.json()
    assert body.pop("student_number").startswith(NUMBER_PREFIX)
    stored = db.execute(select(Student.id, Student.is_active)).one()
    assert body == {
        "id": stored.id,
        "full_name": "Ada Obi",
        "class_id": class_id,
        "class_name": "Primary 1",
        "gender": "female",
        "date_of_birth": "2015-04-02",
        "guardian_display_name": "Mrs Obi",
        "status": "active",
    }
    assert stored.is_active


def test_minimal_body_works_and_numbers_are_unique_and_increasing(
    db, api_client, admin
):
    class_id = make_class(db, "Primary 1")

    numbers = []
    for _ in range(3):
        response = api_client.post(
            URL, headers=admin, json={"full_name": "Same Name", "class_id": class_id}
        )
        assert response.status_code == 201
        assert response.json()["gender"] is None
        assert response.json()["guardian_display_name"] is None
        numbers.append(response.json()["student_number"])

    assert len(set(numbers)) == 3
    assert numbers == sorted(numbers)
    assert all(n.startswith(NUMBER_PREFIX) for n in numbers)


def test_blank_guardian_is_stored_as_null(db, api_client, admin):
    class_id = make_class(db, "Primary 1")

    response = api_client.post(
        URL,
        headers=admin,
        json={"full_name": "A", "class_id": class_id, "guardian_display_name": "  "},
    )

    assert response.json()["guardian_display_name"] is None


TOMORROW = (date.today() + timedelta(days=1)).isoformat()


@pytest.mark.parametrize(
    ("body", "field"),
    [
        ({"class_id": "{c}"}, "full_name"),
        ({"full_name": "   ", "class_id": "{c}"}, "full_name"),
        ({"full_name": "x" * 151, "class_id": "{c}"}, "full_name"),
        ({"full_name": "A"}, "class_id"),
        ({"full_name": "A", "class_id": "1"}, "class_id"),
        ({"full_name": "A", "class_id": "{c}", "gender": "other"}, "gender"),
        (
            {"full_name": "A", "class_id": "{c}", "guardian_display_name": "x" * 151},
            "guardian_display_name",
        ),
        (
            {"full_name": "A", "class_id": "{c}", "date_of_birth": TOMORROW},
            "date_of_birth",
        ),
        (
            {"full_name": "A", "class_id": "{c}", "date_of_birth": "not a date"},
            "date_of_birth",
        ),
        (
            {"full_name": "A", "class_id": "{c}", "student_number": "CAM-1"},
            "student_number",
        ),
        ({"full_name": "A", "class_id": "{c}", "status": "graduated"}, "status"),
        ({"full_name": "A", "class_id": "{c}", "is_active": False}, "is_active"),
    ],
)
def test_invalid_bodies_are_rejected(db, api_client, admin, body, field):
    class_id = make_class(db, "Primary 1")
    body = {k: class_id if v == "{c}" else v for k, v in body.items()}

    response = api_client.post(URL, headers=admin, json=body)

    assert response.status_code == 422
    error = response.json()["error"]
    assert error["code"] == "validation_error"
    assert field in {d["field"].split(".")[-1] for d in error["details"]}
    assert count(db, Student) == 0


def test_missing_and_inactive_classes_are_rejected_identically(db, api_client, admin):
    closed = make_class(db, "Closed")
    db.execute(
        update(SchoolClass).where(SchoolClass.id == closed).values(is_active=False)
    )

    responses = [
        api_client.post(URL, headers=admin, json={"full_name": "A", "class_id": cid})
        for cid in (closed, closed + 1000)
    ]

    for response in responses:
        assert response.status_code == 422
        assert response.json() == responses[0].json()
        assert response.json()["error"]["message"] == "Class not found."
    assert count(db, Student) == 0


@pytest.mark.parametrize("role", ["teacher", "parent"])
def test_other_roles_are_forbidden(db, api_client, role):
    class_id = make_class(db, "Primary 1")
    headers = headers_for(db, api_client, role)

    response = api_client.post(
        URL, headers=headers, json={"full_name": "A", "class_id": class_id}
    )

    assert response.status_code == 403
    assert count(db, Student) == 0


def test_anonymous_is_unauthorized(db, api_client):
    class_id = make_class(db, "Primary 1")

    response = api_client.post(URL, json={"full_name": "A", "class_id": class_id})

    assert response.status_code == 401


def test_creation_is_audited_to_the_acting_admin(db, api_client, admin):
    class_id = make_class(db, "Primary 1")
    assert count(db, AuditLog) == 0

    body = api_client.post(
        URL, headers=admin, json={"full_name": "Ada Obi", "class_id": class_id}
    ).json()

    row = db.execute(select(AuditLog.__table__)).one()
    assert row.action == "student.create"
    assert (row.entity_type, row.entity_id) == ("student", body["id"])
    assert row.actor_user_id is not None
    assert row.changes == {
        "student_number": body["student_number"],
        "full_name": "Ada Obi",
        "class_id": class_id,
        "gender": None,
        "guardian_display_name": None,
    }


def test_creating_a_student_creates_no_scores_or_payments(db, api_client, admin):
    class_id = make_class(db, "Primary 1")

    api_client.post(URL, headers=admin, json={"full_name": "A", "class_id": class_id})

    for model in (Result, FeeRecord, FeePayment):
        assert count(db, model) == 0


# --- list, search and detail ----------------------------------------------------------


def add_student(db, class_id, name, number=None, **extra) -> int:
    number = number or f"T-{name}"
    return add(
        db,
        Student,
        student_number=number,
        full_name=name,
        class_id=class_id,
        **extra,
    )


def account(db, client, role, email=None):
    email = email or f"{role}@camdock.test"
    user_id = add_user(db, email=email, role=role)
    user = User(id=user_id, email=email, role=role, is_active=True)
    return user, bearer(login_token(client, email=email))


@pytest.fixture
def roster(db):
    p1, p2 = make_class(db, "Primary 1"), make_class(db, "Primary 2")
    return {
        "p1": p1,
        "p2": p2,
        "ada": add_student(db, p1, "Ada Obi"),
        "chi": add_student(db, p1, "Chidi Eze"),
        "bola": add_student(db, p2, "Bola Ade"),
    }


def names(response) -> list[str]:
    return [item["full_name"] for item in response.json()["items"]]


def test_admin_lists_every_active_student_ordered_by_name(db, api_client, roster):
    _, admin = account(db, api_client, "admin")
    add_student(db, roster["p1"], "Gone", is_active=False)

    response = api_client.get(URL, headers=admin)

    assert response.status_code == 200
    body = response.json()
    assert names(response) == ["Ada Obi", "Bola Ade", "Chidi Eze"]
    assert (body["total"], body["limit"], body["offset"]) == (3, 20, 0)
    first = body["items"][0]
    assert first["class_name"] == "Primary 1"
    assert first["student_number"] == "T-Ada Obi"


def test_empty_directory_is_a_valid_page(db, api_client):
    _, admin = account(db, api_client, "admin")

    response = api_client.get(URL, headers=admin)

    assert response.status_code == 200
    assert response.json() == {"items": [], "total": 0, "limit": 20, "offset": 0}


def test_pagination_pages_do_not_overlap(db, api_client, roster):
    _, admin = account(db, api_client, "admin")
    add_student(db, roster["p1"], "Ada Obi", number="T-dup")  # same name, tie on id

    seen = []
    for offset in (0, 2, 4):
        page = api_client.get(URL, headers=admin, params={"limit": 2, "offset": offset})
        assert page.json()["total"] == 4
        seen += [item["id"] for item in page.json()["items"]]

    assert len(seen) == len(set(seen)) == 4
    beyond = api_client.get(URL, headers=admin, params={"offset": 50})
    assert beyond.json()["items"] == []
    assert beyond.json()["total"] == 4


@pytest.mark.parametrize(
    "params", [{"limit": 0}, {"limit": 101}, {"offset": -1}, {"q": "x" * 101}]
)
def test_out_of_range_parameters_are_rejected(db, api_client, params):
    _, admin = account(db, api_client, "admin")

    assert api_client.get(URL, headers=admin, params=params).status_code == 422


def test_search_is_case_insensitive_substring(db, api_client, roster):
    _, admin = account(db, api_client, "admin")

    for q, expected in [
        ("ada", ["Ada Obi"]),
        ("  OBI ", ["Ada Obi"]),
        ("d", ["Ada Obi", "Bola Ade", "Chidi Eze"]),
        ("nobody", []),
    ]:
        response = api_client.get(URL, headers=admin, params={"q": q})
        assert names(response) == expected, q
        assert response.json()["total"] == len(expected)


def test_search_wildcards_match_literally(db, api_client, roster):
    _, admin = account(db, api_client, "admin")
    add_student(db, roster["p1"], "100%_Sure")

    for q in ("%", "_", "0%_S"):
        assert names(api_client.get(URL, headers=admin, params={"q": q})) == [
            "100%_Sure"
        ], q
    assert names(api_client.get(URL, headers=admin, params={"q": "\\"})) == []


def test_class_filter(db, api_client, roster):
    _, admin = account(db, api_client, "admin")

    response = api_client.get(URL, headers=admin, params={"class_id": roster["p2"]})

    assert names(response) == ["Bola Ade"]
    assert (
        api_client.get(URL, headers=admin, params={"class_id": 99999}).json()["total"]
        == 0
    )


def test_teacher_lists_only_assigned_classes(db, api_client, roster):
    teacher, headers = account(db, api_client, "teacher")
    make_teacher(db, teacher, classes=[roster["p1"]])

    assert names(api_client.get(URL, headers=headers)) == ["Ada Obi", "Chidi Eze"]
    other = api_client.get(URL, headers=headers, params={"class_id": roster["p2"]})
    assert other.status_code == 200
    assert other.json()["items"] == [] and other.json()["total"] == 0
    search = api_client.get(URL, headers=headers, params={"q": "bola"})
    assert search.json()["items"] == []


def test_teacher_without_assignments_gets_an_empty_page(db, api_client, roster):
    _, headers = account(db, api_client, "teacher")

    assert api_client.get(URL, headers=headers).json()["items"] == []


def test_parent_and_anonymous_cannot_use_the_directory(db, api_client, roster):
    _, parent = account(db, api_client, "parent")

    assert api_client.get(URL, headers=parent).status_code == 403
    assert api_client.get(URL).status_code == 401


def test_listing_does_not_query_once_per_row(db, api_client):
    _, admin = account(db, api_client, "admin")
    class_id = make_class(db, "Primary 1")
    for n in range(10):
        add_student(db, class_id, f"Student {n:02d}")
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


def test_admin_reads_any_student_detail(db, api_client, roster):
    _, admin = account(db, api_client, "admin")

    response = api_client.get(f"{URL}/{roster['bola']}", headers=admin)

    assert response.status_code == 200
    assert response.json() == {
        "id": roster["bola"],
        "student_number": "T-Bola Ade",
        "full_name": "Bola Ade",
        "class_id": roster["p2"],
        "class_name": "Primary 2",
        "gender": None,
        "date_of_birth": None,
        "guardian_display_name": None,
        "status": "active",
    }


def test_teacher_detail_follows_class_assignment(db, api_client, roster):
    teacher, headers = account(db, api_client, "teacher")
    make_teacher(db, teacher, classes=[roster["p1"]])

    assert api_client.get(f"{URL}/{roster['ada']}", headers=headers).status_code == 200
    assert api_client.get(f"{URL}/{roster['bola']}", headers=headers).status_code == 404


def test_parent_detail_only_for_linked_children(db, api_client, roster):
    parent, headers = account(db, api_client, "parent")
    link(db, parent, roster["ada"])

    own = api_client.get(f"{URL}/{roster['ada']}", headers=headers)
    classmate = api_client.get(f"{URL}/{roster['chi']}", headers=headers)
    missing = api_client.get(f"{URL}/{roster['bola'] + 1000}", headers=headers)

    assert own.status_code == 200
    assert classmate.status_code == missing.status_code == 404
    assert classmate.json() == missing.json()
    assert classmate.json()["error"]["message"] == "Student not found."


def test_inactive_student_detail_is_not_found(db, api_client, roster):
    _, admin = account(db, api_client, "admin")
    gone = add_student(db, roster["p1"], "Gone", is_active=False)

    assert api_client.get(f"{URL}/{gone}", headers=admin).status_code == 404


def test_detail_requires_authentication(api_client, roster):
    assert api_client.get(f"{URL}/{roster['ada']}").status_code == 401
