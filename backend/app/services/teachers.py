from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.errors import AppError
from app.models import (
    AuditLog,
    SchoolClass,
    Subject,
    Teacher,
    TeacherClass,
    TeacherSubject,
    User,
)
from app.schemas.teachers import NamedRef, TeacherCreate, TeacherOut

ALREADY_LINKED = "That account already has a teacher record."


def _validation_error(message: str) -> AppError:
    return AppError(message, status_code=422, code="validation_error")


def _find_active(db: Session, model, ids: list[int], label: str):
    """The active rows for `ids`, sorted by name; every id must match, else 422."""
    rows = db.execute(
        select(model).where(model.id.in_(ids), model.is_active.is_(True))
    ).scalars()
    found = {row.id: row for row in rows}
    if len(found) != len(ids):
        raise _validation_error(f"One or more {label} were not found.")
    return sorted(found.values(), key=lambda row: row.name)


def create_teacher(db: Session, actor: User, data: TeacherCreate) -> TeacherOut:
    """Create an active teacher linked to an existing teacher account, with class and
    subject assignments, and audit it. The caller owns the transaction."""
    account = db.execute(
        select(User).where(
            User.id == data.user_id, User.role == "teacher", User.is_active.is_(True)
        )
    ).scalar_one_or_none()
    # One message for a missing, disabled or wrong-role account.
    if account is None:
        raise _validation_error("Teacher account not found.")
    if db.execute(select(Teacher.id).where(Teacher.user_id == account.id)).first():
        raise AppError(ALREADY_LINKED, status_code=409, code="conflict")
    classes = _find_active(db, SchoolClass, data.class_ids, "classes")
    subjects = _find_active(db, Subject, data.subject_ids, "subjects")

    teacher = Teacher(
        user_id=account.id, full_name=data.full_name, phone=data.phone, is_active=True
    )
    try:
        with db.begin_nested():
            db.add(teacher)
            db.flush()
    except IntegrityError:  # lost a race with another link of the same account
        raise AppError(ALREADY_LINKED, status_code=409, code="conflict") from None
    db.add_all(TeacherClass(teacher_id=teacher.id, class_id=c.id) for c in classes)
    db.add_all(TeacherSubject(teacher_id=teacher.id, subject_id=s.id) for s in subjects)
    db.add(
        AuditLog(
            actor_user_id=actor.id,
            action="teacher.create",
            entity_type="teacher",
            entity_id=teacher.id,
            changes={
                "user_id": account.id,
                "full_name": teacher.full_name,
                "class_ids": sorted(c.id for c in classes),
                "subject_ids": sorted(s.id for s in subjects),
            },
        )
    )
    db.flush()
    return TeacherOut(
        id=teacher.id,
        user_id=teacher.user_id,
        full_name=teacher.full_name,
        phone=teacher.phone,
        is_active=teacher.is_active,
        classes=[NamedRef(id=c.id, name=c.name) for c in classes],
        subjects=[NamedRef(id=s.id, name=s.name) for s in subjects],
    )
