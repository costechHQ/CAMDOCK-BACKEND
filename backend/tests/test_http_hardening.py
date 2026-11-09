import asyncio

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy import event

from app.core.config import (
    Settings,
    check_production_settings,
    get_settings,
    parse_origins,
)
from app.core.http_hardening import BodySizeLimitMiddleware
from app.core.ratelimit import RateLimiter
from app.main import create_app
from app.schemas.students import StudentCreate
from app.schemas.teachers import TeacherCreate
from tests.test_auth_api import LOGIN_URL, login

NOWHERE = "/api/v1/no-such-route"
ORIGIN = "https://portal.camdock.test"
SECURITY = {
    "x-content-type-options": "nosniff",
    "x-frame-options": "DENY",
    "referrer-policy": "no-referrer",
    "cache-control": "no-store",
    "content-security-policy": "default-src 'none'; frame-ancestors 'none'",
}
DB_URL = "postgresql+psycopg://camdock:{password}@db:5432/camdock"


def app_with(monkeypatch, **env):
    for name, value in env.items():
        monkeypatch.setenv(name.upper(), value)
    get_settings.cache_clear()
    return create_app()


# --- security headers --------------------------------------------------------------


def test_every_response_carries_the_security_headers(api_client):
    responses = [
        api_client.get("/api/v1/health"),  # 200
        api_client.get("/api/v1/auth/me"),  # 401
        api_client.get(NOWHERE),  # 404
        api_client.post(LOGIN_URL, json={}),  # 422
        api_client.get("/api/v1/public/stats"),  # 200
    ]

    assert [r.status_code for r in responses] == [200, 401, 404, 422, 200]
    for response in responses:
        for name, value in SECURITY.items():
            assert response.headers[name] == value, (response.status_code, name)


def test_refusals_from_the_limiters_carry_the_headers_too(api_client):
    api_client.app.state.global_limiter = RateLimiter(1)
    api_client.get(NOWHERE)

    limited = api_client.get(NOWHERE)

    assert limited.status_code == 429
    for name, value in SECURITY.items():
        assert limited.headers[name] == value


def test_hsts_is_sent_in_production_only(make_client):
    production = make_client("production").get(NOWHERE)
    development = make_client("development").get(NOWHERE)
    test = make_client("test").get(NOWHERE)

    assert production.headers["strict-transport-security"] == (
        "max-age=31536000; includeSubDomains"
    )
    assert "strict-transport-security" not in development.headers
    assert "strict-transport-security" not in test.headers


@pytest.mark.parametrize("path", ["/docs", "/redoc", "/openapi.json"])
def test_the_development_docs_are_exempt_from_the_csp_only(make_client, path):
    response = make_client("development").get(path)

    assert response.status_code == 200
    assert "content-security-policy" not in response.headers
    assert response.headers["x-content-type-options"] == "nosniff"


def test_the_docs_do_not_exist_in_production(make_client):
    assert make_client("production").get("/docs").status_code == 404


# --- CORS --------------------------------------------------------------------------


def preflight(client, origin=ORIGIN, method="POST", headers="authorization"):
    return client.options(
        LOGIN_URL,
        headers={
            "Origin": origin,
            "Access-Control-Request-Method": method,
            "Access-Control-Request-Headers": headers,
        },
    )


def test_without_a_setting_no_origin_is_allowed(make_client):
    client = make_client()

    for response in (
        preflight(client),
        client.get(NOWHERE, headers={"Origin": ORIGIN}),
    ):
        assert "access-control-allow-origin" not in response.headers


def test_a_listed_origin_is_allowed_and_others_are_not(monkeypatch):
    client = TestClient(app_with(monkeypatch, cors_allowed_origins=ORIGIN))

    allowed = preflight(client)
    other = preflight(client, origin="https://evil.example")

    assert allowed.status_code == 200
    assert allowed.headers["access-control-allow-origin"] == ORIGIN
    assert "access-control-allow-origin" not in other.headers
    assert other.status_code == 400


