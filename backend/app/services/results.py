from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.errors import AppError
from app.models import (
    AuditLog,
    ClassSubject,
    Result,
    Student,
    Subject,
    Term,
    User,
)
from app.schemas.results import ResultOut, ResultWrite
from app.services import access
from app.services.grading import grade_for

TWO_PLACES = Decimal("0.01")


def _validation_error(message: str) -> AppError:
    return AppError(message, status_code=422, code="validation_error")


def _to_out(result: Result) -> ResultOut:
    return ResultOut(
        id=result.id,
        student_id=result.student_id,
        subject_id=result.subject_id,
        term_id=result.term_id,
        class_id=result.class_id,
        score=result.score,
        grade=grade_for(result.score),
    )


def _find(db: Session, data: ResultWrite) -> Result | None:
    return db.execute(
        select(Result)
        .where(
            Result.student_id == data.student_id,
            Result.subject_id == data.subject_id,
            Result.term_id == data.term_id,
        )
        .with_for_update()
    ).scalar_one_or_none()


def _audit(
    db: Session, actor: User, action: str, result: Result, changes: dict
) -> None:
    db.add(
        AuditLog(
            actor_user_id=actor.id,
            action=action,
            entity_type="result",
            entity_id=result.id,
            changes=changes,
        )
    )


def put_result(db: Session, actor: User, data: ResultWrite) -> tuple[ResultOut, bool]:
    """Create or change one score; returns the result and whether it was created.

    Checks run in an order that reveals nothing about records the caller cannot reach:
    student scope (404), subject access (403), then existence and class rules (422).
    The caller owns the transaction.
    """
    student = db.execute(
        select(Student).where(
            Student.id == data.student_id,
            Student.is_active.is_(True),
            access.student_scope(actor),
        )
    ).scalar_one_or_none()
    if student is None:
        raise AppError("Student not found.", status_code=404, code="not_found")
    if not access.can_access_class_subject(
        db, actor, student.class_id, data.subject_id
    ):
        raise AppError(
            "You do not have access to this subject for this class.",
            status_code=403,
            code="forbidden",
        )
    subject_active = db.execute(
        select(Subject.is_active).where(Subject.id == data.subject_id)
    ).scalar_one_or_none()
    if not subject_active:
        raise _validation_error("Subject not found.")
    if db.get(Term, data.term_id) is None:
        raise _validation_error("Term not found.")
    taught = db.execute(
        select(ClassSubject.class_id).where(
            ClassSubject.class_id == student.class_id,
            ClassSubject.subject_id == data.subject_id,
        )
    ).first()
    if taught is None:
        raise _validation_error("That subject is not taught in the student's class.")

    score = data.score.quantize(TWO_PLACES)
    existing = _find(db, data)
    if existing is None:
        result = Result(
            student_id=student.id,
            subject_id=data.subject_id,
            term_id=data.term_id,
            class_id=student.class_id,
            score=score,
            created_by=actor.id,
        )
        try:
            with db.begin_nested():
                db.add(result)
                db.flush()
        except IntegrityError:  # lost a race with another entry of the same score
            existing = _find(db, data)
            if existing is None:
                raise
        else:
            _audit(
                db,
                actor,
                "result.create",
                result,
                {
                    "student_id": result.student_id,
                    "subject_id": result.subject_id,
                    "term_id": result.term_id,
                    "class_id": result.class_id,
                    "score": str(score),
                },
            )
            db.flush()
            return _to_out(result), True

    if existing.score != score:
        previous = existing.score
        existing.score = score
        existing.updated_by = actor.id
        db.flush()
        _audit(
            db,
            actor,
            "result.update",
            existing,
            {"score": {"from": str(previous), "to": str(score)}},
        )
        db.flush()
    return _to_out(existing), False
