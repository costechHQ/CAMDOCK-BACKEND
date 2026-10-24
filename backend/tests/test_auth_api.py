import logging
from datetime import UTC, datetime, timedelta

import pytest
from argon2 import PasswordHasher
from sqlalchemy import insert, select, update

from app.core.security import hash_password, hash_token
from app.models import AuthSession, User
from app.services import auth as auth_service

PASSWORD = "correct horse battery staple"
EMAIL = "admin@camdock.test"
LOGIN_URL = "/api/v1/auth/login"
LOGOUT_URL = "/api/v1/auth/logout"
ME_URL = "/api/v1/auth/me"

# One hash for every user keeps the suite fast; argon2 is deliberately slow.
PASSWORD_HASH = hash_password(PASSWORD)


def add_user(
    db, email=EMAIL, role="admin", is_active=True, password_hash=PASSWORD_HASH
):
    return db.execute(
        insert(User)
        .values(
            email=email, password_hash=password_hash, role=role, is_active=is_active
        )
        .returning(User.id)
    ).scalar_one()


def login(client, email=EMAIL, password=PASSWORD, **extra):
    return client.post(LOGIN_URL, json={"email": email, "password": password, **extra})


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def login_token(client, **kwargs) -> str:
    response = login(client, **kwargs)
    assert response.status_code == 200
    return response.json()["access_token"]


def add_session(db, user_id, token, created_ago, expires_in, revoked=False):
    now = datetime.now(UTC)
    db.execute(
        insert(AuthSession).values(
            user_id=user_id,
            token_hash=hash_token(token),
            created_at=now - created_ago,
            expires_at=now + expires_in,
            revoked_at=now if revoked else None,
        )
    )


# --- login -----------------------------------------------------------------------


def test_login_returns_working_token(db, api_client):
    user_id = add_user(db)
    response = login(api_client)

    assert response.status_code == 200
    body = response.json()
    assert body["token_type"] == "bearer"
    assert body["user"] == {"id": user_id, "email": EMAIL, "role": "admin"}
    expires_at = datetime.fromisoformat(body["expires_at"])
    assert (
        timedelta(hours=11, minutes=55)
        < expires_at - datetime.now(UTC)
        <= timedelta(hours=12)
    )
    assert (
        api_client.get(ME_URL, headers=bearer(body["access_token"])).status_code == 200
    )


def test_only_the_token_hash_is_stored(db, api_client):
    add_user(db)
    token = login_token(api_client)

    stored = db.execute(select(AuthSession.token_hash)).scalars().all()
    assert stored == [hash_token(token)]
    assert token not in stored


def test_login_sets_last_login_at(db, api_client):
    add_user(db)
    assert db.execute(select(User.last_login_at)).scalar_one() is None

    login(api_client)

    assert db.execute(select(User.last_login_at)).scalar_one() is not None


def test_failed_logins_are_indistinguishable(db, api_client):
    add_user(db)
    add_user(db, email="off@camdock.test", is_active=False)

    wrong_password = login(api_client, password="wrong password")
    unknown_email = login(api_client, email="nobody@camdock.test")
    inactive = login(api_client, email="off@camdock.test")

    for response in (wrong_password, unknown_email, inactive):
        assert response.status_code == 401
        assert response.headers["www-authenticate"] == "Bearer"
        assert response.json() == wrong_password.json()
    assert wrong_password.json()["error"]["message"] == "Invalid email or password."
    assert db.execute(select(AuthSession.id)).first() is None


def test_unknown_email_still_runs_a_password_check(db, api_client, monkeypatch):
    calls = []
    real = auth_service.verify_password
    monkeypatch.setattr(
        auth_service,
        "verify_password",
        lambda plain, hashed: calls.append(hashed) or real(plain, hashed),
    )

    login(api_client, email="nobody@camdock.test")

    assert len(calls) == 1


def test_email_is_case_insensitive_and_trimmed(db, api_client):
    add_user(db)
    assert login(api_client, email="  Admin@Camdock.TEST ").status_code == 200


def test_client_cannot_choose_a_role(db, api_client):
    add_user(db, role="parent")

    response = login(api_client, role="admin")

    assert response.status_code == 200
    assert response.json()["user"]["role"] == "parent"


@pytest.mark.parametrize(
    "payload",
    [{}, {"email": EMAIL}, {"password": PASSWORD}, {"email": "", "password": ""}],
)
def test_login_requires_email_and_password(api_client, payload):
    assert api_client.post(LOGIN_URL, json=payload).status_code == 422


