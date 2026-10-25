import pytest
from sqlalchemy import insert, select

from app.core.errors import AppError
from app.models import (
    ParentStudent,
    SchoolClass,
    Student,
    Subject,
    Teacher,
    TeacherClass,
    TeacherSubject,
    User,
)
from app.services import access


def add(db, model, **values) -> int:
    return db.execute(insert(model).values(**values).returning(model.id)).scalar_one()


def make_user(db, role, email=None) -> User:
    email = email or f"{role}{db.execute(select(User.id)).all().__len__()}@camdock.test"
    user_id = add(db, User, email=email, password_hash="hash", role=role)
    return User(id=user_id, email=email, role=role, is_active=True)


def make_class(db, name, level="primary") -> int:
    return add(db, SchoolClass, name=name, level=level)


def make_student(db, class_id, number) -> int:
    return add(
        db,
        Student,
        student_number=number,
        full_name=f"Student {number}",
        class_id=class_id,
    )


def make_teacher(db, user, classes=(), subjects=(), is_active=True) -> int:
    teacher_id = add(db, Teacher, user_id=user.id, full_name="T", is_active=is_active)
    for class_id in classes:
        db.execute(
            insert(TeacherClass).values(teacher_id=teacher_id, class_id=class_id)
        )
    for subject_id in subjects:
        db.execute(
            insert(TeacherSubject).values(teacher_id=teacher_id, subject_id=subject_id)
        )
    return teacher_id


def link(db, parent: User, student_id: int) -> None:
    db.execute(
        insert(ParentStudent).values(parent_user_id=parent.id, student_id=student_id)
    )


@pytest.fixture
def school(db):
    """Two classes, three students (two in P1, one in P2) and two subjects."""
    p1, p2 = make_class(db, "Primary 1"), make_class(db, "Primary 2")
    return {
        "p1": p1,
        "p2": p2,
        "a": make_student(db, p1, "S-A"),
        "b": make_student(db, p1, "S-B"),
        "c": make_student(db, p2, "S-C"),
        "maths": add(db, Subject, name="Mathematics"),
        "english": add(db, Subject, name="English Language"),
    }


def visible_students(db, user) -> set[int]:
    return set(
        db.execute(select(Student.id).where(access.student_scope(user))).scalars()
    )


def visible_classes(db, user) -> set[int]:
    return set(
        db.execute(select(SchoolClass.id).where(access.class_scope(user))).scalars()
    )


# --- admin ---------------------------------------------------------------------------


def test_admin_reaches_everything(db, school):
    admin = make_user(db, "admin")

    assert visible_students(db, admin) == {school["a"], school["b"], school["c"]}
    assert visible_classes(db, admin) == {school["p1"], school["p2"]}
    assert access.can_access_student(db, admin, school["c"])
    assert access.can_access_class(db, admin, school["p2"])
    assert access.can_access_class_subject(db, admin, school["p1"], school["maths"])


# --- parent --------------------------------------------------------------------------


def test_parent_reaches_only_linked_children(db, school):
    parent = make_user(db, "parent")
    link(db, parent, school["a"])

    assert visible_students(db, parent) == {school["a"]}
    assert access.can_access_student(db, parent, school["a"])
    # Same class, and a neighbouring id: still not theirs.
    assert not access.can_access_student(db, parent, school["b"])
    assert not access.can_access_student(db, parent, school["c"])
    assert not access.can_access_student(db, parent, school["a"] + 1000)


def test_parent_with_two_children_reaches_both(db, school):
    parent = make_user(db, "parent")
    link(db, parent, school["a"])
    link(db, parent, school["c"])

    assert visible_students(db, parent) == {school["a"], school["c"]}


def test_other_parent_is_unaffected_by_a_link(db, school):
    first, second = (
        make_user(db, "parent", "p1@camdock.test"),
        make_user(db, "parent", "p2@camdock.test"),
    )
    link(db, first, school["a"])

    assert visible_students(db, second) == set()


def test_parent_has_no_class_or_class_subject_access(db, school):
    parent = make_user(db, "parent")
    link(db, parent, school["a"])

    assert visible_classes(db, parent) == set()
    assert not access.can_access_class(db, parent, school["p1"])
    assert not access.can_access_class_subject(
        db, parent, school["p1"], school["maths"]
    )


def test_teacher_role_user_with_a_parent_link_gets_nothing(db, school):
    user = make_user(db, "teacher")
    link(db, user, school["a"])

    assert visible_students(db, user) == set()


