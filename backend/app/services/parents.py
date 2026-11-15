from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.errors import AppError
from app.models import AuditLog, ParentStudent, Student, User
from app.schemas.parents import ParentLinkCreate, ParentLinkOut

ALREADY_LINKED = "That parent is already linked to this student."


def _require_student(db: Session, student_id: int) -> int:
    """An active student's id; missing and inactive students give the same 404."""
    found = db.execute(
        select(Student.id).where(Student.id == student_id, Student.is_active.is_(True))
    ).scalar_one_or_none()
    if found is None:
        raise AppError("Student not found.", status_code=404, code="not_found")
    return found


def _is_linked(db: Session, student_id: int, parent_user_id: int) -> bool:
    return (
        db.execute(
            select(ParentStudent.student_id).where(
                ParentStudent.student_id == student_id,
                ParentStudent.parent_user_id == parent_user_id,
            )
        ).first()
        is not None
    )


def _audit(
    db: Session,
    actor: User,
    action: str,
    student_id: int,
    parent_user_id: int,
    relationship: str | None,
) -> None:
    db.add(
        AuditLog(
            actor_user_id=actor.id,
            action=action,
            entity_type="student",
            entity_id=student_id,
            changes={"parent_user_id": parent_user_id, "relationship": relationship},
        )
    )
    db.flush()


def link_parent(
    db: Session, actor: User, student_id: int, data: ParentLinkCreate
) -> ParentLinkOut:
    """Give an existing, active parent-role account access to a student, and audit it.
    The caller owns the transaction."""
    _require_student(db, student_id)
    parent = db.execute(
        select(User).where(
            User.id == data.parent_user_id,
            User.role == "parent",
            User.is_active.is_(True),
        )
    ).scalar_one_or_none()
    # One message for a missing, disabled or wrong-role account.
    if parent is None:
        raise AppError(
            "Parent account not found.", status_code=422, code="validation_error"
        )
    if _is_linked(db, student_id, parent.id):
        raise AppError(ALREADY_LINKED, status_code=409, code="conflict")
    try:
        with db.begin_nested():
            db.add(
                ParentStudent(
                    parent_user_id=parent.id,
                    student_id=student_id,
                    relationship=data.relationship,
                )
            )
            db.flush()
    except IntegrityError:  # lost a race with another link of the same pair
        raise AppError(ALREADY_LINKED, status_code=409, code="conflict") from None
    _audit(db, actor, "parent_link.create", student_id, parent.id, data.relationship)
    return ParentLinkOut(
        student_id=student_id,
        parent_user_id=parent.id,
        parent_email=parent.email,
        relationship=data.relationship,
    )


def list_parents(db: Session, student_id: int) -> list[ParentLinkOut]:
    """Everyone with access to the student, ordered by email."""
    _require_student(db, student_id)
    rows = db.execute(
        select(ParentStudent.parent_user_id, User.email, ParentStudent.relationship)
        .join(User, User.id == ParentStudent.parent_user_id)
        .where(ParentStudent.student_id == student_id)
        .order_by(User.email, User.id)
    )
    return [
        ParentLinkOut(
            student_id=student_id,
            parent_user_id=parent_id,
            parent_email=email,
            relationship=relationship,
        )
        for parent_id, email, relationship in rows
    ]


def unlink_parent(
    db: Session, actor: User, student_id: int, parent_user_id: int
) -> None:
    """Remove a parent's access to a student, and audit it. This removes access only;
    no academic or financial record is touched. The caller owns the transaction."""
    _require_student(db, student_id)
    link = db.execute(
        select(ParentStudent).where(
            ParentStudent.student_id == student_id,
            ParentStudent.parent_user_id == parent_user_id,
        )
    ).scalar_one_or_none()
    if link is None:
        raise AppError("Parent link not found.", status_code=404, code="not_found")
    relationship = link.relationship
    db.execute(
        delete(ParentStudent).where(
            ParentStudent.student_id == student_id,
            ParentStudent.parent_user_id == parent_user_id,
        )
    )
    _audit(db, actor, "parent_link.delete", student_id, parent_user_id, relationship)
