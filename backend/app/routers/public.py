from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.schemas.dashboard import PublicStats
from app.services import dashboard as dashboard_service

router = APIRouter(prefix="/public", tags=["public"])


@router.get("/stats")
def public_stats(db: Annotated[Session, Depends(get_db)]) -> PublicStats:
    """No login needed. Counts of active students, teachers and classes only."""
    return dashboard_service.public_stats(db)
