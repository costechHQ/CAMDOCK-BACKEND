import pytest
from sqlalchemy import Column, Integer, Table

from app.core.config import get_settings
from app.db import session as session_module
from app.db.base import Base

DB_URL = "postgresql+psycopg://user:pw@localhost:5432/db"


CACHED = (
    get_settings,
    session_module.get_engine,
    session_module.get_session_factory,
)


@pytest.fixture(autouse=True)
def configured_env(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", DB_URL)
    for cached in CACHED:
        cached.cache_clear()
    yield
    for cached in CACHED:
        cached.cache_clear()


def test_engine_is_created_lazily_without_connecting():
    engine = session_module.get_engine()
    assert engine.url.host == "localhost"
    assert engine is session_module.get_engine()


def test_get_db_yields_session_and_closes_it(monkeypatch):
    closed = []

    class FakeSession:
        def close(self):
            closed.append(True)

    monkeypatch.setattr(session_module, "get_session_factory", lambda: FakeSession)
    gen = session_module.get_db()
    assert isinstance(next(gen), FakeSession)
    with pytest.raises(StopIteration):
        next(gen)
    assert closed == [True]


def test_get_db_closes_session_on_error(monkeypatch):
    closed = []

    class FakeSession:
        def close(self):
            closed.append(True)

    monkeypatch.setattr(session_module, "get_session_factory", lambda: FakeSession)
    gen = session_module.get_db()
    next(gen)
    with pytest.raises(RuntimeError):
        gen.throw(RuntimeError("boom"))
    assert closed == [True]


def test_naming_convention_applied_to_constraints():
    table = Table(
        "naming_probe", Base.metadata, Column("id", Integer, primary_key=True)
    )
    try:
        assert table.primary_key.name == "pk_naming_probe"
    finally:
        Base.metadata.remove(table)
