from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, insert, select, update

from app.db.base import Base
from app.models import AuthSession, User
from tests.support import assert_rejected

TOKEN_HASH = "a" * 64


def add_user(db, email="a@camdock.test") -> int:
    return db.execute(
        insert(User)
        .values(email=email, password_hash="hash", role="admin")
        .returning(User.id)
    ).scalar_one()


def add_session(db, user_id, **overrides) -> int:
    values = {
        "user_id": user_id,
        "token_hash": TOKEN_HASH,
        "expires_at": datetime.now(UTC) + timedelta(hours=12),
    }
    return db.execute(
        insert(AuthSession).values(**{**values, **overrides}).returning(AuthSession.id)
    ).scalar_one()


def test_models_registered_on_metadata():
    assert "auth_sessions" in Base.metadata.tables


def test_session_is_accepted_and_starts_unrevoked(db):
    user_id = add_user(db)
    session_id = add_session(db, user_id)
    row = db.execute(select(AuthSession).where(AuthSession.id == session_id)).one()
    assert row.revoked_at is None
    assert row.created_at is not None
    assert row.user_id == user_id


def test_session_can_be_revoked(db):
    session_id = add_session(db, add_user(db))
    db.execute(
        update(AuthSession)
        .where(AuthSession.id == session_id)
        .values(revoked_at=datetime.now(UTC))
    )
    revoked = db.execute(
        select(AuthSession.revoked_at).where(AuthSession.id == session_id)
    ).scalar_one()
    assert revoked is not None


def test_token_hash_is_unique(db):
    user_id = add_user(db)
    add_session(db, user_id)
    assert_rejected(
        db,
        insert(AuthSession).values(
            user_id=user_id,
            token_hash=TOKEN_HASH,
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        ),
        "uq_auth_sessions_token_hash",
    )


def test_token_hash_must_be_64_characters(db):
    # Longer values are stopped earlier by the VARCHAR(64) column type.
    assert_rejected(
        db,
        insert(AuthSession).values(
            user_id=add_user(db),
            token_hash="short",
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        ),
        "ck_auth_sessions_token_hash_length",
    )


def test_expiry_must_be_after_creation(db):
    now = datetime.now(UTC)
    assert_rejected(
        db,
        insert(AuthSession).values(
            user_id=add_user(db),
            token_hash=TOKEN_HASH,
            created_at=now,
            expires_at=now,
        ),
        "ck_auth_sessions_expires_after_created",
    )


def test_user_must_exist(db):
    assert_rejected(
        db,
        insert(AuthSession).values(
            user_id=999999,
            token_hash=TOKEN_HASH,
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        ),
        "fk_auth_sessions_user_id_users",
    )


def test_user_with_sessions_cannot_be_deleted(db):
    user_id = add_user(db)
    add_session(db, user_id)
    assert_rejected(
        db, delete(User).where(User.id == user_id), "fk_auth_sessions_user_id_users"
    )


def test_sessions_can_be_found_by_user(db):
    first, second = add_user(db), add_user(db, email="b@camdock.test")
    add_session(db, first)
    add_session(db, second, token_hash="b" * 64)
    found = (
        db.execute(select(AuthSession.token_hash).where(AuthSession.user_id == second))
        .scalars()
        .all()
    )
    assert found == ["b" * 64]
