import logging
import os

import pytest
from sqlalchemy.exc import OperationalError

from app.db.session import get_db

SECRET_DB_URL = "postgresql+psycopg://user:s3cret-pw@localhost:5432/db_test"


class WorkingSession:
    def execute(self, statement):
        return None


class BrokenSession:
    def execute(self, statement):
        raise OperationalError(
            "SELECT 1", {}, Exception("host=db.internal pw=s3cret-pw")
        )


def client_with_session(monkeypatch, make_client, session):
    monkeypatch.setenv("DATABASE_URL", SECRET_DB_URL)
    client = make_client()
    client.app.dependency_overrides[get_db] = lambda: session
    return client


def test_health_ok_when_database_reachable(monkeypatch, make_client):
    client = client_with_session(monkeypatch, make_client, WorkingSession())
    response = client.get("/api/v1/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "database": "ok"}


def test_health_is_public(monkeypatch, make_client):
    client = client_with_session(monkeypatch, make_client, WorkingSession())
    assert client.get("/api/v1/health").status_code == 200


def test_health_503_in_standard_shape_without_leaking_details(
    monkeypatch, make_client, caplog
):
    client = client_with_session(monkeypatch, make_client, BrokenSession())
    with caplog.at_level(logging.ERROR):
        response = client.get("/api/v1/health")
    assert response.status_code == 503
    assert response.json() == {
        "error": {"code": "service_unavailable", "message": "Service unavailable."}
    }
    assert "s3cret-pw" not in response.text
    assert "db.internal" not in response.text
    assert "Health check failed" in caplog.text


def test_health_503_when_database_really_unreachable(monkeypatch, make_client):
    # Port 1 on localhost refuses connections, so no real database is needed.
    monkeypatch.setenv(
        "DATABASE_URL", "postgresql+psycopg://user:s3cret-pw@127.0.0.1:1/db_test"
    )
    response = make_client().get("/api/v1/health")
    assert response.status_code == 503
    assert "s3cret-pw" not in response.text
    assert "127.0.0.1" not in response.text


@pytest.mark.skipif(
    not os.environ.get("TEST_DATABASE_URL"),
    reason="TEST_DATABASE_URL not set; needs a Postgres test DB",
)
def test_health_ok_against_real_postgres(monkeypatch, make_client):
    monkeypatch.setenv("DATABASE_URL", os.environ["TEST_DATABASE_URL"])
    response = make_client(raise_server_exceptions=True).get("/api/v1/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "database": "ok"}
