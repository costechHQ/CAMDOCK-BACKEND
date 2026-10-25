from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.dependencies import require_roles
from app.models import SchoolClass, User
from app.schemas.academic import ClassOut
from app.services import access

router = APIRouter(prefix="/classes", tags=["classes"])


@router.get("")
def list_classes(
    user: Annotated[User, Depends(require_roles("admin", "teacher"))],
    db: Annotated[Session, Depends(get_db)],
) -> list[ClassOut]:
    """Active classes the caller may reach: all for admins, assigned ones for teachers."""
    rows = db.scalars(
        select(SchoolClass)
        .where(SchoolClass.is_active.is_(True), access.class_scope(user))
        .order_by(SchoolClass.name)
    )
    return [ClassOut.model_validate(row) for row in rows]
