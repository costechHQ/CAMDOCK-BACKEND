# ruff: noqa: F811
import io
import json
import logging

import pytest
from pydantic import ValidationError

from app.core.config import Settings, get_settings
from app.core.logging import (
    HANDLER_MARK,
    configure_logging,
    redact,
    request_id_var,
)
from app.core.ratelimit import RateLimiter
from app.core.request_log import pick_request_id
from app.db.session import get_engine
from app.main import create_app
from tests.test_auth_api import PASSWORD, add_user, login
from tests.test_fee_payments import admin, bill, pay, school  # noqa: F401
from tests.test_health import BrokenSession, client_with_session

NOWHERE = "/api/v1/no-such-route"
HEX_ID = r"[0-9a-f]{32}"


@pytest.fixture
def logs(caplog):
    """Capture everything the application logs, including DEBUG."""
    caplog.set_level(logging.DEBUG, logger="app")
    return caplog


def access_lines(caplog):
    return [r for r in caplog.records if r.name == "app.request"]


def fields(record) -> dict[str, str]:
    return dict(part.split("=", 1) for part in record.getMessage().split())


def marked_handlers():
    return [
        h for h in logging.getLogger("app").handlers if getattr(h, HANDLER_MARK, False)
    ]


# --- request ids -------------------------------------------------------------------


def test_every_response_has_a_request_id(api_client, logs):
    api_client.app.state.global_limiter = RateLimiter(5)
    responses = [
        api_client.get("/api/v1/health"),  # 200
        api_client.get("/api/v1/auth/me"),  # 401
        api_client.get(NOWHERE),  # 404
        api_client.post("/api/v1/auth/login", json={}),  # 422
    ]
    while (last := api_client.get(NOWHERE)).status_code != 429:
        pass
    responses.append(last)

    assert {r.status_code for r in responses} == {200, 401, 404, 422, 429}
    for response in responses:
        assert len(response.headers["x-request-id"]) >= 8


def test_an_oversized_request_gets_an_id_too(monkeypatch, make_client):
    monkeypatch.setenv("MAX_REQUEST_BODY_KB", "1")
    client = make_client()

    response = client.post(NOWHERE, content=b"x" * 2000)

    assert response.status_code == 413
    assert response.headers["x-request-id"]


def test_ids_are_unique_per_request(api_client):
    ids = {api_client.get(NOWHERE).headers["x-request-id"] for _ in range(5)}

    assert len(ids) == 5


def test_a_harmless_client_id_is_kept(api_client):
    response = api_client.get(NOWHERE, headers={"X-Request-ID": "frontend-1234.abc"})

    assert response.headers["x-request-id"] == "frontend-1234.abc"


@pytest.mark.parametrize(
    "supplied",
    [
        "short",
        "has space in it",
        "line\nbreak-injected",
        'quote"inside-id',
        "a" * 65,
        "ünïcode-identifier",
        "",
    ],
)
def test_an_unsafe_client_id_is_replaced(supplied):
    chosen = pick_request_id(supplied)

    assert chosen != supplied
    assert len(chosen) == 32
    assert chosen.isalnum()


def test_a_missing_client_id_is_generated():
    assert len(pick_request_id(None)) == 32


def test_the_id_is_attached_to_the_requests_log_line(api_client, logs):
    response = api_client.get(NOWHERE)

    (line,) = access_lines(logs)
    assert fields(line)["request_id"] == response.headers["x-request-id"]


def test_each_request_logs_its_own_id(api_client, logs):
    first = api_client.get(NOWHERE).headers["x-request-id"]
    second = api_client.get(NOWHERE).headers["x-request-id"]

    assert [fields(r)["request_id"] for r in access_lines(logs)] == [first, second]
    assert request_id_var.get() == "-"


# --- the access line ---------------------------------------------------------------


def test_one_info_line_per_request_with_the_expected_fields(api_client, logs):
    api_client.get(NOWHERE)

    (line,) = access_lines(logs)
    values = fields(line)
    assert line.levelno == logging.INFO
    assert (values["method"], values["path"], values["status"]) == (
        "GET",
        NOWHERE,
        "404",
    )
    assert values["duration_ms"].isdigit()
    assert values["client"] == "testclient"


