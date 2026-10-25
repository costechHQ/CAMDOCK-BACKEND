from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.dependencies import require_roles
from app.models import User
from app.schemas.students import StudentCreate, StudentOut
from app.services import students as student_service

router = APIRouter(prefix="/students", tags=["students"])


@router.post("", status_code=201)
def create_student(
    body: StudentCreate,
    admin: Annotated[User, Depends(require_roles("admin"))],
    db: Annotated[Session, Depends(get_db)],
) -> StudentOut:
    student = student_service.create_student(db, admin, body)
    db.commit()
    return student
