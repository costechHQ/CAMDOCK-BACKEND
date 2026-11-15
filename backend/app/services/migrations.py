"""Whether the database schema is at the migration revision this code expects."""

from functools import lru_cache
from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import text
from sqlalchemy.orm import Session

MIGRATIONS = Path(__file__).resolve().parents[2] / "migrations"


@lru_cache
def expected_head() -> str:
    """The newest revision in the migrations folder shipped with this code."""
    config = Config()
    config.set_main_option("script_location", str(MIGRATIONS))
    head = ScriptDirectory.from_config(config).get_current_head()
    if head is None:
        raise RuntimeError("No migrations found.")
    return head


def schema_is_current(db: Session) -> bool:
    """True only when the database has been migrated exactly to the expected head.

    A missing or empty version table raises or compares unequal, so an unmigrated
    database is never reported as ready.
    """
    rows = db.execute(text("SELECT version_num FROM alembic_version")).scalars().all()
    return rows == [expected_head()]
