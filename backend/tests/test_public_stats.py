import pytest
from sqlalchemy import event, func, select, update

from app.models import AuditLog, SchoolClass, Student, Teacher
from tests.test_access_scope import add, make_class, make_student, make_user
from tests.test_teachers_api import login_headers

URL = "/api/v1/public/stats"


def stats(api_client, headers=None):
    return api_client.get(URL, headers=headers)


@pytest.fixture
def populated(db):
    p1, p2 = make_class(db, "Primary 1"), make_class(db, "Primary 2")
    db.execute(update(SchoolClass).where(SchoolClass.id == p2).values(is_active=False))
    ids = [make_student(db, p1, f"S-{n}") for n in range(3)]
    db.execute(update(Student).where(Student.id == ids[0]).values(is_active=False))
    for active in (True, False):
        add(db, Teacher, full_name="Tutor Secret", is_active=active)
    return ids


def test_an_empty_school_gives_zeros(db, api_client):
    response = stats(api_client)

    assert response.status_code == 200
    assert response.json() == {"students": 0, "teachers": 0, "classes": 0}


def test_no_login_is_needed_and_credentials_are_ignored(db, api_client):
    assert stats(api_client).status_code == 200
    assert stats(api_client, {"Authorization": "Bearer garbage"}).status_code == 200


def test_only_active_records_are_counted(db, api_client, populated):
    assert stats(api_client).json() == {"students": 2, "teachers": 1, "classes": 1}


def test_every_caller_sees_the_same_body(db, api_client, populated):
    callers = [
        login_headers(db, api_client, r)[1] for r in ("admin", "teacher", "parent")
    ]

    bodies = [stats(api_client, h).json() for h in (None, *callers)]

    assert all(body == bodies[0] for body in bodies)


def test_nothing_personal_is_exposed(db, api_client, populated):
    make_user(db, "admin", "boss@camdock.test")
    response = stats(api_client)

    assert set(response.json()) == {"students", "teachers", "classes"}
    for secret in ("Tutor Secret", "S-1", "Primary", "boss@camdock.test"):
        assert secret not in response.text


def test_it_runs_one_statement(db, api_client, populated):
    statements: list[str] = []

    def record(conn, cursor, statement, *args):
        statements.append(statement)

    event.listen(db, "before_cursor_execute", record)
    try:
        stats(api_client)
    finally:
        event.remove(db, "before_cursor_execute", record)

    assert len([s for s in statements if s.startswith("SELECT")]) == 1


def test_counts_follow_deactivation_and_reactivation(db, api_client, populated):
    target = populated[1]

    db.execute(update(Student).where(Student.id == target).values(is_active=False))
    assert stats(api_client).json()["students"] == 1
    db.execute(update(Student).where(Student.id == target).values(is_active=True))
    assert stats(api_client).json()["students"] == 2


def test_reading_writes_nothing(db, api_client, populated):
    before = db.execute(select(func.count()).select_from(AuditLog)).scalar_one()

    stats(api_client)

    assert db.execute(select(func.count()).select_from(AuditLog)).scalar_one() == before


@pytest.mark.parametrize("method", ["post", "put", "patch", "delete"])
def test_only_get_is_allowed(db, api_client, method):
    assert getattr(api_client, method)(URL).status_code == 405