def test_the_query_string_is_never_logged(api_client, logs, admin):
    api_client.get("/api/v1/students?q=Ada%20Obi&token=abc123&limit=5", headers=admin)

    (line,) = access_lines(logs)
    assert fields(line)["path"] == "/api/v1/students"
    for secret in ("Ada", "Obi", "token", "abc123", "limit"):
        assert secret not in logs.text


def test_health_checks_are_logged_at_debug_only(api_client, caplog):
    caplog.set_level(logging.INFO, logger="app")
    api_client.get("/api/v1/health")
    assert access_lines(caplog) == []

    caplog.set_level(logging.DEBUG, logger="app")
    api_client.get("/api/v1/health")
    (line,) = access_lines(caplog)
    assert line.levelno == logging.DEBUG


def test_a_server_error_is_logged_at_error_level(make_client, logs):
    app = make_client().app
    app.add_api_route("/boom", lambda: 1 / 0)
    client = make_client_for(app)

    client.get("/boom")

    (line,) = access_lines(logs)
    assert line.levelno == logging.ERROR
    assert fields(line)["status"] == "500"


def make_client_for(app):
    from fastapi.testclient import TestClient

    return TestClient(app, raise_server_exceptions=False)


# --- nothing sensitive in the log --------------------------------------------------


def test_login_logs_no_credentials(db, api_client, logs):
    add_user(db, email="leak.check@camdock.test")

    bad = login(api_client, email="leak.check@camdock.test", password="wrong-guess-77")
    good = login(api_client, email="leak.check@camdock.test")
    token = good.json()["access_token"]

    assert (bad.status_code, good.status_code) == (401, 200)
    for secret in ("leak.check", "wrong-guess-77", PASSWORD, token, "Authorization"):
        assert secret not in logs.text


def test_a_payment_logs_no_names_or_amounts(db, api_client, logs, admin, school):
    fee = bill(api_client, admin, school, "9876.54")
    response = pay(api_client, admin, fee["id"], "1234.56")
    assert response.status_code == 201
    token = admin["Authorization"].split()[1]

    for secret in ("1234.56", "9876.54", "Student S-ADA", "S-ADA", token):
        assert secret not in logs.text
    paths = {fields(r)["path"] for r in access_lines(logs)}
    assert f"/api/v1/fees/{fee['id']}/payments" in paths


# --- unexpected errors -------------------------------------------------------------

LEAKY = "password=hunter2 for ada.obi@camdock.test token abc123def"


def boom_client(make_client):
    app = make_client().app

    def boom():
        raise RuntimeError(LEAKY)

    app.add_api_route("/boom", boom)
    return make_client_for(app)


def test_an_unexpected_error_is_generic_and_carries_the_usual_headers(
    make_client, logs
):
    response = boom_client(make_client).get("/boom")

    assert response.status_code == 500
    assert response.json() == {
        "error": {"code": "internal_error", "message": "An internal error occurred."}
    }
    assert response.headers["x-request-id"]
    assert response.headers["x-content-type-options"] == "nosniff"


def test_the_log_of_an_unexpected_error_has_the_type_but_not_the_message(
    make_client, logs
):
    response = boom_client(make_client).get("/boom")

    unexpected = [r for r in logs.records if "Unexpected error" in r.getMessage()]
    assert len(unexpected) == 1
    text = unexpected[0].getMessage()
    assert "RuntimeError" in text
    assert "boom" in text  # the frame, so operators can find the cause
    assert "path=/boom" in text
    for secret in ("hunter2", "ada.obi", "abc123def", "password="):
        assert secret not in logs.text
    ids = {fields(r)["request_id"] for r in access_lines(logs)}
    assert ids == {response.headers["x-request-id"]}


def test_a_failing_database_logs_no_driver_message(monkeypatch, make_client, logs):
    client = client_with_session(monkeypatch, make_client, BrokenSession())

    response = client.get("/api/v1/health")

    assert response.status_code == 503
    assert "Health check failed" in logs.text
    for secret in ("s3cret-pw", "db.internal"):
        assert secret not in logs.text


def test_database_errors_never_carry_bound_parameters():
    assert get_engine().hide_parameters is True


