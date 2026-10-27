from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import AppError
from app.models import AcademicYear, Term
from app.schemas.results import TermRef


def require_term(db: Session, term_id: int) -> TermRef:
    """The term with its academic year name, or a 422 when it does not exist."""
    row = db.execute(
        select(Term.id, Term.name, AcademicYear.name)
        .join(AcademicYear, AcademicYear.id == Term.academic_year_id)
        .where(Term.id == term_id)
    ).one_or_none()
    if row is None:
        raise AppError("Term not found.", status_code=422, code="validation_error")
    return TermRef(id=row[0], name=row[1], academic_year=row[2])
