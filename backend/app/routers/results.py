from typing import Annotated

from fastapi import APIRouter, Depends, Response
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.dependencies import require_roles
from app.models import User
from app.schemas.results import ResultOut, ResultWrite
from app.services import results as result_service

router = APIRouter(prefix="/results", tags=["results"])


@router.put("")
def put_result(
    body: ResultWrite,
    response: Response,
    user: Annotated[User, Depends(require_roles("admin", "teacher"))],
    db: Annotated[Session, Depends(get_db)],
) -> ResultOut:
    """Create (201) or change (200) one score; repeating the same score is a no-op."""
    result, created = result_service.put_result(db, user, body)
    db.commit()
    response.status_code = 201 if created else 200
    return result
