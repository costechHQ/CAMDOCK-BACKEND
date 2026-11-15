"""Student and class averages (SRS FR-RES-006 to 008).

The database only sums and counts, which is exact; the division and the single
half-up rounding happen here, through `grading`, so every figure uses one rule.
Missing scores are absent from the sums, never counted as zero.
"""

from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.errors import AppError
from app.models import Result, SchoolClass, Student, Subject, User
from app.schemas.results import ClassAverages, ClassRef, SubjectAverage
from app.services import access
from app.services.grading import mean, mean_from_sum, round_average
from app.services.terms import require_term


def student_average(
    db: Session, student_id: int, term_id: int
) -> tuple[Decimal | None, int]:
    """The student's average over every recorded subject for the term, and how many
    subjects it covers. The average is None when nothing is recorded."""
    total, count = db.execute(
        select(func.sum(Result.score), func.count()).where(
            Result.student_id == student_id, Result.term_id == term_id
        )
    ).one()
    average = mean_from_sum(total or Decimal(0), count)
    return (None if average is None else round_average(average)), count


def class_averages(
    db: Session, user: User, class_id: int, term_id: int
) -> ClassAverages:
    """Per-subject and overall averages for a class and term.

    The overall figure is the mean of the unrounded subject means, as the SRS specifies
    (FR-RES-008), not a mean weighted by how many students were scored. Only active
    students count, and "the class" is the class recorded on each result.
    """
    school_class = db.execute(
        select(SchoolClass.id, SchoolClass.name).where(
            SchoolClass.id == class_id,
            SchoolClass.is_active.is_(True),
            access.class_scope(user),
        )
    ).one_or_none()
    if school_class is None:
        raise AppError("Class not found.", status_code=404, code="not_found")
    term = require_term(db, term_id)

    in_class = (
        Result.class_id == class_id,
        Result.term_id == term_id,
        Student.is_active.is_(True),
    )
    subject_rows = db.execute(
        select(Subject.id, Subject.name, func.sum(Result.score), func.count())
        .join(Result, Result.subject_id == Subject.id)
        .join(Student, Student.id == Result.student_id)
        .where(*in_class)
        .group_by(Subject.id, Subject.name)
        .order_by(Subject.name, Subject.id)
    ).all()
    students = db.execute(
        select(func.count(func.distinct(Result.student_id)))
        .join(Student, Student.id == Result.student_id)
        .where(*in_class)
    ).scalar_one()

    means = [(row, mean_from_sum(row[2], row[3])) for row in subject_rows]
    overall = mean(value for _, value in means)
    return ClassAverages(
        school_class=ClassRef(id=school_class.id, name=school_class.name),
        term=term,
        students_with_results=students,
        subjects=[
            SubjectAverage(
                subject_id=row[0],
                subject_name=row[1],
                average=round_average(value),
                students_scored=row[3],
            )
            for row, value in means
        ],
        overall_average=None if overall is None else round_average(overall),
    )
