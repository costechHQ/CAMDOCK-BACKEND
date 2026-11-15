import pytest
from sqlalchemy import func, select, update
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from app import cli
from app.core.security import PasswordPolicyError, verify_password
from app.models import AuditLog, User
from app.services import accounts
from tests.test_auth_api import LOGIN_URL, ME_URL, bearer

EMAIL = "head@camdock.test"
PASSWORD = "a long enough passphrase"


@pytest.fixture
def session(db):
    """An ORM session inside the rolled-back `db` transaction (savepoints)."""
    with Session(
        bind=db, join_transaction_mode="create_savepoint", expire_on_commit=False
    ) as session:
        yield session


def count(db, model) -> int:
    return db.execute(select(func.count()).select_from(model)).scalar_one()


# --- service -------------------------------------------------------------------------


def test_creates_an_active_admin_with_a_hashed_password(db, session):
    user = accounts.create_admin(session, EMAIL, PASSWORD)

    row = db.execute(select(User).where(User.id == user.id)).one()
    assert (row.email, row.role, row.is_active) == (EMAIL, "admin", True)
    assert row.password_hash.startswith("$argon2id$")
    assert PASSWORD not in row.password_hash
    assert verify_password(PASSWORD, row.password_hash)


def test_email_is_trimmed_and_lowercased(db, session):
    user = accounts.create_admin(session, "  Head@Camdock.TEST ", PASSWORD)

    assert user.email == EMAIL


def test_creation_is_audited_without_secrets(db, session):
    user = accounts.create_admin(session, EMAIL, PASSWORD)

    log = db.execute(select(AuditLog)).one()
    assert log.action == "user.create"
    assert (log.entity_type, log.entity_id) == ("user", user.id)
    assert log.actor_user_id is None
    assert log.changes == {"email": EMAIL, "role": "admin"}
    assert PASSWORD not in str(log.changes)
    assert "argon2" not in str(log.changes)


@pytest.mark.parametrize("existing_role", ["admin", "teacher", "parent"])
def test_duplicate_email_is_refused_and_changes_nothing(db, session, existing_role):
    accounts.create_admin(session, EMAIL, PASSWORD)
    db.execute(update(User).values(role=existing_role))
    original_hash = db.execute(select(User.password_hash)).scalar_one()
    users_before, logs_before = count(db, User), count(db, AuditLog)

    with pytest.raises(accounts.AccountError, match="already exists"):
        accounts.create_admin(session, EMAIL.upper(), "a different passphrase!")

    assert (count(db, User), count(db, AuditLog)) == (users_before, logs_before)
    assert db.execute(select(User.password_hash)).scalar_one() == original_hash
    assert db.execute(select(User.role)).scalar_one() == existing_role


@pytest.mark.parametrize(
    "password",
    ["", "short", " " * 20, EMAIL, "x" * 2000],
    ids=lambda p: p[:8] or "empty",
)
def test_bad_password_creates_nothing(db, session, password):
    with pytest.raises(PasswordPolicyError):
        accounts.create_admin(session, EMAIL, password)

    assert (count(db, User), count(db, AuditLog)) == (0, 0)


@pytest.mark.parametrize(
    "email", ["", "plain", "a@b", "@camdock.test", "a b@camdock.test", "a@@c.test"]
)
def test_bad_email_creates_nothing(db, session, email):
    with pytest.raises(accounts.AccountError, match="valid email"):
        accounts.create_admin(session, email, PASSWORD)

    assert count(db, User) == 0


def test_created_admin_can_log_in(db, session, api_client):
    accounts.create_admin(session, EMAIL, PASSWORD)
    session.commit()

    response = api_client.post(LOGIN_URL, json={"email": EMAIL, "password": PASSWORD})
    assert response.status_code == 200
    me = api_client.get(ME_URL, headers=bearer(response.json()["access_token"]))
    assert me.json()["role"] == "admin"


# --- command line ----------------------------------------------------------------------


@pytest.fixture
def run(db, monkeypatch):
    """Run the CLI against the test transaction, with a clean password environment."""
    monkeypatch.delenv(cli.PASSWORD_ENV, raising=False)

    def factory():
        return Session(
            bind=db, join_transaction_mode="create_savepoint", expire_on_commit=False
        )

    monkeypatch.setattr(cli, "get_session_factory", lambda: factory)
    return lambda *args: cli.main(["create-admin", *args])


def test_cli_creates_admin_from_the_environment(db, run, monkeypatch, capsys):
    monkeypatch.setenv(cli.PASSWORD_ENV, PASSWORD)

    assert run("--email", EMAIL) == 0

    out = capsys.readouterr()
    assert out.out.startswith(f"Created admin account {EMAIL} (id ")
    assert PASSWORD not in out.out + out.err
    assert count(db, User) == 1


def test_cli_prompts_twice_when_interactive(db, run, monkeypatch, capsys):
    prompts = []
    answers = iter([PASSWORD, PASSWORD])
    monkeypatch.setattr(cli.sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(
        cli.getpass, "getpass", lambda prompt: prompts.append(prompt) or next(answers)
    )

    assert run("--email", EMAIL) == 0

    assert prompts == ["Password: ", "Confirm password: "]
    captured = capsys.readouterr()
    assert PASSWORD not in captured.out + captured.err
    assert count(db, User) == 1


def test_cli_rejects_mismatched_confirmation(db, run, monkeypatch, capsys):
    answers = iter([PASSWORD, "something else entirely"])
    monkeypatch.setattr(cli.sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(cli.getpass, "getpass", lambda prompt: next(answers))

    assert run("--email", EMAIL) == 1

    assert "do not match" in capsys.readouterr().err
    assert count(db, User) == 0


def test_cli_without_a_password_source_fails_clearly(db, run, monkeypatch, capsys):
    monkeypatch.setattr(cli.sys.stdin, "isatty", lambda: False)

    assert run("--email", EMAIL) == 1

    assert cli.PASSWORD_ENV in capsys.readouterr().err
    assert count(db, User) == 0


def test_cli_reports_policy_and_duplicate_errors(db, run, monkeypatch, capsys):
    monkeypatch.setenv(cli.PASSWORD_ENV, "short")
    assert run("--email", EMAIL) == 1
    assert "at least 12" in capsys.readouterr().err

    monkeypatch.setenv(cli.PASSWORD_ENV, PASSWORD)
    assert run("--email", EMAIL) == 0
    capsys.readouterr()
    assert run("--email", EMAIL) == 1
    assert "already exists" in capsys.readouterr().err
    assert count(db, User) == 1


def test_cli_has_no_password_option(capsys):
    with pytest.raises(SystemExit) as excinfo:
        cli.main(["create-admin", "--email", EMAIL, "--password", PASSWORD])

    assert excinfo.value.code == 2
    assert PASSWORD not in capsys.readouterr().out


def test_cli_requires_an_email():
    with pytest.raises(SystemExit) as excinfo:
        cli.main(["create-admin"])

    assert excinfo.value.code == 2


def test_cli_database_failure_hides_details(monkeypatch, capsys):
    def broken():
        raise OperationalError("SELECT 1", {}, Exception("host=db.internal pw=s3cret"))

    monkeypatch.setenv(cli.PASSWORD_ENV, PASSWORD)
    monkeypatch.setattr(cli, "get_session_factory", broken)

    assert cli.main(["create-admin", "--email", EMAIL]) == 1

    err = capsys.readouterr().err
    assert "DATABASE_URL" in err
    assert "s3cret" not in err
    assert "db.internal" not in err
