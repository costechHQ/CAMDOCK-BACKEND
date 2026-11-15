"""The account directory (GET /users) and disabling or re-enabling accounts (PATCH)."""

import threading
import time

import pytest
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.core.errors import AppError
from app.models import AuditLog, AuthSession, User
from app.services import accounts
from app.services import auth as auth_service
from tests.test_auth_api import add_user, login, login_token
from tests.test_fee_payments_concurrency import wipe
from tests.test_teachers_api import login_headers

USERS = "/api/v1/users"


@pytest.fixture
def admin(db, api_client):
    return login_headers(db, api_client, "admin")


@pytest.fixture
def session(db):
    """A Session on the test's connection, for calling services directly."""
    with Session(bind=db, join_transaction_mode="create_savepoint") as s:
        yield s


def is_active(db, user_id) -> bool:
    return db.execute(select(User.is_active).where(User.id == user_id)).scalar_one()


def patch(api_client, headers, user_id, body):
    return api_client.patch(f"{USERS}/{user_id}", headers=headers, json=body)


def audits(db, *actions):
    return db.execute(
        select(AuditLog.__table__).where(AuditLog.action.in_(actions))
    ).all()


# --- GET /users -----------------------------------------------------------------------


def test_the_directory_lists_accounts_by_email(db, api_client, admin):
    add_user(db, email="zoe@camdock.test", role="parent")
    add_user(db, email="amy@camdock.test", role="teacher")

    response = api_client.get(USERS, headers=admin[1])

    assert response.status_code == 200
    body = response.json()
    assert [u["email"] for u in body["items"]] == [
        "admin@camdock.test",
        "amy@camdock.test",
        "zoe@camdock.test",
    ]
    assert body["total"] == 3
    assert (body["limit"], body["offset"]) == (20, 0)


def test_each_item_shows_status_and_times_and_no_credential(db, api_client, admin):
    add_user(db, email="amy@camdock.test", role="teacher", is_active=False)

    response = api_client.get(USERS, params={"q": "amy"}, headers=admin[1])

    (item,) = response.json()["items"]
    assert set(item) == {
        "id",
        "email",
        "role",
        "is_active",
        "last_login_at",
        "created_at",
    }
    assert item["is_active"] is False
    assert item["last_login_at"] is None
    assert "hash" not in response.text and "password" not in response.text


def test_pagination_counts_every_match(db, api_client, admin):
    for i in range(5):
        add_user(db, email=f"p{i}@camdock.test", role="parent")

    page = api_client.get(
        USERS, params={"role": "parent", "limit": 2, "offset": 2}, headers=admin[1]
    ).json()

    assert page["total"] == 5
    assert [u["email"] for u in page["items"]] == ["p2@camdock.test", "p3@camdock.test"]


def test_search_is_case_insensitive_and_takes_wildcards_literally(
    db, api_client, admin
):
    add_user(db, email="ada.obi@camdock.test", role="parent")
    add_user(db, email="adaxobi@camdock.test", role="parent")
    add_user(db, email="100%@camdock.test", role="parent")

    def found(q):
        response = api_client.get(USERS, params={"q": q}, headers=admin[1])
        return [u["email"] for u in response.json()["items"]]

    assert found("ADA.OBI") == ["ada.obi@camdock.test"]
    assert found("ada_obi") == []
    assert found("%") == ["100%@camdock.test"]


def test_filters_by_role_and_active_state(db, api_client, admin):
    add_user(db, email="t1@camdock.test", role="teacher")
    add_user(db, email="t2@camdock.test", role="teacher", is_active=False)
    add_user(db, email="p1@camdock.test", role="parent")

    def emails(**params):
        response = api_client.get(USERS, params=params, headers=admin[1])
        return [u["email"] for u in response.json()["items"]]

    assert emails(role="teacher") == ["t1@camdock.test", "t2@camdock.test"]
    assert emails(role="teacher", is_active="false") == ["t2@camdock.test"]
    assert emails(is_active="true", role="parent") == ["p1@camdock.test"]


@pytest.mark.parametrize(
    "params",
    [
        {"role": "superuser"},
        {"limit": 0},
        {"limit": 101},
        {"offset": -1},
        {"q": "x" * 101},
    ],
)
def test_bad_query_values_are_refused(api_client, admin, params):
    response = api_client.get(USERS, params=params, headers=admin[1])

    assert response.status_code == 422


def test_only_an_administrator_may_list_accounts(db, api_client):
    teacher = login_headers(db, api_client, "teacher")[1]
    parent = login_headers(db, api_client, "parent")[1]

    assert api_client.get(USERS).status_code == 401
    assert api_client.get(USERS, headers=teacher).status_code == 403
    assert api_client.get(USERS, headers=parent).status_code == 403


# --- PATCH /users/{id}: disabling ------------------------------------------------------


def test_disabling_ends_the_accounts_sessions_at_once(db, api_client, admin):
    user_id, teacher = login_headers(db, api_client, "teacher")
    assert api_client.get("/api/v1/auth/me", headers=teacher).status_code == 200

    response = patch(api_client, admin[1], user_id, {"is_active": False})

    assert response.status_code == 200
    assert response.json()["is_active"] is False
    assert api_client.get("/api/v1/auth/me", headers=teacher).status_code == 401


