from collections.abc import Iterable
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
from app.schemas.dashboard import (
    AdminOverview,
    ChildLinks,
    ChildRef,
    ClassAverageEntry,
    FeeAmounts,
    FeesByPeriod,
    ParentChild,
    ParentOverview,
    PublicStats,
    TeacherClassEntry,
    TeacherOverview,
)
from app.schemas.results import ClassRef, TermRef
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
    return _overall_averages(db, term_id, class_ids)


def _overall_averages(
    db: Session, term_id: int, class_ids: Iterable[int]
) -> dict[int, Decimal | None]:
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


def admin_overview(db: Session, user: User, term_id: int | None) -> AdminOverview:
    """The admin dashboard (SRS FR-DASH-002): school-wide counts, fees for the term
    and for all time, and each active class's overall average for the term."""
    term = resolve_term(db, term_id)
    counts = public_stats(db)
    occupied = db.execute(
        select(func.count(func.distinct(SchoolClass.id)))
        .join(Student, Student.class_id == SchoolClass.id)
        .where(SchoolClass.is_active.is_(True), Student.is_active.is_(True))
    ).scalar_one()
    classes = db.execute(
        select(SchoolClass.id, SchoolClass.name)
        .where(SchoolClass.is_active.is_(True), access.class_scope(user))
        .order_by(SchoolClass.name, SchoolClass.id)
    ).all()
    averages = class_overall_averages(db, user, term.id) if term is not None else {}
    return AdminOverview(
        term=term,
        students=counts.students,
        teachers=counts.teachers,
        active_classes=occupied,
        fees=FeesByPeriod(
            term=None if term is None else fee_totals(db, FeeRecord.term_id == term.id),
            all_time=fee_totals(db),
        ),
        class_averages=[
            ClassAverageEntry(
                school_class=ClassRef(id=class_id, name=name),
                overall_average=averages.get(class_id),
            )
            for class_id, name in classes
        ],
    )


def teacher_overview(db: Session, user: User, term_id: int | None) -> TeacherOverview:
    """ "My overview" (SRS FR-DASH-002): the teacher's assigned active classes with
    their active student counts and overall averages for the term. Nothing school-wide
    and no fees."""
    term = resolve_term(db, term_id)
    classes = db.execute(
        select(SchoolClass.id, SchoolClass.name, func.count(Student.id))
        .outerjoin(
            Student,
            (Student.class_id == SchoolClass.id) & Student.is_active.is_(True),
        )
        .where(SchoolClass.is_active.is_(True), access.class_scope(user))
        .group_by(SchoolClass.id, SchoolClass.name)
        .order_by(SchoolClass.name, SchoolClass.id)
    ).all()
    averages = class_overall_averages(db, user, term.id) if term is not None else {}
    return TeacherOverview(
        term=term,
        students=sum(row[2] for row in classes),
        classes=[
            TeacherClassEntry(
                school_class=ClassRef(id=class_id, name=name),
                students=students,
                overall_average=averages.get(class_id),
            )
            for class_id, name, students in classes
        ],
    )


def overview(
    db: Session, user: User, term_id: int | None
) -> AdminOverview | TeacherOverview | ParentOverview:
    """The overview for the caller's role."""
    if user.role == "teacher":
        return teacher_overview(db, user, term_id)
    if user.role == "parent":
        return parent_overview(db, user, term_id)
    return admin_overview(db, user, term_id)


def _student_averages(
    db: Session, student_ids: list[int], term_id: int
) -> dict[int, tuple[Decimal | None, int]]:
    """Each student's average and recorded-subject count for the term, in one query;
    the same rule as `averages.student_average`."""
    rows = db.execute(
        select(Result.student_id, func.sum(Result.score), func.count())
        .where(Result.student_id.in_(student_ids), Result.term_id == term_id)
        .group_by(Result.student_id)
    ).all()
    found = {}
    for student_id, total, count in rows:
        average = mean_from_sum(total, count)
        found[student_id] = (None if average is None else round_average(average), count)
    return found


def fee_totals_by_student(
    db: Session, student_ids: list[int], term_id: int | None = None
) -> dict[int, FeeAmounts]:
    """`fee_totals` for several students at once, in one query. Students with no bills
    are absent from the result."""
    paid_per_fee, paid = paid_per_fee_subquery()
    conditions = [FeeRecord.student_id.in_(student_ids)]
    if term_id is not None:
        conditions.append(FeeRecord.term_id == term_id)
    rows = db.execute(
        select(
            FeeRecord.student_id,
            func.sum(FeeRecord.amount_billed),
            func.coalesce(func.sum(paid), 0),
        )
        .outerjoin(paid_per_fee, paid_per_fee.c.fee_id == FeeRecord.id)
        .where(*conditions)
        .group_by(FeeRecord.student_id)
    ).all()
    return {
        student_id: FeeAmounts(
            billed=money.quantize(billed),
            collected=money.quantize(collected),
            outstanding=money.quantize(billed - collected),
        )
        for student_id, billed, collected in rows
    }


_NO_FEES = FeeAmounts(
    billed=money.quantize(Decimal(0)),
    collected=money.quantize(Decimal(0)),
    outstanding=money.quantize(Decimal(0)),
)


def parent_overview(db: Session, user: User, term_id: int | None) -> ParentOverview:
    """The parent dashboard (SRS FR-DASH-003): each linked active child with their
    average, class average, recorded subjects and fee balance, in a fixed number of
    queries however many children there are."""
    term = resolve_term(db, term_id)
    rows = db.execute(
        select(
            Student.id,
            Student.student_number,
            Student.full_name,
            SchoolClass.id,
            SchoolClass.name,
            SchoolClass.is_active,
        )
        .join(SchoolClass, SchoolClass.id == Student.class_id)
        .where(Student.is_active.is_(True), access.student_scope(user))
        .order_by(Student.full_name, Student.id)
    ).all()
    if not rows:
        return ParentOverview(term=term, children=[])

    student_ids = [row[0] for row in rows]
    own = {}
    class_averages = {}
    term_fees = {}
    if term is not None:
        own = _student_averages(db, student_ids, term.id)
        class_averages = _overall_averages(
            db, term.id, {row[3] for row in rows if row[5]}
        )
        term_fees = fee_totals_by_student(db, student_ids, term.id)
    all_fees = fee_totals_by_student(db, student_ids)

    def links(student_id: int) -> ChildLinks:
        query = "" if term is None else f"?term_id={term.id}"
        return ChildLinks(
            results=f"/api/v1/students/{student_id}/results{query}",
            fees=f"/api/v1/fees?student_id={student_id}",
        )

    children = []
    for student_id, number, name, class_id, class_name, class_active in rows:
        average, recorded = own.get(student_id, (None, 0))
        children.append(
            ParentChild(
                student=ChildRef(id=student_id, student_number=number, full_name=name),
                school_class=ClassRef(id=class_id, name=class_name),
                student_average=average,
                class_average=class_averages.get(class_id) if class_active else None,
                subjects_recorded=recorded,
                fees=FeesByPeriod(
                    term=None if term is None else term_fees.get(student_id, _NO_FEES),
                    all_time=all_fees.get(student_id, _NO_FEES),
                ),
                links=links(student_id),
            )
        )
    return ParentOverview(term=term, children=children)
