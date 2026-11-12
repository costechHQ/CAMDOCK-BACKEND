"""POST /users/{id}/password-reset: an administrator issues a one-time reset code."""

import logging
import re
import threading
import time
from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError
from sqlalchemy import func, insert, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.core.errors import AppError
from app.core.security import (
    RESET_CODE_ALPHABET,
    hash_reset_code,
    new_reset_code,
    normalize_reset_code,
)
from app.models import AuditLog, PasswordReset, User
from app.services import password_resets
from tests.test_auth_api import add_user
from tests.test_fee_payments_concurrency import wipe
from tests.test_teachers_api import login_headers

CODE_FORMAT = re.compile(r"[A-HJ-NP-Z2-9]{4}(-[A-HJ-NP-Z2-9]{4}){2}")


@pytest.fixture
def admin(db, api_client):
    return login_headers(db, api_client, "admin")


@pytest.fixture
def parent_id(db):
    return add_user(db, email="parent@camdock.test", role="parent")


def issue(api_client, headers, user_id):
    return api_client.post(f"/api/v1/users/{user_id}/password-reset", headers=headers)


def rows(db, user_id):
    table = PasswordReset.__table__
    return db.execute(
        select(table).where(table.c.user_id == user_id).order_by(table.c.id)
    ).all()


def audits(db):
    return db.execute(
        select(AuditLog.__table__).where(AuditLog.action == "user.password_reset_issue")
    ).all()


# --- the code itself ------------------------------------------------------------------


def test_a_code_is_twelve_unambiguous_characters_in_groups_of_four():
    code = new_reset_code()

    assert CODE_FORMAT.fullmatch(code)
    assert len(RESET_CODE_ALPHABET) == len(set(RESET_CODE_ALPHABET)) == 32
    assert not set("0O1I") & set(RESET_CODE_ALPHABET)


def test_codes_do_not_repeat():
    codes = {new_reset_code() for _ in range(500)}

    assert len(codes) == 500
    assert all(CODE_FORMAT.fullmatch(c) for c in codes)


def test_every_alphabet_character_can_appear():
    seen = set()
    for _ in range(300):
        seen |= set(new_reset_code().replace("-", ""))

    assert seen == set(RESET_CODE_ALPHABET)


def test_typing_differences_do_not_change_the_hash():
    plain = hash_reset_code(7, "K7F2-9QXM-4PWD")

    assert hash_reset_code(7, "k7f2 9qxm 4pwd") == plain
    assert hash_reset_code(7, "  K7F29QXM4PWD\n") == plain
    assert normalize_reset_code("k7f2-9qxm-4pwd") == "K7F29QXM4PWD"


def test_a_code_is_tied_to_one_account():
    assert hash_reset_code(7, "K7F2-9QXM-4PWD") != hash_reset_code(8, "K7F2-9QXM-4PWD")
    assert len(hash_reset_code(7, "K7F2-9QXM-4PWD")) == 64


# --- issuing ----------------------------------------------------------------------------


def test_issuing_returns_the_code_once_with_its_expiry(
    db, api_client, admin, parent_id
):
    response = issue(api_client, admin[1], parent_id)

    assert response.status_code == 201
    body = response.json()
    assert set(body) == {"code", "expires_at", "user"}
    assert CODE_FORMAT.fullmatch(body["code"])
    assert body["user"] == {
        "id": parent_id,
        "email": "parent@camdock.test",
        "role": "parent",
        "is_active": True,
    }
    expires = datetime.fromisoformat(body["expires_at"])
    assert abs(expires - (datetime.now(UTC) + timedelta(hours=24))) < timedelta(
        minutes=2
    )
    assert response.headers["cache-control"] == "no-store"


