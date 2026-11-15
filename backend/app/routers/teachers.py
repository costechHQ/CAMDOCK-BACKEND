from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.dependencies import require_roles
from app.models import User
from app.schemas.teachers import TeacherCreate, TeacherOut, TeacherPage
from app.services import teachers as teacher_service

router = APIRouter(prefix="/teachers", tags=["teachers"])


@router.post("", status_code=201)
def create_teacher(
    body: TeacherCreate,
    admin: Annotated[User, Depends(require_roles("admin"))],
    db: Annotated[Session, Depends(get_db)],
) -> TeacherOut:
    teacher = teacher_service.create_teacher(db, admin, body)
    db.commit()
    return teacher


@router.get("", dependencies=[Depends(require_roles("admin"))])
def list_teachers(
    db: Annotated[Session, Depends(get_db)],
    q: Annotated[str | None, Query(max_length=100)] = None,
    class_id: int | None = None,
    subject_id: int | None = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> TeacherPage:
    return teacher_service.list_teachers(
        db, q=q, class_id=class_id, subject_id=subject_id, limit=limit, offset=offset
    )