def test_only_the_expected_methods_and_headers_are_allowed(monkeypatch):
    client = TestClient(app_with(monkeypatch, cors_allowed_origins=ORIGIN))

    allowed = preflight(client, method="DELETE", headers="authorization, content-type")
    bad_method = preflight(client, method="TRACE")
    bad_header = preflight(client, headers="x-custom")

    assert allowed.status_code == 200
    assert bad_method.status_code == 400
    assert bad_header.status_code == 400


def test_credentials_are_never_allowed(monkeypatch):
    client = TestClient(app_with(monkeypatch, cors_allowed_origins=ORIGIN))

    response = preflight(client)

    assert "access-control-allow-credentials" not in response.headers


def test_a_normal_response_to_a_listed_origin_carries_the_cors_header(monkeypatch):
    client = TestClient(app_with(monkeypatch, cors_allowed_origins=ORIGIN))

    response = client.get(NOWHERE, headers={"Origin": ORIGIN})

    assert response.headers["access-control-allow-origin"] == ORIGIN


def test_a_rate_limit_refusal_is_still_readable_by_the_frontend(monkeypatch):
    app = app_with(monkeypatch, cors_allowed_origins=ORIGIN)
    app.state.global_limiter = RateLimiter(1)
    client = TestClient(app)
    client.get(NOWHERE)

    limited = client.get(NOWHERE, headers={"Origin": ORIGIN})

    assert limited.status_code == 429
    assert limited.headers["access-control-allow-origin"] == ORIGIN


def test_several_origins_are_parsed_and_normalised():
    assert parse_origins("https://a.example, http://localhost:5173/ ,") == [
        "https://a.example",
        "http://localhost:5173",
    ]
    assert parse_origins("") == []


@pytest.mark.parametrize(
    "value",
    [
        "*",
        "https://*.camdock.test",
        "https://camdock.test/app",
        "camdock.test",
        "ftp://camdock.test",
        "https://user:pw@camdock.test",
        "https://camdock.test?x=1",
        "https://camdock.test#top",
    ],
)
def test_bad_origins_stop_startup(value):
    with pytest.raises(ValidationError):
        Settings(database_url=DB_URL.format(password="x"), cors_allowed_origins=value)


# --- request size ------------------------------------------------------------------


@pytest.fixture
def small_cap(monkeypatch):
    """A one-kilobyte request cap, set before the app is built."""
    monkeypatch.setenv("MAX_REQUEST_BODY_KB", "1")


def body_of(size: int) -> bytes:
    prefix = b'{"email":"a@camdock.test","password":"x","pad":"'
    suffix = b'"}'
    return prefix + b"a" * (size - len(prefix) - len(suffix)) + suffix


def post(client, content, **kwargs):
    return client.post(
        LOGIN_URL,
        content=content,
        headers={"Content-Type": "application/json"},
        **kwargs,
    )


def test_a_body_at_the_cap_is_accepted(small_cap, api_client):
    assert post(api_client, body_of(1024)).status_code == 401  # reached the login


def test_one_byte_over_the_cap_is_refused(small_cap, api_client):
    response = post(api_client, body_of(1025))

    assert response.status_code == 413
    assert response.json() == {
        "error": {
            "code": "payload_too_large",
            "message": "The request body is too large.",
        }
    }


def test_an_oversized_request_runs_no_queries(small_cap, api_client, db):
    statements: list[str] = []

    def record(conn, cursor, statement, *args):
        statements.append(statement)

    event.listen(db, "before_cursor_execute", record)
    try:
        assert post(api_client, body_of(5000)).status_code == 413
    finally:
        event.remove(db, "before_cursor_execute", record)

    assert not [s for s in statements if s.startswith("SELECT")]


def test_a_chunked_body_is_refused_because_its_size_is_unknown(api_client):
    chunks = iter([b'{"email":', b'"a@camdock.test","password":"x"}'])

    response = post(api_client, chunks)

    assert response.status_code == 411
    assert response.json()["error"]["code"] == "length_required"


def test_ordinary_requests_are_unaffected(api_client):
    assert login(api_client).status_code == 401
    assert api_client.get("/api/v1/public/stats").status_code == 200


