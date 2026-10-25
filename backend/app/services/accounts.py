import re

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.security import hash_password, validate_new_password
from app.models import AuditLog, User

EMAIL_PATTERN = re.compile(r"[^@\s]+@[^@\s]+\.[^@\s]+")
EMAIL_MAX_LENGTH = 254


class AccountError(ValueError):
    """An account cannot be created; the message is safe to show."""


def normalize_email(email: str) -> str:
    email = email.strip().lower()
    if len(email) > EMAIL_MAX_LENGTH or not EMAIL_PATTERN.fullmatch(email):
        raise AccountError("Enter a valid email address.")
    return email


def create_admin(db: Session, email: str, password: str) -> User:
    """Create an active administrator and record it in the audit log.

    Never changes an existing account. The caller owns the transaction.
    """
    email = normalize_email(email)
    validate_new_password(password, email)
    if db.execute(select(User.id).where(User.email == email)).first() is not None:
        raise AccountError("An account with that email already exists.")

    user = User(
        email=email, password_hash=hash_password(password), role="admin", is_active=True
    )
    try:
        with db.begin_nested():
            db.add(user)
            db.flush()
    except IntegrityError:  # lost a race with another creation of the same email
        raise AccountError("An account with that email already exists.") from None
    db.add(
        AuditLog(
            actor_user_id=None,  # system action: no signed-in user
            action="user.create",
            entity_type="user",
            entity_id=user.id,
            changes={"email": email, "role": "admin"},
        )
    )
    db.flush()
    return user
