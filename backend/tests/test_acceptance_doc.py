"""SRS Section 8 stays covered: every criterion is listed, mapped and demonstrated."""

import importlib.util
import re
import sys
from datetime import date
from pathlib import Path

import pytest

from app.db import seed
from tests.test_auth_api import EMAIL, PASSWORD, add_user

BACKEND = Path(__file__).resolve().parent.parent
ROOT = BACKEND.parent
SRS = ROOT / "DOCS" / "Camdock_International_School_Backend_SRS.md"
ACCEPTANCE_DOC = BACKEND / "docs" / "ACCEPTANCE.md"
SCRIPT = BACKEND / "scripts" / "acceptance_run.py"


def load_script():
    spec = importlib.util.spec_from_file_location("acceptance_run", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    sys.modules["acceptance_run"] = module
    spec.loader.exec_module(module)
    return module


def srs_criteria() -> list[str]:
    text = SRS.read_text(encoding="utf-8")
    section = text[text.index("## 8. Acceptance criteria") : text.index("## 9.")]
    return [
        line.removeprefix("- [ ] ").strip()
        for line in section.splitlines()
        if line.startswith("- [ ] ")
    ]


def test_the_srs_still_has_the_criteria_we_expect():
    assert len(srs_criteria()) == 33


def test_the_script_checks_exactly_the_srs_criteria_in_order():
    script = load_script()

    listed = [*script.AUTH, *script.STUDENTS, *script.RESULTS, *script.FEES]
    listed += script.QUALITY

    assert listed == srs_criteria()


def test_every_criterion_is_in_the_acceptance_document():
    text = ACCEPTANCE_DOC.read_text(encoding="utf-8")

    missing = [c for c in srs_criteria() if c not in text]

    assert not missing, f"docs/ACCEPTANCE.md lacks: {missing}"


def test_every_test_file_named_in_the_document_exists():
    text = ACCEPTANCE_DOC.read_text(encoding="utf-8")
    named = set(re.findall(r"`(tests/test_[a-z_]+\.py)`", text))

    missing = sorted(n for n in named if not (BACKEND / n).exists())

    assert named, "the document names no test files"
    assert not missing, f"docs/ACCEPTANCE.md names missing files: {missing}"


# --- the script refuses to run carelessly ------------------------------------------


def test_the_script_refuses_without_confirmation(capsys):
    script = load_script()

    code = script.main(["--base-url", "http://x", "--admin-email", "a@b.example"])

    assert code == 2
    assert "--confirm-writes" in capsys.readouterr().out


def test_the_script_needs_the_admin_password(monkeypatch, capsys):
    monkeypatch.delenv("ACCEPTANCE_ADMIN_PASSWORD", raising=False)
    script = load_script()

    code = script.main(
        ["--base-url", "http://x", "--admin-email", "a@b.example", "--confirm-writes"]
    )

    assert code == 2
    assert "ACCEPTANCE_ADMIN_PASSWORD" in capsys.readouterr().out


def test_the_script_refuses_a_database_that_already_has_students(
    monkeypatch, api_client, db, capsys
):
    monkeypatch.setenv("ACCEPTANCE_ADMIN_PASSWORD", "x")
    script = load_script()
    monkeypatch.setattr(
        script.Run,
        "__init__",
        lambda self, base_url: (
            setattr(self, "http", api_client)
            or setattr(self, "outcomes", {})
            or setattr(self, "cookies_seen", False)
            or setattr(self, "non_json_seen", False)
            or setattr(self, "requests", 0)
        ),
    )
    from tests.test_access_scope import make_class, make_student

    make_student(db, make_class(db, "Primary 1"), "S-1")

    code = script.main(
        ["--base-url", "http://x", "--admin-email", "a@b.example", "--confirm-writes"]
    )

    assert code == 2
    assert "already has students" in capsys.readouterr().out


# --- the whole demonstration, against the application itself -----------------------


@pytest.fixture
def schooled(db, api_client):
    """An administrator and the school's reference data, as on a freshly set-up server."""
    add_user(db)
    seed.seed_reference_data(
        db,
        academic_year="2026/2027",
        start_date=date(2026, 9, 7),
        end_date=date(2027, 7, 23),
        is_current=True,
    )
    return api_client


def test_the_acceptance_run_passes_against_the_application(
    schooled, monkeypatch, capsys
):
    monkeypatch.setenv("ACCEPTANCE_ADMIN_PASSWORD", PASSWORD)
    script = load_script()
    monkeypatch.setattr(
        script.Run,
        "__init__",
        lambda self, base_url: (
            setattr(self, "http", schooled)
            or setattr(self, "outcomes", {})
            or setattr(self, "cookies_seen", False)
            or setattr(self, "non_json_seen", False)
            or setattr(self, "requests", 0)
        ),
    )

    code = script.main(
        ["--base-url", "http://testserver", "--admin-email", EMAIL, "--confirm-writes"]
    )

    output = capsys.readouterr().out
    assert code == 0, output
    assert "30 passed, 0 failed, 3 shown elsewhere" in output
