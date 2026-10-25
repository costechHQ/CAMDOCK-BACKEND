"""SRS section 8, "Authentication and authorization": one test per checklist item.

Each test goes through the HTTP API with a real login token. No student, result or
fee endpoints exist yet, so the data-scope items use throwaway probe routes that call
the same access checks the real endpoints will use. Repeat them against the real
endpoints when Phase 4 (students and teachers) and Phase 5 (results) add them.

Still open until later phases (they need endpoints that do not exist yet): parents
retrieving only linked students' results and fees, administrator-only fee entry, and
teachers seeing only their classes' students through the student endpoints.
"""

from typing import Annotated

import pytest
from fastapi import APIRouter, Depends
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.core.errors import AppError
from app.db.session import get_db
from app.dependencies import get_current_user, require_roles
from app.models import AuthSession, Subject, User
from app.services import access
from tests.test_access_scope import (
    add,
    link,
    make_class,
    make_student,
    make_teacher,
)
from tests.test_auth_api import (
    EMAIL,
    LOGOUT_URL,
    ME_URL,
    add_user,
    bearer,
    login,
    login_token,
)

PUBLIC_ROUTES = {("get", "/api/v1/health"), ("post", "/api/v1/auth/login")}

probe_router = APIRouter(prefix="/_acc")


@probe_router.get("/admin")
def admin_only(user: Annotated[User, Depends(require_roles("admin"))]):
    return {"ok": True}


@probe_router.get("/students/{student_id}")
def read_student(
    student_id: int,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
):
    return {"id": access.require_student_access(db, user, student_id)}


@probe_router.get("/results")
def read_results(
    class_id: int,
    subject_id: int,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
):
    if not access.can_access_class_subject(db, user, class_id, subject_id):
        raise AppError("Results not found.", status_code=404, code="not_found")
    return {"class_id": class_id, "subject_id": subject_id}


@pytest.fixture
def client(api_client):
    api_client.app.include_router(probe_router)
    return api_client


def account(db, client, role, email) -> tuple[User, str]:
    """Create an account, log in through the API and return (user, token)."""
    user_id = add_user(db, email=email, role=role)
    user = User(id=user_id, email=email, role=role, is_active=True)
    return user, login_token(client, email=email)


# --- A valid registered account can authenticate -------------------------------------


@pytest.mark.parametrize("role", ["admin", "teacher", "parent"])
def test_a_valid_account_can_authenticate(db, client, role):
    add_user(db, role=role)

    response = login(client)

    assert response.status_code == 200
    token = response.json()["access_token"]
    me = client.get(ME_URL, headers=bearer(token))
    assert me.status_code == 200
    assert me.json()["role"] == role


# --- Invalid credentials are rejected ------------------------------------------------


def test_invalid_credentials_are_rejected(db, client):
    add_user(db)
    add_user(db, email="off@camdock.test", is_active=False)

    attempts = [
        login(client, password="wrong password"),
        login(client, email="nobody@camdock.test"),
        login(client, email="off@camdock.test"),
    ]

    for response in attempts:
        assert response.status_code == 401
        assert response.json() == attempts[0].json()
        assert "access_token" not in response.text
    assert db.execute(select(AuthSession.id)).first() is None


# --- Anonymous requests cannot read protected data or mutate records -----------------


def test_anonymous_requests_are_rejected_on_every_protected_route(client):
    paths = client.app.openapi()["paths"]
    protected = [
        (method, path)
        for path, item in paths.items()
        for method in item
        if (method, path) not in PUBLIC_ROUTES
    ]
    assert protected  # guards against the check passing on an empty app

    for method, path in protected:
        url = path.replace("{student_id}", "1")
        response = client.request(method, url, params={"class_id": 1, "subject_id": 1})
        assert response.status_code == 401, (method, path)
        assert response.headers["www-authenticate"] == "Bearer"


def test_anonymous_requests_are_rejected_with_a_bad_token(client):
    for headers in ({}, bearer("garbage"), {"Authorization": "Basic abc"}):
        assert client.get("/_acc/students/1", headers=headers).status_code == 401


# --- A parent cannot read another student's data by changing IDs ----------------------


def test_a_parent_cannot_reach_other_students_by_changing_ids(db, client):
    p1 = make_class(db, "Primary 1")
    own = make_student(db, p1, "S-OWN")
    classmate = make_student(db, p1, "S-MATE")
    parent, token = account(db, client, "parent", "parent@camdock.test")
    link(db, parent, own)

    def get(student_id):
        return client.get(f"/_acc/students/{student_id}", headers=bearer(token))

    assert get(own).status_code == 200
    unlinked, missing = get(classmate), get(own + 1000)
    assert unlinked.status_code == missing.status_code == 404
    assert unlinked.json() == missing.json()  # cannot probe which ids exist
    # Walking the id range finds exactly the one linked child.
    found = [i for i in range(1, classmate + 5) if get(i).status_code == 200]
    assert found == [own]


# --- A teacher cannot read or modify records outside assigned classes/subjects -------


def test_a_teacher_is_limited_to_assigned_classes_and_subjects(db, client):
    p1, p2 = make_class(db, "Primary 1"), make_class(db, "Primary 2")
    in_class, other_class = make_student(db, p1, "S-IN"), make_student(db, p2, "S-OUT")
    maths = add(db, Subject, name="Mathematics")
    english = add(db, Subject, name="English Language")
    teacher, token = account(db, client, "teacher", "teacher@camdock.test")
    make_teacher(db, teacher, classes=[p1], subjects=[maths])

    def student(student_id):
        return client.get(f"/_acc/students/{student_id}", headers=bearer(token))

    def results(class_id, subject_id):
        return client.get(
            "/_acc/results",
            params={"class_id": class_id, "subject_id": subject_id},
            headers=bearer(token),
        )

    assert student(in_class).status_code == 200
    assert student(other_class).status_code == 404
    assert results(p1, maths).status_code == 200
    assert results(p1, english).status_code == 404  # wrong subject
    assert results(p2, maths).status_code == 404  # wrong class


# --- An administrator-only endpoint rejects teacher and parent accounts --------------


def test_an_admin_only_endpoint_rejects_teacher_and_parent(db, client):
    tokens = {
        role: account(db, client, role, f"{role}@camdock.test")[1]
        for role in ("admin", "teacher", "parent")
    }

    statuses = {
        role: client.get("/_acc/admin", headers=bearer(token)).status_code
        for role, token in tokens.items()
    }

    assert statuses == {"admin": 200, "teacher": 403, "parent": 403}
    assert client.get("/_acc/admin").status_code == 401


# --- Logout/session invalidation follows the approved design -------------------------
# Approved design: opaque server-side sessions, fixed 12h expiry, no refresh tokens.


def test_logout_ends_only_the_session_that_logged_out(db, client):
    add_user(db)
    first, second = login_token(client), login_token(client)

    assert client.post(LOGOUT_URL, headers=bearer(first)).status_code == 204

    assert client.get(ME_URL, headers=bearer(first)).status_code == 401
    assert client.get(ME_URL, headers=bearer(second)).status_code == 200
    assert client.post(LOGOUT_URL, headers=bearer(first)).status_code == 401


def test_disabling_an_account_ends_its_sessions_immediately(db, client):
    add_user(db)
    token = login_token(client)
    assert client.get(ME_URL, headers=bearer(token)).status_code == 200

    db.execute(update(User).where(User.email == EMAIL).values(is_active=False))

    assert client.get(ME_URL, headers=bearer(token)).status_code == 401
    assert login(client).status_code == 401
