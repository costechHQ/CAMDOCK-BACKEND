import pytest
from sqlalchemy import delete, insert, select

from app.db.base import Base
from app.models import (
    SchoolClass,
    Subject,
    Teacher,
    TeacherClass,
    TeacherSubject,
    User,
)
from tests.support import assert_rejected

USER = {"email": "ada@camdock.test", "password_hash": "hash", "role": "teacher"}


def add(db, model, **values) -> int:
    return db.execute(insert(model).values(**values).returning(model.id)).scalar_one()


def add_user(db, **overrides) -> int:
    return add(db, User, **{**USER, **overrides})


def add_teacher(db, user_id=None, full_name="Ada Obi") -> int:
    return add(db, Teacher, user_id=user_id, full_name=full_name)


def add_class(db) -> int:
    return add(db, SchoolClass, name="JSS 1", level="junior_secondary")


def test_models_registered_on_metadata():
    assert {"users", "teachers", "teacher_classes", "teacher_subjects"} <= set(
        Base.metadata.tables
    )


def test_user_has_no_plaintext_password_column():
    assert "password" not in User.__table__.columns
    assert "password_hash" in User.__table__.columns


def test_valid_user_gets_defaults(db):
    user_id = add_user(db)
    row = db.execute(select(User).where(User.id == user_id)).one()
    assert row.is_active is True
    assert row.last_login_at is None
    assert row.created_at is not None and row.updated_at is not None


@pytest.mark.parametrize("role", ["admin", "teacher", "parent"])
def test_allowed_roles_are_accepted(db, role):
    add_user(db, role=role)


def test_unknown_role_rejected(db):
    assert_rejected(
        db, insert(User).values(**{**USER, "role": "student"}), "ck_users_valid_role"
    )


def test_duplicate_email_rejected(db):
    add_user(db)
    assert_rejected(db, insert(User).values(**USER), "uq_users_email")


def test_mixed_case_email_cannot_be_stored(db):
    # Together with the unique constraint this stops two accounts differing only by case.
    assert_rejected(
        db,
        insert(User).values(**{**USER, "email": "Ada@Camdock.test"}),
        "ck_users_email_lowercase",
    )


def test_empty_password_hash_rejected(db):
    assert_rejected(
        db,
        insert(User).values(**{**USER, "password_hash": ""}),
        "ck_users_password_hash_not_empty",
    )


def test_teacher_without_account_is_allowed(db):
    add_teacher(db)
    add_teacher(db, full_name="Chi Eze")


def test_teacher_user_link_is_unique(db):
    user_id = add_user(db)
    add_teacher(db, user_id)
    assert_rejected(
        db,
        insert(Teacher).values(user_id=user_id, full_name="Someone Else"),
        "uq_teachers_user_id",
    )


def test_teacher_requires_existing_user(db):
    assert_rejected(
        db,
        insert(Teacher).values(user_id=999999, full_name="Ada Obi"),
        "fk_teachers_user_id_users",
    )


def test_linked_user_cannot_be_deleted(db):
    user_id = add_user(db)
    add_teacher(db, user_id)
    assert_rejected(
        db, delete(User).where(User.id == user_id), "fk_teachers_user_id_users"
    )


def test_teacher_class_pairs(db):
    teacher_id = add_teacher(db)
    class_id = add_class(db)
    db.execute(insert(TeacherClass).values(teacher_id=teacher_id, class_id=class_id))
    assert_rejected(
        db,
        insert(TeacherClass).values(teacher_id=teacher_id, class_id=class_id),
        "pk_teacher_classes",
    )
    assert_rejected(
        db,
        insert(TeacherClass).values(teacher_id=999999, class_id=class_id),
        "fk_teacher_classes_teacher_id_teachers",
    )
    assert_rejected(
        db,
        insert(TeacherClass).values(teacher_id=teacher_id, class_id=999999),
        "fk_teacher_classes_class_id_classes",
    )


def test_teacher_subject_pairs(db):
    teacher_id = add_teacher(db)
    subject_id = add(db, Subject, name="Mathematics")
    db.execute(
        insert(TeacherSubject).values(teacher_id=teacher_id, subject_id=subject_id)
    )
    assert_rejected(
        db,
        insert(TeacherSubject).values(teacher_id=teacher_id, subject_id=subject_id),
        "pk_teacher_subjects",
    )
    assert_rejected(
        db,
        insert(TeacherSubject).values(teacher_id=999999, subject_id=subject_id),
        "fk_teacher_subjects_teacher_id_teachers",
    )
    assert_rejected(
        db,
        insert(TeacherSubject).values(teacher_id=teacher_id, subject_id=999999),
        "fk_teacher_subjects_subject_id_subjects",
    )


def test_assigned_rows_cannot_be_deleted(db):
    teacher_id = add_teacher(db)
    class_id = add_class(db)
    subject_id = add(db, Subject, name="Mathematics")
    db.execute(insert(TeacherClass).values(teacher_id=teacher_id, class_id=class_id))
    db.execute(
        insert(TeacherSubject).values(teacher_id=teacher_id, subject_id=subject_id)
    )
    assert_rejected(
        db,
        delete(SchoolClass).where(SchoolClass.id == class_id),
        "fk_teacher_classes_class_id_classes",
    )
    assert_rejected(
        db,
        delete(Subject).where(Subject.id == subject_id),
        "fk_teacher_subjects_subject_id_subjects",
    )


def test_assigned_teacher_cannot_be_deleted(db):
    teacher_id = add_teacher(db)
    class_id = add_class(db)
    db.execute(insert(TeacherClass).values(teacher_id=teacher_id, class_id=class_id))
    assert_rejected(
        db,
        delete(Teacher).where(Teacher.id == teacher_id),
        "fk_teacher_classes_teacher_id_teachers",
    )


def test_teacher_role_is_not_enforced_by_the_database(db):
    # Known gap, by design: a CHECK cannot look at another table, so a teacher may be
    # linked to a non-teacher user here. The service layer must reject it.
    parent_id = add_user(db, email="parent@camdock.test", role="parent")
    add_teacher(db, parent_id)
