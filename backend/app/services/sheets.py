"""Printable result-sheet data (SRS FR-RES-009)."""

from collections import Counter

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models import Result, SchoolClass, Student, Subject, User
from app.schemas.results import (
    ClassRef,
    ResultSheet,
    SchoolInfo,
    SheetStudent,
    SheetSubject,
)
from app.services import access, averages
from app.services import students as student_service
from app.services.grading import grade_for, mean_from_sum, round_average
from app.services.terms import require_term


def _header_class(db: Session, rows, student) -> ClassRef:
    """The class the scores were recorded in, so a sheet stays correct after a
    promotion. If a mid-term move split them, the class holding the most scores wins
    (ties go to the most recently entered); with no scores, the current class."""
    if not rows:
        return ClassRef(id=student.class_id, name=student.class_name)
    counts = Counter(result.class_id for result, _ in rows)
    latest = {}
    for result, _ in rows:
        latest[result.class_id] = max(latest.get(result.class_id, 0), result.id)
    class_id = max(counts, key=lambda cid: (counts[cid], latest[cid]))
    name = db.execute(
        select(SchoolClass.name).where(SchoolClass.id == class_id)
    ).scalar_one()
    return ClassRef(id=class_id, name=name)


def _class_means(db: Session, term_id: int, rows) -> dict:
    """Unrounded subject means per (subject, recorded class): the F4 definition, in one
    grouped query, over active students only."""
    if not rows:
        return {}
    found = db.execute(
        select(Result.subject_id, Result.class_id, func.sum(Result.score), func.count())
        .join(Student, Student.id == Result.student_id)
        .where(
            Result.term_id == term_id,
            Student.is_active.is_(True),
            Result.subject_id.in_({result.subject_id for result, _ in rows}),
            Result.class_id.in_({result.class_id for result, _ in rows}),
        )
        .group_by(Result.subject_id, Result.class_id)
    )
    return {
        (subject_id, class_id): mean_from_sum(total, count)
        for subject_id, class_id, total, count in found
    }


def result_sheet(db: Session, user: User, student_id: int, term_id: int) -> ResultSheet:
    """The term sheet for one student. Student scope first (the same 404 for missing and
    out-of-scope students), then the term (422). Subjects with no score are absent."""
    student = student_service.get_student(db, user, student_id)
    term = require_term(db, term_id)
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
    class_means = _class_means(db, term.id, rows)
    average, recorded = averages.student_average(db, student.id, term.id)
    settings = get_settings()
    return ResultSheet(
        school=SchoolInfo(name=settings.school_name, location=settings.school_location),
        student=SheetStudent(
            id=student.id,
            student_number=student.student_number,
            full_name=student.full_name,
        ),
        school_class=_header_class(db, rows, student),
        term=term,
        subjects=[
            SheetSubject(
                subject_id=subject.id,
                subject_name=subject.name,
                score=result.score,
                grade=grade_for(result.score),
                class_average=round_average(
                    class_means[(result.subject_id, result.class_id)]
                ),
            )
            for result, subject in rows
        ],
        subjects_recorded=recorded,
        overall_average=average,
    )
