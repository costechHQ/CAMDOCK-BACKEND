import os

import pytest
from sqlalchemy.engine import make_url

from app.core.config import get_settings
from tests.conftest import DUMMY_DATABASE_URL
from tests.support import assert_safe_test_database


def test_tests_run_in_test_environment_with_placeholder_database():
    assert os.environ["ENVIRONMENT"] == "test"
    assert os.environ["DATABASE_URL"] == DUMMY_DATABASE_URL
    assert make_url(DUMMY_DATABASE_URL).database.endswith("_test")


def test_settings_cache_starts_empty():
    assert get_settings.cache_info().currsize == 0
    get_settings()
    assert get_settings.cache_info().currsize == 1


def test_settings_cache_is_cleared_between_tests():
    assert get_settings.cache_info().currsize == 0


@pytest.mark.parametrize(
    "url",
    [
        "postgresql+psycopg://camdock:pw@localhost:5432/camdock_test",
        "postgresql+psycopg://camdock:pw@localhost:5433/school_test",
    ],
)
def test_guard_accepts_test_database_names(url):
    assert_safe_test_database(url)


@pytest.mark.parametrize(
    "url",
    [
        "postgresql+psycopg://camdock:s3cret-pw@localhost:5432/camdock",
        "postgresql+psycopg://camdock:s3cret-pw@localhost:5432/postgres",
        "postgresql+psycopg://camdock:s3cret-pw@localhost:5432/",
        "not a url s3cret-pw",
    ],
)
def test_guard_rejects_other_databases_without_leaking_credentials(url):
    with pytest.raises(ValueError) as exc:
        assert_safe_test_database(url)
    assert "s3cret-pw" not in str(exc.value)
