import pytest
from sqlalchemy import func, select, update

from app.models import AuditLog, ParentStudent, Student, User
from app.services import parents as parent_service
from tests.test_access_scope import make_class, make_student
from tests.test_auth_api import add_user, bearer, login_token
from tests.test_teachers_api import login_headers


def url(student_id, parent_id=None) -> str:
    base = f"/api/v1/students/{student_id}/parents"
    return base if parent_id is None else f"{base}/{parent_id}"


@pytest.fixture
def admin(db, api_client):
    return login_headers(db, api_client, "admin")[1]


@pytest.fixture
def school(db):
    p1 = make_class(db, "Primary 1")
    return {
        "student": make_student(db, p1, "S-ADA"),
        "other": make_student(db, p1, "S-BOLA"),
        "mum": add_user(db, email="mum@camdock.test", role="parent"),
        "dad": add_user(db, email="dad@camdock.test", role="parent"),
    }


def count(db) -> int:
    return db.execute(select(func.count()).select_from(ParentStudent)).scalar_one()


def audits(db, action):
    return db.execute(select(AuditLog.__table__).where(AuditLog.action == action)).all()


def link(api_client, admin, school, parent="mum", student="student", **extra):
    return api_client.post(
        url(school[student]),
        headers=admin,
        json={"parent_user_id": school[parent], **extra},
    )


def test_admin_links_a_parent_who_then_gains_access(db, api_client, admin, school):
    parent = bearer(login_token(api_client, email="mum@camdock.test"))
    before = api_client.get(f"/api/v1/students/{school['student']}", headers=parent)

    response = link(api_client, admin, school, relationship="  Mother ")

    after = api_client.get(f"/api/v1/students/{school['student']}", headers=parent)
    elsewhere = api_client.get(f"/api/v1/students/{school['other']}", headers=parent)
    assert response.status_code == 201
    assert response.json() == {
        "student_id": school["student"],
        "parent_user_id": school["mum"],
        "parent_email": "mum@camdock.test",
        "relationship": "Mother",
    }
    assert (before.status_code, after.status_code, elsewhere.status_code) == (
        404,
        200,
        404,
    )


@pytest.mark.parametrize("relationship", [None, "", "   "])
def test_a_blank_relationship_is_stored_as_null(
    db, api_client, admin, school, relationship
):
    response = link(api_client, admin, school, relationship=relationship)

    assert response.status_code == 201
    assert response.json()["relationship"] is None


def test_a_parent_may_have_several_children_and_a_child_several_parents(
    db, api_client, admin, school
):
    for parent, student in [("mum", "student"), ("mum", "other"), ("dad", "student")]:
        assert link(api_client, admin, school, parent, student).status_code == 201

    assert count(db) == 3


def test_linking_is_audited(db, api_client, admin, school):
    admin_id = db.execute(select(User.id).where(User.role == "admin")).scalar_one()

    link(api_client, admin, school, relationship="Mother")

    (row,) = audits(db, "parent_link.create")
    assert row.actor_user_id == admin_id
    assert (row.entity_type, row.entity_id) == ("student", school["student"])
    assert row.changes == {"parent_user_id": school["mum"], "relationship": "Mother"}


@pytest.mark.parametrize("role", ["teacher", "parent"])
def test_non_admins_are_forbidden_on_every_route(db, api_client, school, role):
    headers = login_headers(db, api_client, role)[1]
    link_row = (school["student"], school["mum"])

    assert (
        api_client.post(
            url(school["student"]),
            headers=headers,
            json={"parent_user_id": school["mum"]},
        ).status_code
        == 403
    )
    assert api_client.get(url(school["student"]), headers=headers).status_code == 403
    assert api_client.delete(url(*link_row), headers=headers).status_code == 403
    assert count(db) == 0


def test_anonymous_requests_are_unauthorized(db, api_client, school):
    assert (
        api_client.post(url(school["student"]), json={"parent_user_id": school["mum"]})
    ).status_code == 401
    assert api_client.get(url(school["student"])).status_code == 401
    assert api_client.delete(url(school["student"], school["mum"])).status_code == 401


def test_a_forbidden_delete_does_not_remove_the_link(db, api_client, admin, school):
    link(api_client, admin, school)
    teacher = login_headers(db, api_client, "teacher")[1]

    response = api_client.delete(url(school["student"], school["mum"]), headers=teacher)

    assert response.status_code == 403
    assert count(db) == 1


@pytest.mark.parametrize("kind", ["missing", "admin", "teacher", "inactive_parent"])
def test_only_active_parent_accounts_can_be_linked(db, api_client, admin, school, kind):
    accounts = {
        "missing": 999999,
        "admin": add_user(db, email="a2@camdock.test", role="admin"),
        "teacher": add_user(db, email="t2@camdock.test", role="teacher"),
        "inactive_parent": add_user(
            db, email="off@camdock.test", role="parent", is_active=False
        ),
    }

    response = api_client.post(
        url(school["student"]), headers=admin, json={"parent_user_id": accounts[kind]}
    )

    assert response.status_code == 422
    assert response.json()["error"]["message"] == "Parent account not found."
    assert count(db) == 0


