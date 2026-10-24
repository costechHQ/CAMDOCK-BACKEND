import pytest
from sqlalchemy import delete, insert, select, text, update
from sqlalchemy.exc import IntegrityError

from app.db.base import Base
from app.models import AuditLog, User
from tests.support import assert_rejected


def add_user(db) -> int:
    return db.execute(
        insert(User)
        .values(email="a@camdock.test", password_hash="hash", role="admin")
        .returning(User.id)
    ).scalar_one()


def add_log(db, **overrides) -> int:
    values = {"action": "result.update", "entity_type": "result", "entity_id": 7}
    return db.execute(
        insert(AuditLog).values(**{**values, **overrides}).returning(AuditLog.id)
    ).scalar_one()


def test_models_registered_on_metadata():
    assert "audit_logs" in Base.metadata.tables


def test_row_with_json_changes_is_accepted(db):
    actor = add_user(db)
    log_id = add_log(
        db,
        actor_user_id=actor,
        changes={"score": {"old": "60.00", "new": "65.50"}},
    )
    row = db.execute(select(AuditLog).where(AuditLog.id == log_id)).one()
    assert row.changes["score"]["new"] == "65.50"
    assert row.created_at is not None
    assert row.actor_user_id == actor


def test_system_action_without_actor_is_accepted(db):
    log_id = add_log(db, action="seed.run", entity_type="system", entity_id=None)
    row = db.execute(select(AuditLog).where(AuditLog.id == log_id)).one()
    assert row.actor_user_id is None and row.changes is None


def test_actor_must_exist(db):
    assert_rejected(
        db,
        insert(AuditLog).values(
            actor_user_id=999999,
            action="result.update",
            entity_type="result",
            entity_id=1,
        ),
        "fk_audit_logs_actor_user_id_users",
    )


def test_logged_actor_cannot_be_deleted(db):
    actor = add_user(db)
    add_log(db, actor_user_id=actor)
    assert_rejected(
        db, delete(User).where(User.id == actor), "fk_audit_logs_actor_user_id_users"
    )


def test_entity_history_index_exists(db):
    names = {
        row.indexname
        for row in db.execute(
            text("SELECT indexname FROM pg_indexes WHERE tablename = 'audit_logs'")
        )
    }
    assert {"ix_audit_logs_entity", "ix_audit_logs_created_at"} <= names


def test_audit_rows_cannot_be_updated(db):
    log_id = add_log(db)
    with pytest.raises(IntegrityError, match="append-only"), db.begin_nested():
        db.execute(
            update(AuditLog).where(AuditLog.id == log_id).values(action="tampered")
        )


def test_audit_rows_cannot_be_deleted(db):
    log_id = add_log(db)
    with pytest.raises(IntegrityError, match="append-only"), db.begin_nested():
        db.execute(delete(AuditLog).where(AuditLog.id == log_id))
    assert db.execute(select(AuditLog.id)).scalar_one() == log_id


def test_database_does_not_filter_sensitive_fields(db):
    # Known gap, by design: the database cannot tell a password hash from other JSON.
    # The code that writes audit events must strip secrets before inserting.
    add_log(db, changes={"password_hash": "x"})
