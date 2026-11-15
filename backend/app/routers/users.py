from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.dependencies import require_roles
from app.models import User
from app.schemas.users import UserCreate, UserOut
from app.services import accounts

router = APIRouter(prefix="/users", tags=["users"])


@router.post("", status_code=201)
def create_user(
    body: UserCreate,
    admin: Annotated[User, Depends(require_roles("admin"))],
    db: Annotated[Session, Depends(get_db)],
) -> UserOut:
    user = accounts.create_user(db, admin, body)
    db.commit()
    return UserOut(
        id=user.id, email=user.email, role=user.role, is_active=user.is_active
    )
