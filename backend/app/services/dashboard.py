from datetime import date
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import (
    AcademicYear,
    FeeRecord,
    Result,
    SchoolClass,
    Student,
    Teacher,
    Term,
    User,
)
from app.models.academic import TERM_NAMES
from app.schemas.dashboard import FeeAmounts, PublicStats
from app.schemas.results import TermRef
from app.services import access, money
from app.services.fees import paid_per_fee_subquery
from app.services.grading import mean, mean_from_sum, round_average
from app.services.terms import require_term


def _active(model):
    return select(func.count()).select_from(model).where(model.is_active.is_(True))


def public_stats(db: Session) -> PublicStats:
    """Active students, teaching staff and classes, in one round trip."""
    row = db.execute(
        select(
            _active(Student).scalar_subquery().label("students"),
            _active(Teacher).scalar_subquery().label("teachers"),
            _active(SchoolClass).scalar_subquery().label("classes"),
        )
    ).one()
    return PublicStats(
        students=row.students, teachers=row.teachers, classes=row.classes
    )


def resolve_term(
    db: Session, term_id: int | None, today: date | None = None
) -> TermRef | None:
    """The requested term (422 when unknown), or a default from the current academic
    year: the term containing today, else the latest term that has started, else the
    first term by name. None when there is no current year or it has no terms."""
    if term_id is not None:
        return require_term(db, term_id)
    today = today or date.today()
    terms = db.execute(
        select(Term, AcademicYear.name)
        .join(AcademicYear, AcademicYear.id == Term.academic_year_id)
        .where(AcademicYear.is_current.is_(True))
    ).all()
    if not terms:
        return None
    terms.sort(key=lambda row: TERM_NAMES.index(row[0].name))

    def started(term: Term) -> bool:
        return term.start_date is not None and term.start_date <= today

    containing = [
        row
        for row in terms
        if started(row[0]) and (row[0].end_date is None or today <= row[0].end_date)
    ]
    began = [row for row in terms if started(row[0])]
    if containing:
        term, year = containing[0]
    elif began:
        term, year = began[-1]
    else:
        term, year = terms[0]
    return TermRef(id=term.id, name=term.name, academic_year=year)


def fee_totals(db: Session, *conditions) -> FeeAmounts:
    """Billed, collected and outstanding over the bills matching `conditions`
    (expressions on `FeeRecord`/`Student`). Bills of deactivated students count:
    the money is still owed."""
    paid_per_fee, paid = paid_per_fee_subquery()
    billed_sum, paid_sum = db.execute(
        select(
            func.coalesce(func.sum(FeeRecord.amount_billed), 0),
            func.coalesce(func.sum(paid), 0),
        )
        .select_from(FeeRecord)
        .join(Student, Student.id == FeeRecord.student_id)
        .outerjoin(paid_per_fee, paid_per_fee.c.fee_id == FeeRecord.id)
        .where(*conditions)
    ).one()
    billed_sum, paid_sum = money.quantize(billed_sum), money.quantize(paid_sum)
    return FeeAmounts(
        billed=billed_sum,
        collected=paid_sum,
        outstanding=money.quantize(billed_sum - paid_sum),
    )


def class_overall_averages(
    db: Session, user: User, term_id: int
) -> dict[int, Decimal | None]:
    """Overall average per active class the user may see, for the term; None where
    nothing is recorded. Same rule as `averages.class_averages`: the mean of the
    unrounded subject means, over active students, rounded once."""
    class_ids = db.execute(
        select(SchoolClass.id).where(
            SchoolClass.is_active.is_(True), access.class_scope(user)
        )
    ).scalars()
    subject_means: dict[int, list[Decimal]] = {class_id: [] for class_id in class_ids}
    rows = db.execute(
        select(Result.class_id, func.sum(Result.score), func.count())
        .join(Student, Student.id == Result.student_id)
        .where(
            Result.term_id == term_id,
            Student.is_active.is_(True),
            Result.class_id.in_(subject_means),
        )
        .group_by(Result.class_id, Result.subject_id)
    ).all()
    for class_id, total, count in rows:
        subject_means[class_id].append(mean_from_sum(total, count))
    return {
        class_id: None if (overall := mean(values)) is None else round_average(overall)
        for class_id, values in subject_means.items()
    }