def test_only_a_hash_of_the_code_is_stored(db, api_client, admin, parent_id):
    code = issue(api_client, admin[1], parent_id).json()["code"]

    (row,) = rows(db, parent_id)
    assert row.code_hash == hash_reset_code(parent_id, code)
    assert row.created_by == admin[0]
    assert row.used_at is None and row.voided_at is None
    everything = db.execute(text("SELECT * FROM password_resets")).all()
    assert code not in str(everything)
    assert code.replace("-", "") not in str(everything)


def test_issuing_again_voids_the_earlier_code(db, api_client, admin, parent_id):
    first = issue(api_client, admin[1], parent_id).json()["code"]
    second = issue(api_client, admin[1], parent_id).json()["code"]

    old, new = rows(db, parent_id)
    assert first != second
    assert old.voided_at is not None and old.used_at is None
    assert new.voided_at is None
    assert new.code_hash == hash_reset_code(parent_id, second)


def test_other_accounts_codes_are_left_alone(db, api_client, admin, parent_id):
    other = add_user(db, email="teacher@camdock.test", role="teacher")
    issue(api_client, admin[1], other)

    issue(api_client, admin[1], parent_id)
    issue(api_client, admin[1], parent_id)

    (kept,) = rows(db, other)
    assert kept.voided_at is None


def test_the_database_allows_one_live_code_per_account(db, parent_id, admin):
    values = {
        "user_id": parent_id,
        "created_by": admin[0],
        "expires_at": datetime.now(UTC) + timedelta(hours=1),
    }
    db.execute(insert(PasswordReset).values(code_hash="a" * 64, **values))

    with pytest.raises(IntegrityError):
        db.execute(insert(PasswordReset).values(code_hash="b" * 64, **values))


def test_an_administrator_can_issue_for_another_administrator(db, api_client, admin):
    other = add_user(db, email="deputy@camdock.test", role="admin")

    response = issue(api_client, admin[1], other)

    assert response.status_code == 201
    assert response.json()["user"]["role"] == "admin"


def test_issuing_changes_nothing_about_the_account(db, api_client, admin):
    teacher_id, teacher = login_headers(db, api_client, "teacher")
    before = db.execute(
        select(User.password_hash, User.is_active).where(User.id == teacher_id)
    ).one()

    issue(api_client, admin[1], teacher_id)

    after = db.execute(
        select(User.password_hash, User.is_active).where(User.id == teacher_id)
    ).one()
    assert after == before
    assert api_client.get("/api/v1/auth/me", headers=teacher).status_code == 200


# --- refusals ----------------------------------------------------------------------------


def test_an_unknown_account_is_a_404(db, api_client, admin):
    response = issue(api_client, admin[1], 999999)

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"


def test_you_cannot_issue_a_code_for_yourself(db, api_client, admin):
    response = issue(api_client, admin[1], admin[0])

    assert response.status_code == 409
    assert "change password" in response.json()["error"]["message"].lower()
    assert rows(db, admin[0]) == []


def test_a_disabled_account_gets_no_code(db, api_client, admin):
    gone = add_user(db, email="gone@camdock.test", role="parent", is_active=False)

    response = issue(api_client, admin[1], gone)

    assert response.status_code == 409
    assert "disabled" in response.json()["error"]["message"]
    assert rows(db, gone) == []
    assert audits(db) == []


def test_only_an_administrator_may_issue_codes(db, api_client, parent_id):
    teacher = login_headers(db, api_client, "teacher")[1]
    parent = login_headers(db, api_client, "parent", email="other-parent@camdock.test")[
        1
    ]

    assert issue(api_client, {}, parent_id).status_code == 401
    assert issue(api_client, teacher, parent_id).status_code == 403
    assert issue(api_client, parent, parent_id).status_code == 403
    assert rows(db, parent_id) == []


def test_a_non_numeric_id_is_refused(api_client, admin):
    response = api_client.post("/api/v1/users/abc/password-reset", headers=admin[1])

    assert response.status_code == 422


# --- audit and secrets -------------------------------------------------------------------


