from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.dependencies import require_roles
from app.models import User
from app.schemas.teachers import TeacherCreate, TeacherOut
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
