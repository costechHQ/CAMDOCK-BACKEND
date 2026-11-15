"""`python -m app.cli reset-password`: recovery from the server when no one can issue a code."""

import threading
import time

import pytest
from sqlalchemy import func, select, update
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from app import cli
from app.core.security import verify_password
from app.models import AuditLog, PasswordReset, User
from app.services import accounts, password_resets
from app.services import auth as auth_service
from tests.test_auth_api import PASSWORD, add_user, login, login_token
from tests.test_fee_payments_concurrency import wipe
from tests.test_teachers_api import bearer

EMAIL = "head@camdock.test"
NEW = "a recovered passphrase 42"


@pytest.fixture
def session(db):
    with Session(
        bind=db, join_transaction_mode="create_savepoint", expire_on_commit=False
    ) as s:
        yield s


@pytest.fixture
def head(db):
    return add_user(db, email=EMAIL, role="admin")


def stored_hash(db, user_id) -> str:
    return db.execute(select(User.password_hash).where(User.id == user_id)).scalar_one()


def audits(db):
    return db.execute(
        select(AuditLog.__table__).where(AuditLog.action == "user.password_reset_cli")
    ).all()


# --- the service ------------------------------------------------------------------------


def test_the_password_is_replaced_by_a_fresh_hash(db, session, head):
    before = stored_hash(db, head)

    user, ended = accounts.reset_password_from_server(session, EMAIL, NEW)

    after = stored_hash(db, head)
    assert user.id == head and ended == 0
    assert after != before and after.startswith("$argon2id$")
    assert verify_password(NEW, after) and not verify_password(PASSWORD, after)
    assert NEW not in after


def test_every_session_of_the_account_ends(db, session, api_client, head):
    first = login_token(api_client, email=EMAIL)
    second = login_token(api_client, email=EMAIL)
    add_user(db, email="other@camdock.test", role="parent")
    bystander = login_token(api_client, email="other@camdock.test")

    _, ended = accounts.reset_password_from_server(session, EMAIL, NEW)

    assert ended == 2
    assert auth_service.get_active_session(session, first) is None
    assert auth_service.get_active_session(session, second) is None
    assert auth_service.get_active_session(session, bystander) is not None


def test_a_live_reset_code_is_voided(db, session, head):
    helper = add_user(db, email="deputy@camdock.test", role="admin")
    password_resets.issue_reset_code(session, session.get(User, helper), head)

    accounts.reset_password_from_server(session, EMAIL, NEW)

    row = db.execute(select(PasswordReset.__table__)).one()
    assert row.voided_at is not None and row.used_at is None


@pytest.mark.parametrize("role", ["admin", "teacher", "parent"])
def test_it_works_for_any_role(db, session, role):
    uid = add_user(db, email=f"{role}@camdock.test", role=role)

    accounts.reset_password_from_server(session, f"{role}@camdock.test", NEW)

    assert verify_password(NEW, stored_hash(db, uid))


def test_the_email_is_trimmed_and_case_does_not_matter(db, session, head):
    accounts.reset_password_from_server(session, "  Head@CAMDOCK.test ", NEW)

    assert verify_password(NEW, stored_hash(db, head))


def test_it_is_audited_as_a_system_action_without_secrets(
    db, session, head, api_client
):
    login_token(api_client, email=EMAIL)

    accounts.reset_password_from_server(session, EMAIL, NEW)

    (row,) = db.execute(
        select(
            AuditLog.actor_user_id,
            AuditLog.entity_type,
            AuditLog.entity_id,
            AuditLog.changes,
        ).where(AuditLog.action == "user.password_reset_cli")
    ).all()
    assert row.actor_user_id is None
    assert (row.entity_type, row.entity_id) == ("user", head)
    assert row.changes == {"email": EMAIL, "role": "admin", "sessions_ended": 1}
    assert NEW not in str(row.changes) and "argon2" not in str(row.changes)


