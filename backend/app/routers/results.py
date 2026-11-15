from typing import Annotated

from fastapi import APIRouter, Depends, Query, Response
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.dependencies import require_roles
from app.models import User
from app.schemas.results import ResultOut, ResultPage, ResultWrite
from app.services import results as result_service

router = APIRouter(prefix="/results", tags=["results"])


@router.get("")
def list_results(
    user: Annotated[User, Depends(require_roles("admin", "teacher"))],
    db: Annotated[Session, Depends(get_db)],
    term_id: Annotated[int, Query(ge=1)],
    class_id: int | None = None,
    subject_id: int | None = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> ResultPage:
    """Admins see every score; teachers only scores for classes and subjects they teach."""
    return result_service.list_results(
        db,
        user,
        term_id=term_id,
        class_id=class_id,
        subject_id=subject_id,
        limit=limit,
        offset=offset,
    )


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
