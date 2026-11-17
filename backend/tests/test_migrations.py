"""Migration chain checks on a clean database.

These tests change the schema, so they run in a scratch database created on the same
server as TEST_DATABASE_URL (name ends in `_test`, dropped afterwards) and never touch
the shared test database.
"""

import os
from datetime import date

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError

import app.models  # noqa: F401  (registers every model on Base.metadata)
from app.core.config import get_settings
from app.db import seed
from app.db.base import Base
from tests.support import assert_safe_test_database

TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL")
SCRATCH_DATABASE = "camdock_migrations_test"
REVISIONS = [
    "0001",
    "0002",
    "0003",
    "0004",
    "0005",
    "0006",
    "0007",
    "0008",
    "0009",
    "0010",
    "0011",
    "0012",
    "0013",
    "0014",
]

pytestmark = pytest.mark.skipif(
    not TEST_DATABASE_URL, reason="TEST_DATABASE_URL not set; needs a Postgres test DB"
)


def alembic_config() -> Config:
    return Config("alembic.ini")


@pytest.fixture(scope="module")
def scratch_url():
    """Create the scratch database on the test server; drop it when done."""
    scratch = make_url(TEST_DATABASE_URL).set(database=SCRATCH_DATABASE)
    assert_safe_test_database(scratch.render_as_string(hide_password=False))
    admin = create_engine(TEST_DATABASE_URL, isolation_level="AUTOCOMMIT")
    try:
        with admin.connect() as conn:
            conn.execute(
                text(f'DROP DATABASE IF EXISTS "{SCRATCH_DATABASE}" WITH (FORCE)')
            )
            conn.execute(text(f'CREATE DATABASE "{SCRATCH_DATABASE}"'))
    except DBAPIError:
        admin.dispose()
        pytest.skip("test database user cannot create the scratch database")
    yield scratch.render_as_string(hide_password=False)
    with admin.connect() as conn:
        conn.execute(text(f'DROP DATABASE IF EXISTS "{SCRATCH_DATABASE}" WITH (FORCE)'))
    admin.dispose()


@pytest.fixture
def clean_engine(scratch_url, monkeypatch):
    """Engine for the scratch database with an empty `public` schema."""
    engine = create_engine(scratch_url)
    with engine.begin() as conn:
        conn.execute(text("DROP SCHEMA public CASCADE"))
        conn.execute(text("CREATE SCHEMA public"))
    monkeypatch.setenv("DATABASE_URL", scratch_url)
    get_settings.cache_clear()
    yield engine
    engine.dispose()


def current_revision(engine) -> str | None:
    with engine.connect() as conn:
        return MigrationContext.configure(conn).get_current_revision()


def table_names(engine) -> set[str]:
    return set(inspect(engine).get_table_names())


def test_history_is_linear_and_complete():
    script = ScriptDirectory.from_config(alembic_config())

    assert script.get_heads() == [REVISIONS[-1]]
    chain = [rev.revision for rev in script.walk_revisions()]
    assert chain == list(reversed(REVISIONS))


def test_upgrade_head_on_clean_database(clean_engine):
    assert table_names(clean_engine) == set()

    command.upgrade(alembic_config(), "head")

    assert current_revision(clean_engine) == REVISIONS[-1]
    assert table_names(clean_engine) == set(Base.metadata.tables) | {"alembic_version"}
    with clean_engine.connect() as conn:
        triggers = conn.execute(
            text("SELECT tgname FROM pg_trigger WHERE NOT tgisinternal")
        ).scalars()
        assert set(triggers) == {
            "audit_logs_append_only",
            "fee_payments_append_only",
            "fee_refunds_append_only",
            "fee_refunds_limit",
        }


def test_student_number_sequence_is_created_and_dropped(clean_engine):
    config = alembic_config()
    query = text(
        "SELECT sequencename FROM pg_sequences "
        "WHERE sequencename = 'student_number_seq'"
    )

    command.upgrade(config, "head")
    with clean_engine.connect() as conn:
        assert conn.execute(query).scalars().all() == ["student_number_seq"]

    command.downgrade(config, "0007")
    with clean_engine.connect() as conn:
        assert conn.execute(query).scalars().all() == []


