"""POST /auth/change-password: a signed-in user choosing a new password."""

import logging
import threading
import time

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import AppError
from app.core.security import verify_password
from app.models import AuditLog, User
from app.services import accounts
from tests.test_auth_api import (
    PASSWORD,
    add_user,
    login,
    login_token,
)
from tests.test_fee_payments_concurrency import wipe
from tests.test_teachers_api import bearer

URL = "/api/v1/auth/change-password"
ME = "/api/v1/auth/me"
NEW = "a brand new passphrase 99"


@pytest.fixture
def user_id(db):
    return add_user(db, email="amy@camdock.test", role="teacher")


def signed_in(api_client, email="amy@camdock.test"):
    return bearer(login_token(api_client, email=email))


def change(api_client, headers, current=PASSWORD, new=NEW, **extra):
    return api_client.post(
        URL,
        headers=headers,
        json={"current_password": current, "new_password": new, **extra},
    )


def stored_hash(db, user_id) -> str:
    return db.execute(select(User.password_hash).where(User.id == user_id)).scalar_one()


def audits(db):
    return db.execute(
        select(AuditLog.__table__).where(AuditLog.action == "user.password_change")
    ).all()


# --- success ---------------------------------------------------------------------------


def test_the_new_password_replaces_the_old_one(db, api_client, user_id):
    headers = signed_in(api_client)

    response = change(api_client, headers)

    assert response.status_code == 204
    assert response.content == b""
    assert login(api_client, email="amy@camdock.test").status_code == 401
    assert login(api_client, email="amy@camdock.test", password=NEW).status_code == 200


def test_the_current_session_stays_and_the_others_end(db, api_client, user_id):
    this_one = signed_in(api_client)
    other_one = signed_in(api_client)
    third = signed_in(api_client)

    change(api_client, this_one)

    assert api_client.get(ME, headers=this_one).status_code == 200
    assert api_client.get(ME, headers=other_one).status_code == 401
    assert api_client.get(ME, headers=third).status_code == 401


def test_other_peoples_sessions_are_untouched(db, api_client, user_id):
    add_user(db, email="bob@camdock.test", role="parent")
    mine = signed_in(api_client)
    bobs = signed_in(api_client, "bob@camdock.test")

    change(api_client, mine)

    assert api_client.get(ME, headers=bobs).status_code == 200


@pytest.mark.parametrize("role", ["admin", "teacher", "parent"])
def test_every_role_can_change_their_own_password(db, api_client, role):
    add_user(db, email=f"{role}@camdock.test", role=role)
    headers = signed_in(api_client, f"{role}@camdock.test")

    assert change(api_client, headers).status_code == 204


def test_the_password_is_stored_as_a_fresh_hash(db, api_client, user_id):
    before = stored_hash(db, user_id)

    change(api_client, signed_in(api_client))

    after = stored_hash(db, user_id)
    assert after != before
    assert after.startswith("$argon2id$")
    assert NEW not in after
    assert verify_password(NEW, after)


def test_passwords_are_used_exactly_as_sent(db, api_client, user_id):
    spaced = "  spaces around this passphrase  "

    assert change(api_client, signed_in(api_client), new=spaced).status_code == 204

    assert (
        login(api_client, email="amy@camdock.test", password=spaced).status_code == 200
    )
    assert (
        login(api_client, email="amy@camdock.test", password=spaced.strip()).status_code
        == 401
    )


def test_the_new_password_can_be_changed_again(db, api_client, user_id):
    headers = signed_in(api_client)
    change(api_client, headers)

    again = change(api_client, headers, current=NEW, new="yet another passphrase 7")

    assert again.status_code == 204


# --- the current password --------------------------------------------------------------


def test_a_wrong_current_password_changes_nothing(db, api_client, user_id):
    headers = signed_in(api_client)
    other = signed_in(api_client)
    before = stored_hash(db, user_id)

    response = change(api_client, headers, current="not my password at all")

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "incorrect_password"
    assert stored_hash(db, user_id) == before
    assert api_client.get(ME, headers=other).status_code == 200
    assert audits(db) == []


