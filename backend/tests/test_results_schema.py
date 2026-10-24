from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import delete, insert, select
from sqlalchemy.exc import IntegrityError

from app.db.base import Base
from app.models import (
    AcademicYear,
    Result,
    SchoolClass,
    Student,
    Subject,
    Term,
    User,
)
from tests.support import assert_rejected


def add(db, model, **values) -> int:
    return db.execute(insert(model).values(**values).returning(model.id)).scalar_one()


@pytest.fixture
def ctx(db) -> dict[str, int]:
    """One of each referenced row, ready for a result to point at."""
    year_id = add(
        db,
        AcademicYear,
        name="2026/2027",
        start_date=date(2026, 9, 1),
        end_date=date(2027, 7, 31),
    )
    class_id = add(db, SchoolClass, name="JSS 1", level="junior_secondary")
    return {
        "student_id": add(
            db, Student, student_number="CAM/001", full_name="Ngozi", class_id=class_id
        ),
        "subject_id": add(db, Subject, name="Mathematics"),
        "term_id": add(db, Term, academic_year_id=year_id, name="First Term"),
        "class_id": class_id,
        "created_by": add(
            db, User, email="t@camdock.test", password_hash="hash", role="teacher"
        ),
    }


def add_result(db, ctx, score="70", **overrides) -> int:
    return add(db, Result, **{**ctx, "score": Decimal(score), **overrides})


def test_models_registered_on_metadata():
    assert "results" in Base.metadata.tables


def test_valid_result_defaults(db, ctx):
    result_id = add_result(db, ctx, "74.5")
    row = db.execute(select(Result).where(Result.id == result_id)).one()
    assert row.score == Decimal("74.50")
    assert row.updated_by is None
    assert row.created_at is not None and row.updated_at is not None


@pytest.mark.parametrize("score", ["0", "100", "74.5", "99.99"])
def test_scores_inside_range_accepted(db, ctx, score):
    add_result(db, ctx, score)


@pytest.mark.parametrize("score", ["-0.01", "100.01", "101"])
def test_scores_outside_range_rejected(db, ctx, score):
    assert_rejected(
        db,
        insert(Result).values(**ctx, score=Decimal(score)),
        "ck_results_score_range",
    )


def test_missing_score_is_not_stored_as_zero(db, ctx):
    # Missing means no row, so a NULL score must be refused (not coerced to 0).
    with pytest.raises(IntegrityError) as excinfo, db.begin_nested():
        db.execute(insert(Result).values(**ctx, score=None))
    assert excinfo.value.orig.diag.column_name == "score"


def test_one_score_per_student_subject_term(db, ctx):
    add_result(db, ctx)
    assert_rejected(
        db,
        insert(Result).values(**ctx, score=Decimal(80)),
        "uq_results_student_subject_term",
    )


def test_same_subject_allowed_in_another_term(db, ctx):
    add_result(db, ctx)
    year_id = db.execute(select(AcademicYear.id)).scalar_one()
    other_term = add(db, Term, academic_year_id=year_id, name="Second Term")
    add_result(db, {**ctx, "term_id": other_term})


@pytest.mark.parametrize(
    ("column", "constraint"),
    [
        ("student_id", "fk_results_student_id_students"),
        ("subject_id", "fk_results_subject_id_subjects"),
        ("term_id", "fk_results_term_id_terms"),
        ("class_id", "fk_results_class_id_classes"),
        ("created_by", "fk_results_created_by_users"),
    ],
)
def test_references_must_exist(db, ctx, column, constraint):
    assert_rejected(
        db,
        insert(Result).values(**{**ctx, column: 999999}, score=Decimal(50)),
        constraint,
    )


def test_updated_by_must_exist_when_set(db, ctx):
    assert_rejected(
        db,
        insert(Result).values(**ctx, score=Decimal(50), updated_by=999999),
        "fk_results_updated_by_users",
    )


@pytest.mark.parametrize(
    ("model", "key", "constraint"),
    [
        (Student, "student_id", "fk_results_student_id_students"),
        (Subject, "subject_id", "fk_results_subject_id_subjects"),
        (Term, "term_id", "fk_results_term_id_terms"),
        # The student also sits in this class, so either foreign key may report first.
        (
            SchoolClass,
            "class_id",
            ("fk_results_class_id_classes", "fk_students_class_id_classes"),
        ),
        (User, "created_by", "fk_results_created_by_users"),
    ],
)
def test_referenced_rows_cannot_be_deleted(db, ctx, model, key, constraint):
    add_result(db, ctx)
    assert_rejected(db, delete(model).where(model.id == ctx[key]), constraint)


def test_subject_class_membership_is_not_enforced_by_the_database(db, ctx):
    # Known gap, by design: no class_subjects row exists for this pair, yet the result
    # is accepted. The service layer must check the subject belongs to the class.
    add_result(db, ctx)
