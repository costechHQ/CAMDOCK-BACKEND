from datetime import date

import pytest
from sqlalchemy import func, select, update

from app.db import seed
from app.models import (
    AcademicYear,
    ClassSubject,
    FeeRecord,
    SchoolClass,
    Student,
    Subject,
    Term,
    User,
)
from app.models.academic import CLASS_LEVELS, TERM_NAMES

YEAR = {
    "academic_year": "2026/2027",
    "start_date": date(2026, 9, 1),
    "end_date": date(2027, 7, 31),
}


def count(db, model) -> int:
    return db.execute(select(func.count()).select_from(model)).scalar_one()


def test_class_data():
    names = [name for name, _ in seed.CLASSES]
    assert len(names) == 14
    assert len(set(names)) == 14
    assert {level for _, level in seed.CLASSES} <= set(CLASS_LEVELS)
    assert names[0] == "Nursery 1"
    assert names[-1] == "SSS 3"


def test_subject_data():
    assert len(seed.SUBJECTS) == 7
    assert all(len(subjects) == 5 for subjects in seed.SUBJECTS_BY_LEVEL.values())
    assert set(seed.SUBJECTS_BY_LEVEL) == {level for _, level in seed.CLASSES}
    assert len(TERM_NAMES) == 3


def test_seed_creates_expected_rows(db):
    summary = seed.seed_reference_data(db, **YEAR)

    assert summary == {
        "classes": (14, 0),
        "subjects": (7, 0),
        "class_subjects": (70, 0),
        "academic_years": (1, 0),
        "terms": (3, 0),
    }
    assert count(db, SchoolClass) == 14
    assert count(db, Subject) == 7
    assert count(db, ClassSubject) == 70
    assert count(db, AcademicYear) == 1
    assert count(db, Term) == 3
    assert db.execute(select(AcademicYear.is_current)).scalar_one() is False


def test_seed_is_idempotent(db):
    seed.seed_reference_data(db, **YEAR)
    second = seed.seed_reference_data(db, **YEAR)

    assert second == {
        "classes": (0, 14),
        "subjects": (0, 7),
        "class_subjects": (0, 70),
        "academic_years": (0, 1),
        "terms": (0, 3),
    }
    assert count(db, SchoolClass) == 14
    assert count(db, ClassSubject) == 70
    assert count(db, Term) == 3


def test_seed_preserves_admin_edits(db):
    seed.seed_reference_data(db, **YEAR)
    db.execute(
        update(Subject).where(Subject.name == "Mathematics").values(is_active=False)
    )
    db.execute(
        update(SchoolClass).where(SchoolClass.name == "JSS 1").values(is_active=False)
    )

    seed.seed_reference_data(db, **YEAR)

    maths = db.execute(select(Subject.is_active).where(Subject.name == "Mathematics"))
    jss1 = db.execute(select(SchoolClass.is_active).where(SchoolClass.name == "JSS 1"))
    assert maths.scalar_one() is False
    assert jss1.scalar_one() is False


def test_seed_marks_year_current_on_request(db):
    seed.seed_reference_data(db, **YEAR, is_current=True)

    assert db.execute(select(AcademicYear.is_current)).scalar_one() is True


def test_seed_creates_no_people_or_fees(db):
    seed.seed_reference_data(db, **YEAR)

    assert count(db, User) == 0
    assert count(db, Student) == 0
    assert count(db, FeeRecord) == 0


def test_bad_date_range_inserts_nothing(db):
    with pytest.raises(ValueError):
        seed.seed_reference_data(
            db,
            academic_year="2026/2027",
            start_date=date(2027, 7, 31),
            end_date=date(2026, 9, 1),
        )

    assert count(db, SchoolClass) == 0
    assert count(db, AcademicYear) == 0


def test_cli_rejects_bad_date_range(capsys):
    with pytest.raises(SystemExit):
        seed.main(
            [
                "--academic-year",
                "2026/2027",
                "--start-date",
                "2027-07-31",
                "--end-date",
                "2026-09-01",
            ]
        )

    assert "--end-date must be after --start-date" in capsys.readouterr().err
