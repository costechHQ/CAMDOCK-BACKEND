"""The documentation must keep up with the code: settings, variables and links."""

import re
from pathlib import Path

import pytest

from app.core.config import Settings

BACKEND = Path(__file__).resolve().parent.parent
ROOT = BACKEND.parent
ENVIRONMENT_DOC = BACKEND / "docs" / "ENVIRONMENT.md"
DOCS = [
    ROOT / "README.md",
    BACKEND / "README.md",
    *sorted((BACKEND / "docs").glob("*.md")),
]
ROW_NAME = re.compile(r"^\|\s*`([A-Z][A-Z0-9_]+)`", re.MULTILINE)


def api_settings_section() -> str:
    text = ENVIRONMENT_DOC.read_text(encoding="utf-8")
    start = text.index("## API settings")
    end = text.index("### Refusals at start-up")
    return text[start:end]


def documented_names(text: str) -> set[str]:
    return set(ROW_NAME.findall(text))


def test_every_setting_is_documented():
    fields = {name.upper() for name in Settings.model_fields}

    missing = fields - documented_names(api_settings_section())

    assert not missing, f"Add these settings to docs/ENVIRONMENT.md: {sorted(missing)}"


def test_the_settings_table_names_no_setting_that_does_not_exist():
    fields = {name.upper() for name in Settings.model_fields}

    unknown = documented_names(api_settings_section()) - fields

    assert not unknown, (
        f"ENVIRONMENT.md lists settings the code lacks: {sorted(unknown)}"
    )


def test_defaults_in_the_table_match_the_code():
    section = api_settings_section()
    for name in Settings.model_fields:
        default = Settings.model_fields[name].default
        if name == "database_url" or default in ("", None):
            continue
        row = re.search(
            rf"^\|\s*`{name.upper()}`\s*\|\s*`?([^|`]+)`?\s*\|", section, re.MULTILINE
        )
        assert row, name
        assert row.group(1).strip() == str(default), name


def compose_variables() -> set[str]:
    names: set[str] = set()
    for path in (
        ROOT / "docker-compose.yml",
        ROOT / "docker-compose.prod.yml",
        ROOT / "deploy" / "Caddyfile",
    ):
        names |= set(re.findall(r"\$\{([A-Z][A-Z0-9_]+)[:}?-]", path.read_text()))
        names |= set(re.findall(r"\{\$([A-Z][A-Z0-9_]+)\}", path.read_text()))
    return names


def test_every_compose_variable_is_documented():
    text = ENVIRONMENT_DOC.read_text(encoding="utf-8")

    missing = compose_variables() - documented_names(text)

    assert not missing, f"Add these to docs/ENVIRONMENT.md: {sorted(missing)}"


def test_the_root_env_example_lists_the_required_compose_variables():
    example = (ROOT / ".env.example").read_text()

    for name in ("POSTGRES_PASSWORD", "SITE_ADDRESS"):
        assert re.search(rf"^{name}=", example, re.MULTILINE), name


def test_the_api_env_example_lists_every_setting():
    example = (BACKEND / ".env.example").read_text()
    # DATABASE_URL and the school print settings are the only ones allowed to be absent.
    fields = {name.upper() for name in Settings.model_fields}

    missing = {
        n for n in fields if not re.search(rf"^#?\s*{n}=", example, re.MULTILINE)
    }

    assert not missing, f"Add to backend/.env.example: {sorted(missing)}"


@pytest.mark.parametrize("doc", DOCS, ids=lambda p: str(p.relative_to(ROOT)))
def test_relative_links_resolve(doc):
    text = doc.read_text(encoding="utf-8")
    broken = []
    for target in re.findall(r"\]\(([^)\s]+)\)", text):
        if re.match(r"[a-z]+:", target) or target.startswith("#"):
            continue
        if not (doc.parent / target.split("#")[0]).exists():
            broken.append(target)

    assert not broken, f"{doc.name} links to missing files: {broken}"


@pytest.mark.parametrize("doc", DOCS, ids=lambda p: str(p.relative_to(ROOT)))
def test_anchors_point_at_real_headings(doc):
    text = doc.read_text(encoding="utf-8")
    broken = []
    for target in re.findall(r"\]\(([^)\s]*#[^)\s]+)\)", text):
        if re.match(r"[a-z]+:", target):
            continue
        file_part, anchor = target.split("#", 1)
        page = doc if not file_part else doc.parent / file_part
        headings = re.findall(
            r"^#+\s+(.*)$", page.read_text(encoding="utf-8"), re.MULTILINE
        )
        slugs = {
            re.sub(r"[^a-z0-9 -]", "", h.lower()).strip().replace(" ", "-")
            for h in headings
        }
        if anchor not in slugs:
            broken.append(target)

    assert not broken, f"{doc.name} has anchors with no heading: {broken}"


STALE = [
    "Phase 1 (foundation) only",
    "no tables or migrations yet",
    "No school features exist yet",
    "There are no school features yet",
]


@pytest.mark.parametrize("doc", DOCS, ids=lambda p: str(p.relative_to(ROOT)))
def test_no_out_of_date_phrases(doc):
    text = doc.read_text(encoding="utf-8")

    assert [p for p in STALE if p in text] == []


def test_the_documented_health_paths_exist_in_the_contract():
    from tests.test_openapi_contract import load_exporter

    paths = load_exporter().build_openapi()["paths"]
    operations = (BACKEND / "docs" / "OPERATIONS.md").read_text(encoding="utf-8")

    for path in re.findall(r"`GET (/api/v1/[a-z/]+)`", operations):
        assert path in paths, path
