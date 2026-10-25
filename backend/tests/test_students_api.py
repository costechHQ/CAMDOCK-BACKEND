from datetime import date, timedelta

import pytest
from sqlalchemy import func, select, update

from app.models import AuditLog, FeePayment, FeeRecord, Result, SchoolClass, Student
from tests.test_access_scope import make_class
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
