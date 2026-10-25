"""student number sequence

Revision ID: 0008
Revises: 0007
Create Date: 2026-10-25 12:00:00.000000

"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0008"
down_revision: str | Sequence[str] | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    # Hand-written: Alembic autogenerate does not track standalone sequences.
    op.execute("CREATE SEQUENCE student_number_seq")


def downgrade() -> None:
    """Downgrade schema."""
    op.execute("DROP SEQUENCE student_number_seq")