def test_the_cap_follows_the_setting(monkeypatch):
    client = TestClient(app_with(monkeypatch, max_request_body_kb="2"))
    headers = {"Content-Type": "application/json"}

    at_cap = client.post(NOWHERE, content=body_of(2048), headers=headers)
    over = client.post(NOWHERE, content=body_of(2049), headers=headers)

    assert at_cap.status_code == 404  # reached routing, which has no such route
    assert over.status_code == 413


def test_oversized_requests_still_count_towards_the_rate_ceiling(small_cap, api_client):
    api_client.app.state.global_limiter = RateLimiter(2)

    statuses = [post(api_client, body_of(2000)).status_code for _ in range(3)]

    assert statuses == [413, 413, 429]


def run_middleware(headers: list[tuple[bytes, bytes]], max_bytes=100):
    sent = []

    async def app(scope, receive, send):
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b""})

    async def send(message):
        sent.append(message)

    scope = {"type": "http", "headers": headers, "method": "POST", "path": "/x"}
    asyncio.run(BodySizeLimitMiddleware(app, max_bytes)(scope, None, send))
    return sent[0]["status"]


def test_a_malformed_length_is_a_bad_request():
    assert run_middleware([(b"content-length", b"abc")]) == 400
    assert run_middleware([(b"content-length", b"-5")]) == 400


def test_the_length_check_boundaries():
    assert run_middleware([(b"content-length", b"100")]) == 200
    assert run_middleware([(b"content-length", b"101")]) == 413
    assert run_middleware([]) == 200
    assert run_middleware([(b"transfer-encoding", b"chunked")]) == 411


def test_the_largest_legitimate_bodies_are_far_below_the_default_cap():
    teacher = TeacherCreate.model_construct(
        user_id=10**9,
        full_name="N" * 150,
        phone="1" * 30,
        class_ids=list(range(10**9, 10**9 + 50)),
        subject_ids=list(range(10**9, 10**9 + 50)),
    )
    student = StudentCreate.model_construct(
        full_name="N" * 150,
        class_id=10**9,
        gender="female",
        guardian_display_name="G" * 150,
        date_of_birth=None,
    )
    cap = get_settings().max_request_body_kb * 1024

    for model in (teacher, student):
        assert len(model.model_dump_json()) * 100 < cap


# --- production startup checks -----------------------------------------------------


def production(password="a-long-random-secret-9f3", **extra):
    return Settings(
        database_url=DB_URL.format(password=password),
        environment="production",
        **extra,
    )


@pytest.mark.parametrize(
    "password", ["change-me", "CHANGE-ME", "changeme", "password", "postgres", "secret"]
)
def test_production_refuses_a_placeholder_database_password(password):
    with pytest.raises(RuntimeError) as error:
        check_production_settings(production(password))

    # A fixed message: the value itself is never echoed.
    assert str(error.value) == (
        "Refusing to start: the database password is a placeholder."
    )


def test_production_refuses_a_missing_database_password():
    settings = Settings(
        database_url="postgresql+psycopg://camdock@db:5432/camdock",
        environment="production",
    )

    with pytest.raises(RuntimeError):
        check_production_settings(settings)


def test_production_starts_with_a_real_password_and_https_origins():
    check_production_settings(production(cors_allowed_origins=ORIGIN))


def test_production_refuses_an_http_origin():
    with pytest.raises(RuntimeError, match="https"):
        check_production_settings(
            production(cors_allowed_origins="http://portal.camdock.test")
        )


@pytest.mark.parametrize("environment", ["development", "test"])
def test_other_environments_may_use_placeholders_and_http(environment):
    settings = Settings(
        database_url=DB_URL.format(password="change-me"),
        environment=environment,
        cors_allowed_origins="http://localhost:5173",
    )

    check_production_settings(settings)


def test_the_app_will_not_start_in_production_with_a_placeholder(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", DB_URL.format(password="change-me"))
    monkeypatch.setenv("ENVIRONMENT", "production")
    get_settings.cache_clear()

    with pytest.raises(RuntimeError):
        create_app()


def test_the_defaults_match_the_policy():
    settings = get_settings()

    assert settings.cors_origins == []
    assert settings.max_request_body_kb == 1024


def test_a_cap_below_one_kilobyte_is_rejected():
    with pytest.raises(ValidationError):
        Settings(database_url=DB_URL.format(password="x"), max_request_body_kb=0)
