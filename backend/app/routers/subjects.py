from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.dependencies import require_roles
from app.models import Subject
from app.schemas.academic import SubjectOut

router = APIRouter(prefix="/subjects", tags=["subjects"])


@router.get("", dependencies=[Depends(require_roles("admin", "teacher"))])
def list_subjects(db: Annotated[Session, Depends(get_db)]) -> list[SubjectOut]:
    rows = db.scalars(
        select(Subject).where(Subject.is_active.is_(True)).order_by(Subject.name)
    )
    return [SubjectOut.model_validate(row) for row in rows]
