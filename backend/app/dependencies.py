from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.models import AuthSession, User
from app.services import auth as auth_service

bearer_scheme = HTTPBearer(auto_error=False)


@dataclass(frozen=True)
class AuthContext:
    session: AuthSession
    user: User


def get_auth_context(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer_scheme)],
    db: Annotated[Session, Depends(get_db)],
) -> AuthContext:
    """Resolve the bearer token on every request, so logout, expiry and disabling an
    account take effect immediately. Every failure looks the same to the caller."""
    found = (
        auth_service.get_active_session(db, credentials.credentials)
        if credentials is not None
        else None
    )
    if found is None:
        raise HTTPException(
            status_code=401,
            detail="Not authenticated.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return AuthContext(session=found[0], user=found[1])


def get_current_user(
    context: Annotated[AuthContext, Depends(get_auth_context)],
) -> User:
    return context.user