def test_wrong_current_passwords_count_toward_the_sign_in_limit(
    db, api_client, user_id
):
    headers = signed_in(api_client)
    for _ in range(5):
        wrong = change(api_client, headers, current="not my password at all")
        assert wrong.status_code == 422

    blocked = change(api_client, headers, current=PASSWORD)

    assert blocked.status_code == 429
    assert blocked.json()["error"]["code"] == "too_many_attempts"
    assert int(blocked.headers["retry-after"]) > 0
    assert audits(db) == []
    # The block is the same one signing in meets.
    assert login(api_client, email="amy@camdock.test").status_code == 429


def test_a_blocked_account_is_refused_before_any_password_is_looked_at(
    db, api_client, user_id
):
    headers = signed_in(api_client)
    for _ in range(5):
        change(api_client, headers, current="wrong wrong wrong wrong")

    response = change(api_client, headers, current=PASSWORD, new="short")

    assert response.status_code == 429


def test_a_successful_change_clears_the_failure_count(db, api_client, user_id):
    headers = signed_in(api_client)
    for _ in range(4):
        change(api_client, headers, current="wrong wrong wrong wrong")

    assert change(api_client, headers).status_code == 204
    for _ in range(4):
        again = change(api_client, headers, current="wrong wrong wrong wrong")
        assert again.status_code == 422

    assert (
        change(
            api_client, headers, current=NEW, new="another passphrase 123"
        ).status_code
        == 204
    )


def test_new_password_problems_do_not_count_as_guesses(db, api_client, user_id):
    headers = signed_in(api_client)
    for _ in range(8):
        assert change(api_client, headers, new="short").status_code == 422

    assert change(api_client, headers).status_code == 204


# --- the new password --------------------------------------------------------------------


@pytest.mark.parametrize(
    ("new", "message"),
    [
        ("short one", "at least 12"),
        ("            ", "must not be blank"),
        ("amy@camdock.test", "same as the email"),
        ("AMY@camdock.test", "same as the email"),
        ("x" * 1025, None),
        (PASSWORD, "different from the current"),
    ],
)
def test_an_unacceptable_new_password_is_refused(db, api_client, user_id, new, message):
    headers = signed_in(api_client)
    before = stored_hash(db, user_id)

    response = change(api_client, headers, new=new)

    assert response.status_code == 422
    if message:
        assert message in response.json()["error"]["message"]
    assert stored_hash(db, user_id) == before


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"current_password": PASSWORD},
        {"new_password": NEW},
        {"current_password": "", "new_password": NEW},
        {"current_password": PASSWORD, "new_password": ""},
        {"current_password": 123, "new_password": NEW},
        {"current_password": PASSWORD, "new_password": None},
        {"current_password": PASSWORD, "new_password": NEW, "email": "x@y.example"},
        {"current_password": PASSWORD, "new_password": NEW, "role": "admin"},
    ],
)
def test_malformed_bodies_are_refused(db, api_client, user_id, body):
    headers = signed_in(api_client)

    response = api_client.post(URL, headers=headers, json=body)

    assert response.status_code == 422
    assert login(api_client, email="amy@camdock.test").status_code == 200


# --- access ----------------------------------------------------------------------------


def test_a_token_is_required(api_client):
    assert change(api_client, {}).status_code == 401
    assert change(api_client, bearer("not-a-real-token-" + "x" * 30)).status_code == 401


def test_a_logged_out_token_cannot_change_a_password(db, api_client, user_id):
    headers = signed_in(api_client)
    api_client.post("/api/v1/auth/logout", headers=headers)

    assert change(api_client, headers).status_code == 401
    assert login(api_client, email="amy@camdock.test").status_code == 200


def test_the_target_is_always_the_caller(db, api_client, user_id):
    other = add_user(db, email="bob@camdock.test", role="parent")
    before = stored_hash(db, other)

    change(api_client, signed_in(api_client), user_id=other, email="bob@camdock.test")

    assert stored_hash(db, other) == before


