"""POST /auth/reset-password: a user redeeming the one-time code an administrator gave."""

import logging
import threading
import time

import pytest
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.models import AuditLog, PasswordReset, User
from app.services import password_resets
from tests.test_auth_api import PASSWORD, add_user, login, login_token
from tests.test_fee_payments_concurrency import wipe
from tests.test_teachers_api import bearer, login_headers

URL = "/api/v1/auth/reset-password"
ME = "/api/v1/auth/me"
EMAIL = "amy@camdock.test"
NEW = "a brand new passphrase 99"
INVALID = {
    "error": {
        "code": "invalid_reset_code",
        "message": "That reset code is not valid. Ask your administrator for a new one.",
    }
}


@pytest.fixture
def admin(db, api_client):
    return login_headers(db, api_client, "admin")


@pytest.fixture
def user_id(db):
    return add_user(db, email=EMAIL, role="teacher")


def issue(api_client, admin_headers, target):
    response = api_client.post(
        f"/api/v1/users/{target}/password-reset", headers=admin_headers
    )
    assert response.status_code == 201, response.text
    return response.json()["code"]


def redeem(api_client, code, email=EMAIL, new=NEW, **extra):
    return api_client.post(
        URL, json={"email": email, "code": code, "new_password": new, **extra}
    )


def signed_in(api_client, email=EMAIL):
    return bearer(login_token(api_client, email=email))


def reset_rows(db, target):
    table = PasswordReset.__table__
    return db.execute(
        select(table).where(table.c.user_id == target).order_by(table.c.id)
    ).all()


# --- success --------------------------------------------------------------------------


def test_the_new_password_replaces_the_old_one(db, api_client, admin, user_id):
    code = issue(api_client, admin[1], user_id)

    response = redeem(api_client, code)

    assert response.status_code == 204
    assert response.content == b""
    assert "authorization" not in response.headers
    assert login(api_client, email=EMAIL).status_code == 401
    assert login(api_client, email=EMAIL, password=NEW).status_code == 200


def test_every_session_of_the_account_ends(db, api_client, admin, user_id):
    first, second = signed_in(api_client), signed_in(api_client)
    bystander = login_headers(db, api_client, "parent")[1]
    code = issue(api_client, admin[1], user_id)

    redeem(api_client, code)

    assert api_client.get(ME, headers=first).status_code == 401
    assert api_client.get(ME, headers=second).status_code == 401
    assert api_client.get(ME, headers=bystander).status_code == 200
    assert api_client.get(ME, headers=admin[1]).status_code == 200


def test_the_code_is_marked_used(db, api_client, admin, user_id):
    code = issue(api_client, admin[1], user_id)

    redeem(api_client, code)

    (row,) = reset_rows(db, user_id)
    assert row.used_at is not None and row.voided_at is None


@pytest.mark.parametrize(
    "shape",
    [
        lambda c: c.lower(),
        lambda c: c.replace("-", ""),
        lambda c: c.replace("-", " "),
        lambda c: f"  {c}  ",
        lambda c: c.lower().replace("-", " ").upper(),
    ],
)
def test_typing_differences_do_not_matter(db, api_client, admin, user_id, shape):
    code = issue(api_client, admin[1], user_id)

    assert redeem(api_client, shape(code)).status_code == 204


def test_the_whole_story_from_issue_to_sign_in(db, api_client, admin, user_id):
    old_session = signed_in(api_client)
    code = issue(api_client, admin[1], user_id)

    assert redeem(api_client, code).status_code == 204
    token = login_token(api_client, email=EMAIL, password=NEW)

    assert api_client.get(ME, headers=old_session).status_code == 401
    assert api_client.get(ME, headers=bearer(token)).json()["email"] == EMAIL


def test_no_login_is_needed(db, api_client, admin, user_id):
    code = issue(api_client, admin[1], user_id)

    assert redeem(api_client, code).status_code == 204


# --- every bad code looks the same -------------------------------------------------------


def make_wrong_code(db, api_client, admin, user_id):
    issue(api_client, admin[1], user_id)
    return {"code": "ZZZZ-ZZZZ-ZZZZ"}


def make_unknown_email(db, api_client, admin, user_id):
    return {
        "code": issue(api_client, admin[1], user_id),
        "email": "nobody@camdock.test",
    }


def make_other_users_code(db, api_client, admin, user_id):
    bob = add_user(db, email="bob@camdock.test", role="parent")
    return {"code": issue(api_client, admin[1], bob)}


def make_expired(db, api_client, admin, user_id):
    code = issue(api_client, admin[1], user_id)
    db.execute(
        text(
            "UPDATE password_resets SET created_at = now() - interval '2 days', "
            "expires_at = now() - interval '1 day'"
        )
    )
    return {"code": code}


def make_used(db, api_client, admin, user_id):
    code = issue(api_client, admin[1], user_id)
    assert redeem(api_client, code, new="first new passphrase 11").status_code == 204
    return {"code": code}


def make_replaced(db, api_client, admin, user_id):
    first = issue(api_client, admin[1], user_id)
    issue(api_client, admin[1], user_id)
    return {"code": first}


