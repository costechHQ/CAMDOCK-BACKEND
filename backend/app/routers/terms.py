from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy import case, select
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.dependencies import require_roles
from app.models import AcademicYear, Term
from app.models.academic import TERM_NAMES
from app.schemas.academic import TermList, TermOut

router = APIRouter(prefix="/terms", tags=["terms"])

TERM_ORDER = case(
    {name: position for position, name in enumerate(TERM_NAMES)}, value=Term.name
)


@router.get("", dependencies=[Depends(require_roles("admin", "teacher", "parent"))])
def list_terms(
    db: Annotated[Session, Depends(get_db)],
    academic_year_id: int | None = None,
) -> TermList:
    """Terms, newest academic year first and First to Third Term within a year."""
    query = (
        select(Term, AcademicYear)
        .join(AcademicYear, AcademicYear.id == Term.academic_year_id)
        .order_by(AcademicYear.start_date.desc(), AcademicYear.id.desc(), TERM_ORDER)
    )
    if academic_year_id is not None:
        query = query.where(Term.academic_year_id == academic_year_id)
    return TermList(
        items=[
            TermOut(
                id=term.id,
                name=term.name,
                academic_year_id=year.id,
                academic_year=year.name,
                is_current_year=year.is_current,
                start_date=term.start_date,
                end_date=term.end_date,
            )
            for term, year in db.execute(query)
        ]
    )
