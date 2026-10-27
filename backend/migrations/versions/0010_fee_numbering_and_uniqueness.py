"""fee numbering and uniqueness

Revision ID: 0010
Revises: 0009
Create Date: 2026-10-28 12:00:00.000000

"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0010"
down_revision: str | Sequence[str] | None = "0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    # Hand-written: Alembic autogenerate does not track standalone sequences.
    op.execute("CREATE SEQUENCE fee_record_number_seq")
    op.execute("CREATE SEQUENCE fee_payment_number_seq")
    op.create_unique_constraint(
        "uq_fee_records_student_id_term_id_fee_type",
        "fee_records",
        ["student_id", "term_id", "fee_type"],
    )
    op.create_index(
        "ix_fee_payments_fee_record_id", "fee_payments", ["fee_record_id"], unique=False
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index("ix_fee_payments_fee_record_id", table_name="fee_payments")
    op.drop_constraint(
        "uq_fee_records_student_id_term_id_fee_type", "fee_records", type_="unique"
    )
    op.execute("DROP SEQUENCE fee_payment_number_seq")
    op.execute("DROP SEQUENCE fee_record_number_seq")
