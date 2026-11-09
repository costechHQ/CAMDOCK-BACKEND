import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy import event

from app.core.config import Settings, get_settings
from app.core.ratelimit import RateLimiter
from app.main import create_app
from tests.test_auth_api import EMAIL, add_user, login
from tests.test_openapi_contract import load_exporter

STATS = "/api/v1/public/stats"
HEALTH = "/api/v1/health"
NOWHERE = "/api/v1/no-such-route"


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


def limiter(clock, limit, **kwargs):
    return RateLimiter(limit, 60, clock=clock, **kwargs)


# --- the limiter -------------------------------------------------------------------


def test_requests_up_to_the_limit_pass_and_the_next_is_refused():
    rate = limiter(Clock(), 3)

    results = [rate.hit("a") for _ in range(4)]

    assert results == [0, 0, 0, 60]


def test_retry_after_counts_down_to_the_end_of_the_window():
    clock = Clock()
    rate = limiter(clock, 1)
    rate.hit("a")
    clock.advance(45)

    assert rate.hit("a") == 15


def test_the_window_resets_on_schedule():
    clock = Clock()
    rate = limiter(clock, 1)
    rate.hit("a")
    clock.advance(59)
    assert rate.hit("a") > 0

    clock.advance(1)

    assert rate.hit("a") == 0
    assert rate.hit("a") > 0  # the new window is counted, not a free pass


def test_a_later_window_starts_a_fresh_count():
    clock = Clock()
    rate = limiter(clock, 2)
    rate.hit("a")
    rate.hit("a")
    clock.advance(500)

    assert [rate.hit("a"), rate.hit("a"), rate.hit("a") > 0] == [0, 0, True]


def test_refused_requests_do_not_push_the_window_out():
    clock = Clock()
    rate = limiter(clock, 1)
    rate.hit("a")
    clock.advance(30)
    for _ in range(20):
        assert rate.hit("a") > 0

    clock.advance(30)

    assert rate.hit("a") == 0


def test_keys_are_independent():
    rate = limiter(Clock(), 1)
    rate.hit("a")

    assert rate.hit("b") == 0


def test_memory_is_capped():
    rate = limiter(Clock(), 1, max_keys=50)

    for number in range(500):
        rate.hit(f"client-{number}")

    assert len(rate._windows) <= 50


def test_ended_windows_are_dropped_before_live_ones_are_evicted():
    clock = Clock()
    rate = limiter(clock, 1, max_keys=3)
    rate.hit("old1")
    rate.hit("old2")
    clock.advance(61)
    rate.hit("live1")
    rate.hit("live2")

    assert set(rate._windows) == {"live1", "live2"}


# --- /public/stats -----------------------------------------------------------------


@pytest.fixture
def clock(api_client):
    """Give the app limiters driven by a fake clock: 30 public, 300 global."""
    fake = Clock()
    api_client.app.state.public_limiter = limiter(fake, 30)
    api_client.app.state.global_limiter = limiter(fake, 300)
    return fake


def test_thirty_requests_pass_and_the_next_is_refused(api_client, clock):
    statuses = [api_client.get(STATS).status_code for _ in range(30)]

    response = api_client.get(STATS)

    assert statuses == [200] * 30
    assert response.status_code == 429
    assert response.json() == {
        "error": {
            "code": "rate_limited",
            "message": "Too many requests. Try again later.",
        }
    }
    assert response.headers["retry-after"] == "60"


def test_the_endpoint_works_again_after_the_window(api_client, clock):
    for _ in range(31):
        api_client.get(STATS)
    clock.advance(60)

    assert api_client.get(STATS).status_code == 200


def test_another_address_is_unaffected(api_client, clock):
    for _ in range(31):
        api_client.get(STATS)
    other = TestClient(api_client.app, client=("198.51.100.7", 4000))

    assert other.get(STATS).status_code == 200


def test_the_body_is_still_only_three_counts(api_client, clock):
    assert set(api_client.get(STATS).json()) == {"students", "teachers", "classes"}


def test_a_limited_request_runs_no_queries(api_client, clock, db):
    for _ in range(30):
        api_client.get(STATS)
    statements: list[str] = []

    def record(conn, cursor, statement, *args):
        statements.append(statement)

    event.listen(db, "before_cursor_execute", record)
    try:
        assert api_client.get(STATS).status_code == 429
    finally:
        event.remove(db, "before_cursor_execute", record)

    assert not [s for s in statements if s.startswith("SELECT")]


