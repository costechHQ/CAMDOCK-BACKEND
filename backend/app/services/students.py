from datetime import date

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.errors import AppError
from app.models import AuditLog, SchoolClass, Student, User
from app.schemas.students import StudentCreate, StudentOut, StudentPage
from app.services import access
from app.services.search import name_contains


def _to_out(student: Student, class_name: str) -> StudentOut:
    return StudentOut(
        id=student.id,
        student_number=student.student_number,
        full_name=student.full_name,
        class_id=student.class_id,
        class_name=class_name,
        gender=student.gender,
        date_of_birth=student.date_of_birth,
        guardian_display_name=student.guardian_display_name,
        status=student.status,
    )


def list_students(
    db: Session,
    user: User,
    *,
    q: str | None,
    class_id: int | None,
    limit: int,
    offset: int,
) -> StudentPage:
    """Active students the user may reach, scoped in SQL, ordered by name then id."""
    conditions = [Student.is_active.is_(True), access.student_scope(user)]
    if q and q.strip():
        conditions.append(name_contains(Student.full_name, q.strip()))
    if class_id is not None:
        conditions.append(Student.class_id == class_id)

    total = db.execute(
        select(func.count()).select_from(Student).where(*conditions)
    ).scalar_one()
    rows = db.execute(
        select(Student, SchoolClass.name)
        .join(SchoolClass, SchoolClass.id == Student.class_id)
        .where(*conditions)
        .order_by(Student.full_name, Student.id)
        .limit(limit)
        .offset(offset)
    ).all()
    return StudentPage(
        items=[_to_out(student, class_name) for student, class_name in rows],
        total=total,
        limit=limit,
        offset=offset,
    )


def get_student(db: Session, user: User, student_id: int) -> StudentOut:
    """One active student in the user's scope; missing, inactive and out-of-scope
    students all give the same 404."""
    row = db.execute(
        select(Student, SchoolClass.name)
        .join(SchoolClass, SchoolClass.id == Student.class_id)
        .where(
            Student.id == student_id,
            Student.is_active.is_(True),
            access.student_scope(user),
        )
    ).one_or_none()
    if row is None:
        raise AppError("Student not found.", status_code=404, code="not_found")
    return _to_out(*row)


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
    return _to_out(student, school_class.name)
