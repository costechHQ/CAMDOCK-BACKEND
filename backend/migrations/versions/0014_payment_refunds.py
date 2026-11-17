"""payment refunds

Revision ID: 0014
Revises: 0013
Create Date: 2026-11-17 12:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0014"
down_revision: str | Sequence[str] | None = "0013"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    # Hand-written: Alembic does not track standalone sequences or triggers.
    op.execute("CREATE SEQUENCE fee_refund_number_seq")
    # Lets a refund name its bill and its payment together, so the two cannot disagree.
    op.create_unique_constraint(
        "uq_fee_payments_id_fee_record_id", "fee_payments", ["id", "fee_record_id"]
    )
    op.create_table(
        "fee_refunds",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("fee_payment_id", sa.Integer(), nullable=False),
        sa.Column("fee_record_id", sa.Integer(), nullable=False),
        sa.Column("amount", sa.Numeric(precision=12, scale=2), nullable=False),
        sa.Column("reason", sa.String(length=200), nullable=False),
        sa.Column("receipt_number", sa.String(length=40), nullable=False),
        sa.Column(
            "refunded_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("created_by", sa.Integer(), nullable=False),
        sa.Column("idempotency_key", sa.String(length=64), nullable=True),
        sa.CheckConstraint("amount > 0", name=op.f("ck_fee_refunds_amount_positive")),
        sa.CheckConstraint(
            "length(btrim(reason)) >= 5", name=op.f("ck_fee_refunds_reason_length")
        ),
        sa.ForeignKeyConstraint(
            ["created_by"],
            ["users.id"],
            name=op.f("fk_fee_refunds_created_by_users"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["fee_payment_id", "fee_record_id"],
            ["fee_payments.id", "fee_payments.fee_record_id"],
            name=op.f("fk_fee_refunds_fee_payment_id_fee_payments"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_fee_refunds")),
        sa.UniqueConstraint(
            "created_by",
            "idempotency_key",
            name="uq_fee_refunds_created_by_idempotency_key",
        ),
        sa.UniqueConstraint(
            "receipt_number", name=op.f("uq_fee_refunds_receipt_number")
        ),
    )
    op.create_index(
        "ix_fee_refunds_fee_record_id", "fee_refunds", ["fee_record_id"], unique=False
    )
    op.create_index(
        "ix_fee_refunds_fee_payment_id", "fee_refunds", ["fee_payment_id"], unique=False
    )

    # A refund is never edited or deleted.
    op.execute(
        """
        CREATE FUNCTION fee_refunds_reject_change() RETURNS trigger AS $$
        BEGIN
            RAISE EXCEPTION 'fee_refunds is append-only'
                USING ERRCODE = 'restrict_violation';
        END;
        $$ LANGUAGE plpgsql
        """
    )
    op.execute(
        """
        CREATE TRIGGER fee_refunds_append_only
        BEFORE UPDATE OR DELETE ON fee_refunds
        FOR EACH ROW EXECUTE FUNCTION fee_refunds_reject_change()
        """
    )
    # A second line of defence behind the application's rules: a refund must be for a
    # live payment and must not exceed what is left of it.
    op.execute(
        """
        CREATE FUNCTION fee_refunds_check() RETURNS trigger AS $$
        DECLARE
            paid numeric;
            voided timestamptz;
            already numeric;
        BEGIN
            SELECT amount, voided_at INTO paid, voided
                FROM fee_payments WHERE id = NEW.fee_payment_id;
            IF voided IS NOT NULL THEN
                RAISE EXCEPTION 'a voided payment cannot be refunded'
                    USING ERRCODE = 'restrict_violation';
            END IF;
            SELECT COALESCE(SUM(amount), 0) INTO already
                FROM fee_refunds WHERE fee_payment_id = NEW.fee_payment_id;
            IF already + NEW.amount > paid THEN
                RAISE EXCEPTION 'refunds cannot exceed the payment'
                    USING ERRCODE = 'restrict_violation';
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql
        """
    )
    op.execute(
        """
        CREATE TRIGGER fee_refunds_limit
        BEFORE INSERT ON fee_refunds
        FOR EACH ROW EXECUTE FUNCTION fee_refunds_check()
        """
    )
    # ... and a payment that has been refunded can no longer be voided. This replaces
    # the function from revision 0013 with the same rules plus that one.
    op.execute(
        """
        CREATE OR REPLACE FUNCTION fee_payments_guard() RETURNS trigger AS $$
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
            IF NEW.voided_at IS NOT NULL
                AND EXISTS (SELECT 1 FROM fee_refunds WHERE fee_payment_id = OLD.id) THEN
                RAISE EXCEPTION 'a refunded payment cannot be voided'
                    USING ERRCODE = 'restrict_violation';
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql
        """
    )


def downgrade() -> None:
    """Downgrade schema."""
    # Dropping the table would silently put refunded money back into the totals.
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM fee_refunds) THEN
                RAISE EXCEPTION
                    'cannot downgrade: refunds exist and would be forgotten';
            END IF;
        END
        $$
        """
    )
    # Back to the revision 0013 version of the payment guard.
    op.execute(
        """
        CREATE OR REPLACE FUNCTION fee_payments_guard() RETURNS trigger AS $$
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
    op.execute("DROP TRIGGER fee_refunds_limit ON fee_refunds")
    op.execute("DROP FUNCTION fee_refunds_check()")
    op.execute("DROP TRIGGER fee_refunds_append_only ON fee_refunds")
    op.execute("DROP FUNCTION fee_refunds_reject_change()")
    op.drop_index("ix_fee_refunds_fee_payment_id", table_name="fee_refunds")
    op.drop_index("ix_fee_refunds_fee_record_id", table_name="fee_refunds")
    op.drop_table("fee_refunds")
    op.drop_constraint(
        "uq_fee_payments_id_fee_record_id", "fee_payments", type_="unique"
    )
    op.execute("DROP SEQUENCE fee_refund_number_seq")
