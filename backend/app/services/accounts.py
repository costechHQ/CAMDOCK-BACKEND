import re

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.errors import AppError
from app.core.security import PasswordPolicyError, hash_password, validate_new_password
from app.models import AuditLog, User
from app.schemas.users import UserCreate, UserPage, UserSummary
from app.services import auth as auth_service
from app.services.search import name_contains

EMAIL_PATTERN = re.compile(r"[^@\s]+@[^@\s]+\.[^@\s]+")
EMAIL_MAX_LENGTH = 254


class AccountError(ValueError):
    """An account cannot be created; the message is safe to show."""


def normalize_email(email: str) -> str:
    email = email.strip().lower()
    if len(email) > EMAIL_MAX_LENGTH or not EMAIL_PATTERN.fullmatch(email):
        raise AccountError("Enter a valid email address.")
    return email


class AccountConflict(AccountError):
    """An account with that email already exists."""

    def __init__(self) -> None:
        super().__init__("An account with that email already exists.")


def _create_account(
    db: Session, email: str, password: str, role: str, actor_id: int | None
) -> User:
    """Create an active account and audit it. Never changes an existing account."""
    email = normalize_email(email)
    validate_new_password(password, email)
    if db.execute(select(User.id).where(User.email == email)).first() is not None:
        raise AccountConflict

    user = User(
        email=email, password_hash=hash_password(password), role=role, is_active=True
    )
    try:
        with db.begin_nested():
            db.add(user)
            db.flush()
    except IntegrityError:  # lost a race with another creation of the same email
        raise AccountConflict from None
    db.add(
        AuditLog(
            actor_user_id=actor_id,
            action="user.create",
            entity_type="user",
            entity_id=user.id,
            changes={"email": email, "role": role},
        )
    )
    db.flush()
    return user


def create_admin(db: Session, email: str, password: str) -> User:
    """Create an active administrator and record it in the audit log.

    A system action with no signed-in user. The caller owns the transaction.
    """
    return _create_account(db, email, password, "admin", None)


def create_user(db: Session, actor: User, data: UserCreate) -> User:
    """Create a teacher or parent account on behalf of an administrator.

    The caller owns the transaction.
    """
    try:
        return _create_account(db, data.email, data.password, data.role, actor.id)
    except AccountConflict as exc:
        raise AppError(str(exc), status_code=409, code="conflict") from None
    except (AccountError, PasswordPolicyError) as exc:
        raise AppError(str(exc), status_code=422, code="validation_error") from None


def list_users(
    db: Session,
    *,
    q: str | None,
    role: str | None,
    is_active: bool | None,
    limit: int,
    offset: int,
) -> UserPage:
    """Accounts ordered by email then id, optionally narrowed by a part of the email,
    the role or the active flag. The total counts every match, not just this page."""
    conditions = []
    if q and q.strip():
        conditions.append(name_contains(User.email, q.strip()))
    if role is not None:
        conditions.append(User.role == role)
    if is_active is not None:
        conditions.append(User.is_active.is_(is_active))

    total = db.execute(
        select(func.count()).select_from(User).where(*conditions)
    ).scalar_one()
    users = (
        db.execute(
            select(User)
            .where(*conditions)
            .order_by(User.email, User.id)
            .limit(limit)
            .offset(offset)
        )
        .scalars()
        .all()
    )
    return UserPage(
        items=[UserSummary.model_validate(u) for u in users],
        total=total,
        limit=limit,
        offset=offset,
    )


def set_user_active(
    db: Session, actor: User, user_id: int, active: bool
) -> UserSummary:
    """Disable or re-enable an account and audit the change.

    Disabling also ends the account's sessions, so re-enabling it does not bring old
    tokens back. An administrator cannot disable themselves, and an administrator
    cannot be disabled unless another one stays active. Setting the value an account
    already has changes and records nothing. The caller owns the transaction.
    """
    role = db.execute(select(User.role).where(User.id == user_id)).scalar_one_or_none()
    if role is None:
        raise AppError("User not found.", status_code=404, code="not_found")
    active_admins: list[int] = []
    if not active and role == "admin":
        # Lock every active administrator, in a fixed order, before counting how many
        # would remain: two administrators disabling each other at once then queue
        # instead of both succeeding (or deadlocking).
        active_admins = list(
            db.execute(
                select(User.id)
                .where(User.role == "admin", User.is_active.is_(True))
                .order_by(User.id)
                .with_for_update()
            )
            .scalars()
            .all()
        )
    user = db.execute(
        select(User).where(User.id == user_id).with_for_update()
    ).scalar_one()

    if user.is_active == active:
        return UserSummary.model_validate(user)
    if not active:
        if user.id == actor.id:
            raise AppError(
                "You cannot disable your own account.",
                status_code=409,
                code="conflict",
            )
        if user.role == "admin" and not [i for i in active_admins if i != user.id]:
            raise AppError(
                "The last active administrator cannot be disabled.",
                status_code=409,
                code="conflict",
            )

    user.is_active = active
    if not active:
        auth_service.revoke_user_sessions(db, user.id)
    db.add(
        AuditLog(
            actor_user_id=actor.id,
            action="user.enable" if active else "user.disable",
            entity_type="user",
            entity_id=user.id,
            changes={"email": user.email, "role": user.role},
        )
    )
    db.flush()
    return UserSummary.model_validate(user)