@pytest.mark.parametrize(
    ("email", "message"),
    [
        ("nobody@camdock.test", "No account with that email"),
        ("not-an-email", "valid email"),
        ("", "valid email"),
    ],
)
def test_an_unusable_email_changes_nothing(db, session, head, email, message):
    before = stored_hash(db, head)

    with pytest.raises(accounts.AccountError, match=message):
        accounts.reset_password_from_server(session, email, NEW)

    assert stored_hash(db, head) == before
    assert audits(db) == []


def test_a_disabled_account_is_refused(db, session):
    uid = add_user(db, email="gone@camdock.test", role="teacher", is_active=False)
    before = stored_hash(db, uid)

    with pytest.raises(accounts.AccountError, match="disabled"):
        accounts.reset_password_from_server(session, "gone@camdock.test", NEW)

    assert stored_hash(db, uid) == before
    assert audits(db) == []


@pytest.mark.parametrize(
    "password",
    ["", "short", " " * 20, EMAIL, "x" * 2000],
    ids=lambda p: p[:8] or "empty",
)
def test_a_password_that_breaks_the_rules_changes_nothing(db, session, head, password):
    before = stored_hash(db, head)

    with pytest.raises(ValueError):
        accounts.reset_password_from_server(session, EMAIL, password)

    assert stored_hash(db, head) == before
    assert audits(db) == []


def test_finding_an_account_reports_the_same_problems(db, session, head):
    assert accounts.find_resettable_account(session, EMAIL.upper()).id == head
    with pytest.raises(accounts.AccountError, match="No account"):
        accounts.find_resettable_account(session, "nobody@camdock.test")


# --- the command line ------------------------------------------------------------------------


@pytest.fixture
def run(db, monkeypatch):
    monkeypatch.delenv(cli.PASSWORD_ENV, raising=False)
    monkeypatch.setattr(cli.sys.stdin, "isatty", lambda: False)

    def factory():
        return Session(
            bind=db, join_transaction_mode="create_savepoint", expire_on_commit=False
        )

    monkeypatch.setattr(cli, "get_session_factory", lambda: factory)
    return lambda *args: cli.main(["reset-password", *args])


def test_cli_resets_from_the_environment_with_yes(
    db, run, head, api_client, monkeypatch, capsys
):
    token = login_token(api_client, email=EMAIL)
    monkeypatch.setenv(cli.PASSWORD_ENV, NEW)

    assert run("--email", EMAIL, "--yes") == 0

    out = capsys.readouterr()
    assert (
        out.out.strip()
        == f"Password reset for {EMAIL} (id {head}). 1 session(s) ended."
    )
    assert NEW not in out.out + out.err
    assert login(api_client, email=EMAIL).status_code == 401
    assert login(api_client, email=EMAIL, password=NEW).status_code == 200
    assert api_client.get("/api/v1/auth/me", headers=bearer(token)).status_code == 401


