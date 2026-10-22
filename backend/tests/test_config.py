import pytest
from pydantic import ValidationError

from app.core.config import Settings

DB_URL = "postgresql+psycopg://user:s3cret-pw@localhost:5432/db"


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for name in ("ENVIRONMENT", "DATABASE_URL", "API_V1_PREFIX"):
        monkeypatch.delenv(name, raising=False)


def make_settings() -> Settings:
    return Settings(_env_file=None)


def test_loads_from_environment(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", DB_URL)
    monkeypatch.setenv("ENVIRONMENT", "test")
    settings = make_settings()
    assert settings.environment == "test"
    assert settings.database_url.get_secret_value() == DB_URL
    assert settings.api_v1_prefix == "/api/v1"


def test_missing_database_url_fails():
    with pytest.raises(ValidationError) as exc:
        make_settings()
    assert "database_url" in str(exc.value)


def test_invalid_environment_rejected(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", DB_URL)
    monkeypatch.setenv("ENVIRONMENT", "staging")
    with pytest.raises(ValidationError) as exc:
        make_settings()
    assert "environment" in str(exc.value)


@pytest.mark.parametrize(
    ("environment", "expected"),
    [("development", True), ("test", False), ("production", False)],
)
def test_docs_enabled_only_in_development(monkeypatch, environment, expected):
    monkeypatch.setenv("DATABASE_URL", DB_URL)
    monkeypatch.setenv("ENVIRONMENT", environment)
    assert make_settings().docs_enabled is expected


def test_secret_not_exposed_in_errors_or_repr(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", DB_URL)
    assert "s3cret-pw" not in repr(make_settings())
    monkeypatch.setenv("ENVIRONMENT", "staging")
    with pytest.raises(ValidationError) as exc:
        make_settings()
    assert "s3cret-pw" not in str(exc.value)
