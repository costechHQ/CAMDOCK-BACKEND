"""payment idempotency key

Revision ID: 0011
Revises: 0010
Create Date: 2026-11-09 12:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0011"
down_revision: str | Sequence[str] | None = "0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        "fee_payments",
        sa.Column("idempotency_key", sa.String(length=64), nullable=True),
    )
    op.create_unique_constraint(
        "uq_fee_payments_created_by_idempotency_key",
        "fee_payments",
        ["created_by", "idempotency_key"],
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_constraint(
        "uq_fee_payments_created_by_idempotency_key", "fee_payments", type_="unique"
    )
    op.drop_column("fee_payments", "idempotency_key")