def test_cli_asks_to_confirm_and_names_the_account(db, run, head, monkeypatch, capsys):
    prompts = []
    monkeypatch.setattr(cli.sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr("builtins.input", lambda prompt: prompts.append(prompt) or "y")
    answers = iter([NEW, NEW])
    monkeypatch.setattr(cli.getpass, "getpass", lambda prompt: next(answers))

    assert run("--email", EMAIL) == 0

    assert prompts == [f"Reset the password of admin {EMAIL}? [y/N] "]
    assert verify_password(NEW, stored_hash(db, head))
    assert NEW not in "".join(capsys.readouterr())


@pytest.mark.parametrize("answer", ["n", "N", "", "no", "maybe", "  "])
def test_cli_declining_changes_nothing_and_asks_for_no_password(
    db, run, head, monkeypatch, capsys, answer
):
    before = stored_hash(db, head)
    monkeypatch.setattr(cli.sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr("builtins.input", lambda prompt: answer)
    asked = []
    monkeypatch.setattr(
        cli.getpass, "getpass", lambda prompt: asked.append(prompt) or NEW
    )

    assert run("--email", EMAIL) == 1

    assert "Cancelled" in capsys.readouterr().err
    assert asked == []
    assert stored_hash(db, head) == before
    assert audits(db) == []


def test_cli_without_a_terminal_needs_yes(db, run, head, monkeypatch, capsys):
    monkeypatch.setenv(cli.PASSWORD_ENV, NEW)
    before = stored_hash(db, head)

    assert run("--email", EMAIL) == 1

    assert "--yes" in capsys.readouterr().err
    assert stored_hash(db, head) == before


def test_cli_prompts_twice_for_the_password(db, run, head, monkeypatch, capsys):
    prompts = []
    answers = iter([NEW, NEW])
    monkeypatch.setattr(cli.sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(
        cli.getpass, "getpass", lambda prompt: prompts.append(prompt) or next(answers)
    )

    assert run("--email", EMAIL, "--yes") == 0

    assert prompts == ["Password: ", "Confirm password: "]


def test_cli_rejects_a_mismatched_confirmation(db, run, head, monkeypatch, capsys):
    before = stored_hash(db, head)
    answers = iter([NEW, "something else entirely"])
    monkeypatch.setattr(cli.sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(cli.getpass, "getpass", lambda prompt: next(answers))

    assert run("--email", EMAIL, "--yes") == 1

    assert "do not match" in capsys.readouterr().err
    assert stored_hash(db, head) == before


def test_cli_without_a_password_source_fails_clearly(db, run, head, capsys):
    assert run("--email", EMAIL, "--yes") == 1

    assert cli.PASSWORD_ENV in capsys.readouterr().err


def test_cli_reports_each_refusal_plainly(db, run, head, monkeypatch, capsys):
    add_user(db, email="gone@camdock.test", role="parent", is_active=False)
    monkeypatch.setenv(cli.PASSWORD_ENV, NEW)

    assert run("--email", "nobody@camdock.test", "--yes") == 1
    assert "No account with that email" in capsys.readouterr().err
    assert run("--email", "gone@camdock.test", "--yes") == 1
    assert "disabled" in capsys.readouterr().err
    assert run("--email", "nonsense", "--yes") == 1
    assert "valid email" in capsys.readouterr().err

    monkeypatch.setenv(cli.PASSWORD_ENV, "short")
    assert run("--email", EMAIL, "--yes") == 1
    assert "at least 12" in capsys.readouterr().err
    assert audits(db) == []


def test_cli_has_no_password_option(capsys):
    with pytest.raises(SystemExit) as excinfo:
        cli.main(["reset-password", "--email", EMAIL, "--password", NEW])

    assert excinfo.value.code == 2
    assert NEW not in capsys.readouterr().out


def test_cli_requires_an_email():
    with pytest.raises(SystemExit) as excinfo:
        cli.main(["reset-password", "--yes"])

    assert excinfo.value.code == 2


def test_cli_database_failure_hides_details(monkeypatch, capsys):
    def broken():
        raise OperationalError("SELECT 1", {}, Exception("host=db.internal pw=s3cret"))

    monkeypatch.setenv(cli.PASSWORD_ENV, NEW)
    monkeypatch.setattr(cli, "get_session_factory", broken)

    assert cli.main(["reset-password", "--email", EMAIL, "--yes"]) == 1

    err = capsys.readouterr().err
    assert "DATABASE_URL" in err
    assert "s3cret" not in err and "db.internal" not in err


def test_create_admin_still_works(db, run, monkeypatch, capsys):
    monkeypatch.setenv(cli.PASSWORD_ENV, NEW)

    assert cli.main(["create-admin", "--email", "new@camdock.test"]) == 0

    assert "Created admin account new@camdock.test" in capsys.readouterr().out
    assert db.execute(select(func.count()).select_from(User)).scalar_one() == 1


# --- a reset while the user changes their own password (real, committed rows) --------------


def test_a_reset_wins_over_a_simultaneous_password_change(migrated_engine, monkeypatch):
    wipe(migrated_engine)
    with Session(migrated_engine) as setup, setup.begin():
        uid = add_user(setup, email=EMAIL, role="admin")
    real = accounts.hash_password

    def slow(plain):
        time.sleep(0.5)  # both would otherwise work from the same old password
        return real(plain)

    monkeypatch.setattr(accounts, "hash_password", slow)
    start = threading.Barrier(2)

    def reset():
        with Session(migrated_engine) as session:
            start.wait()
            accounts.reset_password_from_server(session, EMAIL, NEW)
            session.commit()

    def change():
        with Session(migrated_engine) as session:
            start.wait()
            time.sleep(0.1)
            try:
                user = session.get(User, uid)
                accounts.change_own_password(
                    session, user, 0, PASSWORD, "my own chosen passphrase 8"
                )
                session.commit()
            except accounts.IncorrectPassword:
                session.rollback()

    threads = [threading.Thread(target=reset), threading.Thread(target=change)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)
        assert not thread.is_alive(), "a request hung"

    try:
        with Session(migrated_engine) as session:
            final = session.execute(
                select(User.password_hash).where(User.id == uid)
            ).scalar_one()
        assert verify_password(NEW, final)
    finally:
        wipe(migrated_engine)


# --- a disabled account is refused before anything is asked ---------------------------------


def test_finding_a_disabled_account_is_refused(db, session):
    add_user(db, email="gone@camdock.test", role="teacher", is_active=False)

    with pytest.raises(accounts.AccountError, match="disabled"):
        accounts.find_resettable_account(session, "gone@camdock.test")


def test_cli_does_not_ask_to_confirm_a_disabled_account(db, run, monkeypatch, capsys):
    add_user(db, email="gone@camdock.test", role="teacher", is_active=False)
    monkeypatch.setattr(cli.sys.stdin, "isatty", lambda: True)

    def never(prompt):
        raise AssertionError("asked to confirm a disabled account")

    monkeypatch.setattr("builtins.input", never)

    assert run("--email", "gone@camdock.test") == 1

    assert "disabled" in capsys.readouterr().err


# --- against changes made by someone else at the same time (real, committed rows) -----------


def test_a_reset_cannot_overtake_a_simultaneous_disable(migrated_engine, monkeypatch):
    wipe(migrated_engine)
    with Session(migrated_engine) as setup, setup.begin():
        admin_id = add_user(setup, email="admin@camdock.test", role="admin")
        target = add_user(setup, email="amy@camdock.test", role="teacher")
    original = None
    with Session(migrated_engine) as s:
        original = s.execute(
            select(User.password_hash).where(User.id == target)
        ).scalar_one()
    real = auth_service.revoke_user_sessions

    def slow(db, user_id, **kwargs):
        time.sleep(0.5)  # holds the lock the disable took
        return real(db, user_id, **kwargs)

    monkeypatch.setattr(accounts.auth_service, "revoke_user_sessions", slow)
    outcome: list[str] = []

    def disable():
        with Session(migrated_engine) as session:
            actor = session.get(User, admin_id)
            accounts.set_user_active(session, actor, target, False)
            session.commit()

    def reset():
        time.sleep(0.15)
        with Session(migrated_engine) as session:
            try:
                accounts.reset_password_from_server(session, "amy@camdock.test", NEW)
                session.commit()
                outcome.append("reset")
            except accounts.AccountError:
                session.rollback()
                outcome.append("refused")

    threads = [threading.Thread(target=disable), threading.Thread(target=reset)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)
        assert not thread.is_alive(), "a request hung"

    try:
        with Session(migrated_engine) as session:
            row = session.execute(
                select(User.password_hash, User.is_active).where(User.id == target)
            ).one()
        assert outcome == ["refused"]
        assert row.is_active is False
        assert row.password_hash == original
    finally:
        wipe(migrated_engine)


def test_a_user_already_loaded_in_the_session_is_read_afresh(migrated_engine):
    wipe(migrated_engine)
    with Session(migrated_engine) as setup, setup.begin():
        target = add_user(setup, email="amy@camdock.test", role="teacher")
    try:
        with Session(migrated_engine) as session:
            # Keep a reference: the session only remembers objects something still holds.
            loaded = session.get(User, target)  # loaded while still active
            assert loaded.is_active is True
            with Session(migrated_engine) as other, other.begin():
                other.execute(
                    update(User).where(User.id == target).values(is_active=False)
                )

            with pytest.raises(accounts.AccountError, match="disabled"):
                accounts.reset_password_from_server(session, "amy@camdock.test", NEW)
    finally:
        wipe(migrated_engine)
