from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.dependencies import limit_public_requests
from app.schemas.dashboard import PublicStats
from app.services import dashboard as dashboard_service

router = APIRouter(prefix="/public", tags=["public"])


@router.get(
    "/stats",
    dependencies=[Depends(limit_public_requests)],
    responses={429: {"description": "Too many requests; see the Retry-After header."}},
)
def public_stats(db: Annotated[Session, Depends(get_db)]) -> PublicStats:
    """No login needed. Counts of active students, teachers and classes only."""
    return dashboard_service.public_stats(db)
