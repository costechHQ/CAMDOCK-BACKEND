import pytest
from sqlalchemy import func, select

from app.core.security import verify_password
from app.models import AuditLog, User
from tests.test_auth_api import add_user, bearer, login_token
from tests.test_teachers_api import login_headers

URL = "/api/v1/users"
PASSWORD = "a long enough passphrase"


def body(**extra):
    return {
        "email": "new.teacher@camdock.test",
        "role": "teacher",
        "password": PASSWORD,
        **extra,
    }


def user_count(db) -> int:
    return db.execute(select(func.count()).select_from(User)).scalar_one()


@pytest.fixture
def admin(db, api_client):
    return login_headers(db, api_client, "admin")[1]


def test_admin_creates_a_teacher_account(db, api_client, admin):
    response = api_client.post(
        URL, headers=admin, json=body(email=" New@Camdock.TEST ")
    )

    assert response.status_code == 201
    out = response.json()
    assert out == {
        "id": out["id"],
        "email": "new@camdock.test",
        "role": "teacher",
        "is_active": True,
    }
    stored = db.execute(
        select(User.password_hash).where(User.id == out["id"])
    ).scalar_one()
    assert stored != PASSWORD
    assert verify_password(PASSWORD, stored)


def test_admin_creates_a_parent_account(db, api_client, admin):
    response = api_client.post(URL, headers=admin, json=body(role="parent"))

    assert response.status_code == 201
    assert response.json()["role"] == "parent"


def test_response_never_contains_password_or_hash(api_client, admin):
    text = api_client.post(URL, headers=admin, json=body()).text

    assert PASSWORD not in text
    assert "argon2" not in text
    assert "password" not in text


def test_created_user_can_log_in(db, api_client, admin):
    api_client.post(URL, headers=admin, json=body())

    token = login_token(api_client, email="new.teacher@camdock.test", password=PASSWORD)
    me = api_client.get("/api/v1/auth/me", headers=bearer(token))

    assert me.json()["role"] == "teacher"


def test_created_teacher_account_can_be_linked(db, api_client, admin):
    user_id = api_client.post(URL, headers=admin, json=body()).json()["id"]

    response = api_client.post(
        "/api/v1/teachers",
        headers=admin,
        json={"user_id": user_id, "full_name": "Ngozi Eze"},
    )

    assert response.status_code == 201


def test_audit_row_records_the_acting_admin_without_secrets(db, api_client, admin):
    admin_id = db.execute(select(User.id).where(User.role == "admin")).scalar_one()
    user_id = api_client.post(URL, headers=admin, json=body()).json()["id"]

    row = db.execute(
        select(AuditLog.__table__).where(AuditLog.action == "user.create")
    ).one()
    assert row.actor_user_id == admin_id
    assert (row.entity_type, row.entity_id) == ("user", user_id)
    assert row.changes == {"email": "new.teacher@camdock.test", "role": "teacher"}


@pytest.mark.parametrize("role", ["teacher", "parent"])
def test_non_admins_are_forbidden(db, api_client, role):
    headers = login_headers(db, api_client, role)[1]
    before = user_count(db)

    response = api_client.post(URL, headers=headers, json=body())

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "forbidden"
    assert user_count(db) == before


def test_anonymous_request_is_unauthorized(db, api_client):
    assert api_client.post(URL, json=body()).status_code == 401
    assert user_count(db) == 0


def test_an_admin_account_cannot_be_created_over_http(db, api_client, admin):
    before = user_count(db)

    response = api_client.post(URL, headers=admin, json=body(role="admin"))

    assert response.status_code == 422
    assert user_count(db) == before


def test_duplicate_email_is_a_conflict_even_in_another_case(db, api_client, admin):
    add_user(db, email="taken@camdock.test", role="teacher")
    before = user_count(db)

    response = api_client.post(
        URL, headers=admin, json=body(email="TAKEN@camdock.test")
    )

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "conflict"
    assert user_count(db) == before


@pytest.mark.parametrize(
    "password", ["short", " " * 20, "new.teacher@camdock.test", "x" * 1025]
)
def test_weak_passwords_are_rejected(db, api_client, admin, password):
    before = user_count(db)

    response = api_client.post(URL, headers=admin, json=body(password=password))

    assert response.status_code == 422
    assert PASSWORD not in response.text
    assert user_count(db) == before


@pytest.mark.parametrize("email", ["", "not-an-email", "a@b", "x" * 250 + "@c.test"])
def test_invalid_emails_are_rejected(db, api_client, admin, email):
    response = api_client.post(URL, headers=admin, json=body(email=email))

    assert response.status_code == 422


def test_unknown_and_missing_fields_are_rejected(api_client, admin):
    assert (
        api_client.post(URL, headers=admin, json=body(is_active=False)).status_code
        == 422
    )
    assert (
        api_client.post(URL, headers=admin, json={"role": "parent"}).status_code == 422
    )