# --- the redaction safety net ------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Bearer abc.DEF-123_x", "Bearer [redacted]"),
        ("auth bearer Zm9vYmFy==", "auth Bearer [redacted]"),
        ("password=hunter2 next", "password=[redacted] next"),
        ("PASSWORD: hunter2", "PASSWORD: [redacted]"),
        ("token=abc&x=1", "token=[redacted]&x=1"),
        ("api_key = k-123", "api_key = [redacted]"),
        ("mail ada.obi+x@camdock.test now", "mail [email redacted] now"),
    ],
)
def test_secrets_are_masked(text, expected):
    assert redact(text) == expected


@pytest.mark.parametrize(
    "text",
    [
        "request_id=abc method=GET path=/api/v1/students status=200",
        "Refusing to start: the database password is a placeholder.",
        "Sign-in attempts blocked for client 203.0.113.9",
    ],
)
def test_ordinary_messages_are_untouched(text):
    assert redact(text) == text


def capture(environment="test", level="DEBUG"):
    stream = io.StringIO()
    configure_logging(environment, level, stream)
    return stream


def test_the_handler_masks_secrets_but_the_record_itself_is_not_altered(logs):
    stream = capture()

    logging.getLogger("app.demo").warning("login for %s", "ada@camdock.test")

    assert "[email redacted]" in stream.getvalue()
    assert "ada@camdock.test" not in stream.getvalue()
    assert "ada@camdock.test" in logs.text  # other handlers see the original


# --- formats and setup -------------------------------------------------------------


def test_production_lines_are_json_with_the_expected_keys(make_client):
    client = make_client("production")
    stream = capture("production", "INFO")

    response = client.get(NOWHERE)

    (line,) = stream.getvalue().splitlines()
    entry = json.loads(line)
    assert set(entry) == {"ts", "level", "logger", "message", "request_id"}
    assert entry["level"] == "INFO"
    assert entry["request_id"] == response.headers["x-request-id"]
    assert "status=404" in entry["message"]


def test_json_lines_survive_newlines_and_quotes():
    stream = capture("production", "INFO")

    logging.getLogger("app.demo").info('first line\nsecond "quoted" line')

    (line,) = stream.getvalue().splitlines()
    assert json.loads(line)["message"] == 'first line\nsecond "quoted" line'


def test_json_exceptions_carry_the_type_and_frames_not_the_message():
    stream = capture("production", "INFO")

    try:
        raise ValueError(LEAKY)
    except ValueError:
        logging.getLogger("app.demo").exception("failed")

    entry = json.loads(stream.getvalue().splitlines()[0])
    assert entry["exception"].startswith("ValueError")
    for secret in ("hunter2", "ada.obi", "abc123def"):
        assert secret not in stream.getvalue()


def test_development_lines_show_the_request_id(make_client):
    client = make_client("development")
    stream = capture("development", "INFO")

    response = client.get(NOWHERE)

    assert f"[{response.headers['x-request-id']}]" in stream.getvalue()


def test_lines_outside_a_request_show_a_dash():
    stream = capture()

    logging.getLogger("app.demo").info("startup")

    assert "[-]" in stream.getvalue()


def test_repeated_startups_keep_a_single_handler(monkeypatch):
    root_before = list(logging.getLogger().handlers)

    for _ in range(5):
        create_app()

    assert len(marked_handlers()) == 1
    assert logging.getLogger().handlers == root_before


def test_the_uvicorn_access_log_is_switched_off():
    logging.getLogger("uvicorn.access").disabled = False

    create_app()

    assert logging.getLogger("uvicorn.access").disabled is True


def test_the_log_level_comes_from_the_setting(monkeypatch):
    monkeypatch.setenv("LOG_LEVEL", "warning")
    get_settings.cache_clear()

    create_app()

    assert logging.getLogger("app").level == logging.WARNING


def test_the_default_level_is_info():
    assert get_settings().log_level == "INFO"


def test_an_unknown_level_stops_startup():
    with pytest.raises(ValidationError):
        Settings(database_url="postgresql+psycopg://u:p@h/db", log_level="chatty")


def test_the_cors_policy_exposes_the_headers_the_frontend_needs(monkeypatch):
    from fastapi.testclient import TestClient

    monkeypatch.setenv("CORS_ALLOWED_ORIGINS", "https://portal.camdock.test")
    get_settings.cache_clear()
    client = TestClient(create_app())

    response = client.get(NOWHERE, headers={"Origin": "https://portal.camdock.test"})

    exposed = response.headers["access-control-expose-headers"]
    assert "X-Request-ID" in exposed
    assert "Retry-After" in exposed