def make_disabled(db, api_client, admin, user_id):
    code = issue(api_client, admin[1], user_id)
    db.execute(
        text("UPDATE users SET is_active = false WHERE email = 'amy@camdock.test'")
    )
    return {"code": code}


def make_blank(db, api_client, admin, user_id):
    issue(api_client, admin[1], user_id)
    return {"code": "   "}


def make_garbage(db, api_client, admin, user_id):
    issue(api_client, admin[1], user_id)
    return {"code": "!!!! ???? ####"}


SCENARIOS = [
    make_wrong_code,
    make_unknown_email,
    make_other_users_code,
    make_expired,
    make_used,
    make_replaced,
    make_disabled,
    make_blank,
    make_garbage,
]


@pytest.mark.parametrize("scenario", SCENARIOS, ids=lambda f: f.__name__)
def test_every_kind_of_bad_code_gets_the_same_answer(
    db, api_client, admin, user_id, scenario
):
    case = scenario(db, api_client, admin, user_id)

    response = redeem(api_client, **case, new="another new passphrase 7")

    assert response.status_code == 422
    assert response.json() == INVALID
    assert login(
        api_client, email=EMAIL, password="another new passphrase 7"
    ).status_code in (401,)


def test_a_failed_attempt_changes_nothing(db, api_client, admin, user_id):
    session = signed_in(api_client)
    issue(api_client, admin[1], user_id)
    before = db.execute(
        select(User.password_hash).where(User.id == user_id)
    ).scalar_one()

    redeem(api_client, "ZZZZ-ZZZZ-ZZZZ")

    assert (
        db.execute(select(User.password_hash).where(User.id == user_id)).scalar_one()
        == before
    )
    assert api_client.get(ME, headers=session).status_code == 200
    (row,) = reset_rows(db, user_id)
    assert row.used_at is None and row.voided_at is None


def test_a_used_code_cannot_be_used_again(db, api_client, admin, user_id):
    code = issue(api_client, admin[1], user_id)
    redeem(api_client, code)

    again = redeem(api_client, code, new="a different new passphrase 5")

    assert again.json() == INVALID
    assert login(api_client, email=EMAIL, password=NEW).status_code == 200


# --- the attempt limit ----------------------------------------------------------------


def test_five_wrong_codes_block_further_attempts(db, api_client, admin, user_id):
    code = issue(api_client, admin[1], user_id)
    for _ in range(5):
        assert redeem(api_client, "ZZZZ-ZZZZ-ZZZZ").status_code == 422

    blocked = redeem(api_client, code)

    assert blocked.status_code == 429
    assert blocked.json()["error"]["code"] == "too_many_attempts"
    assert int(blocked.headers["retry-after"]) > 0
    assert login(api_client, email=EMAIL, password=NEW).status_code == 401


def test_wrong_codes_do_not_block_signing_in(db, api_client, admin, user_id):
    issue(api_client, admin[1], user_id)
    for _ in range(6):
        redeem(api_client, "ZZZZ-ZZZZ-ZZZZ")

    assert login(api_client, email=EMAIL).status_code == 200


def test_wrong_passwords_do_not_block_redeeming(db, api_client, admin, user_id):
    code = issue(api_client, admin[1], user_id)
    for _ in range(5):
        assert (
            login(
                api_client, email=EMAIL, password="not the password at all"
            ).status_code
            == 401
        )

    assert redeem(api_client, code).status_code == 204


def test_a_successful_reset_lifts_a_sign_in_block(db, api_client, admin, user_id):
    code = issue(api_client, admin[1], user_id)
    for _ in range(5):
        login(api_client, email=EMAIL, password="not the password at all")
    assert login(api_client, email=EMAIL).status_code == 429

    assert redeem(api_client, code).status_code == 204

    assert login(api_client, email=EMAIL, password=NEW).status_code == 200


def test_a_successful_reset_clears_the_reset_failures(db, api_client, admin, user_id):
    code = issue(api_client, admin[1], user_id)
    for _ in range(4):
        redeem(api_client, "ZZZZ-ZZZZ-ZZZZ")
    assert redeem(api_client, code).status_code == 204
    for _ in range(4):
        assert redeem(api_client, "ZZZZ-ZZZZ-ZZZZ").status_code == 422


# --- the new password ------------------------------------------------------------------


@pytest.mark.parametrize(
    ("new", "message"),
    [
        ("short one", "at least 12"),
        ("            ", "must not be blank"),
        (EMAIL, "same as the email"),
        ("x" * 1025, None),
    ],
)
def test_an_unacceptable_password_is_refused_and_keeps_the_code(
    db, api_client, admin, user_id, new, message
):
    code = issue(api_client, admin[1], user_id)
    before = db.execute(
        select(User.password_hash).where(User.id == user_id)
    ).scalar_one()

    response = redeem(api_client, code, new=new)

    assert response.status_code == 422
    assert response.json()["error"]["code"] != "invalid_reset_code"
    if message:
        assert message in response.json()["error"]["message"]
    assert (
        db.execute(select(User.password_hash).where(User.id == user_id)).scalar_one()
        == before
    )
    assert redeem(api_client, code).status_code == 204