def test_results_term_class_index_is_created_and_dropped(clean_engine):
    config = alembic_config()
    query = text(
        "SELECT indexdef FROM pg_indexes "
        "WHERE tablename = 'results' AND indexname = 'ix_results_term_id_class_id'"
    )

    command.upgrade(config, "head")
    with clean_engine.connect() as conn:
        (definition,) = conn.execute(query).scalars().all()
    assert "(term_id, class_id)" in definition

    command.downgrade(config, "0008")
    with clean_engine.connect() as conn:
        assert conn.execute(query).scalars().all() == []


def test_fee_numbering_and_uniqueness_are_created_and_dropped(clean_engine):
    config = alembic_config()
    sequences = text(
        "SELECT sequencename FROM pg_sequences "
        "WHERE sequencename IN ('fee_record_number_seq', 'fee_payment_number_seq') "
        "ORDER BY sequencename"
    )
    constraint = text(
        "SELECT conname FROM pg_constraint "
        "WHERE conname = 'uq_fee_records_student_id_term_id_fee_type'"
    )
    index = text(
        "SELECT indexname FROM pg_indexes "
        "WHERE indexname = 'ix_fee_payments_fee_record_id'"
    )

    command.upgrade(config, "head")
    with clean_engine.connect() as conn:
        assert conn.execute(sequences).scalars().all() == [
            "fee_payment_number_seq",
            "fee_record_number_seq",
        ]
        assert conn.execute(constraint).scalars().all() == [
            "uq_fee_records_student_id_term_id_fee_type"
        ]
        assert conn.execute(index).scalars().all() == ["ix_fee_payments_fee_record_id"]

    command.downgrade(config, "0009")
    with clean_engine.connect() as conn:
        assert conn.execute(sequences).scalars().all() == []
        assert conn.execute(constraint).scalars().all() == []
        assert conn.execute(index).scalars().all() == []


def test_payment_idempotency_key_is_created_and_dropped(clean_engine):
    config = alembic_config()
    column = text(
        "SELECT is_nullable FROM information_schema.columns "
        "WHERE table_name = 'fee_payments' AND column_name = 'idempotency_key'"
    )
    constraint = text(
        "SELECT conname FROM pg_constraint "
        "WHERE conname = 'uq_fee_payments_created_by_idempotency_key'"
    )

    command.upgrade(config, "head")
    with clean_engine.connect() as conn:
        assert conn.execute(column).scalars().all() == ["YES"]
        assert conn.execute(constraint).scalars().all() == [
            "uq_fee_payments_created_by_idempotency_key"
        ]

    command.downgrade(config, "0010")
    with clean_engine.connect() as conn:
        assert conn.execute(column).scalars().all() == []
        assert conn.execute(constraint).scalars().all() == []


def test_password_resets_are_created_and_dropped(clean_engine):
    config = alembic_config()
    table = text("SELECT to_regclass('public.password_resets')")
    index = text(
        "SELECT indexdef FROM pg_indexes "
        "WHERE indexname = 'uq_password_resets_live_per_user'"
    )

    command.upgrade(config, "head")
    with clean_engine.connect() as conn:
        assert conn.execute(table).scalar() == "password_resets"
        definition = conn.execute(index).scalar_one()
    assert "UNIQUE" in definition
    assert "used_at IS NULL" in definition and "voided_at IS NULL" in definition

    command.downgrade(config, "0011")
    with clean_engine.connect() as conn:
        assert conn.execute(table).scalar() is None
        assert conn.execute(index).scalars().all() == []


