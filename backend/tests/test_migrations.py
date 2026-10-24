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
REVISIONS = ["0001", "0002", "0003", "0004", "0005", "0006", "0007"]

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
        assert set(triggers) == {"audit_logs_append_only"}


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
