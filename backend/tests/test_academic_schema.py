from datetime import date

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import delete, func, insert, select, update
from sqlalchemy.exc import IntegrityError

from app.db.base import Base
from app.models import AcademicYear, ClassSubject, SchoolClass, Subject, Term

YEAR = {
    "name": "2026/2027",
    "start_date": date(2026, 9, 1),
    "end_date": date(2027, 7, 31),
}


def add(db, model, **values) -> int:
    return db.execute(insert(model).values(**values).returning(model.id)).scalar_one()


def add_year(db, **overrides) -> int:
    return add(db, AcademicYear, **{**YEAR, **overrides})


def add_class(db, name="JSS 1", level="junior_secondary") -> int:
    return add(db, SchoolClass, name=name, level=level)


def assert_rejected(db, statement) -> None:
    """The statement must violate a constraint; the savepoint keeps the test usable."""
    with pytest.raises(IntegrityError), db.begin_nested():
        db.execute(statement)


def test_models_registered_on_metadata():
    assert {
        "academic_years",
        "terms",
        "classes",
        "subjects",
        "class_subjects",
    } <= set(Base.metadata.tables)


def test_models_match_migrations(migrated_engine, monkeypatch):
    # The autouse fixture points DATABASE_URL at a placeholder; Alembic needs the real test DB.
    monkeypatch.setenv(
        "DATABASE_URL", migrated_engine.url.render_as_string(hide_password=False)
    )
    command.check(Config("alembic.ini"))


def test_valid_rows_are_accepted_with_timestamps(db):
    year_id = add_year(db, is_current=True)
    term_id = add(db, Term, academic_year_id=year_id, name="First Term")
    class_id = add_class(db)
    subject_id = add(db, Subject, name="Mathematics")
    db.execute(insert(ClassSubject).values(class_id=class_id, subject_id=subject_id))
    assert term_id and class_id
    created = db.execute(select(AcademicYear.created_at, AcademicYear.updated_at)).one()
    assert created.created_at is not None and created.updated_at is not None
    assert db.execute(select(func.count()).select_from(ClassSubject)).scalar_one() == 1


def test_academic_year_name_unique(db):
    add_year(db)
    assert_rejected(db, insert(AcademicYear).values(**YEAR))


@pytest.mark.parametrize(
    ("start", "end"),
    [(date(2027, 7, 31), date(2026, 9, 1)), (date(2026, 9, 1), date(2026, 9, 1))],
)
def test_academic_year_end_must_be_after_start(db, start, end):
    assert_rejected(
        db,
        insert(AcademicYear).values(name="2030/2031", start_date=start, end_date=end),
    )


def test_only_one_current_academic_year(db):
    first = add_year(db, is_current=True)
    assert_rejected(
        db,
        insert(AcademicYear).values(
            **{**YEAR, "name": "2027/2028", "is_current": True}
        ),
    )
    # Many non-current years are fine, and the flag can move once it is cleared.
    add_year(db, name="2025/2026")
    add_year(db, name="2024/2025")
    db.execute(
        update(AcademicYear).where(AcademicYear.id == first).values(is_current=False)
    )
    add_year(db, name="2027/2028", is_current=True)


def test_term_unique_per_year_and_allowed_across_years(db):
    year_id = add_year(db)
    other_year_id = add_year(db, name="2027/2028")
    add(db, Term, academic_year_id=year_id, name="First Term")
    assert_rejected(
        db, insert(Term).values(academic_year_id=year_id, name="First Term")
    )
    add(db, Term, academic_year_id=other_year_id, name="First Term")


def test_term_name_must_be_valid(db):
    year_id = add_year(db)
    assert_rejected(
        db, insert(Term).values(academic_year_id=year_id, name="Fourth Term")
    )


def test_term_dates_optional_but_ordered(db):
    year_id = add_year(db)
    add(db, Term, academic_year_id=year_id, name="First Term")
    assert_rejected(
        db,
        insert(Term).values(
            academic_year_id=year_id,
            name="Second Term",
            start_date=date(2027, 1, 10),
            end_date=date(2026, 12, 1),
        ),
    )


def test_term_requires_existing_year(db):
    assert_rejected(db, insert(Term).values(academic_year_id=999999, name="First Term"))


def test_class_name_unique_and_level_valid(db):
    add_class(db)
    assert_rejected(
        db, insert(SchoolClass).values(name="JSS 1", level="junior_secondary")
    )
    assert_rejected(db, insert(SchoolClass).values(name="Year 9", level="middle"))


def test_subject_name_unique(db):
    add(db, Subject, name="Mathematics")
    assert_rejected(db, insert(Subject).values(name="Mathematics"))


def test_class_subject_pair_unique_and_references_checked(db):
    class_id = add_class(db)
    subject_id = add(db, Subject, name="Mathematics")
    db.execute(insert(ClassSubject).values(class_id=class_id, subject_id=subject_id))
    assert_rejected(
        db, insert(ClassSubject).values(class_id=class_id, subject_id=subject_id)
    )
    assert_rejected(
        db, insert(ClassSubject).values(class_id=999999, subject_id=subject_id)
    )
    assert_rejected(
        db, insert(ClassSubject).values(class_id=class_id, subject_id=999999)
    )


def test_referenced_rows_cannot_be_deleted(db):
    year_id = add_year(db)
    add(db, Term, academic_year_id=year_id, name="First Term")
    class_id = add_class(db)
    subject_id = add(db, Subject, name="Mathematics")
    db.execute(insert(ClassSubject).values(class_id=class_id, subject_id=subject_id))
    assert_rejected(db, delete(AcademicYear).where(AcademicYear.id == year_id))
    assert_rejected(db, delete(SchoolClass).where(SchoolClass.id == class_id))
    assert_rejected(db, delete(Subject).where(Subject.id == subject_id))