# --- audit and secrets -----------------------------------------------------------------


def test_the_change_is_audited_without_secrets(db, api_client, user_id):
    headers = signed_in(api_client)
    signed_in(api_client)

    change(api_client, headers)

    (row,) = db.execute(
        select(AuditLog.actor_user_id, AuditLog.entity_id, AuditLog.changes).where(
            AuditLog.action == "user.password_change"
        )
    ).all()
    assert row.actor_user_id == user_id
    assert row.entity_id == user_id
    assert row.changes == {"other_sessions_ended": 1}


def test_no_password_reaches_the_response_the_audit_log_or_the_logs(
    db, api_client, user_id, caplog
):
    caplog.set_level(logging.DEBUG)
    headers = signed_in(api_client)

    ok = change(api_client, headers)
    bad = change(api_client, headers, current="my wrong guess of a password")

    rows = db.execute(select(AuditLog.changes)).scalars().all()
    everything = ok.text + bad.text + str(rows) + caplog.text
    for secret in (PASSWORD, NEW, "my wrong guess of a password"):
        assert secret not in everything


def test_the_contract_lists_the_endpoint(api_client):
    operation = api_client.app.openapi()["paths"]["/api/v1/auth/change-password"][
        "post"
    ]

    assert "204" in operation["responses"]
    assert {"422", "429"} <= set(operation["responses"])


# --- the service, called directly ---------------------------------------------------------


@pytest.fixture
def session(db):
    with Session(bind=db, join_transaction_mode="create_savepoint") as s:
        yield s


def test_the_service_reports_how_many_other_sessions_ended(db, session, api_client):
    uid = add_user(db, email="amy@camdock.test", role="teacher")
    signed_in(api_client)
    signed_in(api_client)
    keep = signed_in(api_client)
    from app.services import auth as auth_service

    kept_session, _ = auth_service.get_active_session(
        session, keep["Authorization"].split()[1]
    )

    ended = accounts.change_own_password(
        session, session.get(User, uid), kept_session.id, PASSWORD, NEW
    )

    assert ended == 2


def test_the_service_raises_for_a_wrong_current_password(db, session):
    uid = add_user(db, email="amy@camdock.test", role="teacher")

    with pytest.raises(accounts.IncorrectPassword):
        accounts.change_own_password(
            session, session.get(User, uid), 0, "nope nope nope nope", NEW
        )


# --- two requests at the same moment (real, committed rows) ------------------------------


def test_two_simultaneous_changes_with_the_same_password_cannot_both_win(
    migrated_engine, monkeypatch
):
    wipe(migrated_engine)
    with Session(migrated_engine) as setup, setup.begin():
        uid = add_user(setup, email="amy@camdock.test", role="teacher")
    real = accounts.verify_password

    def slow(plain, hashed):
        result = real(plain, hashed)
        time.sleep(0.4)  # both requests would pass the check before either wrote
        return result

    monkeypatch.setattr(accounts, "verify_password", slow)
    start = threading.Barrier(2)
    outcomes: list[str] = []
    guard = threading.Lock()

    def worker(new_password):
        with Session(migrated_engine) as session:
            start.wait()
            try:
                user = session.get(User, uid)
                accounts.change_own_password(session, user, 0, PASSWORD, new_password)
                session.commit()
                result = "changed"
            except accounts.IncorrectPassword:
                session.rollback()
                result = "incorrect"
            except AppError:
                session.rollback()
                result = "refused"
            with guard:
                outcomes.append(result)

    threads = [
        threading.Thread(target=worker, args=("first new passphrase 11",)),
        threading.Thread(target=worker, args=("second new passphrase 22",)),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)
        assert not thread.is_alive(), "a request hung"

    try:
        assert sorted(outcomes) == ["changed", "incorrect"]
    finally:
        wipe(migrated_engine)
