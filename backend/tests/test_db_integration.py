import os

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text

TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL")

pytestmark = pytest.mark.skipif(
    not TEST_DATABASE_URL, reason="TEST_DATABASE_URL not set; needs a Postgres test DB"
)


@pytest.fixture(autouse=True)
def database_env(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", TEST_DATABASE_URL)


def test_database_connection():
    engine = create_engine(TEST_DATABASE_URL)
    with engine.connect() as conn:
        assert conn.execute(text("SELECT 1")).scalar() == 1
    engine.dispose()


def test_alembic_upgrade_head_on_empty_database():
    command.upgrade(Config("alembic.ini"), "head")
