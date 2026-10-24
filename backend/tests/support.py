import pytest
from sqlalchemy.engine import make_url
from sqlalchemy.exc import ArgumentError, IntegrityError

TEST_DATABASE_SUFFIX = "_test"


def assert_safe_test_database(url: str) -> None:
    """Refuse any test database URL whose database name does not end in `_test`.

    Messages never include the URL, which may contain credentials.
    """
    try:
        name = make_url(url).database
    except ArgumentError:
        raise ValueError("TEST_DATABASE_URL is not a valid database URL.") from None
    if not name or not name.endswith(TEST_DATABASE_SUFFIX):
        raise ValueError(
            f"TEST_DATABASE_URL must point to a database whose name ends with "
            f"'{TEST_DATABASE_SUFFIX}'."
        )


def assert_rejected(db, statement, constraint: str) -> None:
    """The statement must violate the named constraint.

    Runs in a savepoint so the surrounding test transaction stays usable.
    """
    with pytest.raises(IntegrityError) as excinfo, db.begin_nested():
        db.execute(statement)
    assert excinfo.value.orig.diag.constraint_name == constraint
