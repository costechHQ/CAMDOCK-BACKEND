from datetime import UTC, datetime, timedelta
from functools import lru_cache

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.security import (
    hash_password,
    hash_token,
    needs_rehash,
    new_session_token,
    verify_password,
)
from app.models import AuthSession, User


@lru_cache
def _dummy_hash() -> str:
    return hash_password("camdock-dummy-password")


def authenticate(db: Session, email: str, password: str) -> User | None:
    """Return the active user whose credentials match, else None.

    A password check always runs, even for an unknown email, so response time does not
    reveal which emails have accounts.
    """
    user = db.execute(select(User).where(User.email == email)).scalar_one_or_none()
    if user is None:
        verify_password(password, _dummy_hash())
        return None
    password_ok = verify_password(password, user.password_hash)
    if not password_ok or not user.is_active:
        return None
    if needs_rehash(user.password_hash):
        user.password_hash = hash_password(password)
    return user


def create_session(db: Session, user: User) -> tuple[str, datetime]:
    """Start a session; returns the bearer token (shown once) and its expiry."""
    now = datetime.now(UTC)
    expires_at = now + timedelta(hours=get_settings().session_ttl_hours)
    token = new_session_token()
    db.add(
        AuthSession(
            user_id=user.id,
            token_hash=hash_token(token),
            created_at=now,
            expires_at=expires_at,
        )
    )
    user.last_login_at = now
    db.flush()
    return token, expires_at


def get_active_session(db: Session, token: str) -> tuple[AuthSession, User] | None:
    """The session and user for a usable token: not revoked, not expired, user active."""
    row = db.execute(
        select(AuthSession, User)
        .join(User, User.id == AuthSession.user_id)
        .where(
            AuthSession.token_hash == hash_token(token),
            AuthSession.revoked_at.is_(None),
            AuthSession.expires_at > datetime.now(UTC),
            User.is_active.is_(True),
        )
    ).one_or_none()
    return None if row is None else (row[0], row[1])


def revoke_session(db: Session, session: AuthSession) -> None:
    session.revoked_at = datetime.now(UTC)
    db.flush()
