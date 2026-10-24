import os

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db import session as session_module
from app.db.session import get_db
from app.main import create_app
from tests.support import assert_safe_test_database

# Harmless placeholder: the engine is lazy, so tests using it never connect.
DUMMY_DATABASE_URL = "postgresql+psycopg://test:test@localhost:5432/camdock_test"

CACHED = (
    get_settings,
    session_module.get_engine,
    session_module.get_session_factory,
)


def pytest_configure(config):
    url = os.environ.get("TEST_DATABASE_URL")
    if url:
        try:
            assert_safe_test_database(url)
        except ValueError as exc:
            raise pytest.UsageError(str(exc)) from None


def clear_caches() -> None:
    for cached in CACHED:
        cached.cache_clear()


@pytest.fixture(autouse=True)
def isolated_settings(monkeypatch):
    """Give every test a test-only configuration and empty settings caches."""
    monkeypatch.setenv("ENVIRONMENT", "test")
    monkeypatch.setenv("DATABASE_URL", DUMMY_DATABASE_URL)
    monkeypatch.setenv("API_V1_PREFIX", "/api/v1")
    clear_caches()
    yield
    clear_caches()


@pytest.fixture
def make_client(monkeypatch):
    """Build an app and client for the environment, using the current DATABASE_URL."""

    def _make(
        environment: str = "test", raise_server_exceptions: bool = False
    ) -> TestClient:
        monkeypatch.setenv("ENVIRONMENT", environment)
        get_settings.cache_clear()
        return TestClient(create_app(), raise_server_exceptions=raise_server_exceptions)

    return _make


@pytest.fixture(scope="session")
def migrated_engine():
    """Engine for the guarded `*_test` database, rebuilt from migrations once per run."""
    url = os.environ.get("TEST_DATABASE_URL")
    if not url:
        pytest.skip("TEST_DATABASE_URL not set; needs a Postgres test DB")
    with pytest.MonkeyPatch.context() as mp:
        mp.setenv("DATABASE_URL", url)
        get_settings.cache_clear()
        config = Config("alembic.ini")
        command.downgrade(config, "base")
        command.upgrade(config, "head")
        engine = create_engine(url)
        yield engine
        engine.dispose()
        get_settings.cache_clear()


@pytest.fixture
def db(migrated_engine):
    """A connection inside a transaction that is always rolled back."""
    connection = migrated_engine.connect()
    transaction = connection.begin()
    yield connection
    transaction.rollback()
    connection.close()


@pytest.fixture
def api_client(db, make_client):
    """Client whose requests use their own Session inside the rolled-back `db`
    transaction, so endpoint commits only release a savepoint."""

    def override_get_db():
        session = Session(
            bind=db,
            join_transaction_mode="create_savepoint",
            autoflush=False,
            expire_on_commit=False,
        )
        try:
            yield session
        finally:
            session.close()

    client = make_client()
    client.app.dependency_overrides[get_db] = override_get_db
    return client