def test_unknown_and_inactive_students_are_not_found(db, api_client, admin, school):
    db.execute(
        update(Student).where(Student.id == school["other"]).values(is_active=False)
    )

    for student_id in (999999, school["other"]):
        body = {"parent_user_id": school["mum"]}
        assert (
            api_client.post(url(student_id), headers=admin, json=body).status_code
            == 404
        )
        assert api_client.get(url(student_id), headers=admin).status_code == 404
        assert (
            api_client.delete(url(student_id, school["mum"]), headers=admin).status_code
            == 404
        )
    assert count(db) == 0


def test_linking_twice_is_a_conflict(db, api_client, admin, school):
    link(api_client, admin, school)

    response = link(api_client, admin, school, relationship="Mother")

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "conflict"
    assert count(db) == 1
    assert len(audits(db, "parent_link.create")) == 1


def test_losing_a_race_to_link_is_a_conflict_not_a_server_error(
    db, api_client, admin, school, monkeypatch
):
    link(api_client, admin, school)
    monkeypatch.setattr(parent_service, "_is_linked", lambda *args: False)

    response = link(api_client, admin, school)

    assert response.status_code == 409
    assert count(db) == 1


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"parent_user_id": 0},
        {"parent_user_id": "1"},
        {"parent_user_id": True},
        {"parent_user_id": 1.5},
        {"parent_user_id": 1, "relationship": "x" * 31},
        {"parent_user_id": 1, "is_primary": True},
    ],
)
def test_invalid_bodies_are_rejected(db, api_client, admin, school, payload):
    response = api_client.post(url(school["student"]), headers=admin, json=payload)

    assert response.status_code == 422
    assert count(db) == 0


def test_the_longest_relationship_is_accepted(db, api_client, admin, school):
    assert link(api_client, admin, school, relationship="x" * 30).status_code == 201


def test_admin_lists_the_links_ordered_by_email(db, api_client, admin, school):
    assert api_client.get(url(school["student"]), headers=admin).json() == []
    link(api_client, admin, school, "mum", relationship="Mother")
    link(api_client, admin, school, "dad")
    link(api_client, admin, school, "mum", "other")

    response = api_client.get(url(school["student"]), headers=admin)

    assert response.status_code == 200
    assert [(p["parent_email"], p["relationship"]) for p in response.json()] == [
        ("dad@camdock.test", None),
        ("mum@camdock.test", "Mother"),
    ]


def test_unlinking_removes_access_and_is_audited(db, api_client, admin, school):
    link(api_client, admin, school, relationship="Mother")
    link(api_client, admin, school, "dad")
    mum = bearer(login_token(api_client, email="mum@camdock.test"))
    admin_id = db.execute(select(User.id).where(User.role == "admin")).scalar_one()

    response = api_client.delete(url(school["student"], school["mum"]), headers=admin)

    assert response.status_code == 204
    assert response.content == b""
    gone = api_client.get(f"/api/v1/students/{school['student']}", headers=mum)
    assert gone.status_code == 404
    assert count(db) == 1  # Dad's link is untouched
    (row,) = audits(db, "parent_link.delete")
    assert row.actor_user_id == admin_id
    assert (row.entity_type, row.entity_id) == ("student", school["student"])
    assert row.changes == {"parent_user_id": school["mum"], "relationship": "Mother"}


def test_unlinking_does_not_touch_the_student_or_the_account(
    db, api_client, admin, school
):
    link(api_client, admin, school)

    api_client.delete(url(school["student"], school["mum"]), headers=admin)

    assert db.execute(select(func.count()).select_from(Student)).scalar_one() == 2
    assert (
        db.execute(select(User.id).where(User.id == school["mum"])).first() is not None
    )


def test_unlinking_a_missing_link_is_not_found_and_not_audited(
    db, api_client, admin, school
):
    first = api_client.delete(url(school["student"], school["mum"]), headers=admin)
    link(api_client, admin, school)
    api_client.delete(url(school["student"], school["mum"]), headers=admin)
    again = api_client.delete(url(school["student"], school["mum"]), headers=admin)

    assert first.status_code == again.status_code == 404
    assert first.json()["error"]["message"] == "Parent link not found."
    assert len(audits(db, "parent_link.delete")) == 1


def test_a_relinked_parent_regains_access(db, api_client, admin, school):
    mum = bearer(login_token(api_client, email="mum@camdock.test"))
    path = f"/api/v1/students/{school['student']}"
    link(api_client, admin, school)
    api_client.delete(url(school["student"], school["mum"]), headers=admin)

    assert api_client.get(path, headers=mum).status_code == 404
    assert link(api_client, admin, school).status_code == 201
    assert api_client.get(path, headers=mum).status_code == 200
