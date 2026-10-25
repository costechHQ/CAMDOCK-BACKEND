from collections import defaultdict

from sqlalchemy import exists, func, select
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
from app.schemas.teachers import NamedRef, TeacherCreate, TeacherOut, TeacherPage
from app.services.search import name_contains

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


def _assignments(db: Session, teacher_ids: list[int], link, target_id_column, target):
    """Active assignment targets per teacher id, sorted by name, in one query."""
    rows = db.execute(
        select(link.teacher_id, target.id, target.name)
        .join(target, target.id == target_id_column)
        .where(link.teacher_id.in_(teacher_ids), target.is_active.is_(True))
        .order_by(target.name, target.id)
    )
    grouped = defaultdict(list)
    for teacher_id, target_id, name in rows:
        grouped[teacher_id].append(NamedRef(id=target_id, name=name))
    return grouped


def list_teachers(
    db: Session,
    *,
    q: str | None,
    class_id: int | None,
    subject_id: int | None,
    limit: int,
    offset: int,
) -> TeacherPage:
    """Active teachers ordered by name then id. Assignment filters use EXISTS, so a
    teacher matching through several rows is still counted and listed once."""
    conditions = [Teacher.is_active.is_(True)]
    if q and q.strip():
        conditions.append(name_contains(Teacher.full_name, q.strip()))
    if class_id is not None:
        conditions.append(
            exists().where(
                TeacherClass.teacher_id == Teacher.id, TeacherClass.class_id == class_id
            )
        )
    if subject_id is not None:
        conditions.append(
            exists().where(
                TeacherSubject.teacher_id == Teacher.id,
                TeacherSubject.subject_id == subject_id,
            )
        )

    total = db.execute(
        select(func.count()).select_from(Teacher).where(*conditions)
    ).scalar_one()
    teachers = (
        db.execute(
            select(Teacher)
            .where(*conditions)
            .order_by(Teacher.full_name, Teacher.id)
            .limit(limit)
            .offset(offset)
        )
        .scalars()
        .all()
    )
    ids = [teacher.id for teacher in teachers]
    classes = _assignments(db, ids, TeacherClass, TeacherClass.class_id, SchoolClass)
    subjects = _assignments(db, ids, TeacherSubject, TeacherSubject.subject_id, Subject)
    return TeacherPage(
        items=[
            TeacherOut(
                id=teacher.id,
                user_id=teacher.user_id,
                full_name=teacher.full_name,
                phone=teacher.phone,
                is_active=teacher.is_active,
                classes=classes[teacher.id],
                subjects=subjects[teacher.id],
            )
            for teacher in teachers
        ],
        total=total,
        limit=limit,
        offset=offset,
    )
