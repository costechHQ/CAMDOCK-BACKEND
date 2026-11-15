"""The committed API contract must match the code (docs/openapi.json)."""

import importlib.util
import json
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "export_openapi.py"


def load_exporter():
    spec = importlib.util.spec_from_file_location("export_openapi", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_committed_openapi_document_is_up_to_date(monkeypatch):
    exporter = load_exporter()
    committed = json.loads(exporter.TARGET.read_text(encoding="utf-8"))

    # The exporter sets environment variables; keep them out of the other tests.
    for name in ("ENVIRONMENT", "DATABASE_URL", "API_V1_PREFIX"):
        monkeypatch.setenv(name, "placeholder")
    current = exporter.build_openapi()

    assert current == committed, (
        "docs/openapi.json is out of date: run "
        "`uv run python scripts/export_openapi.py` and commit the result"
    )


def test_the_contract_lists_the_dashboard_and_public_routes():
    committed = json.loads(load_exporter().TARGET.read_text(encoding="utf-8"))["paths"]

    assert "get" in committed["/api/v1/dashboard/overview"]
    assert "get" in committed["/api/v1/public/stats"]
