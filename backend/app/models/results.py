from decimal import Decimal

from sqlalchemy import CheckConstraint, ForeignKey, Numeric, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.academic import TimestampMixin


class Result(TimestampMixin, Base):
    __tablename__ = "results"
    __table_args__ = (
        # The term owns its academic year, so the year is not part of the key.
        UniqueConstraint(
            "student_id",
            "subject_id",
            "term_id",
            name="uq_results_student_subject_term",
        ),
        CheckConstraint("score >= 0 AND score <= 100", name="score_range"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    student_id: Mapped[int] = mapped_column(
        ForeignKey("students.id", ondelete="RESTRICT")
    )
    subject_id: Mapped[int] = mapped_column(
        ForeignKey("subjects.id", ondelete="RESTRICT")
    )
    term_id: Mapped[int] = mapped_column(ForeignKey("terms.id", ondelete="RESTRICT"))
    # The class the student was in when the score was recorded, so old result sheets
    # stay correct after a promotion.
    class_id: Mapped[int] = mapped_column(ForeignKey("classes.id", ondelete="RESTRICT"))
    score: Mapped[Decimal] = mapped_column(Numeric(5, 2))
    created_by: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    updated_by: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT")
    )
