from datetime import date

from sqlalchemy import Boolean, CheckConstraint, Date, ForeignKey, String, text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.academic import TimestampMixin, _in_list

STUDENT_STATUSES = ("active", "graduated", "transferred", "withdrawn")
GENDERS = ("male", "female")


class Student(TimestampMixin, Base):
    __tablename__ = "students"
    __table_args__ = (
        CheckConstraint(_in_list("status", STUDENT_STATUSES), name="valid_status"),
        CheckConstraint(
            "gender IS NULL OR " + _in_list("gender", GENDERS), name="valid_gender"
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    student_number: Mapped[str] = mapped_column(String(30), unique=True)
    full_name: Mapped[str] = mapped_column(String(150))
    class_id: Mapped[int] = mapped_column(ForeignKey("classes.id", ondelete="RESTRICT"))
    gender: Mapped[str | None] = mapped_column(String(10))
    date_of_birth: Mapped[date | None] = mapped_column(Date)
    guardian_display_name: Mapped[str | None] = mapped_column(String(150))
    status: Mapped[str] = mapped_column(String(20), server_default="active")
    is_active: Mapped[bool] = mapped_column(Boolean, server_default=text("true"))


class ParentStudent(Base):
    __tablename__ = "parent_student"

    parent_user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), primary_key=True
    )
    student_id: Mapped[int] = mapped_column(
        ForeignKey("students.id", ondelete="RESTRICT"), primary_key=True
    )
    relationship: Mapped[str | None] = mapped_column(String(30))
