"""Record-level access: which students and classes a user may reach.

Admins reach everything. Parents reach only students linked to them in
`parent_student`. Teachers reach only classes (and, for results, subjects) assigned to
their active `Teacher` record. Anything else is denied, including unknown roles and a
role that does not match the data (a teacher-role user holding a parent link, etc.).

`student_scope` / `class_scope` return SQL filters so list queries are scoped by
construction; the `can_*` checks reuse them, so there is one definition of "allowed".
"""

from sqlalchemy import ColumnElement, Connection, exists, false, select, true
from sqlalchemy.orm import Session

from app.core.errors import AppError
from app.models import (
    ParentStudent,
    SchoolClass,
    Student,
    Teacher,
    TeacherClass,
    TeacherSubject,
    User,
)


def _teacher_id(user: User):
    """Scalar subquery: the user's active teacher record, if any."""
    return (
        select(Teacher.id)
        .where(Teacher.user_id == user.id, Teacher.is_active.is_(True))
        .scalar_subquery()
    )


def student_scope(user: User) -> ColumnElement[bool]:
    """Filter on `Student` limiting rows to those the user may access."""
    if user.role == "admin":
        return true()
    if user.role == "parent":
        linked = select(ParentStudent.student_id).where(
            ParentStudent.parent_user_id == user.id
        )
        return Student.id.in_(linked)
    if user.role == "teacher":
        assigned = select(TeacherClass.class_id).where(
            TeacherClass.teacher_id == _teacher_id(user)
        )
        return Student.class_id.in_(assigned)
    return false()


def class_scope(user: User) -> ColumnElement[bool]:
    """Filter on `SchoolClass` limiting rows to those the user may access.

    Parents get no class-level access yet; see the Phase 5 open question on
    class averages.
    """
    if user.role == "admin":
        return true()
    if user.role == "teacher":
        assigned = select(TeacherClass.class_id).where(
            TeacherClass.teacher_id == _teacher_id(user)
        )
        return SchoolClass.id.in_(assigned)
    return false()


def can_access_student(db: Session | Connection, user: User, student_id: int) -> bool:
    return db.execute(
        select(exists().where(Student.id == student_id, student_scope(user)))
    ).scalar_one()


def can_access_class(db: Session | Connection, user: User, class_id: int) -> bool:
    return db.execute(
        select(exists().where(SchoolClass.id == class_id, class_scope(user)))
    ).scalar_one()


def can_access_class_subject(
    db: Session | Connection, user: User, class_id: int, subject_id: int
) -> bool:
    """Results access: a teacher needs both the class and the subject assigned."""
    if user.role == "admin":
        return True
    if user.role != "teacher":
        return False
    teacher_id = _teacher_id(user)
    return db.execute(
        select(
            exists().where(
                TeacherClass.teacher_id == teacher_id,
                TeacherClass.class_id == class_id,
            )
            & exists().where(
                TeacherSubject.teacher_id == teacher_id,
                TeacherSubject.subject_id == subject_id,
            )
        )
    ).scalar_one()


def require_student_access(
    db: Session | Connection, user: User, student_id: int
) -> int:
    """Return the student id, or raise the same 404 whether the student does not exist
    or is out of scope, so callers cannot probe which ids exist."""
    found = db.execute(
        select(Student.id).where(Student.id == student_id, student_scope(user))
    ).scalar_one_or_none()
    if found is None:
        raise AppError("Student not found.", status_code=404, code="not_found")
    return found
