"""Reset codes an administrator hands to a user who cannot sign in."""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.errors import AppError
from app.core.security import hash_reset_code, new_reset_code
from app.models import AuditLog, PasswordReset, User


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
    db.execute(
        update(PasswordReset)
        .where(
            PasswordReset.user_id == target.id,
            PasswordReset.used_at.is_(None),
            PasswordReset.voided_at.is_(None),
        )
        .values(voided_at=now)
    )
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
