from typing import Annotated

from fastapi import APIRouter, Depends, Query
from pydantic import Field
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.dependencies import require_roles
from app.models import User
from app.schemas.dashboard import AdminOverview, ParentOverview, TeacherOverview
from app.services import dashboard as dashboard_service

router = APIRouter(prefix="/dashboard", tags=["dashboard"])


@router.get("/overview")
def overview(
    user: Annotated[User, Depends(require_roles("admin", "teacher", "parent"))],
    db: Annotated[Session, Depends(get_db)],
    term_id: Annotated[int | None, Query(ge=1)] = None,
) -> Annotated[
    AdminOverview | TeacherOverview | ParentOverview, Field(discriminator="role")
]:
    """Role-specific overview for the selected term (default: the current term).
    Admins get school-wide figures with fees for the term and for all time, kept
    separate. Teachers get "My overview": their own classes only, no fees. Parents get their
    linked children."""
    return dashboard_service.overview(db, user, term_id)
