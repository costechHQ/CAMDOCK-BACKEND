from datetime import date

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.errors import AppError
from app.models import AuditLog, SchoolClass, Student, User
from app.schemas.students import StudentCreate, StudentOut


def generate_student_number(db: Session) -> str:
    """`CAM-<year>-<5 digits>` from a database sequence, so it is unique under
    concurrency. The sequence never resets; a rolled-back creation leaves a gap."""
    sequence = db.execute(select(func.nextval("student_number_seq"))).scalar_one()
    return f"CAM-{date.today().year}-{sequence:05d}"


def create_student(db: Session, actor: User, data: StudentCreate) -> StudentOut:
    """Create an active student and audit it. The caller owns the transaction."""
    school_class = db.execute(
        select(SchoolClass).where(
            SchoolClass.id == data.class_id, SchoolClass.is_active.is_(True)
        )
    ).scalar_one_or_none()
    if school_class is None:
        raise AppError("Class not found.", status_code=422, code="validation_error")

    student = Student(
        student_number=generate_student_number(db),
        full_name=data.full_name,
        class_id=school_class.id,
        gender=data.gender,
        date_of_birth=data.date_of_birth,
        guardian_display_name=data.guardian_display_name,
        status="active",
    )
    db.add(student)
    db.flush()
    db.add(
        AuditLog(
            actor_user_id=actor.id,
            action="student.create",
            entity_type="student",
            entity_id=student.id,
            changes={
                "student_number": student.student_number,
                "full_name": student.full_name,
                "class_id": student.class_id,
                "gender": student.gender,
                "guardian_display_name": student.guardian_display_name,
            },
        )
    )
    db.flush()
    return StudentOut(
        id=student.id,
        student_number=student.student_number,
        full_name=student.full_name,
        class_id=student.class_id,
        class_name=school_class.name,
        gender=student.gender,
        date_of_birth=student.date_of_birth,
        guardian_display_name=student.guardian_display_name,
        status=student.status,
    )
