from decimal import Decimal

from sqlalchemy import func, select
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
from app.schemas.results import (
    ResultOut,
    ResultPage,
    ResultRow,
    ResultWrite,
    StudentRef,
    StudentResults,
    SubjectScore,
)
from app.services import access, averages
from app.services import students as student_service
from app.services.grading import grade_for
from app.services.terms import require_term

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


def list_results(
    db: Session,
    user: User,
    *,
    term_id: int,
    class_id: int | None,
    subject_id: int | None,
    limit: int,
    offset: int,
) -> ResultPage:
    """Scores for a term, scoped in SQL, ordered by student name then subject name.
    Results of inactive students are left out."""
    require_term(db, term_id)
    conditions = [
        Result.term_id == term_id,
        Student.is_active.is_(True),
        access.result_scope(user),
    ]
    if class_id is not None:
        conditions.append(Result.class_id == class_id)
    if subject_id is not None:
        conditions.append(Result.subject_id == subject_id)
    joined = (
        select(Result, Student, Subject)
        .join(Student, Student.id == Result.student_id)
        .join(Subject, Subject.id == Result.subject_id)
        .where(*conditions)
    )

    total = db.execute(
        select(func.count()).select_from(joined.order_by(None).subquery())
    ).scalar_one()
    rows = db.execute(
        joined.order_by(Student.full_name, Subject.name, Result.id)
        .limit(limit)
        .offset(offset)
    ).all()
    return ResultPage(
        items=[
            ResultRow(
                id=result.id,
                student_id=student.id,
                student_number=student.student_number,
                student_name=student.full_name,
                subject_id=subject.id,
                subject_name=subject.name,
                term_id=result.term_id,
                class_id=result.class_id,
                score=result.score,
                grade=grade_for(result.score),
            )
            for result, student, subject in rows
        ],
        total=total,
        limit=limit,
        offset=offset,
    )


def student_results(
    db: Session, user: User, student_id: int, term_id: int
) -> StudentResults:
    """A student's recorded scores for a term. Student scope comes first (the same 404
    for missing and out-of-scope students); a teacher then sees only their subjects.
    Subjects with no score are simply absent, never filled in as zero. The average
    covers every recorded subject for every viewer, so it is one consistent number."""
    student = student_service.get_student(db, user, student_id)
    term = require_term(db, term_id)
    average, recorded = averages.student_average(db, student.id, term.id)
    rows = db.execute(
        select(Result, Subject)
        .join(Subject, Subject.id == Result.subject_id)
        .where(
            Result.student_id == student.id,
            Result.term_id == term.id,
            access.result_scope(user),
        )
        .order_by(Subject.name, Result.id)
    ).all()
    return StudentResults(
        student=StudentRef(
            id=student.id,
            student_number=student.student_number,
            full_name=student.full_name,
            class_id=student.class_id,
            class_name=student.class_name,
        ),
        term=term,
        average=average,
        subjects_recorded=recorded,
        items=[
            SubjectScore(
                result_id=result.id,
                subject_id=subject.id,
                subject_name=subject.name,
                score=result.score,
                grade=grade_for(result.score),
            )
            for result, subject in rows
        ],
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
