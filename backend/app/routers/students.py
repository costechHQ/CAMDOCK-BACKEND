from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.dependencies import get_current_user, require_roles
from app.models import User
from app.schemas.students import StudentCreate, StudentOut, StudentPage
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


@router.get("")
def list_students(
    user: Annotated[User, Depends(require_roles("admin", "teacher"))],
    db: Annotated[Session, Depends(get_db)],
    q: Annotated[str | None, Query(max_length=100)] = None,
    class_id: int | None = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> StudentPage:
    """Admins see every student; teachers only those in their assigned classes."""
    return student_service.list_students(
        db, user, q=q, class_id=class_id, limit=limit, offset=offset
    )


@router.get("/{student_id}")
def get_student(
    student_id: int,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> StudentOut:
    return student_service.get_student(db, user, student_id)