def test_re_enabling_does_not_bring_old_sessions_back(db, api_client, admin):
    user_id, teacher = login_headers(db, api_client, "teacher")
    patch(api_client, admin[1], user_id, {"is_active": False})

    response = patch(api_client, admin[1], user_id, {"is_active": True})

    assert response.json()["is_active"] is True
    assert api_client.get("/api/v1/auth/me", headers=teacher).status_code == 401


def test_a_disabled_account_cannot_sign_in_until_enabled(db, api_client, admin):
    user_id = add_user(db, email="amy@camdock.test", role="teacher")
    patch(api_client, admin[1], user_id, {"is_active": False})

    blocked = login(api_client, email="amy@camdock.test")
    patch(api_client, admin[1], user_id, {"is_active": True})
    allowed = login(api_client, email="amy@camdock.test")

    assert blocked.status_code == 401
    assert allowed.status_code == 200


def test_disabling_leaves_other_peoples_sessions_alone(db, api_client, admin):
    victim_id, _ = login_headers(db, api_client, "teacher")
    _, bystander = login_headers(db, api_client, "parent")

    patch(api_client, admin[1], victim_id, {"is_active": False})

    assert api_client.get("/api/v1/auth/me", headers=bystander).status_code == 200
    assert api_client.get("/api/v1/auth/me", headers=admin[1]).status_code == 200


def test_an_administrator_cannot_disable_themselves(db, api_client, admin):
    # A second administrator exists, so only the "own account" rule can refuse this.
    add_user(db, email="deputy@camdock.test", role="admin")

    response = patch(api_client, admin[1], admin[0], {"is_active": False})

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "conflict"
    assert "own account" in response.json()["error"]["message"]
    assert api_client.get("/api/v1/auth/me", headers=admin[1]).status_code == 200
    assert audits(db, "user.disable") == []


def test_another_administrator_can_be_disabled(db, api_client, admin):
    other = add_user(db, email="deputy@camdock.test", role="admin")

    response = patch(api_client, admin[1], other, {"is_active": False})

    assert response.status_code == 200
    assert response.json()["is_active"] is False


def test_the_last_active_administrator_cannot_be_disabled(db, session):
    """A stale actor (disabled meanwhile) is the only way to reach this rule in one
    request; the concurrent version is tested below on real connections."""
    only = add_user(db, email="only@camdock.test", role="admin")
    gone = add_user(db, email="gone@camdock.test", role="admin", is_active=False)

    with pytest.raises(AppError) as error:
        accounts.set_user_active(session, User(id=gone, role="admin"), only, False)

    assert error.value.status_code == 409
    assert "last active administrator" in error.value.message
    assert is_active(db, only) is True


def test_disabling_a_teacher_does_not_need_other_administrators(db, api_client, admin):
    teacher_id = add_user(db, email="amy@camdock.test", role="teacher")

    assert (
        patch(api_client, admin[1], teacher_id, {"is_active": False}).status_code == 200
    )


# --- no-ops, validation, audit ---------------------------------------------------------


def test_setting_the_same_value_changes_and_records_nothing(db, api_client, admin):
    user_id, teacher = login_headers(db, api_client, "teacher")

    again = patch(api_client, admin[1], user_id, {"is_active": True})

    assert again.status_code == 200
    assert again.json()["is_active"] is True
    assert audits(db, "user.enable", "user.disable") == []
    assert api_client.get("/api/v1/auth/me", headers=teacher).status_code == 200

    patch(api_client, admin[1], user_id, {"is_active": False})
    patch(api_client, admin[1], user_id, {"is_active": False})
    assert len(audits(db, "user.disable")) == 1


def test_an_unknown_account_is_a_404(api_client, admin):
    response = patch(api_client, admin[1], 999999, {"is_active": False})

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"is_active": "yes"},
        {"is_active": 1},
        {"is_active": None},
        {"is_active": False, "role": "admin"},
        {"is_active": False, "email": "x@camdock.test"},
        {"password": "a-new-password-12"},
    ],
)
def test_only_a_boolean_active_flag_is_accepted(db, api_client, admin, body):
    user_id = add_user(db, email="amy@camdock.test", role="teacher")

    response = patch(api_client, admin[1], user_id, body)

    assert response.status_code == 422
    assert is_active(db, user_id) is True


def test_a_non_numeric_id_is_refused(api_client, admin):
    assert patch(api_client, admin[1], "abc", {"is_active": False}).status_code == 422


def test_only_an_administrator_may_change_status(db, api_client):
    victim = add_user(db, email="amy@camdock.test", role="teacher")
    teacher = login_headers(db, api_client, "teacher")[1]
    parent = login_headers(db, api_client, "parent")[1]
    body = {"is_active": False}

    assert patch(api_client, {}, victim, body).status_code == 401
    assert patch(api_client, teacher, victim, body).status_code == 403
    assert patch(api_client, parent, victim, body).status_code == 403
    assert is_active(db, victim) is True


