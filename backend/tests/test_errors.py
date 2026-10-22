from fastapi import HTTPException
from fastapi.testclient import TestClient
from pydantic import BaseModel, Field

from app.core.errors import AppError


class ScoreIn(BaseModel):
    score: int = Field(ge=0, le=100)
    password: str


def build_client(make_client, environment: str = "development") -> TestClient:
    client = make_client(environment)
    app = client.app

    @app.post("/probe/score")
    def post_score(body: ScoreIn):
        return {"ok": True}

    @app.get("/probe/app-error")
    def raise_app_error():
        raise AppError("Duplicate record.", status_code=409, code="conflict")

    @app.get("/probe/http-error")
    def raise_http_error():
        raise HTTPException(status_code=403, detail="Not allowed.")

    @app.get("/probe/boom")
    def boom():
        raise RuntimeError("secret internal detail")

    return client


def test_validation_error_uses_standard_shape_without_echoing_input(make_client):
    client = build_client(make_client)
    response = client.post("/probe/score", json={"score": 101, "password": "hunter2"})
    assert response.status_code == 422
    error = response.json()["error"]
    assert error["code"] == "validation_error"
    assert error["details"][0]["field"] == "body.score"
    assert "hunter2" not in response.text
    assert "101" not in response.text


def test_missing_field_is_reported_by_path(make_client):
    client = build_client(make_client)
    response = client.post("/probe/score", json={"score": 50})
    fields = [d["field"] for d in response.json()["error"]["details"]]
    assert fields == ["body.password"]


def test_unknown_path_returns_404_standard_shape(make_client):
    response = build_client(make_client).get("/nope")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"
    assert "details" not in response.json()["error"]


def test_wrong_method_returns_405(make_client):
    response = build_client(make_client).get("/probe/score")
    assert response.status_code == 405
    assert response.json()["error"]["code"] == "method_not_allowed"


def test_http_exception_mapped_to_standard_shape(make_client):
    response = build_client(make_client).get("/probe/http-error")
    assert response.status_code == 403
    assert response.json() == {
        "error": {"code": "forbidden", "message": "Not allowed."}
    }


def test_app_error_uses_its_status_and_code(make_client):
    response = build_client(make_client).get("/probe/app-error")
    assert response.status_code == 409
    assert response.json() == {
        "error": {"code": "conflict", "message": "Duplicate record."}
    }


def test_unhandled_exception_returns_generic_500(make_client):
    response = build_client(make_client).get("/probe/boom")
    assert response.status_code == 500
    assert response.json() == {
        "error": {"code": "internal_error", "message": "An internal error occurred."}
    }
    assert "secret internal detail" not in response.text
    assert "Traceback" not in response.text


def test_docs_enabled_in_development(make_client):
    client = build_client(make_client, "development")
    assert client.get("/docs").status_code == 200
    assert client.get("/openapi.json").status_code == 200


def test_docs_disabled_in_production(make_client):
    client = build_client(make_client, "production")
    assert client.get("/docs").status_code == 404
    assert client.get("/openapi.json").status_code == 404