def seed_a_payment(engine) -> int:
    """One payment on one bill, through the models; returns the payment id."""
    from datetime import date
    from decimal import Decimal

    from sqlalchemy import insert

    from app.models import (
        AcademicYear,
        FeePayment,
        FeeRecord,
        SchoolClass,
        Student,
        Term,
        User,
    )

    with engine.begin() as conn:

        def add(model, **values) -> int:
            return conn.execute(
                insert(model).values(**values).returning(model.id)
            ).scalar_one()

        admin = add(User, email="a@camdock.test", password_hash="x", role="admin")
        year = add(
            AcademicYear,
            name="2026/2027",
            start_date=date(2026, 9, 1),
            end_date=date(2027, 7, 31),
        )
        klass = add(SchoolClass, name="Primary 1", level="primary")
        student = add(Student, student_number="S-1", full_name="Ada", class_id=klass)
        term = add(Term, academic_year_id=year, name="First Term")
        fee = add(
            FeeRecord,
            student_id=student,
            term_id=term,
            fee_type="Tuition Fee",
            amount_billed=Decimal("100.00"),
            receipt_number="FEE-T-1",
            created_by=admin,
        )
        return add(
            FeePayment,
            fee_record_id=fee,
            amount=Decimal("40.00"),
            receipt_number="RCP-T-1",
            created_by=admin,
        )


def test_payment_void_is_created_guarded_and_dropped(clean_engine):
    config = alembic_config()
    columns = text(
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_name = 'fee_payments' AND column_name LIKE 'void%' "
        "ORDER BY column_name"
    )
    trigger = text(
        "SELECT tgname FROM pg_trigger "
        "WHERE tgrelid = 'fee_payments'::regclass AND NOT tgisinternal"
    )
    checks = text(
        "SELECT conname FROM pg_constraint WHERE conrelid = 'fee_payments'::regclass "
        "AND conname LIKE 'ck_fee_payments_void%' ORDER BY conname"
    )

    command.upgrade(config, "head")
    with clean_engine.connect() as conn:
        assert conn.execute(columns).scalars().all() == [
            "void_reason",
            "voided_at",
            "voided_by",
        ]
        assert conn.execute(trigger).scalars().all() == ["fee_payments_append_only"]
        assert conn.execute(checks).scalars().all() == [
            "ck_fee_payments_void_fields_together",
            "ck_fee_payments_void_reason_length",
        ]

    command.downgrade(config, "0012")
    with clean_engine.connect() as conn:
        assert conn.execute(columns).scalars().all() == []
        assert conn.execute(trigger).scalars().all() == []
        assert conn.execute(checks).scalars().all() == []


def test_downgrade_keeps_the_data_and_the_payment_figures(clean_engine):
    config = alembic_config()
    command.upgrade(config, "head")
    payment_id = seed_a_payment(clean_engine)

    command.downgrade(config, "0012")

    with clean_engine.connect() as conn:
        row = conn.execute(
            text("SELECT amount, receipt_number FROM fee_payments WHERE id = :id"),
            {"id": payment_id},
        ).one()
    assert (str(row.amount), row.receipt_number) == ("40.00", "RCP-T-1")


def test_downgrade_refuses_while_voided_payments_exist(clean_engine):
    config = alembic_config()
    command.upgrade(config, "head")
    payment_id = seed_a_payment(clean_engine)
    with clean_engine.begin() as conn:
        conn.execute(
            text(
                "UPDATE fee_payments SET voided_at = now(), "
                "voided_by = (SELECT id FROM users LIMIT 1), "
                "void_reason = 'entered in error' WHERE id = :id"
            ),
            {"id": payment_id},
        )

    with pytest.raises(Exception, match="voided payments exist"):
        command.downgrade(config, "0012")

    assert current_revision(clean_engine) == "0014"
    with clean_engine.connect() as conn:
        assert conn.execute(
            text("SELECT voided_at IS NOT NULL FROM fee_payments WHERE id = :id"),
            {"id": payment_id},
        ).scalar()


def add_a_refund(engine, payment_id: int) -> None:
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO fee_refunds (fee_payment_id, fee_record_id, amount, reason, "
                "receipt_number, created_by) SELECT id, fee_record_id, 10, "
                "'a long enough reason', 'REF-T-1', created_by "
                "FROM fee_payments WHERE id = :id"
            ),
            {"id": payment_id},
        )


