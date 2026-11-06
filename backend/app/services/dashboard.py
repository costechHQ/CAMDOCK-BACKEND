from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import SchoolClass, Student, Teacher
from app.schemas.dashboard import PublicStats


def _active(model):
    return select(func.count()).select_from(model).where(model.is_active.is_(True))


def public_stats(db: Session) -> PublicStats:
    """Active students, teaching staff and classes, in one round trip."""
    row = db.execute(
        select(
            _active(Student).scalar_subquery().label("students"),
            _active(Teacher).scalar_subquery().label("teachers"),
            _active(SchoolClass).scalar_subquery().label("classes"),
        )
    ).one()
    return PublicStats(
        students=row.students, teachers=row.teachers, classes=row.classes
    )