def test_the_public_limit_does_not_apply_to_other_endpoints(api_client, clock, db):
    for _ in range(31):
        api_client.get(STATS)

    assert api_client.get(NOWHERE).status_code == 404


# --- the global ceiling ------------------------------------------------------------


@pytest.fixture
def tight(api_client):
    """A global ceiling of five requests per window."""
    fake = Clock()
    api_client.app.state.global_limiter = limiter(fake, 5)
    return fake


def test_the_request_over_the_ceiling_is_refused_even_for_unknown_routes(
    api_client, tight
):
    statuses = [api_client.get(NOWHERE).status_code for _ in range(5)]

    response = api_client.get(NOWHERE)

    assert statuses == [404] * 5
    assert response.status_code == 429
    assert response.json()["error"]["code"] == "rate_limited"
    assert response.headers["retry-after"] == "60"


def test_failed_logins_count_towards_the_ceiling(api_client, tight, db):
    add_user(db)
    for _ in range(5):
        login(api_client, password="wrong")

    response = login(api_client)

    assert response.status_code == 429
    assert response.json()["error"]["code"] == "rate_limited"


def test_unauthorized_requests_count_too(api_client, tight):
    for _ in range(5):
        assert api_client.get("/api/v1/dashboard/overview").status_code == 401

    assert api_client.get("/api/v1/dashboard/overview").status_code == 429


def test_health_is_never_limited(api_client, tight):
    statuses = {api_client.get(HEALTH).status_code for _ in range(20)}

    assert statuses == {200}
    assert api_client.get(NOWHERE).status_code == 404  # the ceiling is still unspent


def test_the_ceiling_ends_with_the_window(api_client, tight):
    for _ in range(6):
        api_client.get(NOWHERE)
    tight.advance(60)

    assert api_client.get(NOWHERE).status_code == 404


def test_a_spoofed_forwarded_header_does_not_dodge_the_ceiling(api_client, tight):
    codes = [
        api_client.get(NOWHERE, headers={"X-Forwarded-For": f"198.51.100.{n}"})
        for n in range(6)
    ]

    assert [c.status_code for c in codes] == [404] * 5 + [429]


def test_a_trusted_proxy_lets_each_real_client_have_its_own_allowance(monkeypatch):
    monkeypatch.setenv("TRUSTED_PROXIES", "10.0.0.0/8")
    get_settings.cache_clear()
    app = create_app()
    app.state.global_limiter = limiter(Clock(), 2)
    proxy = TestClient(app, client=("10.1.2.3", 4000))

    first = [
        proxy.get(NOWHERE, headers={"X-Forwarded-For": "198.51.100.1"}).status_code
        for _ in range(3)
    ]
    second = proxy.get(NOWHERE, headers={"X-Forwarded-For": "198.51.100.2"})

    assert first == [404, 404, 429]
    assert second.status_code == 404


def test_the_login_block_keeps_its_own_error_code(api_client, db):
    add_user(db)
    for _ in range(5):
        login(api_client, email=EMAIL, password="wrong")

    response = login(api_client)

    assert response.status_code == 429
    assert response.json()["error"]["code"] == "too_many_attempts"


def test_each_app_instance_has_its_own_counters(make_client):
    first, second = make_client(), make_client()

    assert first.app.state.public_limiter is not second.app.state.public_limiter
    assert first.app.state.global_limiter is not second.app.state.global_limiter


def test_the_default_ceiling_leaves_room_for_ordinary_use(api_client):
    statuses = {api_client.get(NOWHERE).status_code for _ in range(250)}

    assert statuses == {404}


# --- contract and configuration ----------------------------------------------------


def test_the_contract_documents_the_429_on_public_stats():
    paths = load_exporter().build_openapi()["paths"]

    assert "429" in paths[STATS]["get"]["responses"]


def test_the_defaults_match_the_policy():
    settings = get_settings()

    assert settings.rate_limit_public_per_minute == 30
    assert settings.rate_limit_global_per_minute == 300


@pytest.mark.parametrize(
    "name", ["rate_limit_public_per_minute", "rate_limit_global_per_minute"]
)
def test_limits_below_one_are_rejected(name):
    with pytest.raises(ValidationError):
        Settings(database_url="postgresql+psycopg://u:p@h/db", **{name: 0})


def test_the_limits_come_from_the_environment(monkeypatch):
    monkeypatch.setenv("RATE_LIMIT_PUBLIC_PER_MINUTE", "7")
    get_settings.cache_clear()

    client = TestClient(create_app())

    assert get_settings().rate_limit_public_per_minute == 7
    assert client.app.state.public_limiter._limit == 7