# --- teacher -------------------------------------------------------------------------


def test_teacher_reaches_students_of_assigned_classes_only(db, school):
    teacher = make_user(db, "teacher")
    make_teacher(db, teacher, classes=[school["p1"]])

    assert visible_students(db, teacher) == {school["a"], school["b"]}
    assert visible_classes(db, teacher) == {school["p1"]}
    assert access.can_access_student(db, teacher, school["a"])
    assert not access.can_access_student(db, teacher, school["c"])
    assert access.can_access_class(db, teacher, school["p1"])
    assert not access.can_access_class(db, teacher, school["p2"])


def test_teacher_results_access_needs_class_and_subject(db, school):
    teacher = make_user(db, "teacher")
    make_teacher(db, teacher, classes=[school["p1"]], subjects=[school["maths"]])

    can = access.can_access_class_subject
    assert can(db, teacher, school["p1"], school["maths"])
    assert not can(db, teacher, school["p1"], school["english"])  # wrong subject
    assert not can(db, teacher, school["p2"], school["maths"])  # wrong class


def test_teacher_assigned_subject_without_class_gets_no_students(db, school):
    teacher = make_user(db, "teacher")
    make_teacher(db, teacher, subjects=[school["maths"]])

    assert visible_students(db, teacher) == set()
    assert not access.can_access_class_subject(
        db, teacher, school["p1"], school["maths"]
    )


def test_inactive_teacher_record_gets_nothing(db, school):
    teacher = make_user(db, "teacher")
    make_teacher(
        db, teacher, classes=[school["p1"]], subjects=[school["maths"]], is_active=False
    )

    assert visible_students(db, teacher) == set()
    assert visible_classes(db, teacher) == set()
    assert not access.can_access_class_subject(
        db, teacher, school["p1"], school["maths"]
    )


def test_teacher_without_a_teacher_record_gets_nothing(db, school):
    teacher = make_user(db, "teacher")

    assert visible_students(db, teacher) == set()
    assert not access.can_access_class(db, teacher, school["p1"])


def test_teacher_record_on_a_parent_account_gets_nothing(db, school):
    user = make_user(db, "parent")
    make_teacher(db, user, classes=[school["p1"]], subjects=[school["maths"]])

    assert visible_students(db, user) == set()
    assert visible_classes(db, user) == set()
    assert not access.can_access_class_subject(db, user, school["p1"], school["maths"])


def test_teachers_are_isolated_from_each_other(db, school):
    first = make_user(db, "teacher", "t1@camdock.test")
    second = make_user(db, "teacher", "t2@camdock.test")
    make_teacher(db, first, classes=[school["p1"]])
    make_teacher(db, second, classes=[school["p2"]])

    assert visible_students(db, first) == {school["a"], school["b"]}
    assert visible_students(db, second) == {school["c"]}


# --- default deny ---------------------------------------------------------------------


def test_unknown_role_is_denied_everything(db, school):
    stranger = User(id=999999, email="x@camdock.test", role="superuser", is_active=True)

    assert visible_students(db, stranger) == set()
    assert visible_classes(db, stranger) == set()
    assert not access.can_access_student(db, stranger, school["a"])
    assert not access.can_access_class_subject(
        db, stranger, school["p1"], school["maths"]
    )


# --- require_student_access ---------------------------------------------------------


def not_found(db, user, student_id) -> AppError:
    with pytest.raises(AppError) as excinfo:
        access.require_student_access(db, user, student_id)
    return excinfo.value


def test_require_student_access_returns_the_id_in_scope(db, school):
    parent = make_user(db, "parent")
    link(db, parent, school["a"])

    assert access.require_student_access(db, parent, school["a"]) == school["a"]


def test_out_of_scope_and_missing_students_look_identical(db, school):
    parent = make_user(db, "parent")
    link(db, parent, school["a"])

    out_of_scope = not_found(db, parent, school["b"])
    missing = not_found(db, parent, school["a"] + 1000)

    assert (out_of_scope.status_code, out_of_scope.code, out_of_scope.message) == (
        404,
        "not_found",
        "Student not found.",
    )
    assert (missing.status_code, missing.code, missing.message) == (
        out_of_scope.status_code,
        out_of_scope.code,
        out_of_scope.message,
    )


def test_admin_gets_404_only_for_a_missing_student(db, school):
    admin = make_user(db, "admin")

    assert access.require_student_access(db, admin, school["c"]) == school["c"]
    assert not_found(db, admin, school["c"] + 1000).status_code == 404
