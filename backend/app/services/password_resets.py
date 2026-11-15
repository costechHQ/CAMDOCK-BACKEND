"""Reset codes an administrator hands to a user who cannot sign in."""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.errors import AppError
from app.core.security import (
    PasswordPolicyError,
    hash_password,
    hash_reset_code,
    new_reset_code,
    validate_new_password,
)
from app.models import AuditLog, PasswordReset, User
from app.services import auth as auth_service


def void_live_codes(db: Session, user_id: int, now: datetime | None = None) -> int:
    """Cancel an account's unused reset code, if it has one; returns how many."""
    result = db.execute(
        update(PasswordReset)
        .where(
            PasswordReset.user_id == user_id,
            PasswordReset.used_at.is_(None),
            PasswordReset.voided_at.is_(None),
        )
        .values(voided_at=now or datetime.now(UTC))
    )
    return result.rowcount


class InvalidResetCode(Exception):
    """The code does not unlock a reset. Every reason looks the same to the caller."""


@dataclass(frozen=True)
class IssuedCode:
    code: str
    expires_at: datetime
    user: User


def issue_reset_code(db: Session, actor: User, user_id: int) -> IssuedCode:
    """Create a one-time code for another account and void any earlier live one.

    The code is returned once and only its hash is stored. The target account row is
    locked first, so two administrators issuing at once leave exactly one live code.
    Nothing about the account (password, sessions) changes until the code is redeemed.
    The caller owns the transaction.
    """
    target = db.execute(
        select(User)
        .where(User.id == user_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    ).scalar_one_or_none()
    if target is None:
        raise AppError("User not found.", status_code=404, code="not_found")
    if target.id == actor.id:
        raise AppError(
            "Use change password for your own account.",
            status_code=409,
            code="conflict",
        )
    if not target.is_active:
        raise AppError(
            "That account is disabled. Enable it before issuing a reset code.",
            status_code=409,
            code="conflict",
        )

    now = datetime.now(UTC)
    expires_at = now + timedelta(hours=get_settings().password_reset_ttl_hours)
    void_live_codes(db, target.id, now)
    code = new_reset_code()
    db.add(
        PasswordReset(
            user_id=target.id,
            code_hash=hash_reset_code(target.id, code),
            created_by=actor.id,
            created_at=now,
            expires_at=expires_at,
        )
    )
    db.add(
        AuditLog(
            actor_user_id=actor.id,
            action="user.password_reset_issue",
            entity_type="user",
            entity_id=target.id,
            changes={
                "email": target.email,
                "role": target.role,
                "expires_at": expires_at.isoformat(),
            },
        )
    )
    db.flush()
    return IssuedCode(code=code, expires_at=expires_at, user=target)


def redeem_reset_code(db: Session, email: str, code: str, new_password: str) -> int:
    """Use a reset code to set a new password; returns how many sessions were ended.

    Unknown email, wrong code, a code for someone else, expired, used, replaced and a
    disabled account all raise `InvalidResetCode` alike. The account row and the code
    row are locked, so two requests with the same code cannot both succeed. A new
    password that breaks the rules is refused (422) without using up the code. All of
    the account's sessions end. The caller owns the transaction.
    """
    now = datetime.now(UTC)
    found = db.execute(select(User.id).where(User.email == email)).scalar_one_or_none()
    user_id = found or 0
    digest = hash_reset_code(user_id, code)
    user = None
    if found is not None:
        user = db.execute(
            select(User)
            .where(User.id == found)
            .with_for_update()
            .execution_options(populate_existing=True)
        ).scalar_one()
    reset = db.execute(
        select(PasswordReset)
        .where(
            PasswordReset.user_id == user_id,
            PasswordReset.code_hash == digest,
            PasswordReset.used_at.is_(None),
            PasswordReset.voided_at.is_(None),
            PasswordReset.expires_at > now,
        )
        .with_for_update()
    ).scalar_one_or_none()
    if user is None or not user.is_active or reset is None:
        raise InvalidResetCode

    try:
        validate_new_password(new_password, user.email)
    except PasswordPolicyError as exc:
        raise AppError(str(exc), status_code=422, code="validation_error") from None

    reset.used_at = now
    user.password_hash = hash_password(new_password)
    ended = auth_service.revoke_user_sessions(db, user.id)
    db.add(
        AuditLog(
            actor_user_id=user.id,
            action="user.password_reset_complete",
            entity_type="user",
            entity_id=user.id,
            changes={"sessions_ended": ended, "issued_by": reset.created_by},
        )
    )
    db.flush()
    return ended
