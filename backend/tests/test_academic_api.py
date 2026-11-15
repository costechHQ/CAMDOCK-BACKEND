import pytest
from sqlalchemy import update

from app.models import SchoolClass, Subject, User
from tests.test_access_scope import add, make_class, make_teacher
from tests.test_auth_api import add_user, bearer, login_token

CLASSES_URL = "/api/v1/classes"
SUBJECTS_URL = "/api/v1/subjects"


def account(db, client, role, email=None):
    email = email or f"{role}@camdock.test"
    user_id = add_user(db, email=email, role=role)
    user = User(id=user_id, email=email, role=role, is_active=True)
    return user, bearer(login_token(client, email=email))


@pytest.mark.parametrize("url", [CLASSES_URL, SUBJECTS_URL])
def test_anonymous_and_parents_are_rejected(db, api_client, url):
    _, parent = account(db, api_client, "parent")

    assert api_client.get(url).status_code == 401
    assert api_client.get(url, headers=parent).status_code == 403


@pytest.mark.parametrize("url", [CLASSES_URL, SUBJECTS_URL])
def test_empty_database_returns_an_empty_list(db, api_client, url):
    _, admin = account(db, api_client, "admin")

    response = api_client.get(url, headers=admin)

    assert response.status_code == 200
    assert response.json() == []


def test_admin_sees_active_classes_ordered_by_name(db, api_client):
    _, admin = account(db, api_client, "admin")
    p2 = make_class(db, "Primary 2")
    p1 = make_class(db, "Primary 1")
    old = make_class(db, "Closed Class")
    db.execute(update(SchoolClass).where(SchoolClass.id == old).values(is_active=False))

    response = api_client.get(CLASSES_URL, headers=admin)

    assert response.json() == [
        {"id": p1, "name": "Primary 1", "level": "primary"},
        {"id": p2, "name": "Primary 2", "level": "primary"},
    ]


def test_teacher_sees_only_assigned_classes(db, api_client):
    teacher, headers = account(db, api_client, "teacher")
    p1, _ = make_class(db, "Primary 1"), make_class(db, "Primary 2")
    make_teacher(db, teacher, classes=[p1])

    response = api_client.get(CLASSES_URL, headers=headers)

    assert [row["id"] for row in response.json()] == [p1]


def test_teacher_without_assignments_sees_no_classes(db, api_client):
    _, headers = account(db, api_client, "teacher")
    make_class(db, "Primary 1")

    assert api_client.get(CLASSES_URL, headers=headers).json() == []


def test_staff_see_active_subjects_ordered_by_name(db, api_client):
    _, admin = account(db, api_client, "admin")
    _, teacher = account(db, api_client, "teacher")
    english = add(db, Subject, name="English Language")
    maths = add(db, Subject, name="Mathematics")
    retired = add(db, Subject, name="Retired")
    db.execute(update(Subject).where(Subject.id == retired).values(is_active=False))
    expected = [
        {"id": english, "name": "English Language"},
        {"id": maths, "name": "Mathematics"},
    ]

    for headers in (admin, teacher):
        assert api_client.get(SUBJECTS_URL, headers=headers).json() == expected
