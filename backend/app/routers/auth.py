from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.dependencies import AuthContext, get_auth_context, get_current_user
from app.models import User
from app.schemas.auth import LoginRequest, LoginResponse, UserOut
from app.services import auth as auth_service

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/login")
def login(body: LoginRequest, db: Annotated[Session, Depends(get_db)]) -> LoginResponse:
    user = auth_service.authenticate(db, body.email, body.password)
    if user is None:
        # One message for a wrong password, an unknown email and a disabled account.
        raise HTTPException(
            status_code=401,
            detail="Invalid email or password.",
            headers={"WWW-Authenticate": "Bearer"},
        )
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
