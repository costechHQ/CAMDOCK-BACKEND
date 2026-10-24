import logging
from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.errors import AppError
from app.db.session import get_db

logger = logging.getLogger(__name__)

router = APIRouter(tags=["health"])


@router.get("/health")
def health(db: Annotated[Session, Depends(get_db)]) -> dict[str, str]:
    try:
        db.execute(text("SELECT 1"))
    except SQLAlchemyError:
        # Details stay in the server log; the response never exposes them.
        logger.exception("Health check failed: database unreachable")
        raise AppError(
            "Service unavailable.", status_code=503, code="service_unavailable"
        ) from None
    return {"status": "ok", "database": "ok"}