def test_payment_refunds_are_created_guarded_and_dropped(clean_engine):
    config = alembic_config()
    table = text("SELECT to_regclass('public.fee_refunds')")
    sequence = text("SELECT to_regclass('public.fee_refund_number_seq')")
    triggers = text(
        "SELECT tgname FROM pg_trigger "
        "WHERE tgrelid = 'fee_refunds'::regclass AND NOT tgisinternal ORDER BY tgname"
    )
    pair = text(
        "SELECT conname FROM pg_constraint "
        "WHERE conname = 'uq_fee_payments_id_fee_record_id'"
    )
    guard = text("SELECT pg_get_functiondef('fee_payments_guard'::regproc)")

    command.upgrade(config, "head")
    with clean_engine.connect() as conn:
        assert conn.execute(table).scalar() == "fee_refunds"
        assert conn.execute(sequence).scalar() == "fee_refund_number_seq"
        assert conn.execute(triggers).scalars().all() == [
            "fee_refunds_append_only",
            "fee_refunds_limit",
        ]
        assert conn.execute(pair).scalars().all() == [
            "uq_fee_payments_id_fee_record_id"
        ]
        assert "fee_refunds" in conn.execute(guard).scalar()

    command.downgrade(config, "0013")
    with clean_engine.connect() as conn:
        assert conn.execute(table).scalar() is None
        assert conn.execute(sequence).scalar() is None
        assert conn.execute(pair).scalars().all() == []
        # the payment guard is back to its revision 0013 form
        assert "fee_refunds" not in conn.execute(guard).scalar()
        assert "cannot be deleted" in conn.execute(guard).scalar()


def test_downgrade_refuses_while_refunds_exist(clean_engine):
    config = alembic_config()
    command.upgrade(config, "head")
    payment_id = seed_a_payment(clean_engine)
    add_a_refund(clean_engine, payment_id)

    with pytest.raises(Exception, match="refunds exist"):
        command.downgrade(config, "0013")

    assert current_revision(clean_engine) == "0014"
    with clean_engine.connect() as conn:
        assert conn.execute(text("SELECT count(*) FROM fee_refunds")).scalar() == 1


def test_downgrade_without_refunds_keeps_the_payment(clean_engine):
    config = alembic_config()
    command.upgrade(config, "head")
    payment_id = seed_a_payment(clean_engine)

    command.downgrade(config, "0013")

    with clean_engine.connect() as conn:
        row = conn.execute(
            text("SELECT amount, receipt_number FROM fee_payments WHERE id = :id"),
            {"id": payment_id},
        ).one()
    assert (str(row.amount), row.receipt_number) == ("40.00", "RCP-T-1")


def test_migrated_schema_matches_models(clean_engine):
    command.upgrade(alembic_config(), "head")

    with clean_engine.connect() as conn:
        diffs = compare_metadata(MigrationContext.configure(conn), Base.metadata)

    assert diffs == []


def test_step_by_step_round_trip(clean_engine):
    config = alembic_config()
    command.upgrade(config, "head")

    for previous in reversed(REVISIONS[:-1]):
        command.downgrade(config, previous)
        assert current_revision(clean_engine) == previous
    command.downgrade(config, "base")
    assert current_revision(clean_engine) is None
    assert table_names(clean_engine) == {"alembic_version"}

    for revision in REVISIONS:
        command.upgrade(config, revision)
        assert current_revision(clean_engine) == revision
    assert table_names(clean_engine) == set(Base.metadata.tables) | {"alembic_version"}


def test_repeat_upgrade_is_a_no_op(clean_engine):
    config = alembic_config()
    command.upgrade(config, "head")
    before = table_names(clean_engine)

    command.upgrade(config, "head")

    assert current_revision(clean_engine) == REVISIONS[-1]
    assert table_names(clean_engine) == before


def test_seed_runs_on_freshly_migrated_database(clean_engine):
    command.upgrade(alembic_config(), "head")
    year = {
        "academic_year": "2026/2027",
        "start_date": date(2026, 9, 1),
        "end_date": date(2027, 7, 31),
    }

    with clean_engine.begin() as conn:
        first = seed.seed_reference_data(conn, **year)
    with clean_engine.begin() as conn:
        second = seed.seed_reference_data(conn, **year)

    assert {table: added for table, (added, _) in first.items()} == {
        "classes": 14,
        "subjects": 7,
        "class_subjects": 70,
        "academic_years": 1,
        "terms": 3,
    }
    assert all(added == 0 for added, _ in second.values())
