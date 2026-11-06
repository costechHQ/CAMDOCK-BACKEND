from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.dependencies import require_roles
from app.models import User
from app.schemas.dashboard import AdminOverview
from app.services import dashboard as dashboard_service

router = APIRouter(prefix="/dashboard", tags=["dashboard"])


@router.get("/overview")
def overview(
    user: Annotated[User, Depends(require_roles("admin"))],
    db: Annotated[Session, Depends(get_db)],
    term_id: Annotated[int | None, Query(ge=1)] = None,
) -> AdminOverview:
    """Role-specific overview for the selected term (default: the current term).
    Fees are given for the term and for all time, separately."""
    return dashboard_service.admin_overview(db, user, term_id)
