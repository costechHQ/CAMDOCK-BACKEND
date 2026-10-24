"""Idempotent reference data seed: `uv run python -m app.db.seed --help`.

Loads classes, subjects, the class-subject mapping, one academic year and its three
terms. Every insert is `ON CONFLICT DO NOTHING` on a natural key, so re-running adds
nothing and never overwrites rows an administrator has edited. No users, students,
results or fees are created. Subjects and the mapping are the SRS 2.4 prototype
defaults, not the school's confirmed curriculum.
"""

import argparse
from collections.abc import Sequence
from datetime import date

from sqlalchemy import Connection, literal, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.db.session import get_session_factory
from app.models import AcademicYear, ClassSubject, SchoolClass, Subject, Term
from app.models.academic import TERM_NAMES

CLASSES: tuple[tuple[str, str], ...] = (
    ("Nursery 1", "nursery"),
    ("Nursery 2", "nursery"),
    *((f"Primary {n}", "primary") for n in range(1, 7)),
    *((f"JSS {n}", "junior_secondary") for n in range(1, 4)),
    *((f"SSS {n}", "senior_secondary") for n in range(1, 4)),
)

EARLY_SUBJECTS = (
    "Mathematics",
    "English Language",
    "Basic Science",
    "Social Studies",
    "Quantitative Reasoning",
)
SECONDARY_SUBJECTS = (
    "Mathematics",
    "English Language",
    "Basic Science",
    "Civic Education",
    "Agricultural Science",
)
SUBJECTS_BY_LEVEL: dict[str, tuple[str, ...]] = {
    "nursery": EARLY_SUBJECTS,
    "primary": EARLY_SUBJECTS,
    "junior_secondary": SECONDARY_SUBJECTS,
    "senior_secondary": SECONDARY_SUBJECTS,
}
SUBJECTS: tuple[str, ...] = tuple(dict.fromkeys(EARLY_SUBJECTS + SECONDARY_SUBJECTS))


def _count_inserted(db: Connection | Session, statement) -> int:
    # rowcount is unreliable for multi-row inserts, so count the RETURNING rows.
    return len(db.execute(statement.returning(literal(1))).all())


def _id_by_name(db: Connection | Session, model, name: str) -> int:
    return db.execute(select(model.id).where(model.name == name)).scalar_one()


def seed_reference_data(
    db: Connection | Session,
    *,
    academic_year: str,
    start_date: date,
    end_date: date,
    is_current: bool = False,
) -> dict[str, tuple[int, int]]:
    """Seed reference data; returns `{table: (inserted, already_present)}`.

    Does not commit: the caller owns the transaction.
    """
    if end_date <= start_date:
        raise ValueError("end date must be after start date")

    inserted: dict[str, int] = {}

    inserted["classes"] = _count_inserted(
        db,
        insert(SchoolClass)
        .values([{"name": name, "level": level} for name, level in CLASSES])
        .on_conflict_do_nothing(index_elements=["name"]),
    )
    inserted["subjects"] = _count_inserted(
        db,
        insert(Subject)
        .values([{"name": name} for name in SUBJECTS])
        .on_conflict_do_nothing(index_elements=["name"]),
    )

    class_ids = {name: _id_by_name(db, SchoolClass, name) for name, _ in CLASSES}
    subject_ids = {name: _id_by_name(db, Subject, name) for name in SUBJECTS}
    inserted["class_subjects"] = _count_inserted(
        db,
        insert(ClassSubject)
        .values(
            [
                {"class_id": class_ids[name], "subject_id": subject_ids[subject]}
                for name, level in CLASSES
                for subject in SUBJECTS_BY_LEVEL[level]
            ]
        )
        .on_conflict_do_nothing(),
    )

    inserted["academic_years"] = _count_inserted(
        db,
        insert(AcademicYear)
        .values(
            name=academic_year,
            start_date=start_date,
            end_date=end_date,
            is_current=is_current,
        )
        .on_conflict_do_nothing(index_elements=["name"]),
    )
    year_id = _id_by_name(db, AcademicYear, academic_year)
    inserted["terms"] = _count_inserted(
        db,
        insert(Term)
        .values([{"academic_year_id": year_id, "name": name} for name in TERM_NAMES])
        .on_conflict_do_nothing(constraint="uq_terms_academic_year_id_name"),
    )

    attempted = {
        "classes": len(CLASSES),
        "subjects": len(SUBJECTS),
        "class_subjects": sum(len(SUBJECTS_BY_LEVEL[level]) for _, level in CLASSES),
        "academic_years": 1,
        "terms": len(TERM_NAMES),
    }
    return {
        table: (inserted[table], attempted[table] - inserted[table])
        for table in attempted
    }


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="python -m app.db.seed",
        description="Seed reference data (idempotent). Creates no users or fees.",
    )
    parser.add_argument("--academic-year", required=True, help="e.g. 2026/2027")
    parser.add_argument("--start-date", required=True, type=date.fromisoformat)
    parser.add_argument("--end-date", required=True, type=date.fromisoformat)
    parser.add_argument(
        "--current", action="store_true", help="mark the academic year as current"
    )
    args = parser.parse_args(argv)
    if args.end_date <= args.start_date:
        parser.error("--end-date must be after --start-date")
    return args


def main(argv: Sequence[str] | None = None) -> None:
    args = _parse_args(argv)
    with get_session_factory()() as db, db.begin():
        summary = seed_reference_data(
            db,
            academic_year=args.academic_year,
            start_date=args.start_date,
            end_date=args.end_date,
            is_current=args.current,
        )
    for table, (added, present) in summary.items():
        print(f"{table}: {added} inserted, {present} already present")


if __name__ == "__main__":
    main()