def test_the_issue_is_audited_without_the_code(
    db, api_client, admin, parent_id, caplog
):
    caplog.set_level(logging.DEBUG)

    response = issue(api_client, admin[1], parent_id)

    code = response.json()["code"]
    (row,) = db.execute(
        select(
            AuditLog.actor_user_id,
            AuditLog.entity_type,
            AuditLog.entity_id,
            AuditLog.changes,
        ).where(AuditLog.action == "user.password_reset_issue")
    ).all()
    assert row.actor_user_id == admin[0]
    assert (row.entity_type, row.entity_id) == ("user", parent_id)
    assert set(row.changes) == {"email", "role", "expires_at"}
    assert row.changes["email"] == "parent@camdock.test"
    text_everywhere = str(row.changes) + caplog.text
    assert code not in text_everywhere
    assert code.replace("-", "") not in text_everywhere


# --- the lifetime setting --------------------------------------------------------------------


def test_the_expiry_follows_the_setting(db, api_client, admin, parent_id, monkeypatch):
    monkeypatch.setenv("PASSWORD_RESET_TTL_HOURS", "2")
    get_settings.cache_clear()

    body = issue(api_client, admin[1], parent_id).json()

    expires = datetime.fromisoformat(body["expires_at"])
    assert abs(expires - (datetime.now(UTC) + timedelta(hours=2))) < timedelta(
        minutes=2
    )


@pytest.mark.parametrize(
    ("hours", "valid"), [(0, False), (169, False), (1, True), (168, True)]
)
def test_the_lifetime_setting_is_bounded(hours, valid):
    url = "postgresql+psycopg://u:p@h/d"
    if valid:
        assert Settings(database_url=url, password_reset_ttl_hours=hours)
    else:
        with pytest.raises(ValidationError):
            Settings(database_url=url, password_reset_ttl_hours=hours)


def test_the_default_lifetime_is_a_day():
    settings = Settings(database_url="postgresql+psycopg://u:p@h/d", _env_file=None)

    assert settings.password_reset_ttl_hours == 24


def test_the_contract_lists_the_endpoint(api_client):
    paths = api_client.app.openapi()["paths"]

    assert "post" in paths["/api/v1/users/{user_id}/password-reset"]


# --- two administrators at the same moment (real, committed rows) ------------------------------


def test_two_administrators_issuing_at_once_leave_one_live_code(
    migrated_engine, monkeypatch
):
    wipe(migrated_engine)
    with Session(migrated_engine) as setup, setup.begin():
        admins = [
            add_user(setup, email=f"admin{i}@camdock.test", role="admin")
            for i in (1, 2)
        ]
        target = add_user(setup, email="parent@camdock.test", role="parent")
    real = password_resets.new_reset_code

    def slow():
        time.sleep(0.5)  # without the account lock both would insert a live code
        return real()

    monkeypatch.setattr(password_resets, "new_reset_code", slow)
    start = threading.Barrier(2)
    outcomes: list[str] = []
    guard = threading.Lock()

    def worker(admin_id):
        with Session(migrated_engine) as session:
            start.wait()
            try:
                actor = session.get(User, admin_id)
                password_resets.issue_reset_code(session, actor, target)
                session.commit()
                result = "issued"
            except (AppError, IntegrityError) as error:
                session.rollback()
                result = type(error).__name__
            with guard:
                outcomes.append(result)

    threads = [threading.Thread(target=worker, args=(a,)) for a in admins]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)
        assert not thread.is_alive(), "a request hung"

    try:
        with Session(migrated_engine) as session:
            live = session.execute(
                select(func.count())
                .select_from(PasswordReset)
                .where(
                    PasswordReset.used_at.is_(None), PasswordReset.voided_at.is_(None)
                )
            ).scalar_one()
            voided = session.execute(
                select(func.count())
                .select_from(PasswordReset)
                .where(PasswordReset.voided_at.is_not(None))
            ).scalar_one()
        assert outcomes == ["issued", "issued"]
        assert (live, voided) == (1, 1)
    finally:
        wipe(migrated_engine)