def test_password_problems_do_not_count_as_guesses(db, api_client, admin, user_id):
    code = issue(api_client, admin[1], user_id)
    for _ in range(8):
        assert redeem(api_client, code, new="short").status_code == 422

    assert redeem(api_client, code).status_code == 204


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"email": EMAIL},
        {"email": EMAIL, "code": "AAAA-AAAA-AAAA"},
        {"email": EMAIL, "new_password": NEW},
        {"code": "AAAA-AAAA-AAAA", "new_password": NEW},
        {"email": EMAIL, "code": "", "new_password": NEW},
        {"email": EMAIL, "code": "A" * 65, "new_password": NEW},
        {"email": EMAIL, "code": 123, "new_password": NEW},
        {"email": EMAIL, "code": "AAAA-AAAA-AAAA", "new_password": None},
        {
            "email": EMAIL,
            "code": "AAAA-AAAA-AAAA",
            "new_password": NEW,
            "role": "admin",
        },
    ],
)
def test_malformed_bodies_are_refused(api_client, body):
    assert api_client.post(URL, json=body).status_code == 422


# --- voiding on disable and on password change ----------------------------------------------


def test_disabling_the_account_voids_its_code_for_good(db, api_client, admin, user_id):
    code = issue(api_client, admin[1], user_id)
    api_client.patch(
        f"/api/v1/users/{user_id}", headers=admin[1], json={"is_active": False}
    )
    api_client.patch(
        f"/api/v1/users/{user_id}", headers=admin[1], json={"is_active": True}
    )

    response = redeem(api_client, code)

    assert response.json() == INVALID
    (row,) = reset_rows(db, user_id)
    assert row.voided_at is not None


def test_changing_your_own_password_voids_an_outstanding_code(
    db, api_client, admin, user_id
):
    code = issue(api_client, admin[1], user_id)
    headers = signed_in(api_client)
    changed = api_client.post(
        "/api/v1/auth/change-password",
        headers=headers,
        json={
            "current_password": PASSWORD,
            "new_password": "my own chosen passphrase 3",
        },
    )
    assert changed.status_code == 204

    assert redeem(api_client, code).json() == INVALID


# --- audit and secrets ---------------------------------------------------------------------


def test_the_reset_is_audited_without_secrets(db, api_client, admin, user_id, caplog):
    caplog.set_level(logging.DEBUG)
    signed_in(api_client)
    code = issue(api_client, admin[1], user_id)

    ok = redeem(api_client, code)
    bad = redeem(api_client, "ZZZZ-ZZZZ-ZZZZ", new="rejected passphrase 12")

    (row,) = db.execute(
        select(AuditLog.actor_user_id, AuditLog.entity_id, AuditLog.changes).where(
            AuditLog.action == "user.password_reset_complete"
        )
    ).all()
    assert row.actor_user_id == user_id
    assert row.entity_id == user_id
    assert row.changes == {"sessions_ended": 1, "issued_by": admin[0]}
    everything = ok.text + bad.text + str(row.changes) + caplog.text
    for secret in (code, code.replace("-", ""), NEW, "rejected passphrase 12"):
        assert secret not in everything


def test_failures_are_not_audited(db, api_client, admin, user_id):
    issue(api_client, admin[1], user_id)

    redeem(api_client, "ZZZZ-ZZZZ-ZZZZ")

    assert (
        db.execute(
            select(AuditLog.id).where(AuditLog.action == "user.password_reset_complete")
        ).first()
        is None
    )


def test_the_endpoint_is_public_in_the_contract(api_client):
    operation = api_client.app.openapi()["paths"][URL]["post"]

    assert not operation.get("security")
    assert {"204", "422", "429"} <= set(operation["responses"])


# --- two redemptions at the same moment (real, committed rows) ----------------------------------


def test_two_simultaneous_redemptions_cannot_both_win(migrated_engine, monkeypatch):
    wipe(migrated_engine)
    with Session(migrated_engine) as setup, setup.begin():
        admin_id = add_user(setup, email="admin@camdock.test", role="admin")
        target = add_user(setup, email=EMAIL, role="teacher")
        issued = password_resets.issue_reset_code(
            setup, setup.get(User, admin_id), target
        )
    real = password_resets.hash_password

    def slow(plain):
        time.sleep(0.5)  # both requests would pass the code check before either wrote
        return real(plain)

    monkeypatch.setattr(password_resets, "hash_password", slow)
    start = threading.Barrier(2)
    outcomes: list[str] = []
    guard = threading.Lock()

    def worker(new_password):
        with Session(migrated_engine) as session:
            start.wait()
            try:
                password_resets.redeem_reset_code(
                    session, EMAIL, issued.code, new_password
                )
                session.commit()
                result = "redeemed"
            except password_resets.InvalidResetCode:
                session.rollback()
                result = "invalid"
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
        assert sorted(outcomes) == ["invalid", "redeemed"]
    finally:
        wipe(migrated_engine)
