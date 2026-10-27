from fastapi import APIRouter, Depends

from app.dependencies import require_roles
from app.models.fees import FEE_TYPES
from app.schemas.fees import FeeTypes

router = APIRouter(prefix="/fee-types", tags=["fees"])


@router.get("", dependencies=[Depends(require_roles("admin", "parent"))])
def list_fee_types() -> FeeTypes:
    """The fixed list of fee categories."""
    return FeeTypes(items=list(FEE_TYPES))
