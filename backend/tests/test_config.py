import pytest
from pydantic import ValidationError

from app.core.config import Settings

DB_URL = "postgresql+psycopg://user:s3cret-pw@localhost:5432/db"


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    names = (
        "ENVIRONMENT",
        "DATABASE_URL",
        "API_V1_PREFIX",
        "SESSION_TTL_HOURS",
        "SCHOOL_NAME",
        "SCHOOL_LOCATION",
    )
    for name in names:
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


def test_session_ttl_defaults_and_overrides(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", DB_URL)
    assert make_settings().session_ttl_hours == 12
    monkeypatch.setenv("SESSION_TTL_HOURS", "2")
    assert make_settings().session_ttl_hours == 2


@pytest.mark.parametrize("value", ["0", "-1"])
def test_session_ttl_must_be_positive(monkeypatch, value):
    monkeypatch.setenv("DATABASE_URL", DB_URL)
    monkeypatch.setenv("SESSION_TTL_HOURS", value)
    with pytest.raises(ValidationError) as exc:
        make_settings()
    assert "session_ttl_hours" in str(exc.value)


def test_secret_not_exposed_in_errors_or_repr(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", DB_URL)
    assert "s3cret-pw" not in repr(make_settings())
    monkeypatch.setenv("ENVIRONMENT", "staging")
    with pytest.raises(ValidationError) as exc:
        make_settings()
    assert "s3cret-pw" not in str(exc.value)


def test_school_details_default_to_the_srs_values_and_can_be_overridden(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", DB_URL)
    settings = Settings(_env_file=None)
    assert settings.school_name == "Camdock International School"
    assert settings.school_location == "Enugu State, Nigeria"

    monkeypatch.setenv("SCHOOL_NAME", "Another School")
    monkeypatch.setenv("SCHOOL_LOCATION", "Awka, Nigeria")
    settings = Settings(_env_file=None)
    assert (settings.school_name, settings.school_location) == (
        "Another School",
        "Awka, Nigeria",
    )


@pytest.mark.parametrize("name", ["SCHOOL_NAME", "SCHOOL_LOCATION"])
def test_school_details_must_not_be_empty(monkeypatch, name):
    monkeypatch.setenv("DATABASE_URL", DB_URL)
    monkeypatch.setenv(name, "")
    with pytest.raises(ValidationError):
        Settings(_env_file=None)
