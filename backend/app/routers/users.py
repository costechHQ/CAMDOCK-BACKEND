from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.dependencies import require_roles
from app.models import User
from app.schemas.users import (
    ResetCodeIssued,
    UserCreate,
    UserOut,
    UserPage,
    UserStatusUpdate,
    UserSummary,
)
from app.services import accounts, password_resets

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


@router.get("", dependencies=[Depends(require_roles("admin"))])
def list_users(
    db: Annotated[Session, Depends(get_db)],
    q: Annotated[str | None, Query(max_length=100)] = None,
    role: Literal["admin", "teacher", "parent"] | None = None,
    is_active: bool | None = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> UserPage:
    """Accounts by email. `q` matches part of the email; no credential is ever shown."""
    return accounts.list_users(
        db, q=q, role=role, is_active=is_active, limit=limit, offset=offset
    )


@router.patch("/{user_id}")
def update_user(
    user_id: int,
    body: UserStatusUpdate,
    admin: Annotated[User, Depends(require_roles("admin"))],
    db: Annotated[Session, Depends(get_db)],
) -> UserSummary:
    """Disable or re-enable an account. Disabling ends its sessions. You cannot disable
    yourself or the last active administrator (409)."""
    user = accounts.set_user_active(db, admin, user_id, body.is_active)
    db.commit()
    return user


@router.post("/{user_id}/password-reset", status_code=201)
def issue_password_reset(
    user_id: int,
    admin: Annotated[User, Depends(require_roles("admin"))],
    db: Annotated[Session, Depends(get_db)],
) -> ResetCodeIssued:
    """Issue a one-time reset code to hand to a user who cannot sign in. It is shown
    once and expires; issuing another voids it. Not for your own account (409), nor for
    a disabled one (409)."""
    issued = password_resets.issue_reset_code(db, admin, user_id)
    db.commit()
    return ResetCodeIssued(
        code=issued.code,
        expires_at=issued.expires_at,
        user=UserOut(
            id=issued.user.id,
            email=issued.user.email,
            role=issued.user.role,
            is_active=issued.user.is_active,
        ),
    )
