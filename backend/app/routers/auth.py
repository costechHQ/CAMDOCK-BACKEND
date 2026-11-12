import logging
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy.orm import Session

from app.core.client_ip import client_ip
from app.core.config import get_settings
from app.core.errors import AppError
from app.db.session import get_db
from app.dependencies import AuthContext, get_auth_context, get_current_user
from app.models import User
from app.schemas.auth import LoginRequest, LoginResponse, PasswordChange, UserOut
from app.services import accounts
from app.services import auth as auth_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post(
    "/login",
    responses={
        429: {"description": "Too many failed attempts; see the Retry-After header."}
    },
)
def login(
    body: LoginRequest, request: Request, db: Annotated[Session, Depends(get_db)]
) -> LoginResponse:
    throttle = request.app.state.login_throttle
    ip = client_ip(request, get_settings().trusted_proxy_networks)
    # Checked before the password so a block cannot be used to keep guessing.
    wait = throttle.retry_after(body.email, ip)
    if wait:
        raise AppError(
            "Too many failed sign-in attempts. Try again later.",
            status_code=429,
            code="too_many_attempts",
            headers={"Retry-After": str(wait)},
        )
    user = auth_service.authenticate(db, body.email, body.password)
    if user is None:
        if throttle.record_failure(body.email, ip):
            logger.warning("Sign-in attempts blocked for client %s", ip)
        # One message for a wrong password, an unknown email and a disabled account.
        raise HTTPException(
            status_code=401,
            detail="Invalid email or password.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    throttle.record_success(body.email)
    token, expires_at = auth_service.create_session(db, user)
    db.commit()
    return LoginResponse(
        access_token=token, expires_at=expires_at, user=UserOut.model_validate(user)
    )


@router.post("/logout", status_code=204)
def logout(
    context: Annotated[AuthContext, Depends(get_auth_context)],
    db: Annotated[Session, Depends(get_db)],
) -> Response:
    auth_service.revoke_session(db, context.session)
    db.commit()
    return Response(status_code=204)


@router.get("/me")
def me(user: Annotated[User, Depends(get_current_user)]) -> UserOut:
    return UserOut.model_validate(user)


@router.post(
    "/change-password",
    status_code=204,
    responses={
        422: {
            "description": "The current password is wrong (code `incorrect_password`) "
            "or the new one is not acceptable."
        },
        429: {"description": "Too many wrong passwords; see the Retry-After header."},
    },
)
def change_password(
    body: PasswordChange,
    request: Request,
    context: Annotated[AuthContext, Depends(get_auth_context)],
    db: Annotated[Session, Depends(get_db)],
) -> Response:
    """Change your own password. Your other sessions end; this one stays. A wrong
    current password counts toward the same limit as a wrong sign-in password."""
    throttle = request.app.state.login_throttle
    ip = client_ip(request, get_settings().trusted_proxy_networks)
    email = context.user.email
    wait = throttle.retry_after(email, ip)
    if wait:
        raise AppError(
            "Too many failed attempts. Try again later.",
            status_code=429,
            code="too_many_attempts",
            headers={"Retry-After": str(wait)},
        )
    try:
        accounts.change_own_password(
            db,
            context.user,
            context.session.id,
            body.current_password,
            body.new_password,
        )
    except accounts.IncorrectPassword:
        db.rollback()
        if throttle.record_failure(email, ip):
            logger.warning("Password change attempts blocked for client %s", ip)
        raise AppError(
            "The current password is incorrect.",
            status_code=422,
            code="incorrect_password",
        ) from None
    db.commit()
    throttle.record_success(email)
    return Response(status_code=204)
