import logging
from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.errors import AppError
from app.core.logging import log_unexpected
from app.db.session import get_db
from app.services import migrations

logger = logging.getLogger(__name__)

router = APIRouter(tags=["health"])


@router.get("/health")
def health(db: Annotated[Session, Depends(get_db)]) -> dict[str, str]:
    try:
        db.execute(text("SELECT 1"))
    except SQLAlchemyError as exc:
        # The log gets the error type only: driver messages can name hosts and users.
        log_unexpected(logger, "Health check failed: database unreachable", exc)
        raise AppError(
            "Service unavailable.", status_code=503, code="service_unavailable"
        ) from None
    return {"status": "ok", "database": "ok"}


@router.get("/health/ready")
def ready(db: Annotated[Session, Depends(get_db)]) -> dict[str, str]:
    """For deploy gates: the database answers and its schema is at the expected
    migration. Failures are generic; the log says which check failed."""
    try:
        current = migrations.schema_is_current(db)
    except SQLAlchemyError as exc:
        log_unexpected(logger, "Readiness check failed: database error", exc)
        current = None
    if not current:
        if current is not None:
            logger.error(
                "Readiness check failed: schema is not at the expected revision"
            )
        raise AppError(
            "Service unavailable.", status_code=503, code="service_unavailable"
        )
    return {"status": "ok", "database": "ok", "migrations": "ok"}
