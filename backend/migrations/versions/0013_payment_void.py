"""payment void

Revision ID: 0013
Revises: 0012
Create Date: 2026-11-16 12:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0013"
down_revision: str | Sequence[str] | None = "0012"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        "fee_payments",
        sa.Column("voided_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column("fee_payments", sa.Column("voided_by", sa.Integer(), nullable=True))
    op.add_column(
        "fee_payments", sa.Column("void_reason", sa.String(length=200), nullable=True)
    )
    op.create_foreign_key(
        op.f("fk_fee_payments_voided_by_users"),
        "fee_payments",
        "users",
        ["voided_by"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_check_constraint(
        op.f("ck_fee_payments_void_fields_together"),
        "fee_payments",
        "(voided_at IS NULL) = (voided_by IS NULL) "
        "AND (voided_at IS NULL) = (void_reason IS NULL)",
    )
    op.create_check_constraint(
        op.f("ck_fee_payments_void_reason_length"),
        "fee_payments",
        "void_reason IS NULL OR length(btrim(void_reason)) >= 5",
    )
    # Hand-written: Alembic does not see triggers. Money rows are append-only: a payment
    # is never deleted or edited, and the only change allowed is to void it, once.
    op.execute(
        """
        CREATE FUNCTION fee_payments_guard() RETURNS trigger AS $$
        BEGIN
            IF TG_OP = 'DELETE' THEN
                RAISE EXCEPTION 'fee_payments rows cannot be deleted'
                    USING ERRCODE = 'restrict_violation';
            END IF;
            IF OLD.voided_at IS NOT NULL THEN
                RAISE EXCEPTION 'a voided payment cannot be changed'
                    USING ERRCODE = 'restrict_violation';
            END IF;
            IF NEW.id IS DISTINCT FROM OLD.id
                OR NEW.fee_record_id IS DISTINCT FROM OLD.fee_record_id
                OR NEW.amount IS DISTINCT FROM OLD.amount
                OR NEW.paid_at IS DISTINCT FROM OLD.paid_at
                OR NEW.receipt_number IS DISTINCT FROM OLD.receipt_number
                OR NEW.created_by IS DISTINCT FROM OLD.created_by
                OR NEW.idempotency_key IS DISTINCT FROM OLD.idempotency_key
                OR NEW.created_at IS DISTINCT FROM OLD.created_at THEN
                RAISE EXCEPTION 'only the void fields of a payment can be set'
                    USING ERRCODE = 'restrict_violation';
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql
        """
    )
    op.execute(
        """
        CREATE TRIGGER fee_payments_append_only
        BEFORE UPDATE OR DELETE ON fee_payments
        FOR EACH ROW EXECUTE FUNCTION fee_payments_guard()
        """
    )


def downgrade() -> None:
    """Downgrade schema."""
    # Dropping the columns would silently count voided payments again.
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM fee_payments WHERE voided_at IS NOT NULL) THEN
                RAISE EXCEPTION
                    'cannot downgrade: voided payments exist and would be counted again';
            END IF;
        END
        $$
        """
    )
    op.execute("DROP TRIGGER fee_payments_append_only ON fee_payments")
    op.execute("DROP FUNCTION fee_payments_guard()")
    op.drop_constraint(
        op.f("ck_fee_payments_void_reason_length"), "fee_payments", type_="check"
    )
    op.drop_constraint(
        op.f("ck_fee_payments_void_fields_together"), "fee_payments", type_="check"
    )
    op.drop_constraint(
        op.f("fk_fee_payments_voided_by_users"), "fee_payments", type_="foreignkey"
    )
    op.drop_column("fee_payments", "void_reason")
    op.drop_column("fee_payments", "voided_by")
    op.drop_column("fee_payments", "voided_at")