def test_each_change_is_audited_without_secrets(db, api_client, admin):
    user_id = add_user(db, email="amy@camdock.test", role="teacher")

    patch(api_client, admin[1], user_id, {"is_active": False})
    patch(api_client, admin[1], user_id, {"is_active": True})

    rows = db.execute(
        select(
            AuditLog.action,
            AuditLog.actor_user_id,
            AuditLog.entity_id,
            AuditLog.changes,
        )
        .where(AuditLog.action.in_(["user.disable", "user.enable"]))
        .order_by(AuditLog.id)
    ).all()
    assert [r.action for r in rows] == ["user.disable", "user.enable"]
    for row in rows:
        assert row.actor_user_id == admin[0]
        assert row.entity_id == user_id
        assert row.changes == {"email": "amy@camdock.test", "role": "teacher"}


def test_the_response_matches_the_directory_entry(db, api_client, admin):
    user_id = add_user(db, email="amy@camdock.test", role="teacher")

    changed = patch(api_client, admin[1], user_id, {"is_active": False}).json()
    listed = api_client.get(USERS, params={"q": "amy"}, headers=admin[1]).json()[
        "items"
    ][0]

    assert changed == listed


# --- revoke_user_sessions ----------------------------------------------------------------


def test_revoking_ends_only_that_users_usable_sessions(db, session, api_client):
    one = add_user(db, email="one@camdock.test", role="teacher")
    two = add_user(db, email="two@camdock.test", role="teacher")
    login_token(api_client, email="one@camdock.test")
    login_token(api_client, email="one@camdock.test")
    survivor = login_token(api_client, email="two@camdock.test")

    ended = auth_service.revoke_user_sessions(session, one)

    assert ended == 2
    assert auth_service.revoke_user_sessions(session, one) == 0
    assert auth_service.get_active_session(session, survivor) is not None
    assert two != one


def test_revoking_can_spare_one_session(db, session, api_client):
    user_id = add_user(db, email="one@camdock.test", role="teacher")
    keep = login_token(api_client, email="one@camdock.test")
    drop = login_token(api_client, email="one@camdock.test")
    kept_id = (
        db.execute(
            select(AuthSession.id)
            .where(AuthSession.user_id == user_id)
            .order_by(AuthSession.id)
        )
        .scalars()
        .first()
    )

    ended = auth_service.revoke_user_sessions(
        session, user_id, except_session_id=kept_id
    )

    assert ended == 1
    assert auth_service.get_active_session(session, keep) is not None
    assert auth_service.get_active_session(session, drop) is None


def test_the_contract_lists_both_endpoints(api_client):
    paths = api_client.app.openapi()["paths"]

    assert {"get", "post"} <= set(paths["/api/v1/users"])
    assert "patch" in paths["/api/v1/users/{user_id}"]


# --- two administrators at the same moment (real, committed rows) -----------------------


@pytest.fixture
def two_admins(migrated_engine):
    wipe(migrated_engine)
    with Session(migrated_engine) as session, session.begin():
        ids = [
            add_user(session, email=f"admin{i}@camdock.test", role="admin")
            for i in (1, 2)
        ]
    yield ids
    wipe(migrated_engine)


def test_two_administrators_cannot_disable_each_other(
    migrated_engine, two_admins, monkeypatch
):
    first, second = two_admins
    real = auth_service.revoke_user_sessions

    def slow(db, user_id, **kwargs):
        time.sleep(0.5)  # holds the locks while the other request waits
        return real(db, user_id, **kwargs)

    monkeypatch.setattr(accounts.auth_service, "revoke_user_sessions", slow)
    start = threading.Barrier(2)
    outcomes: list[tuple[str, int]] = []
    guard = threading.Lock()

    def worker(actor_id, target_id):
        with Session(migrated_engine) as session:
            start.wait()
            try:
                actor = session.get(User, actor_id)
                accounts.set_user_active(session, actor, target_id, False)
                session.commit()
                result = ("ok", 200)
            except AppError as error:
                session.rollback()
                result = ("refused", error.status_code)
            with guard:
                outcomes.append(result)

    threads = [
        threading.Thread(target=worker, args=(first, second)),
        threading.Thread(target=worker, args=(second, first)),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)
        assert not thread.is_alive(), "a request hung (deadlock?)"

    with Session(migrated_engine) as session:
        active = session.execute(
            select(func.count()).select_from(User).where(User.is_active.is_(True))
        ).scalar_one()
    assert sorted(outcomes) == [("ok", 200), ("refused", 409)]
    assert active == 1


def test_a_disable_that_loses_the_race_leaves_no_audit_row(migrated_engine, two_admins):
    first, second = two_admins
    with Session(migrated_engine) as session, session.begin():
        session.execute(update(User).where(User.id == second).values(is_active=False))

    with Session(migrated_engine) as session:
        actor = session.get(User, second)
        with pytest.raises(AppError):
            accounts.set_user_active(session, actor, first, False)
        session.rollback()

    with Session(migrated_engine) as session:
        rows = session.execute(
            select(func.count())
            .select_from(AuditLog)
            .where(AuditLog.action == "user.disable")
        ).scalar_one()
    assert rows == 0