def test_validation_error_does_not_echo_the_password(api_client):
    response = api_client.post(LOGIN_URL, json={"email": "", "password": "x" * 2000})

    assert response.status_code == 422
    assert "xxxx" not in response.text


def test_login_rehashes_an_outdated_password_hash(db, api_client):
    weak = PasswordHasher(time_cost=1, memory_cost=8, parallelism=1).hash(PASSWORD)
    add_user(db, password_hash=weak)

    assert login(api_client).status_code == 200

    stored = db.execute(select(User.password_hash)).scalar_one()
    assert stored != weak
    assert auth_service.needs_rehash(stored) is False
    assert login(api_client).status_code == 200


# --- current user ------------------------------------------------------------------


def test_me_returns_the_account_without_secrets(db, api_client):
    user_id = add_user(db, role="teacher")
    token = login_token(api_client)

    response = api_client.get(ME_URL, headers=bearer(token))

    assert response.status_code == 200
    assert response.json() == {"id": user_id, "email": EMAIL, "role": "teacher"}


@pytest.mark.parametrize(
    "headers",
    [
        {},
        {"Authorization": "Basic YTpi"},
        {"Authorization": "Bearer"},
        {"Authorization": "Bearer not-a-real-token"},
        {"Authorization": "Bearer "},
    ],
)
def test_me_rejects_missing_or_bad_credentials(db, api_client, headers):
    add_user(db)

    response = api_client.get(ME_URL, headers=headers)

    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"
    assert response.json()["error"]["code"] == "unauthorized"


def test_me_rejects_an_expired_token(db, api_client):
    user_id = add_user(db)
    add_session(db, user_id, "expired-token", timedelta(hours=2), -timedelta(hours=1))

    assert api_client.get(ME_URL, headers=bearer("expired-token")).status_code == 401


def test_me_rejects_a_revoked_token(db, api_client):
    user_id = add_user(db)
    add_session(
        db,
        user_id,
        "revoked-token",
        timedelta(hours=1),
        timedelta(hours=1),
        revoked=True,
    )

    assert api_client.get(ME_URL, headers=bearer("revoked-token")).status_code == 401


def test_disabling_an_account_ends_access_immediately(db, api_client):
    add_user(db)
    token = login_token(api_client)
    assert api_client.get(ME_URL, headers=bearer(token)).status_code == 200

    db.execute(update(User).values(is_active=False))

    assert api_client.get(ME_URL, headers=bearer(token)).status_code == 401
    assert login(api_client).status_code == 401


# --- logout --------------------------------------------------------------------------


def test_logout_invalidates_the_token(db, api_client):
    add_user(db)
    token = login_token(api_client)

    response = api_client.post(LOGOUT_URL, headers=bearer(token))

    assert response.status_code == 204
    assert response.content == b""
    assert api_client.get(ME_URL, headers=bearer(token)).status_code == 401
    assert db.execute(select(AuthSession.revoked_at)).scalar_one() is not None


def test_second_logout_is_unauthorized(db, api_client):
    add_user(db)
    token = login_token(api_client)
    api_client.post(LOGOUT_URL, headers=bearer(token))

    assert api_client.post(LOGOUT_URL, headers=bearer(token)).status_code == 401


def test_logout_without_a_token_is_unauthorized(api_client):
    assert api_client.post(LOGOUT_URL).status_code == 401


def test_logout_leaves_other_sessions_of_the_user_valid(db, api_client):
    add_user(db)
    first = login_token(api_client)
    second = login_token(api_client)
    assert first != second

    api_client.post(LOGOUT_URL, headers=bearer(first))

    assert api_client.get(ME_URL, headers=bearer(first)).status_code == 401
    assert api_client.get(ME_URL, headers=bearer(second)).status_code == 200


# --- no leaks ------------------------------------------------------------------------


def test_logs_never_contain_passwords_or_tokens(db, api_client, caplog):
    add_user(db)
    with caplog.at_level(logging.DEBUG):
        login(api_client, password="wrong password")
        token = login_token(api_client)
        api_client.get(ME_URL, headers=bearer(token))
        api_client.post(LOGOUT_URL, headers=bearer(token))

    assert PASSWORD not in caplog.text
    assert "wrong password" not in caplog.text
    assert token not in caplog.text


def test_responses_never_contain_the_password_hash(db, api_client):
    add_user(db)
    responses = [login(api_client)]
    token = responses[0].json()["access_token"]
    responses.append(api_client.get(ME_URL, headers=bearer(token)))

    for response in responses:
        assert "password" not in response.text
        assert "argon2" not in response.text
