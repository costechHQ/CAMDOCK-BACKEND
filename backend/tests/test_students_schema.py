import pytest
from sqlalchemy import delete, insert, select

from app.db.base import Base
from app.models import ParentStudent, SchoolClass, Student, User
from tests.support import assert_rejected


def add(db, model, **values) -> int:
    return db.execute(insert(model).values(**values).returning(model.id)).scalar_one()


def add_class(db, name="JSS 1") -> int:
    return add(db, SchoolClass, name=name, level="junior_secondary")


def add_student(db, class_id, number="CAM/001", **overrides) -> int:
    return add(
        db,
        Student,
        student_number=number,
        full_name="Ngozi Okafor",
        class_id=class_id,
        **overrides,
    )


def add_parent(db, email="parent@camdock.test") -> int:
    return add(db, User, email=email, password_hash="hash", role="parent")


def test_models_registered_on_metadata():
    assert {"students", "parent_student"} <= set(Base.metadata.tables)


def test_valid_student_gets_defaults(db):
    student_id = add_student(db, add_class(db))
    row = db.execute(select(Student).where(Student.id == student_id)).one()
    assert row.status == "active"
    assert row.is_active is True
    assert row.gender is None and row.date_of_birth is None
    assert row.created_at is not None and row.updated_at is not None


def test_student_number_unique(db):
    class_id = add_class(db)
    add_student(db, class_id)
    assert_rejected(
        db,
        insert(Student).values(
            student_number="CAM/001", full_name="Other", class_id=class_id
        ),
        "uq_students_student_number",
    )


@pytest.mark.parametrize("status", ["active", "graduated", "transferred", "withdrawn"])
def test_allowed_statuses_are_accepted(db, status):
    add_student(db, add_class(db), status=status)


def test_invalid_status_rejected(db):
    class_id = add_class(db)
    assert_rejected(
        db,
        insert(Student).values(
            student_number="CAM/002",
            full_name="Other",
            class_id=class_id,
            status="expelled",
        ),
        "ck_students_valid_status",
    )


@pytest.mark.parametrize("gender", ["male", "female", None])
def test_allowed_genders_are_accepted(db, gender):
    add_student(db, add_class(db), gender=gender)


def test_invalid_gender_rejected(db):
    class_id = add_class(db)
    assert_rejected(
        db,
        insert(Student).values(
            student_number="CAM/002",
            full_name="Other",
            class_id=class_id,
            gender="other",
        ),
        "ck_students_valid_gender",
    )


def test_student_requires_existing_class(db):
    assert_rejected(
        db,
        insert(Student).values(
            student_number="CAM/003", full_name="Other", class_id=999999
        ),
        "fk_students_class_id_classes",
    )


def test_class_with_students_cannot_be_deleted(db):
    class_id = add_class(db)
    add_student(db, class_id)
    assert_rejected(
        db,
        delete(SchoolClass).where(SchoolClass.id == class_id),
        "fk_students_class_id_classes",
    )


def test_student_may_have_no_parent_link(db):
    student_id = add_student(db, add_class(db))
    count = db.execute(
        select(ParentStudent).where(ParentStudent.student_id == student_id)
    ).all()
    assert count == []


def test_parent_student_pairs(db):
    class_id = add_class(db)
    student_id = add_student(db, class_id)
    parent_id = add_parent(db)
    db.execute(
        insert(ParentStudent).values(
            parent_user_id=parent_id, student_id=student_id, relationship="mother"
        )
    )
    assert_rejected(
        db,
        insert(ParentStudent).values(parent_user_id=parent_id, student_id=student_id),
        "pk_parent_student",
    )
    assert_rejected(
        db,
        insert(ParentStudent).values(parent_user_id=999999, student_id=student_id),
        "fk_parent_student_parent_user_id_users",
    )
    assert_rejected(
        db,
        insert(ParentStudent).values(parent_user_id=parent_id, student_id=999999),
        "fk_parent_student_student_id_students",
    )


def test_many_to_many_links(db):
    class_id = add_class(db)
    first = add_student(db, class_id, "CAM/001")
    second = add_student(db, class_id, "CAM/002")
    mother = add_parent(db, "mother@camdock.test")
    father = add_parent(db, "father@camdock.test")
    for parent_id, student_id in [
        (mother, first),
        (mother, second),
        (father, first),
    ]:
        db.execute(
            insert(ParentStudent).values(
                parent_user_id=parent_id, student_id=student_id
            )
        )


def test_linked_rows_cannot_be_deleted(db):
    student_id = add_student(db, add_class(db))
    parent_id = add_parent(db)
    db.execute(
        insert(ParentStudent).values(parent_user_id=parent_id, student_id=student_id)
    )
    assert_rejected(
        db,
        delete(Student).where(Student.id == student_id),
        "fk_parent_student_student_id_students",
    )
    assert_rejected(
        db,
        delete(User).where(User.id == parent_id),
        "fk_parent_student_parent_user_id_users",
    )


def test_parent_role_is_not_enforced_by_the_database(db):
    # Known gap, by design: a CHECK cannot look at another table, so a student may be
    # linked to a non-parent user here. The service layer must reject it.
    teacher_id = add(
        db, User, email="t@camdock.test", password_hash="hash", role="teacher"
    )
    student_id = add_student(db, add_class(db))
    db.execute(
        insert(ParentStudent).values(parent_user_id=teacher_id, student_id=student_id)
    )
