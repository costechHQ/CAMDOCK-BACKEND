"""Write the API's OpenAPI document to docs/openapi.json.

Run from `backend/`: `uv run python scripts/export_openapi.py`. It needs no database;
it builds the app with fixed settings so the output is the same on every machine.
`tests/test_openapi_contract.py` fails when the committed file is out of date.
"""

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TARGET = ROOT / "docs" / "openapi.json"


def build_openapi() -> dict:
    sys.path.insert(0, str(ROOT))
    os.environ["ENVIRONMENT"] = "test"
    os.environ["DATABASE_URL"] = "postgresql+psycopg://test:test@localhost:5432/camdock"
    os.environ["API_V1_PREFIX"] = "/api/v1"
    os.environ.pop("SCHOOL_NAME", None)
    os.environ.pop("SCHOOL_LOCATION", None)

    from app.core.config import get_settings
    from app.main import create_app

    get_settings.cache_clear()
    return create_app().openapi()


def render(document: dict) -> str:
    return json.dumps(document, indent=2, sort_keys=True) + "\n"


if __name__ == "__main__":
    TARGET.parent.mkdir(exist_ok=True)
    TARGET.write_text(render(build_openapi()), encoding="utf-8")
    print(f"wrote {TARGET}")
