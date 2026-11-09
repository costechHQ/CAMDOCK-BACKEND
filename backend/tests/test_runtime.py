import logging
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest
from fastapi import FastAPI
from sqlalchemy import text

import app.main as main_module
from app.core.ratelimit import RateLimiter
from app.main import create_app
from app.services import migrations
from tests.test_health import BrokenSession, client_with_session
from tests.test_openapi_contract import load_exporter

BACKEND = Path(__file__).resolve().parent.parent
READY = "/api/v1/health/ready"
GENERIC = {"error": {"code": "service_unavailable", "message": "Service unavailable."}}


# --- `app` for fastapi dev / uvicorn app.main:app ----------------------------------


def test_importing_the_module_needs_no_configuration():
    env = {k: v for k, v in os.environ.items() if k != "DATABASE_URL"}
    code = (
        "import sys; sys.path.insert(0, '.'); import app.main as m; "
        "assert 'app' in dir(m); assert 'create_app' in dir(m)"
    )

    result = subprocess.run(
        [sys.executable, "-c", code],
        check=False,
        cwd=BACKEND,
        env=env,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr


def test_the_app_attribute_is_a_working_application(monkeypatch):
    monkeypatch.setattr(main_module, "_app", None)

    built = main_module.app

    assert isinstance(built, FastAPI)
    assert "/api/v1/health" in built.openapi()["paths"]


def test_the_app_attribute_is_built_once(monkeypatch):
    monkeypatch.setattr(main_module, "_app", None)

    assert main_module.app is main_module.app


def test_other_missing_names_still_raise(monkeypatch):
    with pytest.raises(AttributeError):
        main_module.no_such_name  # noqa: B018


def test_the_factory_still_gives_independent_instances():
    assert create_app() is not create_app()


def test_the_name_app_is_listed_for_tools_that_discover_it():
    assert "app" in dir(main_module)


# --- migration head ----------------------------------------------------------------


def test_the_expected_head_is_the_newest_migration_file():
    versions = sorted(
        p.name for p in (BACKEND / "migrations" / "versions").glob("[0-9]*_*.py")
    )
    newest = re.match(r"(\d+)_", versions[-1]).group(1)

    assert migrations.expected_head() == newest
    assert migrations.expected_head() not in ("", "base")


def test_a_migrated_database_is_current(db_session):
    assert migrations.schema_is_current(db_session) is True


# --- GET /health/ready -------------------------------------------------------------


@pytest.fixture
def db_session(db):
    from sqlalchemy.orm import Session

    session = Session(bind=db, join_transaction_mode="create_savepoint")
    yield session
    session.close()


def test_ready_when_the_schema_is_current(api_client):
    response = api_client.get(READY)

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "database": "ok", "migrations": "ok"}


def test_ready_needs_no_login(api_client):
    assert api_client.get(READY).status_code == 200


def test_not_ready_when_the_schema_is_behind(db, api_client, caplog):
    caplog.set_level(logging.DEBUG, logger="app")
    db.execute(text("UPDATE alembic_version SET version_num = '0001'"))

    response = api_client.get(READY)

    assert response.status_code == 503
    assert response.json() == GENERIC
    assert "0001" not in response.text + caplog.text
    assert migrations.expected_head() not in response.text + caplog.text
    assert "not at the expected revision" in caplog.text


def test_not_ready_when_the_version_table_is_empty(db, api_client):
    db.execute(text("DELETE FROM alembic_version"))

    response = api_client.get(READY)

    assert response.status_code == 503
    assert response.json() == GENERIC


def test_not_ready_when_the_database_was_never_migrated(db, api_client, caplog):
    caplog.set_level(logging.DEBUG, logger="app")
    db.execute(text("DROP TABLE alembic_version"))

    response = api_client.get(READY)

    assert response.status_code == 503
    assert response.json() == GENERIC
    assert "alembic_version" not in response.text + caplog.text
    assert "ProgrammingError" in caplog.text


def test_not_ready_when_the_database_is_down(monkeypatch, make_client, caplog):
    caplog.set_level(logging.DEBUG, logger="app")
    client = client_with_session(monkeypatch, make_client, BrokenSession())

    response = client.get(READY)

    assert response.status_code == 503
    assert response.json() == GENERIC
    for secret in ("s3cret-pw", "db.internal"):
        assert secret not in response.text + caplog.text


def test_a_mismatch_is_not_ready_even_with_extra_rows(db, api_client):
    db.execute(text("INSERT INTO alembic_version (version_num) VALUES ('9999')"))

    assert api_client.get(READY).status_code == 503


def test_the_plain_health_check_is_unchanged(api_client):
    response = api_client.get("/api/v1/health")

    assert response.json() == {"status": "ok", "database": "ok"}


def test_the_plain_health_check_stays_up_when_the_schema_is_behind(db, api_client):
    db.execute(text("UPDATE alembic_version SET version_num = '0001'"))

    assert api_client.get("/api/v1/health").status_code == 200


def test_ready_is_exempt_from_the_rate_ceiling(api_client):
    api_client.app.state.global_limiter = RateLimiter(2)

    statuses = {api_client.get(READY).status_code for _ in range(10)}

    assert statuses == {200}


def test_ready_is_logged_at_debug_only(api_client, caplog):
    caplog.set_level(logging.INFO, logger="app")
    api_client.get(READY)
    assert [r for r in caplog.records if r.name == "app.request"] == []

    caplog.set_level(logging.DEBUG, logger="app")
    api_client.get(READY)
    (line,) = [r for r in caplog.records if r.name == "app.request"]
    assert line.levelno == logging.DEBUG


def test_ready_carries_the_usual_headers(api_client):
    response = api_client.get(READY)

    assert response.headers["x-request-id"]
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["cache-control"] == "no-store"


def test_the_contract_lists_the_readiness_endpoint():
    paths = load_exporter().build_openapi()["paths"]

    assert "get" in paths[READY]
