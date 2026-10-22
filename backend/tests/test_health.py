import logging
import os

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.exc import OperationalError

from app.core.config import get_settings
from app.db import session as session_module
from app.db.session import get_db
from app.main import create_app

DB_URL = "postgresql+psycopg://user:s3cret-pw@localhost:5432/db"
CACHED = (
    get_settings,
    session_module.get_engine,
    session_module.get_session_factory,
)


def reset_caches():
    for cached in CACHED:
        cached.cache_clear()


@pytest.fixture(autouse=True)
def clean_caches():
    reset_caches()
    yield
    reset_caches()


class WorkingSession:
    def execute(self, statement):
        return None


class BrokenSession:
    def execute(self, statement):
        raise OperationalError(
            "SELECT 1", {}, Exception("host=db.internal pw=s3cret-pw")
        )


def client_with_session(monkeypatch, session) -> TestClient:
    monkeypatch.setenv("DATABASE_URL", DB_URL)
    app = create_app()
    app.dependency_overrides[get_db] = lambda: session
    return TestClient(app, raise_server_exceptions=False)


def test_health_ok_when_database_reachable(monkeypatch):
    client = client_with_session(monkeypatch, WorkingSession())
    response = client.get("/api/v1/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "database": "ok"}


def test_health_is_public(monkeypatch):
    client = client_with_session(monkeypatch, WorkingSession())
    assert client.get("/api/v1/health").status_code == 200


def test_health_503_in_standard_shape_without_leaking_details(monkeypatch, caplog):
    client = client_with_session(monkeypatch, BrokenSession())
    with caplog.at_level(logging.ERROR):
        response = client.get("/api/v1/health")
    assert response.status_code == 503
    assert response.json() == {
        "error": {"code": "service_unavailable", "message": "Service unavailable."}
    }
    assert "s3cret-pw" not in response.text
    assert "db.internal" not in response.text
    assert "Health check failed" in caplog.text


def test_health_503_when_database_really_unreachable(monkeypatch):
    # Port 1 on localhost refuses connections, so no real database is needed.
    monkeypatch.setenv(
        "DATABASE_URL", "postgresql+psycopg://user:s3cret-pw@127.0.0.1:1/db"
    )
    client = TestClient(create_app(), raise_server_exceptions=False)
    response = client.get("/api/v1/health")
    assert response.status_code == 503
    assert "s3cret-pw" not in response.text
    assert "127.0.0.1" not in response.text


@pytest.mark.skipif(
    not os.environ.get("TEST_DATABASE_URL"),
    reason="TEST_DATABASE_URL not set; needs a Postgres test DB",
)
def test_health_ok_against_real_postgres(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", os.environ["TEST_DATABASE_URL"])
    client = TestClient(create_app())
    response = client.get("/api/v1/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "database": "ok"}
