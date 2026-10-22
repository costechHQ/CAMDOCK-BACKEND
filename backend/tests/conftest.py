import os

import pytest
from fastapi.testclient import TestClient

from app.core.config import get_settings
from app.db import session as session_module
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
